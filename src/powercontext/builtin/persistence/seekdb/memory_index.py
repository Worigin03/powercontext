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

"""Single-statement native FTS and ANN candidate retrieval for embedded seekdb."""

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SQL = """
WITH fts_candidates AS (
    SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id, v.text,
           MATCH(m.searchable_text) AGAINST (:query) AS score
    FROM pc_memory_entry_heads AS m
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id AND m.memory_artifact_id IN :memory_artifact_ids
      AND MATCH(m.searchable_text) AGAINST (:query)
      {tag_filter}
    ORDER BY score DESC, m.memory_artifact_id, m.entry_id, m.entry_version_id
    LIMIT :candidate_limit
), vector_candidates AS (
    SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id, v.text,
           l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id AND m.memory_artifact_id IN :memory_artifact_ids
      {tag_filter}
    ORDER BY l2_distance(m.embedding, :query_vector) {approximate}
    LIMIT :candidate_limit
)
SELECT 'fts' AS channel, memory_artifact_id, head_revision, entry_id, entry_version_id, text,
       NULL AS distance, -score AS sort_key FROM fts_candidates
UNION ALL
SELECT 'vector' AS channel, memory_artifact_id, head_revision, entry_id, entry_version_id, text,
       distance, distance AS sort_key FROM vector_candidates
ORDER BY channel, sort_key, memory_artifact_id, entry_id, entry_version_id
"""


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Retain shared projection and single-channel behavior, combining hybrid candidates."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self._vector = OceanBaseMemoryVectorIndex(profile)
        super().__init__(OceanBaseMemoryFTSIndex(), self._vector)
        parameters = (
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )
        self._hybrid = text(_HYBRID_SQL.format(tag_filter="", approximate="APPROXIMATE")).bindparams(*parameters)
        self._tagged_hybrid = text(_HYBRID_SQL.format(tag_filter=memory_tag_sql("m"), approximate="")).bindparams(
            *parameters, bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True)
        )

    @override
    async def search(
        self, connection: AsyncConnection, scope_id: str, request: MemorySearchRequest, /
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        profile = self._vector.profile
        if request.embedding_profile != profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector.vector_complete(connection, scope_id, request.memories, profile):
            raise CapabilityNotSupportedError("vector")
        rows = (
            (
                await connection.execute(
                    self._hybrid if request.tag_filter is None else self._tagged_hybrid,
                    {
                        "scope_id": scope_id,
                        "memory_artifact_ids": tuple(ref.artifact_id for ref in request.memories),
                        "query": request.analyzed_query,
                        "query_vector": validate_embedding(request.query_vector, dimension=profile.dimension),
                        "candidate_limit": request.candidate_limit,
                        **memory_tag_parameters(request.tag_filter),
                    },
                )
            )
            .mappings()
            .all()
        )
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in rows if row["channel"] == "fts"), request.memories),
            vector=memory_channel_hits((row for row in rows if row["channel"] == "vector"), request.memories),
        )
