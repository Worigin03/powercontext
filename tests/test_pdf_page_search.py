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
async def test_document_id_survives_memory_text_normalization():
    definitions = registry()
    page = PDFPageInput(
        document_id="  Cafe\u0301\nHoldings_2022_10K",
        document_sha256="a" * 64,
        page_number=9,
        text="Contract liabilities increased to 430 million.",
    )
    source = await definitions.resolve(page)
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [page])
        citations = await search_pdf_pages(memory, "What were the contract liabilities?")
        assert await source_ids(memory, citations) == {source.name}


@pytest.mark.asyncio
async def test_dense_page_can_be_persisted_and_recalled_in_full(tmp_path):
    definitions = registry()
    page = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=12,
        text="Operating results and consolidated financial disclosures.\n" * 160
        + "Asset retirement obligations increased to 170 million.",
    )
    source = await definitions.resolve(page)
    projected = project_source_for_transport(definitions, source).projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"]
    assert len(projected.encode("utf-8")) > 8192
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dense-pages.db'}"))
    async with open_builtin_runtime(config, candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [page])
        scope_id = memory.scope_id
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        citations = await search_pdf_pages(memory, "What were Acme's asset retirement obligations?", limit=1)
        assert len(citations) == 1
        record = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
        assert record.entry.sources[0].source_id == source.name
        assert record.entry.text == projected


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


@pytest.mark.asyncio
async def test_cash_flow_subsection_is_recalled_without_query_terms():
    definitions = registry()
    original = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=30,
        text="Consolidated Statements of Cash Flows\n2022 2021\nOperations 34 20\nDepreciation 6 4",
    )
    following = original.model_copy(
        update={
            "page_number": 31,
            "text": "Investing Activities\nPurchases 25 19\nProceeds 12 7",
        }
    )
    references = [
        original.model_copy(
            update={
                "page_number": number,
                "text": f"Discussion {number}\nCash flow statement and cash flows from operating activities.",
            }
        )
        for number in (1, 3, 5)
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [original, following, *references])
        citations = await search_pdf_pages(memory, "Show Acme's cash flow statement", limit=2)
        assert await source_ids(memory, citations) == {
            (await definitions.resolve(original)).name,
            (await definitions.resolve(following)).name,
        }


@pytest.mark.asyncio
async def test_fragmented_navigation_header_does_not_hide_financial_statements():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=(
                "EBITDA operating profit operating income depreciation amortization cash flows. "
                "EBITDA EBITDA compensation targets in 2022."
                if i == 1
                else f"Acme corporate governance in 2022. Discussion {i}."
            ),
        )
        for i in range(1, 26)
    ]
    pages += [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=40,
            text="T\nable of\nContents\nAcme Company and Subsidiaries\nConsolidated Statement of Income\n"
            "2022 2021\nNet revenue 100 90\nOperating income 30 25",
        ),
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=44,
            text="T\nable of\nContents\nAcme Company and Subsidiaries\nConsolidated Statement of Cash Flows\n"
            "2022 2021\nDepreciation and amortization 8 7\nCash provided by operating activities 40 35",
        ),
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What is Acme's FY2022 EBITDA?", limit=2)
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in pages[-2:]}


@pytest.mark.asyncio
async def test_quick_ratio_question_recalls_its_balance_sheet_components():
    definitions = registry()
    page = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=30,
        text="Consolidated Balance Sheets\n2022 2021\nCash and cash equivalents 50 40\n"
        "Marketable securities 20 10\nAccounts receivable 30 20\nTotal current liabilities 60 50",
    )
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [page])
        citations = await search_pdf_pages(memory, "What is Acme's quick ratio?", limit=20)
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "What was Acme's free cash flow in 2022?",
        "How much cash did Acme's operations generate, and what was spent on capital projects?",
    ],
)
async def test_free_cash_flow_recalls_both_line_items_even_with_literal_mentions(question):
    definitions = registry()
    template = PDFPageInput(document_id="ACME_2022_10K", document_sha256="a" * 64, page_number=1, text="")
    references = [
        template.model_copy(
            update={"page_number": page, "text": f"Management discussion\nFree cash flow target, see note {page}."}
        )
        for page in range(1, 31)
    ]
    evidence = [
        template.model_copy(update={"page_number": 42, "text": "Cash generated by operating activities 84 77"}),
        template.model_copy(update={"page_number": 50, "text": "Purchases of property and equipment (13) (11)"}),
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [*references, *evidence])
        citations = await search_pdf_pages(memory, question, limit=2)
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name for page in evidence}


@pytest.mark.asyncio
async def test_explicit_income_statements_are_retrieved_when_a_line_item_is_absent():
    definitions = registry()
    statement = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=42,
        text="Consolidated Statements of Income\n2022 2021\nNet sales 100 95\nOperating income 30 25",
    )
    notes = [
        statement.model_copy(
            update={"page_number": page, "text": f"Restructuring costs\nCharges to the reserve were {page} million."}
        )
        for page in range(1, 23)
    ]
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [*notes, statement])
        citations = await search_pdf_pages(
            memory, "What restructuring costs are explicitly shown in Acme's income statements?", limit=2
        )
        assert (await definitions.resolve(statement)).name in await source_ids(memory, citations)


@pytest.mark.asyncio
async def test_financial_acronym_recalls_expanded_statement_label():
    definitions = registry()
    page = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=30,
        text="Consolidated Statements of Income\n2022 2021\nResearch and development 50 40\n"
        "Revenue 200 180\nOperating income 30 20",
    )
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, [page])
        citations = await search_pdf_pages(memory, "How much did Acme spend on R&D?", limit=20)
        assert await source_ids(memory, citations) == {(await definitions.resolve(page)).name}


@pytest.mark.asyncio
async def test_explicit_report_year_is_not_overwhelmed_by_older_report_notes():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2021_10K",
            document_sha256="b" * 64,
            page_number=i,
            text=f"Income tax rate. Effective income tax rates and taxes. Tax rate reconciliation note {i}.\n"
            "Fiscal 2021 tax information and outlook for 2022. Income tax rate effective taxes.",
        )
        for i in range(1, 30)
    ]
    current = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=30,
        text="Income taxes\nThe effective rate was 18 percent for the fiscal period.",
    )
    pages.append(current)
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What was Acme's effective income tax rate in FY2022?", limit=20)
        assert (await definitions.resolve(current)).name in await source_ids(memory, citations)


@pytest.mark.asyncio
async def test_primary_statement_survives_a_report_full_of_matching_appendices():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=f"Debt to equity ratio. Debt borrowings and equity ratios. Appendix {i}. "
            "Debt equity borrowing ratio covenant discussion.",
        )
        for i in range(1, 401)
    ]
    statement = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=401,
        text="Consolidated Balance Sheets\n2022 2021\nTotal liabilities 100 90\nStockholders equity 200 180\n"
        + "\n".join(f"Asset category {i} 100 90" for i in range(200)),
    )
    pages.append(statement)
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What is Acme's debt to equity ratio?", limit=20)
        assert (await definitions.resolve(statement)).name in await source_ids(memory, citations)


@pytest.mark.asyncio
async def test_statement_with_individual_numeric_cell_lines_is_retrieved():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2022_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=f"EBITDA performance target. Operating income, depreciation and amortization. Discussion {i}.",
        )
        for i in range(1, 26)
    ]
    statement = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="a" * 64,
        page_number=30,
        text="T\nable of\nContents\nAcme and Subsidiaries\nConsolidated\nStatement of Income\n"
        "2022\n2021\nRevenue\n100\n90\nOperating income\n30\n25\nNet income\n20\n15",
    )
    pages.append(statement)
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What was Acme's FY2022 EBITDA?", limit=1)
        assert await source_ids(memory, citations) == {(await definitions.resolve(statement)).name}


@pytest.mark.asyncio
async def test_comparative_year_evidence_is_not_excluded_by_report_date():
    definitions = registry()
    pages = [
        PDFPageInput(
            document_id="ACME_2021_10K",
            document_sha256="a" * 64,
            page_number=i,
            text=f"Pension costs for 2021. Retirement costs and pension expense. Plan discussion {i}.",
        )
        for i in range(1, 26)
    ]
    comparative = PDFPageInput(
        document_id="ACME_2022_10K",
        document_sha256="b" * 64,
        page_number=30,
        text="Retirement benefits\nPension costs for 2022, 2021 and 2020 were 150, 120 and 110, respectively.",
    )
    pages.append(comparative)
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What were Acme's pension costs in FY2021?", limit=20)
        assert (await definitions.resolve(comparative)).name in await source_ids(memory, citations)


@pytest.mark.asyncio
async def test_statements_remain_available_across_same_issuer_reports():
    definitions = registry()
    pages = []
    for year, digest in ((2019, "a"), (2020, "b"), (2021, "c"), (2022, "d")):
        pages.extend(
            PDFPageInput(
                document_id=f"ACME_{year}_10K",
                document_sha256=digest * 64,
                page_number=i,
                text="Operating cash flows and revenue growth. Net income, depreciation and amortization. "
                f"Discussion of 2019 EBITDA and capital expenditures. Note {i}.",
            )
            for i in range(1, 8)
        )
        pages.extend([
            PDFPageInput(
                document_id=f"ACME_{year}_10K",
                document_sha256=digest * 64,
                page_number=40,
                text=f"Consolidated Statements of Income\n{year} {year - 1}\nRevenue 100 90\nOperating income 20 18",
            ),
            PDFPageInput(
                document_id=f"ACME_{year}_10K",
                document_sha256=digest * 64,
                page_number=42,
                text=f"Consolidated Statements of Cash Flows\n{year} {year - 1}\nNet income 15 14\n"
                "Depreciation and amortization 5 4\nCapital expenditures 10 9",
            ),
        ])
    statements = {(await definitions.resolve(page)).name for page in pages if page.page_number in {40, 42}}
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=EvidencePipeline()) as runtime:
        memory = await seed(runtime, definitions, pages)
        citations = await search_pdf_pages(memory, "What was Acme's EBITDA less capex in FY2019?", limit=20)
        assert statements <= await source_ids(memory, citations)
