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

"""Row-level CSV evidence: one captured Source per primary-key identified record."""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


def _require_document_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_id must be a non-blank string")  # noqa: TRY003
    if value != value.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def _require_primary_key(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    return value


def _require_primary_key_value(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("primary_key_value must be a non-blank string")  # noqa: TRY003
    return value


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document addressed by document id and primary key."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_primary_key(value)


class CSVRowInput(BaseModel):
    """One CSV record as the exact strings written in the source document."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_primary_key(value)


class CSVRowSource(Source):
    """Captured row evidence identified by document id and primary-key value."""

    source_id: Annotated[str, Field(max_length=MAX_SOURCE_ID_LENGTH)]
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Build the delimiter-safe row identity shared by every consumer."""

    identity = (
        f"csv:{quote(_require_document_id(document_id), safe='')}:"
        f"{quote(_require_primary_key_value(primary_key_value), safe='')}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:
    """Parse and fully validate one document before returning any row."""

    document_id = _require_document_id(value.document_id)
    primary_key = _require_primary_key(value.primary_key)
    records = _read_records(value.content)
    if not records:
        raise ValueError("csv document must contain a header record")  # noqa: TRY003

    columns = tuple(records[0])
    _validate_header(columns, primary_key)
    key_index = columns.index(primary_key)

    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        primary_key_value = _validate_record(record, columns, key_index, seen_keys)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _validate_header(columns: tuple[str, ...], primary_key: str) -> None:
    if not columns:
        raise ValueError("csv document must contain a non-empty header record")  # noqa: TRY003
    for column in columns:
        if not column.strip():
            raise ValueError("csv header columns must be non-empty and not blank")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("csv header columns must be unique")  # noqa: TRY003
    if primary_key not in columns:
        raise ValueError(f"primary_key {primary_key!r} is not an exact header column")  # noqa: TRY003


def _validate_record(record: list[str], columns: tuple[str, ...], key_index: int, seen_keys: set[str]) -> str:
    if len(record) != len(columns):
        raise ValueError(f"csv record has {len(record)} fields, expected {len(columns)}")  # noqa: TRY003
    primary_key_value = _require_primary_key_value(record[key_index])
    if primary_key_value in seen_keys:
        raise ValueError(f"duplicate primary key value {primary_key_value!r}")  # noqa: TRY003
    seen_keys.add(primary_key_value)
    return primary_key_value


def _read_records(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("csv content must be strict UTF-8") from error  # noqa: TRY003
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed csv content: {error}") from error  # noqa: TRY003


class _CSVRowSourceAdapter:
    """Resolve row inputs into captured Sources and read them back exactly."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        if len(value.columns) != len(value.values):
            raise ValueError("csv row columns and values must have the same width")  # noqa: TRY003
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    """Expose one row as ordered JSON evidence without retyping any field."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        if len(source.columns) != len(source.values):
            raise ValueError("csv row columns and values must have the same width")  # noqa: TRY003
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = _CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
