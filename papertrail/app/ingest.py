import io
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

from . import db
from .config import PDF_DIR
from .embeddings import embed_texts

ARXIV_API = "https://export.arxiv.org/api/query?id_list={ids}"
ARXIV_PDF = "https://arxiv.org/pdf/{id}"
UA = {"User-Agent": "PaperTrail/1.0 (research assistant; contact: local)"}
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

HEADINGS = {
    "abstract", "introduction", "related work", "background", "motivation",
    "method", "methods", "methodology", "approach", "architecture",
    "experiments", "experiment", "evaluation", "results", "discussion",
    "conclusion", "conclusions", "future work", "references", "bibliography",
    "acknowledgments", "acknowledgements", "appendix",
}
SKIP_SECTIONS = {"references", "bibliography", "acknowledgments", "acknowledgements"}

NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+([A-Za-z][A-Za-z0-9 ,\-:/&]{2,69})$")


def normalize_arxiv_id(raw):
    raw = raw.strip()
    raw = raw.rstrip("/")
    if raw.startswith("http"):
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:v\d+)?(?:\.pdf)?$", raw)
        if m:
            raw = m.group(1)
    m = re.search(r"(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", raw)
    if not m:
        raise ValueError(f"无法识别 arXiv ID: {raw}")
    return m.group(1)


def fetch_metadata(arxiv_id):
    url = ARXIV_API.format(ids=arxiv_id)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
        root = ET.fromstring(resp.read())
    entry = root.find("a:entry", ATOM)
    if entry is None:
        raise ValueError(f"arXiv 未返回 {arxiv_id} 的元数据")
    title = " ".join(entry.findtext("a:title", default="", namespaces=ATOM).split())
    abstract = " ".join(entry.findtext("a:summary", default="", namespaces=ATOM).split())
    published = entry.findtext("a:published", default="", namespaces=ATOM)[:10]
    authors = ", ".join(
        a.findtext("a:name", default="", namespaces=ATOM)
        for a in entry.findall("a:author", ATOM)
    )
    categories = ", ".join(
        c.get("term", "") for c in entry.findall("a:category", ATOM)
    )
    return {
        "arxiv_id": arxiv_id,
        "title": title or arxiv_id,
        "authors": authors,
        "abstract": abstract,
        "published": published,
        "categories": categories,
        "pdf_url": ARXIV_PDF.format(id=arxiv_id),
    }


def download_pdf(arxiv_id, dest=None):
    dest = dest or (PDF_DIR / f"{arxiv_id.replace('/', '_')}.pdf")
    if dest.exists() and dest.stat().st_size > 10000:
        return dest
    req = urllib.request.Request(ARXIV_PDF.format(id=arxiv_id), headers=UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
    if not data.startswith(b"%PDF"):
        raise ValueError("下载内容不是 PDF")
    dest.write_bytes(data)
    return dest


def clean_line(line):
    line = line.replace("\u00ad", "").replace("\ufb01", "fi").replace("\ufb02", "fl")
    line = re.sub(r"[\u200b-\u200f\u2028\u2029]", " ", line)
    return re.sub(r"[ \t]+", " ", line).strip()


def is_heading(line):
    if not line or len(line) > 80 or line[-1] in ".!?,;":
        return None
    low = line.lower().strip(" :")
    if low in HEADINGS:
        return line.strip(" :")
    m = NUMBERED_HEADING.match(line)
    if m and not re.search(r"\d{4}", line):
        return f"{m.group(1)} {m.group(2).strip()}"
    return None


def page_sections(text, current):
    groups = []
    buf, sec = [], current

    def flush():
        if buf:
            text = " ".join(buf)
            text = re.sub(r"([A-Za-z]{2,})-\s+([a-z]{2,})", r"\1\2", text)
            groups.append((sec, text))
            buf.clear()

    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if re.fullmatch(r"(arXiv:\S+|Page \d+|\d{1,3})", line):
            continue
        heading = is_heading(line)
        if heading:
            flush()
            sec = heading
            continue
        buf.append(line)
    flush()
    return groups, sec


def split_sentences(text):
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text, target=700, overlap_sentences=1):
    sentences = split_sentences(text)
    chunks, buf, length = [], [], 0
    for sent in sentences:
        if length + len(sent) > target and buf:
            chunk = " ".join(buf).strip()
            if len(chunk) >= 40:
                chunks.append(chunk)
            tail = buf[-overlap_sentences:] if overlap_sentences else []
            buf = list(tail)
            length = sum(len(s) for s in buf) + max(0, len(buf) - 1)
        buf.append(sent)
        length += len(sent) + 1
    chunk = " ".join(buf).strip()
    if len(chunk) >= 40:
        chunks.append(chunk)
    return chunks


def extract_chunks(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    section = "Abstract"
    result = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        groups, section = page_sections(text, section)
        seq = 0
        for sec, group_text in groups:
            if sec.lower() in SKIP_SECTIONS:
                continue
            for chunk in chunk_text(group_text):
                seq += 1
                result.append(
                    {
                        "page_no": page_index,
                        "seq": seq,
                        "section": sec[:200],
                        "content": chunk,
                        "tokens": max(1, len(chunk) // 4),
                    }
                )
    return len(reader.pages), result


def ingest_pdf(pdf_path, meta, force=False):
    arxiv_id = meta.get("arxiv_id")
    if arxiv_id:
        existing = db.one("SELECT id FROM papers WHERE arxiv_id=%s", (arxiv_id,))
        if existing and not force:
            return {"status": "skipped", "paper_id": existing["id"], "reason": "already ingested"}
    paper_id = meta.get("paper_id") or (arxiv_id or Path(pdf_path).stem).replace("/", "-")

    started = time.time()
    page_count, chunks = extract_chunks(Path(pdf_path).read_bytes())
    if not chunks:
        raise ValueError("PDF 未解析出可用文本（可能是扫描版）")

    vectors = embed_texts([c["content"] for c in chunks], batch_size=32)

    conn = db.connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE paper_id=%s", (paper_id,))
            cur.execute("DELETE FROM papers WHERE id=%s", (paper_id,))
            cur.execute(
                """INSERT INTO papers
                   (id, arxiv_id, title, authors, abstract, published, categories, pdf_url, page_count, chunk_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    paper_id, arxiv_id, meta["title"], meta.get("authors", ""),
                    meta.get("abstract", ""), meta.get("published", ""),
                    meta.get("categories", ""), meta.get("pdf_url", ""),
                    page_count, len(chunks),
                ),
            )
            payload = [
                (
                    paper_id, c["page_no"], c["seq"], c["section"], c["content"],
                    c["tokens"], db.vector_literal(vectors[i]),
                )
                for i, c in enumerate(chunks)
            ]
            cur.executemany(
                """INSERT INTO chunks
                   (paper_id, page_no, seq, section, content, tokens, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                payload,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "ok",
        "paper_id": paper_id,
        "pages": page_count,
        "chunks": len(chunks),
        "seconds": round(time.time() - started, 1),
    }


def ingest_arxiv(arxiv_id, force=False):
    arxiv_id = normalize_arxiv_id(arxiv_id)
    meta = fetch_metadata(arxiv_id)
    pdf_path = download_pdf(arxiv_id)
    return ingest_pdf(pdf_path, meta, force=force)


def ingest_upload(filename, data):
    stem = Path(filename).stem
    title = stem.replace("_", " ").replace("-", " ").strip() or "Untitled"
    meta = {
        "arxiv_id": None,
        "paper_id": "upload-" + re.sub(r"[^a-zA-Z0-9]+", "-", stem)[:40].strip("-").lower(),
        "title": title,
        "authors": "",
        "abstract": "",
        "published": "",
        "categories": "upload",
        "pdf_url": "",
    }
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename)
    path = PDF_DIR / f"upload-{int(time.time())}-{safe}"
    path.write_bytes(data)
    try:
        return ingest_pdf(path, meta)
    except Exception:
        path.unlink(missing_ok=True)
        raise
