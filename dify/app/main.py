import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import db, seed, service
from .service import AppError


@asynccontextmanager
async def lifespan(app):
    db.init_schema()
    count = seed.seed_if_empty()
    if count:
        print(f"seeded {count} public memories", flush=True)
    yield


app = FastAPI(title="Memory Pawnshop", version="1.0.0", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status,
        content={"ok": False, "error": exc.code, "message": exc.message, **exc.extra},
    )


@app.get("/api/health")
def health():
    db.one("SELECT 1 AS ok")
    return {"ok": True}


@app.get("/api/stats")
def stats():
    return {"ok": True, **service.stats()}


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
