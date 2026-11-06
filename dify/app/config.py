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

# The Dify HTTP nodes must send this value in X-Pawnshop-Token. It is a
# deployment secret: change it in .env and in the two DSL files together.
API_TOKEN = os.getenv("PAWNSHOP_API_TOKEN", "change-me-pawnshop-token")

# fastembed is the default so retrieval is semantic out of the box. The hash
# backend stays available for machines that cannot download the model.
EMBED_BACKEND = os.getenv("PAWNSHOP_EMBED_BACKEND", "fastembed").strip().lower()
EMBED_MODEL = os.getenv(
    "PAWNSHOP_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384
MODEL_CACHE = Path(os.getenv("PAWNSHOP_MODEL_CACHE", str(ROOT / ".models")))

START_COINS = 120
DREAM_COST = 10
