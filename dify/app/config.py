import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_CONFIG = {
    "host": os.getenv("SEEKDB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SEEKDB_PORT", "2881")),
    "user": os.getenv("SEEKDB_USER", "root"),
    "password": os.getenv("SEEKDB_PASSWORD", ""),
    "database": os.getenv("SEEKDB_DATABASE", "memory_pawnshop"),
    "charset": "utf8mb4",
    "autocommit": True,
}

EMBED_MODEL = os.getenv(
    "PAWNSHOP_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384
MODEL_CACHE = Path(os.getenv("PAWNSHOP_MODEL_CACHE", str(ROOT / ".models")))

START_COINS = int(os.getenv("PAWNSHOP_START_COINS", "120"))
DREAM_COST = int(os.getenv("PAWNSHOP_DREAM_COST", "10"))
