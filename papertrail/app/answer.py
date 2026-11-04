import re
import time
from datetime import date

import numpy as np

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .embeddings import embed_query, embed_texts
from .retrieval import hybrid_search, highlight_terms

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u201c(])|(?<=[。！？])")
SKIP_START = re.compile(r"^(figure|table|fig\.|algorithm|eq\.|arxiv:)", re.I)


def split_sentences(text):
    out = []
    for s in SENT_SPLIT.split(text):
        s = " ".join(s.split())
        if 50 <= len(s) <= 520 and not SKIP_START.match(s) and "@" not in s:
            out.append(s)
    return out


def clean_section(sec):
    if not sec:
        return "正文"
    s = sec.strip()
    if len(s) > 34 or re.search(r"\d{2,}", s) or s.count(" ") > 6:
        return "正文"
    return s


def rank_sentences(query, hits, max_points=8, matched=None):
    query_vec = embed_query(query)
    candidates = []
    seen = set()
    for hit in hits:
        for sent in split_sentences(hit["content"]):
            key = sent[:90].lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append((hit, sent))
    if not candidates:
        return []
    sent_vecs = embed_texts([s for _, s in candidates])
    sims = sent_vecs @ query_vec
    terms = [t.lower() for t in highlight_terms(query, matched or []) if len(t) > 1]
    scored = []
    for i, (hit, sent) in enumerate(candidates):
        low = sent.lower()
        lexical = sum(1 for t in terms if t in low) / max(1, len(terms))
        rank_prior = 1.0 / (1.0 + hit["rank"] * 0.15)
        score = 0.62 * float(sims[i]) + 0.28 * lexical + 0.10 * rank_prior
        scored.append(
            {
                "text": sent,
                "paper_id": hit["paper_id"],
                "title": hit["title"],
                "authors": hit["authors"],
                "page_no": hit["page_no"],
                "section": clean_section(hit["section"]),
                "score": round(score, 4),
                "vector_sim": round(float(sims[i]), 4),
                "chunk_id": hit["chunk_id"],
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    selected, per_paper = [], {}
    floor = max(0.40, scored[0]["score"] * 0.70)
    for item in scored:
        if item["score"] < floor or len(selected) >= max_points:
            break
        if per_paper.get(item["paper_id"], 0) >= 2:
            continue
        per_paper[item["paper_id"]] = per_paper.get(item["paper_id"], 0) + 1
        selected.append(item)
    if not selected:
        selected = scored[:3]
    return selected


def llm_answer(query, evidence):
    if not (LLM_BASE_URL and LLM_API_KEY and LLM_MODEL):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=60)
        block = "\n\n".join(
            f"[{i+1}] {e['title']} (第 {e['page_no']} 页)\n{e['text']}"
            for i, e in enumerate(evidence)
        )
        prompt = (
            "你是科研文献助手。仅依据下列证据用中文回答问题，"
            "每个结论后用 [编号] 标注来源；证据不足时明确说明，不要编造。\n\n"
            f"问题：{query}\n\n证据：\n{block}"
        )
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=900,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return f"（大模型调用失败：{exc}）"


def answer_question(query, top_k=12, paper_ids=None):
    started = time.time()
    search = hybrid_search(query, top_k=top_k, paper_ids=paper_ids)
    evidence = rank_sentences(query, search["hits"], max_points=7, matched=search["glossary"])
    papers, index = [], {}
    for item in evidence:
        if item["paper_id"] not in index:
            index[item["paper_id"]] = len(papers) + 1
            papers.append(
                {
                    "ref": f"P{len(papers) + 1}",
                    "paper_id": item["paper_id"],
                    "title": item["title"],
                    "page_no": item["page_no"],
                }
            )
        item["ref"] = f"P{index[item['paper_id']]}"
    sections = []
    for item in evidence:
        if item["section"] and item["section"] not in sections:
            sections.append(item["section"])
    summary = (
        f"从 {len(papers)} 篇文献中定位到 {len(evidence)} 条直接证据"
        + (f"，集中在 {', '.join(sections[:4])} 等章节。" if sections else "。")
    ) if evidence else "没有找到足够相关的证据，试试更具体的研究问题或切换检索模式。"
    generated = llm_answer(query, evidence) if evidence else None
    return {
        "query": query,
        "summary": summary,
        "evidence": evidence,
        "papers": papers,
        "generated": generated,
        "llm_enabled": bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL),
        "glossary": search["glossary"],
        "latency_ms": int((time.time() - started) * 1000),
    }


def compare_papers(query, paper_ids):
    started = time.time()
    columns = []
    for pid in paper_ids:
        search = hybrid_search(query, top_k=6, paper_ids=[pid], log=False)
        evidence = rank_sentences(query, search["hits"], max_points=2, matched=search["glossary"])
        paper = search["hits"][0] if search["hits"] else {}
        columns.append(
            {
                "paper_id": pid,
                "title": paper.get("title", pid),
                "authors": paper.get("authors", ""),
                "published": paper.get("published", ""),
                "evidence": evidence,
                "top_score": evidence[0]["score"] if evidence else 0,
            }
        )
    return {
        "query": query,
        "columns": columns,
        "latency_ms": int((time.time() - started) * 1000),
    }


def review_markdown(topic, paper_ids=None, top_k=5):
    search = hybrid_search(topic, top_k=80, paper_ids=paper_ids, per_paper=4)
    grouped = {}
    for hit in search["hits"]:
        grouped.setdefault(hit["paper_id"], []).append(hit)
    lines = [
        f"# 文献综述素材：{topic}",
        "",
        f"> 由 PaperTrail 基于 seekdb 混合检索生成 · {date.today().isoformat()}",
        "> 所有引文均可在原文对应页码核验，未做自动改写。",
        "",
        "## 检索概况",
        "",
        f"- 候选证据：{len(search['hits'])} 条，覆盖 {len(grouped)} 篇文献",
        f"- 检索模式：向量 + IK 全文 + RRF 融合，耗时 {search['latency_ms']} ms",
    ]
    if search["glossary"]:
        lines.append(
            "- 术语扩展：" + "、".join(f"{g['zh']} → {g['en']}" for g in search["glossary"])
        )
    lines += ["", "## 分文献证据", ""]
    refs = []
    for i, (pid, hits) in enumerate(grouped.items(), start=1):
        top = hits[0]
        refs.append(f"[{i}] {top['authors']}. {top['title']}. arXiv, {top['published']}. {top.get('pdf_url') or ''}".strip())
        lines += [f"### [{i}] {top['title']}", "", f"*{top['authors']} · {top['published']}*", ""]
        seen = set()
        for hit in hits[:top_k]:
            sent = split_sentences(hit["content"])
            quote = (sent[0] if sent else hit["content"][:240]).strip()
            if quote[:60] in seen:
                continue
            seen.add(quote[:60])
            lines.append(f"- “{quote}”  \n  — 第 {hit['page_no']} 页 · {hit['section'] or '正文'}")
        lines.append("")
    lines += ["## 参考文献", ""] + refs
    return "\n".join(lines)
