"""Lightweight completion-context detection for incomplete Hy forms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CompletionContextKind = Literal[
    "default",
    "import-module",
    "import-member",
    "require-module",
    "require-macro",
    "require-reader",
]


@dataclass(frozen=True)
class CompletionContext:
    """The syntactic niche around a completion request."""

    kind: CompletionContextKind = "default"
    module: str | None = None


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int


_DELIMITERS = set("()[]{}")
_REQUIRE_SELECTORS = {":macros", ":readers"}


def completion_context(source: str, line: int, character: int) -> CompletionContext:
    """Return import/require-specific completion context at an LSP position.

    Hy's reader is the source of truth for complete forms, but completion often
    runs while a form is incomplete. This scanner intentionally recognizes only
    enough structure to tell whether the cursor is in an ``import``/``require``
    module slot or inside a member/macro list.
    """

    prefix = _enclosing_form_prefix(source, line, character)
    if prefix is None:
        return CompletionContext()

    tokens = _tokenize(prefix)
    if not tokens:
        return CompletionContext()

    head = tokens[0].text
    if head not in {"import", "require"}:
        return CompletionContext()

    square_index = _innermost_open_square(tokens)
    if head == "import":
        if square_index is not None:
            module = _nearest_module_before(tokens, square_index)
            return CompletionContext("import-member", module)
        if _previous_token(tokens) == ":as":
            return CompletionContext()
        return CompletionContext("import-module")

    if square_index is not None:
        selector = tokens[square_index - 1].text if square_index > 0 else ""
        module = _nearest_module_before(tokens, square_index)
        if selector == ":readers":
            return CompletionContext("require-reader", module)
        return CompletionContext("require-macro", module)

    previous = _previous_token(tokens)
    if previous == ":as":
        return CompletionContext()
    if previous == ":readers":
        return CompletionContext("require-reader", _nearest_module_before(tokens, len(tokens)))
    if previous == ":macros":
        return CompletionContext("require-macro", _nearest_module_before(tokens, len(tokens)))
    return CompletionContext("require-module")


def _enclosing_form_prefix(source: str, line: int, character: int) -> str | None:
    lines = source.splitlines(keepends=True)
    if not 0 <= line < len(lines):
        return None
    offset = sum(len(lines[i]) for i in range(line)) + min(character, len(lines[line]))

    stack: list[int] = []
    in_string = False
    escape = False
    in_comment = False
    i = 0
    while i < offset:
        ch = source[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
            i += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == ";":
            in_comment = True
        elif ch == '"':
            in_string = True
        elif ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            stack.pop()
        i += 1

    if not stack:
        return None
    return source[stack[-1] + 1 : offset]


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ";":
            newline = text.find("\n", i)
            if newline == -1:
                break
            i = newline + 1
            continue
        if ch == '"':
            start = i
            i += 1
            escape = False
            while i < len(text):
                if escape:
                    escape = False
                elif text[i] == "\\":
                    escape = True
                elif text[i] == '"':
                    i += 1
                    break
                i += 1
            tokens.append(_Token(text[start:i], start, i))
            continue
        if ch in _DELIMITERS:
            tokens.append(_Token(ch, i, i + 1))
            i += 1
            continue
        start = i
        while i < len(text):
            ch = text[i]
            if ch.isspace() or ch == ";" or ch in _DELIMITERS or ch == '"':
                break
            i += 1
        tokens.append(_Token(text[start:i], start, i))
    return tokens


def _innermost_open_square(tokens: list[_Token]) -> int | None:
    stack: list[int] = []
    for index, token in enumerate(tokens):
        if token.text == "[":
            stack.append(index)
        elif token.text == "]" and stack:
            stack.pop()
    return stack[-1] if stack else None


def _nearest_module_before(tokens: list[_Token], index: int) -> str | None:
    """Find the most plausible module token before INDEX."""

    depth = 0
    skip_alias = False
    for i in range(index - 1, 0, -1):
        text = tokens[i].text
        if text == "]":
            depth += 1
            continue
        if text == "[" and depth:
            depth -= 1
            continue
        if depth:
            continue
        if text in _DELIMITERS or text == "*" or text in _REQUIRE_SELECTORS:
            continue
        if text == ":as":
            skip_alias = True
            continue
        if skip_alias:
            skip_alias = False
            continue
        if text.startswith(":"):
            continue
        return text
    return None


def _previous_token(tokens: list[_Token]) -> str:
    for token in reversed(tokens):
        if token.text not in _DELIMITERS:
            return token.text
    return ""
