"""Word extraction utilities for Hy source."""

from __future__ import annotations

import re

# Hy symbols include many operator characters. Keep this conservative enough to
# avoid swallowing delimiters but broad enough for normal Hy identifiers.
WORD_RE = re.compile(r"[+\-.?!><$/*%=@^&|~:\\\w]+")


def line_word_prefix(line: str, character: int) -> str:
    """Return the symbol prefix ending at CHARACTER on LINE."""
    character = max(0, min(character, len(line)))
    for match in WORD_RE.finditer(line):
        if match.start() <= character <= match.end():
            return line[match.start() : character]
    return ""


def line_word_at(line: str, character: int) -> str:
    """Return the full symbol under CHARACTER on LINE."""
    character = max(0, min(character, len(line)))
    for match in WORD_RE.finditer(line):
        if match.start() <= character <= match.end():
            return match.group(0)
    return ""


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
