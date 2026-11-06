import hmac
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db, seed, service
from .config import API_TOKEN
from .embeddings import backend_name
from .service import AppError

OPEN_PATHS = {"/api/health"}


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
    print("api auth: X-Pawnshop-Token required", flush=True)
    yield


app = FastAPI(title="Memory Pawnshop", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in OPEN_PATHS:
        supplied = request.headers.get("x-pawnshop-token", "")
        if not API_TOKEN or not hmac.compare_digest(supplied, API_TOKEN):
            return JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "error": "unauthorized",
                    "message": "缺少或错误的 X-Pawnshop-Token。",
                },
            )
    return await call_next(request)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    # Business rejections (insufficient coins, empty stall, etc.) stay 200 so
    # Dify's HTTP node can hand the reason to the reply Code node.
    return JSONResponse(
        status_code=200,
        content={"ok": False, "error": exc.code, "message": exc.message, **exc.extra},
    )


class PlayerRequest(BaseModel):
    player_id: str


class WalletApplyRequest(BaseModel):
    player_id: str
    turn_id: str
    delta: int
    reason: str
    memory_id: int | None = None


class MemoryStoreRequest(BaseModel):
    player_id: str
    text: str
    title: str
    emotion: str
    appraisal: int


class MemorySearchRequest(BaseModel):
    player_id: str
    query: str = ""
    scope: str = "public"
    limit: int = Field(default=3, ge=1, le=5)


class MemoryRedeemRequest(BaseModel):
    player_id: str
    memory_id: int
    fee: int
    turn_id: str


class FragmentsRequest(BaseModel):
    player_id: str
    limit: int = Field(default=3, ge=1, le=5)


@app.get("/api/health")
def health():
    db.one("SELECT 1 AS ok")
    return {"ok": True, "embedding": backend_name(), "auth": "required"}


@app.get("/api/stats")
def stats():
    return {"ok": True, "embedding": backend_name(), **service.stats()}


@app.post("/api/wallet/get")
def wallet_get(body: PlayerRequest):
    return {"ok": True, **service.wallet_get(body.player_id)}


@app.post("/api/wallet/apply")
def wallet_apply(body: WalletApplyRequest):
    return {
        "ok": True,
        **service.wallet_apply(
            body.player_id,
            body.turn_id,
            body.delta,
            body.reason,
            body.memory_id,
        ),
    }


@app.post("/api/memory/store")
def memory_store(body: MemoryStoreRequest):
    return {
        "ok": True,
        **service.memory_store(
            body.player_id,
            body.text,
            body.title,
            body.emotion,
            body.appraisal,
        ),
    }


@app.post("/api/memory/search")
def memory_search(body: MemorySearchRequest):
    return {
        "ok": True,
        **service.memory_search(body.player_id, body.query, body.scope, body.limit),
    }


@app.post("/api/memory/redeem")
def memory_redeem(body: MemoryRedeemRequest):
    return {
        "ok": True,
        **service.memory_redeem(
            body.player_id,
            body.memory_id,
            body.fee,
            body.turn_id,
        ),
    }


@app.post("/api/memory/fragments")
def memory_fragments(body: FragmentsRequest):
    return {"ok": True, **service.dream_fragments(body.player_id, body.limit)}


@app.post("/api/admin/reset")
def admin_reset(body: PlayerRequest):
    return {"ok": True, **service.reset_player(body.player_id)}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("PAWNSHOP_HOST", "127.0.0.1"),
        port=int(os.getenv("PAWNSHOP_PORT", "8700")),
        log_level="info",
    )
