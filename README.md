# HyGround

HyGround is a principled language server for [Hy](https://hylang.org/) with first-class Python interop.

It is a clean rewrite inspired by Hyuga's useful ideas, but built around an explicit local symbol model: each server instance owns its workspace/document indexes, and completion, hover, definition, symbols, and references all read from the same model.

## Current features

- stdio LSP server via `hyground`
- Hy parse diagnostics and basic compile diagnostics
- completion for:
  - Hy core forms (`if`, `lfor`, `setv`, `defn`, ...)
  - Python builtins
  - importable Python modules
  - imported Python objects and module attributes
  - local `defn`, `defmacro`, `defclass`, `setv`
  - project-local Hy definitions across files
- hover docs for:
  - Hy core forms via explicit Hy docs provider
  - Python runtime objects via `inspect`
  - local Hy docstrings
- go-to-definition for:
  - local Hy definitions
  - project-local Hy definitions
  - imported Python modules/classes/functions when `inspect` can locate source
- document symbols for local definitions
- simple references across indexed Hy files
- signature help for indexed Hy/Python callables
- explicit workspace reindex command: `hyground.reindexWorkspace`

## Install / run

During early development, run directly from GitHub:

```bash
uvx --from git+https://github.com/rajp152k/HyGround hyground
```

From a local checkout:

```bash
uv run hyground --version
uv run hyground
```

The eventual stable path is:

```bash
uvx hyground
```

or:

```bash
pipx install hyground
```

## Emacs / lsp-mode

Development checkout command:

```elisp
(require 'lsp-mode)

(defcustom tbm/hyground-command
  '("uv" "--directory" "/home/tbm/source/vcops/hylang/HyGround-Dev/HyGround" "run" "hyground")
  "Command used to start HyGround."
  :type '(repeat string))

(add-to-list 'lsp-language-id-configuration '(hy-mode . "hy"))

(lsp-register-client
 (make-lsp-client
  :new-connection (lsp-stdio-connection (lambda () tbm/hyground-command))
  :major-modes '(hy-mode)
  :activation-fn (lsp-activate-on "hy")
  :priority 20
  :server-id 'hyground))

(add-hook 'hy-mode-hook #'lsp-deferred)
```

Once published or installed globally, change the command to:

```elisp
(setq tbm/hyground-command '("uvx" "hyground"))
```

If jump/hover data feels stale after adding files, changing imports, or installing
packages into `.venv`, force a fresh index:

```elisp
(lsp-send-execute-command "hyground.reindexWorkspace" (vector (lsp--buffer-uri)))
```

This rebuilds the current workspace's Hy index and clears Python import/source
resolution caches.

## Architecture

HyGround separates the server into local, testable parts:

- `model.py`: shared `SymbolInfo` / source ranges.
- `core_docs.py`: explicit docs for Hy compiler forms that don't have useful Python `__doc__`.
- `resolver.py`: workspace-scoped Python import/object/source resolution.
- `index.py`: document/workspace indexing from Hy forms.
- `server.py`: thin pygls LSP adapter.

This keeps Hy/Python facts local to a workspace index instead of hiding them in mutable process-global registries.

## Development

```bash
uv sync
uv run pytest -q
uv run hyground --version
```

Smoke file: `examples/smoke.hy`.
