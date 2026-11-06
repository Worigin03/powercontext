#!/usr/bin/env python3
"""Delete one player's wallet, ledger and tickets so a replay starts at 120."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, service  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("player_id", help="e.g. acct:e2e-2026 or dify:<uid>")
    args = parser.parse_args()

    db.init_schema()
    result = service.reset_player(args.player_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
