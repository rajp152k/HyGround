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


def test_local_reader_macro_definition() -> None:
    index = build('(defreader bang []\n  "Bang docs"\n  1)\n')

    assert "#bang" in names(index, "#ba")
    info = index.resolve(URI, "#bang")
    assert info is not None
    assert info.kind == SymbolKind.READER_MACRO
    assert info.signature == "(defreader bang [])"
    assert info.documentation == "Bang docs"
    assert info.source is not None
    assert info.source.start_line == 0


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


def test_assignment_destructuring_and_setx_are_indexed() -> None:
    index = build('(setv [a b] pair)\n(setv (, c d) other)\n(setv {"x" e :y f} data)\n(setx g 1)\n(setv (. obj attr) 2)\n')

    for name in ["a", "b", "c", "d", "e", "f", "g"]:
        symbol = index.resolve(URI, name)
        assert symbol is not None
        assert symbol.kind == SymbolKind.LOCAL_VARIABLE

    assert "attr" not in index.documents[URI].symbols
    assert "g" in names(index, "g")


def test_parameter_resolution_is_position_scoped() -> None:
    source = '(defn foo [print [y 1] #* rest]\n  (print y rest))\n(print "outside")\n'
    index = build(source)

    scoped_print = index.resolve(URI, "print", 1, 4)
    assert scoped_print is not None
    assert scoped_print.kind == SymbolKind.PARAMETER
    assert scoped_print.detail == "parameter of foo"
    assert scoped_print.source is not None
    assert scoped_print.source.start_line == 0

    y = index.resolve(URI, "y", 1, 10)
    assert y is not None
    assert y.kind == SymbolKind.PARAMETER

    rest = index.resolve(URI, "rest", 1, 17)
    assert rest is not None
    assert rest.kind == SymbolKind.PARAMETER

    outside_print = index.resolve(URI, "print", 2, 2)
    assert outside_print is not None
    assert outside_print.kind == SymbolKind.PYTHON_BUILTIN

    completions = {symbol.name for symbol in index.symbols_for_completion(URI, "y", 1, 10)}
    assert "y" in completions
    assert "y" not in names(index, "y")


def test_parse_diagnostic() -> None:
    document = WorkspaceIndex().update_document(URI, "(if True 1")

    assert document.diagnostics
    diagnostic = document.diagnostics[0]
    assert "Premature end" in diagnostic.message
    assert diagnostic.code == "hy-reader"
    assert diagnostic.line == 0
    assert diagnostic.character == 9
    assert diagnostic.end_line == 0
    assert diagnostic.end_character == 10


def test_reader_recovery_indexes_complete_forms_before_error() -> None:
    index = WorkspaceIndex()
    document = index.update_document(URI, '(defn before-error []\n  "Before docs"\n  1)\n(if True 1')

    assert document.diagnostics
    before = index.resolve(URI, "before-error")
    assert before is not None
    assert before.documentation == "Before docs"
    assert "before-error" in names(index, "before")


def test_compile_diagnostic() -> None:
    document = WorkspaceIndex().update_document(URI, "(if True 1)\n")

    assert document.diagnostics
    diagnostic = document.diagnostics[0]
    assert "parse error for pattern macro 'if'" in diagnostic.message
    assert diagnostic.code == "hy-compiler"
    assert diagnostic.line == 0
    assert diagnostic.end_character > diagnostic.character


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
    assert "#bang" in {symbol.name for symbol in index.symbols_for_completion(uri, "#ba")}


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


def test_imported_hy_member_resolution_is_module_aware(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / "alpha.hy").write_text('(defn helper []\n  "Alpha docs"\n  1)\n')
    (root / "beta.hy").write_text(
        '(defn helper []\n  "Beta docs"\n  2)\n'
        '(raise (Exception "do not import beta at indexing time"))\n'
    )

    index = WorkspaceIndex()
    index.update_document(uri, "(import beta [helper])\n(helper)\n")

    helper = index.resolve(uri, "helper")
    assert helper is not None
    assert helper.documentation == "Beta docs"
    assert helper.source is not None
    assert helper.source.uri.endswith("beta.hy")


def test_hy_module_alias_dotted_resolution_and_completion(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (root / "lib.hy").write_text('(defn helper []\n  "Alias docs"\n  1)\n')

    index = WorkspaceIndex()
    index.update_document(uri, "(import lib :as L)\n(L.helper)\n")

    helper = index.resolve(uri, "L.helper")
    assert helper is not None
    assert helper.name == "L.helper"
    assert helper.documentation == "Alias docs"
    assert helper.source is not None
    assert helper.source.uri.endswith("lib.hy")

    completions = {symbol.name for symbol in index.symbols_for_completion(uri, "L.he")}
    assert "L.helper" in completions


def test_static_stub_symbols_are_enriched_with_implementation_docstrings(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    site_packages = root / ".venv" / "lib" / "python3.12" / "site-packages"
    package = site_packages / "doc_pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (package / "__init__.pyi").write_text(
        "def load(path: str) -> Model: ...\n"
        "from .core import Model as Model\n"
    )
    (package / "__init__.py").write_text(
        '"Implementation module docs"\n'
        "def load(path):\n"
        "    \"Load docs from implementation.\"\n"
        "    raise RuntimeError\n"
        "import missing_binary_extension\n"
    )
    (package / "core.pyi").write_text("class Model: ...\n")
    (package / "core.py").write_text(
        "class Model:\n"
        "    \"Model docs from implementation.\"\n"
        "    pass\n"
    )

    index = WorkspaceIndex()
    index.update_document(uri, "(import doc-pkg :as dp)\ndp.load\ndp.Model\n")

    module = index.resolve(uri, "dp")
    assert module is not None
    assert module.documentation == "Implementation module docs"

    load = index.resolve(uri, "dp.load")
    assert load is not None
    assert load.signature == "(load path: str) -> Model"
    assert load.documentation == "Load docs from implementation."
    assert load.source is not None
    assert load.source.uri.endswith("doc_pkg/__init__.pyi")

    model = index.resolve(uri, "dp.Model")
    assert model is not None
    assert model.documentation == "Model docs from implementation."
    assert model.source is not None
    assert model.source.uri.endswith("doc_pkg/core.pyi")


def test_uv_site_packages_static_fallback_when_runtime_import_fails(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    site_packages = root / ".venv" / "lib" / "python3.12" / "site-packages"
    package = site_packages / "external_pkg"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (package / "__init__.py").write_text("import missing_external_pkg_extension\n")
    (package / "__init__.pyi").write_text(
        '"External package docs"\n'
        "class DataFrame: ...\n"
        "def read_csv(path: str) -> DataFrame: ...\n"
        "from external_pkg.core import Series as Series\n"
    )

    index = WorkspaceIndex()
    index.update_document(uri, "(import external-pkg :as xp)\nxp.read-csv\n")

    module = index.resolve(uri, "xp")
    assert module is not None
    assert "External package docs" in module.documentation
    assert module.source is not None
    assert module.source.uri.endswith("external_pkg/__init__.pyi")

    read_csv = index.resolve(uri, "xp.read-csv")
    assert read_csv is not None
    assert read_csv.signature == "(read-csv path: str) -> DataFrame"
    assert read_csv.source is not None
    assert read_csv.source.uri.endswith("external_pkg/__init__.pyi")

    completions = {symbol.name for symbol in index.symbols_for_completion(uri, "xp.")}
    assert {"xp.DataFrame", "xp.read-csv", "xp.Series"}.issubset(completions)


def test_static_python_resolution_when_runtime_import_is_disabled(tmp_path) -> None:
    root = tmp_path
    main = root / "main.hy"
    uri = uris.from_fs_path(str(main))
    marker = root / "imported.txt"
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n"
        "[tool.hyground]\nallow-workspace-imports = false\n"
    )
    (root / "safe_lib.py").write_text(
        '"Module docs"\n'
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def hello_world(value: int = 1) -> int:\n"
        "    \"Hello docs\"\n"
        "    return value\n"
        "class Thing:\n"
        "    \"Thing docs\"\n"
        "    pass\n"
        "ANSWER = 42\n"
    )

    index = WorkspaceIndex()
    index.update_document(uri, "(import safe-lib)\nsafe-lib.hello-world\n")

    assert not marker.exists()

    module = index.resolve(uri, "safe-lib")
    assert module is not None
    assert "Module docs" in module.documentation

    hello = index.resolve(uri, "safe-lib.hello-world")
    assert hello is not None
    assert hello.documentation == "Hello docs"
    assert "value: int=1" in hello.signature
    assert "-> int" in hello.signature
    assert hello.source is not None
    assert hello.source.uri.endswith("safe_lib.py")

    attrs = {symbol.name for symbol in index.symbols_for_completion(uri, "safe-lib.he")}
    assert "safe-lib.hello-world" in attrs


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
