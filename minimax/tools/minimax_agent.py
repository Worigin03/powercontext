"""Drive MiniMax to turn one context pack into a code change.

The model never receives the whole repository: it gets the rendered context
pack (specs, decisions, code symbols, tests) and must return complete file
contents. That keeps the exchange auditable and avoids fragile patch fuzzing.

Usage:
    python tools/minimax_agent.py --context records/context/TASK-20261104-01.md \
        --project demo_project --out records/minimax

Every request and response is appended to ``<out>/session.jsonl``; per round
transcripts go to ``<out>/round-NN.md``.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M2"

SYSTEM_PROMPT = """你是一名严格遵守规格的 Python 工程师。

你会收到一份“开发上下文包”，其中包含：适用规格、生效/失效的设计决策、需要修改的代码符号、目标测试与回归测试。

硬性要求：
1. 只实现上下文里“目标功能测试”要求的行为，行为细节以 status=active 的规格和决策为准；不得依据“已失效条目”。
2. 禁止修改测试文件（tests/ 下任何文件），禁止删除、跳过或弱化断言，禁止修改测试期望值。
3. 保持上下文中“回归测试”覆盖的既有行为不变。
4. 只输出 JSON，不要输出解释性散文。格式：
   {"files": [{"path": "<相对项目根的路径>", "content": "<完整文件内容>"}], "notes": "<一句话说明>"}
5. 只需要返回被修改的文件的完整内容；未修改的文件不要出现在 files 里。"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MiniMaxClient:
    def __init__(self, api_key: str, base_url: str, model: str, log_path: Path) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.log_path = log_path
        self.total_tokens = 0

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 8192,
        }
        started = time.time()
        last_error = ""
        for attempt in range(3):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=180.0,
                )
                if response.status_code == 200:
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    usage = body.get("usage", {})
                    self.total_tokens += int(usage.get("total_tokens", 0) or 0)
                    self._log(
                        {
                            "at": now(),
                            "model": self.model,
                            "latency_ms": int((time.time() - started) * 1000),
                            "usage": usage,
                            "request": payload,
                            "response": body,
                        }
                    )
                    return content
                last_error = f"HTTP {response.status_code}: {response.text[:400]}"
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
            time.sleep(2 + attempt * 3)
        raise RuntimeError(f"MiniMax call failed: {last_error}")

    def _log(self, entry: dict[str, Any]) -> None:
        entry["api_key"] = "***redacted***"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(candidate[start : end + 1])


def snapshot(project: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in project.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts:
            relative = path.relative_to(project).as_posix()
            if relative.startswith("tests/") or relative.endswith(".pyc"):
                continue
            files[relative] = path.read_text(encoding="utf-8")
    return files


def restore(project: Path, baseline: dict[str, str]) -> None:
    for relative, content in baseline.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def validate_files(files: list[dict[str, Any]], baseline: dict[str, str]) -> list[dict[str, str]]:
    accepted: list[dict[str, str]] = []
    for entry in files:
        relative = str(entry.get("path", "")).replace("\\", "/").lstrip("./")
        content = entry.get("content")
        if not relative or not isinstance(content, str):
            raise ValueError(f"bad file entry: {entry!r}")
        if relative.startswith("tests/") or "/tests/" in relative:
            raise ValueError(f"model tried to modify a test file: {relative}")
        if relative not in baseline:
            raise ValueError(f"model returned an unknown path: {relative}")
        if not relative.endswith(".py"):
            raise ValueError(f"only python sources may change: {relative}")
        accepted.append({"path": relative, "content": content})
    if not accepted:
        raise ValueError("model returned no files")
    return accepted


def apply_files(project: Path, files: list[dict[str, str]]) -> None:
    for entry in files:
        (project / entry["path"]).write_text(entry["content"], encoding="utf-8")


def run_pytest(project: Path, marker: str, python: str) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603 - fixed local command
        [python, "-m", "pytest", "-m", marker, "--tb=short", "-p", "no:cacheprovider"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def make_diff(project: Path, baseline: dict[str, str], changed: list[str]) -> str:
    chunks: list[str] = []
    for relative in sorted(changed):
        before = baseline[relative].splitlines(keepends=True)
        after = (project / relative).read_text(encoding="utf-8").splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                n=3,
            )
        )
    return "".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--project", default="demo_project")
    parser.add_argument("--out", default="records/minimax")
    parser.add_argument("--model", default=os.environ.get("MINIMAX_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--base-url", default=os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE_URL)
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--extra-instruction", default="")
    args = parser.parse_args()

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        print("error: MINIMAX_API_KEY is not set", file=sys.stderr)
        return 2

    project = Path(args.project).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    baseline = snapshot(project)
    (out / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    context = Path(args.context).read_text(encoding="utf-8")
    client = MiniMaxClient(api_key, args.base_url, args.model, log_path)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{context}\n\n---\n请实现目标功能，并按约定 JSON 返回需要修改的完整文件内容。"
                + (f"\n{args.extra_instruction}" if args.extra_instruction else "")
            ),
        },
    ]

    changed: list[str] = []
    success = False
    for round_index in range(1, args.rounds + 1):
        reply = client.chat(messages)
        (out / f"round-{round_index:02d}.md").write_text(
            f"# Round {round_index}\n\n## 模型回复\n\n{reply}\n", encoding="utf-8"
        )
        try:
            payload = extract_json(reply)
            files = validate_files(list(payload.get("files") or []), baseline)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"[round {round_index}] unusable reply: {exc}", file=sys.stderr)
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": f"输出无法应用：{exc}。请严格按要求只输出 JSON，路径使用相对项目根目录的路径。",
                }
            )
            continue

        restore(project, baseline)
        apply_files(project, files)
        changed = [entry["path"] for entry in files]
        target_code, target_out = run_pytest(project, "target", args.python)
        print(f"[round {round_index}] target exit={target_code}")
        (out / f"round-{round_index:02d}-target.log").write_text(target_out, encoding="utf-8")
        if target_code != 0:
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "目标测试未通过。下面是 pytest 输出（-m target）：\n\n"
                        f"{target_out[-4000:]}\n\n"
                        "请修正实现，重新返回完整的修改文件 JSON；不要修改 tests/ 下的文件。"
                    ),
                }
            )
            continue

        regression_code, regression_out = run_pytest(project, "regression", args.python)
        print(f"[round {round_index}] regression exit={regression_code}")
        (out / f"round-{round_index:02d}-regression.log").write_text(
            regression_out, encoding="utf-8"
        )
        if regression_code != 0:
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "回归测试未通过。下面是 pytest 输出（-m regression）：\n\n"
                        f"{regression_out[-4000:]}\n\n"
                        "请修正实现，保持既有行为不变，重新返回完整的修改文件 JSON。"
                    ),
                }
            )
            continue

        success = True
        break

    if success:
        diff = make_diff(project, baseline, changed)
        (out / "final.patch").write_text(diff, encoding="utf-8")
        (out / "final-files.json").write_text(
            json.dumps({"files": changed}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[ok] changed: {', '.join(changed)}")
        print(f"[ok] diff: {out / 'final.patch'}")
        print(f"[ok] total tokens: {client.total_tokens}")
        return 0

    restore(project, baseline)
    print("[fail] no round satisfied target + regression tests; project restored", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
