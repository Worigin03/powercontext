"""Tokenisation and BM25 ranking used to score retrieved records.

Chinese text has no word delimiters, so CJK runs are indexed as unigrams and
bigrams; identifiers are split on snake_case and camelCase boundaries. SQL does
the coarse recall (`LIKE` on the indexed `search_text`), BM25 does the ordering.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence

ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
STOPWORDS = frozenset(
    {"req", "adr", "ctr", "ord", "the", "and", "for", "with", "this", "that"}
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in ASCII_WORD.findall(text):
        for part in re.split(r"_+", word):
            tokens.extend(match.group(0).lower() for match in CAMEL.finditer(part))
    for run in CJK_RUN.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def sql_terms(text: str, limit: int = 12) -> list[str]:
    """Terms safe to use inside a SQL ``LIKE`` recall query."""

    seen: list[str] = []
    for token in tokenize(text):
        if token in STOPWORDS:
            continue
        if len(token) < 2:
            continue
        if token.isascii() and len(token) < 3:
            continue
        if token not in seen:
            seen.append(token)
    return seen[:limit]


def bm25(
    query: str,
    documents: Sequence[str],
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> list[float]:
    query_tokens = set(tokenize(query))
    docs = [tokenize(document) for document in documents]
    if not docs or not query_tokens:
        return [0.0 for _ in docs]
    total = len(docs)
    lengths = [len(doc) for doc in docs]
    average = sum(lengths) / total if total else 0.0
    document_frequency: Counter[str] = Counter()
    for doc in docs:
        for token in set(doc):
            document_frequency[token] += 1
    scores: list[float] = []
    for doc, length in zip(docs, lengths, strict=True):
        frequencies = Counter(doc)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1 - b + b * (length / average if average else 0))
            score += idf * (frequency * (k1 + 1)) / denominator
        scores.append(score)
    return scores


def rank(
    query: str,
    items: Sequence[object],
    text_of: Callable[[object], str],
    *,
    limit: int | None = None,
) -> list[tuple[object, float]]:
    scores = bm25(query, [text_of(item) for item in items])
    ranked = sorted(zip(items, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    return ranked
