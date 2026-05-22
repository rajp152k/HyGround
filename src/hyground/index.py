"""Workspace and document indexes for HyGround."""

from __future__ import annotations

import builtins
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import hy
from hy.compiler import hy_compile
from hy.models import Expression, Keyword, List as HyList, String, Symbol
from lsprotocol import types as lsp
from pygls import uris

from .core_docs import CORE_DOCS
from .model import SourceRange, SymbolInfo, SymbolKind
from .resolver import PythonResolver, find_workspace_root, iter_hy_files, symbol_from_object


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
    def build(
        cls,
        uri: str,
        source: str,
        resolver: PythonResolver | None = None,
        compile_forms: bool = True,
    ) -> "DocumentIndex":
        index = cls(uri=uri, source=source)
        try:
            forms = list(hy.read_many(source, filename=uri))
        except Exception as exc:  # Hy parse exceptions don't share a stable base type.
            index.diagnostics.append(_diagnostic_from_exception(exc))
            return index

        for form in forms:
            index._walk_form(form, resolver)

        if compile_forms:
            index._record_compile_diagnostics(forms, source)
        return index

    def _record_compile_diagnostics(self, forms: list[object], source: str) -> None:
        for form in forms:
            try:
                hy_compile(form, "__main__", filename=self.uri, source=source)
            except Exception as exc:
                diagnostic = _diagnostic_from_exception(exc)
                if not any(
                    d.message == diagnostic.message
                    and d.line == diagnostic.line
                    and d.character == diagnostic.character
                    for d in self.diagnostics
                ):
                    self.diagnostics.append(diagnostic)

    def _walk_form(self, form: object, resolver: PythonResolver | None) -> None:
        if isinstance(form, Expression) and form:
            self._record_definition(form, resolver)
            for child in form:
                self._walk_form(child, resolver)
        elif isinstance(form, HyList):
            for child in form:
                self._walk_form(child, resolver)

    def _record_definition(self, form: Expression, resolver: PythonResolver | None) -> None:
        head = _symbol_name(form[0])
        if head in {"defn", "defmacro"}:
            self._record_callable(form, head)
        elif head == "defclass":
            self._record_class(form)
        elif head == "setv":
            self._record_setv(form)
        elif head == "import" and resolver is not None:
            self._record_import(form, resolver)
        elif head == "require":
            self._record_require(form)

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
            signature=f"({name} {params})",
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
            signature=f"(defclass {name} {bases})",
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

    def _record_import(self, form: Expression, resolver: PythonResolver) -> None:
        items = list(form[1:])
        i = 0
        while i < len(items):
            model = items[i]
            if isinstance(model, Keyword):
                i += 1
                continue
            module_name = _module_name(model)
            if not module_name:
                i += 1
                continue

            if i + 2 < len(items) and _is_keyword(items[i + 1], "as") and isinstance(items[i + 2], Symbol):
                alias = str(items[i + 2])
                self._add_symbol(resolver.module_symbol(alias, module_name))
                i += 3
                continue

            if i + 1 < len(items) and isinstance(items[i + 1], HyList):
                self._record_import_members(module_name, items[i + 1], resolver)
                i += 2
                continue

            # `(import os.path)` makes the top-level package useful for dotted completion.
            visible = module_name.split(".")[0] if "." in module_name else module_name
            self._add_symbol(resolver.module_symbol(visible, visible))
            self._add_symbol(resolver.module_symbol(module_name, module_name))
            i += 1

    def _record_import_members(self, module_name: str, members: HyList, resolver: PythonResolver) -> None:
        i = 0
        values = list(members)
        if len(values) == 1 and isinstance(values[0], Symbol) and str(values[0]) == "*":
            module = resolver.import_module(module_name)
            if module is None:
                return
            for py_name in getattr(module, "__all__", dir(module)):
                if py_name.startswith("_"):
                    continue
                hy_name = hy.unmangle(py_name)
                try:
                    self._add_symbol(symbol_from_object(hy_name, getattr(module, py_name), detail=f"imported from {module_name}"))
                except Exception:
                    continue
            return

        while i < len(values):
            member = values[i]
            if not isinstance(member, Symbol):
                i += 1
                continue
            original = str(member)
            visible = original
            if i + 2 < len(values) and _is_keyword(values[i + 1], "as") and isinstance(values[i + 2], Symbol):
                visible = str(values[i + 2])
                i += 3
            else:
                i += 1
            self._add_symbol(resolver.object_symbol(visible, f"{module_name}.{original}"))

    def _record_require(self, form: Expression) -> None:
        # Required macros deserve completion even before full macro resolution exists.
        if len(form) < 3:
            return
        module_name = _module_name(form[1])
        spec = form[2]
        if isinstance(spec, HyList):
            for value in spec:
                if isinstance(value, Symbol):
                    self.symbols[str(value)] = SymbolInfo(
                        name=str(value),
                        kind=SymbolKind.LOCAL_MACRO,
                        detail=f"required macro from {module_name}",
                        documentation=f"Macro required from `{module_name}`.",
                        source=SourceRange.from_hy_model(self.uri, value),
                    )

    def _add_symbol(self, symbol: SymbolInfo | None) -> None:
        if symbol is not None:
            self.symbols[symbol.name] = symbol


class WorkspaceIndex:
    """Explicit server-owned index; no process-global symbol table."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentIndex] = {}
        self.core_symbols = _load_core_symbols()
        self.builtin_symbols = _load_builtin_symbols()
        self.resolvers: dict[Path, PythonResolver] = {}
        self.indexed_roots: set[Path] = set()

    def update_document(self, uri: str, source: str) -> DocumentIndex:
        root = self.root_for_uri(uri)
        resolver = self.resolver_for_root(root)
        document = DocumentIndex.build(uri, source, resolver)
        self.documents[uri] = document
        self.ensure_project_index(root)
        return document

    def remove_document(self, uri: str) -> None:
        self.documents.pop(uri, None)

    def root_for_uri(self, uri: str) -> Path:
        try:
            path = Path(uris.to_fs_path(uri))
        except Exception:
            return Path.cwd()
        return find_workspace_root(path)

    def resolver_for_root(self, root: Path) -> PythonResolver:
        root = root.resolve()
        if root not in self.resolvers:
            self.resolvers[root] = PythonResolver(root)
        return self.resolvers[root]

    def ensure_project_index(self, root: Path) -> None:
        root = root.resolve()
        if root in self.indexed_roots:
            return
        resolver = self.resolver_for_root(root)
        for path in iter_hy_files(root):
            uri = uris.from_fs_path(str(path.resolve()))
            if uri in self.documents:
                continue
            try:
                self.documents[uri] = DocumentIndex.build(uri, path.read_text(), resolver, compile_forms=False)
            except UnicodeDecodeError:
                continue
        self.indexed_roots.add(root)

    def reindex_root(self, root: Path, open_sources: dict[str, str] | None = None) -> list[DocumentIndex]:
        """Force a fresh resolver and symbol index for ROOT.

        This is the escape hatch for real editing sessions: new files, changed
        imports, or newly installed venv packages can make old resolver misses
        stale. Reindexing drops the root's Python resolver caches, rebuilds open
        buffers from their in-memory text, and rereads project `.hy` files from
        disk.
        """
        root = root.resolve()
        open_sources = open_sources or {}
        self.resolvers[root] = PythonResolver(root)
        self.indexed_roots.discard(root)

        for doc_uri in list(self.documents):
            if self.root_for_uri(doc_uri).resolve() == root:
                self.documents.pop(doc_uri, None)

        resolver = self.resolver_for_root(root)
        rebuilt: list[DocumentIndex] = []
        for doc_uri, source in open_sources.items():
            if self.root_for_uri(doc_uri).resolve() != root:
                continue
            document = DocumentIndex.build(doc_uri, source, resolver)
            self.documents[doc_uri] = document
            rebuilt.append(document)

        self.ensure_project_index(root)
        return rebuilt

    def symbols_for_completion(self, uri: str, prefix: str) -> list[SymbolInfo]:
        if "." in prefix:
            return self._attribute_completions(uri, prefix)

        seen: set[str] = set()
        out: list[SymbolInfo] = []
        symbol_sources = []
        if uri in self.documents:
            symbol_sources.append(self.documents[uri].symbols.values())
        symbol_sources.extend(document.symbols.values() for doc_uri, document in self.documents.items() if doc_uri != uri)
        symbol_sources.extend([self.core_symbols.values(), self.builtin_symbols.values()])

        for symbols in symbol_sources:
            for symbol in symbols:
                if symbol.name in seen:
                    continue
                if symbol.name.startswith(prefix):
                    seen.add(symbol.name)
                    out.append(symbol)

        # Importable modules are useful in `(import ...)` forms and ordinary code.
        for symbol in self.resolver_for_root(self.root_for_uri(uri)).module_candidates(prefix):
            if symbol.name not in seen:
                seen.add(symbol.name)
                out.append(symbol)

        return sorted(out, key=lambda s: (s.name.startswith("_"), s.name))

    def _attribute_completions(self, uri: str, prefix: str) -> list[SymbolInfo]:
        base_name, _, attr_prefix = prefix.rpartition(".")
        base = self.resolve(uri, base_name)
        if base is None:
            root = self.root_for_uri(uri)
            obj = self.resolver_for_root(root).resolve_qualified(base_name)
            if obj is None:
                return []
            base = symbol_from_object(base_name, obj)
        if base.runtime_object is None:
            return []
        return self.resolver_for_root(self.root_for_uri(uri)).attr_symbols(base_name, base.runtime_object, attr_prefix)

    def resolve(self, uri: str, name: str) -> SymbolInfo | None:
        if "." in name:
            resolved = self._resolve_dotted(uri, name)
            if resolved is not None:
                return resolved
        document = self.documents.get(uri)
        if document and name in document.symbols:
            return document.symbols[name]
        for doc_uri, other in self.documents.items():
            if doc_uri != uri and name in other.symbols:
                return other.symbols[name]
        if name in self.core_symbols:
            return self.core_symbols[name]
        if name in self.builtin_symbols:
            return self.builtin_symbols[name]
        root = self.root_for_uri(uri)
        obj = self.resolver_for_root(root).resolve_qualified(name)
        if obj is not None:
            return symbol_from_object(name, obj)
        return None

    def _resolve_dotted(self, uri: str, name: str) -> SymbolInfo | None:
        base_name, _, rest = name.partition(".")
        base = self.resolve(uri, base_name) if base_name != name else None
        obj = base.runtime_object if base and base.runtime_object is not None else None
        if obj is not None:
            try:
                for part in rest.split("."):
                    obj = getattr(obj, hy.mangle(part))
                return symbol_from_object(name, obj, detail=f"attribute of {base_name}")
            except Exception:
                pass
        root = self.root_for_uri(uri)
        obj = self.resolver_for_root(root).resolve_qualified(name)
        if obj is not None:
            return symbol_from_object(name, obj)
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


def _module_name(model: object) -> str:
    if isinstance(model, Symbol):
        return str(model)
    if isinstance(model, Expression) and model and _symbol_name(model[0]) == ".":
        parts = [str(part) for part in model[1:] if isinstance(part, Symbol)]
        return ".".join(parts)
    return ""


def _is_keyword(model: object, name: str) -> bool:
    return isinstance(model, Keyword) and model.name == name
