import html
import re
import time

from . import db
from .config import RRF_K
from .embeddings import embed_query

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def load_glossary():
    return db.rows("SELECT zh, en FROM glossary")


def expand_query(query):
    """Chinese -> English term expansion so IK full-text can hit English papers."""
    matched = []
    for row in load_glossary():
        if row["zh"] in query:
            matched.append(row)
    extra = " ".join(r["en"] for r in matched)
    expanded = f"{query} {extra}".strip()
    return expanded, matched


def highlight_terms(query, matched):
    terms = set(w.lower() for w in WORD_RE.findall(query))
    for row in matched:
        terms.update(w.lower() for w in WORD_RE.findall(row["en"]))
    if CJK_RE.search(query):
        try:
            import jieba

            terms.update(w for w in jieba.cut(query) if len(w.strip()) >= 2)
        except Exception:
            terms.add(query.strip())
    terms = {t for t in terms if t and len(t) >= 2}
    return sorted(terms, key=len, reverse=True)[:14]


def make_snippet(content, terms, width=300):
    lower = content.lower()
    pos = -1
    for term in terms:
        idx = lower.find(term)
        if idx >= 0 and (pos < 0 or idx < pos):
            pos = idx
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 3)
    end = min(len(content), start + width)
    snippet = content[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    escaped = html.escape(prefix + snippet + suffix)
    patterns = [re.escape(html.escape(t)) for t in terms if t]
    if not patterns:
        return escaped
    return re.sub(
        "(" + "|".join(patterns) + ")",
        r"<mark>\1</mark>",
        escaped,
        flags=re.I,
    )


def _filter_sql(paper_ids):
    if not paper_ids:
        return "", []
    placeholders = ",".join(["%s"] * len(paper_ids))
    return f" AND paper_id IN ({placeholders})", list(paper_ids)


def _vector_hits(query_vec, limit, paper_ids):
    where, params = _filter_sql(paper_ids)
    sql = f"""SELECT id, paper_id, page_no, seq, section, content,
                     cosine_distance(embedding, %s) AS dist
              FROM chunks WHERE 1=1 {where}
              ORDER BY dist LIMIT %s"""
    return db.rows(sql, [db.vector_literal(query_vec), *params, limit])


def _fts_hits(expanded, limit, paper_ids):
    if not expanded.strip():
        return []
    where, params = _filter_sql(paper_ids)
    sql = f"""SELECT id, paper_id, page_no, seq, section, content,
                     MATCH(content) AGAINST(%s) AS bm
              FROM chunks WHERE MATCH(content) AGAINST(%s) {where}
              ORDER BY bm DESC LIMIT %s"""
    return db.rows(sql, [expanded, expanded, *params, limit])


def hybrid_search(query, top_k=10, paper_ids=None, mode="hybrid", log=True, per_paper=2):
    query = (query or "").strip()
    if not query:
        return {"query": query, "mode": mode, "hits": [], "glossary": [], "latency_ms": 0}
    started = time.time()
    expanded, matched = expand_query(query)
    terms = highlight_terms(query, matched)

    if mode == "keyword":
        vector_rows, fts_rows = [], _fts_hits(expanded, top_k * 3, paper_ids)
    elif mode == "semantic":
        vector_rows, fts_rows = _vector_hits(embed_query(query), top_k * 3, paper_ids), []
    else:
        vector_rows = _vector_hits(embed_query(query), top_k * 3, paper_ids)
        fts_rows = _fts_hits(expanded, top_k * 3, paper_ids)

    scores = {}
    for rank, row in enumerate(vector_rows):
        item = scores.setdefault(
            row["id"], {"row": row, "rrf": 0.0, "vector": None, "bm25": None, "vrank": None, "frank": None}
        )
        item["rrf"] += 1.0 / (RRF_K + rank + 1)
        item["vector"] = round(1.0 - float(row["dist"]), 4)
        item["vrank"] = rank + 1
    for rank, row in enumerate(fts_rows):
        item = scores.setdefault(
            row["id"], {"row": row, "rrf": 0.0, "vector": None, "bm25": None, "vrank": None, "frank": None}
        )
        item["rrf"] += 1.0 / (RRF_K + rank + 1)
        item["bm25"] = round(float(row["bm"]), 4)
        item["frank"] = rank + 1

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["rrf"], reverse=True)
    fused, per_counts = [], {}
    for chunk_id, item in ranked:
        if per_paper:
            pid = item["row"]["paper_id"]
            if per_counts.get(pid, 0) >= per_paper:
                continue
            per_counts[pid] = per_counts.get(pid, 0) + 1
        fused.append((chunk_id, item))
        if len(fused) >= top_k:
            break
    paper_ids_found = list({v["row"]["paper_id"] for _, v in fused})
    paper_map = {}
    if paper_ids_found:
        placeholders = ",".join(["%s"] * len(paper_ids_found))
        paper_map = {
            r["id"]: r
            for r in db.rows(
                f"SELECT id, title, authors, published, pdf_url FROM papers WHERE id IN ({placeholders})",
                paper_ids_found,
            )
        }

    hits = []
    for rank, (chunk_id, item) in enumerate(fused, start=1):
        row = item["row"]
        paper = paper_map.get(row["paper_id"], {})
        hits.append(
            {
                "rank": rank,
                "chunk_id": chunk_id,
                "paper_id": row["paper_id"],
                "title": paper.get("title", row["paper_id"]),
                "authors": paper.get("authors", ""),
                "published": paper.get("published", ""),
                "page_no": row["page_no"],
                "section": row["section"],
                "content": row["content"],
                "snippet": make_snippet(row["content"], terms),
                "vector_sim": item["vector"],
                "bm25": item["bm25"],
                "rrf": round(item["rrf"], 5),
                "ranks": {"vector": item["vrank"], "fulltext": item["frank"]},
            }
        )

    latency = int((time.time() - started) * 1000)
    if log:
        try:
            db.run(
                "INSERT INTO search_log (query, mode, latency_ms, hits) VALUES (%s,%s,%s,%s)",
                (query[:500], mode, latency, len(hits)),
            )
        except Exception:
            pass
    return {
        "query": query,
        "expanded": expanded if matched else "",
        "glossary": [{"zh": r["zh"], "en": r["en"]} for r in matched],
        "mode": mode,
        "latency_ms": latency,
        "hits": hits,
        "counts": {"vector": len(vector_rows), "fulltext": len(fts_rows)},
    }
