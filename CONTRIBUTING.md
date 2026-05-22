# Contributing to HyGround

HyGround is a Hy language server focused on practical editor ergonomics, Python interop, and a principled symbol model.

## Development setup

```bash
uv sync
uv run hyground --version
uv run pytest -q
```

Run the full local validation set before opening a PR:

```bash
uv run pytest -q
uv run python -m py_compile src/hyground/*.py tests/*.py
uv build
```

## Branch and PR workflow

- Work on a topic branch.
- Keep changes small enough to review.
- Add or update tests for behavior changes.
- Update README or notes when user-facing behavior changes.
- Prefer conservative editor behavior over ambitious but unsafe behavior, especially for rename/refactor features.

## Compatibility policy

HyGround currently supports:

- Python 3.10 through 3.14.
- Hy 1.2.0 and newer.

CI covers the Python grid and a focused Hy compatibility grid for the declared minimum Hy and latest Hy. If a change relies on a newer Hy API, either add a compatibility fallback or intentionally raise the minimum Hy version in `pyproject.toml`, README, and release notes.

## Design principles

- Keep workspace state owned by the server instance; avoid process-global registries.
- Prefer one shared `SymbolInfo` model feeding completion, hover, definition, symbols, references, signature help, and rename.
- Runtime importing is useful but potentially unsafe. Honor `allow-workspace-imports` and prefer static fallbacks when safety is requested.
- Lisp syntax and Python runtime semantics both matter. Hy macros, reader macros, and Python objects need distinct handling.
- If an operation can edit user code, be conservative until symbol identity is proven.

## Release checklist

Release publishing uses PyPI Trusted Publishing via `.github/workflows/publish.yml`. Before the first release, configure the PyPI project trusted publisher to match this repository, workflow `publish.yml`, and environment `pypi`. Do not commit PyPI tokens.

1. Ensure `main`/`master` is green in CI.
2. Update `CHANGELOG.md` with the release version and date.
3. Bump `version` in `pyproject.toml` and `src/hyground/__init__.py` together.
4. Run:
   ```bash
   uv sync
   uv run pytest -q
   uv run python -m py_compile src/hyground/*.py tests/*.py
   uv build
   uv run hyground --version
   ```
5. Smoke-test an editor client when possible.
6. Create and push a signed tag, e.g. `v0.2.0`.
7. Create a GitHub release with highlights and known limitations. Publishing the release triggers the `Publish` workflow.
8. Confirm the workflow uploaded both sdist and wheel to PyPI.
9. Smoke-test `uvx hyground --version` from a clean environment after PyPI propagation.
