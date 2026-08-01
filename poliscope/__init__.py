"""Top-level entry point package for the Poliscope command line interface.

The CLI implementation lives in :mod:`apps.cli` because the design spec places
every deployable surface under ``apps/``. This package exists only so that
``python -m poliscope`` works without requiring the console script to be
installed, which matters when the repository is run straight from a checkout.

Nothing except the entry point belongs here. Adding business logic would create
a second home for the CLI and split its behaviour across two packages.
"""

from __future__ import annotations

from apps.cli.main import build_parser, main

__all__ = ["build_parser", "main"]
