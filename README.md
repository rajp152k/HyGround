# HyGround

HyGround is a language server for [Hy](https://hylang.org/). It provides LSP features for Hy source files and resolves Python objects from the active workspace, including project-local modules and virtual environments.

The server runs over stdio and keeps its index in the server instance. It does not use process-global symbol registries.

## Status

Alpha. HyGround is usable for local development, but the indexing and resolution model is still evolving.

## Installation

Run from GitHub:

```bash
uvx --from git+https://github.com/rajp152k/HyGround hyground
```

Run from a local checkout:

```bash
uv run hyground
```

After a PyPI release, the intended install commands are:

```bash
uvx hyground
pipx install hyground
```

## LSP capabilities

| Capability | Status | Notes |
| --- | --- | --- |
| `textDocument/completion` | supported | Hy forms, Python builtins, importable modules, imported members, dotted attributes, local/project Hy symbols |
| `textDocument/hover` | supported | Python docs from `inspect`, local Hy docstrings, provisional Hy form docs |
| `textDocument/definition` | supported | Local/project Hy definitions, Python source via `inspect`, typeshed fallback for builtins/C extensions |
| `textDocument/documentSymbol` | supported | Definitions in the current Hy document |
| `workspace/symbol` | supported | Indexed Hy definitions across the workspace |
| `textDocument/references` | partial | Name-based references across indexed Hy files |
| `textDocument/signatureHelp` | partial | Available when a signature is known from HyGround's symbol model or `inspect` |
| `textDocument/rename` | partial | Local Hy symbols only; implemented as name-based workspace edits |
| `textDocument/publishDiagnostics` | partial | Hy reader diagnostics and basic compile diagnostics |
| `workspace/executeCommand` | supported | `hyground.reindexWorkspace` |

## Python resolution

HyGround resolves Python objects using the current workspace root and common virtual environment locations:

- project root
- `.venv/lib/python*/site-packages`
- `venv/lib/python*/site-packages`

Supported examples:

```hy
(import pathlib [Path])
(Path ".")

(import toolz)
toolz.pipe

(import math)
(math.sqrt 4)
```

Pure Python modules normally jump to their `.py` source. Builtin and C extension modules, such as `math` and `cmath`, do not expose Python implementation files; HyGround falls back to bundled typeshed `.pyi` stubs for these cases.

Hy names are mapped to Python names when resolving imports and attributes:

```hy
(import local-lib)
(local-lib.make-thing 1) ; resolves local_lib.make_thing
```

## Reindexing

HyGround indexes open buffers and project `.hy` files. If files, imports, or virtual environment packages change while the server is running, request a fresh index:

```json
{
  "command": "hyground.reindexWorkspace",
  "arguments": ["file:///path/to/current-buffer.hy"]
}
```

In Emacs/lsp-mode:

```elisp
(lsp-send-execute-command "hyground.reindexWorkspace" (vector (lsp--buffer-uri)))
```

The command clears Python resolver caches, rebuilds open Hy buffers from editor text, rereads project `.hy` files from disk, and republishes diagnostics.

## Emacs / lsp-mode

Development checkout configuration:

```elisp
(require 'lsp-mode)

(defcustom hyground-command
  '("uv" "--directory" "/path/to/HyGround" "run" "hyground")
  "Command used to start HyGround."
  :type '(repeat string))

(add-to-list 'lsp-language-id-configuration '(hy-mode . "hy"))

(lsp-register-client
 (make-lsp-client
  :new-connection (lsp-stdio-connection (lambda () hyground-command))
  :major-modes '(hy-mode)
  :activation-fn (lsp-activate-on "hy")
  :priority 20
  :server-id 'hyground))

(add-hook 'hy-mode-hook #'lsp-deferred)
```

For an installed package, use:

```elisp
(setq hyground-command '("uvx" "hyground"))
```

## Architecture

- `server.py`: pygls feature registration and LSP request handlers.
- `index.py`: Hy document/workspace indexing.
- `resolver.py`: workspace-scoped Python import, object, source, and stub resolution.
- `model.py`: shared symbol and source range model.
- `word.py`: token, range, occurrence, and call-site utilities.
- `core_docs.py`: temporary Hy form documentation provider.

The same `SymbolInfo` model feeds completion, hover, definition, symbols, references, signature help, and rename.

## Development

```bash
uv sync
uv run pytest -q
uv run python -m py_compile src/hyground/*.py tests/*.py
uv build
uv run hyground --version
```

The test suite contains:

- unit tests for Hy indexing, Python resolution, reindexing, and word utilities
- end-to-end stdio LSP tests that launch `hyground` and speak JSON-RPC
- CI coverage for Python 3.10, 3.11, 3.12, 3.13, and 3.14

Smoke file:

```bash
examples/smoke.hy
```

## Known limitations

- Hy project symbol lookup is currently name-based, not module-aware.
- References and rename are lexical/name-based and can produce false positives.
- Diagnostics use coarse ranges for many Hy reader/compiler errors.
- Python object resolution imports modules. A static resolver is needed for packages that are unsafe or expensive to import.
- Typeshed jumps target interface stubs, not C implementation source.
- Hy core form documentation is currently explicit data in `core_docs.py`. This is a stopgap. The production path should derive these docs from Hy's own documentation/source metadata or an upstream-supported machine-readable source, so HyGround does not maintain a parallel manual table.

## Roadmap

Work required to move from the current implementation to production-grade tooling:

1. Replace manual Hy core-form documentation with generated or upstream-provided documentation data.
2. Make Hy symbol resolution module-aware, including imports, requires, aliases, and shadowing.
3. Replace name-based references/rename with scoped symbol references.
4. Improve diagnostics with precise ranges, stable error categories, and recovery for incomplete forms.
5. Add context-aware completions for import/require forms, keywords, attributes, and macros.
6. Add static Python source/stub resolution paths that do not import user modules by default.
7. Add configuration for workspace roots, indexing limits, excluded paths, and resolver behavior.
8. Publish versioned releases to PyPI and document editor integrations beyond Emacs.
