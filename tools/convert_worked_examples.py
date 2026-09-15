"""Entry point of the worked-example converter (cost-spec-v2 §3.3).

    python tools/convert_worked_examples.py            # regenerate all YAML files
    python tools/convert_worked_examples.py --check    # CI: fail if a YAML drifted
    python tools/convert_worked_examples.py --list-unreviewed

The implementation lives in the `tools/worked_examples/` package, which this file was split into
during the PR-561 review round — at 1,147 lines it was more than twice the 500-line ceiling the
rest of this library holds itself to. See that package's docstring for what the converter protects
and for a map of its modules.

This shim keeps the original path working because two callers name it literally:
`.github/workflows/worked-examples.yml` runs `python tools/convert_worked_examples.py --check`, and
`roadmap/cost-spec-v2.md` §3.3 documents that command as the one authors run.
"""

from __future__ import annotations

import os
import sys


def _add_repository_root_to_path() -> None:
    """Makes `tools.worked_examples` importable when this file is run as a script.

    Run as `python tools/convert_worked_examples.py`, Python puts `tools/` on `sys.path` — not the
    repository root — under which the package is reachable as `worked_examples` but not under the
    dotted `tools.worked_examples` name its own modules and the test suite import it by. Prepending
    the repository root fixes the name regardless of the directory the command was started from,
    which the CI job depends on: it runs from the checkout root with nothing installed.
    """
    repository_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)


_add_repository_root_to_path()

from tools.worked_examples.cli import main  # noqa: E402  pylint: disable=wrong-import-position

if __name__ == "__main__":
    sys.exit(main())
