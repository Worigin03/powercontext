"""Value objects shared by ingestion, retrieval and context assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpecItem:
    item_id: str
    title: str
    kind: str
    status: str
    module: str
    tags: list[str]
    applies_to: list[str]
    verified_by: list[str]
    supersedes: list[str]
    superseded_by: str
    affects: list[str]
    body: str
    source_path: str
    source_line: int
    revision: str

    @property
    def citation(self) -> str:
        return f"{self.source_path}#L{self.source_line}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "module": self.module,
            "tags": self.tags,
            "applies_to": self.applies_to,
            "verified_by": self.verified_by,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "affects": self.affects,
            "body": self.body,
            "source": self.citation,
            "revision": self.revision,
        }


@dataclass
class CodeSymbol:
    symbol_id: str
    path: str
    qualname: str
    symbol_kind: str
    lineno: int
    end_lineno: int
    signature: str
    docstring: str
    module: str
    source: str

    @property
    def citation(self) -> str:
        return f"{self.path}#L{self.lineno}-L{self.end_lineno}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.qualname,
            "kind": self.symbol_kind,
            "path": self.path,
            "lines": [self.lineno, self.end_lineno],
            "signature": self.signature,
            "docstring": self.docstring,
            "source": self.citation,
            "module": self.module,
        }


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class

    test_id: str
    path: str
    name: str
    kind: str
    covers: list[str]
    docstring: str
    lineno: int
    module: str

    @property
    def citation(self) -> str:
        return f"{self.path}#L{self.lineno}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.test_id,
            "name": self.name,
            "kind": self.kind,
            "covers": self.covers,
            "path": self.path,
            "line": self.lineno,
            "docstring": self.docstring,
            "source": self.citation,
        }


@dataclass
class TaskState:
    task_id: str
    updated_at: str = ""
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    verified: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    notes: str = ""
    source: str = "powercontext"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "updated_at": self.updated_at,
            "done": self.done,
            "todo": self.todo,
            "verified": self.verified,
            "changed_files": self.changed_files,
            "notes": self.notes,
            "source": self.source,
        }

    def is_empty(self) -> bool:
        return not (self.done or self.todo or self.verified or self.changed_files)


@dataclass
class ContextPack:
    task: dict[str, Any]
    specs: list[SpecItem]
    active_decisions: list[SpecItem]
    invalidated: list[dict[str, Any]]
    code: list[CodeSymbol]
    target_tests: list[TestCase]
    regression_tests: list[TestCase]
    state: TaskState
    database: dict[str, Any]
    generated_at: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "sdd-context-pack/1",
            "generated_at": self.generated_at,
            "task": self.task,
            "specs": [item.to_dict() for item in self.specs],
            "decisions": {
                "active": [item.to_dict() for item in self.active_decisions],
                "invalidated": self.invalidated,
            },
            "code": [symbol.to_dict() for symbol in self.code],
            "tests": {
                "target": [test.to_dict() for test in self.target_tests],
                "regression": [test.to_dict() for test in self.regression_tests],
            },
            "state": self.state.to_dict(),
            "database": self.database,
        }
