"""Stable process exit codes for the Poliscope CLI.

Scripts and CI steps branch on these numbers, so a code that has been released
must never be reassigned to a different meaning. Add new codes at the end.

``USAGE`` is fixed at 2 because :mod:`argparse` exits with 2 on a malformed
command line and that behaviour cannot be overridden without reimplementing its
error handling.
"""

from __future__ import annotations

from typing import Final

OK: Final = 0
"""The command completed and the server accepted it."""

FAILED: Final = 1
"""An unexpected local error. The traceback is suppressed but the message is not."""

USAGE: Final = 2
"""The command line itself was wrong. Emitted by argparse."""

REQUEST_REJECTED: Final = 3
"""The API answered with a 4xx status, for example an unknown task."""

UNREACHABLE: Final = 4
"""The API could not be contacted at all."""

INTERRUPTED: Final = 130
"""The user pressed Ctrl-C. Mirrors the shell convention of 128 + SIGINT."""
