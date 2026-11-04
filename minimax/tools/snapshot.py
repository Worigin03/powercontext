#!/usr/bin/env python
"""Copy the demo project into a snapshot directory (baseline / after).

    python tools/snapshot.py --to validation/baseline
    python tools/snapshot.py --to validation/after --only src/ledgerflow/service.py
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "demo_project"
INCLUDE = ("src", "tests", "specs", "data", "pytest.ini")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True)
    parser.add_argument("--only", nargs="*", default=None, help="只复制指定相对路径")
    args = parser.parse_args()
    target_root = (ROOT / args.to).resolve()
    if target_root.exists() and not args.only:
        shutil.rmtree(target_root)
    for name in INCLUDE:
        source = PROJECT / name
        if not source.exists():
            continue
        if source.is_file():
            target = target_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            continue
        for path in source.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(PROJECT)
            if args.only and relative.as_posix() not in args.only:
                continue
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    print(f"snapshot -> {target_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
