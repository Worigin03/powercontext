import json
import time
from contextlib import contextmanager

import pymysql

from .config import DB_CONFIG

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS memories (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        owner_id VARCHAR(64) NOT NULL,
        title VARCHAR(120) NOT NULL,
        content TEXT NOT NULL,
        emotion VARCHAR(24) NOT NULL,
        appraisal INT NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pawned',
        is_public TINYINT NOT NULL DEFAULT 1,
        embedding VECTOR(384) NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        redeemed_at DATETIME NULL,
        FULLTEXT INDEX ft_mem (content) WITH PARSER ik,
        VECTOR INDEX vidx_mem (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
        KEY idx_owner_status (owner_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS wallets (
        owner_id VARCHAR(64) PRIMARY KEY,
        coins INT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ledger (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        owner_id VARCHAR(64) NOT NULL,
        turn_id VARCHAR(64) NULL,
        delta INT NOT NULL,
        reason VARCHAR(160) NOT NULL,
        memory_id BIGINT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_owner (owner_id),
        UNIQUE KEY uk_owner_turn (owner_id, turn_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        k VARCHAR(64) PRIMARY KEY,
        v VARCHAR(255) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

LEDGER_MIGRATIONS = (
    ("turn_id", "ALTER TABLE ledger ADD COLUMN turn_id VARCHAR(64) NULL AFTER owner_id"),
    (
        "uk_owner_turn",
        "ALTER TABLE ledger ADD UNIQUE KEY uk_owner_turn (owner_id, turn_id)",
    ),
)


def connect(with_db=True):
    cfg = dict(DB_CONFIG)
    if not with_db:
        cfg.pop("database", None)
    conn = pymysql.connect(**cfg)
    with conn.cursor() as cur:
        cur.execute("SET SESSION ob_query_timeout = 600000000")
    return conn


def wait_ready(attempts=60, delay=2.0):
    last_error = None
    for _ in range(attempts):
        try:
            conn = connect(with_db=False)
            conn.close()
            return
        except pymysql.MySQLError as error:
            last_error = error
            time.sleep(delay)
    raise RuntimeError(f"seekdb is not ready: {last_error}")


def _migrate_ledger(cur):
    for name, sql in LEDGER_MIGRATIONS:
        try:
            cur.execute(sql)
        except pymysql.err.OperationalError as error:
            # 1060 duplicate column, 1061 duplicate key: already applied.
            if error.args and error.args[0] in (1060, 1061):
                continue
            raise


def init_schema():
    wait_ready()
    conn = connect(with_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` DEFAULT CHARSET utf8mb4"
            )
        conn.select_db(DB_CONFIG["database"])
        with conn.cursor() as cur:
            for sql in SCHEMA:
                cur.execute(sql)
            _migrate_ledger(cur)
    finally:
        conn.close()


@contextmanager
def tx():
    conn = connect()
    try:
        conn.begin()
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_meta(key):
    row = one("SELECT v FROM meta WHERE k=%s", [key])
    return row["v"] if row else None


def set_meta(key, value):
    run(
        "INSERT INTO meta (k, v) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE v=VALUES(v)",
        [key, value],
    )


def rows(sql, params=None):
    conn = connect()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def one(sql, params=None):
    result = rows(sql, params)
    return result[0] if result else None


def run(sql, params=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.lastrowid
    finally:
        conn.close()


def execute(sql, params=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount
    finally:
        conn.close()


def vector_literal(vec):
    return json.dumps([round(float(x), 6) for x in vec], separators=(",", ":"))
