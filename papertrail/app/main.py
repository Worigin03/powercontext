import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .answer import answer_question, compare_papers, review_markdown
from .config import EMBED_MODEL, LLM_API_KEY, LLM_MODEL, WEB_DIR
from .embeddings import get_model
from .ingest import ingest_arxiv, ingest_upload
from .retrieval import hybrid_search, load_glossary

app = FastAPI(title="PaperTrail API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TOPIC_LABELS = {
    "cs.LG": "机器学习",
    "cs.CL": "自然语言处理",
    "cs.AI": "人工智能",
    "cs.DC": "分布式系统",
    "cs.NI": "网络",
    "cs.IR": "信息检索",
    "cs.CV": "计算机视觉",
    "cs.RO": "机器人",
    "cs.CR": "安全",
    "cs.IT": "通信",
    "eess.SP": "通信",
}

TITLE_TOPICS = [
    ("federated", "联邦学习"),
    ("edge", "边缘计算"),
    ("internet of things", "物联网"),
    ("6g", "物联网"),
    ("retrieval-augmented", "检索增强"),
    ("nearest neighbor", "向量检索"),
    ("low-rank adaptation", "参数高效微调"),
    ("lora", "参数高效微调"),
    ("chain-of-thought", "推理增强"),
    ("agent", "智能体"),
    ("bert", "预训练语言模型"),
    ("attention is all", "神经网络架构"),
]

JOBS = {}
JOBS_LOCK = threading.Lock()


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    mode: str = Field(default="hybrid", pattern="^(hybrid|semantic|keyword)$")
    paper_ids: list[str] | None = None


class AnswerBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=12, ge=3, le=30)
    paper_ids: list[str] | None = None


class CompareBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    paper_ids: list[str] = Field(min_length=2, max_length=4)


class ReviewBody(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    paper_ids: list[str] | None = None


class IngestBody(BaseModel):
    arxiv: str = Field(min_length=1, max_length=300)
    force: bool = False


class NoteBody(BaseModel):
    paper_id: str
    page_no: int | None = None
    quote: str = ""
    comment: str = Field(min_length=1, max_length=2000)


def topic_of(categories, title=""):
    low = (title or "").lower()
    for needle, label in TITLE_TOPICS:
        if re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", low):
            return label
    if not categories:
        return "其他"
    for cat in categories.split(","):
        cat = cat.strip()
        if cat in TOPIC_LABELS:
            return TOPIC_LABELS[cat]
    return "其他"


@app.on_event("startup")
def startup():
    db.init_schema()

    def warm():
        try:
            get_model()
        except Exception as exc:
            print("embedding warmup failed:", exc)

    threading.Thread(target=warm, daemon=True).start()


@app.get("/api/health")
def health():
    try:
        version = db.one("SELECT VERSION() v")["v"]
        return {
            "ok": True,
            "seekdb": version,
            "embedding": EMBED_MODEL,
            "llm_enabled": bool(LLM_MODEL and LLM_API_KEY),
        }
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.get("/api/stats")
def stats():
    summary = db.one(
        """SELECT COUNT(*) AS papers, COALESCE(SUM(chunk_count),0) AS chunks,
                  COALESCE(SUM(page_count),0) AS pages, COALESCE(SUM(chunk_count*700),0) AS chars
           FROM papers"""
    )
    latency = db.one(
        "SELECT AVG(latency_ms) avg_ms FROM (SELECT latency_ms FROM search_log ORDER BY id DESC LIMIT 200) t"
    )
    by_topic = {}
    for row in db.rows("SELECT title, categories FROM papers"):
        label = topic_of(row["categories"], row["title"])
        by_topic[label] = by_topic.get(label, 0) + 1
    top_papers = db.rows(
        """SELECT id, title, page_count, chunk_count, published, authors, categories
           FROM papers ORDER BY chunk_count DESC LIMIT 6"""
    )
    recent = db.rows(
        """SELECT query, MAX(latency_ms) latency_ms, MAX(id) last_id
           FROM search_log GROUP BY query ORDER BY last_id DESC LIMIT 6"""
    )
    return {
        "papers": summary["papers"],
        "chunks": int(summary["chunks"]),
        "pages": int(summary["pages"]),
        "approx_words": int(summary["chars"]) // 6,
        "avg_latency_ms": round(float(latency["avg_ms"] or 0)) if latency else 0,
        "topics": [{"name": k, "count": v} for k, v in sorted(by_topic.items(), key=lambda x: -x[1])],
        "top_papers": [{**p, "topic": topic_of(p["categories"], p["title"])} for p in top_papers],
        "recent_queries": recent,
        "glossary_count": len(load_glossary()),
    }


@app.get("/api/papers")
def list_papers(q: str = ""):
    if q:
        pattern = f"%{q}%"
        paper_rows = db.rows(
            """SELECT id, arxiv_id, title, authors, published, categories, page_count,
                      chunk_count, pdf_url FROM papers
               WHERE title LIKE %s OR authors LIKE %s ORDER BY ingested_at DESC""",
            (pattern, pattern),
        )
    else:
        paper_rows = db.rows(
            """SELECT id, arxiv_id, title, authors, published, categories, page_count,
                      chunk_count, pdf_url FROM papers ORDER BY ingested_at DESC"""
        )
    for p in paper_rows:
        p["topic"] = topic_of(p["categories"], p["title"])
    return {"papers": paper_rows}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    paper = db.one("SELECT * FROM papers WHERE id=%s", (paper_id,))
    if not paper:
        raise HTTPException(404, "paper not found")
    paper["topic"] = topic_of(paper["categories"], paper["title"])
    outline = db.rows(
        """SELECT section, MIN(page_no) first_page, MAX(page_no) last_page, COUNT(*) chunks
           FROM chunks WHERE paper_id=%s GROUP BY section ORDER BY first_page""",
        (paper_id,),
    )
    notes = db.rows(
        "SELECT * FROM notes WHERE paper_id=%s ORDER BY id DESC", (paper_id,)
    )
    return {"paper": paper, "outline": outline, "notes": notes}


@app.get("/api/papers/{paper_id}/pages/{page_no}")
def get_page(paper_id: str, page_no: int):
    chunks = db.rows(
        """SELECT id, seq, section, content FROM chunks
           WHERE paper_id=%s AND page_no=%s ORDER BY seq""",
        (paper_id, page_no),
    )
    if not chunks:
        raise HTTPException(404, "page not found")
    return {"paper_id": paper_id, "page_no": page_no, "chunks": chunks}


@app.post("/api/search")
def search(body: SearchBody):
    return hybrid_search(body.query, top_k=body.top_k, paper_ids=body.paper_ids, mode=body.mode)


@app.post("/api/answer")
def answer(body: AnswerBody):
    return answer_question(body.query, top_k=body.top_k, paper_ids=body.paper_ids)


@app.post("/api/compare")
def compare(body: CompareBody):
    return compare_papers(body.query, body.paper_ids)


@app.post("/api/review")
def review(body: ReviewBody):
    markdown = review_markdown(body.topic, paper_ids=body.paper_ids)
    return {"markdown": markdown, "filename": f"review-{int(time.time())}.md"}


@app.post("/api/review/download", response_class=PlainTextResponse)
def review_download(body: ReviewBody):
    return PlainTextResponse(
        review_markdown(body.topic, paper_ids=body.paper_ids),
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/api/glossary")
def glossary():
    return {"glossary": load_glossary()}


@app.get("/api/notes")
def list_notes(paper_id: str = ""):
    if paper_id:
        return {"notes": db.rows("SELECT * FROM notes WHERE paper_id=%s ORDER BY id DESC", (paper_id,))}
    return {
        "notes": db.rows(
            """SELECT n.*, p.title FROM notes n LEFT JOIN papers p ON p.id=n.paper_id
               ORDER BY n.id DESC LIMIT 200"""
        )
    }


@app.post("/api/notes")
def add_note(body: NoteBody):
    note_id = db.run(
        "INSERT INTO notes (paper_id, page_no, quote, comment) VALUES (%s,%s,%s,%s)",
        (body.paper_id, body.page_no, body.quote[:2000], body.comment),
    )
    return {"id": note_id}


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: int):
    db.run("DELETE FROM notes WHERE id=%s", (note_id,))
    return {"ok": True}


def _start_job(fn, *args, **kwargs):
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "queued", "started": time.time(), "log": []}

    def runner():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            result = fn(*args, **kwargs)
            with JOBS_LOCK:
                JOBS[job_id].update(status="done", result=result, finished=time.time())
        except Exception as exc:
            with JOBS_LOCK:
                JOBS[job_id].update(status="error", error=str(exc), finished=time.time())

    threading.Thread(target=runner, daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/ingest")
def ingest(body: IngestBody):
    return _start_job(ingest_arxiv, body.arxiv, force=body.force)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (max 40MB)")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "only PDF files are supported")
    return _start_job(ingest_upload, file.filename or "upload.pdf", data)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return dict(job)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
