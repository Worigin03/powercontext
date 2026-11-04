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


def classify_emotion(text):
    scores = {}
    for name, words in EMOTION_WORDS.items():
        scores[name] = sum(text.count(w) for w in words)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "未命名"


def make_title(text):
    cleaned = PUNCT_RE.sub(" ", text).strip()
    return cleaned[:16] if cleaned else "无题"


def appraise(text):
    text = text.strip()
    coins = 25
    reasons = []

    detail = min(20, len(text) // 20)
    coins += detail
    if detail:
        reasons.append(f"细节浓度 +{detail}")

    sensory = sum(text.count(w) for w in SENSORY)
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
    return {
        "title": make_title(text),
        "coins": coins,
        "emotion": emotion,
        "reasons": reasons or ["干净、克制，像一张没写地址的明信片"],
    }


def redeem_cost(appraisal, similarity):
    factor = max(0.55, min(1.35, 1.55 - similarity))
    return max(8, round(appraisal * factor))
