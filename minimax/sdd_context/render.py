"""Render a context pack as the Markdown prompt handed to the coding agent."""

from __future__ import annotations

from sdd_context.models import ContextPack, SpecItem


def render_markdown(pack: ContextPack) -> str:
    lines: list[str] = []
    task = pack.task
    lines.append(f"# 开发上下文包 · {task['task_id']} {task['title']}")
    lines.append("")
    lines.append(f"- 生成时间：{pack.generated_at}")
    lines.append(
        f"- 数据来源：seekdb `{pack.database['dsn']}`"
        f"（spec {pack.database['counts']['spec_item']} / symbol {pack.database['counts']['code_symbol']}"
        f" / test {pack.database['counts']['test_case']}，本次 {pack.database['queries']} 条查询）"
    )
    lines.append(f"- 任务状态来源：{pack.state.source}")
    lines.append(f"- 检索词：{'、'.join(pack.database['recall_terms'][:10])}")
    lines.append("")

    lines.append("## 1. 任务")
    lines.append("")
    lines.append(task["description"])
    if task.get("modules"):
        lines.append("")
        lines.append(f"涉及模块：{', '.join(task['modules'])}")
    lines.append("")

    lines.append("## 2. 本次任务适用的规格（status=active）")
    lines.append("")
    for item in pack.specs:
        lines.extend(_render_spec(item))
    if not pack.specs:
        lines.append("（未检索到适用规格）")
        lines.append("")

    lines.append("## 3. 生效的设计决策（必须遵守）")
    lines.append("")
    for item in pack.active_decisions:
        lines.extend(_render_spec(item))
    if not pack.active_decisions:
        lines.append("（无生效决策）")
        lines.append("")

    lines.append("## 4. 已失效条目（不得作为实现依据）")
    lines.append("")
    for entry in pack.invalidated:
        lines.append(f"- **{entry['id']}** {entry['title']} — 状态 `{entry['status']}`，"
                     f"被 `{entry['superseded_by']}` 取代；来源 {entry['source']}")
    if not pack.invalidated:
        lines.append("（无）")
    lines.append("")

    lines.append("## 5. 需要修改的代码")
    lines.append("")
    for symbol in pack.code:
        linked = [
            item.item_id
            for item in pack.specs + pack.active_decisions
            if symbol.qualname in item.applies_to
        ]
        lines.append(f"### `{symbol.qualname}` — {symbol.path}:{symbol.lineno}-{symbol.end_lineno}")
        lines.append("")
        lines.append(f"- 签名：`{symbol.signature}`")
        if linked:
            lines.append(f"- 关联规格：{', '.join(linked)}")
        if symbol.docstring:
            first = symbol.docstring.strip().splitlines()[0]
            lines.append(f"- 说明：{first}")
        lines.append("")
        lines.append("```python")
        lines.extend(_truncate(symbol.source, 60))
        lines.append("```")
        lines.append("")
    if not pack.code:
        lines.append("（未检索到相关代码符号）")
        lines.append("")

    lines.append("## 6. 验证测试")
    lines.append("")
    lines.append("### 6.1 目标功能测试（修改前应失败，修改后必须全部通过）")
    lines.append("")
    for test in pack.target_tests:
        lines.append(_render_test(test))
    if not pack.target_tests:
        lines.append("（无）")
    lines.append("")
    lines.append("### 6.2 回归测试（修改前后都必须通过）")
    lines.append("")
    for test in pack.regression_tests:
        lines.append(_render_test(test))
    if not pack.regression_tests:
        lines.append("（无）")
    lines.append("")

    lines.append("## 7. 任务状态（来自 PowerContext）")
    lines.append("")
    state = pack.state
    if state.is_empty():
        lines.append(f"（暂无已保存状态，task={state.task_id}）")
    else:
        lines.append(f"- 更新时间：{state.updated_at}")
        lines.append(f"- 已完成：{'；'.join(state.done) if state.done else '（无）'}")
        lines.append(f"- 待办：{'；'.join(state.todo) if state.todo else '（无）'}")
        if state.changed_files:
            lines.append(f"- 已改动文件：{', '.join(state.changed_files)}")
        for result in state.verified:
            lines.append(
                f"- 已验证：`{result.get('command', '')}` → exit {result.get('exit_code')}，"
                f"{result.get('summary', '')}"
            )
        if state.notes:
            lines.append(f"- 备注：{state.notes}")
    lines.append("")

    lines.append("## 8. 上下文自检")
    lines.append("")
    if pack.warnings:
        for warning in pack.warnings:
            lines.append(f"- ⚠ {warning}")
    else:
        lines.append("- 规格、代码、测试来源均存在，目标与回归测试齐全")
    lines.append("")
    return "\n".join(lines)


def _render_spec(item: SpecItem) -> list[str]:
    lines = [f"### {item.item_id} {item.title}", ""]
    lines.append(
        f"- 来源：{item.citation} · 状态 `{item.status}` · 修订 `{item.revision[:12]}`"
    )
    if item.supersedes:
        lines.append(f"- 取代：{', '.join(item.supersedes)}")
    if item.applies_to:
        lines.append(f"- 适用位置：{', '.join(item.applies_to)}")
    if item.verified_by:
        lines.append(f"- 验证：{', '.join(item.verified_by)}")
    lines.append("")
    lines.append(item.body.strip())
    lines.append("")
    return lines


def _render_test(test) -> str:
    covers = f"（covers: {', '.join(test.covers)}）" if test.covers else ""
    return f"- `{test.name}` — {test.citation} {covers}"


def _truncate(source: str, limit: int) -> list[str]:
    rows = source.splitlines()
    if len(rows) <= limit:
        return rows
    return [*rows[:limit], f"# ... 省略 {len(rows) - limit} 行 ..."]
