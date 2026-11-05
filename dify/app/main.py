import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import db, seed, service
from .embeddings import backend_name
from .service import AppError


@asynccontextmanager
async def lifespan(app):
    db.init_schema()
    reembedded = seed.ensure_embedding_backend()
    if reembedded:
        print(f"re-embedded {reembedded} memories with {backend_name()}", flush=True)
    count = seed.seed_if_empty()
    if count:
        print(f"seeded {count} public memories", flush=True)
    print(f"embedding backend: {backend_name()}", flush=True)
    yield


app = FastAPI(title="Memory Pawnshop", version="1.0.0", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    # /api/tool backs a Dify HTTP node: keep business errors as 200 so the
    # workflow can still let the keeper explain what went wrong.
    status = 200 if request.url.path == "/api/tool" else exc.status
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": exc.code, "message": exc.message, **exc.extra},
    )


@app.get("/api/health")
def health():
    db.one("SELECT 1 AS ok")
    return {"ok": True, "embedding": backend_name()}


@app.get("/api/stats")
def stats():
    return {"ok": True, "embedding": backend_name(), **service.stats()}


@app.get("/api/wallet/{player_id}")
def wallet(player_id: str):
    return {"ok": True, **service.balance(player_id)}


@app.post("/api/tool")
async def tool(request: Request):
    payload = await request.json()
    try:
        data = service.handle(payload)
    except AppError:
        raise
    return {"ok": True, "action": payload.get("action", "balance"), **data}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("PAWNSHOP_HOST", "127.0.0.1"),
        port=int(os.getenv("PAWNSHOP_PORT", "8700")),
        log_level="info",
    )
