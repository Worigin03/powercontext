"""Print the seekdb evidence used in docs/VALIDATION.md.

Usage:
    python -m scripts.verify_seekdb --player verify-2026 --query "关东煮"
"""

import argparse
import sys

from app import db
from app.embeddings import backend_name, embed
from app.db import vector_literal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="verify-2026")
    parser.add_argument("--query", default="关东煮")
    args = parser.parse_args()

    version = db.one("SELECT VERSION() AS v")["v"]
    tables = db.rows("SHOW TABLES")
    print("== seekdb ==")
    print("version:", version)
    print("database:", db.DB_CONFIG["database"])
    print("tables:", ", ".join(next(iter(row.values())) for row in tables))
    print("embedding backend:", backend_name())

    print("\n== schema ==")
    print(db.rows("SHOW CREATE TABLE memories")[0]["Create Table"])

    print("\n== memories ==")
    counts = db.rows(
        "SELECT status, COUNT(*) AS n FROM memories GROUP BY status ORDER BY status"
    )
    print("total:", db.one("SELECT COUNT(*) AS n FROM memories")["n"])
    for row in counts:
        print(f"  status={row['status']}: {row['n']}")

    print("\n== vector search ==")
    vec = vector_literal(embed(args.query))
    rows = db.rows(
        """SELECT id, owner_id, title, status,
                  ROUND(cosine_distance(embedding, %s), 4) AS distance
           FROM memories
           ORDER BY distance LIMIT 3""",
        [vec],
    )
    for row in rows:
        print(
            f"  id={row['id']} title={row['title']} owner={row['owner_id']} "
            f"status={row['status']} distance={row['distance']} "
            f"similarity={round(1 - float(row['distance']), 4)}"
        )

    print("\n== fulltext search (ik) ==")
    try:
        rows = db.rows(
            """SELECT id, title,
                      MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
               FROM memories
               WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE)
               ORDER BY score DESC LIMIT 3""",
            [args.query, args.query],
        )
        for row in rows:
            print(f"  id={row['id']} title={row['title']} score={row['score']}")
        if not rows:
            print("  (no fulltext hits for this query)")
    except Exception as error:  # fulltext is optional evidence, not a hard dependency
        print("  fulltext query failed:", error)

    print("\n== wallet / ledger ==")
    wallet = db.one("SELECT coins FROM wallets WHERE owner_id=%s", [args.player])
    print("player:", args.player, "coins:", wallet["coins"] if wallet else None)
    ledger = db.rows(
        "SELECT id, delta, reason, memory_id FROM ledger WHERE owner_id=%s ORDER BY id DESC LIMIT 8",
        [args.player],
    )
    for row in ledger:
        print(
            f"  #{row['id']} {row['delta']:+d} {row['reason']} memory={row['memory_id']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
