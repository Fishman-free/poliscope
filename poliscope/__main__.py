"""Support ``python -m poliscope`` as an alias for the ``poliscope`` script.

Both entry points must reach the same :func:`apps.cli.main.main`, so that the
documented invocation and the installed console script cannot drift apart.
"""

from __future__ import annotations

import sys

from apps.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
