"""Parse specs, code symbols and tests into retrievable records.

Specs are Markdown; code and tests are read through the Python AST so that the
context pack can cite real line ranges instead of guessed file names.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from sdd_context.models import CodeSymbol, SpecItem, TestCase

HEADING = re.compile(r"^##\s+([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3})\s+(.+?)\s*$")
ATTRIBUTE = re.compile(r"^-\s+([a-z_]+):\s*(.*?)\s*$")
COVERS = re.compile(r"covers:\s*(.+)")

KIND_BY_PREFIX = {"REQ": "requirement", "ADR": "decision", "CTR": "contract"}


def parse_spec_file(path: Path, repo_root: Path) -> list[SpecItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    relative = path.relative_to(repo_root).as_posix()
    items: list[SpecItem] = []
    current: dict[str, object] | None = None
    body: list[str] = []
    in_body = False

    def flush() -> None:
        if current is None:
            return
        item = _build_item(current, "\n".join(body).strip(), relative)
        items.append(item)

    for index, line in enumerate(lines, start=1):
        heading = HEADING.match(line)
        if heading:
            flush()
            item_id, title = heading.group(1), heading.group(2)
            current = {
                "id": item_id,
                "title": title,
                "line": index,
                "attrs": {},
            }
            body = []
            in_body = False
            continue
        if current is None:
            continue
        if not in_body:
            attribute = ATTRIBUTE.match(line)
            if attribute:
                key, value = attribute.group(1), attribute.group(2)
                attrs = current["attrs"]
                assert isinstance(attrs, dict)
                attrs[key] = value
                continue
            if line.strip() == "":
                continue
            in_body = True
        body.append(line)
    flush()
    return items


def _build_item(current: dict[str, object], body: str, relative: str) -> SpecItem:
    item_id = str(current["id"])
    title = str(current["title"])
    attrs = current["attrs"]
    assert isinstance(attrs, dict)
    prefix = item_id.split("-", 1)[0]
    kind = str(attrs.get("kind") or KIND_BY_PREFIX.get(prefix, "requirement"))
    status = str(attrs.get("status") or "active").strip().lower()
    module = str(attrs.get("module") or "unassigned").strip()
    tags = _csv(attrs.get("tags"))
    applies_to = _csv(attrs.get("applies_to"))
    verified_by = _csv(attrs.get("verified_by"))
    supersedes = _csv(attrs.get("supersedes"))
    superseded_by = str(attrs.get("superseded_by") or "").strip()
    affects = _csv(attrs.get("affects"))
    revision = hashlib.sha256(
        "\x1f".join([item_id, title, kind, status, module, body, ",".join(tags)]).encode("utf-8")
    ).hexdigest()
    return SpecItem(
        item_id=item_id,
        title=title,
        kind=kind,
        status=status,
        module=module,
        tags=tags,
        applies_to=applies_to,
        verified_by=verified_by,
        supersedes=supersedes,
        superseded_by=superseded_by,
        affects=affects,
        body=body,
        source_path=relative,
        source_line=int(current["line"]),
        revision=revision,
    )


def _csv(value: object) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def parse_specs(specs_dir: Path, repo_root: Path) -> list[SpecItem]:
    items: list[SpecItem] = []
    for path in sorted(specs_dir.rglob("*.md")):
        items.extend(parse_spec_file(path, repo_root))
    ids = [item.item_id for item in items]
    duplicates = {item_id for item_id in ids if ids.count(item_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate spec ids: {sorted(duplicates)}")
    return items


def index_code(src_dir: Path, repo_root: Path, module_map: dict[str, str]) -> list[CodeSymbol]:
    symbols: list[CodeSymbol] = []
    for path in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo_root).as_posix()
        module_dotted = _module_name(path, src_dir)
        module = module_map.get(module_dotted.split(".")[0], "unassigned")
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualname = _qualname(node, tree)
            if qualname is None:
                continue
            qualified = f"{module_dotted}.{qualname}"
            end = getattr(node, "end_lineno", node.lineno)
            segment = "\n".join(lines[node.lineno - 1 : end])
            symbols.append(
                CodeSymbol(
                    symbol_id=qualified,
                    path=relative,
                    qualname=qualified,
                    symbol_kind=(
                        "class"
                        if isinstance(node, ast.ClassDef)
                        else ("async-function" if isinstance(node, ast.AsyncFunctionDef) else "function")
                    ),
                    lineno=node.lineno,
                    end_lineno=end,
                    signature=_signature(node),
                    docstring=ast.get_docstring(node) or "",
                    module=module,
                    source=segment,
                )
            )
    return symbols


def _module_name(path: Path, src_dir: Path) -> str:
    relative = path.relative_to(src_dir).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _qualname(node: ast.AST, tree: ast.Module) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in parent.body:
                if child is node:
                    return f"{parent.name}.{node.name}"  # type: ignore[attr-defined]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    return None


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    args = ast.unparse(node.args)  # type: ignore[attr-defined]
    returns = getattr(node, "returns", None)
    suffix = f" -> {ast.unparse(returns)}" if returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){suffix}"


def index_tests(tests_dir: Path, repo_root: Path, module_map: dict[str, str]) -> list[TestCase]:
    cases: list[TestCase] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo_root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        module_kind = _module_marker(tree)
        module_covers = _covers(ast.get_docstring(tree) or "")
        package = relative.split("/")[0] if "/" in relative else "tests"
        module = module_map.get(package, "order-query")
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            kind = _function_marker(node) or module_kind or "regression"
            covers = module_covers + _covers(ast.get_docstring(node) or "")
            cases.append(
                TestCase(
                    test_id=f"{relative}::{node.name}",
                    path=relative,
                    name=node.name,
                    kind=kind,
                    covers=sorted(set(covers)),
                    docstring=ast.get_docstring(node) or "",
                    lineno=node.lineno,
                    module=module,
                )
            )
    return cases


def _module_marker(tree: ast.Module) -> str | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets):
            continue
        return _marker_name(node.value)
    return None


def _function_marker(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for decorator in node.decorator_list:
        name = _marker_name(decorator)
        if name:
            return name
    return None


def _marker_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
            if node.value.value.id == "pytest" and node.value.attr == "mark":
                return node.attr
    return None


def _covers(docstring: str) -> list[str]:
    found: list[str] = []
    for line in docstring.splitlines():
        match = COVERS.search(line)
        if match:
            found.extend(
                part.strip() for part in re.split(r"[,、\s]+", match.group(1)) if part.strip()
            )
    return [item for item in found if re.match(r"^[A-Z]+(?:-[A-Z0-9]+)+-\d{3}$", item)]
