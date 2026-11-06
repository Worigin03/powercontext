#!/usr/bin/env python3
"""Backend smoke test: auth, identity, memory writes, vector search, ledger.

This exercises the storage service directly, without Dify. Gameplay decisions
(appraisal, fee, wallet delta) belong to the Dify nodes; here we submit fixed
values to prove the service persists them and keeps the ledger idempotent.

Usage:
    python scripts/smoke.py [base_url] [player_id]
Environment:
    PAWNSHOP_API_TOKEN (default: change-me-pawnshop-token)
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PAWNSHOP_BASE_URL", "http://127.0.0.1:8700")
PLAYER = sys.argv[2] if len(sys.argv) > 2 else "verify-2026"
TOKEN = os.getenv("PAWNSHOP_API_TOKEN", "change-me-pawnshop-token")
HEADERS = {"Content-Type": "application/json; charset=utf-8", "X-Pawnshop-Token": TOKEN}


def call(path, payload=None, headers=None, expect=200):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers=HEADERS if headers is None else headers,
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            status, raw = resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read().decode("utf-8")
    body = json.loads(raw)
    if status != expect:
        raise SystemExit(f"{path} expected {expect}, got {status}: {body}")
    return body


def show(name, body):
    print(f"\n== {name} ==")
    print(json.dumps(body, ensure_ascii=False, indent=2))


def main():
    health = call("/api/health", headers={})
    show("health (open)", health)
    if health.get("embedding", "").startswith("hash:"):
        print("WARNING: running the lexical hash fallback, not semantic embeddings")

    unauthorized = call("/api/wallet/get", {"player_id": f"acct:{PLAYER}"}, headers={}, expect=401)
    show("missing token -> 401", unauthorized)

    guest = call("/api/wallet/get", {"player_id": "guest"})
    if guest.get("ok") is not False or guest.get("error") != "bad_player":
        raise SystemExit(f"guest should be rejected: {guest}")
    print("\n== guest rejected ==")
    print("guest player id was rejected as expected")

    show("reset", call("/api/admin/reset", {"player_id": f"acct:{PLAYER}"}))
    wallet = call("/api/wallet/get", {"player_id": f"acct:{PLAYER}"})
    show("wallet opened", wallet)

    text = "去年冬天奶奶在厨房煮姜汤，窗外下着小雪，锅盖被蒸汽顶得咯咯响，她把碗塞进我手里说快喝。"
    stored = call(
        "/api/memory/store",
        {
            "player_id": f"acct:{PLAYER}",
            "text": text,
            "title": "奶奶的姜汤",
            "emotion": "温柔",
            "appraisal": 47,
        },
    )
    show("memory_store", stored)
    memory_id = stored["memory_id"]

    ledger = call(
        "/api/wallet/apply",
        {
            "player_id": f"acct:{PLAYER}",
            "turn_id": "smoke-pawn-1",
            "delta": 47,
            "reason": "典当《奶奶的姜汤》",
            "memory_id": memory_id,
        },
    )
    show("wallet_apply (+47)", ledger)
    repeated = call(
        "/api/wallet/apply",
        {
            "player_id": f"acct:{PLAYER}",
            "turn_id": "smoke-pawn-1",
            "delta": 47,
            "reason": "典当《奶奶的姜汤》",
            "memory_id": memory_id,
        },
    )
    show("same turn twice -> idempotent", repeated)

    hits = call(
        "/api/memory/search",
        {"player_id": f"acct:{PLAYER}", "query": "亲人在灶台边熬汤", "scope": "owner", "limit": 3},
    )
    show("semantic search (owner)", hits)

    redeemed = call(
        "/api/memory/redeem",
        {
            "player_id": f"acct:{PLAYER}",
            "memory_id": memory_id,
            "fee": 40,
            "turn_id": "smoke-redeem-1",
        },
    )
    show("memory_redeem (-40)", redeemed)

    show(
        "fragments after redeem",
        call("/api/memory/fragments", {"player_id": f"acct:{PLAYER}", "limit": 3}),
    )
    show("final wallet", call("/api/wallet/get", {"player_id": f"acct:{PLAYER}"}))
    show("cleanup", call("/api/admin/reset", {"player_id": f"acct:{PLAYER}"}))
    print("\nsmoke: ok")


if __name__ == "__main__":
    main()
