#!/usr/bin/env python3
"""Print the seekdb facts used as evidence: version, schema, indexes, SQL search.

Reads connection settings from the same environment as the service. Run from
the dify/ directory (or use ``python -m scripts.verify_seekdb``).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.db import vector_literal  # noqa: E402
from app.embeddings import backend_name, embed  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="acct:verify-2026")
    parser.add_argument("--query", default="淋雨跑回家")
    args = parser.parse_args()

    version = db.one("SELECT VERSION() AS v")["v"]
    tables = db.rows("SHOW TABLES")
    print("== seekdb ==")
    print("version:", version)
    print("database:", db.DB_CONFIG["database"])
    print("tables:", ", ".join(next(iter(row.values())) for row in tables))
    print("embedding backend:", backend_name())

    print("\n== schema: memories ==")
    print(db.one("SHOW CREATE TABLE memories")["Create Table"])

    print("\n== indexes: memories ==")
    for row in db.rows("SHOW INDEX FROM memories"):
        print(
            f"  key={row['Key_name']} seq={row['Seq_in_index']} "
            f"column={row['Column_name']} type={row['Index_type']}"
        )

    print("\n== schema: ledger ==")
    print(db.one("SHOW CREATE TABLE ledger")["Create Table"])

    print("\n== row counts ==")
    print("memories:", db.one("SELECT COUNT(*) AS n FROM memories")["n"])
    for row in db.rows("SELECT status, COUNT(*) AS n FROM memories GROUP BY status ORDER BY status"):
        print(f"  status={row['status']}: {row['n']}")
    print("ledger rows:", db.one("SELECT COUNT(*) AS n FROM ledger")["n"])

    print("\n== vector search: ORDER BY cosine_distance(...) ==")
    vec = vector_literal(embed(args.query))
    rows = db.rows(
        """SELECT id, owner_id, title, status,
                  cosine_distance(embedding, %s) AS dist
           FROM memories
           WHERE is_public=1
           ORDER BY dist LIMIT 3""",
        [vec],
    )
    print("query:", args.query)
    for row in rows:
        print(
            f"  id={row['id']} title={row['title']} status={row['status']} "
            f"distance={row['dist']:.4f} similarity={1 - float(row['dist']):.4f}"
        )

    print("\n== fulltext search: MATCH ... AGAINST (ik parser) ==")
    rows = db.rows(
        """SELECT id, title,
                  MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
           FROM memories
           WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE)
           ORDER BY score DESC LIMIT 3""",
        [args.query, args.query],
    )
    for row in rows:
        print(f"  id={row['id']} title={row['title']} score={row['score']:.4f}")
    if not rows:
        print("  (no fulltext hit for this query)")

    print("\n== wallet and ledger for", args.player, "==")
    wallet = db.one("SELECT coins, created_at FROM wallets WHERE owner_id=%s", [args.player])
    print("wallet:", json.dumps(wallet, ensure_ascii=False, default=str) if wallet else "none")
    for row in db.rows(
        "SELECT id, turn_id, delta, reason, memory_id, created_at FROM ledger "
        "WHERE owner_id=%s ORDER BY id",
        [args.player],
    ):
        print(" ", json.dumps(row, ensure_ascii=False, default=str))

    print("\n== this player's tickets ==")
    for row in db.rows(
        "SELECT id, title, status, appraisal, redeemed_at FROM memories "
        "WHERE owner_id=%s ORDER BY id",
        [args.player],
    ):
        print(" ", json.dumps(row, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
