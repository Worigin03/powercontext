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

"""Page-level financial-report evidence extracted from complete PDF bytes."""

from __future__ import annotations

import hashlib
import io
import re
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
DEFAULT_PDF_SEARCH_LIMIT = 10

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOPWORDS = frozenset(
    """
    a an and are as at be been being between by did do does for from had has have how in into is it its
    of on or over per that the their there these this those to total was were what when where which who
    why with within without year years fy what's
    """.split()  # noqa: SIM905 - readable term list
)


def _require_document_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_id must be a non-blank string")  # noqa: TRY003
    return value


class PDFDocumentInput(BaseModel):
    """One complete original PDF file addressed by document id."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)


class PDFPageInput(BaseModel):
    """One physical PDF page with its document revision and extracted text."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_document_id(value)


class PDFPageSource(Source):
    """Captured evidence for exactly one physical page of one PDF revision."""

    source_id: Annotated[str, Field(max_length=MAX_SOURCE_ID_LENGTH)]
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Build the revision-scoped identity of one physical page."""

    _require_document_id(document_id)
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be a lowercase sha256 digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    readable = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    digest = hashlib.sha256(f"{document_id}\x00{document_sha256}\x00{page_number}".encode()).hexdigest()
    return f"pdf:{digest}"


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Extract every physical page in order, or fail without partial output."""

    document_id = _require_document_id(value.document_id)
    content = value.content
    if not content:
        raise ValueError("pdf content must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content))
        locked = bool(reader.is_encrypted) and not bool(reader.decrypt(""))
        texts = () if locked else tuple(page.extract_text() or "" for page in reader.pages)
    except Exception as error:  # pypdf raises a wide family of parse errors
        raise ValueError(f"pdf content cannot be parsed: {error}") from error  # noqa: TRY003
    if locked:
        raise ValueError("pdf requires a user password")  # noqa: TRY003
    if not texts:
        raise ValueError("pdf must contain at least one page")  # noqa: TRY003
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=document_sha256,
            page_number=index,
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    )


async def search_pdf_pages(
    memory: object, question: str, limit: int = DEFAULT_PDF_SEARCH_LIMIT
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct page citations for one natural-language question."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return ()
    if not question.strip():
        return ()

    citations: list[MemoryCitation] = []
    seen_hits: set[tuple[str, str]] = set()
    seen_pages: set[tuple[str, int]] = set()
    for query in _query_variants(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(limit * 3, 30)))
        for hit in page.hits:
            key = (hit.entry_id, hit.entry_version_id)
            if key in seen_hits:
                continue
            seen_hits.add(key)
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            location = _page_location(entry.sources)
            if location is None or location in seen_pages:
                continue
            seen_pages.add(location)
            citations.append(citation)
            if len(citations) >= limit:
                return tuple(citations)
    return tuple(citations)


def _page_location(sources: object) -> tuple[str, int] | None:
    """Map one Memory entry back to the physical page its evidence cites."""

    for reference in sources:
        source_id = getattr(reference, "source_id", "")
        if getattr(reference, "source_type", "") != PDF_PAGE_SOURCE_NAME:
            continue
        parts = source_id.removeprefix("pdf:").rsplit(":", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            continue
        return parts[0], int(parts[2])
    return None


def _query_variants(question: str) -> tuple[str, ...]:
    """Prefer precise lexical queries, then widen to keep recall over page text."""

    lowered = question.lower()
    terms = [token for token in _TOKEN_PATTERN.findall(lowered) if token not in _STOPWORDS]
    numbers = [token for token in terms if any(character.isdigit() for character in token)]
    variants: list[str] = []
    if terms:
        variants.append(" ".join(dict.fromkeys(terms)))
    if numbers:
        variants.append(" ".join(dict.fromkeys(numbers)))
    variants.append(question.strip())
    return tuple(dict.fromkeys(variant for variant in variants if variant))


class _PDFPageSourceAdapter:
    """Resolve page inputs into captured Sources and read them back exactly."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidenceProjection:
    """Expose one page as document-scoped textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = _PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(_PDFPageTextEvidenceProjection(),),
)

__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
