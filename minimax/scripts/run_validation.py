#!/usr/bin/env python
"""End-to-end validation for the SDD context module.

    python scripts/run_validation.py --reset

Steps: reset the demo project, ingest specs into seekdb, prove the target tests
fail, build a context pack, apply the recorded reference change, prove target +
regression tests pass while the state is written to PowerContext, then rebuild
the pack in a fresh process and check that the saved state is reused. Finally
the spec is updated and re-ingested to show the pack is not hard-coded.

Every command, exit code and output tail is written to
``records/validation-report.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "demo_project"
TASK_FILE = ROOT / "tasks" / "TASK-20261104-01.json"
TASK_ID = "TASK-20261104-01"
RECORDS = ROOT / "records"
BASELINE = ROOT / "validation" / "baseline"
AFTER = ROOT / "validation" / "after"
SPEC_V2 = ROOT / "validation" / "spec-v2"


def run(command: list[str], *, cwd: Path = ROOT, expect: int | None = 0) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(  # noqa: S603 - explicit local commands
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    record = {
        "command": " ".join(command),
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "output_tail": output[-2500:],
    }
    print(f"$ {' '.join(command)}  (exit {completed.returncode})")
    if expect is not None and completed.returncode != expect:
        print(output[-4000:], file=sys.stderr)
        raise SystemExit(f"unexpected exit code {completed.returncode}")
    return record


def cli(*args: str, expect: int | None = 0) -> dict:
    return run([sys.executable, "-m", "sdd_context", *args], expect=expect)


def reset_project() -> None:
    for path in PROJECT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)
    for path in BASELINE.rglob("*"):
        if path.is_file():
            target = PROJECT / path.relative_to(BASELINE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def apply_reference() -> list[str]:
    changed: list[str] = []
    for path in AFTER.rglob("*"):
        if path.is_file():
            relative = path.relative_to(AFTER)
            target = PROJECT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            changed.append(relative.as_posix())
    return changed


def read_pack(name: str) -> str:
    return (RECORDS / "context" / name).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="先把 demo_project 恢复为修改前快照")
    parser.add_argument("--skip-spec-update", action="store_true")
    args = parser.parse_args()

    if not (BASELINE / "src" / "ledgerflow" / "service.py").exists():
        print("missing validation/baseline; run tools/snapshot_baseline.py first", file=sys.stderr)
        return 2

    report: dict = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "steps": [],
        "checks": {},
    }

    if args.reset:
        reset_project()
        report["steps"].append({"step": "reset", "restored_from": str(BASELINE)})

    report["steps"].append({"step": "ingest", **cli("ingest", "--repo", "demo_project", "--note", "validation v1")})

    before = run(
        [sys.executable, "-m", "pytest", "-m", "target", "--tb=line", "-p", "no:cacheprovider"],
        cwd=PROJECT,
        expect=None,
    )
    report["steps"].append({"step": "target-tests-before", **before})
    report["checks"]["target_failed_before"] = before["exit_code"] != 0

    report["steps"].append(
        {
            "step": "prepare-initial",
            **cli("prepare", "--task", str(TASK_FILE), "--save", "--allow-empty-state"),
        }
    )
    report["steps"].append(
        {
            "step": "state-initial",
            **cli(
                "state",
                "set",
                "--task",
                TASK_ID,
                "--done",
                "已导入规格、代码与测试索引",
                "--todo",
                "实现 REQ-ORD-014 金额区间筛选",
                "--todo",
                "运行目标与回归测试",
            ),
        }
    )

    changed = apply_reference()
    report["steps"].append({"step": "apply-reference", "files": changed})

    target_after = cli(
        "verify", "--task", TASK_ID, "--cwd", str(PROJECT), "--label", "target",
        "--", sys.executable, "-m", "pytest", "-m", "target", "--tb=line",
        expect=None,
    )
    report["steps"].append({"step": "target-tests-after", **target_after})
    report["checks"]["target_passed_after"] = target_after["exit_code"] == 0

    regression_after = cli(
        "verify", "--task", TASK_ID, "--cwd", str(PROJECT), "--label", "regression",
        "--", sys.executable, "-m", "pytest", "-m", "regression", "--tb=line",
        expect=None,
    )
    report["steps"].append({"step": "regression-tests-after", **regression_after})
    report["checks"]["regression_passed_after"] = regression_after["exit_code"] == 0

    report["steps"].append(
        {"step": "prepare-after", **cli("prepare", "--task", str(TASK_FILE), "--save")}
    )
    after_pack = read_pack(f"{TASK_ID}.md")
    report["checks"]["pack_has_state"] = "已验证" in after_pack and "实现 REQ-ORD-014" in after_pack
    report["checks"]["pack_has_spec"] = "REQ-ORD-014" in after_pack
    report["checks"]["pack_has_target_tests"] = "test_amount_filter.py" in after_pack
    report["checks"]["pack_has_code_symbol"] = "ledgerflow.service.OrderQueryService.search" in after_pack
    section_two = after_pack.split("## 2.")[1].split("## 3.")[0] if "## 2." in after_pack else ""
    report["checks"]["pack_excludes_superseded_from_active"] = (
        "### REQ-ORD-009" not in section_two and "### ADR-003" not in section_two
    )
    report["checks"]["pack_lists_invalidated"] = "REQ-ORD-009" in after_pack.split("## 4.")[1].split(
        "## 5."
    )[0]

    if not args.skip_spec_update:
        report["steps"].append(
            {
                "step": "ingest-spec-v2",
                **cli(
                    "ingest",
                    "--repo",
                    ".",
                    "--specs",
                    str(SPEC_V2),
                    "--src",
                    str(PROJECT / "src"),
                    "--tests",
                    str(PROJECT / "tests"),
                    "--note",
                    "spec update demo",
                ),
            }
        )
        report["steps"].append(
            {
                "step": "prepare-v2",
                **cli("prepare", "--task", str(TASK_FILE), "--out", str(RECORDS / "context" / "TASK-v2.md")),
            }
        )
        v2_pack = (RECORDS / "context" / "TASK-v2.md").read_text(encoding="utf-8")
        report["checks"]["spec_update_visible"] = "订单应付金额区间筛选" in v2_pack
        report["steps"].append(
            {"step": "restore-v1", **cli("ingest", "--repo", "demo_project", "--note", "restore v1")}
        )

    report["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["ok"] = all(report["checks"].values())
    RECORDS.mkdir(exist_ok=True)
    (RECORDS / "validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["checks"], ensure_ascii=False, indent=2))
    print("OK" if report["ok"] else "FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
