"""Generate the model-free DSL variant from the main chatflow.

The lite variant keeps the same router and seekdb counter, then turns the
counter JSON into a reply with a Code node instead of an LLM. It has no
marketplace dependencies, so it imports and runs on a bare Dify install.
"""

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "dsl" / "memory-pawnshop.yml"
TARGET = ROOT / "dsl" / "memory-pawnshop-lite.yml"

FORMAT_CODE = r'''
import json


def main(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {"text": "柜台暂时打烊了。请确认记忆当铺服务已经启动，再敲敲柜台。"}

    if not data.get("ok", True):
        message = data.get("message") or "掌柜摇了摇头，没办成。"
        if data.get("hint") == "action=pawn":
            message += "（先当掉一段记忆，再来赎。）"
        return {"text": message}

    action = data.get("action")
    say = data.get("say") or ""
    lines = [] if action == "dream" else [say]

    if action == "pawn":
        reasons = data.get("reasons") or []
        if reasons:
            lines.append("估价明细：" + "、".join(str(item) for item in reasons))
    elif action == "redeem":
        content = data.get("content")
        if content:
            lines.append(f"当票背面还留着字：「{content}」")
    elif action == "browse":
        items = data.get("items") or []
        if items:
            lines.append("")
            for item in items:
                lines.append(
                    f"· {item.get('owner')} 的《{item.get('title')}》"
                    f"（{item.get('emotion')}，相似度 {item.get('similarity')}）："
                    f"{item.get('fragment')}"
                )
    elif action == "dream":
        fragments = data.get("fragments") or []
        if fragments:
            lines.append("")
            lines.append(f"掌柜把灯拧暗，你听见 {len(fragments)} 段旧事在锅里翻身：")
            for item in fragments:
                lines.append(f"你回到《{item.get('title')}》——{item.get('text')}")
            lines.append(f"梦的成本 {data.get('cost')} 枚，余额 {data.get('balance')} 枚。")
    elif action == "balance":
        lines.append("")
        for item in data.get("recent") or []:
            lines.append(f"· {item.get('delta'):+d} 枚：{item.get('reason')}")
    return {"text": "\n".join(line for line in lines if line).strip()}
'''


def main():
    dsl = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    workflow = dsl["workflow"]
    nodes = {node["id"]: node for node in workflow["graph"]["nodes"]}

    start = copy.deepcopy(nodes["start"])
    route = copy.deepcopy(nodes["route"])
    counter = copy.deepcopy(nodes["counter"])
    answer = copy.deepcopy(nodes["answer"])

    format_node = {
        "data": {
            "code": FORMAT_CODE.strip(),
            "code_language": "python3",
            "desc": "把柜台返回的 JSON 整理成当铺口吻的文字，不依赖任何模型。",
            "outputs": {"text": {"children": None, "type": "string"}},
            "selected": False,
            "title": "整理柜台回话",
            "type": "code",
            "variables": [
                {
                    "value_selector": ["counter", "body"],
                    "value_type": "string",
                    "variable": "raw",
                }
            ],
        },
        "height": 90,
        "id": "format",
        "position": {"x": 980, "y": 282},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }

    answer["data"]["answer"] = "{{#format.text#}}"
    answer["position"]["x"] = 1280

    workflow["graph"]["nodes"] = [start, route, counter, format_node, answer]
    workflow["graph"]["edges"] = [
        {
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": "start", "targetType": "code"},
            "id": "start-route",
            "source": "start",
            "sourceHandle": "source",
            "target": "route",
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
        },
        {
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": "code", "targetType": "http-request"},
            "id": "route-counter",
            "source": "route",
            "sourceHandle": "source",
            "target": "counter",
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
        },
        {
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": "http-request", "targetType": "code"},
            "id": "counter-format",
            "source": "counter",
            "sourceHandle": "source",
            "target": "format",
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
        },
        {
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": "code", "targetType": "answer"},
            "id": "format-answer",
            "source": "format",
            "sourceHandle": "source",
            "target": "answer",
            "targetHandle": "target",
            "type": "custom",
            "zIndex": 0,
        },
    ]

    dsl["dependencies"] = []
    dsl["app"]["name"] = "记忆当铺 · 离线版"
    dsl["app"]["description"] = (
        "记忆当铺的无模型版本：典当、赎回、黑市、托梦全部可用，"
        "柜台结果由 Dify Code 节点整理，不依赖任何 LLM。"
    )
    dsl["site"]["title"] = "记忆当铺 · 离线版"
    dsl["site"]["description"] = (
        "一家开在城市缝隙里的记忆当铺。无模型也能玩：seekdb 负责找回当掉的旧时光。"
    )

    TARGET.write_text(
        yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False, width=1000),
        encoding="utf-8",
    )
    print(f"wrote {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
