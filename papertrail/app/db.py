import json
import pymysql

from .config import DB_CONFIG

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS papers (
        id VARCHAR(64) PRIMARY KEY,
        arxiv_id VARCHAR(32),
        title TEXT NOT NULL,
        authors TEXT,
        abstract MEDIUMTEXT,
        published VARCHAR(16),
        categories VARCHAR(200),
        pdf_url VARCHAR(500),
        page_count INT DEFAULT 0,
        chunk_count INT DEFAULT 0,
        ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_arxiv (arxiv_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        paper_id VARCHAR(64) NOT NULL,
        page_no INT NOT NULL,
        seq INT NOT NULL,
        section VARCHAR(200),
        content MEDIUMTEXT NOT NULL,
        tokens INT DEFAULT 0,
        embedding VECTOR(384) NOT NULL,
        FULLTEXT INDEX ft_content(content) WITH PARSER ik,
        VECTOR INDEX vidx_vec(embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
        KEY idx_paper (paper_id),
        UNIQUE KEY uk_chunk (paper_id, page_no, seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS glossary (
        id INT AUTO_INCREMENT PRIMARY KEY,
        zh VARCHAR(64) NOT NULL,
        en VARCHAR(200) NOT NULL,
        note VARCHAR(200),
        UNIQUE KEY uk_zh (zh)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS notes (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        paper_id VARCHAR(64) NOT NULL,
        page_no INT,
        quote TEXT,
        comment TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_note_paper (paper_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS search_log (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        query TEXT NOT NULL,
        mode VARCHAR(20),
        latency_ms INT,
        hits INT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def connect(with_db=True):
    cfg = dict(DB_CONFIG)
    if not with_db:
        cfg.pop("database", None)
    return pymysql.connect(**cfg)


def init_schema():
    conn = connect(with_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` "
                "DEFAULT CHARSET utf8mb4"
            )
        conn.select_db(DB_CONFIG["database"])
        with conn.cursor() as cur:
            for sql in SCHEMA:
                cur.execute(sql)
    finally:
        conn.close()


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


def vector_literal(vec):
    return json.dumps([round(float(x), 6) for x in vec], separators=(",", ":"))
