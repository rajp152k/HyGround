from hyground.completion_context import CompletionContext, completion_context


def test_import_module_context() -> None:
    source = "(import pathli)\n"

    assert completion_context(source, 0, len("(import pathli")) == CompletionContext("import-module")


def test_import_member_context() -> None:
    source = "(import os.path [ex])\n"

    assert completion_context(source, 0, source.index("ex") + 2) == CompletionContext(
        "import-member",
        "os.path",
    )


def test_require_macro_and_reader_contexts() -> None:
    source = "(require macros :macros [za] :readers [ba])\n"

    assert completion_context(source, 0, source.index("za") + 2) == CompletionContext(
        "require-macro",
        "macros",
    )
    assert completion_context(source, 0, source.index("ba") + 2) == CompletionContext(
        "require-reader",
        "macros",
    )
