"""seekdb / OceanBase persistence for specs, code symbols and tests.

The module talks plain MySQL protocol, so the same code works against the
seekdb container (``127.0.0.1:2881``) and an OceanBase tenant. Every statement
that feeds a context pack is written to a query log, which is the audit trail
used by the validation sample.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from sdd_context.config import DatabaseConfig
from sdd_context.models import CodeSymbol, SpecItem, TestCase

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS spec_item (
        item_id       VARCHAR(64)  NOT NULL,
        item_kind     VARCHAR(24)  NOT NULL,
        title         VARCHAR(255) NOT NULL,
        item_status   VARCHAR(24)  NOT NULL,
        module        VARCHAR(64)  NOT NULL,
        tags          VARCHAR(255) NOT NULL,
        applies_to    VARCHAR(512) NOT NULL,
        verified_by   VARCHAR(512) NOT NULL,
        supersedes    VARCHAR(255) NOT NULL,
        superseded_by VARCHAR(128) NOT NULL,
        affects       VARCHAR(255) NOT NULL DEFAULT '',
        body          MEDIUMTEXT   NOT NULL,
        search_text   MEDIUMTEXT   NOT NULL,
        source_path   VARCHAR(255) NOT NULL,
        source_line   INT          NOT NULL,
        revision      CHAR(64)     NOT NULL,
        updated_at    DATETIME     NOT NULL,
        PRIMARY KEY (item_id),
        KEY idx_spec_status (item_status),
        KEY idx_spec_module (module)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS code_symbol (
        symbol_id   VARCHAR(255) NOT NULL,
        path        VARCHAR(255) NOT NULL,
        qualname    VARCHAR(255) NOT NULL,
        symbol_kind VARCHAR(24)  NOT NULL,
        lineno      INT          NOT NULL,
        end_lineno  INT          NOT NULL,
        signature   VARCHAR(512) NOT NULL,
        docstring   TEXT         NOT NULL,
        body_text   MEDIUMTEXT   NOT NULL,
        search_text MEDIUMTEXT   NOT NULL,
        module      VARCHAR(64)  NOT NULL,
        revision    CHAR(64)     NOT NULL,
        PRIMARY KEY (symbol_id),
        KEY idx_symbol_path (path)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS test_case (
        test_id    VARCHAR(255) NOT NULL,
        path       VARCHAR(255) NOT NULL,
        test_name  VARCHAR(255) NOT NULL,
        test_kind  VARCHAR(24)  NOT NULL,
        covers     VARCHAR(255) NOT NULL,
        docstring  TEXT         NOT NULL,
        lineno     INT          NOT NULL,
        module     VARCHAR(64)  NOT NULL,
        PRIMARY KEY (test_id),
        KEY idx_test_path (path)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_run (
        run_id      VARCHAR(64)  NOT NULL,
        started_at  DATETIME     NOT NULL,
        finished_at DATETIME     NOT NULL,
        source_root VARCHAR(255) NOT NULL,
        spec_count  INT          NOT NULL,
        symbol_count INT         NOT NULL,
        test_count  INT          NOT NULL,
        note        VARCHAR(512) NOT NULL,
        PRIMARY KEY (run_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def content_revision(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


class QueryLog:
    """Append-only record of every SQL statement issued by the module."""

    def __init__(self, path: str | None) -> None:
        self._path = path
        self.entries: list[dict[str, Any]] = []

    def write(self, sql: str, params: Sequence[Any], rows: int) -> None:
        entry = {
            "at": utc_now(),
            "sql": " ".join(sql.split()),
            "params": [str(param)[:120] for param in params],
            "rows": rows,
        }
        self.entries.append(entry)
        if self._path:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class Database:
    def __init__(self, config: DatabaseConfig, query_log: QueryLog | None = None) -> None:
        self.config = config
        self.log = query_log or QueryLog(None)
        self._conn: pymysql.connections.Connection | None = None

    # -- connection -----------------------------------------------------
    def _connect(self, database: str | None) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=database,
            charset=self.config.charset,
            cursorclass=DictCursor,
            autocommit=False,
        )

    @property
    def conn(self) -> pymysql.connections.Connection:
        if self._conn is None:
            self._conn = self._connect(self.config.database)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def ensure_database(self) -> None:
        conn = self._connect(None)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        self.ensure_database()
        with self.conn.cursor() as cursor:
            for statement in SCHEMA:
                cursor.execute(statement)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema version."""

        rows = self.query(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='spec_item'",
            (self.config.database,),
        )
        names = {row["COLUMN_NAME"].upper() for row in rows}
        if "AFFECTS" not in names:
            self.execute(
                "ALTER TABLE spec_item ADD COLUMN affects VARCHAR(255) NOT NULL DEFAULT '' "
                "AFTER superseded_by"
            )
        self.conn.commit()

    # -- primitives -----------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall())
        self.log.write(sql, params, len(rows))
        return rows

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.conn.cursor() as cursor:
            affected = cursor.execute(sql, tuple(params))
        self.log.write(sql, params, affected)
        return affected

    def commit(self) -> None:
        self.conn.commit()

    # -- writes ---------------------------------------------------------
    def upsert_specs(self, items: Iterable[SpecItem]) -> int:
        sql = """
            INSERT INTO spec_item (item_id, item_kind, title, item_status, module, tags,
                applies_to, verified_by, supersedes, superseded_by, affects, body, search_text,
                source_path, source_line, revision, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE item_kind=VALUES(item_kind), title=VALUES(title),
                item_status=VALUES(item_status), module=VALUES(module), tags=VALUES(tags),
                applies_to=VALUES(applies_to), verified_by=VALUES(verified_by),
                supersedes=VALUES(supersedes), superseded_by=VALUES(superseded_by),
                affects=VALUES(affects), body=VALUES(body), search_text=VALUES(search_text),
                source_path=VALUES(source_path), source_line=VALUES(source_line),
                revision=VALUES(revision), updated_at=VALUES(updated_at)
        """
        now = utc_now()
        count = 0
        for item in items:
            self.execute(
                sql,
                (
                    item.item_id,
                    item.kind,
                    item.title[:255],
                    item.status,
                    item.module,
                    ",".join(item.tags)[:255],
                    ",".join(item.applies_to)[:512],
                    ",".join(item.verified_by)[:512],
                    ",".join(item.supersedes)[:255],
                    item.superseded_by,
                    ",".join(item.affects)[:255],
                    item.body,
                    _spec_search_text(item),
                    item.source_path,
                    item.source_line,
                    item.revision,
                    now,
                ),
            )
            count += 1
        return count

    def upsert_symbols(self, symbols: Iterable[CodeSymbol], revision: str) -> int:
        sql = """
            INSERT INTO code_symbol (symbol_id, path, qualname, symbol_kind, lineno, end_lineno,
                signature, docstring, body_text, search_text, module, revision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE path=VALUES(path), qualname=VALUES(qualname),
                symbol_kind=VALUES(symbol_kind), lineno=VALUES(lineno),
                end_lineno=VALUES(end_lineno), signature=VALUES(signature),
                docstring=VALUES(docstring), body_text=VALUES(body_text),
                search_text=VALUES(search_text), module=VALUES(module), revision=VALUES(revision)
        """
        count = 0
        for symbol in symbols:
            self.execute(
                sql,
                (
                    symbol.symbol_id,
                    symbol.path,
                    symbol.qualname,
                    symbol.symbol_kind,
                    symbol.lineno,
                    symbol.end_lineno,
                    symbol.signature[:512],
                    symbol.docstring,
                    symbol.source,
                    f"{symbol.qualname} {symbol.signature} {symbol.docstring} {symbol.path}",
                    symbol.module,
                    revision,
                ),
            )
            count += 1
        return count

    def upsert_tests(self, tests: Iterable[TestCase]) -> int:
        sql = """
            INSERT INTO test_case (test_id, path, test_name, test_kind, covers, docstring,
                lineno, module)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE path=VALUES(path), test_name=VALUES(test_name),
                test_kind=VALUES(test_kind), covers=VALUES(covers),
                docstring=VALUES(docstring), lineno=VALUES(lineno), module=VALUES(module)
        """
        count = 0
        for test in tests:
            self.execute(
                sql,
                (
                    test.test_id,
                    test.path,
                    test.name,
                    test.kind,
                    ",".join(test.covers)[:255],
                    test.docstring,
                    test.lineno,
                    test.module,
                ),
            )
            count += 1
        return count

    def prune(self, table: str, key: str, keep: Sequence[str]) -> None:
        if not keep:
            self.execute(f"DELETE FROM {table}")
            return
        placeholders = ",".join(["%s"] * len(keep))
        self.execute(f"DELETE FROM {table} WHERE {key} NOT IN ({placeholders})", list(keep))

    def record_run(
        self,
        run_id: str,
        source_root: str,
        specs: int,
        symbols: int,
        tests: int,
        note: str,
    ) -> None:
        now = utc_now()
        self.execute(
            """
            INSERT INTO ingest_run (run_id, started_at, finished_at, source_root,
                spec_count, symbol_count, test_count, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE finished_at=VALUES(finished_at), spec_count=VALUES(spec_count),
                symbol_count=VALUES(symbol_count), test_count=VALUES(test_count), note=VALUES(note)
            """,
            (run_id, now, now, source_root, specs, symbols, tests, note[:512]),
        )

    # -- reads ----------------------------------------------------------
    def fetch_specs(self, statuses: Sequence[str] | None = None) -> list[SpecItem]:
        sql = "SELECT * FROM spec_item"
        params: list[Any] = []
        if statuses:
            placeholders = ",".join(["%s"] * len(statuses))
            sql += f" WHERE item_status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY item_id"
        return [self._row_to_spec(row) for row in self.query(sql, params)]

    def search_specs(self, keywords: Sequence[str], statuses: Sequence[str]) -> list[SpecItem]:
        if not keywords:
            return self.fetch_specs(statuses)
        placeholders = ",".join(["%s"] * len(statuses))
        like = " OR ".join(["search_text LIKE %s"] * len(keywords))
        sql = (
            "SELECT * FROM spec_item "
            f"WHERE item_status IN ({placeholders}) AND ({like}) ORDER BY item_id"
        )
        params: list[Any] = list(statuses) + [f"%{kw}%" for kw in keywords]
        return [self._row_to_spec(row) for row in self.query(sql, params)]

    def fetch_symbols(self) -> list[CodeSymbol]:
        rows = self.query("SELECT * FROM code_symbol ORDER BY symbol_id")
        return [self._row_to_symbol(row) for row in rows]

    def search_symbols(self, keywords: Sequence[str], limit: int = 40) -> list[CodeSymbol]:
        if not keywords:
            rows = self.query("SELECT * FROM code_symbol ORDER BY symbol_id LIMIT %s", (limit,))
        else:
            like = " OR ".join(["search_text LIKE %s"] * len(keywords))
            rows = self.query(
                f"SELECT * FROM code_symbol WHERE {like} ORDER BY symbol_id LIMIT %s",
                [f"%{kw}%" for kw in keywords] + [limit],
            )
        return [self._row_to_symbol(row) for row in rows]

    def symbols_for(self, qualnames: Sequence[str]) -> list[CodeSymbol]:
        if not qualnames:
            return []
        placeholders = ",".join(["%s"] * len(qualnames))
        rows = self.query(
            f"SELECT * FROM code_symbol WHERE qualname IN ({placeholders}) ORDER BY qualname",
            list(qualnames),
        )
        return [self._row_to_symbol(row) for row in rows]

    def fetch_tests(self, paths: Sequence[str] | None = None) -> list[TestCase]:
        if paths:
            placeholders = ",".join(["%s"] * len(paths))
            rows = self.query(
                f"SELECT * FROM test_case WHERE path IN ({placeholders}) ORDER BY test_id",
                list(paths),
            )
        else:
            rows = self.query("SELECT * FROM test_case ORDER BY test_id")
        return [self._row_to_test(row) for row in rows]

    def search_tests(self, keywords: Sequence[str], limit: int = 60) -> list[TestCase]:
        if not keywords:
            rows = self.query("SELECT * FROM test_case ORDER BY test_id LIMIT %s", (limit,))
        else:
            like = " OR ".join(
                ["test_name LIKE %s"] * len(keywords) + ["docstring LIKE %s"] * len(keywords)
            )
            params: list[Any] = [f"%{kw}%" for kw in keywords] + [
                f"%{kw}%" for kw in keywords
            ]
            rows = self.query(
                f"SELECT * FROM test_case WHERE {like} ORDER BY test_id LIMIT %s",
                params + [limit],
            )
        return [self._row_to_test(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table in ("spec_item", "code_symbol", "test_case", "ingest_run"):
            rows = self.query(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = int(rows[0]["n"]) if rows else 0
        return result

    # -- mapping --------------------------------------------------------
    @staticmethod
    def _row_to_spec(row: dict[str, Any]) -> SpecItem:
        return SpecItem(
            item_id=row["item_id"],
            title=row["title"],
            kind=row["item_kind"],
            status=row["item_status"],
            module=row["module"],
            tags=_split(row["tags"]),
            applies_to=_split(row["applies_to"]),
            verified_by=_split(row["verified_by"]),
            supersedes=_split(row["supersedes"]),
            superseded_by=row["superseded_by"] or "",
            affects=_split(row["affects"]),
            body=row["body"],
            source_path=row["source_path"],
            source_line=int(row["source_line"]),
            revision=row["revision"],
        )

    @staticmethod
    def _row_to_symbol(row: dict[str, Any]) -> CodeSymbol:
        return CodeSymbol(
            symbol_id=row["symbol_id"],
            path=row["path"],
            qualname=row["qualname"],
            symbol_kind=row["symbol_kind"],
            lineno=int(row["lineno"]),
            end_lineno=int(row["end_lineno"]),
            signature=row["signature"],
            docstring=row["docstring"] or "",
            module=row["module"],
            source=row["body_text"],
        )

    @staticmethod
    def _row_to_test(row: dict[str, Any]) -> TestCase:
        return TestCase(
            test_id=row["test_id"],
            path=row["path"],
            name=row["test_name"],
            kind=row["test_kind"],
            covers=_split(row["covers"]),
            docstring=row["docstring"] or "",
            lineno=int(row["lineno"]),
            module=row["module"],
        )


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _spec_search_text(item: SpecItem) -> str:
    return " ".join(
        [
            item.item_id,
            item.title,
            item.module,
            " ".join(item.tags),
            " ".join(item.applies_to),
            " ".join(item.verified_by),
            " ".join(item.affects),
            item.body,
        ]
    )
