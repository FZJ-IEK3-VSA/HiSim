"""The command line: workbook discovery, the three run modes, and the process exit code.

Kept apart from `workbook.py` so that converting one workbook is independent of how the tool was
invoked, and so the discovery predicate has a single owner: `find_workbooks` is what
`tests/test_worked_examples.py` imports to check the xlsx-to-yaml pairing, instead of repeating
"walk the tree, keep `.xlsx`, skip names starting with `_` or `~$`" a second time in a test.

`run` is the one driver behind all three modes — write, `--check`, `--list-unreviewed` — because
all three convert every workbook and differ only in what they do with the result. It never lets one
broken workbook hide the others: every per-workbook failure is collected and reported at the end.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from tools.worked_examples.attestation import ReviewState, review_status
from tools.worked_examples.model import ValidationError
from tools.worked_examples.workbook import convert_workbook


class Cli:
    """File-name conventions and exit codes of the tool, in one place.

    Class-scoped because the two suffixes and the two skipped prefixes together *are* the discovery
    convention — an example is a file, with no registry to update — and because the exit codes are a
    contract with CI: `.github/workflows/worked-examples.yml` fails the job on any non-zero code,
    and the three values distinguish "nothing to do", "a YAML drifted" and "a workbook is broken".
    """

    WORKBOOK_SUFFIX = ".xlsx"
    YAML_SUFFIX = ".yaml"
    #: `_template.xlsx` is the file authors copy, and `~$…` are Excel's lock files.
    SKIPPED_PREFIXES = ("_", "~$")

    EXIT_OK = 0
    #: No workbook found, or a committed YAML no longer matches its workbook (§3.6).
    EXIT_DRIFT = 1
    #: At least one workbook failed validation (§3.3).
    EXIT_INVALID = 2

    DESCRIPTION = "Converts the Excel worked-example workbooks into the YAML fixtures the tests read (§3.3)."


def find_workbooks(root: str) -> List[str]:
    """All example workbooks below `root`, sorted; template and lock files are skipped.

    Discovery is purely by convention — one `.xlsx` per example anywhere under the group
    directories — so adding an example means adding a file, with no registry to update and no
    chance of an example silently not being converted. Names starting with `_` (the shared
    `_template.xlsx`) and Excel's `~$` lock files are excluded, and the result is sorted so that
    the converter's output order does not depend on the filesystem.

    This is the single owner of that predicate: `tests/test_worked_examples.py` calls it to assert
    that every workbook has a generated fixture, rather than re-deriving the same walk. A test
    importing the tool is the right direction — the tool must not import `hisim`, the tests may.
    """
    workbooks = []
    for directory, _, file_names in os.walk(root):
        for file_name in sorted(file_names):
            if not file_name.endswith(Cli.WORKBOOK_SUFFIX) or file_name.startswith(Cli.SKIPPED_PREFIXES):
                continue
            workbooks.append(os.path.join(directory, file_name))
    return sorted(workbooks)


def yaml_path_of(workbook_path: str) -> str:
    """The generated fixture that belongs next to `workbook_path`.

    One line, but it is the pairing rule itself — same directory, same stem, `.yaml` instead of
    `.xlsx` — and both the writer and the drift check have to agree on it.
    """
    return os.path.splitext(workbook_path)[0] + Cli.YAML_SUFFIX


def _drift_of(workbook_path: str, root: str, text: str) -> Optional[str]:
    """Compares the regenerated text against the committed fixture; a message if they differ.

    Byte-for-byte on purpose (§3.6): this is what closes both silent failure modes at once — an
    xlsx edited without regenerating, and a YAML hand-edited to make a test pass.

    Returns:
        `None` if the committed file is exactly the regenerated text, otherwise a one-line
        description naming the file relative to `root`.
    """
    path = yaml_path_of(workbook_path)
    relative = os.path.relpath(path, root)
    if not os.path.isfile(path):
        return f"{relative}: missing (run the converter)"
    with open(path, encoding="utf-8") as handle:
        committed = handle.read()
    if committed != text:
        return f"{relative}: differs from the workbook"
    return None


def run(root: str, check: bool, list_unreviewed: bool) -> int:
    """Converts (or verifies) every workbook below `root`; returns the process exit code.

    The one driver behind all three modes of the tool: writing the YAML fixtures (the authoring
    step), verifying them without touching the tree (`--check`, the CI drift gate of §3.6), and
    reporting attestation state (`--list-unreviewed`, §3.8). Every workbook is converted in all
    three modes — the difference is only what is done with the result.

    Failures are collected rather than raised, and *every* exception type is collected, not only
    `ValidationError`: openpyxl refusing a corrupt file, or an `OSError` on write, used to abort
    the loop and silently skip every later workbook, which broke this function's own promise to
    report each broken example in one pass. The summary therefore also states how many workbooks
    were actually converted, so a partial run cannot read as a clean one.

    Args:
        root: Directory tree holding the group sub-directories with the workbooks.
        check: Compare the regenerated text against the committed YAML instead of writing it.
        list_unreviewed: Print the unreviewed/stale examples and write nothing.

    Returns:
        `Cli.EXIT_OK`, `Cli.EXIT_DRIFT` if no workbook was found or committed YAML drifted, or
        `Cli.EXIT_INVALID` if any workbook failed. Non-zero is what makes the CI job fail.
    """
    workbooks = find_workbooks(root)
    if not workbooks:
        print(f"No worked-example workbooks found below {root}.")
        return Cli.EXIT_DRIFT
    failures: List[str] = []
    drifted: List[str] = []
    unreviewed: List[str] = []
    converted = 0
    attested = 0
    for path in workbooks:
        relative = os.path.relpath(path, root)
        try:
            example, text = convert_workbook(path)
            state, message = review_status(example, text)
            if state is ReviewState.OK:
                attested += 1
            else:
                unreviewed.append(message)
            if check:
                drift = _drift_of(path, root, text)
                if drift is not None:
                    drifted.append(drift)
            elif not list_unreviewed:
                with open(yaml_path_of(path), "w", encoding="utf-8") as handle:
                    handle.write(text)
                print(message)
            converted += 1
        except ValidationError as error:
            failures.append(f"{relative}: {error}")
        except Exception as error:  # noqa: BLE001 - one unconvertible workbook must not hide the rest
            failures.append(f"{relative}: unexpected {type(error).__name__}: {error}")

    if list_unreviewed:
        for message in unreviewed:
            print(message)
        print(f"{len(unreviewed)} of {converted} examples are unreviewed or stale.")
    else:
        # The attestation state belongs in the CI log as one line. It used to be visible only as 25
        # individual per-example warnings in the pytest output, where "how many are attested" — the
        # number that decides when the §3.8 enforcement switch can be flipped — was not observable.
        print(f"{attested} of {converted} examples carry a valid attestation (§3.8).")
    if converted != len(workbooks):
        print(f"{len(workbooks) - converted} of {len(workbooks)} workbooks could not be converted.")
    if failures:
        print("\nValidation failures:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return Cli.EXIT_INVALID
    if drifted:
        print("\nThe committed YAML no longer matches its workbook (§3.6):", file=sys.stderr)
        for drift in drifted:
            print(f"  {drift}", file=sys.stderr)
        print("\nRun: python tools/convert_worked_examples.py", file=sys.stderr)
        return Cli.EXIT_DRIFT
    if check:
        print(f"{converted} worked examples: YAML matches the workbooks.")
    return Cli.EXIT_OK


def default_root() -> str:
    """`tests/worked_examples` of this checkout, resolved from this file's own location.

    Resolved from `__file__` rather than from the working directory so the tool behaves the same
    when run from the repository root (which is what the CI job does), from `tools/`, or from
    anywhere else — including the bare CI checkout that runs the drift check without HiSim
    installed.
    """
    package_directory = os.path.dirname(os.path.abspath(__file__))
    repository_root = os.path.dirname(os.path.dirname(package_directory))
    return os.path.join(repository_root, "tests", "worked_examples")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Command line entry point.

    Parses the three-flag interface and delegates to `run()`.

    Args:
        argv: Argument list for testing; `None` reads `sys.argv`.

    Returns:
        The process exit code from `run()`.
    """
    parser = argparse.ArgumentParser(description=Cli.DESCRIPTION)
    parser.add_argument("root", nargs="?", default=default_root(), help="worked-example directory")
    parser.add_argument("--check", action="store_true", help="verify the committed YAML instead of writing it")
    parser.add_argument("--list-unreviewed", action="store_true", help="list examples without a valid attestation")
    arguments = parser.parse_args(argv)
    return run(arguments.root, arguments.check, arguments.list_unreviewed)
