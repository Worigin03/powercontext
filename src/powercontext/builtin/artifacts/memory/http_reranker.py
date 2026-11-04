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

"""Cross-encoder reranking through one HTTP scoring service."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_BATCH_SIZE = 32
DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class _ScoreRecord(BaseModel):
    """One rank and its service score, with no tolerated extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(strict=True, ge=1)
    score: float = Field(allow_inf_nan=False)


class _ScoreResponse(BaseModel):
    """The complete score payload of one batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[_ScoreRecord, ...]


class HTTPMemoryReranker:
    """Score coarse candidates with one HTTP service and select across batches."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = DEFAULT_BATCH_SIZE,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not endpoint:
            raise ValueError("rerank endpoint must be non-empty")  # noqa: TRY003
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        if not policy_id:
            raise ValueError("policy_id must be non-empty")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Request every candidate exactly once and select from the whole pool."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            expected = tuple(range(start + 1, start + 1 + len(batch)))
            scores.update(await self._score_batch(query, batch, expected))
            requests += 1
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )

    async def _score_batch(
        self,
        query: str,
        batch: Sequence[MemoryHit],
        expected_ranks: tuple[int, ...],
    ) -> dict[int, float]:
        payload = {
            "query": query,
            "candidates": [
                {"rank": rank, "text": candidate.text}
                for rank, candidate in zip(expected_ranks, batch, strict=True)
            ],
        }
        response = await self._client.post(self._endpoint, json=payload)
        response.raise_for_status()
        try:
            parsed = _ScoreResponse.model_validate(response.json())
        except ValueError as error:
            raise ValueError("rerank service returned an invalid payload") from error  # noqa: TRY003
        return _covered_scores(parsed.scores, expected_ranks)


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be within the candidate count")  # noqa: TRY003


def _covered_scores(records: tuple[_ScoreRecord, ...], expected_ranks: tuple[int, ...]) -> dict[int, float]:
    """Require this batch's ranks exactly once each, in any order."""

    scores: dict[int, float] = {}
    for record in records:
        if record.rank not in expected_ranks:
            raise ValueError(f"rerank service scored rank {record.rank} outside its batch")  # noqa: TRY003
        if record.rank in scores:
            raise ValueError(f"rerank service scored rank {record.rank} more than once")  # noqa: TRY003
        scores[record.rank] = record.score
    missing = [rank for rank in expected_ranks if rank not in scores]
    if missing:
        raise ValueError(f"rerank service did not score ranks {missing}")  # noqa: TRY003
    return scores


__all__ = ["DEFAULT_BATCH_SIZE", "DEFAULT_HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
