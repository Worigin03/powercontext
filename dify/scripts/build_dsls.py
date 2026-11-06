"""Build both Dify chatflow DSLs from one graph definition.

The graph is deliberately non-linear: an IF/ELSE fans out into six action
branches, nested IF/ELSE nodes settle redeem/dream, and the black-market
branch formats each hit inside an iteration node. Appraisal, emotion, fee,
wallet deltas and the dream prompt all live in Dify nodes; the FastAPI
service only authenticates, embeds and persists to seekdb.
"""

from pathlib import Path
import textwrap

import yaml

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "change-me-pawnshop-token"
BACKEND = "http://host.docker.internal:8700"
MODEL = {
    "completion_params": {"max_tokens": 1200, "temperature": 0.8},
    "mode": "chat",
    "name": "MiniMax-M2.5",
    "provider": "langgenius/minimax/minimax",
}
MINIMAX_DEPENDENCY = {
    "type": "marketplace",
    "value": {
        "marketplace_plugin_unique_identifier": (
            "langgenius/minimax:0.0.23@"
            "85e20eade1517276e81952545130aa14ad08f7b6b91062ebd1c92d1b58f1e666"
        )
    },
}
DEFAULT_BODY = (
    '{"ok": false, "error": "backend_unreachable", "message": '
    '"柜台暂时打烊了：记忆当铺服务没有响应。请确认服务已启动，'
    '并检查节点的地址与 X-Pawnshop-Token。"}'
)

ROUTE_CODE = textwrap.dedent(
    r'''
    import json
    import re

    START_COINS = 120
    DREAM_COST = 10


    def main(query: str, user_id: str, workflow_run_id: str, account: str) -> dict:
        q = (query or "").strip()
        uid = (user_id or "").strip()
        handle = re.sub(r"[^0-9A-Za-z_-]", "", (account or "").strip())[:32]
        if len(handle) < 3:
            handle = ""
        player_id = ("acct:" + handle) if handle else (("dify:" + uid) if uid else "")

        action = "chat"
        text = ""
        search = ""
        hint = ""

        def after_cmd(s: str, words) -> str:
            for word in words:
                if s.startswith(word):
                    s = s[len(word):]
                    break
            return s.lstrip("：:，,。. 　")

        if re.search(r"余额|记忆币|账本|还剩|存了|赎回了几", q) and not re.search(r"当掉|典当|赎回", q):
            action = "balance"
        elif re.search(r"托.{0,2}梦|做梦|缝成梦|梦到|把.*梦", q):
            action = "dream"
        elif re.search(r"黑市|别人.*记忆|逛逛|看看.*柜|有什么.*记忆", q):
            action = "browse"
            search = after_cmd(q, ["帮我逛逛黑市", "逛逛黑市", "黑市", "看看别人的记忆", "别人的记忆", "逛逛"])
            search = re.sub(r"^(看看|帮我|我想)", "", search).strip(" 。！？!?")
            if search in ("", "别人的秘密", "别人的记忆", "秘密", "记忆", "别人的"):
                search = ""
        elif re.search(r"赎回|赎当|赎|找回|取回", q):
            action = "redeem"
            search = after_cmd(q, ["帮我赎回", "我想赎回", "赎回", "赎当", "找回", "取回", "赎"])
            search = re.sub(r"^(?:一段|那段|这段|我的|关于|的)+", "", search).strip(" 。！？!?")
            if len(search) < 2:
                search = q
        elif re.search(r"当掉|典当|当了|卖掉|押给你|换.*记忆币", q):
            candidate = after_cmd(q, ["我想当掉", "我要当掉", "帮我当掉", "当掉", "典当", "卖掉", "押给你"])
            candidate = re.sub(r"^(?:一段|一个|这段|这个)?(?:记忆|故事)[：:，,。]?", "", candidate).strip()
            if len(candidate) >= 10:
                action = "pawn"
                text = candidate
            else:
                hint = "客人想当记忆，但描述不足十个字；请邀请他多讲一点细节。"

        wallet_body = json.dumps({"player_id": player_id}, ensure_ascii=False)
        redeem_body = json.dumps(
            {"player_id": player_id, "query": search or q, "scope": "owner", "limit": 5},
            ensure_ascii=False,
        )
        browse_body = json.dumps(
            {"player_id": player_id, "query": search, "scope": "public", "limit": 4},
            ensure_ascii=False,
        )
        dream_body = json.dumps({"player_id": player_id, "limit": 3}, ensure_ascii=False)

        return {
            "action": action,
            "text": text,
            "query": search,
            "player_id": player_id,
            "turn_id": workflow_run_id,
            "wallet_body": wallet_body,
            "redeem_search_body": redeem_body,
            "browse_search_body": browse_body,
            "dream_body": dream_body,
            "hint": hint,
            "start_coins": START_COINS,
            "dream_cost": DREAM_COST,
        }
    '''
).strip()

APPRAISE_CODE = textwrap.dedent(
    r'''
    import json
    import re

    SENSORY = (
        "雨", "雪", "风", "光", "灯", "味道", "气味", "香", "声音", "响", "烫", "凉",
        "冰", "热", "沙", "海", "雾", "烟", "甜", "苦", "咸", "颜色", "红", "蓝", "白",
    )
    EMOTION_WORDS = {
        "温柔": ("妈", "奶奶", "外婆", "猫", "拥抱", "手", "信", "晚安", "回家", "灯"),
        "遗憾": ("没", "错过", "如果", "离开", "最后", "来不及", "本来", "可惜", "忘了"),
        "狂喜": ("笑", "跳", "尖叫", "赢", "成功", "第一次", "终于", "炸开", "飞奔"),
        "恐惧": ("怕", "黑", "血", "追", "坠", "锁", "尖叫", "躲", "颤抖"),
        "酸涩": ("分手", "泪", "背影", "旧", "一个人", "车站", "空", "再见", "删"),
        "平静": ("雨", "海", "散步", "午后", "茶", "书", "窗", "发呆", "慢慢"),
    }
    NUMBER_RE = re.compile(r"\d|[一二三四五六七八九十百千万零两]")
    PUNCT_RE = re.compile(r"[\s，。！？、,.!?;；:：\"'“”‘’()（）]+")


    def classify_emotion(text: str) -> str:
        scores = {}
        for name, words in EMOTION_WORDS.items():
            scores[name] = sum(text.count(word) for word in words)
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "未命名"


    def main(text: str, player_id: str, turn_id: str) -> dict:
        text = (text or "").strip()
        if len(text) < 10:
            return {
                "can_pawn": False,
                "title": "",
                "emotion": "",
                "appraisal": 0,
                "reasons_line": "",
                "store_body": "",
                "ledger_body": "",
            }

        coins = 25
        reasons = []

        detail = min(20, len(text) // 20)
        coins += detail
        if detail:
            reasons.append(f"细节浓度 +{detail}")

        sensory = sum(text.count(word) for word in SENSORY)
        sensory_bonus = min(18, sensory * 3)
        coins += sensory_bonus
        if sensory_bonus:
            reasons.append(f"感官颗粒 +{sensory_bonus}")

        emotion = classify_emotion(text)
        if emotion != "未命名":
            coins += 10
            reasons.append(f"{emotion}余温 +10")

        if NUMBER_RE.search(text):
            coins += 8
            reasons.append("可核对的刻度 +8")

        if "我" in text:
            coins += 4
            reasons.append("第一人称 +4")

        coins = max(30, min(180, coins))
        cleaned = PUNCT_RE.sub(" ", text).strip()
        title = (cleaned[:16] if cleaned else "无题")
        reasons_line = "、".join(reasons) or "干净、克制，像一张没写地址的明信片"

        store_body = json.dumps(
            {
                "player_id": player_id,
                "text": text,
                "title": title,
                "emotion": emotion,
                "appraisal": coins,
            },
            ensure_ascii=False,
        )
        ledger_body = json.dumps(
            {
                "player_id": player_id,
                "turn_id": turn_id,
                "delta": coins,
                "reason": f"典当《{title}》",
            },
            ensure_ascii=False,
        )
        return {
            "can_pawn": True,
            "title": title,
            "emotion": emotion,
            "appraisal": coins,
            "reasons_line": reasons_line,
            "store_body": store_body,
            "ledger_body": ledger_body,
        }
    '''
).strip()

PAWN_REPLY_CODE = textwrap.dedent(
    r'''
    import json


    def main(store_body: str, ledger_body: str, title: str, emotion: str, appraisal: int, reasons_line: str) -> dict:
        try:
            store = json.loads(store_body or "{}")
        except ValueError:
            store = {"ok": False, "message": "柜台没有回话。"}
        if not store.get("ok"):
            return {"text": "柜台没写成当票：" + (store.get("message") or "记忆当铺服务不可达。")}
        try:
            ledger = json.loads(ledger_body or "{}")
        except ValueError:
            ledger = {"ok": False, "message": "账本没有回话。"}
        if not ledger.get("ok"):
            return {
                "text": f"《{title}》已经存进柜底，但入账失败："
                + (ledger.get("message") or "账本服务不可达。")
            }
        balance = ledger.get("balance")
        memory_id = store.get("memory_id")
        return {
            "text": (
                f"掌柜收下了《{title}》，估价 {appraisal} 枚记忆币，判它是「{emotion}」味。\n"
                f"估价明细：{reasons_line}\n"
                f"当票编号 #{memory_id}。你现在还有 {balance} 枚记忆币。"
            )
        }
    '''
).strip()

REDEEM_CHOOSE_CODE = textwrap.dedent(
    r'''
    import json

    MIN_MATCH = 0.35


    def main(wallet_body: str, search_body: str, player_id: str, turn_id: str) -> dict:
        try:
            wallet = json.loads(wallet_body or "{}")
        except ValueError:
            wallet = {"ok": False, "message": "钱柜没有回话。"}
        if not wallet.get("ok"):
            return {
                "decision": "fail",
                "fail_reason": "柜台暂时打烊了：" + (wallet.get("message") or "钱柜不可达。"),
                "memory_id": 0,
                "title": "",
                "similarity": 0,
                "cost": 0,
                "exactness": "",
                "balance": 0,
                "commit_body": "",
            }
        balance = int(wallet.get("balance") or 0)

        try:
            result = json.loads(search_body or "{}")
        except ValueError:
            result = {"ok": False, "items": [], "message": "检索柜没有回话。"}
        items = result.get("items") or []
        if not result.get("ok"):
            return {
                "decision": "fail",
                "fail_reason": "检索柜暂时打烊了：" + (result.get("message") or "向量检索不可达。"),
                "memory_id": 0,
                "title": "",
                "similarity": 0,
                "cost": 0,
                "exactness": "",
                "balance": balance,
                "commit_body": "",
            }
        if not items:
            return {
                "decision": "fail",
                "fail_reason": "柜台上没有你当掉的记忆。先去典当一段吧。",
                "memory_id": 0,
                "title": "",
                "similarity": 0,
                "cost": 0,
                "exactness": "",
                "balance": balance,
                "commit_body": "",
            }

        best = items[0]
        similarity = float(best.get("similarity") or 0)
        if similarity < MIN_MATCH:
            return {
                "decision": "fail",
                "fail_reason": (
                    f"最像的一张只有 {similarity:.2f}，低于掌柜敢认账的 {MIN_MATCH:.2f}。"
                    "换一条更具体的线索，或者先去典当一段。"
                ),
                "memory_id": 0,
                "title": str(best.get("title") or ""),
                "similarity": similarity,
                "cost": 0,
                "exactness": "",
                "balance": balance,
                "commit_body": "",
            }

        appraisal = int(best.get("appraisal") or 0)
        factor = max(0.55, min(1.35, 1.55 - similarity))
        cost = max(8, round(appraisal * factor))
        if balance < cost:
            return {
                "decision": "fail",
                "fail_reason": (
                    f"赎回《{best.get('title')}》要 {cost} 枚记忆币，你只有 {balance} 枚。"
                    "再当一段故事吧。"
                ),
                "memory_id": int(best.get("id") or 0),
                "title": str(best.get("title") or ""),
                "similarity": similarity,
                "cost": cost,
                "exactness": "",
                "balance": balance,
                "commit_body": "",
            }

        if similarity >= 0.82:
            exactness = "几乎一字不差"
        elif similarity >= 0.55:
            exactness = "轮廓还在，边角被雨水泡软了"
        else:
            exactness = "只剩一个模糊的影子"
        commit_body = json.dumps(
            {
                "player_id": player_id,
                "memory_id": int(best.get("id") or 0),
                "fee": cost,
                "turn_id": turn_id,
            },
            ensure_ascii=False,
        )
        return {
            "decision": "commit",
            "fail_reason": "",
            "memory_id": int(best.get("id") or 0),
            "title": str(best.get("title") or ""),
            "similarity": similarity,
            "cost": cost,
            "exactness": exactness,
            "balance": balance,
            "commit_body": commit_body,
        }
    '''
).strip()

REDEEM_REPLY_CODE = textwrap.dedent(
    r'''
    import json


    def main(commit_body: str, title: str, similarity: float, cost: int, exactness: str) -> dict:
        try:
            data = json.loads(commit_body or "{}")
        except ValueError:
            data = {"ok": False, "message": "柜台没有回话。"}
        if not data.get("ok"):
            return {"text": "这张当票没赎成：" + (data.get("message") or "记忆当铺服务不可达。")}
        return {
            "text": (
                f"掌柜从柜底抽出《{data.get('title') or title}》，"
                f"相似度 {float(similarity):.2f}，收手续费 {cost} 枚，"
                f"余额 {data.get('balance')} 枚。\n"
                f"当票背面还留着字：「{data.get('content') or ''}」\n"
                f"清晰度：{exactness}。"
            )
        }
    '''
).strip()

REDEEM_FAIL_CODE = textwrap.dedent(
    r'''
    def main(fail_reason: str, balance: int) -> dict:
        reason = (fail_reason or "掌柜摇了摇头，没办成。").strip()
        if balance and balance > 0:
            return {"text": f"{reason}\n你现在有 {balance} 枚记忆币。"}
        return {"text": reason}
    '''
).strip()

BROWSE_PARSE_CODE = textwrap.dedent(
    r'''
    import json


    def main(search_body: str) -> dict:
        try:
            data = json.loads(search_body or "{}")
        except ValueError:
            return {"items": [], "error": "黑市的检索柜没有回话。", "query": ""}
        items = data.get("items") or []
        if not isinstance(items, list):
            items = []
        if not data.get("ok"):
            return {
                "items": [],
                "error": "黑市暂时打烊了：" + (data.get("message") or "向量检索不可达。"),
                "query": data.get("query") or "",
            }
        error = "" if items else "黑市今晚没开张，换个词试试。"
        return {"items": items, "error": error, "query": data.get("query") or ""}
    '''
).strip()

BROWSE_LINE_CODE = textwrap.dedent(
    r'''
    import json


    def main(item) -> dict:
        if not isinstance(item, dict):
            try:
                item = json.loads(item)
            except (TypeError, ValueError):
                item = {}
        key = str(item.get("owner_key") or "")
        alias = "匿名的旅人·" + (key[:2].upper() if key else "??")
        similarity = float(item.get("similarity") or 0)
        line = (
            f"· {alias} 的《{item.get('title') or '无题'}》"
            f"（{item.get('emotion') or '未命名'}，相似度 {similarity:.4f}）："
            f"{item.get('snippet') or ''}"
        )
        return {"line": line}
    '''
).strip()

BROWSE_JOIN_CODE = textwrap.dedent(
    r'''
    def main(lines: list, error: str, query: str) -> dict:
        if error:
            return {"text": error}
        lines = [str(line) for line in (lines or []) if str(line).strip()]
        if not lines:
            return {"text": "黑市今晚没开张，换个词试试。"}
        head = "掌柜拉开黑市的暗门，翻出几段不属于你的记忆："
        if query:
            head += f"（线索：{query}）"
        return {"text": head + "\n" + "\n".join(lines)}
    '''
).strip()

DREAM_CHECK_CODE = textwrap.dedent(
    r'''
    import json

    COST = 10


    def main(wallet_body: str, fragments_body: str) -> dict:
        try:
            wallet = json.loads(wallet_body or "{}")
        except ValueError:
            wallet = {"ok": False, "message": "钱柜没有回话。"}
        try:
            result = json.loads(fragments_body or "{}")
        except ValueError:
            result = {"ok": False, "fragments": [], "message": "托梦机没有回话。"}

        balance = int(wallet.get("balance") or 0) if wallet.get("ok") else 0
        if not wallet.get("ok"):
            return {
                "decision": "fail",
                "fail_reason": "钱柜暂时打烊了：" + (wallet.get("message") or "余额不可达。"),
                "fragment_text": "",
                "fragment_count": 0,
                "ledger_body": "",
                "balance": balance,
            }
        if not result.get("ok"):
            return {
                "decision": "fail",
                "fail_reason": "托梦机暂时打烊了：" + (result.get("message") or "碎片检索不可达。"),
                "fragment_text": "",
                "fragment_count": 0,
                "ledger_body": "",
                "balance": balance,
            }
        fragments = result.get("fragments") or []
        if not fragments:
            return {
                "decision": "fail",
                "fail_reason": "你还没在当铺留下任何东西，梦是拼不出来的。先去典当一段吧。",
                "fragment_text": "",
                "fragment_count": 0,
                "ledger_body": "",
                "balance": balance,
            }
        if balance < COST:
            return {
                "decision": "fail",
                "fail_reason": f"托梦要 {COST} 枚记忆币，你只有 {balance} 枚。",
                "fragment_text": "",
                "fragment_count": 0,
                "ledger_body": "",
                "balance": balance,
            }
        text = "\n".join(
            f"- 《{item.get('title') or '无题'}》（{item.get('emotion') or '未命名'}）："
            f"{item.get('text') or ''}"
            for item in fragments
        )
        ledger_body = json.dumps(
            {"player_id": "", "turn_id": "", "delta": -COST, "reason": "托梦：把旧记忆缝在一起"},
            ensure_ascii=False,
        )
        return {
            "decision": "dream",
            "fail_reason": "",
            "fragment_text": text,
            "fragment_count": len(fragments),
            "ledger_body": ledger_body,
            "balance": balance,
        }
    '''
).strip()

DREAM_LEDGER_FIX_CODE = textwrap.dedent(
    r'''
    import json


    def main(player_id: str, turn_id: str, base_body: str) -> dict:
        try:
            data = json.loads(base_body or "{}")
        except ValueError:
            data = {}
        data["player_id"] = player_id
        data["turn_id"] = turn_id
        return {"body": json.dumps(data, ensure_ascii=False)}
    '''
).strip()

DREAM_REPLY_CODE = textwrap.dedent(
    r'''
    import json
    import re


    def main(raw: str, ledger_body: str, cost: int, count: int) -> dict:
        try:
            ledger = json.loads(ledger_body or "{}")
        except ValueError:
            ledger = {"ok": False, "message": "账本没有回话。"}
        if not ledger.get("ok"):
            return {"text": "托梦机响了一下又灭了：" + (ledger.get("message") or "入账失败。")}
        dream = (raw or "").strip()
        dream = re.sub(r"(?is)<think>.*?</think>", "", dream)
        if "</think>" in dream:
            dream = dream.split("</think>")[-1]
        dream = dream.replace("<think>", "").replace("</think>", "").strip()
        if not dream:
            dream = "梦没有成形，只剩一点潮湿的灯光。"
        return {
            "text": (
                f"{dream}\n\n"
                f"（托梦扣 {cost} 枚，余额 {ledger.get('balance')} 枚；"
                f"用了 {count} 段旧记忆。）"
            )
        }
    '''
).strip()

DREAM_WEAVE_CODE = textwrap.dedent(
    r'''
    import json


    def main(fragment_text: str, count: int, ledger_body: str, cost: int) -> dict:
        try:
            ledger = json.loads(ledger_body or "{}")
        except ValueError:
            ledger = {"ok": False, "message": "账本没有回话。"}
        if not ledger.get("ok"):
            return {"text": "托梦机响了一下又灭了：" + (ledger.get("message") or "入账失败。")}
        return {
            "text": (
                f"掌柜把灯拧暗，你听见 {count} 段旧事在锅里翻身：\n"
                f"{fragment_text}\n\n"
                f"（离线版不生成新叙事；托梦扣 {cost} 枚，余额 {ledger.get('balance')} 枚。）"
            )
        }
    '''
).strip()

DREAM_FAIL_CODE = textwrap.dedent(
    r'''
    def main(fail_reason: str, balance: int) -> dict:
        reason = (fail_reason or "掌柜摇了摇头，梦没有接上。").strip()
        if balance and balance > 0:
            return {"text": f"{reason}\n你现在有 {balance} 枚记忆币。"}
        return {"text": reason}
    '''
).strip()

BALANCE_FMT_CODE = textwrap.dedent(
    r'''
    import json


    def main(wallet_body: str) -> dict:
        try:
            data = json.loads(wallet_body or "{}")
        except ValueError:
            data = {"ok": False, "message": "钱柜没有回话。"}
        if not data.get("ok"):
            return {"text": "钱柜暂时打烊了：" + (data.get("message") or "余额不可达。")}
        lines = [
            f"你现在有 {data.get('balance')} 枚记忆币。"
            f"柜台上还存着 {data.get('pawned')} 段记忆，已经赎走 {data.get('redeemed')} 段。"
        ]
        recent = data.get("recent") or []
        if recent:
            lines.append("最近账目：")
            for item in recent:
                delta = int(item.get("delta") or 0)
                lines.append(f"· {delta:+d} 枚：{item.get('reason') or '未注明'}")
        return {"text": "\n".join(lines)}
    '''
).strip()

CHAT_CLEAN_CODE = textwrap.dedent(
    r'''
    import re


    def main(raw: str) -> dict:
        text = (raw or "").strip()
        text = re.sub(r"(?is)<think>.*?</think>", "", text)
        if "</think>" in text:
            text = text.split("</think>")[-1]
        text = text.replace("<think>", "").replace("</think>", "").strip()
        if not text:
            text = "掌柜在擦一只不存在的杯子，等你开口。"
        return {"text": text}
    '''
).strip()

CHAT_LITE_CODE = textwrap.dedent(
    r'''
    def main(query: str, hint: str) -> dict:
        if hint:
            return {"text": hint + "\n当铺可以：典当一段记忆、凭线索赎回、逛黑市、托梦、查余额。"}
        return {
            "text": (
                "掌柜在擦一只不存在的杯子，等你开口。\n"
                "这里可以：把记忆当掉换记忆币、凭线索赎回旧当票、"
                "逛黑市看别人的记忆、托梦，或者查一查余额。"
            )
        }
    '''
).strip()


def code_node(node_id, title, desc, code, outputs, variables, x, y):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 90,
        "data": {
            "type": "code",
            "title": title,
            "desc": desc,
            "selected": False,
            "code_language": "python3",
            "code": code,
            "outputs": {
                name: {"children": None, "type": kind} for name, kind in outputs.items()
            },
            "variables": [
                {
                    "variable": name,
                    "value_selector": selector,
                    "value_type": kind,
                }
                for name, selector, kind in variables
            ],
        },
    }


def http_node(node_id, title, desc, path, body_selector, x, y):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 90,
        "data": {
            "type": "http-request",
            "title": title,
            "desc": desc,
            "selected": False,
            "method": "post",
            "url": BACKEND + path,
            "authorization": {"type": "no-auth"},
            "headers": "Content-Type: application/json\nX-Pawnshop-Token: " + TOKEN,
            "params": "",
            "body": {
                "type": "json",
                "data": [
                    {
                        "key": "",
                        "type": "text",
                        "value": "{{#" + ".".join(body_selector) + "#}}",
                    }
                ],
            },
            "timeout": {"connect": 10, "read": 60, "write": 60},
            "retry_config": {
                "enabled": False,
                "max_retries": 1,
                "retry_interval": 1000,
                "exponential_backoff": {
                    "enabled": False,
                    "max_interval": 10000,
                    "multiplier": 2,
                },
            },
            "error_strategy": "default-value",
            "default_value": [
                {"key": "body", "type": "string", "value": DEFAULT_BODY},
                {"key": "status_code", "type": "number", "value": 0},
                {"key": "headers", "type": "object", "value": {}},
            ],
        },
    }


def condition(case_id, selector, value):
    return {
        "case_id": case_id,
        "id": case_id,
        "logical_operator": "and",
        "conditions": [
            {
                "id": case_id + "-cond",
                "comparison_operator": "is",
                "value": value,
                "varType": "string",
                "variable_selector": selector,
            }
        ],
    }


def if_node(node_id, title, desc, cases, x, y):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 170,
        "data": {
            "type": "if-else",
            "title": title,
            "desc": desc,
            "selected": False,
            "cases": cases,
        },
    }


def aggregator_node(node_id, title, selectors, x, y):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 220,
        "data": {
            "type": "variable-aggregator",
            "title": title,
            "desc": "把六个动作分支的最终回话合并成一个出口。",
            "selected": False,
            "output_type": "string",
            "variables": selectors,
        },
    }


def answer_node(node_id, x, y):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 90,
        "data": {
            "type": "answer",
            "title": "回复",
            "desc": "",
            "selected": False,
            "answer": "{{#merge.output#}}",
            "variables": [],
        },
    }


def llm_node(node_id, title, desc, system_prompt, user_prompt, x, y, memory=True):
    return {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 90,
        "data": {
            "type": "llm",
            "title": title,
            "desc": desc,
            "selected": False,
            "model": MODEL,
            "prompt_template": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_prompt},
            ],
            "context": {"enabled": False, "variable_selector": []},
            "memory": {
                "enabled": memory,
                "query_prompt_template": "{{#sys.query#}}",
                "window": {"enabled": memory, "size": 8},
            },
            "vision": {"enabled": False, "configs": {"variable_selector": []}},
            "structured_output": {"enabled": False},
            "retry_config": {
                "enabled": True,
                "max_retries": 2,
                "retry_interval": 1000,
                "exponential_backoff": {
                    "enabled": False,
                    "max_interval": 10000,
                    "multiplier": 2,
                },
            },
            "variables": [],
        },
    }


def start_node():
    return {
        "id": "start",
        "type": "custom",
        "position": {"x": 60, "y": 320},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 244,
        "height": 120,
        "data": {
            "type": "start",
            "title": "开始",
            "desc": "account 可选；填写后钱包按 acct:<account> 记账，不填则使用 Dify 会话用户，绝不落到共享 guest。",
            "selected": False,
            "variables": [
                {
                    "label": "当铺账号（可选，3-32 位字母数字）",
                    "variable": "account",
                    "type": "text-input",
                    "required": False,
                    "max_length": 32,
                    "options": [],
                }
            ],
        },
    }


def iteration_node(node_id, title, iterator_selector, output_selector, child, x, y):
    start_id = node_id + "_start"
    wrapper = {
        "id": node_id,
        "type": "custom",
        "position": {"x": x, "y": y},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 420,
        "height": 220,
        "zIndex": 1,
        "data": {
            "type": "iteration",
            "title": title,
            "desc": "对黑市检索返回的每一条记忆执行一次格式化。",
            "selected": False,
            "start_node_id": start_id,
            "iterator_selector": iterator_selector,
            "iterator_input_type": "array[object]",
            "output_selector": output_selector,
            "output_type": "array[string]",
            "is_parallel": False,
            "parallel_nums": 10,
            "error_handle_mode": "terminated",
        },
    }
    start = {
        "id": start_id,
        "type": "custom-iteration-start",
        "parentId": node_id,
        "extent": "parent",
        "draggable": False,
        "selectable": False,
        "zIndex": 1002,
        "position": {"x": 40, "y": 70},
        "sourcePosition": "right",
        "targetPosition": "left",
        "selected": False,
        "width": 44,
        "height": 48,
        "data": {
            "type": "iteration-start",
            "title": "",
            "desc": "",
            "selected": False,
            "isInIteration": True,
        },
    }
    child["parentId"] = node_id
    child["extent"] = "parent"
    child["zIndex"] = 1002
    child["position"] = {"x": 300, "y": 65}
    child["data"]["isInIteration"] = True
    child["data"]["iteration_id"] = node_id
    return wrapper, start, child


def edge(source, target, source_handle="source", iteration_id=None, source_type="", target_type=""):
    data = {
        "isInIteration": bool(iteration_id),
        "isInLoop": False,
        "sourceType": source_type,
        "targetType": target_type,
    }
    if iteration_id:
        data["iteration_id"] = iteration_id
    return {
        "id": f"{source}-{source_handle}-{target}",
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 1002 if iteration_id else 0,
        "data": data,
    }


def build(with_llm):
    nodes = []
    edges = []

    nodes.append(start_node())
    nodes.append(
        code_node(
            "route",
            "柜台分诊",
            "把中文意图拆成 action；生成各分支所需的安全 JSON 载荷，并绑定非 guest 的玩家 ID 与 workflow_run_id。",
            ROUTE_CODE,
            {
                "action": "string",
                "text": "string",
                "query": "string",
                "player_id": "string",
                "turn_id": "string",
                "wallet_body": "string",
                "redeem_search_body": "string",
                "browse_search_body": "string",
                "dream_body": "string",
                "hint": "string",
                "start_coins": "number",
                "dream_cost": "number",
            },
            [
                ("query", ["sys", "query"], "string"),
                ("user_id", ["sys", "user_id"], "string"),
                ("workflow_run_id", ["sys", "workflow_run_id"], "string"),
                ("account", ["start", "account"], "string"),
            ],
            330,
            320,
        )
    )
    cases = [
        condition("pawn", ["route", "action"], "pawn"),
        condition("redeem", ["route", "action"], "redeem"),
        condition("browse", ["route", "action"], "browse"),
        condition("dream", ["route", "action"], "dream"),
        condition("balance", ["route", "action"], "balance"),
    ]
    nodes.append(
        if_node(
            "branch",
            "按动作分支",
            "六个动作走六条不同的节点链；chat 走 ELSE。",
            cases,
            620,
            320,
        )
    )

    nodes.append(
        code_node(
            "pawn_appraise",
            "估价 · 判味",
            "核心玩法在 Dify：细节浓度、感官颗粒、情绪、数字刻度、第一人称共同决定估价。",
            APPRAISE_CODE,
            {
                "can_pawn": "boolean",
                "title": "string",
                "emotion": "string",
                "appraisal": "number",
                "reasons_line": "string",
                "store_body": "string",
                "ledger_body": "string",
            },
            [
                ("text", ["route", "text"], "string"),
                ("player_id", ["route", "player_id"], "string"),
                ("turn_id", ["route", "turn_id"], "string"),
            ],
            920,
            -560,
        )
    )
    nodes.append(
        http_node(
            "pawn_store",
            "写入当票",
            "只负责向量化并写入 seekdb；估价由上一个 Code 节点给出。",
            "/api/memory/store",
            ["pawn_appraise", "store_body"],
            1220,
            -560,
        )
    )
    nodes.append(
        http_node(
            "pawn_ledger",
            "记忆币入账",
            "Dify 已决定估价；这里把 delta 交给 seekdb 钱包/账本事务，turn_id 保证重试不重复入账。",
            "/api/wallet/apply",
            ["pawn_appraise", "ledger_body"],
            1520,
            -560,
        )
    )
    nodes.append(
        code_node(
            "pawn_reply",
            "整理当票",
            "把估价、当票编号和 seekdb 返回的余额合成回复；失败时说明是没写成还是没入账。",
            PAWN_REPLY_CODE,
            {"text": "string"},
            [
                ("store_body", ["pawn_store", "body"], "string"),
                ("ledger_body", ["pawn_ledger", "body"], "string"),
                ("title", ["pawn_appraise", "title"], "string"),
                ("emotion", ["pawn_appraise", "emotion"], "string"),
                ("appraisal", ["pawn_appraise", "appraisal"], "number"),
                ("reasons_line", ["pawn_appraise", "reasons_line"], "string"),
            ],
            1820,
            -560,
        )
    )

    nodes.append(
        http_node(
            "redeem_wallet",
            "赎回前查余额",
            "手续费由 Dify 计算，先取 seekdb 中的余额。",
            "/api/wallet/get",
            ["route", "wallet_body"],
            920,
            -280,
        )
    )
    nodes.append(
        http_node(
            "redeem_search",
            "赎回向量检索",
            "只检索当前玩家未赎回的当票，返回 top5 候选与相似度。",
            "/api/memory/search",
            ["route", "redeem_search_body"],
            1220,
            -280,
        )
    )
    nodes.append(
        code_node(
            "redeem_choose",
            "选票 · 算手续费",
            "核心玩法在 Dify：设置 0.35 相似度门槛，按 appraisal 与 similarity 计算手续费，判断余额是否足够。",
            REDEEM_CHOOSE_CODE,
            {
                "decision": "string",
                "fail_reason": "string",
                "memory_id": "number",
                "title": "string",
                "similarity": "number",
                "cost": "number",
                "exactness": "string",
                "balance": "number",
                "commit_body": "string",
            },
            [
                ("wallet_body", ["redeem_wallet", "body"], "string"),
                ("search_body", ["redeem_search", "body"], "string"),
                ("player_id", ["route", "player_id"], "string"),
                ("turn_id", ["route", "turn_id"], "string"),
            ],
            1520,
            -280,
        )
    )
    nodes.append(
        if_node(
            "redeem_if",
            "能否赎回",
            "相似度过低、没有当票或余额不足都走失败分支。",
            [condition("commit", ["redeem_choose", "decision"], "commit")],
            1820,
            -280,
        )
    )
    nodes.append(
        http_node(
            "redeem_commit",
            "赎回当票",
            "Dify 已算好手续费；seekdb 在一次事务里改当票状态、扣余额、写账本。",
            "/api/memory/redeem",
            ["redeem_choose", "commit_body"],
            2120,
            -360,
        )
    )
    nodes.append(
        code_node(
            "redeem_reply",
            "交还当票",
            "把相似度、手续费与当票原文交给客人。",
            REDEEM_REPLY_CODE,
            {"text": "string"},
            [
                ("commit_body", ["redeem_commit", "body"], "string"),
                ("title", ["redeem_choose", "title"], "string"),
                ("similarity", ["redeem_choose", "similarity"], "number"),
                ("cost", ["redeem_choose", "cost"], "number"),
                ("exactness", ["redeem_choose", "exactness"], "string"),
            ],
            2420,
            -360,
        )
    )
    nodes.append(
        code_node(
            "redeem_fail",
            "赎回失败说明",
            "没有当票、相似度低于门槛或余额不足时，由 Dify 给出下一步建议。",
            REDEEM_FAIL_CODE,
            {"text": "string"},
            [
                ("fail_reason", ["redeem_choose", "fail_reason"], "string"),
                ("balance", ["redeem_choose", "balance"], "number"),
            ],
            2120,
            -180,
        )
    )

    nodes.append(
        http_node(
            "browse_search",
            "黑市向量检索",
            "在别人公开的存当里做 seekdb 向量检索。",
            "/api/memory/search",
            ["route", "browse_search_body"],
            920,
            40,
        )
    )
    nodes.append(
        code_node(
            "browse_parse",
            "解析候选",
            "把 HTTP 字符串解析成数组，交给迭代节点逐条处理。",
            BROWSE_PARSE_CODE,
            {"items": "array[object]", "error": "string", "query": "string"},
            [("search_body", ["browse_search", "body"], "string")],
            1220,
            40,
        )
    )
    line_node = code_node(
        "browse_line",
        "格式化一条黑市记忆",
        "迭代体：每个匿名旅人一条，别名由 owner_key 前两位生成。",
        BROWSE_LINE_CODE,
        {"line": "string"},
        [("item", ["browse_iter", "item"], "object")],
        0,
        0,
    )
    iter_wrapper, iter_start, line_node = iteration_node(
        "browse_iter",
        "逐条翻黑市",
        ["browse_parse", "items"],
        ["browse_line", "line"],
        line_node,
        1520,
        40,
    )
    nodes.extend([iter_wrapper, iter_start, line_node])
    nodes.append(
        code_node(
            "browse_join",
            "拼黑市回话",
            "迭代结束后把每条 line 合成一段散文式回话。",
            BROWSE_JOIN_CODE,
            {"text": "string"},
            [
                ("lines", ["browse_iter", "output"], "array[string]"),
                ("error", ["browse_parse", "error"], "string"),
                ("query", ["browse_parse", "query"], "string"),
            ],
            2100,
            40,
        )
    )

    nodes.append(
        http_node(
            "dream_wallet",
            "托梦前查余额",
            "托梦扣费由 Dify 决定，先确认余额。",
            "/api/wallet/get",
            ["route", "wallet_body"],
            920,
            330,
        )
    )
    nodes.append(
        http_node(
            "dream_fragments",
            "抽取旧记忆碎片",
            "只做存储层随机抽取，不扣费、不生成叙事。",
            "/api/memory/fragments",
            ["route", "dream_body"],
            1220,
            330,
        )
    )
    nodes.append(
        code_node(
            "dream_check",
            "托梦检查与扣费单",
            "核心玩法在 Dify：检查碎片与余额，生成 -10 的账本载荷。",
            DREAM_CHECK_CODE,
            {
                "decision": "string",
                "fail_reason": "string",
                "fragment_text": "string",
                "fragment_count": "number",
                "ledger_body": "string",
                "balance": "number",
            },
            [
                ("wallet_body", ["dream_wallet", "body"], "string"),
                ("fragments_body", ["dream_fragments", "body"], "string"),
            ],
            1520,
            330,
        )
    )
    nodes.append(
        code_node(
            "dream_ledger_prep",
            "补全扣费单",
            "把 player_id 与 turn_id 填进扣费载荷。",
            DREAM_LEDGER_FIX_CODE,
            {"body": "string"},
            [
                ("player_id", ["route", "player_id"], "string"),
                ("turn_id", ["route", "turn_id"], "string"),
                ("base_body", ["dream_check", "ledger_body"], "string"),
            ],
            1820,
            330,
        )
    )
    nodes.append(
        if_node(
            "dream_if",
            "能否托梦",
            "没有碎片或余额不足走失败分支；否则扣费并生成梦。",
            [condition("dream", ["dream_check", "decision"], "dream")],
            2120,
            330,
        )
    )
    nodes.append(
        http_node(
            "dream_ledger",
            "托梦扣费",
            "seekdb 事务扣 10 枚并写账本，turn_id 幂等。",
            "/api/wallet/apply",
            ["dream_ledger_prep", "body"],
            2420,
            230,
        )
    )
    if with_llm:
        nodes.append(
            llm_node(
                "dream_llm",
                "掌柜缝梦",
                "核心叙事在 Dify LLM：把碎片缝成第二人称短梦。",
                (
                    "你是「记忆当铺」的掌柜。把客人当掉的旧记忆缝成一段第二人称的中文短梦，"
                    "八十到一百六十字。保留每段碎片里最锋利的一处细节，不要罗列，不要解释，"
                    "不要提到 AI、模型、JSON、数据库或接口，不要输出思考过程。"
                ),
                (
                    "客人的原话：{{#sys.query#}}\n\n"
                    "旧记忆碎片：\n{{#dream_check.fragment_text#}}"
                ),
                2720,
                230,
                memory=False,
            )
        )
        nodes.append(
            code_node(
                "dream_reply",
                "缝梦后记账",
                "剥掉推理模型的思考块，并附上扣费与余额。",
                DREAM_REPLY_CODE,
                {"text": "string"},
                [
                    ("raw", ["dream_llm", "text"], "string"),
                    ("ledger_body", ["dream_ledger", "body"], "string"),
                    ("cost", ["route", "dream_cost"], "number"),
                    ("count", ["dream_check", "fragment_count"], "number"),
                ],
                3020,
                230,
            )
        )
    else:
        nodes.append(
            code_node(
                "dream_weave",
                "离线缝梦",
                "无模型版本：按碎片顺序拼成一段短梦，扣费数字仍由 Dify 给出。",
                DREAM_WEAVE_CODE,
                {"text": "string"},
                [
                    ("fragment_text", ["dream_check", "fragment_text"], "string"),
                    ("count", ["dream_check", "fragment_count"], "number"),
                    ("ledger_body", ["dream_ledger", "body"], "string"),
                    ("cost", ["route", "dream_cost"], "number"),
                ],
                2720,
                230,
            )
        )
    nodes.append(
        code_node(
            "dream_fail",
            "托梦失败说明",
            "没有碎片或余额不足时说明原因。",
            DREAM_FAIL_CODE,
            {"text": "string"},
            [
                ("fail_reason", ["dream_check", "fail_reason"], "string"),
                ("balance", ["dream_check", "balance"], "number"),
            ],
            2420,
            430,
        )
    )

    nodes.append(
        http_node(
            "balance_wallet",
            "查余额",
            "只读 seekdb 的钱包、当票计数与最近账本。",
            "/api/wallet/get",
            ["route", "wallet_body"],
            920,
            620,
        )
    )
    nodes.append(
        code_node(
            "balance_fmt",
            "整理账本",
            "把余额、存当/赎回数量与最近账目整理成回话。",
            BALANCE_FMT_CODE,
            {"text": "string"},
            [("wallet_body", ["balance_wallet", "body"], "string")],
            1220,
            620,
        )
    )

    if with_llm:
        nodes.append(
            llm_node(
                "chat_llm",
                "掌柜闲聊",
                "非交易类对话走 ELSE 分支；掌柜介绍规则，不编造余额或交易结果。",
                (
                    "你是「记忆当铺」的掌柜，语气温和、节俭、带一点旧时代的诗意。"
                    "你可以介绍：典当记忆换记忆币、凭线索赎回、逛黑市、托梦、查余额。"
                    "不要提 AI、模型、接口、JSON、数据库。不要编造余额或交易结果。"
                    "如果客人想典当但描述太短，请请他多讲一点细节。只输出中文回复，不要输出思考过程。"
                ),
                "客人的话：{{#sys.query#}}\n\n补充提示：{{#route.hint#}}",
                920,
                900,
            )
        )
        nodes.append(
            code_node(
                "chat_clean",
                "去掉思考块",
                "推理模型可能输出 <think>，这里只保留给客人看的部分。",
                CHAT_CLEAN_CODE,
                {"text": "string"},
                [("raw", ["chat_llm", "text"], "string")],
                1220,
                900,
            )
        )
    else:
        nodes.append(
            code_node(
                "chat_fmt",
                "离线闲聊",
                "无模型版本：说明玩法，不编造交易。",
                CHAT_LITE_CODE,
                {"text": "string"},
                [
                    ("query", ["sys", "query"], "string"),
                    ("hint", ["route", "hint"], "string"),
                ],
                920,
                900,
            )
        )

    terminals = [
        ["pawn_reply", "text"],
        ["redeem_reply", "text"],
        ["redeem_fail", "text"],
        ["browse_join", "text"],
        ["dream_reply" if with_llm else "dream_weave", "text"],
        ["dream_fail", "text"],
        ["balance_fmt", "text"],
        ["chat_clean" if with_llm else "chat_fmt", "text"],
    ]
    nodes.append(aggregator_node("merge", "汇总六路回话", terminals, 3340, 320))
    nodes.append(answer_node("answer", 3640, 320))

    edges.extend(
        [
            edge("start", "route", source_type="start", target_type="code"),
            edge("route", "branch", source_type="code", target_type="if-else"),
        ]
    )
    for case_id, target in [
        ("pawn", "pawn_appraise"),
        ("redeem", "redeem_wallet"),
        ("browse", "browse_search"),
        ("dream", "dream_wallet"),
        ("balance", "balance_wallet"),
        ("false", "chat_llm" if with_llm else "chat_fmt"),
    ]:
        edges.append(
            edge("branch", target, source_handle=case_id, source_type="if-else", target_type="code" if target != "chat_llm" else "llm")
        )

    edges.extend(
        [
            edge("pawn_appraise", "pawn_store", source_type="code", target_type="http-request"),
            edge("pawn_store", "pawn_ledger", source_type="http-request", target_type="http-request"),
            edge("pawn_ledger", "pawn_reply", source_type="http-request", target_type="code"),
            edge("pawn_reply", "merge", source_type="code", target_type="variable-aggregator"),
            edge("redeem_wallet", "redeem_search", source_type="http-request", target_type="http-request"),
            edge("redeem_search", "redeem_choose", source_type="http-request", target_type="code"),
            edge("redeem_choose", "redeem_if", source_type="code", target_type="if-else"),
            edge("redeem_if", "redeem_commit", source_handle="commit", source_type="if-else", target_type="http-request"),
            edge("redeem_if", "redeem_fail", source_handle="false", source_type="if-else", target_type="code"),
            edge("redeem_commit", "redeem_reply", source_type="http-request", target_type="code"),
            edge("redeem_reply", "merge", source_type="code", target_type="variable-aggregator"),
            edge("redeem_fail", "merge", source_type="code", target_type="variable-aggregator"),
            edge("browse_search", "browse_parse", source_type="http-request", target_type="code"),
            edge("browse_parse", "browse_iter", source_type="code", target_type="iteration"),
            edge("browse_iter_start", "browse_line", iteration_id="browse_iter", source_type="iteration-start", target_type="code"),
            edge("browse_iter", "browse_join", source_type="iteration", target_type="code"),
            edge("browse_join", "merge", source_type="code", target_type="variable-aggregator"),
            edge("dream_wallet", "dream_fragments", source_type="http-request", target_type="http-request"),
            edge("dream_fragments", "dream_check", source_type="http-request", target_type="code"),
            edge("dream_check", "dream_ledger_prep", source_type="code", target_type="code"),
            edge("dream_ledger_prep", "dream_if", source_type="code", target_type="if-else"),
            edge("dream_if", "dream_ledger", source_handle="dream", source_type="if-else", target_type="http-request"),
            edge("dream_if", "dream_fail", source_handle="false", source_type="if-else", target_type="code"),
            edge("dream_fail", "merge", source_type="code", target_type="variable-aggregator"),
            edge("balance_wallet", "balance_fmt", source_type="http-request", target_type="code"),
            edge("balance_fmt", "merge", source_type="code", target_type="variable-aggregator"),
        ]
    )
    if with_llm:
        edges.extend(
            [
                edge("dream_ledger", "dream_llm", source_type="http-request", target_type="llm"),
                edge("dream_llm", "dream_reply", source_type="llm", target_type="code"),
                edge("dream_reply", "merge", source_type="code", target_type="variable-aggregator"),
                edge("chat_llm", "chat_clean", source_type="llm", target_type="code"),
                edge("chat_clean", "merge", source_type="code", target_type="variable-aggregator"),
            ]
        )
    else:
        edges.extend(
            [
                edge("dream_ledger", "dream_weave", source_type="http-request", target_type="code"),
                edge("dream_weave", "merge", source_type="code", target_type="variable-aggregator"),
                edge("chat_fmt", "merge", source_type="code", target_type="variable-aggregator"),
            ]
        )
    edges.append(edge("merge", "answer", source_type="variable-aggregator", target_type="answer"))

    features = {
        "file_upload": {},
        "opening_statement": (
            "欢迎光临记忆当铺。\n\n"
            "你可以：把一段记忆当掉换记忆币、凭线索赎回旧当票、逛黑市看看别人的记忆、"
            "托梦把碎片缝成梦，或者查一查余额。\n"
            "先说说，你今天带什么来？"
        ),
        "retriever_resource": {"enabled": False},
        "sensitive_word_avoidance": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "suggested_questions": [
            "我想当掉一段记忆：去年冬天奶奶在厨房煮姜汤，窗外下着雪。",
            "帮我赎回一段关东煮的记忆",
            "逛逛黑市，看看别人的秘密",
            "帮我把当掉的记忆缝成一个梦",
            "我还有多少记忆币？",
            "你好，这里怎么玩？",
        ],
        "suggested_questions_after_answer": {"enabled": False},
        "text_to_speech": {"enabled": False, "language": "", "voice": ""},
        "show_workflow_steps": True,
    }

    dsl = {
        "app": {
            "description": (
                "记忆当铺：把记忆当掉换记忆币，凭线索赎回，逛别人的黑市，再托梦把碎片缝成梦。"
                "分支、迭代、估价、手续费与钱包增量都在 Dify 节点内。"
            ),
            "icon": "🏮",
            "icon_background": "#FFEAD5",
            "icon_type": "emoji",
            "mode": "advanced-chat",
            "name": "记忆当铺" if with_llm else "记忆当铺 · 离线版",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [MINIMAX_DEPENDENCY] if with_llm else [],
        "kind": "app",
        "site": {
            "chat_color_theme": None,
            "chat_color_theme_inverted": False,
            "copyright": None,
            "custom_disclaimer": "",
            "default_language": "zh-Hans",
            "description": (
                "一家开在城市缝隙里的记忆当铺。Dify 负责估价、手续费与分支，"
                "seekdb 负责语义向量检索与钱包账本。"
            ),
            "icon": "🏮",
            "icon_background": "#FFEAD5",
            "icon_type": "emoji",
            "input_placeholder": "说说你今天带了什么记忆来…",
            "privacy_policy": None,
            "show_workflow_steps": True,
            "title": "记忆当铺" if with_llm else "记忆当铺 · 离线版",
            "use_icon_as_answer_icon": False,
        },
        "version": "0.7.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": features,
            "graph": {
                "edges": edges,
                "nodes": nodes,
                "viewport": {"x": 0, "y": 0, "zoom": 0.45},
            },
            "rag_pipeline_variables": [],
        },
    }
    return dsl


def main():
    for with_llm, name in [(True, "memory-pawnshop.yml"), (False, "memory-pawnshop-lite.yml")]:
        dsl = build(with_llm)
        path = ROOT / "dsl" / name
        path.write_text(
            yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False, width=1000),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {path.relative_to(ROOT)} nodes={len(dsl['workflow']['graph']['nodes'])}")


if __name__ == "__main__":
    main()
