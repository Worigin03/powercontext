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

"""Durable source dependency graphs and retryable native Memory state changes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE as NODES
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

Reference = SourceRef | MemoryCitation | ArtifactRef


class SourceLifecycleError(ValueError):
    """A dependency registration, event, or resolution violates the lifecycle contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "source-registration": "invalid Source registration",
            "memory-citation": "invalid Memory citation",
            "unknown-reference": "registered reference must exist and be readable in this scope",
            "topic-reference": "invalid Topic reference",
            "node-identity": "invalid node identity",
            "policy": "derived nodes require parents and an all/any policy",
            "parents": "invalid parents",
            "cycle": "duplicate parents or cycle",
            "missing-parent": "parents must be registered first",
            "conflict": "conflicting node binding",
            "event-source": "unknown Source or lifecycle revision",
            "event-sequence": "stale or conflicting event sequence",
            "stored-cycle": "cyclic stored dependency graph",
            "body-version": "registered Memory body version changed",
            "purpose": "unknown resolution purpose",
            "unavailable": "node is unknown or unavailable for generation",
        }
        super().__init__(messages[code])


class SourceLifecycleEvent(BaseModel):
    """One immutable, strictly typed Source availability event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain non-whitespace text")  # noqa: TRY003
        return value


class _Node(BaseModel):
    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: str
    artifact_id: str | None
    parents: tuple[str, ...]
    policy: Literal["all", "any"]
    revision: int
    sequence: int = 0
    last_event: str | None = None
    available: bool = True
    reconciled: bool = False

    def ref(self) -> Reference:
        if self.kind == "source":
            return SourceRef.model_validate_json(self.reference)
        if self.kind == "memory":
            return MemoryCitation.model_validate_json(self.reference)
        return ArtifactRef.model_validate_json(self.reference)


class SourceLifecycleApplication:
    """Select a durable dependency graph in one scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase) -> None:
        self.runtime = runtime
        self.database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self, validate_scope_id(scope_id))

    async def recover(self) -> None:
        """Complete native state writes interrupted after durable event acceptance."""
        async with self.database.transaction() as connection:
            scopes = tuple(
                (
                    await connection.scalars(select(NODES.c.scope_id).where(NODES.c.reconciled.is_(False)).distinct())
                ).all()
            )
        for scope in scopes:
            service = self.for_scope(scope)
            async with self.runtime._scope_operation(scope), self.runtime._locked(scope):
                await service._reconcile(await service._nodes(), reason="lifecycle recovery")


class ScopedSourceLifecycle:
    def __init__(self, app: SourceLifecycleApplication, scope_id: str) -> None:
        self._app = app
        self.scope_id = scope_id

    async def _nodes(self) -> dict[str, _Node]:
        async with self._app.database.transaction() as connection:
            rows = (await connection.execute(select(NODES).where(NODES.c.scope_id == self.scope_id))).mappings()
            return {
                row["node_id"]: _Node.model_validate({
                    **{key: row[key] for key in _Node.model_fields},
                    "parents": json.loads(row["parents"]),
                })
                for row in rows
            }

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if type(revision) is not int or revision < 1 or not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source-registration")
        try:
            await self._app.runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type, source_ref.source_id
            )
        except (LookupError, BaseValueNotFoundError) as error:
            raise SourceLifecycleError("unknown-reference") from error
        await self._register(node_id, "source", source_ref, (), "all", revision)

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(citation, MemoryCitation) or citation.memory_ref.family != "memory":
            raise SourceLifecycleError("memory-citation")
        try:
            await self._app.runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except (LookupError, ValueError) as error:
            raise SourceLifecycleError("unknown-reference") from error
        await self._register(node_id, "memory", citation, parents, policy, 0)

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: str
    ) -> None:
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("topic-reference")
        try:
            await self._app.runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except (LookupError, BaseValueNotFoundError) as error:
            raise SourceLifecycleError("unknown-reference") from error
        await self._register(node_id, "topic", artifact_ref, parents, policy, 0)

    async def _register(
        self,
        node_id: str,
        kind: Literal["source", "memory", "topic"],
        reference: Reference,
        parents: tuple[str, ...],
        policy: str,
        revision: int,
    ) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
            raise SourceLifecycleError("node-identity")
        if policy not in {"all", "any"} or (kind != "source" and not parents):
            raise SourceLifecycleError("policy")
        if isinstance(parents, str) or any(not isinstance(parent, str) for parent in parents):
            raise SourceLifecycleError("parents")
        if len(set(parents)) != len(parents) or node_id in parents:
            raise SourceLifecycleError("cycle")
        artifact_id = None
        if isinstance(reference, MemoryCitation):
            artifact_id = reference.memory_ref.artifact_id
        elif isinstance(reference, ArtifactRef):
            artifact_id = reference.artifact_id
        node = _Node(
            node_id=node_id,
            kind=kind,
            reference=reference.model_dump_json(),
            artifact_id=artifact_id,
            parents=tuple(sorted(parents)),
            policy=policy,
            revision=revision,
        )
        async with self._app.runtime._scope_operation(self.scope_id), self._app.runtime._locked(self.scope_id):
            nodes = await self._nodes()
            if any(parent not in nodes for parent in node.parents):
                raise SourceLifecycleError("missing-parent")
            existing = nodes.get(node_id)
            if existing is not None:
                fields = ("kind", "reference", "parents", "policy", "revision")
                if any(getattr(existing, field) != getattr(node, field) for field in fields):
                    raise SourceLifecycleError("conflict")
            else:
                node.available = kind == "source" or self._supported(node, nodes)
                async with self._app.database.transaction() as connection:
                    await connection.execute(
                        insert(NODES).values(
                            scope_id=self.scope_id,
                            **node.model_dump(exclude={"parents"}),
                            parents=json.dumps(node.parents),
                        )
                    )
                nodes[node_id] = node
            await self._reconcile(nodes, reason="lifecycle registration")

    @staticmethod
    def _supported(node: _Node, nodes: dict[str, _Node]) -> bool:
        support = (nodes[parent].available for parent in node.parents)
        return all(support) if node.policy == "all" else any(support)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        event = SourceLifecycleEvent.model_validate(event)
        async with self._app.runtime._scope_operation(self.scope_id), self._app.runtime._locked(self.scope_id):
            nodes = await self._nodes()
            root = nodes.get(event.node_id)
            if root is None or root.kind != "source" or root.revision != event.revision:
                raise SourceLifecycleError("event-source")
            payload = event.model_dump_json()
            if event.sequence < root.sequence or (event.sequence == root.sequence and payload != root.last_event):
                raise SourceLifecycleError("event-sequence")
            if event.sequence > root.sequence:
                root.sequence, root.last_event, root.available = event.sequence, payload, event.available
                # Registration requires all parents first. Traverse by dependencies,
                # never by lexical node order or database row order.
                resolved = {key for key, node in nodes.items() if node.kind == "source"}
                while len(resolved) < len(nodes):
                    ready = [
                        node for key, node in nodes.items() if key not in resolved and set(node.parents) <= resolved
                    ]
                    if not ready:
                        raise SourceLifecycleError("stored-cycle")
                    for node in ready:
                        node.available = self._supported(node, nodes)
                        resolved.add(node.node_id)
                # Accept desired state before native writes. A failed write leaves
                # a durable reconciliation marker; replay or restart completes it.
                async with self._app.database.transaction() as connection:
                    for node in nodes.values():
                        await connection.execute(
                            update(NODES)
                            .where(NODES.c.scope_id == self.scope_id, NODES.c.node_id == node.node_id)
                            .values(
                                available=node.available,
                                sequence=node.sequence,
                                last_event=node.last_event,
                                reconciled=False,
                            )
                        )
            await self._reconcile(nodes, reason=event.reason)
            return sorted(key for key, node in nodes.items() if node.available)

    async def _reconcile(self, nodes: dict[str, _Node], *, reason: str) -> None:
        memories: dict[str, list[_Node]] = {}
        for node in nodes.values():
            if node.kind == "memory":
                citation = MemoryCitation.model_validate_json(node.reference)
                memories.setdefault(citation.memory_ref.artifact_id, []).append(node)
        if memories:
            async with self._app.runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                for artifact_id, group in memories.items():
                    current = await service.head(artifact_id)
                    entries = {entry.entry_id: entry for entry in await service.entries(current)}
                    states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
                    for available in (False, True):
                        changes = []
                        for node in group:
                            citation = MemoryCitation.model_validate_json(node.reference)
                            entry = entries.get(citation.entry_id)
                            if entry is None or entry.entry_version_id != citation.entry_version_id:
                                raise SourceLifecycleError("body-version")
                            if node.available == available and (states[entry.entry_id] == "active") != available:
                                changes.append(entry)
                        if changes:
                            operation = service.reactivate if available else service.forget
                            current = await operation(current, entries=tuple(changes), reason=reason)
        async with self._app.database.transaction() as connection:
            await connection.execute(update(NODES).where(NODES.c.scope_id == self.scope_id).values(reconciled=True))

    async def resolve(self, node_id: str, *, purpose: str) -> Reference:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("purpose")
        nodes = await self._nodes()
        node = nodes.get(node_id)
        if node is None or (purpose == "generation" and not node.available):
            raise SourceLifecycleError("unavailable")
        return node.ref()


__all__ = ["SourceLifecycleError", "SourceLifecycleEvent"]
