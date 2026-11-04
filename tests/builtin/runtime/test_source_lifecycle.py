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

"""Lifecycle events survive interrupted native writes and remain scope-local."""

import sqlite3

import pytest
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_interrupted_event_completes_on_replay_or_reopen(tmp_path, restart):
    database = tmp_path / "lifecycle.db"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
    async with open_builtin_runtime(config) as runtime:
        scope = (
            await runtime.scopes.create(
                ScopeDraft(title="Lifecycle retry", summary="Retry evidence", idempotency_key="retry")
            )
        ).scope_id
        app = runtime.lifecycle.for_scope(scope)
        receipt = await runtime.sources.for_scope(scope).capture(
            CaptureSource(source_id="root", content="Origin", metadata={})
        )
        await app.register_source("root", receipt.source_ref, revision=1)
        created = await runtime.memory.for_scope(scope).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="retryneedle supported fact"),))
        )
        citation = created.entry.citation
        await app.register_memory("fact", citation, parents=("root",), policy="all")
        event = SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="withdrawal")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TRIGGER interrupt_lifecycle BEFORE INSERT ON pc_artifacts "
                "WHEN NEW.family = 'memory' BEGIN SELECT RAISE(ABORT, 'lifecycle failure'); END"
            )
        with pytest.raises(IntegrityError):
            await app.apply(event)
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER interrupt_lifecycle")
        if not restart:
            assert await app.apply(event) == []
    async with open_builtin_runtime(config) as runtime:
        app = runtime.lifecycle.for_scope(scope)
        memory = runtime.memory.for_scope(scope)
        assert not (await memory.search(SearchMemoryRequest(query="retryneedle", mode="fts"))).hits
        before = (await memory.list(include_inactive=True)).memory_ref
        assert await app.apply(event) == []
        assert (await memory.list(include_inactive=True)).memory_ref == before
        assert (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text == "retryneedle supported fact"
        assert await app.resolve("fact", purpose="audit") == citation
        assert await app.apply(event.model_copy(update={"sequence": 2, "available": True})) == ["fact", "root"]
        assert (await memory.search(SearchMemoryRequest(query="retryneedle", mode="fts"))).hits


@pytest.mark.asyncio
async def test_equal_node_ids_have_independent_scope_state_and_missing_references_fail():
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
        services = []
        for title in ("left", "right"):
            scope = (
                await runtime.scopes.create(ScopeDraft(title=title, summary="Isolation", idempotency_key=title))
            ).scope_id
            app = runtime.lifecycle.for_scope(scope)
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="same", content=title, metadata={})
            )
            await app.register_source("root", source.source_ref, revision=1)
            await app.register_source("root", source.source_ref, revision=1)
            with pytest.raises(SourceLifecycleError):
                await app.register_source("absent", SourceRef(source_type="content", source_id="absent"), revision=1)
            services.append((app, source.source_ref))
        await services[0][0].apply(
            SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="scope withdrawal")
        )
        with pytest.raises(SourceLifecycleError):
            await services[0][0].resolve("root", purpose="generation")
        assert await services[1][0].resolve("root", purpose="generation") == services[1][1]
