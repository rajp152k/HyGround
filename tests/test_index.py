from pygls import uris

from hyground.index import WorkspaceIndex
from hyground.model import SymbolKind

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
    index = build("(import pathlib [Path])\n(import os)\n(import json)\n(import math)\n(import cmath)\n")

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

    sqrt = index.resolve(URI, "math.sqrt")
    assert sqrt is not None
    assert "square root" in sqrt.documentation
    assert sqrt.source is not None
    assert sqrt.source.uri.endswith("math.pyi")
    assert sqrt.source.start_line > 0

    exp = index.resolve(URI, "cmath.exp")
    assert exp is not None
    assert exp.source is not None
    assert exp.source.uri.endswith("cmath.pyi")


def test_importable_module_completion() -> None:
    index = build("")

    candidates = names(index, "pathli")
    assert "pathlib" in candidates


def test_star_import_records_public_members() -> None:
    index = build("(import math *)\n(sqrt 4)\n")

    sqrt = index.resolve(URI, "sqrt")
    assert sqrt is not None
    assert "square root" in sqrt.documentation
    assert sqrt.source is not None
    assert sqrt.source.uri.endswith("math.pyi")


def test_require_records_alias_star_selected_and_reader_macros(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / "macros.hy").write_text(
        '(defmacro zap []\n  "Zap docs"\n  1)\n'
        '(defmacro zip []\n  "Zip docs"\n  1)\n'
        '(defreader bang []\n  "Bang docs"\n  1)\n'
    )

    index = WorkspaceIndex()
    index.update_document(
        uri,
        "(require macros :as M)\n"
        "(require macros [zap :as zap-alias])\n"
        "(require macros *)\n"
        "(require macros :readers [bang])\n",
    )

    prefixed = index.resolve(uri, "M.zap")
    assert prefixed is not None
    assert prefixed.documentation == "Zap docs"

    alias = index.resolve(uri, "zap-alias")
    assert alias is not None
    assert alias.documentation == "Zap docs"

    star = index.resolve(uri, "zip")
    assert star is not None
    assert star.documentation == "Zip docs"

    reader = index.resolve(uri, "#bang")
    assert reader is not None
    assert reader.kind == SymbolKind.READER_MACRO
    assert "reader macro" in reader.detail


def test_hyphenated_import_member_aliases(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / "cool_lib.py").write_text('def hello_world():\n    "Hello alias docs"\n    return 1\n')

    index = WorkspaceIndex()
    index.update_document(uri, "(import cool-lib [hello-world :as greet])\n")

    greet = index.resolve(uri, "greet")
    assert greet is not None
    assert greet.documentation == "Hello alias docs"
    assert greet.source is not None
    assert greet.source.uri.endswith("cool_lib.py")


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


def test_reindex_root_refreshes_project_files_and_python_imports(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    main_source = "(import fresh-lib)\n(helper)\n"

    index = WorkspaceIndex()
    index.update_document(uri, main_source)
    assert index.resolve(uri, "fresh-lib") is None
    assert index.resolve(uri, "helper") is None

    (root / "fresh_lib.py").write_text('def hello_world():\n    "Hello docs"\n    return 1\n')
    (root / "lib.hy").write_text('(defn helper []\n  "Helper docs"\n  1)\n')

    rebuilt = index.reindex_root(root, {uri: main_source})
    assert [document.uri for document in rebuilt] == [uri]

    freshlib = index.resolve(uri, "fresh-lib")
    assert freshlib is not None
    assert freshlib.source is not None
    assert freshlib.source.uri.endswith("fresh_lib.py")

    hello = index.resolve(uri, "fresh-lib.hello-world")
    assert hello is not None
    assert hello.documentation == "Hello docs"

    helper = index.resolve(uri, "helper")
    assert helper is not None
    assert helper.documentation == "Helper docs"
