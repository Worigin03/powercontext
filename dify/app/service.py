import hashlib
import re

import pymysql

from . import db
from .config import DREAM_COST, START_COINS
from .embeddings import embed

ALIAS_SALT = "memory-pawnshop-2026"
PLAYER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TURN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class AppError(Exception):
    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra


def normalize_player_id(value):
    player_id = str(value or "").strip()
    if (
        not player_id
        or player_id.lower() == "guest"
        or not PLAYER_RE.fullmatch(player_id)
    ):
        raise AppError(
            "bad_player",
            "缺少有效的当铺账号；请从 Dify 会话进入，或提供 account 输入。",
        )
    return player_id


def normalize_turn_id(value):
    turn_id = str(value or "").strip()
    if not turn_id or not TURN_RE.fullmatch(turn_id):
        raise AppError("bad_turn", "工作流缺少可用的 turn_id，账本拒绝入账。")
    return turn_id


def _owner_key(owner_id):
    return hashlib.sha1((ALIAS_SALT + owner_id).encode()).hexdigest()


def _ensure_wallet(cur, player_id):
    cur.execute(
        "SELECT coins FROM wallets WHERE owner_id=%s FOR UPDATE", [player_id]
    )
    row = cur.fetchone()
    if row:
        return int(row["coins"])
    cur.execute(
        "INSERT INTO wallets (owner_id, coins) VALUES (%s, %s)",
        [player_id, START_COINS],
    )
    cur.execute(
        "INSERT INTO ledger (owner_id, turn_id, delta, reason) VALUES (%s, NULL, %s, %s)",
        [player_id, START_COINS, "开户：掌柜塞给你的启动记忆币"],
    )
    return START_COINS


def wallet_get(player_id):
    player_id = normalize_player_id(player_id)
    with db.tx() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            coins = _ensure_wallet(cur, player_id)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE owner_id=%s AND status='pawned'",
                [player_id],
            )
            pawned = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE owner_id=%s AND status='redeemed'",
                [player_id],
            )
            redeemed = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT delta, reason, created_at FROM ledger "
                "WHERE owner_id=%s ORDER BY id DESC LIMIT 5",
                [player_id],
            )
            recent = [
                {
                    "delta": int(item["delta"]),
                    "reason": item["reason"],
                    "at": str(item["created_at"]),
                }
                for item in cur.fetchall()
            ]
    return {
        "balance": coins,
        "pawned": pawned,
        "redeemed": redeemed,
        "recent": recent,
        "start_coins": START_COINS,
        "dream_cost": DREAM_COST,
    }


def wallet_apply(player_id, turn_id, delta, reason, memory_id=None):
    player_id = normalize_player_id(player_id)
    turn_id = normalize_turn_id(turn_id)
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise AppError("bad_delta", "账本金额必须是整数。")
    reason = str(reason or "").strip()[:160]
    if not reason:
        raise AppError("bad_reason", "账本缺少摘要。")
    if delta == 0:
        raise AppError("bad_delta", "账本金额不能为 0。")

    with db.tx() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            coins = _ensure_wallet(cur, player_id)
            cur.execute(
                "SELECT id, delta FROM ledger WHERE owner_id=%s AND turn_id=%s",
                [player_id, turn_id],
            )
            existing = cur.fetchone()
            if existing:
                return {"balance": coins, "applied": False}
            if coins + delta < 0:
                raise AppError(
                    "not_enough_coins",
                    f"记忆币不够，还差 {abs(coins + delta)} 枚。",
                    balance=coins,
                    needed=abs(delta),
                )
            cur.execute(
                "UPDATE wallets SET coins=coins+%s WHERE owner_id=%s",
                [delta, player_id],
            )
            cur.execute(
                "INSERT INTO ledger (owner_id, turn_id, delta, reason, memory_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                [player_id, turn_id, delta, reason, memory_id],
            )
            coins += delta
    return {"balance": coins, "applied": True}


def memory_store(player_id, text, title, emotion, appraisal):
    player_id = normalize_player_id(player_id)
    text = str(text or "").strip()
    title = str(title or "").strip()[:120]
    emotion = str(emotion or "").strip()[:24]
    if len(text) < 10:
        raise AppError("too_short", "这段记忆太薄了，至少写十个字。")
    if len(text) > 2000:
        raise AppError("too_long", "请把记忆浓缩到两千字以内。")
    if not title or not emotion:
        raise AppError("bad_memory", "当票缺少标题或情绪标签。")
    try:
        appraisal = int(appraisal)
    except (TypeError, ValueError):
        raise AppError("bad_appraisal", "估价必须是整数。")
    if appraisal <= 0 or appraisal > 1000:
        raise AppError("bad_appraisal", "估价超出柜台允许的范围。")

    vector = db.vector_literal(embed(text))
    memory_id = db.run(
        """INSERT INTO memories (owner_id, title, content, emotion, appraisal, embedding)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        [player_id, title, text, emotion, appraisal, vector],
    )
    return {"memory_id": memory_id, "title": title}


def _search(query, player_id, scope, limit):
    query = str(query or "").strip()[:200]
    if not query:
        if scope == "public":
            query = "下雨天 跑回家"
        else:
            raise AppError("query_too_short", "赎回需要一条线索。")
    scope = "owner" if scope == "owner" else "public"
    limit = max(1, min(5, int(limit or 3)))
    vec = db.vector_literal(embed(query))

    where = ["status='pawned'"]
    params = []
    if scope == "owner":
        where.append("owner_id=%s")
        params.append(player_id)
    else:
        where.append("is_public=1")
        where.append("owner_id<>%s")
        params.append(player_id)
    sql = f"""
        SELECT id, owner_id, title, content, emotion, appraisal,
               cosine_distance(embedding, %s) AS dist
        FROM memories
        WHERE {' AND '.join(where)}
        ORDER BY dist
        LIMIT %s
    """
    rows = db.rows(sql, [vec, *params, limit])
    items = []
    for row in rows:
        similarity = round(max(0.0, min(1.0, 1.0 - float(row["dist"]))), 4)
        content = row["content"]
        items.append(
            {
                "id": int(row["id"]),
                "title": row["title"],
                "snippet": content[:120] + ("…" if len(content) > 120 else ""),
                "emotion": row["emotion"],
                "appraisal": int(row["appraisal"]),
                "similarity": similarity,
                "owner_key": _owner_key(row["owner_id"]),
            }
        )
    return {"query": query, "scope": scope, "items": items}


def memory_search(player_id, query, scope="public", limit=3):
    player_id = normalize_player_id(player_id)
    return _search(query, player_id, scope, limit)


def memory_redeem(player_id, memory_id, fee, turn_id):
    player_id = normalize_player_id(player_id)
    turn_id = normalize_turn_id(turn_id)
    try:
        memory_id = int(memory_id)
        fee = int(fee)
    except (TypeError, ValueError):
        raise AppError("bad_request", "当票编号和手续费必须是整数。")
    if fee < 0 or fee > 10000:
        raise AppError("bad_fee", "手续费超出柜台允许的范围。")

    with db.tx() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            coins = _ensure_wallet(cur, player_id)
            cur.execute(
                "SELECT id FROM ledger WHERE owner_id=%s AND turn_id=%s",
                [player_id, turn_id],
            )
            if cur.fetchone():
                cur.execute(
                    "SELECT id, title, content, emotion, status FROM memories "
                    "WHERE id=%s AND owner_id=%s",
                    [memory_id, player_id],
                )
                row = cur.fetchone()
                if row is None:
                    raise AppError("no_memory", "这张当票不在你的名下。")
                return {
                    "memory_id": int(row["id"]),
                    "title": row["title"],
                    "content": row["content"],
                    "emotion": row["emotion"],
                    "balance": coins,
                    "applied": False,
                }
            cur.execute(
                "SELECT id, title, content, emotion, status FROM memories "
                "WHERE id=%s AND owner_id=%s FOR UPDATE",
                [memory_id, player_id],
            )
            row = cur.fetchone()
            if row is None or row["status"] != "pawned":
                raise AppError(
                    "no_memory",
                    "柜台上没有这张待赎的当票，可能已经被赎走了。",
                )
            if coins < fee:
                raise AppError(
                    "not_enough_coins",
                    f"赎回这张当票要 {fee} 枚记忆币，你只有 {coins} 枚。",
                    balance=coins,
                    cost=fee,
                )
            cur.execute(
                "UPDATE memories SET status='redeemed', redeemed_at=NOW() "
                "WHERE id=%s AND owner_id=%s AND status='pawned'",
                [memory_id, player_id],
            )
            cur.execute(
                "UPDATE wallets SET coins=coins-%s WHERE owner_id=%s",
                [fee, player_id],
            )
            cur.execute(
                "INSERT INTO ledger (owner_id, turn_id, delta, reason, memory_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                [player_id, turn_id, -fee, f"赎回《{row['title']}》", memory_id],
            )
            coins -= fee
    return {
        "memory_id": int(row["id"]),
        "title": row["title"],
        "content": row["content"],
        "emotion": row["emotion"],
        "balance": coins,
        "applied": True,
    }


def dream_fragments(player_id, limit=3):
    player_id = normalize_player_id(player_id)
    limit = max(1, min(5, int(limit or 3)))
    rows = db.rows(
        """SELECT id, title, content, emotion FROM memories
           WHERE owner_id=%s AND status='pawned' ORDER BY RAND() LIMIT %s""",
        [player_id, limit],
    )
    return {
        "fragments": [
            {
                "id": int(row["id"]),
                "title": row["title"],
                "text": row["content"][:200],
                "emotion": row["emotion"],
            }
            for row in rows
        ],
        "count": len(rows),
        "dream_cost": DREAM_COST,
    }


def stats():
    total = db.one("SELECT COUNT(*) AS n FROM memories")["n"]
    pawned = db.one("SELECT COUNT(*) AS n FROM memories WHERE status='pawned'")["n"]
    avg = db.one("SELECT COALESCE(ROUND(AVG(appraisal)),0) AS v FROM memories")["v"]
    latest = db.rows(
        "SELECT title, emotion, appraisal, status FROM memories ORDER BY id DESC LIMIT 6"
    )
    return {
        "total": int(total),
        "pawned": int(pawned),
        "avg_appraisal": int(avg),
        "latest": latest,
    }


def reset_player(player_id):
    player_id = normalize_player_id(player_id)
    with db.tx() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("DELETE FROM ledger WHERE owner_id=%s", [player_id])
            ledger_count = cur.rowcount
            cur.execute("DELETE FROM wallets WHERE owner_id=%s", [player_id])
            wallet_count = cur.rowcount
            cur.execute("DELETE FROM memories WHERE owner_id=%s", [player_id])
            memory_count = cur.rowcount
    return {
        "removed_memories": memory_count,
        "removed_ledger": ledger_count,
        "removed_wallet": wallet_count,
    }
