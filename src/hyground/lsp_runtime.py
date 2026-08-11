"""Runtime adapter from HyGround's Hy LSP DSL to pygls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import hy  # noqa: F401 - installs the import hook for .hy modules.

from . import lsp_specs

REINDEX_COMMAND = str(lsp_specs.REINDEX_COMMAND)


@dataclass(frozen=True)
class LspSpec:
    """A normalized LSP registration spec emitted by ``lsp_specs.hy``."""

    kind: str
    handler: str
    method: str = ""
    command: str = ""
    options: Any = None


def registry() -> tuple[LspSpec, ...]:
    """Return the Hy-authored LSP registry as immutable Python objects."""

    return tuple(_normalize_spec(spec) for spec in lsp_specs.REGISTRY)


def register_lsp_specs(server: Any, handlers: Mapping[str, Any]) -> None:
    """Register Hy-authored LSP specs against a pygls server."""

    for spec in registry():
        handler = handlers[spec.handler]
        if spec.kind == "feature":
            decorator = server.feature(spec.method, spec.options) if spec.options is not None else server.feature(spec.method)
            decorator(handler)
        elif spec.kind == "command":
            server.command(spec.command)(handler)
        else:  # pragma: no cover - guarded by tests and the tiny DSL surface.
            raise ValueError(f"Unknown LSP spec kind: {spec.kind!r}")


def _normalize_spec(spec: Mapping[str, Any]) -> LspSpec:
    kind = str(spec["kind"])
    if kind == "feature":
        return LspSpec(
            kind=kind,
            method=str(spec["method"]),
            handler=str(spec["handler"]),
            options=spec.get("options"),
        )
    if kind == "command":
        return LspSpec(
            kind=kind,
            command=str(spec["name"]),
            handler=str(spec["handler"]),
        )
    raise ValueError(f"Unknown LSP spec kind: {kind!r}")
