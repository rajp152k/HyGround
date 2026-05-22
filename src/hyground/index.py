"""Workspace and document indexes for HyGround."""

from __future__ import annotations

import builtins
import inspect
from dataclasses import dataclass, field
from typing import Iterable

import hy
from hy.models import Expression, List as HyList, String, Symbol
from lsprotocol import types as lsp

from .core_docs import CORE_DOCS
from .model import SourceRange, SymbolInfo, SymbolKind


@dataclass
class ParseDiagnostic:
    message: str
    line: int = 0
    character: int = 0

    def to_lsp(self) -> lsp.Diagnostic:
        return lsp.Diagnostic(
            range=lsp.Range(
                start=lsp.Position(line=self.line, character=self.character),
                end=lsp.Position(line=self.line, character=self.character + 1),
            ),
            message=self.message,
            source="hyground",
            severity=lsp.DiagnosticSeverity.Error,
        )


@dataclass
class DocumentIndex:
    uri: str
    source: str
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)
    diagnostics: list[ParseDiagnostic] = field(default_factory=list)

    @classmethod
    def build(cls, uri: str, source: str) -> "DocumentIndex":
        index = cls(uri=uri, source=source)
        try:
            forms = list(hy.read_many(source, filename=uri))
        except Exception as exc:  # Hy parse exceptions don't share a stable base type.
            index.diagnostics.append(_diagnostic_from_exception(exc))
            return index

        for form in forms:
            index._walk_form(form)
        return index

    def _walk_form(self, form: object) -> None:
        if isinstance(form, Expression) and form:
            self._record_definition(form)
            for child in form:
                self._walk_form(child)
        elif isinstance(form, HyList):
            for child in form:
                self._walk_form(child)

    def _record_definition(self, form: Expression) -> None:
        head = _symbol_name(form[0])
        if head in {"defn", "defmacro"}:
            self._record_callable(form, head)
        elif head == "defclass":
            self._record_class(form)
        elif head == "setv":
            self._record_setv(form)

    def _record_callable(self, form: Expression, head: str) -> None:
        if len(form) < 3 or not isinstance(form[1], Symbol):
            return
        name = str(form[1])
        params = _format_model(form[2])
        body = list(form[3:])
        doc = _leading_docstring(body)
        kind = SymbolKind.LOCAL_MACRO if head == "defmacro" else SymbolKind.LOCAL_FUNCTION
        self.symbols[name] = SymbolInfo(
            name=name,
            kind=kind,
            detail=f"local {head}",
            signature=f"{params}",
            documentation=doc,
            source=SourceRange.from_hy_model(self.uri, form[1]),
        )

    def _record_class(self, form: Expression) -> None:
        if len(form) < 2 or not isinstance(form[1], Symbol):
            return
        name = str(form[1])
        bases = _format_model(form[2]) if len(form) > 2 else "[]"
        body = list(form[3:]) if len(form) > 3 else []
        doc = _leading_docstring(body)
        self.symbols[name] = SymbolInfo(
            name=name,
            kind=SymbolKind.LOCAL_CLASS,
            detail="local defclass",
            signature=f"{bases}",
            documentation=doc,
            source=SourceRange.from_hy_model(self.uri, form[1]),
        )

    def _record_setv(self, form: Expression) -> None:
        # Hy supports complex assignment targets. MVP: record simple symbol targets.
        for target in form[1::2]:
            if isinstance(target, Symbol):
                name = str(target)
                self.symbols[name] = SymbolInfo(
                    name=name,
                    kind=SymbolKind.LOCAL_VARIABLE,
                    detail="local setv",
                    documentation="Local value bound with setv.",
                    source=SourceRange.from_hy_model(self.uri, target),
                )


class WorkspaceIndex:
    """Explicit server-owned index; no process-global symbol table."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentIndex] = {}
        self.core_symbols = _load_core_symbols()
        self.builtin_symbols = _load_builtin_symbols()

    def update_document(self, uri: str, source: str) -> DocumentIndex:
        document = DocumentIndex.build(uri, source)
        self.documents[uri] = document
        return document

    def remove_document(self, uri: str) -> None:
        self.documents.pop(uri, None)

    def symbols_for_completion(self, uri: str, prefix: str) -> list[SymbolInfo]:
        seen: set[str] = set()
        out: list[SymbolInfo] = []
        for symbols in (
            self.documents.get(uri, DocumentIndex(uri, "")).symbols.values(),
            self.core_symbols.values(),
            self.builtin_symbols.values(),
        ):
            for symbol in symbols:
                if symbol.name in seen:
                    continue
                if symbol.name.startswith(prefix):
                    seen.add(symbol.name)
                    out.append(symbol)
        return sorted(out, key=lambda s: (s.name.startswith("_"), s.name))

    def resolve(self, uri: str, name: str) -> SymbolInfo | None:
        document = self.documents.get(uri)
        if document and name in document.symbols:
            return document.symbols[name]
        if name in self.core_symbols:
            return self.core_symbols[name]
        if name in self.builtin_symbols:
            return self.builtin_symbols[name]
        return None


_DEFCORE_DETAIL = "Hy core form"


def _load_core_symbols() -> dict[str, SymbolInfo]:
    # Importing hy installs builtins._hy_macros.
    macros = getattr(builtins, "_hy_macros", {})
    symbols: dict[str, SymbolInfo] = {}
    for name, obj in macros.items():
        doc = CORE_DOCS.get(name)
        runtime_doc = inspect.getdoc(obj) or ""
        symbols[name] = SymbolInfo(
            name=name,
            kind=SymbolKind.CORE_FORM,
            detail=_DEFCORE_DETAIL,
            signature=doc.signature if doc else "",
            documentation=doc.documentation if doc else runtime_doc,
            runtime_object=obj,
        )
    # Ensure documented forms are present even if Hy changes macro loading details.
    for name, doc in CORE_DOCS.items():
        symbols.setdefault(
            name,
            SymbolInfo(
                name=name,
                kind=SymbolKind.CORE_FORM,
                detail=_DEFCORE_DETAIL,
                signature=doc.signature,
                documentation=doc.documentation,
            ),
        )
    return symbols


def _load_builtin_symbols() -> dict[str, SymbolInfo]:
    symbols: dict[str, SymbolInfo] = {}
    for name, obj in vars(builtins).items():
        if name.startswith("_"):
            continue
        try:
            signature = str(inspect.signature(obj)) if callable(obj) else ""
        except (TypeError, ValueError):
            signature = ""
        symbols[name] = SymbolInfo(
            name=name,
            kind=SymbolKind.PYTHON_BUILTIN,
            detail="Python builtin",
            signature=signature,
            documentation=inspect.getdoc(obj) or "",
            runtime_object=obj,
        )
    return symbols


def _diagnostic_from_exception(exc: Exception) -> ParseDiagnostic:
    line = max(int(getattr(exc, "lineno", 1) or 1) - 1, 0)
    character = max(int(getattr(exc, "offset", 1) or 1) - 1, 0)
    message = getattr(exc, "msg", None) or str(exc)
    return ParseDiagnostic(message=message, line=line, character=character)


def _symbol_name(model: object) -> str:
    return str(model) if isinstance(model, Symbol) else ""


def _format_model(model: object) -> str:
    try:
        return hy.repr(model).lstrip("'")
    except Exception:
        return str(model)


def _leading_docstring(body: Iterable[object]) -> str:
    first = next(iter(body), None)
    if isinstance(first, String):
        return str(first)
    return ""
