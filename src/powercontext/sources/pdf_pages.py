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
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, NullObject, StreamObject

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
    from powercontext.builtin.runtime.application import ScopedMemoryApplication
    from powercontext.sources import SourceRef

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
DEFAULT_PDF_SEARCH_LIMIT = 10

_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset(
    """
    a an and are as at be been being between by did do does for from had has have how in into is it its
    of on or over per that the their there these this those to total was were what when where which who
    why with within without year years fy fiscal what's can could should would please any also about
    answer respond response question assuming assume perspective investment analyst details shown
    use using only based according reported report reports directly explicitly outlined state give
    calculate calculated quantity define defined unadjusted adjusted usd dollars dollar millions million
    important materially ongoing then if not has s relying rely shown shown details
    """.split()  # noqa: SIM905 - readable term list
)
_FINANCIAL_EXPANSIONS = (
    (r"\bebitda\b", ("operating income", "operating profit", "depreciation amortization", "cash flows")),
    (r"\b(?:capex|capital expenditures?)\b", ("capital expenditures", "property plant equipment")),
    (r"\b(?:legal|litigation|lawsuits?|battles?)\b", ("legal proceedings", "litigation contingencies")),
    (
        r"\b(?:geograph\w*|regions?|countries|operates)\b",
        ("geographic segments", "international operations", "reportable segments", "divisions businesses"),
    ),
    (r"\brestructur\w*\b", ("restructuring charges", "restructuring impairment")),
    (r"\b(?:cash flow|cash flows|cashflow)\b", ("cash flows", "operating activities")),
    (r"\b(?:income statement|p&l)\b", ("statements income", "statements operations", "statements earnings")),
    (r"\b(?:gross margin|gross profit)\b", ("revenue cost sales",)),
    (r"\b(?:net margin|net profit)\b", ("net income revenue",)),
    (r"\b(?:borrow\w*|debt|leverage)\b", ("debt borrowings",)),
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
    if not isinstance(document_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", document_sha256) is None:
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
        # Lenient recovery can silently drop a broken /Kids entry and report a
        # shorter, apparently successful document. Partial reports are invalid.
        reader = PdfReader(io.BytesIO(content), strict=True)
        locked = bool(reader.is_encrypted) and not bool(reader.decrypt(""))
        texts = () if locked else _extract_complete_document(reader)
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


def _extract_complete_document(reader: PdfReader) -> tuple[str, ...]:
    pages = tuple(reader.pages)
    page_tree = reader.root_object["/Pages"]
    if not isinstance(page_tree, DictionaryObject) or len(pages) != page_tree.get("/Count"):
        raise ValueError("PDF page tree count does not match its pages")  # noqa: TRY003
    texts = []
    for page in pages:
        contents = page.get("/Contents")
        if contents is not None:
            contents = contents.get_object()
            streams = contents if isinstance(contents, ArrayObject) else (contents,)
            for stream in streams:
                resolved = stream.get_object()
                # A null /Contents is legal for an empty page. Other objects
                # must not be ignored by pypdf's ContentStream concatenation.
                if not isinstance(resolved, (StreamObject, NullObject)):
                    raise TypeError("PDF page content is not a stream")  # noqa: TRY003
        texts.append(page.extract_text() or "")
    return tuple(texts)


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = DEFAULT_PDF_SEARCH_LIMIT
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct page citations for one natural-language question."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return ()
    if not question.strip():
        return ()

    # A broad question, a fiscal year and a short financial phrase have different
    # admission/FTS rankings. Pool them before applying the caller's page limit.
    candidates: dict[tuple[str, str, str], MemoryHit] = {}
    recall_limit = max(limit, min(512, max(64, limit * 16)))
    for query in _query_variants(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=recall_limit))
        for hit in page.hits:
            key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            candidates[key] = hit

    ranked = _rank_page_hits(tuple(candidates.values()), question)
    neighbors = await _continuation_neighbors(memory, ranked)
    ordered = []
    continuations = 0
    for hit in ranked:
        ordered.append(hit)
        neighbor = neighbors.get((hit.entry_id, hit.entry_version_id))
        if neighbor is not None and continuations < max(1, limit // 5):
            ordered.append(neighbor)
            continuations += 1

    citations: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    for hit in ordered:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        identity = _page_identity(entry.sources)
        if identity is None or identity in seen_pages:
            continue
        seen_pages.add(identity)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


def _page_identity(sources: tuple[SourceRef, ...]) -> str | None:
    """Use the complete persisted identity, including revision and hashed long IDs."""
    identities = {ref.source_id for ref in sources if ref.source_type == PDF_PAGE_SOURCE_NAME}
    return next(iter(identities)) if len(identities) == 1 else None


def _starts_continuation(body: str) -> bool:
    lines = _heading_lines(body)
    return bool(lines and (re.match(r"^[2-9]\d*[.)]\s", lines[0]) or "(continued)" in lines[0].casefold()))


def _heading_lines(body: str) -> list[str]:
    # Running navigation text can be emitted as "T\nable of\nContents".
    # Normalize it only for ranking; preserve the original page evidence.
    body = re.sub(r"\A\s*t\s*able\s+of\s+contents\b[^\S\n]*", "", body, flags=re.IGNORECASE)
    return [line.strip() for line in body.splitlines() if line.strip()]


async def _continuation_neighbors(
    memory: ScopedMemoryApplication, hits: tuple[MemoryHit, ...]
) -> dict[tuple[str, str], MemoryHit]:
    """Use persisted page identities to include search-recalled continuation pages.

    At most one public list read supplies identity metadata for the bounded search
    pool. It never supplies synthetic citations or substitutes pre-parsed assets.
    """
    if not any(_starts_continuation(hit.text.partition("\n")[2]) for hit in hits):
        return {}
    candidates = {(hit.entry_id, hit.entry_version_id): hit for hit in hits}
    pages = {}
    for record in (await memory.list()).entries:
        key = (record.entry.entry_id, record.entry.entry_version_id)
        identity = _page_identity(record.entry.sources)
        if key not in candidates or identity is None:
            continue
        parts = identity.rsplit(":", 2)
        if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[1]) and parts[2].isdigit():
            pages[(parts[0], parts[1], int(parts[2]))] = candidates[key]
    result = {}
    for (document, digest, number), hit in pages.items():
        following = pages.get((document, digest, number + 1))
        if following is not None and _starts_continuation(following.text.partition("\n")[2]):
            result[(hit.entry_id, hit.entry_version_id)] = following
    return result


def _question_text(question: str) -> str:
    text = unicodedata.normalize("NFKC", question).casefold()
    text = re.sub(r"\b(?:fy|fiscal\s+year)\s*['’]?(\d{4})\b", r"\1", text)  # noqa: RUF001
    text = re.sub(r"['’]s\b", "", text)  # noqa: RUF001
    return text.replace("d&a", "depreciation amortization").replace("p&l", "income statement")


def _expansions(text: str) -> tuple[str, ...]:
    # General financial vocabulary only; never document IDs, benchmark queries,
    # evidence pages, annotations, answers, or report-specific numbers.
    return tuple(phrase for pattern, phrases in _FINANCIAL_EXPANSIONS if re.search(pattern, text) for phrase in phrases)


def _query_variants(question: str) -> tuple[str, ...]:
    """Short queries avoid rejecting evidence that lacks the question's instruction words."""
    text = _question_text(question)
    terms = tuple(dict.fromkeys(token for token in _TOKEN_PATTERN.findall(text) if token not in _STOPWORDS))
    variants = [question.strip()]
    if terms:
        variants.append(" ".join(terms))
    variants.extend(_expansions(text))
    words = tuple(term for term in terms if not term.isdigit())
    variants.extend(" ".join(words[start : start + 3]) for start in range(0, len(words), 3))
    variants.extend(words)
    variants.extend(term for term in terms if term.isdigit())
    return tuple(dict.fromkeys(variant for variant in variants if variant))[:24]


def _word_terms(text: str) -> tuple[str, ...]:
    """Light inflection normalization for reranking, leaving stored text untouched."""
    words = _TOKEN_PATTERN.findall(unicodedata.normalize("NFKC", text).casefold())
    result = []
    for word in words:
        if word in _STOPWORDS:
            continue
        if word in {"operate", "operates"}:
            word = "operate"
        elif word.startswith("geograph"):
            word = "geograph"
        elif len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        result.append(word)
    return tuple(result)


def _document_match(document_id: str, question: str) -> bool:
    issuer = re.split(r"(?:19|20)\d{2}", document_id, maxsplit=1)[0]
    words = [
        word
        for word in _TOKEN_PATTERN.findall(issuer.casefold())
        if word not in {"annual", "report", "corporation", "corp", "inc", "company", "the", "and", "fy"}
    ]
    name = "".join(words)
    question_words = _TOKEN_PATTERN.findall(question)
    return len(name) >= 2 and any(
        name == "".join(question_words[start:end])
        for start in range(len(question_words))
        for end in range(start + 1, min(len(question_words), start + len(words) + 2) + 1)
    )


def _statement_role(body: str) -> str | None:
    """Recognize actual statement headings, rather than an audit/index reference."""
    lines = body.splitlines()
    if sum(len(re.findall(r"\d[\d,]*(?:\.\d+)?", line)) >= 2 for line in lines) < 3:
        return None
    headings = _heading_lines(body)
    for line in headings[:3]:
        title = " ".join(line.casefold().split())
        if len(title) > 120:
            continue
        if re.match(
            r"^(?:(?:consolidated|combined|condensed|unaudited)\s+)*"
            r"statements?\s+of\s+(?:consolidated\s+)?",
            title,
        ):
            if "cash flow" in title:
                return "cash"
            if re.search(r"of\s+(?:consolidated\s+)?comprehensive", title):
                return "comprehensive"
            if any(term in title for term in ("income", "operations", "earnings")):
                return "income"
            if "financial position" in title:
                return "balance"
        if re.match(r"^(?:consolidated\s+)?balance sheets?\b", title):
            return "balance"
    return None


def _question_roles(text: str) -> tuple[str, ...]:
    roles = []
    if "comprehensive" in text:
        roles.append("comprehensive")
    if re.search(r"\b(?:ebitda|income|profit|margin|earnings|revenues?|sales|expenses?)\b", text):
        roles.append("income")
    if re.search(r"\b(?:ebitda|capex|cash|depreciation|amortization|capital expenditures?)\b", text):
        roles.append("cash")
    if re.search(
        r"\b(?:balance|assets?|liabilit\w*|equity|inventory|receivables?|working capital|current ratio)\b", text
    ):
        roles.append("balance")
    return tuple(roles)


def _query_weights(question: str, issuer_terms: set[str]) -> dict[str, float]:
    weights = {term: 0.35 if term.isdigit() else 1.0 for term in _word_terms(question)}
    for term in _word_terms(" ".join(_expansions(question))):
        weights.setdefault(term, 0.45)
    for term in issuer_terms.intersection(weights):
        # Document identity supplies issuer/date context independently of the
        # body. Repeated boilerplate must not outweigh subject evidence.
        weights[term] *= 0.15
    return weights


def _cover_statement_roles(scored, page_roles: tuple[str | None, ...], roles: tuple[str, ...]) -> tuple[MemoryHit, ...]:
    selected = []
    for depth in range(2):
        for role in roles:
            statements = [row for row in scored if page_roles[row[1]] == role]
            if len(statements) > depth:
                selected.append(statements[depth])
    selected_indices = {row[1] for row in selected}
    return tuple(row[2] for row in selected + [row for row in scored if row[1] not in selected_indices])


def _rank_page_hits(hits: tuple[MemoryHit, ...], question: str) -> tuple[MemoryHit, ...]:
    """BM25 over the pooled page bodies, with issuer, date and statement context."""
    if not hits:
        return ()
    question_text = _question_text(question)
    bodies = tuple(hit.text.partition("\n")[2] for hit in hits)
    counts = tuple(Counter(_word_terms(body)) for body in bodies)
    document_frequency = Counter(term for count in counts for term in count)
    average_length = max(1.0, sum(sum(count.values()) for count in counts) / len(counts))
    matches = tuple(_document_match(hit.text.partition("\n")[0], question_text) for hit in hits)
    restrict_issuer = any(matches)
    issuer_terms = {
        term
        for match, hit in zip(matches, hits, strict=True)
        if match
        for term in _word_terms(hit.text.partition("\n")[0])
    }
    weights = _query_weights(question_text, issuer_terms)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", question_text))
    roles = _question_roles(question_text)
    page_roles = tuple(_statement_role(body) for body in bodies)
    geographic = bool(re.search(r"\b(?:geograph\w*|regions?|countries|operates)\b", question_text))
    scored = []
    for index, (hit, body, count) in enumerate(zip(hits, bodies, counts, strict=True)):
        if not body.strip() or (restrict_issuer and not matches[index]):
            continue
        score = _bm25_score(count, weights, document_frequency, len(hits), average_length)
        if score <= 0:
            continue
        role = page_roles[index]
        if role in roles:
            score = score * 1.3 + 2.0
        if geographic and re.search(
            r"\b(?:divisions?|segments?|geograph\w*)\b", " ".join(body.casefold().split())[:600]
        ):
            score = score * 1.3 + 1.0
        report_id = hit.text.partition("\n")[0]
        if years.intersection(re.findall(r"(?:19|20)\d{2}", report_id)):
            score *= 1.15
        scored.append((score, index, hit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    # A multi-part financial question may require different statements. Preserve
    # the best evidence for each requested role before filling with global ranks.
    return _cover_statement_roles(scored, page_roles, roles)


def _bm25_score(
    count: Counter[str],
    weights: Mapping[str, float],
    document_frequency: Counter[str],
    total: int,
    average_length: float,
) -> float:
    length = sum(count.values())
    score = 0.0
    for term, weight in weights.items():
        frequency = count.get(term, 0)
        if not frequency:
            continue
        idf = math.log1p((total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        norm = 1.3 * (0.55 + 0.45 * length / average_length)
        score += weight * idf * frequency * 2.3 / (frequency + norm)
    return score


class _PDFPageSourceAdapter:
    """Resolve page inputs into captured Sources and read them back exactly."""

    input_class: type[PDFPageInput] = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class: type[PDFPageSource] = PDFPageSource

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
    source_class: type[PDFPageSource] = PDFPageSource
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
