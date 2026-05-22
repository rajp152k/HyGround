"""Semantic token support for Hy source."""

from __future__ import annotations

from dataclasses import dataclass

from .model import SymbolInfo, SymbolKind
from .word import WORD_RE

SEMANTIC_TOKEN_TYPES = [
    "namespace",
    "class",
    "function",
    "macro",
    "variable",
    "keyword",
    "string",
    "number",
    "operator",
    "comment",
]
SEMANTIC_TOKEN_MODIFIERS: list[str] = []

_TOKEN_INDEX = {name: index for index, name in enumerate(SEMANTIC_TOKEN_TYPES)}
_OPERATOR_CHARS = set("+-*/%=<>!&|^~@")


@dataclass(frozen=True)
class SemanticToken:
    line: int
    start: int
    length: int
    token_type: str


def semantic_tokens(source: str, resolve_symbol) -> list[SemanticToken]:
    """Return best-effort semantic tokens for SOURCE.

    ``resolve_symbol`` is a callable accepting a token string and returning a
    ``SymbolInfo | None``. This keeps scanning independent from workspace state.
    """

    tokens: list[SemanticToken] = []
    for line_no, line in enumerate(source.splitlines()):
        code, comment_start = _split_comment(line)
        tokens.extend(_line_tokens(code, line_no, resolve_symbol))
        if comment_start is not None:
            tokens.append(
                SemanticToken(
                    line=line_no,
                    start=comment_start,
                    length=len(line) - comment_start,
                    token_type="comment",
                )
            )
    return sorted(tokens, key=lambda token: (token.line, token.start))


def encode_semantic_tokens(tokens: list[SemanticToken]) -> list[int]:
    """Encode tokens in LSP's relative five-integer representation."""

    data: list[int] = []
    previous_line = 0
    previous_start = 0
    for token in tokens:
        delta_line = token.line - previous_line
        delta_start = token.start if delta_line else token.start - previous_start
        data.extend([delta_line, delta_start, token.length, _TOKEN_INDEX[token.token_type], 0])
        previous_line = token.line
        previous_start = token.start
    return data


def _line_tokens(line: str, line_no: int, resolve_symbol) -> list[SemanticToken]:
    tokens: list[SemanticToken] = []
    spans_to_skip: list[tuple[int, int]] = []
    for start, end in _string_spans(line):
        spans_to_skip.append((start, end))
        tokens.append(SemanticToken(line_no, start, end - start, "string"))

    for match in WORD_RE.finditer(line):
        start, end = match.span()
        if any(skip_start <= start < skip_end for skip_start, skip_end in spans_to_skip):
            continue
        text = match.group(0)
        token_type = _classify_token(text, resolve_symbol(text))
        if token_type is None:
            continue
        tokens.append(SemanticToken(line_no, start, end - start, token_type))
    return tokens


def _classify_token(text: str, symbol: SymbolInfo | None) -> str | None:
    if symbol is not None:
        return _token_type_for_symbol(symbol)
    if text.startswith(":"):
        return "keyword"
    if _is_number(text):
        return "number"
    if any(ch in _OPERATOR_CHARS for ch in text) and not any(ch.isalnum() for ch in text):
        return "operator"
    return None


def _token_type_for_symbol(symbol: SymbolInfo) -> str:
    return {
        SymbolKind.CORE_FORM: "keyword",
        SymbolKind.PYTHON_BUILTIN: "function",
        SymbolKind.LOCAL_FUNCTION: "function",
        SymbolKind.LOCAL_MACRO: "macro",
        SymbolKind.READER_MACRO: "macro",
        SymbolKind.LOCAL_CLASS: "class",
        SymbolKind.LOCAL_VARIABLE: "variable",
        SymbolKind.MODULE: "namespace",
    }.get(symbol.kind, "variable")


def _split_comment(line: str) -> tuple[str, int | None]:
    in_string = False
    escape = False
    for index, ch in enumerate(line):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ";":
            return line[:index], index
    return line, None


def _string_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    in_string = False
    escape = False
    start = 0
    for index, ch in enumerate(line):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                spans.append((start, index + 1))
                in_string = False
            continue
        if ch == '"':
            start = index
            in_string = True
    if in_string:
        spans.append((start, len(line)))
    return spans


def _is_number(text: str) -> bool:
    try:
        float(text.replace("_", ""))
    except ValueError:
        return False
    return any(ch.isdigit() for ch in text)
