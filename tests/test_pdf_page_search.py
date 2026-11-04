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

"""Financial page retrieval through real scoped Memory and exact source reads."""

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages


def registry():
    return SourceDefinitionRegistry((*BUILTIN_SOURCE_REGISTRY.definitions, PDF_PAGE_SOURCE_DEFINITION))


class EvidencePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


async def seed(runtime, definitions, pages):
    scope = (
        await runtime.scopes.create(
            ScopeDraft(
                title="Financial evidence",
                summary="Synthetic financial reports",
                idempotency_key="pdf-evidence",
            )
        )
    ).scope_id
    await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
    for page in pages:
        source = project_source_for_transport(definitions, await definitions.resolve(page))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope, observation=source))
    await runtime.memory.for_scope(scope).flush(limit=len(pages))
    return runtime.memory.for_scope(scope)


async def source_ids(memory, citations):
    return {
        (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources[0].source_id
        for citation in citations
    }


@pytest.mark.asyncio
async def test_hashed_page_identity_remains_searchable():
    definitions = registry()
    page = PDFPageInput(
        document_id="A very long issuer identifier " * 20,
        document_sha256="a" * 64,
        page_number=9,
        text="Cash provided by operating activities was 450 million.",
    )
    source = await definitions.resolve(page)
    assert len(source.name) <= 256
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [page])
        citations = await search_pdf_pages(memory, "cash provided by operating activities", limit=5)
        assert await source_ids(memory, citations) == {source.name}


@pytest.mark.asyncio
async def test_same_physical_page_in_distinct_pdf_revisions_is_not_collapsed():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256=digest * 64,
            page_number=7,
            text=f"Cash flow statement. Operating cash provided was {amount} million.",
        )
        for digest, amount in (("a", 100), ("b", 120))
    ]
    sources = [await definitions.resolve(page) for page in pages]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "Acme cash flow statement", limit=5)
        assert await source_ids(memory, citations) == {source.name for source in sources}


@pytest.mark.asyncio
async def test_natural_language_legal_question_recalls_financial_terminology():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=17,
            text="Item 3. Legal Proceedings\nLitigation and contingencies.\n"
            "The subsidiary is a defendant in pending lawsuits.",
        )
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(
            memory, "Has Acme reported any materially important ongoing legal battles from FY2022?", limit=5
        )
        assert await source_ids(memory, citations) == {(await definitions.resolve(pages[0])).name}


@pytest.mark.asyncio
async def test_financial_statement_components_survive_company_and_query_noise():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="OTHER_2022_10K",
            document_sha256="b" * 64,
            page_number=i,
            text=f"Other Corporation EBITDA capex income statement cash flow. Note {i}.",
        )
        for i in range(1, 31)
    ]
    pages += [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=42,
            text="CONSOLIDATED STATEMENTS OF INCOME\n2022 2021\nNet revenue 100 90\nOperating income 30 25",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=44,
            text="CONSOLIDATED STATEMENTS OF CASH FLOWS\n2022 2021\nDepreciation and amortization 8 7\n"
            "Capital expenditures (10) (9)\nCash provided by operating activities 40 35",
        ),
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(
            memory,
            "What is Acme's FY2022 unadjusted EBITDA less capex? Use the income statement and statement of cash flows.",
            limit=2,
        )
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in pages[-2:]}


@pytest.mark.asyncio
async def test_statement_index_and_comprehensive_income_do_not_replace_income_evidence():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=1,
            text="Financial statement schedules\nConsolidated Statement of Income\n"
            "Consolidated Statement of Cash Flows\nRefer to the income statement and cash flow statement.",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=2,
            text="Consolidated Statement of Comprehensive Income\n2022 2021\nNet income 20 18\n"
            "Other comprehensive income 5 6\nTotal comprehensive income 25 24",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=3,
            text="Consolidated Statement of Income\n2022 2021\nNet revenue 100 90\nOperating profit 30 25",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=4,
            text="Consolidated Statement of Cash Flows\n2022 2021\nDepreciation and amortization 8 7\n"
            "Capital expenditures (10) (9)\nOperating activities 40 35",
        ),
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(
            memory,
            "Acme FY2022 EBITDA using operating income, D&A from the cash flow statement and the P&L statement",
            limit=2,
        )
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in pages[-2:]}


@pytest.mark.asyncio
async def test_operating_geographies_recall_business_division_evidence():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=f"The corporation primarily operates. Geographic strategy overview {i}.",
        )
        for i in range(1, 26)
    ]
    pages += [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=40,
            text="Our Divisions\nReportable segments comprise North Division and South Division.",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=41,
            text="Our Divisions (continued)\nThe remaining divisions are East Division and West Division.",
        ),
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(
            memory, "What are the geographies that Acme primarily operates in FY2022?", limit=2
        )
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in pages[-2:]}


@pytest.mark.asyncio
async def test_adjacent_continuation_is_retained_after_restart(tmp_path):
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=f"Acme primarily operates globally. Geographic strategy overview {i}.",
        )
        for i in range(1, 25)
    ]
    pages += [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=40,
            text="Our Divisions\nThe reportable segments comprise the first five business divisions.",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=41,
            text="6) The remaining territories where Acme primarily operates are described here.",
        ),
    ]
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        scope_id = memory.scope_id
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        question = "What are the geographies that Acme primarily operates in FY2022?"
        citations = await search_pdf_pages(memory, question, limit=2)
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in pages[-2:]}
        assert len(await search_pdf_pages(memory, question, limit=1)) == 1
