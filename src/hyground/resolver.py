"""Python and Hy runtime resolution for HyGround.

Resolution is deliberately scoped by workspace root. We temporarily prepend the
project root and its local virtualenv site-packages to sys.path while resolving,
rather than mutating a process-global search path permanently.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import pkgutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

import hy
from pygls import uris
from typeshed_client import get_stub_file

from .model import SourceRange, SymbolInfo, SymbolKind

_IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}


class PythonResolver:
    """Resolve Python modules/objects for one workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.search_paths = _search_paths(root)
        self._module_cache: dict[str, ModuleType | None] = {}
        self._object_cache: dict[str, object | None] = {}
        self._top_level_modules: list[str] | None = None

    @contextmanager
    def import_context(self) -> Iterator[None]:
        old_path = list(sys.path)
        prefixes = [str(path) for path in self.search_paths]
        sys.path[:] = [*prefixes, *[p for p in sys.path if p not in prefixes]]
        try:
            yield
        finally:
            sys.path[:] = old_path

    def import_module(self, module_name: str) -> ModuleType | None:
        python_module_name = _python_qualified_name(module_name)
        cache_key = python_module_name
        if cache_key in self._module_cache:
            return self._module_cache[cache_key]
        try:
            importlib.invalidate_caches()
            with self.import_context():
                module = importlib.import_module(python_module_name)
        except Exception:
            module = None
        self._module_cache[cache_key] = module
        return module

    def resolve_qualified(self, qualified_name: str) -> object | None:
        """Resolve ``module.attr.attr`` by importing the longest module prefix."""
        if qualified_name in self._object_cache:
            return self._object_cache[qualified_name]

        parts = qualified_name.split(".")
        result: object | None = None
        for split in range(len(parts), 0, -1):
            module_name = ".".join(parts[:split])
            module = self.import_module(module_name)
            if module is None:
                continue
            result = module
            try:
                for attr in parts[split:]:
                    result = getattr(result, hy.mangle(attr))
            except Exception:
                result = None
            break

        self._object_cache[qualified_name] = result
        return result

    def module_symbol(self, visible_name: str, module_name: str) -> SymbolInfo | None:
        module = self.import_module(module_name)
        if module is None:
            return None
        return symbol_from_object(visible_name, module, detail=f"Python module {module_name}")

    def object_symbol(self, visible_name: str, qualified_name: str) -> SymbolInfo | None:
        obj = self.resolve_qualified(qualified_name)
        if obj is None:
            return None
        return symbol_from_object(visible_name, obj, detail=f"Python object {qualified_name}")

    def module_candidates(self, prefix: str) -> list[SymbolInfo]:
        """Return importable modules visible from this workspace."""
        if "." in prefix:
            return self._dotted_module_candidates(prefix)
        return [
            SymbolInfo(
                name=name,
                kind=SymbolKind.MODULE,
                detail="importable Python module",
                documentation=f"Importable Python module `{hy.mangle(name)}`.",
                module=name,
            )
            for name in self.top_level_modules()
            if name.startswith(prefix)
        ]

    def _dotted_module_candidates(self, prefix: str) -> list[SymbolInfo]:
        base_name, _, attr_prefix = prefix.rpartition(".")
        base = self.resolve_qualified(base_name)
        if base is None:
            return []
        candidates: dict[str, SymbolInfo] = {}

        module_path = getattr(base, "__path__", None)
        if module_path is not None:
            with self.import_context():
                for module in pkgutil.iter_modules(module_path, prefix=f"{hy.mangle(base_name)}."):
                    name = hy.unmangle(module.name)
                    if name.startswith(prefix):
                        candidates[name] = SymbolInfo(
                            name=name,
                            kind=SymbolKind.MODULE,
                            detail="importable Python module",
                            documentation=f"Importable Python module `{module.name}`.",
                            module=name,
                        )

        for symbol in self.attr_symbols(base_name, base, attr_prefix):
            if symbol.kind == SymbolKind.MODULE:
                candidates.setdefault(symbol.name, symbol)
        return sorted(candidates.values(), key=lambda symbol: symbol.name)

    def member_candidates(self, module_name: str, prefix: str) -> list[SymbolInfo]:
        """Return importable members of MODULE_NAME labelled by member name."""
        module = self.import_module(module_name)
        if module is None:
            return []
        names = getattr(module, "__all__", None) or dir(module)
        symbols: list[SymbolInfo] = []
        for py_name in names:
            if py_name.startswith("_") and not prefix.startswith("_"):
                continue
            hy_name = hy.unmangle(py_name)
            if not hy_name.startswith(prefix) and not py_name.startswith(prefix):
                continue
            try:
                obj = getattr(module, py_name)
            except Exception:
                continue
            symbols.append(symbol_from_object(hy_name, obj, detail=f"member of {module_name}"))
        return sorted(symbols, key=lambda symbol: symbol.name)

    def macro_candidates(self, module_name: str, prefix: str = "", dotted_prefix: str = "") -> list[SymbolInfo]:
        """Return regular macros exported by MODULE_NAME."""
        module = self.import_module(module_name)
        if module is None:
            return []
        symbols: list[SymbolInfo] = []
        for py_name, obj in _macro_entries(module):
            hy_name = hy.unmangle(py_name)
            visible = f"{dotted_prefix}.{hy_name}" if dotted_prefix else hy_name
            if not visible.startswith(prefix) and not hy_name.startswith(prefix):
                continue
            symbols.append(_symbol_from_macro(visible, obj, f"required macro from {module_name}"))
        return sorted(symbols, key=lambda symbol: symbol.name)

    def macro_symbol(self, visible_name: str, module_name: str, macro_name: str) -> SymbolInfo | None:
        module = self.import_module(module_name)
        if module is None:
            return None
        macros = getattr(module, "_hy_macros", {})
        obj = macros.get(hy.mangle(macro_name))
        if obj is None:
            return None
        return _symbol_from_macro(visible_name, obj, f"required macro from {module_name}")

    def reader_macro_candidates(self, module_name: str, prefix: str = "", include_hash: bool = False) -> list[SymbolInfo]:
        """Return reader macros exported by MODULE_NAME."""
        module = self.import_module(module_name)
        if module is None:
            return []
        symbols: list[SymbolInfo] = []
        for name, obj in _reader_macro_entries(module):
            visible = f"#{name}" if include_hash else name
            if not visible.startswith(prefix) and not name.startswith(prefix):
                continue
            symbols.append(_symbol_from_macro(visible, obj, f"reader macro from {module_name}", reader=True))
        return sorted(symbols, key=lambda symbol: symbol.name)

    def reader_macro_symbol(self, visible_name: str, module_name: str, reader_name: str) -> SymbolInfo | None:
        module = self.import_module(module_name)
        if module is None:
            return None
        readers = getattr(module, "_hy_reader_macros", {})
        obj = readers.get(reader_name)
        if obj is None:
            return None
        return _symbol_from_macro(visible_name, obj, f"reader macro from {module_name}", reader=True)

    def top_level_modules(self) -> list[str]:
        if self._top_level_modules is None:
            seen: set[str] = set()
            with self.import_context():
                for module in pkgutil.iter_modules():
                    if module.name.startswith("_"):
                        continue
                    seen.add(hy.unmangle(module.name))
            self._top_level_modules = sorted(seen)
        return self._top_level_modules

    def attr_symbols(self, base_name: str, base_obj: object, attr_prefix: str = "") -> list[SymbolInfo]:
        symbols: list[SymbolInfo] = []
        for py_name in dir(base_obj):
            if py_name.startswith("_") and not attr_prefix.startswith("_"):
                continue
            hy_name = hy.unmangle(py_name)
            if not hy_name.startswith(attr_prefix) and not py_name.startswith(attr_prefix):
                continue
            try:
                obj = getattr(base_obj, py_name)
            except Exception:
                continue
            visible = f"{base_name}.{hy_name}"
            symbols.append(symbol_from_object(visible, obj, detail=f"attribute of {base_name}"))
        return sorted(symbols, key=lambda s: s.name)


def symbol_from_object(name: str, obj: object, detail: str = "Python object") -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=_kind_for_object(obj),
        detail=detail,
        signature=_signature(obj),
        documentation=inspect.getdoc(obj) or "",
        source=_source_for_object(obj),
        runtime_object=obj,
        module=getattr(obj, "__name__", "") if inspect.ismodule(obj) else getattr(obj, "__module__", ""),
    )


def _symbol_from_macro(name: str, obj: object, detail: str, reader: bool = False) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=SymbolKind.READER_MACRO if reader else SymbolKind.LOCAL_MACRO,
        detail=detail,
        signature=_signature(obj),
        documentation=inspect.getdoc(obj) or "",
        source=_source_for_object(obj),
        runtime_object=obj,
        module=getattr(obj, "__module__", ""),
    )


def _macro_entries(module: ModuleType) -> list[tuple[str, object]]:
    macros = getattr(module, "_hy_macros", {})
    if not isinstance(macros, dict):
        return []
    exports = getattr(module, "_hy_export_macros", None)
    names = exports if exports is not None else [name for name in macros if not name.startswith("_")]
    out: list[tuple[str, object]] = []
    for name in names:
        if not isinstance(name, str):
            continue
        obj = macros.get(hy.mangle(hy.unmangle(name))) or macros.get(name)
        if obj is not None:
            out.append((hy.mangle(hy.unmangle(name)), obj))
    return out


def _reader_macro_entries(module: ModuleType) -> list[tuple[str, object]]:
    readers = getattr(module, "_hy_reader_macros", {})
    if not isinstance(readers, dict):
        return []
    return sorted(
        (name, obj)
        for name, obj in readers.items()
        if isinstance(name, str) and not name.startswith("_")
    )


def find_workspace_root(path: Path) -> Path:
    """Find a practical Python/Hy project root for PATH."""
    start = path if path.is_dir() else path.parent
    for current in (start, *start.parents):
        if any((current / marker).exists() for marker in ("pyproject.toml", "uv.lock", ".git")):
            return current
    return start


def iter_hy_files(root: Path, limit: int = 500) -> Iterator[Path]:
    count = 0
    for path in root.rglob("*.hy"):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        yield path
        count += 1
        if count >= limit:
            return


def _search_paths(root: Path) -> list[Path]:
    paths = [root]
    for venv_name in (".venv", "venv"):
        lib = root / venv_name / "lib"
        if not lib.exists():
            continue
        for site_packages in lib.glob("python*/site-packages"):
            if site_packages.exists():
                paths.append(site_packages)
    return paths


def _python_qualified_name(name: str) -> str:
    return ".".join(hy.mangle(part) if part else part for part in name.split("."))


def _kind_for_object(obj: object) -> SymbolKind:
    if inspect.ismodule(obj):
        return SymbolKind.MODULE
    if inspect.isclass(obj):
        return SymbolKind.LOCAL_CLASS
    if inspect.isfunction(obj) or inspect.ismethod(obj) or inspect.isbuiltin(obj):
        return SymbolKind.LOCAL_FUNCTION
    return SymbolKind.UNKNOWN


def _signature(obj: object) -> str:
    try:
        return str(inspect.signature(obj)) if callable(obj) else ""
    except (TypeError, ValueError):
        return ""


def _source_for_object(obj: object) -> SourceRange | None:
    direct = _direct_source_for_object(obj)
    if direct is not None:
        return direct

    if inspect.ismodule(obj):
        return _source_for_module(obj)

    module_name = getattr(obj, "__module__", None)
    object_name = getattr(obj, "__name__", None)
    if isinstance(module_name, str):
        if isinstance(object_name, str):
            stub = _source_for_stub_object(module_name, object_name)
            if stub is not None:
                return stub
        module = sys.modules.get(module_name)
        if module is None:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                module = None
        if module is not None:
            return _source_for_module(module)
    return None


def _direct_source_for_object(obj: object) -> SourceRange | None:
    try:
        file_name = inspect.getsourcefile(obj) or inspect.getfile(obj)
    except (TypeError, OSError):
        return None
    if not file_name:
        return None
    path = Path(file_name)
    if not path.exists() or path.suffix not in {".py", ".pyi", ".hy"}:
        return None
    try:
        _, line = inspect.getsourcelines(obj)
    except (OSError, TypeError):
        line = 1
    return _range_for_path(path, line)


def _source_for_module(module: ModuleType) -> SourceRange | None:
    for candidate in (getattr(module, "__file__", None), getattr(getattr(module, "__spec__", None), "origin", None)):
        if not candidate or candidate in {"built-in", "frozen", "namespace"}:
            continue
        path = Path(candidate)
        if path.exists() and path.suffix in {".py", ".pyi", ".hy"}:
            return _range_for_path(path, 1)

    name = getattr(module, "__name__", None)
    if isinstance(name, str):
        stub = _find_stub_for_module(name)
        if stub is not None:
            return _range_for_path(stub, 1)
    return None


def _find_stub_for_module(module_name: str) -> Path | None:
    module_path = Path(*module_name.split("."))
    for base in map(Path, sys.path):
        candidates = [
            base / module_path.with_suffix(".pyi"),
            base / module_path / "__init__.pyi",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    try:
        bundled = get_stub_file(module_name)
    except Exception:
        bundled = None
    if bundled is not None:
        return Path(bundled)
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        spec = None
    if spec and spec.origin:
        origin = Path(spec.origin)
        if origin.with_suffix(".pyi").exists():
            return origin.with_suffix(".pyi")
    return None


def _source_for_stub_object(module_name: str, object_name: str) -> SourceRange | None:
    stub = _find_stub_for_module(module_name)
    if stub is None:
        return None
    line = _find_top_level_stub_name(stub, object_name)
    if line is None:
        return _range_for_path(stub, 1)
    return _range_for_path(stub, line)


def _find_top_level_stub_name(path: Path, name: str) -> int | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    candidates = {name, hy.mangle(name)}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in candidates:
            return node.lineno
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in candidates:
                    return node.lineno
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in candidates:
            return node.lineno
    return None


def _range_for_path(path: Path, line: int) -> SourceRange:
    uri = uris.from_fs_path(str(path.resolve()))
    line0 = max(line - 1, 0)
    return SourceRange(uri=uri, start_line=line0, start_character=0, end_line=line0, end_character=0)
