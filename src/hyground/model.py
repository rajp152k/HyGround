"""Core data model shared by HyGround LSP features."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from lsprotocol import types as lsp


class SymbolKind(str, Enum):
    CORE_FORM = "core-form"
    PYTHON_BUILTIN = "python-builtin"
    LOCAL_FUNCTION = "local-function"
    LOCAL_MACRO = "local-macro"
    READER_MACRO = "reader-macro"
    LOCAL_CLASS = "local-class"
    LOCAL_VARIABLE = "local-variable"
    PARAMETER = "parameter"
    MODULE = "module"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceRange:
    """A source range using LSP coordinates: zero-based, end-exclusive."""

    uri: str
    start_line: int
    start_character: int
    end_line: int
    end_character: int

    @classmethod
    def from_hy_model(cls, uri: str, model: Any) -> "SourceRange":
        start_line = max(getattr(model, "start_line", 1) - 1, 0)
        start_character = max(getattr(model, "start_column", 1) - 1, 0)
        end_line = max(getattr(model, "end_line", getattr(model, "start_line", 1)) - 1, 0)
        # Hy model end_column is effectively 1-based inclusive; LSP wants exclusive.
        end_character = max(getattr(model, "end_column", getattr(model, "start_column", 1)), 0)
        return cls(uri, start_line, start_character, end_line, end_character)

    def to_lsp_range(self) -> lsp.Range:
        return lsp.Range(
            start=lsp.Position(line=self.start_line, character=self.start_character),
            end=lsp.Position(line=self.end_line, character=self.end_character),
        )

    def to_location(self) -> lsp.Location:
        return lsp.Location(uri=self.uri, range=self.to_lsp_range())


@dataclass(frozen=True)
class SymbolInfo:
    name: str
    kind: SymbolKind
    detail: str = ""
    documentation: str = ""
    signature: str = ""
    source: SourceRange | None = None
    runtime_object: Any | None = None
    module: str = ""

    def hover_text(self) -> str:
        header = self.name
        if self.signature:
            header = f"{self.name} {self.signature}"
        if self.detail:
            header = f"{header}\n[{self.detail}]"
        parts = [header]
        if self.documentation:
            parts.append(self.documentation)
        return "\n\n".join(parts)
