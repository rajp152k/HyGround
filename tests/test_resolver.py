from pathlib import Path

from hyground.resolver import PythonResolver


def test_resolver_discovers_uv_site_package_layouts(tmp_path: Path) -> None:
    expected = [
        tmp_path / ".venv" / "lib" / "python3.12" / "site-packages",
        tmp_path / ".venv" / "lib64" / "python3.12" / "site-packages",
        tmp_path / ".venv" / "Lib" / "site-packages",
    ]
    for path in expected:
        path.mkdir(parents=True)

    resolver = PythonResolver(tmp_path)
    search_paths = {path.resolve() for path in resolver.search_paths}
    static_paths = {path.resolve() for path in resolver.static_search_paths}

    for path in expected:
        assert path.resolve() in search_paths
        assert path.resolve() in static_paths
