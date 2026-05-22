"""Word extraction utilities for Hy source."""

from __future__ import annotations

import re

# Hy symbols include many operator characters. Keep this conservative enough to
# avoid swallowing delimiters but broad enough for normal Hy identifiers.
WORD_RE = re.compile(r"[#\+\-.?!><$/*%=@^&|~:\\\w]+")


def line_word_prefix(line: str, character: int) -> str:
    """Return the symbol prefix ending at CHARACTER on LINE."""
    character = max(0, min(character, len(line)))
    for match in WORD_RE.finditer(line):
        if match.start() <= character <= match.end():
            return line[match.start() : character]
    return ""


def line_word_at(line: str, character: int) -> str:
    """Return the full symbol under CHARACTER on LINE."""
    found = line_word_range_at(line, character)
    if found is None:
        return ""
    start, end = found
    return line[start:end]


def line_word_range_at(line: str, character: int) -> tuple[int, int] | None:
    """Return the ``(start, end)`` range of the symbol under CHARACTER."""
    character = max(0, min(character, len(line)))
    for match in WORD_RE.finditer(line):
        if match.start() <= character <= match.end():
            return match.start(), match.end()
    return None


def word_prefix(source: str, line: int, character: int) -> str:
    lines = source.splitlines()
    if not 0 <= line < len(lines):
        return ""
    return line_word_prefix(lines[line], character)


def word_at(source: str, line: int, character: int) -> str:
    lines = source.splitlines()
    if not 0 <= line < len(lines):
        return ""
    return line_word_at(lines[line], character)


def word_range_at(source: str, line: int, character: int) -> tuple[int, int] | None:
    lines = source.splitlines()
    if not 0 <= line < len(lines):
        return None
    return line_word_range_at(lines[line], character)


def occurrences(source: str, name: str) -> list[tuple[int, int, int]]:
    """Return ``(line, start, end)`` occurrences of NAME as a Hy word."""
    if not name:
        return []
    out: list[tuple[int, int, int]] = []
    for line_no, line in enumerate(source.splitlines()):
        for match in WORD_RE.finditer(line):
            if match.group(0) == name:
                out.append((line_no, match.start(), match.end()))
    return out


def enclosing_call(source: str, line: int, character: int) -> tuple[str, int] | None:
    """Return ``(callee, active_parameter)`` for the nearest open call.

    This is intentionally lightweight. Hy's reader remains the source of truth
    for indexing; signature help just needs a useful local guess while the user
    is editing incomplete forms.
    """
    lines = source.splitlines(keepends=True)
    if not 0 <= line < len(lines):
        return None
    offset = sum(len(lines[i]) for i in range(line)) + min(character, len(lines[line]))
    prefix = source[:offset]
    depth = 0
    start = -1
    for i in range(len(prefix) - 1, -1, -1):
        ch = prefix[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start < 0:
        return None
    after = prefix[start + 1 :]
    match = WORD_RE.search(after)
    if not match:
        return None
    callee = match.group(0)
    args = after[match.end() :]
    active = _active_parameter(args)
    return callee, active


def _active_parameter(args: str) -> int:
    depth = 0
    active = 0
    in_token = False
    in_string = False
    escape = False
    for ch in args:
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
            in_token = True
        elif ch in "([{" :
            depth += 1
            in_token = True
        elif ch in ")]}":
            depth = max(depth - 1, 0)
            in_token = True
        elif depth == 0 and ch.isspace():
            if in_token:
                active += 1
                in_token = False
        elif not ch.isspace():
            in_token = True
    return active
