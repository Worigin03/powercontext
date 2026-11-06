#!/usr/bin/env python3
"""Replay the published Memory Pawnshop chatflow and record node-level evidence.

Requires a published Dify app and an app API key. Create one in Dify:
"访问 API" -> "API 密钥" -> "创建密钥". Nothing is stored by this script except
the SSE transcript and a summary under records/ (no key is written).

Example (PowerShell):
  $env:DIFY_API_KEY="app-..."
  python scripts/replay_e2e.py --reset --account e2e-2026 \
      --out records/webapp-e2e-2026.json

Example (bash):
  DIFY_API_KEY=app-... python scripts/replay_e2e.py --reset --account e2e-2026
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

TOKEN_HEADER = re.compile(r"(?i)(X-Pawnshop-Token:\s*)[^\r\n\\\"]+")


TURNS = [
    "我还有多少记忆币？",
    "赎回一段关于雨夜的记忆",
    "帮我把当掉的记忆缝成一个梦",
    "当掉：去年冬天奶奶在厨房煮姜汤，窗外下着雪，她一直看着窗外。",
    "当掉：凌晨三点我们在宿舍分一锅泡面，谁都不肯承认自己饿了，汤都喝完了。",
    "当掉：毕业典礼把帽子抛向天空，落下来时找不到自己的那顶，大家笑着说算了。",
    "赎回一段关于奶奶在厨房煮姜汤的记忆",
    "逛逛黑市，看看别人的秘密",
    "帮我把当掉的记忆缝成一个梦",
    "你好，掌柜",
    "我还有多少记忆币？",
]


def post_json(url, payload, headers, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    return urllib.request.urlopen(req, timeout=timeout)


def reset_player(backend, token, account):
    url = backend.rstrip("/") + "/api/admin/reset"
    with post_json(url, {"player_id": "acct:" + account}, {"X-Pawnshop-Token": token}) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stream_turn(base, key, user, account, query, conversation_id, raw_fh):
    payload = {
        "inputs": {"account": account},
        "query": query,
        "response_mode": "streaming",
        "conversation_id": conversation_id,
        "user": user,
    }
    headers = {"Authorization": "Bearer " + key, "Accept": "text/event-stream"}
    turn = {
        "query": query,
        "workflow_run_id": "",
        "conversation_id": conversation_id,
        "status": "",
        "answer": "",
        "nodes": [],
        "node_counts": {},
        "total_tokens": 0,
        "elapsed_time": 0,
    }
    node_events = []
    with post_json(base.rstrip("/") + "/v1/chat-messages", payload, headers) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace")
            raw_fh.write(TOKEN_HEADER.sub(r"\1<redacted>", line))
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            data = event.get("data") or {}
            if event.get("workflow_run_id"):
                turn["workflow_run_id"] = event["workflow_run_id"]
            if event.get("conversation_id"):
                turn["conversation_id"] = event["conversation_id"]
            if kind == "node_finished":
                meta = data.get("execution_metadata") or {}
                node = {
                    "node_id": data.get("node_id"),
                    "node_type": data.get("node_type"),
                    "title": data.get("title"),
                    "status": data.get("status"),
                    "elapsed_time": data.get("elapsed_time"),
                    "total_tokens": meta.get("total_tokens", 0),
                }
                if data.get("outputs") is not None:
                    node["outputs"] = data.get("outputs")
                node_events.append(node)
            elif kind == "message":
                turn["answer"] = event.get("answer") or turn["answer"]
            elif kind == "workflow_finished":
                turn["status"] = data.get("status") or ""
                turn["total_tokens"] = data.get("total_tokens") or 0
                turn["elapsed_time"] = data.get("elapsed_time") or 0
                turn["nodes"] = node_events
                for node in node_events:
                    key = node["node_id"]
                    turn["node_counts"][key] = turn["node_counts"].get(key, 0) + 1
            elif kind == "error":
                turn["status"] = "error"
                turn["answer"] = str(event.get("message") or event)[:2000]
    return turn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("DIFY_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--app-key", default=os.getenv("DIFY_API_KEY", ""))
    parser.add_argument("--user", default=os.getenv("DIFY_USER", "replay-e2e"))
    parser.add_argument("--account", default="e2e-2026")
    parser.add_argument("--backend", default=os.getenv("PAWNSHOP_BASE_URL", "http://127.0.0.1:8700"))
    parser.add_argument("--token", default=os.getenv("PAWNSHOP_API_TOKEN", "change-me-pawnshop-token"))
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--out", default="records/webapp-e2e.json")
    parser.add_argument("--raw", default="")
    args = parser.parse_args()

    if not args.app_key:
        parser.error("缺少 DIFY_API_KEY；请在 Dify 的「访问 API」页面创建应用密钥。")
    if not args.account:
        parser.error("account 不能为空，后端会拒绝 guest。")

    if args.reset:
        print("reset:", reset_player(args.backend, args.token, args.account))

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    raw_path = os.path.abspath(args.raw) if args.raw else out_path + ".sse"

    conversation_id = ""
    turns = []
    with open(raw_path, "w", encoding="utf-8") as raw_fh:
        for index, query in enumerate(TURNS, 1):
            turn = stream_turn(
                args.base, args.app_key, args.user, args.account, query, conversation_id, raw_fh
            )
            conversation_id = turn["conversation_id"] or conversation_id
            turns.append(turn)
            print(
                f"{index:02d} {turn['status']:9s} nodes={len(turn['nodes']):2d} "
                f"tokens={turn['total_tokens']:4d} run={turn['workflow_run_id']}"
            )
            print("   ", turn["answer"].replace("\n", " ")[:160])

    summary = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dify_base": args.base,
        "mode": "published-webapp-api",
        "account": "acct:" + args.account,
        "turns": turns,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("summary:", out_path)
    print("raw sse:", raw_path)
    return 0 if all(t["status"] == "succeeded" for t in turns) else 1


if __name__ == "__main__":
    sys.exit(main())
