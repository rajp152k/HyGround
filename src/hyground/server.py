"""pygls server wiring for HyGround."""

from __future__ import annotations

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from . import __version__
from .completion_context import CompletionContext, completion_context
from .folding import folding_ranges
from .index import DocumentIndex, WorkspaceIndex
from .lsp_runtime import REINDEX_COMMAND, register_lsp_specs
from .model import SymbolInfo, SymbolKind
from .semantic import encode_semantic_tokens, semantic_tokens
from .word import enclosing_call, occurrences, word_at, word_prefix, word_range_at


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
    def did_open(params: lsp.DidOpenTextDocumentParams) -> None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        _index_and_publish(server, uri, document.source)

    def did_change(params: lsp.DidChangeTextDocumentParams) -> None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        _index_and_publish(server, uri, document.source)

    def did_close(params: lsp.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        server.index.remove_document(uri)
        server.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )

    def did_change_watched_files(params: lsp.DidChangeWatchedFilesParams) -> None:
        roots = {
            server.index.root_for_uri(change.uri)
            for change in params.changes
            if _is_reindex_relevant_uri(change.uri)
        }
        for root in roots:
            _reindex_root_and_publish(server, root)

    def completion(params: lsp.CompletionParams) -> lsp.CompletionList:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        prefix = word_prefix(document.source, params.position.line, params.position.character)
        replace_range = lsp.Range(
            start=lsp.Position(
                line=params.position.line,
                character=max(params.position.character - len(prefix), 0),
            ),
            end=params.position,
        )
        context = completion_context(document.source, params.position.line, params.position.character)
        items = [
            _completion_item(symbol, replace_range)
            for symbol in _symbols_for_completion(server, uri, prefix, context, params.position)
        ]
        return lsp.CompletionList(is_incomplete=False, items=items)

    def hover(params: lsp.HoverParams) -> lsp.Hover | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        if not name:
            return None
        symbol = server.index.resolve(uri, name, params.position.line, params.position.character)
        if symbol is None:
            return None
        return lsp.Hover(
            contents=lsp.MarkupContent(kind=lsp.MarkupKind.Markdown, value=_hover_markdown(symbol))
        )

    def definition(params: lsp.DefinitionParams) -> list[lsp.Location] | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        if not name:
            return None
        symbol = server.index.resolve(uri, name, params.position.line, params.position.character)
        if symbol is None or symbol.source is None:
            return None
        return [symbol.source.to_location()]

    def semantic_tokens_full(params: lsp.SemanticTokensParams) -> lsp.SemanticTokens:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        tokens = semantic_tokens(document.source, lambda name, line, character: server.index.resolve(uri, name, line, character))
        return lsp.SemanticTokens(data=encode_semantic_tokens(tokens))

    def folding_range(params: lsp.FoldingRangeParams) -> list[lsp.FoldingRange]:
        document = server.workspace.get_text_document(params.text_document.uri)
        return [
            lsp.FoldingRange(
                start_line=fold.start_line,
                start_character=fold.start_character,
                end_line=fold.end_line,
                end_character=fold.end_character,
            )
            for fold in folding_ranges(document.source)
        ]

    def document_symbol(params: lsp.DocumentSymbolParams) -> list[lsp.DocumentSymbol]:
        uri = params.text_document.uri
        document = server.index.documents.get(uri)
        if document is None:
            text_document = server.workspace.get_text_document(uri)
            document = server.index.update_document(uri, text_document.source)
        symbols: list[lsp.DocumentSymbol] = []
        for symbol in document.symbols.values():
            if symbol.source is None or symbol.source.uri != uri:
                continue
            rng = symbol.source.to_lsp_range()
            symbols.append(
                lsp.DocumentSymbol(
                    name=symbol.name,
                    kind=_symbol_kind(symbol.kind),
                    range=rng,
                    selection_range=rng,
                    detail=symbol.detail or symbol.kind.value,
                )
            )
        return symbols

    def workspace_symbol(params: lsp.WorkspaceSymbolParams) -> list[lsp.SymbolInformation]:
        query = params.query.lower()
        out: list[lsp.SymbolInformation] = []
        seen: set[tuple[str, str, int, int]] = set()
        for document in server.index.documents.values():
            for symbol in document.symbols.values():
                if symbol.source is None:
                    continue
                if query and query not in symbol.name.lower():
                    continue
                key = (
                    symbol.name,
                    symbol.source.uri,
                    symbol.source.start_line,
                    symbol.source.start_character,
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    lsp.SymbolInformation(
                        name=symbol.name,
                        kind=_symbol_kind(symbol.kind),
                        location=symbol.source.to_location(),
                        container_name=symbol.detail or symbol.kind.value,
                    )
                )
        return sorted(out, key=lambda symbol: symbol.name)

    def references(params: lsp.ReferenceParams) -> list[lsp.Location]:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        if not name:
            return []
        symbol = server.index.resolve(uri, name, params.position.line, params.position.character)
        include_declaration = params.context.include_declaration if params.context else True
        return _reference_locations(server, uri, name, symbol, include_declaration=include_declaration)

    def prepare_rename(params: lsp.PrepareRenameParams) -> lsp.Range | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        name = word_at(document.source, params.position.line, params.position.character)
        symbol = server.index.resolve(uri, name, params.position.line, params.position.character) if name else None
        word_range = word_range_at(document.source, params.position.line, params.position.character)
        if symbol is None or word_range is None or not _renamable_at_uri(symbol, uri):
            return None
        start, end = word_range
        return lsp.Range(
            start=lsp.Position(line=params.position.line, character=start),
            end=lsp.Position(line=params.position.line, character=end),
        )

    def rename(params: lsp.RenameParams) -> lsp.WorkspaceEdit | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        old_name = word_at(document.source, params.position.line, params.position.character)
        symbol = server.index.resolve(uri, old_name, params.position.line, params.position.character) if old_name else None
        if symbol is None or not _renamable_at_uri(symbol, uri) or not params.new_name:
            return None
        changes: dict[str, list[lsp.TextEdit]] = {}
        for location in _reference_locations(
            server,
            uri,
            old_name,
            symbol,
            only_uri=symbol.source.uri if symbol.source else uri,
            include_declaration=True,
        ):
            changes.setdefault(location.uri, []).append(
                lsp.TextEdit(range=location.range, new_text=params.new_name)
            )
        return lsp.WorkspaceEdit(changes=changes)

    def signature_help(params: lsp.SignatureHelpParams) -> lsp.SignatureHelp | None:
        uri = params.text_document.uri
        document = server.workspace.get_text_document(uri)
        call = enclosing_call(document.source, params.position.line, params.position.character)
        if call is None:
            return None
        name, active_parameter = call
        symbol = server.index.resolve(uri, name, params.position.line, params.position.character)
        if symbol is None or not symbol.signature:
            return None
        return lsp.SignatureHelp(
            signatures=[
                lsp.SignatureInformation(
                    label=symbol.signature,
                    documentation=lsp.MarkupContent(
                        kind=lsp.MarkupKind.Markdown,
                        value=_hover_markdown(symbol),
                    ),
                )
            ],
            active_signature=0,
            active_parameter=active_parameter,
        )

    def reindex_workspace(ls: HyGroundServer, uri: str | None = None) -> dict[str, object]:
        target_uri = uri or next(iter(ls.workspace.text_documents), None) or ls.workspace.root_uri
        if target_uri is None:
            return {"ok": False, "message": "No workspace or open Hy document to reindex."}

        root = ls.index.root_for_uri(target_uri)
        rebuilt = _reindex_root_and_publish(ls, root)

        message = f"HyGround reindexed {root} ({len(ls.index.documents)} documents)."
        ls.window_show_message(
            lsp.ShowMessageParams(type=lsp.MessageType.Info, message=message)
        )
        return {"ok": True, "root": str(root), "documents": len(ls.index.documents)}

    register_lsp_specs(server, locals())


def _reference_locations(
    server: HyGroundServer,
    request_uri: str,
    name: str,
    symbol: SymbolInfo | None = None,
    only_uri: str | None = None,
    include_declaration: bool = True,
) -> list[lsp.Location]:
    locations: list[lsp.Location] = []
    candidate_uris = _reference_candidate_uris(server, request_uri, symbol, only_uri)
    for doc_uri in candidate_uris:
        indexed = server.index.documents.get(doc_uri)
        if indexed is None:
            continue
        for line, start, end in occurrences(indexed.source, name):
            locations.append(
                lsp.Location(
                    uri=doc_uri,
                    range=lsp.Range(
                        start=lsp.Position(line=line, character=start),
                        end=lsp.Position(line=line, character=end),
                    ),
                )
            )

    if include_declaration and symbol is not None and symbol.source is not None:
        declaration = symbol.source.to_location()
        if not _contains_location(locations, declaration):
            locations.insert(0, declaration)
    elif not include_declaration and symbol is not None and symbol.source is not None:
        locations = [location for location in locations if not _same_location(location, symbol.source.to_location())]

    return locations


def _reference_candidate_uris(
    server: HyGroundServer,
    request_uri: str,
    symbol: SymbolInfo | None,
    only_uri: str | None,
) -> list[str]:
    if only_uri is not None:
        return [only_uri]
    if symbol is not None and symbol.source is not None and _renamable(symbol):
        return list(dict.fromkeys([request_uri, symbol.source.uri]))
    return list(server.index.documents)


def _contains_location(locations: list[lsp.Location], needle: lsp.Location) -> bool:
    return any(_same_location(location, needle) for location in locations)


def _same_location(left: lsp.Location, right: lsp.Location) -> bool:
    return left.uri == right.uri and left.range == right.range


def _renamable_at_uri(symbol: SymbolInfo, uri: str) -> bool:
    return _renamable(symbol) and symbol.source is not None and symbol.source.uri == uri


def _renamable(symbol: SymbolInfo) -> bool:
    return symbol.kind in {
        SymbolKind.LOCAL_FUNCTION,
        SymbolKind.LOCAL_MACRO,
        SymbolKind.LOCAL_CLASS,
        SymbolKind.LOCAL_VARIABLE,
    }


def _index_and_publish(server: HyGroundServer, uri: str, source: str) -> DocumentIndex:
    document = server.index.update_document(uri, source)
    _publish_diagnostics(server, document)
    return document


def _reindex_root_and_publish(server: HyGroundServer, root) -> list[DocumentIndex]:
    open_sources = {
        doc_uri: document.source
        for doc_uri, document in server.workspace.text_documents.items()
        if server.index.root_for_uri(doc_uri).resolve() == root.resolve()
    }
    rebuilt = server.index.reindex_root(root, open_sources)
    for document in rebuilt:
        _publish_diagnostics(server, document)
    return rebuilt


def _is_reindex_relevant_uri(uri: str) -> bool:
    path = uri.rsplit("/", 1)[-1]
    return (
        path.endswith((".hy", ".py", ".pyi"))
        or path in {"pyproject.toml", "uv.lock", "poetry.lock", "pdm.lock", "requirements.txt"}
    )


def _publish_diagnostics(server: HyGroundServer, document: DocumentIndex) -> None:
    server.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(
            uri=document.uri,
            diagnostics=[diagnostic.to_lsp() for diagnostic in document.diagnostics],
        )
    )


def _symbols_for_completion(
    server: HyGroundServer,
    uri: str,
    prefix: str,
    context: CompletionContext,
    position: lsp.Position,
) -> list[SymbolInfo]:
    resolver = server.index.resolver_for_root(server.index.root_for_uri(uri))
    if context.kind in {"import-module", "require-module"}:
        return resolver.module_candidates(prefix)
    if context.kind == "import-member" and context.module:
        return resolver.member_candidates(context.module, prefix)
    if context.kind == "require-macro" and context.module:
        return resolver.macro_candidates(context.module, prefix)
    if context.kind == "require-reader" and context.module:
        return resolver.reader_macro_candidates(context.module, prefix)
    return server.index.symbols_for_completion(uri, prefix, position.line, position.character)


def _completion_item(symbol: SymbolInfo, replace_range: lsp.Range) -> lsp.CompletionItem:
    return lsp.CompletionItem(
        label=symbol.name,
        kind=_completion_kind(symbol.kind),
        detail=symbol.detail or symbol.kind.value,
        documentation=_hover_markdown(symbol) if symbol.documentation or symbol.signature else None,
        text_edit=lsp.TextEdit(range=replace_range, new_text=symbol.name),
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
        SymbolKind.READER_MACRO: lsp.CompletionItemKind.Keyword,
        SymbolKind.LOCAL_CLASS: lsp.CompletionItemKind.Class,
        SymbolKind.LOCAL_VARIABLE: lsp.CompletionItemKind.Variable,
        SymbolKind.PARAMETER: lsp.CompletionItemKind.Variable,
        SymbolKind.MODULE: lsp.CompletionItemKind.Module,
    }.get(kind, lsp.CompletionItemKind.Text)


def _symbol_kind(kind: SymbolKind) -> lsp.SymbolKind:
    return {
        SymbolKind.CORE_FORM: lsp.SymbolKind.Function,
        SymbolKind.PYTHON_BUILTIN: lsp.SymbolKind.Function,
        SymbolKind.LOCAL_FUNCTION: lsp.SymbolKind.Function,
        SymbolKind.LOCAL_MACRO: lsp.SymbolKind.Function,
        SymbolKind.READER_MACRO: lsp.SymbolKind.Function,
        SymbolKind.LOCAL_CLASS: lsp.SymbolKind.Class,
        SymbolKind.LOCAL_VARIABLE: lsp.SymbolKind.Variable,
        SymbolKind.PARAMETER: lsp.SymbolKind.Variable,
        SymbolKind.MODULE: lsp.SymbolKind.Module,
    }.get(kind, lsp.SymbolKind.Object)


def _hover_markdown(symbol: SymbolInfo) -> str:
    heading = f"### `{symbol.name}`"
    if symbol.signature:
        heading = f"### `{symbol.signature}`"
    parts = [heading, f"_{symbol.detail or symbol.kind.value}_"]
    if symbol.documentation:
        parts.append(symbol.documentation)
    return "\n\n".join(parts)
