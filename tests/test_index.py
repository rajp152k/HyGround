from hyground.index import WorkspaceIndex

URI = "file:///workspace/main.hy"


def build(source: str) -> WorkspaceIndex:
    index = WorkspaceIndex()
    index.update_document(URI, source)
    return index


def names(index: WorkspaceIndex, prefix: str) -> set[str]:
    return {symbol.name for symbol in index.symbols_for_completion(URI, prefix)}


def test_core_form_completion_and_docs() -> None:
    index = build("(if True 1 2)\n")

    assert "lfor" in names(index, "lf")
    assert "if" in names(index, "if")
    assert "setv" in names(index, "set")

    info = index.resolve(URI, "lfor")
    assert info is not None
    assert "List comprehension" in info.documentation
    assert "(lfor clauses value)" == info.signature


def test_python_builtin_docs() -> None:
    index = build("(print \"hi\")\n")

    assert "print" in names(index, "pr")
    info = index.resolve(URI, "print")
    assert info is not None
    assert "Prints the values" in info.documentation


def test_local_definition_docs_and_definition_range() -> None:
    index = build('(defn foo [x]\n  "Foo docs"\n  (+ x 1))\n(setv bar 2)\n')

    assert "foo" in names(index, "fo")
    assert "bar" in names(index, "ba")

    foo = index.resolve(URI, "foo")
    assert foo is not None
    assert foo.signature == "(foo [x])"
    assert foo.documentation == "Foo docs"
    assert foo.source is not None
    assert foo.source.start_line == 0
    assert foo.source.start_character == 6


def test_parse_diagnostic() -> None:
    document = WorkspaceIndex().update_document(URI, "(if True 1")

    assert document.diagnostics
    assert "Premature end" in document.diagnostics[0].message


def test_compile_diagnostic() -> None:
    document = WorkspaceIndex().update_document(URI, "(if True 1)\n")

    assert document.diagnostics
    assert "parse error for pattern macro 'if'" in document.diagnostics[0].message


def test_python_import_completion_hover_and_definition() -> None:
    index = build("(import pathlib [Path])\n(import os)\n(import json)\n")

    assert "Path" in names(index, "Pa")
    path_info = index.resolve(URI, "Path")
    assert path_info is not None
    assert "PurePath" in path_info.documentation
    assert path_info.source is not None
    assert "pathlib" in path_info.source.uri

    attrs = {symbol.name for symbol in index.symbols_for_completion(URI, "os.pa")}
    assert "os.path" in attrs

    dumps = index.resolve(URI, "json.dumps")
    assert dumps is not None
    assert "Serialize" in dumps.documentation
    assert dumps.source is not None
    assert "json" in dumps.source.uri


def test_importable_module_completion() -> None:
    index = build("")

    candidates = names(index, "pathli")
    assert "pathlib" in candidates


def test_project_wide_hy_definition(tmp_path) -> None:
    root = tmp_path
    uri = f"file://{root}/main.hy"
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / "lib.hy").write_text('(defn helper []\n  "Helper docs"\n  1)\n')

    index = WorkspaceIndex()
    index.update_document(uri, "(helper)\n")

    helper = index.resolve(uri, "helper")
    assert helper is not None
    assert helper.documentation == "Helper docs"
    assert helper.source is not None
    assert helper.source.uri.endswith("lib.hy")
