"""Entry point: makes both ``python -m hpc_harness`` and ``python scripts/hpc_harness`` work.

When invoked as ``python scripts/hpc_harness``, this file runs without a package
context, so the parent directory (``scripts/``) is put on ``sys.path`` first.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):  # `python scripts/hpc_harness` invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hpc_harness.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
