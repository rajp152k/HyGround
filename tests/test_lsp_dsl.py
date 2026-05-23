from lsprotocol import types as lsp

from hyground.lsp_runtime import REINDEX_COMMAND, registry


def test_hy_lsp_dsl_registry_contains_core_features() -> None:
    specs = registry()
    features = {spec.method: spec for spec in specs if spec.kind == "feature"}
    commands = {spec.command: spec for spec in specs if spec.kind == "command"}

    assert features[lsp.TEXT_DOCUMENT_COMPLETION].handler == "completion"
    assert features[lsp.TEXT_DOCUMENT_COMPLETION].options.trigger_characters == [".", " ", "-", "_", ":", "[", "#"]
    assert features[lsp.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL].handler == "semantic_tokens_full"
    assert features[lsp.TEXT_DOCUMENT_RENAME].options.prepare_provider is True
    assert commands[REINDEX_COMMAND].handler == "reindex_workspace"


def test_hy_lsp_dsl_handler_names_are_unique() -> None:
    handlers = [spec.handler for spec in registry()]

    assert len(handlers) == len(set(handlers))
