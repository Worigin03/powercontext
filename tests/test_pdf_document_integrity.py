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

"""Reject partial PDF recovery without losing legitimate empty pages."""

import io
import re

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
)

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document


def report():
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
    )
    for text in ("First page revenue 100", "Second page revenue 200"):
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    return writer


def serialize(writer):
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def parse(raw):
    return parse_pdf_document(PDFDocumentInput(document_id="integrity", content=raw))


def test_missing_page_object_does_not_return_the_remaining_pages():
    writer = report()
    reference = writer.pages[1].indirect_reference
    assert reference is not None
    raw = serialize(writer)
    pattern = rb"(?m)^" + str(reference.idnum).encode() + rb" 0 obj\b.*?endobj"
    damaged = re.sub(pattern, lambda match: b" " * len(match[0]), raw, flags=re.DOTALL)
    assert damaged != raw
    with pytest.raises(ValueError):
        parse(damaged)


def test_wrong_page_tree_count_is_not_reported_as_complete():
    raw = serialize(report()).replace(b"/Count 2", b"/Count 3")
    with pytest.raises(ValueError):
        parse(raw)


def test_non_stream_in_content_array_is_not_silently_ignored():
    writer = report()
    page = writer.pages[1]
    page[NameObject("/Contents")] = ArrayObject([page.raw_get("/Contents"), NumberObject(42)])
    with pytest.raises(ValueError):
        parse(serialize(writer))


def test_invalid_header_is_not_repaired_into_another_document():
    raw = serialize(report())
    with pytest.raises(ValueError):
        parse(b"not a PDF\n" + raw)


@pytest.mark.parametrize("encrypted", [False, True])
def test_valid_mixed_text_and_empty_pages_survive_integrity_checks(encrypted):
    writer = report()
    blank = writer.add_blank_page(width=600, height=800)
    blank[NameObject("/Contents")] = NullObject()
    if encrypted:
        writer.encrypt(user_password="", owner_password="owner")  # noqa: S106 - synthetic permissions fixture
    pages = parse(serialize(writer))
    assert [(page.page_number, page.text.strip()) for page in pages] == [
        (1, "First page revenue 100"),
        (2, "Second page revenue 200"),
        (3, ""),
    ]
