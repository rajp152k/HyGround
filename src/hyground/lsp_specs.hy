"""HyGround's declarative LSP registration DSL.

This module is intentionally written in Hy: the server surface is a small
language of features and commands, and Hy is a good fit for representing that
language as data.
"""

(import lsprotocol [types :as lsp])

(defn feature [method handler [options None]]
  "Describe a pygls feature handler without registering it yet."
  {"kind" "feature"
   "method" method
   "handler" handler
   "options" options})

(defn command [name handler]
  "Describe a pygls command handler without registering it yet."
  {"kind" "command"
   "name" name
   "handler" handler})

(setv REINDEX_COMMAND "hyground.reindexWorkspace")

(setv REGISTRY
  [(feature lsp.TEXT_DOCUMENT_DID_OPEN "did_open")
   (feature lsp.TEXT_DOCUMENT_DID_CHANGE "did_change")
   (feature lsp.TEXT_DOCUMENT_DID_CLOSE "did_close")
   (feature lsp.WORKSPACE_DID_CHANGE_WATCHED_FILES "did_change_watched_files")
   (feature
     lsp.TEXT_DOCUMENT_COMPLETION
     "completion"
     (lsp.CompletionOptions :trigger-characters ["." " " "-" "_" ":" "[" "#"]))
   (feature lsp.TEXT_DOCUMENT_HOVER "hover")
   (feature lsp.TEXT_DOCUMENT_DEFINITION "definition")
   (feature
     lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL
     "semantic_tokens_full"
     (lsp.SemanticTokensLegend
       :token-types ["namespace" "class" "function" "macro" "variable" "keyword" "string" "number" "operator" "comment"]
       :token-modifiers []))
   (feature lsp.TEXT_DOCUMENT_FOLDING_RANGE "folding_range")
   (feature lsp.TEXT_DOCUMENT_DOCUMENT_SYMBOL "document_symbol")
   (feature
     lsp.WORKSPACE_SYMBOL
     "workspace_symbol"
     (lsp.WorkspaceSymbolOptions :resolve-provider False))
   (feature lsp.TEXT_DOCUMENT_REFERENCES "references")
   (feature lsp.TEXT_DOCUMENT_PREPARE_RENAME "prepare_rename")
   (feature
     lsp.TEXT_DOCUMENT_RENAME
     "rename"
     (lsp.RenameOptions :prepare-provider True))
   (feature
     lsp.TEXT_DOCUMENT_SIGNATURE_HELP
     "signature_help"
     (lsp.SignatureHelpOptions :trigger-characters [" " "("]))
   (command REINDEX_COMMAND "reindex_workspace")])
