from pygls import uris

from hyground.config import DEFAULT_EXCLUDE_DIRS, HyGroundConfig, load_config
from hyground.index import WorkspaceIndex


def test_load_config_from_pyproject(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "index-limit = 37\n"
        "exclude-dirs = ['generated', 'node_modules']\n"
        "allow-workspace-imports = false\n"
    )

    config = load_config(tmp_path)

    assert config.index_limit == 37
    assert config.allow_workspace_imports is False
    assert set(DEFAULT_EXCLUDE_DIRS).issubset(config.exclude_dirs)
    assert "generated" in config.exclude_dirs
    assert "node_modules" in config.exclude_dirs


def test_snake_case_config_keys_are_accepted(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "index_limit = 9\n"
        "exclude_dirs = ['build']\n"
        "allow_workspace_imports = false\n"
    )

    config = load_config(tmp_path)

    assert config.index_limit == 9
    assert "build" in config.exclude_dirs
    assert config.allow_workspace_imports is False


def test_invalid_config_falls_back_to_defaults(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "index-limit = -1\n"
        "exclude-dirs = 'generated'\n"
        "allow-workspace-imports = 'nope'\n"
    )

    assert load_config(tmp_path) == HyGroundConfig()


def test_workspace_config_controls_index_limit_and_excludes(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "index-limit = 1\n"
        "exclude-dirs = ['ignored']\n"
    )
    (tmp_path / "a.hy").write_text('(defn a [] "A docs" 1)\n')
    (tmp_path / "b.hy").write_text('(defn b [] "B docs" 1)\n')
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "c.hy").write_text('(defn c [] "C docs" 1)\n')
    uri = uris.from_fs_path(str(tmp_path / "main.hy"))

    index = WorkspaceIndex()
    index.update_document(uri, "")

    indexed_project_docs = [
        document
        for doc_uri, document in index.documents.items()
        if doc_uri != uri and document.uri.endswith(".hy")
    ]
    assert len(indexed_project_docs) == 1
    assert index.resolve(uri, "c") is None


def test_workspace_imports_can_be_disabled(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "allow-workspace-imports = false\n"
    )
    marker = tmp_path / "imported.txt"
    (tmp_path / "unsafe_lib.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported')\n"
        "def boom():\n"
        "    return 1\n"
    )
    uri = uris.from_fs_path(str(tmp_path / "main.hy"))

    index = WorkspaceIndex()
    index.update_document(uri, "(import unsafe-lib [boom])\n")

    boom = index.resolve(uri, "boom")
    assert boom is not None
    assert boom.source is not None
    assert boom.source.uri.endswith("unsafe_lib.py")
    assert not marker.exists()


def test_workspace_require_imports_are_not_executed_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "allow-workspace-imports = false\n"
    )
    marker = tmp_path / "required.txt"
    (tmp_path / "macros.hy").write_text(
        "(import pathlib [Path])\n"
        f"((. (Path {str(marker)!r}) write-text) \"required\")\n"
        "(defmacro zap [] 1)\n"
    )
    uri = uris.from_fs_path(str(tmp_path / "main.hy"))

    index = WorkspaceIndex()
    index.update_document(uri, "(require macros [zap])\n")

    assert not marker.exists()


def test_external_imports_still_work_when_workspace_imports_are_disabled(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.hyground]\n"
        "allow-workspace-imports = false\n"
    )
    uri = uris.from_fs_path(str(tmp_path / "main.hy"))

    index = WorkspaceIndex()
    index.update_document(uri, "(import math [sqrt])\n")

    sqrt = index.resolve(uri, "sqrt")
    assert sqrt is not None
    assert "square root" in sqrt.documentation
