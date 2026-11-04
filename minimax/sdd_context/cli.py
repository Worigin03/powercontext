"""Command line entry point for the SDD context module.

    python -m sdd_context init-db
    python -m sdd_context ingest --repo demo_project
    python -m sdd_context prepare --task tasks/TASK-001.json --save
    python -m sdd_context state show --task TASK-001
    python -m sdd_context verify --task TASK-001 -- python -m pytest -m target
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sdd_context.config import Settings
from sdd_context.db import Database, QueryLog
from sdd_context.ingest import ingest
from sdd_context.models import TaskState
from sdd_context.powercontext import PowerContextClient, PowerContextError
from sdd_context.prepare import PrepareOptions, build_pack, load_task
from sdd_context.render import render_markdown
from sdd_context.state import load_state, save_state

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = PACKAGE_ROOT / "demo_project"
DEFAULT_RECORDS = PACKAGE_ROOT / "records"

SUMMARY = re.compile(r"(\d+) (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    query_log = settings.query_log or str(Path(args.records) / "db-queries.log")
    return dataclasses.replace(settings, query_log=query_log)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (PowerContextError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdd-context", description="SDD 工程上下文模块")
    parser.add_argument(
        "--records",
        default=str(DEFAULT_RECORDS),
        help="运行记录目录，默认 minimax/records",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_db = sub.add_parser("init-db", help="创建 seekdb 库表")
    init_db.set_defaults(handler=_init_db)

    ingest_cmd = sub.add_parser("ingest", help="导入规格、代码与测试索引")
    ingest_cmd.add_argument("--repo", default=str(DEFAULT_REPO))
    ingest_cmd.add_argument("--specs", default=None)
    ingest_cmd.add_argument("--src", default=None)
    ingest_cmd.add_argument("--tests", default=None)
    ingest_cmd.add_argument("--note", default="")
    ingest_cmd.set_defaults(handler=_ingest)

    prepare_cmd = sub.add_parser("prepare", help="为任务组装上下文包")
    prepare_cmd.add_argument("--task", required=True)
    prepare_cmd.add_argument("--repo", default=str(DEFAULT_REPO))
    prepare_cmd.add_argument("--json", action="store_true", dest="as_json")
    prepare_cmd.add_argument("--out", default=None)
    prepare_cmd.add_argument("--save", action="store_true")
    prepare_cmd.add_argument("--allow-empty-state", action="store_true")
    prepare_cmd.add_argument("--top-specs", type=int, default=5)
    prepare_cmd.set_defaults(handler=_prepare)

    state_cmd = sub.add_parser("state", help="查看或更新任务状态")
    state_sub = state_cmd.add_subparsers(dest="state_command", required=True)
    show = state_sub.add_parser("show")
    show.add_argument("--task", required=True)
    show.set_defaults(handler=_state_show)
    set_cmd = state_sub.add_parser("set")
    set_cmd.add_argument("--task", required=True)
    set_cmd.add_argument("--done", action="append", default=[])
    set_cmd.add_argument("--todo", action="append", default=[])
    set_cmd.add_argument("--changed", action="append", default=[])
    set_cmd.add_argument("--notes", default=None)
    set_cmd.set_defaults(handler=_state_set)

    verify = sub.add_parser("verify", help="运行测试命令并记录验证结果")
    verify.add_argument("--task", required=True)
    verify.add_argument("--cwd", default=str(DEFAULT_REPO))
    verify.add_argument("--label", default="")
    verify.add_argument("command", nargs=argparse.REMAINDER)
    verify.set_defaults(handler=_verify)

    doctor = sub.add_parser("doctor", help="检查 seekdb 与 PowerContext 连通性")
    doctor.set_defaults(handler=_doctor)

    stats = sub.add_parser("stats", help="输出库内统计")
    stats.set_defaults(handler=_stats)
    return parser


def _init_db(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    db = Database(settings.database, QueryLog(settings.query_log))
    try:
        db.init_schema()
        print(json.dumps(db.counts(), ensure_ascii=False))
    finally:
        db.close()
    return 0


def _ingest(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    summary = ingest(
        settings,
        Path(args.repo),
        specs_dir=Path(args.specs) if args.specs else None,
        src_dir=Path(args.src) if args.src else None,
        tests_dir=Path(args.tests) if args.tests else None,
        note=args.note,
    )
    print(
        json.dumps(
            {key: summary[key] for key in ("run_id", "specs", "symbols", "tests", "database", "counts")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _prepare(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    task = load_task(Path(args.task))
    pack = build_pack(
        settings,
        task,
        PrepareOptions(
            top_specs=args.top_specs,
            allow_empty_state=args.allow_empty_state,
            repo_root=Path(args.repo),
        ),
    )
    markdown = render_markdown(pack)
    json_text = json.dumps(pack.to_dict(), ensure_ascii=False, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json_text if args.as_json else markdown, encoding="utf-8")
        print(target)
    else:
        print(json_text if args.as_json else markdown)
    if args.save:
        out_dir = Path(args.records) / "context"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{task['task_id']}.json").write_text(json_text, encoding="utf-8")
        (out_dir / f"{task['task_id']}.md").write_text(markdown, encoding="utf-8")
    return 0


def _state_show(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    client = PowerContextClient(settings.powercontext)
    try:
        state = load_state(client, args.task)
    finally:
        client.close()
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _state_set(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    client = PowerContextClient(settings.powercontext)
    try:
        state = load_state(client, args.task)
        if args.done:
            state.done = args.done
        if args.todo:
            state.todo = args.todo
        if args.changed:
            state.changed_files = args.changed
        if args.notes is not None:
            state.notes = args.notes
        save_state(client, state, reason=f"manual state update for {args.task}")
    finally:
        client.close()
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _verify(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("error: 缺少要执行的命令，用法：sdd-context verify --task T -- <cmd>", file=sys.stderr)
        return 2
    completed = subprocess.run(  # noqa: S603 - explicit local test command
        command,
        cwd=args.cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    output = stdout + stderr
    summary = _summary(stdout)
    code_version = _code_version(Path(args.cwd))
    print(f"[verify] command: {' '.join(command)}")
    print(f"[verify] cwd: {args.cwd}")
    print(f"[verify] code version: {code_version}")
    settings = build_settings(args)
    client = PowerContextClient(settings.powercontext)
    try:
        state = load_state(client, args.task)
        entry: dict[str, Any] = {
            "command": " ".join(command),
            "exit_code": completed.returncode,
            "summary": summary,
            "label": args.label,
            "at": _utc_now(),
            "code_version": code_version,
            "output_tail": stdout[-1200:] + "\n--- stderr ---\n" + stderr[-400:],
        }
        state.verified = [item for item in state.verified if item.get("command") != entry["command"]]
        state.verified.append(entry)
        save_state(client, state, reason=f"verification for {args.task}")
    finally:
        client.close()
    print(output[-4000:])
    print(f"[verify] exit={completed.returncode} summary={summary}")
    return completed.returncode


def _doctor(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    report: dict[str, Any] = {"database": settings.database.dsn()}
    db = Database(settings.database, QueryLog(settings.query_log))
    try:
        report["database_counts"] = db.counts()
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        report["database_error"] = str(exc)
    finally:
        db.close()
    client = PowerContextClient(settings.powercontext)
    try:
        report["powercontext_health"] = client.health()
        report["powercontext_scope"] = client.scope_id()
        report["powercontext_memories"] = len(client.list_memories())
    except Exception as exc:  # noqa: BLE001
        report["powercontext_error"] = str(exc)
    finally:
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _stats(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    db = Database(settings.database, QueryLog(settings.query_log))
    try:
        counts = db.counts()
        runs = db.query(
            "SELECT run_id, finished_at, spec_count, symbol_count, test_count FROM ingest_run "
            "ORDER BY finished_at DESC LIMIT 5"
        )
    finally:
        db.close()
    print(json.dumps({"counts": counts, "recent_runs": runs}, ensure_ascii=False, indent=2))
    return 0


def _summary(output: str) -> str:
    found = SUMMARY.findall(output)
    if not found:
        return "no pytest summary"
    return ", ".join(f"{count} {kind}" for count, kind in found[-3:])


def _code_version(cwd: Path) -> str:
    """Best-effort git revision of the tested tree, for the test log."""

    try:
        completed = subprocess.run(  # noqa: S603 - fixed local command
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    revision = completed.stdout.strip()
    status = subprocess.run(  # noqa: S603 - fixed local command
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = " (working tree modified)" if status.stdout.strip() else ""
    return f"{revision}{dirty}"


if __name__ == "__main__":
    raise SystemExit(main())
