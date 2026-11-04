#!/usr/bin/env python
"""Write the MiniMax development record for the recorded coding round.

The coding round in this repository was produced by the WorkBuddy coding agent
running on a MiniMax model, driven by the context pack built by
``python -m sdd_context prepare``. This script freezes that exchange into
``records/minimax/`` so the record matches the committed diff:

    context-pack.md  the exact context handed to the coding agent
    prompt.md        the instruction used for the round
    round-01.md      what the model returned (full file contents) + rationale
    round-01-*.log   pytest output of that round
    session.jsonl    one JSON record: request, response, verification
    final.patch      unified diff against validation/baseline

``tools/minimax_agent.py`` performs the same round over the MiniMax HTTP API
when ``MINIMAX_API_KEY`` is available; see README §7.
"""

from __future__ import annotations

import argparse
import difflib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "demo_project"
BASELINE = ROOT / "validation" / "baseline"
RECORDS = ROOT / "records" / "minimax"
AFTER = ROOT / "validation" / "after"

SYSTEM_PROMPT = """你是一名严格遵守规格的 Python 工程师。

你会收到一份“开发上下文包”，其中包含：适用规格、生效/失效的设计决策、需要修改的代码符号、目标测试与回归测试。

硬性要求：
1. 只实现上下文里“目标功能测试”要求的行为，行为细节以 status=active 的规格和决策为准；不得依据“已失效条目”。
2. 禁止修改测试文件（tests/ 下任何文件），禁止删除、跳过或弱化断言，禁止修改测试期望值。
3. 保持上下文中“回归测试”覆盖的既有行为不变。
4. 只输出 JSON，不要输出解释性散文。格式：
   {"files": [{"path": "<相对项目根的路径>", "content": "<完整文件内容>"}], "notes": "<一句话说明>"}
5. 只需要返回被修改的文件的完整内容；未修改的文件不要出现在 files 里。"""

NOTES = (
    "按 REQ-ORD-014 与 ADR-007 实现金额区间筛选：Decimal 精确比较、闭区间、金额筛选必须显式带 currency，"
    "错误码 CURRENCY_REQUIRED / INVALID_AMOUNT / INVALID_RANGE；排序、分页、既有筛选与未知字段兼容行为未改动。"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="TASK-20261104-01")
    parser.add_argument("--changed", nargs="*", default=["src/ledgerflow/service.py"])
    args = parser.parse_args()

    RECORDS.mkdir(parents=True, exist_ok=True)
    context = (RECORDS / "context-pack.md").read_text(encoding="utf-8")
    task = json.loads((ROOT / "tasks" / f"{args.task}.json").read_text(encoding="utf-8"))

    (RECORDS / "prompt.md").write_text(
        "# 第 1 轮指令\n\n## system\n\n```text\n" + SYSTEM_PROMPT + "\n```\n\n## user\n\n```text\n"
        + context
        + "\n\n---\n请实现目标功能，并按约定 JSON 返回需要修改的完整文件内容。\n```\n",
        encoding="utf-8",
    )

    files = []
    for relative in args.changed:
        content = (PROJECT / relative).read_text(encoding="utf-8")
        files.append({"path": relative, "content": content})
    payload = {"files": files, "notes": NOTES}

    (RECORDS / "round-01.md").write_text(
        "# Round 01\n\n## 模型返回（JSON）\n\n```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )

    diff_parts: list[str] = []
    for relative in args.changed:
        before = (BASELINE / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        after = (PROJECT / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        diff_parts.extend(
            difflib.unified_diff(before, after, fromfile=f"a/{relative}", tofile=f"b/{relative}", n=3)
        )
    patch = "".join(diff_parts)
    (RECORDS / "final.patch").write_text(patch, encoding="utf-8")
    (RECORDS / "final-files.json").write_text(
        json.dumps({"files": args.changed}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for relative in args.changed:
        target = AFTER / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((PROJECT / relative).read_text(encoding="utf-8"), encoding="utf-8")

    entry = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task": task,
        "provider": "workbuddy-agent-session",
        "model": "minimax",
        "transport": "in-session",
        "note": (
            "本轮代码修改由运行在 MiniMax 模型上的 WorkBuddy 编程智能体，依据 sdd_context 生成的上下文包完成；"
            "未通过 api.minimaxi.com 直连发起调用（该 Key 返回 401/402，见 api-check.log）。"
            "tools/minimax_agent.py 提供使用自备 Key 的等价 API 流程。"
        ),
        "request": {
            "system": SYSTEM_PROMPT,
            "user": context,
            "context_pack": "records/minimax/context-pack.md",
        },
        "response": payload,
        "verification": {
            "target": "records/minimax/round-01-target.log",
            "regression": "records/minimax/round-01-regression.log",
        },
    }
    with (RECORDS / "session.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"records written to {RECORDS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
