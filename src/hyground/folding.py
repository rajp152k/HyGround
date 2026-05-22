"""Folding range support for Hy source."""

from __future__ import annotations

from dataclasses import dataclass


_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {value: key for key, value in _OPEN_TO_CLOSE.items()}


@dataclass(frozen=True)
class FoldRange:
    start_line: int
    start_character: int
    end_line: int
    end_character: int


def folding_ranges(source: str) -> list[FoldRange]:
    """Return foldable multi-line delimiter ranges.

    This intentionally doesn't call Hy's reader: editors ask for folding while
    buffers are incomplete, and a reader error shouldn't disable structural
    folding for the rest of the file.
    """

    ranges: list[FoldRange] = []
    stack: list[tuple[str, int, int]] = []
    line = 0
    character = 0
    in_string = False
    escape = False
    in_comment = False

    for ch in source:
        if in_comment:
            if ch == "\n":
                in_comment = False
                line += 1
                character = 0
            else:
                character += 1
            continue

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            if ch == "\n":
                line += 1
                character = 0
            else:
                character += 1
            continue

        if ch == ";":
            in_comment = True
            character += 1
            continue
        if ch == '"':
            in_string = True
            character += 1
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append((ch, line, character))
        elif ch in _CLOSE_TO_OPEN:
            if stack and stack[-1][0] == _CLOSE_TO_OPEN[ch]:
                _, start_line, start_character = stack.pop()
                if line > start_line:
                    ranges.append(
                        FoldRange(
                            start_line=start_line,
                            start_character=start_character,
                            end_line=line,
                            end_character=character + 1,
                        )
                    )
        if ch == "\n":
            line += 1
            character = 0
        else:
            character += 1

    return sorted(ranges, key=lambda rng: (rng.start_line, rng.start_character, rng.end_line, rng.end_character))
