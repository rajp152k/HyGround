"""Command-line entrypoints for HyGround."""

from __future__ import annotations

import argparse

from . import __version__
from .server import make_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hyground")
    parser.description = "HyGround - a principled Hy language server"
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args(argv)

    if args.version:
        print(f"hyground {__version__}")
        return

    make_server().start_io()
