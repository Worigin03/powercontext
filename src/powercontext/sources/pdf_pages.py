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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PageObject, PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NullObject, StreamObject

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation
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
    (r"\b(?:quick|acid test)\s+ratio\b", ("cash equivalents marketable securities receivables", "current liabilities")),
    (r"\b(?:current ratio|working capital)\b", ("current assets", "current liabilities")),
    (r"\b(?:r&d|research and development)\b", ("research development",)),
    (r"\b(?:sg&a|selling general|general and administrative)\b", ("selling general administrative expenses",)),
    (r"\b(?:pp&e|ppe|fixed assets?)\b", ("property plant equipment",)),
    (r"\b(?:roa|return on assets?)\b", ("net income", "total assets")),
    (r"\b(?:roe|return on equity)\b", ("net income", "shareholders equity", "stockholders equity")),
    (r"\b(?:inventory turnover|days inventory)\b", ("cost sales", "cost goods sold", "inventories")),
    (r"\b(?:dso|days sales outstanding)\b", ("net sales revenue", "accounts receivable")),
    (r"\b(?:tax|taxes|taxation)\b", ("income taxes", "effective tax rate")),
    (r"\b(?:dividends?|payout)\b", ("dividends paid", "net income")),
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


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
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
        texts.append(_extract_page_text(page))
    return tuple(texts)


def _text_characters(text: str) -> Counter[str]:
    return Counter(char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum())


def _extract_page_text(page: PageObject, active_forms: frozenset[int] = frozenset()) -> str:
    """Reconstruct word boundaries without dropping text in forms or rotations."""
    plain = page.extract_text() or ""
    try:
        layout = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False) or ""
    except Exception:  # Layout support can be narrower than plain extraction.
        return plain
    if plain.strip():
        plain_chars = _text_characters(plain)
        if not plain_chars:
            return plain
        try:
            layout = _restore_form_text(page, layout, plain_chars - _text_characters(layout), active_forms)
        except Exception:  # A layout-only limitation must not discard plain text.
            return plain
        # Preserve the original letter/number multiset before accepting layout.
        if plain_chars != _text_characters(layout):
            return plain
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in layout.splitlines()).strip()


def _restore_form_text(page: PageObject, layout: str, missing: Counter[str], active: frozenset[int]) -> str:
    """Recover text from actually drawn forms omitted by pypdf layout mode."""
    if not missing:
        return layout
    resources = page.get("/Resources", DictionaryObject()).get_object()
    objects = resources.get("/XObject", DictionaryObject()).get_object()
    contents = page.get_contents()
    if contents is None:
        return layout
    for operands, operator in contents.operations:
        if operator != b"Do":
            continue
        form = objects[operands[0]].get_object()
        if form.get("/Subtype") != "/Form" or id(form) in active:
            continue
        # Text extraction uses content coordinates and resources, not page size.
        view = PageObject.create_blank_page(pdf=page.pdf, width=1, height=1)
        del view["/Parent"]  # Detached view: use its explicit resources only.
        view[NameObject("/Contents")] = form
        view[NameObject("/Resources")] = form.get("/Resources", resources)
        text = _extract_page_text(view, active | {id(form)})
        characters = _text_characters(text)
        if characters and not characters - missing:
            layout += "\n" + text
            missing -= characters
    return layout


@dataclass(frozen=True)
class _PDFCandidate:
    citation: MemoryCitation
    identity: str
    document_id: str
    body: str


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = DEFAULT_PDF_SEARCH_LIMIT
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct page citations for one natural-language question."""

    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or not question.strip():
        return ()

    # The native lexical admission gate cannot match an acronym or a derived
    # ratio to its unstated components. Read the active, scoped Memory snapshot
    # as well, so its genuine page records remain eligible for offline ranking.
    candidates = []
    for record in (await memory.list()).entries:
        identity = _page_identity(record.entry.sources)
        document_id, separator, body = record.entry.text.partition("\n")
        if identity is not None and separator and body.strip():
            candidates.append(_PDFCandidate(record.citation, identity, document_id, body))
    if not candidates:
        return ()

    native_ranks: dict[tuple[str, str], float] = {}
    recall_limit = max(limit, min(512, max(64, limit * 16)))
    for query in _query_variants(question)[:8]:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=recall_limit))
        for rank, hit in enumerate(page.hits, 1):
            key = (hit.entry_id, hit.entry_version_id)
            native_ranks[key] = native_ranks.get(key, 0.0) + 1 / (60 + rank)

    ranked = _rank_pages(tuple(candidates), question, native_ranks)
    ordered = _with_continuations(ranked, limit)

    citations: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    for hit in ordered:
        citation = hit.citation
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


def _continuation_neighbors(hits: tuple[_PDFCandidate, ...]) -> dict[str, _PDFCandidate]:
    """Keep a physical continuation anchored to the same persisted PDF revision."""
    pages = {}
    for hit in hits:
        parts = hit.identity.rsplit(":", 2)
        if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[1]) and parts[2].isdigit():
            pages[(parts[0], parts[1], int(parts[2]))] = hit
    result = {}
    for (document, digest, number), hit in pages.items():
        following = pages.get((document, digest, number + 1))
        if following is not None and _starts_continuation(following.body):
            result[hit.identity] = following
    return result


def _with_continuations(hits: tuple[_PDFCandidate, ...], limit: int) -> tuple[_PDFCandidate, ...]:
    neighbors = _continuation_neighbors(hits)
    ordered = []
    continuations = 0
    for hit in hits:
        ordered.append(hit)
        neighbor = neighbors.get(hit.identity)
        if neighbor is not None and continuations < max(1, limit // 5):
            ordered.append(neighbor)
            continuations += 1
    return tuple(ordered)


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
        if word in {"taxes", "losses", "businesses"}:
            word = word[:-2]
        elif word in {"operate", "operates"}:
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
    # PDF text order may emit every numeric cell on a separate line. Counting
    # multi-number lines would reject an otherwise complete financial table.
    if len(re.findall(r"\d[\d,]*(?:\.\d+)?", body)) < 6:
        return None
    headings = _heading_lines(body)
    titles = (
        " ".join(" ".join(headings[start:end]).casefold().split())
        for start in range(min(3, len(headings)))
        for end in range(start + 1, min(len(headings), start + 3) + 1)
    )
    for title in titles:
        if len(title) > 160:
            continue
        match = re.fullmatch(
            r"(?:(?:consolidated|combined|condensed|unaudited)\s+)*"
            r"(?:statements?\s+of\s+(?:consolidated\s+)?"
            r"(?P<kind>cash flows?|comprehensive (?:income|loss)|income|operations|earnings|financial position)"
            r"(?:\s+and\s+(?:comprehensive (?:income|loss)|retained earnings))?|balance sheets?)"
            r"(?:\s*\([^)]*\))*",
            title,
        )
        if match is None:
            continue
        kind = match.group("kind")
        if kind is None or kind == "financial position":
            return "balance"
        if kind.startswith("cash"):
            return "cash"
        if kind.startswith("comprehensive"):
            return "comprehensive"
        return "income"
    return None


def _question_roles(text: str) -> tuple[str, ...]:
    roles = []
    if "comprehensive" in text:
        roles.append("comprehensive")
    if re.search(
        r"\b(?:ebitda|income|profit|margin|earnings|revenues?|sales|expenses?|r&d|sg&a|roa|roe|"
        r"return on (?:assets?|equity)|inventory turnover|days sales outstanding)\b",
        text,
    ):
        roles.append("income")
    if re.search(r"\b(?:ebitda|capex|cash|depreciation|amortization|capital expenditures?)\b", text):
        roles.append("cash")
    if re.search(
        r"\b(?:balance|assets?|liabilit\w*|equity|inventor(?:y|ies)|receivables?|working capital|"
        r"(?:quick|current|acid test) ratio|roa|roe|dso|days sales outstanding)\b",
        text,
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


def _cover_statement_roles(
    scored, page_roles: tuple[str | None, ...], roles: tuple[str, ...]
) -> tuple[_PDFCandidate, ...]:
    selected = []
    for depth in range(2):
        for role in roles:
            statements = [row for row in scored if page_roles[row[1]] == role]
            if len(statements) > depth:
                selected.append(statements[depth])
    selected_indices = {row[1] for row in selected}
    return tuple(row[2] for row in selected + [row for row in scored if row[1] not in selected_indices])


def _report_identity(page: _PDFCandidate) -> str:
    parts = page.identity.rsplit(":", 2)
    if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[1]) and parts[2].isdigit():
        return f"{parts[0]}:{parts[1]}"
    return page.document_id


def _diversify_reports(scored, page_roles: tuple[str | None, ...], roles: tuple[str, ...]) -> tuple[_PDFCandidate, ...]:
    """Retain comparative evidence across reports of the same issuer.

    The fiscal period in a question need not be the report's publication year.
    Each report contributes complementary statements or its strongest passages
    before the remaining global ranking fills the page budget.
    """
    reports = {}
    for row in scored:
        reports.setdefault(_report_identity(row[2]), []).append(row)
    if len(reports) <= 1:
        return _cover_statement_roles(scored, page_roles, roles)
    rankings = tuple(_cover_statement_roles(rows, page_roles, roles) for rows in reports.values())
    selected = tuple(
        ranking[depth] for depth in range(max(2, len(roles))) for ranking in rankings if depth < len(ranking)
    )
    selected_ids = {page.identity for page in selected}
    return selected + tuple(row[2] for row in scored if row[2].identity not in selected_ids)


def _rank_pages(
    hits: tuple[_PDFCandidate, ...], question: str, native_ranks: Mapping[tuple[str, str], float]
) -> tuple[_PDFCandidate, ...]:
    """Rank real page records using document context, page and passage relevance."""
    if not hits:
        return ()
    question_text = _question_text(question)
    matches = tuple(_document_match(hit.document_id, question_text) for hit in hits)
    if any(matches):
        hits = tuple(hit for hit, match in zip(hits, matches, strict=True) if match)
    terms = tuple(_word_terms(hit.body) for hit in hits)
    counts = tuple(Counter(words) for words in terms)
    document_frequency = Counter(term for count in counts for term in count)
    average_length = max(1.0, sum(sum(count.values()) for count in counts) / len(counts))
    issuer_terms = {
        term for hit in hits if _document_match(hit.document_id, question_text) for term in _word_terms(hit.document_id)
    }
    weights = _query_weights(question_text, issuer_terms)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", question_text))
    roles = _question_roles(question_text)
    page_roles = tuple(_statement_role(hit.body) for hit in hits)
    # Report dates provide a soft contextual signal. A later report can contain
    # the exact historical comparative evidence requested by the question.
    report_matches = tuple(bool(years.intersection(re.findall(r"(?:19|20)\d{2}", hit.document_id))) for hit in hits)
    geographic = bool(re.search(r"\b(?:geograph\w*|regions?|countries|operates)\b", question_text))
    scored = []
    for index, (hit, words, count) in enumerate(zip(hits, terms, counts, strict=True)):
        score = _bm25_score(count, weights, document_frequency, len(hits), average_length)
        score += 0.35 * max(
            (
                _bm25_score(Counter(words[start : start + 80]), weights, document_frequency, len(hits), 80)
                for start in range(0, len(words), 40)
            ),
            default=0.0,
        )
        role = page_roles[index]
        if role in roles:
            score = score * 1.3 + 2.0
        if score <= 0:
            continue
        if geographic and re.search(
            r"\b(?:divisions?|segments?|geograph\w*)\b", " ".join(hit.body.casefold().split())[:600]
        ):
            score = score * 1.3 + 1.0
        if report_matches[index]:
            score *= 1.15
        key = (hit.citation.entry_id, hit.citation.entry_version_id)
        score += 0.5 * native_ranks.get(key, 0.0)
        scored.append((score, index, hit))
    scored.sort(key=lambda item: (-item[0], item[2].identity))
    # A multi-part financial question may require different statements. Preserve
    # the best evidence for each requested role before filling with global ranks.
    return _diversify_reports(scored, page_roles, roles)


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
