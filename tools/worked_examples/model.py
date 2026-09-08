"""The objects one converted worked example is held in, and the error type the tool raises.

This is the module every other module of the package imports, which is exactly why it exists:
`workbook.py` fills these dataclasses, `emitter.py` renders them, `attestation.py` reads their
metadata and `cli.py` catches this error type per workbook. Giving them a shared home keeps the
package's import graph a straight line (model → attestation/emitter → arithmetic/formulas →
workbook → cli) rather than a cycle.

Nothing here opens a file, parses a sheet or renders text. The only logic in this module is the
invariant each dataclass declares about itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union


class ValidationError(Exception):
    """A workbook violates the authoring conventions (§3.2) or a validation rule (§3.3).

    The only error type this tool raises for author mistakes, so that `cli.run()` can catch it per
    workbook, report every offending example in one pass and still exit non-zero. Its message
    always names the row (or the label) and the rule that was broken, because the reader is the
    author of the spreadsheet, not a Python developer.
    """


class MetadataKeys:
    """The METADATA rows the template defines, and which of them are mandatory (§3.2).

    Class-scoped rather than free module constants because the three tuples describe one thing —
    the shape of `Example.metadata` — and they are read from three modules: the parser validates
    against them, the emitter renders the first five, and the attestation layer reads the last
    three. Keeping the key names in one place is what lets a reader of any of those modules find
    the authoritative list.

    `spec_section` names a subsection of **`cost_spec.md`**, the domain specification — not of
    `roadmap/cost-spec-v2.md`, whose §3 describes this library itself. The two documents both have
    a §3.2 and a §3.6 with different meanings, so the distinction matters when a reviewer follows
    the field to find the rule an example pins down.
    """

    ALL = (
        "name",
        "spec_section",
        "computed_by",
        "description",
        "reviewed_by",
        "review_date",
        "reviewed_fingerprint",
    )
    #: Mandatory: an example without these four cannot be reviewed, only run.
    REQUIRED = ("name", "spec_section", "computed_by", "description")
    #: The §3.8 review triple: all three are filled together or all three stay empty.
    REVIEW = ("reviewed_by", "review_date", "reviewed_fingerprint")


@dataclass
class ExpectedRow:
    """One row of the Expected table: a value, its tolerance and how it was derived.

    The unit of assertion of the whole library: `tests/test_worked_examples.py` runs the engine on
    the example's inputs and compares its result for `label` against `value` within `abs_tol`.
    Carrying `derivation` (the formula rewritten into named quantities) and `note` alongside the
    number is what makes a changed expectation reviewable — the diff shows both what changed and
    how it was computed (§3.4).
    """

    label: str
    value: float  # the cached spreadsheet value, in the quantity's own unit
    abs_tol: float  # absolute tolerance declared by the author in column C; never guessed
    derivation: Optional[str] = None  # Excel formula in named quantities; None for noted constants
    note: Optional[str] = None

    def __post_init__(self) -> None:
        """Rejects the tolerance that validation rule 6 exists to exclude, at the type level.

        A blank or zeroed column C is the exact author mistake rule 6 guards, and until this check
        existed only the one construction site in `convert_workbook` excluded it — an
        `ExpectedRow(abs_tol=0.0)` was a legal instance whose invalid tolerance surfaced later, in
        the emitter, as an error about a value rather than about the row. A non-finite tolerance is
        rejected for the same reason: `nan <= 0` and `inf <= 0` are both False, so a comparison
        against zero alone lets both through.

        `ValidationError` rather than `ValueError` because every caller of this class sits under
        `cli.run()`'s per-workbook handler, which reports one broken example and continues.

        Raises:
            ValidationError: If `abs_tol` is not a finite positive number.
        """
        if not math.isfinite(self.abs_tol) or self.abs_tol <= 0:
            raise ValidationError(
                f"{self.label}: abs_tol must be a finite positive number, got {self.abs_tol!r} (rule 6)."
            )


@dataclass
class Example:
    """One converted workbook, ready to be emitted as YAML.

    The in-memory form of exactly one worked example — one workbook, one sheet, one YAML file
    (§3.1) — after parsing and validation but before rendering. `group` is the containing directory
    name (`financing`, `tariffs`, `subsidies`, `discounting`, `end_to_end`) and is not decoration:
    the test collector maps it to the engine entry point the example is run against (§3.5).
    """

    source_name: str  # workbook file name, echoed into the generated header
    group: str  # parent directory name = the calculator the example exercises
    metadata: Dict[str, str]  # every MetadataKeys.ALL entry, missing optional ones as ""
    inputs: List[Tuple[str, Union[float, int, str, bool]]] = field(default_factory=list)
    expected: List[ExpectedRow] = field(default_factory=list)
