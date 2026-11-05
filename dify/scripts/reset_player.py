"""Remove one demo player's memories, wallet and ledger so a sample can be replayed."""

import argparse
import sys

from app import db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("player_id")
    args = parser.parse_args()

    db.init_schema()
    db.execute("DELETE FROM ledger WHERE owner_id=%s", [args.player_id])
    db.execute("DELETE FROM wallets WHERE owner_id=%s", [args.player_id])
    removed = db.execute("DELETE FROM memories WHERE owner_id=%s", [args.player_id])
    print(f"reset {args.player_id}: removed {removed} memories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
