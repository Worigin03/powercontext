#!/usr/bin/env python3
"""Compare the default semantic embeddings with the lexical hash fallback.

Run from the dify/ directory after installing requirements.txt. The script
loads the same 12 seed memories and ranks them for a paraphrase that shares few
literal characters with the target, then prints both top-3 lists.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.embeddings import _hash_embed, _fastembed_model  # noqa: E402
from app.seed import SEED  # noqa: E402


QUERY = "淋成落汤鸡推开家门，被家人一边数落一边照顾"


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rank(vectors):
    scored = sorted(
        ((cosine(vectors[0], vec), title) for vec, (_, title, _, _, _) in zip(vectors[1:], SEED)),
        reverse=True,
    )
    return scored[:3]


def main():
    texts = [QUERY] + [body for _, _, body, _, _ in SEED]
    model = _fastembed_model()
    semantic = [list(map(float, vec)) for vec in model.embed(texts)]
    lexical = [_hash_embed(text) for text in texts]

    print("query:", QUERY)
    print("\nfastembed (default, semantic):")
    for score, title in rank(semantic):
        print(f"  {score:.4f}  {title}")
    print("\nhash (offline fallback, literal overlap):")
    for score, title in rank(lexical):
        print(f"  {score:.4f}  {title}")


if __name__ == "__main__":
    main()
