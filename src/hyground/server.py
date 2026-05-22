"""pygls server wiring for HyGround."""

from __future__ import annotations

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from . import __version__
from .index import DocumentIndex, WorkspaceIndex
from .model import SymbolInfo, SymbolKind
from .word import word_at, word_prefix


class HyGroundServer(LanguageServer):
    """LanguageServer with explicit, instance-owned HyGround state."""

    def __init__(self) -> None:
        super().__init__("hyground", __version__)
        self.index = WorkspaceIndex()


def make_server() -> HyGroundServer:
    server = HyGroundServer()
    _register_features(server)
    return server


def _register_features(server: HyGroundServer) -> None:
    @server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
    def did_open(params: lsp.DidOpenTextDocumentParams) -> None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        _index_and_publish(server, uri, document.source)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(params: lsp.DidChangeTextDocumentParams) -> None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        _index_and_publish(server, uri, document.source)

    @server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(params: lsp.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        server.index.remove_document(uri)
        server.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )

    @server.feature(
        lsp.TEXT_DOCUMENT_COMPLETION,
        lsp.CompletionOptions(trigger_characters=[".", " ", "-", "_", ":"]),
    )
    def completion(params: lsp.CompletionParams) -> lsp.CompletionList:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        prefix = word_prefix(document.source, params.position.line, params.position.character)
        items = [_completion_item(symbol) for symbol in server.index.symbols_for_completion(uri, prefix)]
        return lsp.CompletionList(is_incomplete=False, items=items)

    @server.feature(lsp.TEXT_DOCUMENT_HOVER)
    def hover(params: lsp.HoverParams) -> lsp.Hover | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        if not name:
            return None
        symbol = server.index.resolve(uri, name)
        if symbol is None:
            return None
        return lsp.Hover(
            contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=_hover_markdown(symbol))
        )

    @server.feature(lsp.TEXT_DOCUMENT_DEFINITION)
    def definition(params: lsp.DefinitionParams) -> list[lsp.Location] | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        if not name:
            return None
        symbol = server.index.resolve(uri, name)
        if symbol is None or symbol.source is None:
            return None
        return [symbol.source.to_location()]


def _index_and_publish(server: HyGroundServer, uri: str, source: str) -> DocumentIndex:
    document = server.index.update_document(uri, source)
    server.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(
            uri=uri,
            diagnostics=[diagnostic.to_lsp() for diagnostic in document.diagnostics],
        )
    )
    return document


def _completion_item(symbol: SymbolInfo) -> lsp.CompletionItem:
    return lsp.CompletionItem(
        label=symbol.name,
        kind=_completion_kind(symbol.kind),
        detail=symbol.detail or symbol.kind.value,
        documentation=_hover_markdown(symbol) if symbol.documentation or symbol.signature else None,
        insert_text=symbol.name,
        filter_text=symbol.name,
        sort_text=symbol.name,
        data={"kind": symbol.kind.value},
    )


def _completion_kind(kind: SymbolKind) -> lsp.CompletionItemKind:
    return {
        SymbolKind.CORE_FORM: lsp.CompletionItemKind.Keyword,
        SymbolKind.PYTHON_BUILTIN: lsp.CompletionItemKind.Function,
        SymbolKind.LOCAL_FUNCTION: lsp.CompletionItemKind.Function,
        SymbolKind.LOCAL_MACRO: lsp.CompletionItemKind.Keyword,
        SymbolKind.LOCAL_CLASS: lsp.CompletionItemKind.Class,
        SymbolKind.LOCAL_VARIABLE: lsp.CompletionItemKind.Variable,
        SymbolKind.MODULE: lsp.CompletionItemKind.Module,
    }.get(kind, lsp.CompletionItemKind.Text)


def _hover_markdown(symbol: SymbolInfo) -> str:
    heading = f"### `{symbol.name}`"
    if symbol.signature:
        heading = f"### `{symbol.signature}`"
    parts = [heading, f"_{symbol.detail or symbol.kind.value}_"]
    if symbol.documentation:
        parts.append(symbol.documentation)
    return "\n\n".join(parts)
