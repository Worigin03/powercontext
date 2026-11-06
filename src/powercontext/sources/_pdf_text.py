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

"""Validate and extract physical pages from complete PDF bytes."""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter

from pypdf import PageObject, PdfReader
from pypdf._text_extraction import _layout_mode
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
    PdfObject,
    StreamObject,
)

_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")


def extract_pdf_pages(content: bytes) -> tuple[str, ...]:
    """Read the final PDF revision and extract all its physical pages."""
    # pypdf may fall back to an older revision when an update is truncated.
    if not content.rstrip(b"\x00\t\n\f\r ").endswith((b"\n%%EOF", b"\r%%EOF")):
        raise ValueError("pdf content must end with a complete EOF marker")  # noqa: TRY003
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("pdf requires a user password")  # noqa: TRY003, TRY301
        count = _validate_page_tree(reader.root_object["/Pages"])
        pages = tuple(reader.pages)
        if not count or len(pages) != count:
            raise ValueError("PDF page tree count does not match its pages")  # noqa: TRY003, TRY301
        return tuple(_extract_physical_page(page) for page in pages)
    except Exception as error:  # pypdf exposes several families of parse errors
        raise ValueError(f"pdf content cannot be parsed: {error}") from error  # noqa: TRY003


def _validate_page_tree(root: PdfObject) -> int:
    """Check every branch and leaf before pypdf flattens and inherits resources."""
    pending = [(root, False)]
    seen = set()
    leaves = 0
    expected_ends = []
    while pending:
        node, closing = pending.pop()
        if closing:
            if leaves != expected_ends.pop():
                raise ValueError("PDF page tree count does not match its descendants")  # noqa: TRY003
            continue

        resolved = node.get_object()
        if not isinstance(resolved, DictionaryObject) or id(resolved) in seen:
            raise ValueError("PDF page tree contains an invalid or repeated node")  # noqa: TRY003
        seen.add(id(resolved))
        kind = resolved.get("/Type")
        if kind == "/Page" and resolved is not root:
            leaves += 1
        elif kind == "/Pages":
            kids = resolved["/Kids"]
            count = resolved["/Count"]
            if not isinstance(kids, ArrayObject) or not isinstance(count, NumberObject) or count < 0:
                raise ValueError("PDF page tree branch is invalid")  # noqa: TRY003
            expected_ends.append(leaves + count)
            pending.append((node, True))
            pending.extend((child, False) for child in reversed(kids))
        else:
            raise ValueError("PDF page tree contains an invalid node type")  # noqa: TRY003
    return leaves


def _extract_physical_page(page: PageObject) -> str:
    contents = page.get("/Contents")
    if contents is not None:
        contents = contents.get_object()
        streams = contents if isinstance(contents, ArrayObject) else (contents,)
        for stream in streams:
            if not isinstance(stream.get_object(), (StreamObject, NullObject)):
                raise TypeError("PDF page content is not a stream")  # noqa: TRY003
    return _extract_page_text(page)


def _text_characters(text: str) -> Counter[str]:
    return Counter(char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum())


def _extract_page_text(page: PageObject, active_forms: frozenset[int] = frozenset()) -> str:
    """Reconstruct word boundaries without dropping text in forms or rotations."""
    original = page.get("/Contents")
    if original is None or isinstance(original.get_object(), NullObject):
        return ""
    # Both extraction modes use the same byte operands. Reuse one decoded
    # stream so large reports do not parse each page's operators twice.
    contents = ContentStream(original, page.pdf, "bytes")
    page[NameObject("/Contents")] = contents
    try:
        plain = page.extract_text() or ""
    finally:
        page[NameObject("/Contents")] = original
    try:
        groups = _layout_mode.text_show_operations(
            iter(contents.operations), page._layout_mode_fonts(), strip_rotated=False
        )
        layout = ""
        if groups:
            rows = _layout_mode.y_coordinate_groups(groups)
            width = _layout_mode.fixed_char_width(groups)
            layout = _layout_mode.fixed_width_page(rows, width, space_vertically=True, font_height_weight=1)
    except Exception:  # Layout support can be narrower than plain extraction.
        return plain
    if plain.strip():
        plain_chars = _text_characters(plain)
        if not plain_chars:
            return plain
        try:
            layout = _restore_form_text(page, contents, layout, plain_chars - _text_characters(layout), active_forms)
        except Exception:  # A layout-only limitation must not discard plain text.
            return plain
        # Preserve the original letter/number multiset before accepting layout.
        if plain_chars != _text_characters(layout):
            return plain
        layout = _separate_column_labels(plain, layout)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in layout.splitlines()).strip()


def _separate_column_labels(plain: str, layout: str) -> str:
    """Restore intact labels lost to layout's horizontal column grouping."""
    plain_terms = Counter(_TOKEN_PATTERN.findall(plain.lower()))
    missing = plain_terms - Counter(_TOKEN_PATTERN.findall(layout.lower()))

    def separate(match: re.Match[str]) -> str:
        original = match.group()
        word = original.lower()
        if plain_terms[word] or len(word) > 128:
            return original
        boundaries: dict[int, tuple[int, ...]] = {0: ()}
        for end in range(2, len(word) + 1):
            for start, cuts in boundaries.items():
                if end - start >= 2 and missing[word[start:end]]:
                    boundaries[end] = (*cuts, end)
                    break
        cuts = boundaries.get(len(word), ())
        if len(cuts) < 3:
            return original
        parts = [original[start:end] for start, end in zip((0, *cuts[:-1]), cuts, strict=True)]
        used = Counter(part.lower() for part in parts)
        if used - missing:
            return original
        missing.subtract(used)
        return " ".join(parts)

    return re.sub(r"[a-zA-Z]{12,}", separate, layout)


def _restore_form_text(
    page: PageObject, contents: ContentStream, layout: str, missing: Counter[str], active: frozenset[int]
) -> str:
    """Recover text from actually drawn forms omitted by pypdf layout mode."""
    if not missing:
        return layout
    resources = page.get("/Resources", DictionaryObject()).get_object()
    objects = resources.get("/XObject", DictionaryObject()).get_object()
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
