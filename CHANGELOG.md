# Changelog

All notable changes to HyGround will be documented in this file.

This project follows pragmatic semantic versioning while the public CLI/LSP surface stabilizes. Before 1.0, minor versions may still refine behavior, but releases should call out compatibility-impacting changes clearly.

## Unreleased

### Added

- Stdio LSP server with completion, hover, definition, document symbols, workspace symbols, references, signature help, rename, diagnostics, semantic tokens, folding ranges, and explicit reindex command.
- Workspace-owned `WorkspaceIndex` / `DocumentIndex` model with no process-global symbol table.
- Hy core form, Python builtin, local Hy definition, project Hy definition, Python runtime object, typeshed stub, and static Python source resolution.
- Position-scoped callable parameters for completion, hover, definition, and semantic tokens.
- Indexing for `setv`/`setx` assignment and destructuring targets.
- Import/require-aware completion and indexing, including aliases, star imports, required macros, and reader macros.
- Static Python fallback across workspace and common uv/venv site-packages layouts, with `.pyi` signature preference, implementation-docstring enrichment, re-export resolution, and nested package attribute resolution.
- PyPI trusted-publishing workflow.
- Static module-aware Hy import resolution for project `.hy` files.
- Workspace configuration from `[tool.hyground]` in `pyproject.toml`.
- Safety switch for workspace-local runtime imports plus side-effect-free static Python fallback.
- Watched-file reindexing for relevant Hy/Python/config changes.
- Python 3.10-3.14 CI and focused Hy compatibility CI for Hy 1.2.0 and latest Hy.
- Editor setup documentation for Emacs, Neovim, Helix, and generic stdio LSP clients.

### Changed

- Rename is conservative and document-local for symbols defined in the current Hy document, avoiding broad workspace false positives.
- Indexing retains complete forms that precede a Hy reader error.
- Diagnostics include stable `hy-reader` / `hy-compiler` codes and best-effort ranges.

### Known limitations

- Full lexical scope/reference graph is not implemented yet.
- Parser recovery for unknown reader macros and incomplete forms is limited.
- Static Python fallback does not perform type inference or arbitrary attribute-flow analysis.
- Hy core documentation still contains a manual stopgap table.

## 0.1.0

Initial packaged development release.
