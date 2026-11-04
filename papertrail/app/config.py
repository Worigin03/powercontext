import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
MODEL_CACHE = ROOT / ".models"
WEB_DIR = ROOT / "web"

PDF_DIR.mkdir(parents=True, exist_ok=True)
MODEL_CACHE.mkdir(parents=True, exist_ok=True)

DB_CONFIG = {
    "host": os.getenv("SEEKDB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SEEKDB_PORT", "2881")),
    "user": os.getenv("SEEKDB_USER", "root"),
    "password": os.getenv("SEEKDB_PASSWORD", ""),
    "database": os.getenv("SEEKDB_DATABASE", "papertrail"),
    "charset": "utf8mb4",
    "autocommit": True,
}

EMBED_MODEL = os.getenv(
    "PAPERTRAIL_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384

LLM_BASE_URL = os.getenv("PAPERTRAIL_LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("PAPERTRAIL_LLM_API_KEY", "")
LLM_MODEL = os.getenv("PAPERTRAIL_LLM_MODEL", "")

RRF_K = 60
