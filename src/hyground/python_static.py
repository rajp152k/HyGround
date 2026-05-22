"""Side-effect-free Python source indexing for workspace modules."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import hy
from pygls import uris

from .model import SourceRange, SymbolInfo, SymbolKind


@dataclass(frozen=True)
class StaticPythonModule:
    module: str
    path: Path
    documentation: str = ""
    symbols: dict[str, SymbolInfo] | None = None


def load_static_python_module(root: Path, module_name: str) -> StaticPythonModule | None:
    """Parse a workspace Python module without importing it."""

    path = find_python_module_path(root, module_name)
    if path is None:
        return None
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None

    symbols: dict[str, SymbolInfo] = {}
    uri = uris.from_fs_path(str(path.resolve()))
    for node in tree.body:
        symbol = _symbol_from_node(uri, module_name, node)
        if symbol is not None:
            symbols[symbol.name] = symbol

    return StaticPythonModule(
        module=module_name,
        path=path,
        documentation=ast.get_docstring(tree) or "",
        symbols=symbols,
    )


def find_python_module_path(root: Path, module_name: str) -> Path | None:
    python_module = _python_qualified_name(module_name)
    module_path = root.joinpath(*python_module.split("."))
    candidates = [
        module_path.with_suffix(".py"),
        module_path.with_suffix(".pyi"),
        module_path / "__init__.py",
        module_path / "__init__.pyi",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def module_symbol(root: Path, visible_name: str, module_name: str) -> SymbolInfo | None:
    module = load_static_python_module(root, module_name)
    if module is None:
        return None
    return module_symbol_from_static_module(module, visible_name)


def module_symbol_from_static_module(module: StaticPythonModule, visible_name: str) -> SymbolInfo:
    return SymbolInfo(
        name=visible_name,
        kind=SymbolKind.MODULE,
        detail=f"Python module {module.module} (static)",
        documentation=module.documentation or f"Python module `{module.module}`.",
        source=_range_for_path(module.path),
        module=module.module,
    )


def member_symbol(root: Path, visible_name: str, module_name: str, member_name: str) -> SymbolInfo | None:
    module = load_static_python_module(root, module_name)
    if module is None:
        return None
    return member_symbol_from_static_module(module, visible_name, member_name)


def member_symbol_from_static_module(
    module: StaticPythonModule,
    visible_name: str,
    member_name: str,
) -> SymbolInfo | None:
    if module.symbols is None:
        return None
    symbol = module.symbols.get(member_name) or module.symbols.get(hy.unmangle(hy.mangle(member_name)))
    if symbol is None:
        return None
    return _with_visible_name(symbol, visible_name)


def member_symbols(root: Path, module_name: str, prefix: str = "", visible_base: str = "") -> list[SymbolInfo]:
    module = load_static_python_module(root, module_name)
    if module is None:
        return []
    return member_symbols_from_static_module(module, prefix, visible_base)


def member_symbols_from_static_module(
    module: StaticPythonModule,
    prefix: str = "",
    visible_base: str = "",
) -> list[SymbolInfo]:
    if module.symbols is None:
        return []
    out: list[SymbolInfo] = []
    for symbol in module.symbols.values():
        if not symbol.name.startswith(prefix):
            continue
        visible = f"{visible_base}.{symbol.name}" if visible_base else symbol.name
        out.append(_with_visible_name(symbol, visible))
    return sorted(out, key=lambda symbol: symbol.name)


def _symbol_from_node(uri: str, module_name: str, node: ast.AST) -> SymbolInfo | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = hy.unmangle(node.name)
        return SymbolInfo(
            name=name,
            kind=SymbolKind.LOCAL_FUNCTION,
            detail=f"Python function {module_name}.{node.name} (static)",
            signature=f"({name}{_format_arguments(node.args)}){_format_returns(node)}",
            documentation=ast.get_docstring(node) or "",
            source=_range_for_node(uri, node),
            module=module_name,
        )

    if isinstance(node, ast.ClassDef):
        name = hy.unmangle(node.name)
        bases = ", ".join(_unparse(base) for base in node.bases)
        signature = f"class {name}({bases})" if bases else f"class {name}"
        return SymbolInfo(
            name=name,
            kind=SymbolKind.LOCAL_CLASS,
            detail=f"Python class {module_name}.{node.name} (static)",
            signature=signature,
            documentation=ast.get_docstring(node) or "",
            source=_range_for_node(uri, node),
            module=module_name,
        )

    if isinstance(node, ast.Assign):
        for target in node.targets:
            symbol = _symbol_from_assignment_target(uri, module_name, target)
            if symbol is not None:
                return symbol

    if isinstance(node, ast.AnnAssign):
        return _symbol_from_assignment_target(uri, module_name, node.target, node.annotation)

    return None


def _symbol_from_assignment_target(
    uri: str,
    module_name: str,
    target: ast.AST,
    annotation: ast.AST | None = None,
) -> SymbolInfo | None:
    if not isinstance(target, ast.Name):
        return None
    name = hy.unmangle(target.id)
    detail = f"Python variable {module_name}.{target.id} (static)"
    if annotation is not None:
        detail = f"{detail}: {_unparse(annotation)}"
    return SymbolInfo(
        name=name,
        kind=SymbolKind.LOCAL_VARIABLE,
        detail=detail,
        source=_range_for_node(uri, target),
        module=module_name,
    )


def _format_arguments(args: ast.arguments) -> str:
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts = [_format_arg(arg, default) for arg, default in zip(positional, defaults)]
    if args.posonlyargs:
        parts.insert(len(args.posonlyargs), "/")
    if args.vararg is not None:
        parts.append("*" + _format_arg(args.vararg, None))
    elif args.kwonlyargs:
        parts.append("*")
    parts.extend(
        _format_arg(arg, default)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults)
    )
    if args.kwarg is not None:
        parts.append("**" + _format_arg(args.kwarg, None))
    return "" if not parts else " " + ", ".join(parts)


def _format_arg(arg: ast.arg, default: ast.AST | None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text = f"{text}: {_unparse(arg.annotation)}"
    if default is not None:
        text = f"{text}={_unparse(default)}"
    return text


def _format_returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f" -> {_unparse(node.returns)}" if node.returns is not None else ""


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse is best-effort for broken stubs.
        return "..."


def _range_for_node(uri: str, node: ast.AST) -> SourceRange:
    start_line = max(getattr(node, "lineno", 1) - 1, 0)
    start_character = max(getattr(node, "col_offset", 0), 0)
    end_line = max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, 0)
    end_character = max(getattr(node, "end_col_offset", start_character + 1), 0)
    return SourceRange(uri, start_line, start_character, end_line, end_character)


def _range_for_path(path: Path) -> SourceRange:
    uri = uris.from_fs_path(str(path.resolve()))
    return SourceRange(uri, start_line=0, start_character=0, end_line=0, end_character=0)


def _with_visible_name(symbol: SymbolInfo, visible_name: str) -> SymbolInfo:
    return SymbolInfo(
        name=visible_name,
        kind=symbol.kind,
        detail=symbol.detail,
        documentation=symbol.documentation,
        signature=symbol.signature,
        source=symbol.source,
        runtime_object=symbol.runtime_object,
        module=symbol.module,
    )


def _python_qualified_name(name: str) -> str:
    return ".".join(hy.mangle(part) if part else part for part in name.split("."))
