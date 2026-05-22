"""HyGround: a principled Hy language server."""

__version__ = "0.1.0"


def main() -> None:
    """Console entrypoint kept for uv's default script shape."""
    from .cli import main as cli_main

    cli_main()
