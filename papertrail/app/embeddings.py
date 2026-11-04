import threading

import numpy as np

from .config import EMBED_MODEL, MODEL_CACHE

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding

                _model = TextEmbedding(
                    model_name=EMBED_MODEL,
                    cache_dir=str(MODEL_CACHE),
                    threads=4,
                )
    return _model


def embed_texts(texts, batch_size=32):
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    model = get_model()
    vectors = np.array(
        list(model.embed(list(texts), batch_size=batch_size)), dtype=np.float32
    )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def embed_query(text):
    return embed_texts([text])[0]
