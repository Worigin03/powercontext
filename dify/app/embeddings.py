"""Embedding backends.

The default backend hashes character n-grams into a fixed 384-dim vector.
It needs no model download and runs offline, which keeps the submission
reproducible on machines that cannot reach Hugging Face. A stronger
multilingual sentence-transformer backend is available by installing
``requirements-fastembed.txt`` and setting ``PAWNSHOP_EMBED_BACKEND=fastembed``.
"""

import hashlib
import math
import re

from .config import EMBED_BACKEND, EMBED_DIM, EMBED_MODEL, MODEL_CACHE

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WORD = re.compile(r"[0-9a-zA-Z_]+")
_model = None


def _hash_features(text):
    text = (text or "").lower()
    features = []
    for word in _WORD.findall(text):
        features.append((word, 1.0))
    chars = _CJK.findall(text)
    features.extend((char, 0.5) for char in chars)
    features.extend((a + b, 1.0) for a, b in zip(chars, chars[1:]))
    return features


def _hash_embed(text):
    vector = [0.0] * EMBED_DIM
    for feature, weight in _hash_features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:4], "little") % EMBED_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _fastembed_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(
            model_name=EMBED_MODEL,
            cache_dir=str(MODEL_CACHE),
            threads=4,
        )
    return _model


def backend_name():
    if EMBED_BACKEND == "fastembed":
        return f"fastembed:{EMBED_MODEL}"
    return "hash:char-ngram-384"


def embed_texts(texts):
    texts = list(texts or [])
    if not texts:
        return []
    if EMBED_BACKEND == "fastembed":
        return [list(map(float, vec)) for vec in _fastembed_model().embed(texts)]
    return [_hash_embed(text) for text in texts]


def embed(text):
    return embed_texts([text])[0]
