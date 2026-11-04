"""End-to-end smoke test for the memory pawnshop service (UTF-8 safe)."""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8700"
PLAYER = "smoke-player"


def call(action, **extra):
    payload = {"player_id": PLAYER, "action": action, **extra}
    req = urllib.request.Request(
        f"{BASE}/api/tool",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def show(name, status, body):
    flag = "OK " if status == 200 else "ERR"
    print(f"[{flag}] {name}: {json.dumps(body, ensure_ascii=False, indent=2)[:700]}")


status, body = call(
    "pawn",
    text="去年冬天奶奶在厨房煮姜汤，窗外下着小雪，锅盖被蒸汽顶得咯咯响，她把碗塞进我手里说快喝。",
)
show("pawn", status, body)

status, body = call("redeem", query="老人做的热汤，下雪的冬天")
show("redeem", status, body)

status, body = call("browse", query="下雨天丢钥匙", limit=3)
show("browse", status, body)

status, body = call("dream")
show("dream", status, body)

status, body = call("balance")
show("balance", status, body)
