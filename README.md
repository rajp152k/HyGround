# HyGround

HyGround is a principled language server for [Hy](https://hylang.org/) with first-class Python interop.

The project is intentionally early. The current MVP supports:

- stdio LSP server via `hyground`
- Hy parse diagnostics
- completion for Hy core forms, Python builtins, and local definitions
- hover docs for Hy core forms, Python builtins, and local definitions
- go-to-definition for local definitions

## Try locally

```bash
uv run hyground --version
uv run hyground
```

## Editor command during development

For Emacs/lsp-mode, point the server command at the checkout:

```elisp
(setq tbm/hyground-command
      '("uv" "--directory" "/home/tbm/source/vcops/hylang/HyGround-Dev/HyGround" "run" "hyground"))
```

The intended end-user install path is eventually:

```bash
uvx hyground
```

or:

```bash
pipx install hyground
```

## Design

See `../notes` in the development workspace for design notes and Hyuga lessons.
