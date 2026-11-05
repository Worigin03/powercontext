import hashlib
import random

from . import appraise as appraiser
from . import db
from .config import DREAM_COST, START_COINS
from .embeddings import embed

ALIAS_SALT = "memory-pawnshop-2026"


class AppError(Exception):
    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra


def _wallet(player_id, cur=None):
    row = db.one("SELECT coins FROM wallets WHERE owner_id=%s", [player_id])
    if row is None:
        db.run("INSERT INTO wallets (owner_id, coins) VALUES (%s, %s)", [player_id, START_COINS])
        db.run(
            "INSERT INTO ledger (owner_id, delta, reason) VALUES (%s, %s, %s)",
            [player_id, START_COINS, "开户：掌柜塞给你的启动记忆币"],
        )
        return START_COINS
    return int(row["coins"])


def _spend(player_id, delta, reason, memory_id=None):
    coins = _wallet(player_id)
    if coins + delta < 0:
        raise AppError(
            "not_enough_coins",
            f"记忆币不够，还差 {abs(coins + delta)} 枚",
            coins=coins,
            needed=-delta,
        )
    db.run("UPDATE wallets SET coins=coins+%s WHERE owner_id=%s", [delta, player_id])
    db.run(
        "INSERT INTO ledger (owner_id, delta, reason, memory_id) VALUES (%s, %s, %s, %s)",
        [player_id, delta, reason, memory_id],
    )
    return coins + delta


def _alias(owner_id):
    digest = hashlib.sha1((ALIAS_SALT + owner_id).encode()).hexdigest()[:2].upper()
    return f"匿名的旅人·{digest}"


def _search(query, player_id=None, only_owner=False, limit=3):
    vec = db.vector_literal(embed(query))
    where = ["status='pawned'", "is_public=1"]
    params = []
    if only_owner:
        where.append("owner_id=%s")
        params.append(player_id)
    elif player_id:
        where.append("owner_id<>%s")
        params.append(player_id)
    sql = f"""
        SELECT id, owner_id, title, content, emotion, appraisal, created_at,
               cosine_distance(embedding, %s) AS dist
        FROM memories
        WHERE {' AND '.join(where)}
        ORDER BY dist
        LIMIT %s
    """
    rows = db.rows(sql, [vec, *params, limit])
    for row in rows:
        row["similarity"] = round(max(0.0, 1.0 - float(row["dist"])), 4)
        row.pop("dist", None)
    return rows


def pawn(player_id, text):
    text = (text or "").strip()
    if len(text) < 10:
        raise AppError("too_short", "这段记忆太薄了，至少写十个字，掌柜才好下笔。")
    if len(text) > 2000:
        raise AppError("too_long", "太长的记忆会把柜台压塌，请浓缩到两千字以内。")

    _wallet(player_id)
    verdict = appraiser.appraise(text)
    memory_id = db.run(
        """INSERT INTO memories (owner_id, title, content, emotion, appraisal, embedding)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        [player_id, verdict["title"], text, verdict["emotion"], verdict["coins"], db.vector_literal(embed(text))],
    )
    balance = _spend(player_id, verdict["coins"], f"典当《{verdict['title']}》", memory_id)
    return {
        "memory_id": memory_id,
        "title": verdict["title"],
        "emotion": verdict["emotion"],
        "appraisal": verdict["coins"],
        "reasons": verdict["reasons"],
        "balance": balance,
        "say": (
            f"掌柜收下了《{verdict['title']}》，估价 {verdict['coins']} 枚记忆币，"
            f"判它是「{verdict['emotion']}」味。你现在还有 {balance} 枚。"
        ),
    }


def redeem(player_id, query):
    query = (query or "").strip()
    if len(query) < 2:
        raise AppError("query_too_short", "你得给掌柜一个线索，哪怕是一阵雨的味道。")

    _wallet(player_id)
    hits = _search(query, player_id=player_id, only_owner=True, limit=3)
    if not hits:
        raise AppError(
            "no_memory",
            "柜台上没有你当掉的记忆。先去「典当」一段吧。",
            hint="action=pawn",
        )
    best = hits[0]
    cost = appraiser.redeem_cost(int(best["appraisal"]), float(best["similarity"]))
    coins = _wallet(player_id)
    if coins < cost:
        raise AppError(
            "not_enough_coins",
            f"赎回这张当票要 {cost} 枚记忆币，你只有 {coins} 枚。再当一段故事吧。",
            coins=coins,
            cost=cost,
        )
    db.run(
        "UPDATE memories SET status='redeemed', redeemed_at=NOW() WHERE id=%s AND owner_id=%s AND status='pawned'",
        [best["id"], player_id],
    )
    balance = _spend(player_id, -cost, f"赎回《{best['title']}》", best["id"])
    return {
        "memory_id": best["id"],
        "title": best["title"],
        "content": best["content"],
        "emotion": best["emotion"],
        "similarity": best["similarity"],
        "cost": cost,
        "balance": balance,
        "exactness": (
            "几乎一字不差" if best["similarity"] >= 0.82
            else "轮廓还在，边角被雨水泡软了" if best["similarity"] >= 0.55
            else "只剩一个模糊的影子"
        ),
        "say": (
            f"掌柜从柜底抽出《{best['title']}》，相似度 {best['similarity']:.2f}，"
            f"收手续费 {cost} 枚，余额 {balance} 枚。记忆的{('清晰度')}："
            f"{'几乎一字不差' if best['similarity'] >= 0.82 else '轮廓还在' if best['similarity'] >= 0.55 else '只剩影子'}。"
        ),
    }


def browse(player_id, query, limit=3):
    query = (query or "").strip() or "下雨天 跑回家"
    _wallet(player_id)
    hits = _search(query, player_id=player_id, only_owner=False, limit=max(1, min(5, limit)))
    items = [
        {
            "title": row["title"],
            "fragment": row["content"][:120] + ("…" if len(row["content"]) > 120 else ""),
            "emotion": row["emotion"],
            "similarity": row["similarity"],
            "owner": _alias(row["owner_id"]),
            "appraisal": row["appraisal"],
        }
        for row in hits
    ]
    return {
        "query": query,
        "items": items,
        "count": len(items),
        "say": (
            "掌柜拉开通往黑市的暗门，翻出几段不属于你的记忆。"
            if items else "黑市今晚没开张，换个词试试。"
        ),
    }


def dream(player_id):
    coins = _wallet(player_id)
    rows = db.rows(
        """SELECT id, title, content, emotion FROM memories
           WHERE owner_id=%s AND status='pawned' ORDER BY RAND() LIMIT 3""",
        [player_id],
    )
    if not rows:
        raise AppError("no_memory", "你还没在这当铺留下任何东西，梦是拼不出来的。")
    if coins < DREAM_COST:
        raise AppError(
            "not_enough_coins",
            f"托梦要 {DREAM_COST} 枚记忆币，你只有 {coins} 枚。",
            coins=coins,
            cost=DREAM_COST,
        )
    balance = _spend(player_id, -DREAM_COST, "托梦：把旧记忆缝在一起")
    fragments = [
        {"title": r["title"], "emotion": r["emotion"], "text": r["content"][:140]}
        for r in rows
    ]
    return {
        "fragments": fragments,
        "cost": DREAM_COST,
        "balance": balance,
        "say": (
            f"掌柜把 {len(fragments)} 段记忆塞进 dream 机，扣了 {DREAM_COST} 枚。"
            "请把碎片缝成一段第二人称的短梦，保留原句里最锋利的那半句。"
        ),
    }


def balance(player_id):
    coins = _wallet(player_id)
    pawned = db.one("SELECT COUNT(*) AS n FROM memories WHERE owner_id=%s AND status='pawned'", [player_id])["n"]
    redeemed = db.one("SELECT COUNT(*) AS n FROM memories WHERE owner_id=%s AND status='redeemed'", [player_id])["n"]
    recent = db.rows(
        "SELECT delta, reason, created_at FROM ledger WHERE owner_id=%s ORDER BY id DESC LIMIT 5",
        [player_id],
    )
    return {
        "balance": coins,
        "pawned": int(pawned),
        "redeemed": int(redeemed),
        "recent": [
            {"delta": int(r["delta"]), "reason": r["reason"], "at": str(r["created_at"])}
            for r in recent
        ],
        "say": f"你现在有 {coins} 枚记忆币，柜台上还存着 {pawned} 段记忆，已经赎走 {redeemed} 段。",
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


def handle(payload):
    player_id = str(payload.get("player_id") or "guest").strip()[:64] or "guest"
    action = str(payload.get("action") or "balance").strip().lower()
    if action == "pawn":
        return pawn(player_id, payload.get("text") or payload.get("memory") or "")
    if action == "redeem":
        return redeem(player_id, payload.get("query") or payload.get("text") or "")
    if action == "browse":
        return browse(player_id, payload.get("query") or "", int(payload.get("limit") or 3))
    if action == "dream":
        return dream(player_id)
    if action == "balance":
        return balance(player_id)
    if action == "chat":
        return {"say": "掌柜在擦一只不存在的杯子，等你开口。", **balance(player_id)}
    raise AppError("unknown_action", f"掌柜听不懂「{action}」这个动作。")
