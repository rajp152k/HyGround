"""Workspace and document indexes for HyGround."""

from __future__ import annotations

import builtins
import inspect
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import hy
from hy.compiler import hy_compile
from hy.models import Expression, Keyword, List as HyList, String, Symbol
from lsprotocol import types as lsp
from pygls import uris

from .config import HyGroundConfig, load_config
from .core_docs import CORE_DOCS
from .model import SourceRange, SymbolInfo, SymbolKind
from .resolver import PythonResolver, find_workspace_root, iter_hy_files, symbol_from_object


@dataclass
class ParseDiagnostic:
    message: str
    line: int = 0
    character: int = 0
    end_line: int = 0
    end_character: int = 1
    code: str = "hyground"

    def to_lsp(self) -> lsp.Diagnostic:
        return lsp.Diagnostic(
            range=lsp.Range(
                start=lsp.Position(line=self.line, character=self.character),
                end=lsp.Position(line=self.end_line, character=self.end_character),
            ),
            message=self.message,
            source="hyground",
            code=self.code,
            severity=lsp.DiagnosticSeverity.Error,
        )


@dataclass
class DocumentIndex:
    uri: str
    source: str
    module: str = ""
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)
    imports: list["_HyImportBinding"] = field(default_factory=list)
    diagnostics: list[ParseDiagnostic] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        uri: str,
        source: str,
        resolver: PythonResolver | None = None,
        compile_forms: bool = True,
        module: str = "",
    ) -> "DocumentIndex":
        index = cls(uri=uri, source=source, module=module)
        forms = index._read_forms(source)

        for form in forms:
            index._walk_form(form, resolver)

        if compile_forms:
            index._record_compile_diagnostics(forms, source, resolver)
        return index

    def _read_forms(self, source: str) -> list[object]:
        forms: list[object] = []
        try:
            for form in hy.read_many(source, filename=self.uri):
                forms.append(form)
        except Exception as exc:  # Hy parse exceptions don't share a stable base type.
            self.diagnostics.append(_diagnostic_from_exception(exc, source, code="hy-reader"))
        return forms

    def _record_compile_diagnostics(
        self,
        forms: list[object],
        source: str,
        resolver: PythonResolver | None,
    ) -> None:
        context = resolver.import_context() if resolver is not None else nullcontext()
        with context:
            for form in forms:
                try:
                    hy_compile(form, "__main__", filename=self.uri, source=source)
                except Exception as exc:
                    diagnostic = _diagnostic_from_exception(exc, source, code="hy-compiler")
                    if not any(
                        d.message == diagnostic.message
                        and d.line == diagnostic.line
                        and d.character == diagnostic.character
                        and d.code == diagnostic.code
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
        if head in {"defn", "defmacro", "defreader"}:
            self._record_callable(form, head)
        elif head == "defclass":
            self._record_class(form)
        elif head == "setv":
            self._record_setv(form)
        elif head == "import" and resolver is not None:
            self._record_import(form, resolver)
        elif head == "require" and resolver is not None:
            self._record_require(form, resolver)

    def _record_callable(self, form: Expression, head: str) -> None:
        if len(form) < 3 or not isinstance(form[1], Symbol):
            return
        raw_name = str(form[1])
        name = f"#{raw_name}" if head == "defreader" else raw_name
        params = _format_model(form[2])
        body = list(form[3:])
        doc = _leading_docstring(body)
        kind = _callable_symbol_kind(head)
        self.symbols[name] = SymbolInfo(
            name=name,
            kind=kind,
            detail=f"local {head}",
            signature=_callable_signature(head, raw_name, name, params),
            documentation=doc,
            source=SourceRange.from_hy_model(self.uri, form[1]),
            module=self.module,
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
            module=self.module,
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
                    module=self.module,
                )

    def _record_import(self, form: Expression, resolver: PythonResolver) -> None:
        for spec in _parse_import_specs(list(form[1:])):
            self._record_hy_import_binding(spec)
            if spec.star:
                self._record_star_import(spec.module, resolver)
                continue

            if spec.members:
                for member in spec.members:
                    self._add_symbol(resolver.object_symbol(member.visible, f"{spec.module}.{member.original}"))
                continue

            if spec.alias:
                self._add_symbol(resolver.module_symbol(spec.alias, spec.module))
                continue

            # `(import os.path)` makes the top-level package useful for dotted completion.
            visible = spec.module.split(".")[0] if "." in spec.module else spec.module
            self._add_symbol(resolver.module_symbol(visible, visible))
            self._add_symbol(resolver.module_symbol(spec.module, spec.module))

    def _record_hy_import_binding(self, spec: "_ImportSpec") -> None:
        if spec.star:
            self.imports.append(
                _HyImportBinding(
                    visible="",
                    module=spec.module,
                    star=True,
                    source=SourceRange.from_hy_model(self.uri, spec.model),
                )
            )
            return

        if spec.members:
            for member in spec.members:
                self.imports.append(
                    _HyImportBinding(
                        visible=member.visible,
                        module=spec.module,
                        member=member.original,
                        source=SourceRange.from_hy_model(self.uri, member.model),
                    )
                )
            return

        if spec.alias:
            self.imports.append(
                _HyImportBinding(
                    visible=spec.alias,
                    module=spec.module,
                    source=SourceRange.from_hy_model(self.uri, spec.alias_model or spec.model),
                )
            )
            return

        top_level = spec.module.split(".")[0]
        self.imports.append(
            _HyImportBinding(
                visible=top_level,
                module=top_level,
                source=SourceRange.from_hy_model(self.uri, spec.model),
            )
        )
        if spec.module != top_level:
            self.imports.append(
                _HyImportBinding(
                    visible=spec.module,
                    module=spec.module,
                    source=SourceRange.from_hy_model(self.uri, spec.model),
                )
            )

    def _record_star_import(self, module_name: str, resolver: PythonResolver) -> None:
        module = resolver.import_module(module_name)
        if module is None:
            return
        for py_name in getattr(module, "__all__", None) or dir(module):
            if py_name.startswith("_"):
                continue
            hy_name = hy.unmangle(py_name)
            try:
                self._add_symbol(
                    symbol_from_object(
                        hy_name,
                        getattr(module, py_name),
                        detail=f"imported from {module_name}",
                    )
                )
            except Exception:
                continue

    def _record_require(self, form: Expression, resolver: PythonResolver) -> None:
        for spec in _parse_require_specs(list(form[1:])):
            if spec.prefix_all:
                prefix = spec.alias or spec.module
                for symbol in resolver.macro_candidates(spec.module, dotted_prefix=prefix):
                    self._add_symbol(symbol)

            if spec.star:
                for symbol in resolver.macro_candidates(spec.module):
                    self._add_symbol(symbol)

            for member in spec.members:
                symbol = resolver.macro_symbol(member.visible, spec.module, member.original)
                self._add_symbol(symbol or self._provisional_required_macro(spec.module, member))

            if spec.reader_star:
                for symbol in resolver.reader_macro_candidates(spec.module, include_hash=True):
                    self._add_symbol(symbol)

            for reader in spec.readers:
                visible = f"#{reader.original}"
                symbol = resolver.reader_macro_symbol(visible, spec.module, reader.original)
                self._add_symbol(symbol or self._provisional_required_macro(spec.module, reader, reader=True))

    def _provisional_required_macro(
        self,
        module_name: str,
        member: "_ImportMember",
        reader: bool = False,
    ) -> SymbolInfo:
        visible = f"#{member.original}" if reader else member.visible
        kind = SymbolKind.READER_MACRO if reader else SymbolKind.LOCAL_MACRO
        noun = "Reader macro" if reader else "Macro"
        return SymbolInfo(
            name=visible,
            kind=kind,
            detail=f"required {'reader macro' if reader else 'macro'} from {module_name}",
            documentation=f"{noun} required from `{module_name}`.",
            source=SourceRange.from_hy_model(self.uri, member.model),
            module=module_name,
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
        self.configs: dict[Path, HyGroundConfig] = {}
        self.indexed_roots: set[Path] = set()

    def update_document(self, uri: str, source: str) -> DocumentIndex:
        root = self.root_for_uri(uri)
        resolver = self.resolver_for_root(root)
        document = DocumentIndex.build(
            uri,
            source,
            resolver,
            module=self.module_for_uri(uri, root),
        )
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
            self.resolvers[root] = PythonResolver(root, self.config_for_root(root))
        return self.resolvers[root]

    def config_for_root(self, root: Path) -> HyGroundConfig:
        root = root.resolve()
        if root not in self.configs:
            self.configs[root] = load_config(root)
        return self.configs[root]

    def module_for_uri(self, uri: str, root: Path) -> str:
        try:
            path = Path(uris.to_fs_path(uri))
        except Exception:
            return ""
        return _module_name_for_path(root, path)

    def ensure_project_index(self, root: Path) -> None:
        root = root.resolve()
        if root in self.indexed_roots:
            return
        resolver = self.resolver_for_root(root)
        config = self.config_for_root(root)
        for path in iter_hy_files(root, limit=config.index_limit, exclude_dirs=config.exclude_dirs):
            uri = uris.from_fs_path(str(path.resolve()))
            if uri in self.documents:
                continue
            try:
                self.documents[uri] = DocumentIndex.build(
                    uri,
                    path.read_text(),
                    resolver,
                    compile_forms=False,
                    module=_module_name_for_path(root, path),
                )
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
        self.configs.pop(root, None)
        self.resolvers[root] = PythonResolver(root, self.config_for_root(root))
        self.indexed_roots.discard(root)

        for doc_uri in list(self.documents):
            if self.root_for_uri(doc_uri).resolve() == root:
                self.documents.pop(doc_uri, None)

        resolver = self.resolver_for_root(root)
        rebuilt: list[DocumentIndex] = []
        for doc_uri, source in open_sources.items():
            if self.root_for_uri(doc_uri).resolve() != root:
                continue
            document = DocumentIndex.build(
                doc_uri,
                source,
                resolver,
                module=self.module_for_uri(doc_uri, root),
            )
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
            document = self.documents[uri]
            symbol_sources.append(document.symbols.values())
            symbol_sources.append(self._hy_import_completion_symbols(uri, document, prefix))
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

    def _hy_import_completion_symbols(
        self,
        uri: str,
        document: DocumentIndex,
        prefix: str,
    ) -> list[SymbolInfo]:
        symbols: list[SymbolInfo] = []
        for binding in document.imports:
            if binding.star:
                module_document = self._document_for_module(uri, binding.module)
                if module_document is None:
                    continue
                symbols.extend(
                    _clone_symbol(symbol, symbol.name)
                    for symbol in module_document.symbols.values()
                    if symbol.name.startswith(prefix)
                )
                continue

            if binding.member:
                symbol = self._symbol_from_hy_module(uri, binding.module, binding.member, binding.visible)
                if symbol is not None and symbol.name.startswith(prefix):
                    symbols.append(symbol)
                continue

            if binding.visible.startswith(prefix):
                module_symbol = self._hy_module_symbol(uri, binding.module, binding.visible)
                if module_symbol is not None:
                    symbols.append(module_symbol)
        return symbols

    def _attribute_completions(self, uri: str, prefix: str) -> list[SymbolInfo]:
        base_name, _, attr_prefix = prefix.rpartition(".")
        base = self.resolve(uri, base_name)
        if base is not None and base.kind == SymbolKind.MODULE and base.module:
            hy_symbols = self._hy_module_attribute_symbols(uri, base.module, base_name, attr_prefix)
            if hy_symbols:
                return hy_symbols
            static_symbols = self.resolver_for_root(self.root_for_uri(uri)).static_member_symbols(
                base.module,
                attr_prefix,
                visible_base=base_name,
            )
            if static_symbols:
                return static_symbols
        if base is None:
            root = self.root_for_uri(uri)
            obj = self.resolver_for_root(root).resolve_qualified(base_name)
            if obj is None:
                return []
            base = symbol_from_object(base_name, obj)
        if base.runtime_object is None:
            return []
        return self.resolver_for_root(self.root_for_uri(uri)).attr_symbols(base_name, base.runtime_object, attr_prefix)

    def _hy_module_attribute_symbols(
        self,
        uri: str,
        module: str,
        visible_base: str,
        attr_prefix: str,
    ) -> list[SymbolInfo]:
        document = self._document_for_module(uri, module)
        if document is None:
            return []
        return sorted(
            (
                _clone_symbol(symbol, f"{visible_base}.{symbol.name}")
                for symbol in document.symbols.values()
                if symbol.name.startswith(attr_prefix)
            ),
            key=lambda symbol: symbol.name,
        )

    def _document_for_module(self, uri: str, module: str) -> DocumentIndex | None:
        root = self.root_for_uri(uri).resolve()
        for doc_uri, document in self.documents.items():
            if document.module != module:
                continue
            if self.root_for_uri(doc_uri).resolve() == root:
                return document
        return None

    def _hy_module_symbol(self, uri: str, module: str, visible: str) -> SymbolInfo | None:
        document = self._document_for_module(uri, module)
        if document is None:
            return None
        return SymbolInfo(
            name=visible,
            kind=SymbolKind.MODULE,
            detail=f"Hy module {module}",
            documentation=f"Hy module `{module}`.",
            source=SourceRange(uri=document.uri, start_line=0, start_character=0, end_line=0, end_character=0),
            module=module,
        )

    def _symbol_from_hy_module(
        self,
        uri: str,
        module: str,
        member: str,
        visible: str,
    ) -> SymbolInfo | None:
        document = self._document_for_module(uri, module)
        if document is None:
            return None
        symbol = document.symbols.get(member)
        if symbol is None:
            return None
        return _clone_symbol(symbol, visible)

    def _resolve_hy_import(self, uri: str, document: DocumentIndex, name: str) -> SymbolInfo | None:
        for binding in document.imports:
            if binding.star:
                if "." not in name:
                    symbol = self._symbol_from_hy_module(uri, binding.module, name, name)
                    if symbol is not None:
                        return symbol
                continue

            if binding.member:
                if name == binding.visible:
                    symbol = self._symbol_from_hy_module(uri, binding.module, binding.member, binding.visible)
                    if symbol is not None:
                        return symbol
                continue

            if name == binding.visible:
                module_symbol = self._hy_module_symbol(uri, binding.module, binding.visible)
                if module_symbol is not None:
                    return module_symbol
                continue

            prefix = f"{binding.visible}."
            if name.startswith(prefix):
                rest = name[len(prefix) :]
                symbol = self._resolve_hy_module_path(uri, binding.module, rest, name)
                if symbol is not None:
                    return symbol
        return None

    def _resolve_hy_module_path(
        self,
        uri: str,
        module: str,
        rest: str,
        visible: str,
    ) -> SymbolInfo | None:
        parts = [part for part in rest.split(".") if part]
        for split in range(0, len(parts) + 1):
            module_suffix = ".".join(parts[:split])
            candidate_module = f"{module}.{module_suffix}" if module_suffix else module
            member = ".".join(parts[split:])
            if not member:
                module_symbol = self._hy_module_symbol(uri, candidate_module, visible)
                if module_symbol is not None:
                    return module_symbol
                continue
            symbol = self._symbol_from_hy_module(uri, candidate_module, member, visible)
            if symbol is not None:
                return symbol
        return None

    def resolve(self, uri: str, name: str) -> SymbolInfo | None:
        if "." in name:
            resolved = self._resolve_dotted(uri, name)
            if resolved is not None:
                return resolved
        document = self.documents.get(uri)
        if document and name in document.symbols:
            return document.symbols[name]
        if document:
            imported = self._resolve_hy_import(uri, document, name)
            if imported is not None:
                return imported
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
        document = self.documents.get(uri)
        if document is not None:
            imported = self._resolve_hy_import(uri, document, name)
            if imported is not None:
                return imported

        base_name, _, rest = name.partition(".")
        base = self.resolve(uri, base_name) if base_name != name else None
        if base is not None and base.kind == SymbolKind.MODULE and base.module:
            static_symbol = self.resolver_for_root(self.root_for_uri(uri)).static_member_symbol(
                name,
                base.module,
                rest,
            )
            if static_symbol is not None:
                return static_symbol

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


@dataclass(frozen=True)
class _HyImportBinding:
    visible: str
    module: str
    member: str = ""
    star: bool = False
    source: SourceRange | None = None


@dataclass(frozen=True)
class _ImportMember:
    original: str
    visible: str
    model: object


@dataclass(frozen=True)
class _ImportSpec:
    module: str
    model: object
    alias: str = ""
    alias_model: object | None = None
    members: tuple[_ImportMember, ...] = ()
    star: bool = False


@dataclass(frozen=True)
class _RequireSpec:
    module: str
    alias: str = ""
    prefix_all: bool = False
    members: tuple[_ImportMember, ...] = ()
    star: bool = False
    readers: tuple[_ImportMember, ...] = ()
    reader_star: bool = False


def _parse_import_specs(items: list[object]) -> list[_ImportSpec]:
    specs: list[_ImportSpec] = []
    i = 0
    while i < len(items):
        model = items[i]
        module_name = _module_name(model)
        if not module_name:
            i += 1
            continue

        alias = ""
        alias_model: object | None = None
        members: tuple[_ImportMember, ...] = ()
        star = False
        i += 1

        if i + 1 < len(items) and _is_keyword(items[i], "as") and isinstance(items[i + 1], Symbol):
            alias = str(items[i + 1])
            alias_model = items[i + 1]
            i += 2
        elif i < len(items) and isinstance(items[i], HyList):
            members, star = _parse_member_selector(items[i])
            i += 1
        elif i < len(items) and _is_star(items[i]):
            star = True
            i += 1

        specs.append(
            _ImportSpec(
                module=module_name,
                model=model,
                alias=alias,
                alias_model=alias_model,
                members=members,
                star=star,
            )
        )
    return specs


def _parse_require_specs(items: list[object]) -> list[_RequireSpec]:
    specs: list[_RequireSpec] = []
    i = 0
    while i < len(items):
        module_name = _module_name(items[i])
        if not module_name:
            i += 1
            continue

        i += 1
        alias = ""
        members: list[_ImportMember] = []
        readers: list[_ImportMember] = []
        star = False
        reader_star = False
        saw_regular_selector = False
        saw_reader_selector = False

        while i < len(items):
            item = items[i]
            if i + 1 < len(items) and _is_keyword(item, "as") and isinstance(items[i + 1], Symbol):
                alias = str(items[i + 1])
                i += 2
                continue
            if i + 1 < len(items) and _is_keyword(item, "macros"):
                selected, selected_star = _parse_selector_model(items[i + 1])
                members.extend(selected)
                star = star or selected_star
                saw_regular_selector = True
                i += 2
                continue
            if i + 1 < len(items) and _is_keyword(item, "readers"):
                selected, selected_star = _parse_selector_model(items[i + 1])
                readers.extend(selected)
                reader_star = reader_star or selected_star
                saw_reader_selector = True
                i += 2
                continue
            if isinstance(item, HyList):
                selected, selected_star = _parse_member_selector(item)
                members.extend(selected)
                star = star or selected_star
                saw_regular_selector = True
                i += 1
                continue
            if _is_star(item):
                star = True
                saw_regular_selector = True
                i += 1
                continue
            if isinstance(item, Keyword):
                i += 1
                continue
            break

        prefix_all = not saw_regular_selector and not saw_reader_selector
        specs.append(
            _RequireSpec(
                module=module_name,
                alias=alias,
                prefix_all=prefix_all,
                members=tuple(members),
                star=star,
                readers=tuple(readers),
                reader_star=reader_star,
            )
        )
    return specs


def _parse_selector_model(model: object) -> tuple[list[_ImportMember], bool]:
    if isinstance(model, HyList):
        members, star = _parse_member_selector(model)
        return list(members), star
    if _is_star(model):
        return [], True
    if isinstance(model, Symbol):
        name = str(model)
        return [_ImportMember(name, name, model)], False
    return [], False


def _parse_member_selector(values: HyList) -> tuple[tuple[_ImportMember, ...], bool]:
    raw = list(values)
    if len(raw) == 1 and _is_star(raw[0]):
        return (), True

    members: list[_ImportMember] = []
    i = 0
    while i < len(raw):
        value = raw[i]
        if not isinstance(value, Symbol):
            i += 1
            continue
        original = str(value)
        visible = original
        if i + 2 < len(raw) and _is_keyword(raw[i + 1], "as") and isinstance(raw[i + 2], Symbol):
            visible = str(raw[i + 2])
            i += 3
        else:
            i += 1
        members.append(_ImportMember(original=original, visible=visible, model=value))
    return tuple(members), False


def _is_star(model: object) -> bool:
    return isinstance(model, Symbol) and str(model) == "*"


def _clone_symbol(symbol: SymbolInfo, name: str) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=symbol.kind,
        detail=symbol.detail,
        documentation=symbol.documentation,
        signature=symbol.signature,
        source=symbol.source,
        runtime_object=symbol.runtime_object,
        module=symbol.module,
    )


def _module_name_for_path(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path(path.name)
    without_suffix = relative.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return hy.unmangle(path.stem)
    return ".".join(hy.unmangle(part) for part in parts)


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


def _diagnostic_from_exception(exc: Exception, source: str, code: str) -> ParseDiagnostic:
    line = max(int(getattr(exc, "lineno", 1) or 1) - 1, 0)
    character = max(int(getattr(exc, "offset", 1) or 1) - 1, 0)
    end_line_attr = getattr(exc, "end_lineno", None)
    end_offset_attr = getattr(exc, "end_offset", None)
    end_line = max(int(end_line_attr or line + 1) - 1, 0)
    end_character = max(int(end_offset_attr or character + 2) - 1, 0)

    lines = source.splitlines() or [source]
    line = min(line, len(lines) - 1)
    end_line = min(max(end_line, line), len(lines) - 1)
    character = min(character, len(lines[line]))
    end_character = min(end_character, len(lines[end_line]))
    if end_line == line and end_character <= character:
        end_character = min(character + 1, len(lines[line]))
        if end_character <= character:
            end_character = character + 1

    message = getattr(exc, "msg", None) or str(exc)
    return ParseDiagnostic(
        message=message,
        line=line,
        character=character,
        end_line=end_line,
        end_character=end_character,
        code=code,
    )


def _symbol_name(model: object) -> str:
    return str(model) if isinstance(model, Symbol) else ""


def _format_model(model: object) -> str:
    try:
        return hy.repr(model).lstrip("'")
    except Exception:
        return str(model)


def _callable_symbol_kind(head: str) -> SymbolKind:
    if head == "defmacro":
        return SymbolKind.LOCAL_MACRO
    if head == "defreader":
        return SymbolKind.READER_MACRO
    return SymbolKind.LOCAL_FUNCTION


def _callable_signature(head: str, raw_name: str, visible_name: str, params: str) -> str:
    if head == "defreader":
        return f"(defreader {raw_name} {params})"
    return f"({visible_name} {params})"


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
