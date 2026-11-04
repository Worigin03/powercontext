"""Task state persistence on top of PowerContext Memory.

One Memory entry per saved revision, kind ``sdd-task-state``, text prefixed with
``[task:<id>]`` so a later run can pick the newest revision for that task.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sdd_context.models import TaskState
from sdd_context.powercontext import PowerContextClient

STATE_KIND = "sdd-task-state"
STATE_SCHEMA = "sdd-task-state/1"
MARKER = "[task:{task_id}]"


def load_state(client: PowerContextClient, task_id: str) -> TaskState:
    marker = MARKER.format(task_id=task_id)
    best: TaskState | None = None
    for entry in client.list_memories():
        if entry.get("kind") != STATE_KIND:
            continue
        text = entry.get("text") or ""
        if marker not in text:
            continue
        payload_text = text.split(marker, 1)[1].strip()
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        state = TaskState(
            task_id=task_id,
            updated_at=str(payload.get("updated_at") or ""),
            done=list(payload.get("done") or []),
            todo=list(payload.get("todo") or []),
            verified=list(payload.get("verified") or []),
            changed_files=list(payload.get("changed_files") or []),
            notes=str(payload.get("notes") or ""),
            source=f"powercontext:memory/{_artifact_id(entry)}",
        )
        if best is None or state.updated_at >= best.updated_at:
            best = state
    return best or TaskState(task_id=task_id, source="powercontext:empty")


def save_state(client: PowerContextClient, state: TaskState, reason: str = "") -> str:
    state.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "updated_at": state.updated_at,
        "done": state.done,
        "todo": state.todo,
        "verified": state.verified,
        "changed_files": state.changed_files,
        "notes": state.notes,
    }
    text = MARKER.format(task_id=state.task_id) + " " + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    response = client.remember(STATE_KIND, text, reason=reason or f"sdd context state for {state.task_id}")
    state.source = "powercontext:memory"
    records = response.get("records") or []
    if records:
        return str((records[0].get("artifact") or {}).get("artifact_id", ""))
    return ""


def _artifact_id(entry: dict[str, Any]) -> str:
    artifact = entry.get("artifact") or {}
    return str(artifact.get("artifact_id") or "entry")
