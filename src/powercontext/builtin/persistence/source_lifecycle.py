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

"""Search eligibility for Topic projections without changing historical bodies."""

from sqlalchemy import select

from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE as NODES


def topic_available(scope_id, artifact_id):
    return (
        ~select(NODES.c.node_id)
        .where(
            NODES.c.scope_id == scope_id,
            NODES.c.artifact_id == artifact_id,
            NODES.c.kind == "topic",
            NODES.c.available.is_(False),
        )
        .exists()
    )
