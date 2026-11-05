from . import appraise as appraiser
from . import db
from .embeddings import backend_name, embed_texts
from .db import vector_literal

SEED = [
    ("路人·01", "外婆的缝纫机", "外婆的缝纫机在午后一直响，机油味混着樟脑。她把我的校服裤脚放长一寸，说孩子又长高了。", "温柔"),
    ("路人·02", "高三的第一场雪", "高三晚自习，窗外的第一场雪落下来，全班同时抬头，班主任破天荒没有骂人。", "平静"),
    ("路人·03", "海边埋脚", "第一次看海，我把脚埋进湿沙里，浪打过来，沙子被抽走，人差点摔倒，笑得像个傻子。", "狂喜"),
    ("路人·04", "凌晨三点的泡面", "宿舍熄灯后，三个人蹲在地上分一锅泡面，谁都不肯承认自己饿了。", "温柔"),
    ("路人·05", "地铁站的分手", "分手那天在换乘通道，她转身走进人潮，我盯着她的背影直到广播报出下一站。", "酸涩"),
    ("路人·06", "父亲的自行车", "父亲蹲在楼道里修我的自行车，手上全是链条油，抬头问我明天想不想骑去学校。", "温柔"),
    ("路人·07", "猫在键盘上睡着", "写论文写到凌晨，猫跳上桌，在键盘上睡着，呼噜声比风扇还响。", "平静"),
    ("路人·08", "操场丢失的钥匙", "小学在操场疯跑，钥匙从口袋里掉出去，找了整整一节课，最后在沙坑里挖到。", "恐惧"),
    ("路人·09", "毕业礼帽", "毕业典礼把帽子抛向天空，落下来时找不到自己的那顶，大家笑着说算了。", "狂喜"),
    ("路人·10", "大雨跑回家", "下大雨没带伞，索性跑回家，进门时全身滴水，妈妈一边骂一边递毛巾。", "温柔"),
    ("路人·11", "陌生人的糖", "公交车上低血糖快晕倒，陌生女孩递来一颗糖，没留下名字就下车了。", "温柔"),
    ("路人·12", "删掉又恢复的短信", "深夜把那条短信删了又恢复，恢复又删，最后手机没电，天也亮了。", "遗憾"),
]


def ensure_embedding_backend():
    name = backend_name()
    if db.get_meta("embed_backend") == name:
        return 0
    existing = db.rows("SELECT id, content FROM memories")
    vectors = embed_texts([row["content"] for row in existing])
    for row, vec in zip(existing, vectors):
        db.run("UPDATE memories SET embedding=%s WHERE id=%s", [vector_literal(vec), row["id"]])
    db.set_meta("embed_backend", name)
    return len(existing)


def seed_if_empty():
    existing = {row["title"] for row in db.rows("SELECT title FROM memories")}
    pending = [item for item in SEED if item[1] not in existing]
    if not pending:
        return 0

    texts = [f"{title}。{body}" for _, title, body, _ in pending]
    vectors = embed_texts(texts)
    values, params = [], []
    for (owner, title, body, emotion), vec in zip(pending, vectors):
        verdict = appraiser.appraise(body)
        values.append("(%s, %s, %s, %s, %s, 1, %s)")
        params.extend([owner, title, body, emotion, verdict["coins"], vector_literal(vec)])
    db.run(
        """INSERT INTO memories (owner_id, title, content, emotion, appraisal, is_public, embedding)
           VALUES """ + ", ".join(values),
        params,
    )
    return len(pending)
