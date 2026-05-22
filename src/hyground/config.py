"""Workspace configuration for HyGround."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - covered on Python 3.10 in CI
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_INDEX_LIMIT = 500
DEFAULT_EXCLUDE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
)


@dataclass(frozen=True)
class HyGroundConfig:
    """Settings loaded from ``[tool.hyground]`` in ``pyproject.toml``."""

    index_limit: int = DEFAULT_INDEX_LIMIT
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS
    allow_workspace_imports: bool = True


def load_config(root: Path) -> HyGroundConfig:
    """Load HyGround configuration for ROOT.

    Unknown keys are ignored so newer configuration files remain compatible with
    older servers. Invalid values fall back to safe defaults instead of failing
    server startup.
    """

    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return HyGroundConfig()

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return HyGroundConfig()

    table = data.get("tool", {}).get("hyground", {})
    if not isinstance(table, dict):
        return HyGroundConfig()

    return HyGroundConfig(
        index_limit=_positive_int(_get(table, "index-limit", "index_limit"), DEFAULT_INDEX_LIMIT),
        exclude_dirs=_exclude_dirs(_get(table, "exclude-dirs", "exclude_dirs")),
        allow_workspace_imports=_bool(
            _get(table, "allow-workspace-imports", "allow_workspace_imports"),
            True,
        ),
    )


def _get(table: dict[str, Any], kebab: str, snake: str) -> Any:
    return table[kebab] if kebab in table else table.get(snake)


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def _exclude_dirs(value: Any) -> tuple[str, ...]:
    extras: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                extras.append(item)
    return tuple(dict.fromkeys([*DEFAULT_EXCLUDE_DIRS, *extras]))


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default
