# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pi session branch history as immutable per-event evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_SOURCE_VERSION = "1"
PI_SESSION_FORMAT_VERSION = 3
PI_SESSION_CHECKPOINT_VERSION = 1

_SESSION_HEADER_TYPE = "session"
_MESSAGE_TYPE = "message"
_CUSTOM_MESSAGE_TYPE = "custom_message"
_SUMMARY_EVENT_TYPES = frozenset({"compaction", "branch_summary"})


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL archive plus the branch leaf to capture."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, with its complete input record."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured original event evidence for one Pi session branch node."""

    source_id: Annotated[str, Field(max_length=MAX_SOURCE_ID_LENGTH)]
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str, /) -> str:
    """Build the session-scoped, delimiter-safe identity of one event."""

    for field_name, value in (("session_id", session_id), ("event_id", event_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-blank string")  # noqa: TRY003
    readable = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    # Percent-encoded components never contain a literal colon, so the hashed form
    # (one separator) cannot collide with the readable form (two separators).
    digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
    return f"pi:{digest}"


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return every visible event on the ancestor path of the selected leaf."""

    records = _read_jsonl(value.content)
    if not records:
        raise ValueError("pi session must start with a session header record")  # noqa: TRY003
    session_id = _session_id(records[0])
    nodes, parents, order = _index_records(records[1:])
    branch = _branch(order, parents, value.leaf_id)
    events = (_visible_event(session_id, event_id, parents[event_id], nodes[event_id]) for event_id in branch)
    return tuple(event for event in events if event is not None)


def _index_records(
    records: list[Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None], list[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    parents: dict[str, str | None] = {}
    order: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("pi session records must be JSON objects")  # noqa: TRY003, TRY004
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("pi session records must carry a non-blank string id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError(f"duplicate pi session record id {event_id!r}")  # noqa: TRY003
        parent_id = _parent_id(record)
        if parent_id is not None and parent_id not in nodes:
            raise ValueError(  # noqa: TRY003
                f"pi session record {event_id!r} references an unknown parent {parent_id!r}"
            )
        nodes[event_id] = record
        parents[event_id] = parent_id
        order.append(event_id)
    return nodes, parents, order


def _branch(order: list[str], parents: dict[str, str | None], leaf_id: str | None) -> list[str]:
    selected = leaf_id if leaf_id is not None else (order[-1] if order else None)
    if selected is None:
        return []
    if not isinstance(selected, str) or selected not in parents:
        raise ValueError(f"pi session leaf {selected!r} does not exist")  # noqa: TRY003

    branch: list[str] = []
    visited: set[str] = set()
    current: str | None = selected
    while current is not None:
        if current in visited:  # pragma: no cover - parents always precede their children
            raise ValueError("pi session branch contains a cycle")  # noqa: TRY003
        visited.add(current)
        branch.append(current)
        current = parents[current]
    branch.reverse()
    return branch


def _read_jsonl(content: bytes) -> list[Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("pi session content must be strict UTF-8") from error  # noqa: TRY003
    records: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError as error:
            raise ValueError(f"pi session line is not valid JSON: {error}") from error  # noqa: TRY003
    return records


def _session_id(header: Any) -> str:
    if not isinstance(header, dict) or header.get("type") != _SESSION_HEADER_TYPE:
        raise ValueError("pi session must start with a session header record")  # noqa: TRY003
    version = header.get("version")
    if isinstance(version, bool) or version != PI_SESSION_FORMAT_VERSION:
        raise ValueError(f"pi session version must be {PI_SESSION_FORMAT_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("pi session header must carry a non-blank string id")  # noqa: TRY003
    return session_id


def _parent_id(record: dict[str, Any]) -> str | None:
    parent_id = record.get("parentId")
    if parent_id is None or parent_id == "":
        return None
    if not isinstance(parent_id, str):
        raise ValueError("pi session parentId must be a string or null")  # noqa: TRY003, TRY004
    return parent_id


def _visible_event(
    session_id: str,
    event_id: str,
    parent_id: str | None,
    record: dict[str, Any],
) -> PiEventInput | None:
    event_type = record.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None
    rendered = _render(event_type, record)
    if rendered is None:
        return None
    role, body = rendered
    if not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _render(event_type: str, record: dict[str, Any]) -> tuple[str, str] | None:
    if event_type == _MESSAGE_TYPE:
        return _render_message(record.get("message"))
    if event_type in _SUMMARY_EVENT_TYPES:
        summary = record.get("summary")
        return (event_type, summary) if isinstance(summary, str) else None
    if event_type == _CUSTOM_MESSAGE_TYPE:
        return "custom", _block_text(record.get("content"))
    # Every other node keeps its place in the branch graph but carries no text evidence.
    return None


def _render_message(message: Any) -> tuple[str, str] | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return None
    if role == "toolResult":
        tool_name = _text_field(message, "toolName")
        tool_call_id = _text_field(message, "toolCallId")
        return role, f"[tool_result {tool_name} {tool_call_id}] {_block_text(message.get('content'))}"
    if role == "bashExecution":
        return role, f"$ {_text_field(message, 'command')}\n{_text_field(message, 'output')}"
    return role, _block_text(message.get("content"))


def _text_field(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    return value if isinstance(value, str) else ""


def _block_text(content: Any) -> str:
    """Render visible text and tool calls; thinking and images never enter evidence."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            name = _text_field(block, "name")
            call_id = _text_field(block, "id")
            parts.append(f"[tool_call {name} {call_id}] {_compact_arguments(block.get('arguments'))}")
    return "\n".join(parts)


def _compact_arguments(arguments: Any) -> str:
    value = arguments
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _PiEventSourceAdapter:
    """Resolve visible events into captured Sources and read them back exactly."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class _PiEventTextEvidenceProjection:
    """Expose the complete event text while keeping its graph identity."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = _PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_SESSION_SOURCE_VERSION,
    projections=(_PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import newly visible branch events while keeping durable progress."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        # The whole archive is parsed and cross-checked before any event is submitted.
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        imported = _load_checkpoint(session.checkpoint)
        pending = _pending_events(events, imported)
        for identity, event, digest in pending:
            await session.submit(identity, PI_EVENT_SOURCE_NAME, event)
            imported[identity] = digest
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": PI_SESSION_CHECKPOINT_VERSION, "events": imported},
        )


def _pending_events(
    events: tuple[PiEventInput, ...],
    imported: dict[str, str],
) -> list[tuple[str, PiEventInput, str]]:
    """Reject rewritten history and return only events this binding has not imported."""

    pending: list[tuple[str, PiEventInput, str]] = []
    for event in events:
        identity = pi_event_source_id(event.session_id, event.event_id)
        digest = _event_digest(event)
        known = imported.get(identity)
        if known is None:
            pending.append((identity, event, digest))
        elif known != digest:
            raise ValueError(f"pi event {event.event_id!r} was rewritten after it was imported")  # noqa: TRY003
    return pending


def _load_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("pi session checkpoint must be a JSON object")  # noqa: TRY003, TRY004
    if checkpoint.get("version") != PI_SESSION_CHECKPOINT_VERSION:
        raise ValueError("pi session checkpoint version is not supported")  # noqa: TRY003
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("pi session checkpoint must record imported events")  # noqa: TRY003, TRY004
    imported: dict[str, str] = {}
    for identity, digest in events.items():
        if not isinstance(identity, str) or not isinstance(digest, str):
            raise ValueError("pi session checkpoint entries must be strings")  # noqa: TRY003, TRY004
        imported[identity] = digest
    return imported


def _event_digest(event: PiEventInput) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
