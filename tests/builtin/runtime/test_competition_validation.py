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

"""Regressions for strict reranking responses and optional Embedding usage fields."""

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryHit
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [True, False, "0.75"])
async def test_http_scores_require_json_numbers(score):
    hit = MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="m", revision=1),
        entry_id="entry",
        entry_version_id="version",
        text="evidence",
        score=1,
        matched_by=("fts",),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"scores": [{"rank": 1, "score": score}]}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError):
            await HTTPMemoryReranker("https://reranker.invalid/score", client=client).rerank("query", (hit,), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage", [InferenceUsage(requests=2, input_tokens=7), InferenceUsage(requests=2, output_tokens=9)]
)
async def test_coalescing_preserves_unknown_token_fields(usage):
    class Model:
        profile = EmbeddingProfile(profile_id="usage", model="offline", dimension=1)

        async def embed(self, texts, /):
            return EmbeddingResult(vectors=tuple((1.0,) for _ in texts), usage=usage)

    model = CoalescingEmbeddingModel(Model(), max_batch_size=1, coalesce_ms=0)
    result = await model.embed(("first", "second", "first"))
    assert result.usage.requests == usage.requests * 2
    assert result.usage.input_tokens == (None if usage.input_tokens is None else usage.input_tokens * 2)
    assert result.usage.output_tokens == (None if usage.output_tokens is None else usage.output_tokens * 2)
    assert (await model.embed(("first",))).usage == InferenceUsage(requests=0)


@pytest.mark.parametrize("prefix", [-1, True, False, 1.0, "1"])
def test_delta_application_rejects_invalid_in_memory_prefixes(prefix):
    base = ContextPack.build((b"a", b"b"))
    delta = ContextPackDelta(base_digest=base.digest, target_digest=base.digest, prefix_items=prefix, appended=(b"b",))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, delta)
