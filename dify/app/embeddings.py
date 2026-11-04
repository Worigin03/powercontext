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


def embed_texts(texts):
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    return np.array(list(get_model().embed(list(texts))), dtype=np.float32)


def embed(text):
    return embed_texts([text])[0]
