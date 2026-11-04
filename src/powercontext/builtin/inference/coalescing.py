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

"""Coalesce concurrent Embedding work behind one bounded vector cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

DEFAULT_MAX_BATCH_SIZE = 64
DEFAULT_COALESCE_MS = 5.0
DEFAULT_CACHE_SIZE = 1024


@dataclass(slots=True)
class _Batch:
    """One underlying model request shared by every waiter of its texts."""

    texts: tuple[str, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


@dataclass(slots=True)
class _Slot:
    """One text's outcome shared across every call that still needs it."""

    future: asyncio.Future[tuple[float, ...]]
    batch: _Batch | None = None


class CoalescingEmbeddingModel:
    """Share in-flight Embedding work and cache successful vectors by text."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = DEFAULT_COALESCE_MS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(coalesce_ms, (int, float)) or isinstance(coalesce_ms, bool) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1_000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._slots: dict[str, _Slot] = {}
        self._queued: deque[str] = deque()
        self._flusher: asyncio.Task[None] | None = None
        self.profile = model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input position, reusing cache and shared work."""

        inputs = tuple(texts)
        if not inputs:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        resolved: dict[str, tuple[float, ...]] = {}
        slots: dict[str, _Slot] = {}
        queued = False
        for text in inputs:
            if text in resolved or text in slots:
                continue
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                resolved[text] = cached
                continue
            slot = self._slots.get(text)
            if slot is None:
                slot = _Slot(future=asyncio.get_running_loop().create_future())
                slot.future.add_done_callback(_consume_outcome)
                self._slots[text] = slot
                self._queued.append(text)
                queued = True
            slots[text] = slot
        if queued:
            self._ensure_flusher()

        if slots:
            # Wait on the shared futures instead of the call's own task, so one
            # cancelled caller never cancels work other callers still need.
            await asyncio.wait([slot.future for slot in slots.values()])
        vectors = tuple(resolved[text] if text in resolved else slots[text].future.result() for text in inputs)
        batches = {id(slot.batch): slot.batch for slot in slots.values() if slot.batch is not None}
        return EmbeddingResult(vectors=vectors, usage=_sum_usage(batches.values()))

    def _ensure_flusher(self) -> None:
        if self._flusher is None or self._flusher.done():
            self._flusher = asyncio.get_running_loop().create_task(self._flush())

    async def _flush(self) -> None:
        # One yield gives same-tick callers a chance to join the pending batch.
        await asyncio.sleep(self._coalesce_seconds)
        while True:
            batch = self._take_batch()
            if batch is None:
                return
            await self._run_batch(batch)

    def _take_batch(self) -> _Batch | None:
        texts: list[str] = []
        while self._queued and len(texts) < self._max_batch_size:
            text = self._queued.popleft()
            if self._slots.get(text) is None:
                continue
            texts.append(text)
        if not texts:
            return None
        batch = _Batch(texts=tuple(texts))
        for text in texts:
            self._slots[text].batch = batch
        return batch

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._model.embed(batch.texts)
        except Exception as error:
            self._settle(batch, error=error)
            return
        vectors = tuple(result.vectors)
        if len(vectors) != len(batch.texts):
            self._settle(batch, error=ValueError("embedding vector count does not match the batch texts"))
            return
        batch.usage = result.usage
        self._settle(batch, vectors=vectors)

    def _settle(
        self,
        batch: _Batch,
        *,
        vectors: tuple[tuple[float, ...], ...] | None = None,
        error: BaseException | None = None,
    ) -> None:
        for index, text in enumerate(batch.texts):
            slot = self._slots.pop(text, None)
            if slot is None:  # pragma: no cover - a text is settled once per batch
                continue
            if error is not None:
                slot.future.set_exception(error)
                continue
            vector = vectors[index]
            self._remember(text, vector)
            slot.future.set_result(vector)

    def _remember(self, text: str, vector: tuple[float, ...]) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _consume_outcome(future: asyncio.Future[tuple[float, ...]]) -> None:
    """Read the outcome so a cancelled waiter cannot leak an unretrieved error."""

    if not future.cancelled():
        future.exception()


def _sum_usage(batches) -> InferenceUsage:
    requests = input_tokens = output_tokens = 0
    counted_tokens = False
    for batch in batches:
        usage = batch.usage
        requests += usage.requests
        if usage.input_tokens is not None:
            input_tokens += usage.input_tokens
            counted_tokens = True
        if usage.output_tokens is not None:
            output_tokens += usage.output_tokens
            counted_tokens = True
    return InferenceUsage(
        requests=requests,
        input_tokens=input_tokens if counted_tokens else None,
        output_tokens=output_tokens if counted_tokens else None,
    )


__all__ = ["DEFAULT_CACHE_SIZE", "DEFAULT_COALESCE_MS", "DEFAULT_MAX_BATCH_SIZE", "CoalescingEmbeddingModel"]
