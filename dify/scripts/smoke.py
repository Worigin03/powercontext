"""End-to-end smoke test for the memory pawnshop service (UTF-8 safe)."""

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
PLAYER = sys.argv[2] if len(sys.argv) > 2 else "verify-2026"


def call(action, **extra):
    payload = {"player_id": PLAYER, "action": action, **extra}
    req = urllib.request.Request(
        f"{BASE}/api/tool",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def show(name, status, body):
    flag = "OK " if body.get("ok") else "ERR"
    print(f"\n[{flag}] {name} http={status}")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    if not body.get("ok"):
        raise SystemExit(f"{name} failed: {body}")


health = json.loads(urllib.request.urlopen(f"{BASE}/api/health", timeout=30).read())
print("health:", json.dumps(health, ensure_ascii=False))

status, body = call(
    "pawn",
    text="去年冬天奶奶在厨房煮姜汤，窗外下着小雪，锅盖被蒸汽顶得咯咯响，她把碗塞进我手里说快喝。",
)
show("pawn 1", status, body)

status, body = call(
    "pawn",
    text="凌晨三点的便利店，我买了两份关东煮，等一个不会来的人。",
)
show("pawn 2", status, body)

status, body = call("redeem", query="便利店 关东煮 不会来的人")
show("redeem", status, body)

status, body = call("browse", query="下雨天 跑回家", limit=3)
show("browse", status, body)

status, body = call("dream")
show("dream", status, body)

status, body = call("balance")
show("balance", status, body)

print("\nsmoke: all actions ok")
