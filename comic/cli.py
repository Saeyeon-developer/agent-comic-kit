"""Command line entry point: `comic <command>` / `python -m comic <command>`."""

from __future__ import annotations

import argparse
import importlib
import sys

from comic import __version__
from comic.project import ComicError, ComicWaiting

COMMAND_MODULES = ("comic.commands.core", "comic.commands.generate", "comic.commands.imaging")


def _load_command_modules():
    """Import command modules; skip a module that does not exist yet, but never hide its own errors."""
    modules = []
    for name in COMMAND_MODULES:
        try:
            modules.append(importlib.import_module(name))
        except ModuleNotFoundError as e:
            if e.name != name:  # the module exists but something it imports is missing
                raise
    return modules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comic",
        description="agent-comic-kit: make comics with AI image generation (cast -> script -> pages -> panels -> compose -> letter -> export).",
    )
    parser.add_argument("--version", action="version", version=f"agent-comic-kit {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True
    for module in _load_command_modules():
        module.register(subparsers)
    return parser


def _utf8_stdio():
    # Pipes on Windows default to the ANSI code page; agents read our output through pipes.
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except ComicWaiting as e:
        print(str(e))
        return int(getattr(e, "exit_code", 3))
    except ComicError as e:
        print(f"error: {' '.join(str(e).split())}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
