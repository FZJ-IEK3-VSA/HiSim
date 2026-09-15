"""Parses one workbook against the §3.2 template and converts it into an `Example` (§3.3).

Owns the sheet *layout* and the row-level rules: which all-caps column-A markers open a section,
which metadata rows exist, what a label may look like and must not look like, that value and
tolerance cells carry no number format but `General`, that revenue-type quantities are negative,
and that every expected cell holds either a formula or a note saying where its constant came from.
`convert_workbook` is the only function in the package that opens a workbook, and it is where all
ten validation rules of §3.3 are actually applied to a sheet.

Separate from `formulas.py` (reference rewriting) and `arithmetic.py` (the stale-cache check) so
that this module reads as the template it validates, and from `cli.py` so that converting one
workbook does not depend on how the tool was invoked. Nothing here writes: openpyxl discards cached
formula values on save, and attesting a review has to stay a human action performed inside Excel
with the sheet open (§3.8).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Tokenizer
from openpyxl.utils.cell import coordinate_to_tuple

from tools.worked_examples.arithmetic import cross_check_arithmetic, is_pure_arithmetic
from tools.worked_examples.attestation import FingerprintFormat
from tools.worked_examples.emitter import emit_yaml
from tools.worked_examples.formulas import rewrite_formula
from tools.worked_examples.model import Example, ExpectedRow, MetadataKeys, ValidationError


class WorkbookTemplate:
    """The §3.2 template this module validates a sheet against, in one place.

    Class-scoped rather than a dozen free module constants: every value here is part of one
    contract between the author's spreadsheet and this parser, and a reader checking "what is a
    legal label?" or "which columns must be `General`?" should find the answer without grepping.
    """

    #: Labels, in Excel and YAML alike, are valid Python identifiers in snake case (§3.2).
    LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

    #: Section markers in column A. Uppercase, so they can never collide with a label.
    METADATA_MARKER = "METADATA"
    INPUTS_MARKER = "INPUTS"
    EXPECTED_MARKER = "EXPECTED"
    SECTION_MARKERS = (METADATA_MARKER, INPUTS_MARKER, EXPECTED_MARKER)

    #: Column holding the value (a literal in INPUTS, a formula or noted constant in EXPECTED).
    VALUE_COLUMN = 2
    #: Columns the template defines at all, read as `[A, B, C, D]` per row.
    ROW_WIDTH = 4

    #: Rule 8: worked examples use degenerate bands only, so band label triples are rejected (§3.2).
    #: Only uncertainty vocabulary is caught here — "band" alone is legitimate tariff vocabulary
    #: (a time-of-use band), so the check keys on the _min/_best_estimate/_max triple convention
    #: instead. The legacy _avg spellings stay on the reject list so workbooks authored against the
    #: pre-rename vocabulary are still caught.
    BAND_LABEL_SUFFIXES = ("_min", "_best_estimate", "_max", "_minimum", "_maximum", "_avg", "_average")
    BAND_LABEL_TOKENS = ("uncertainty", "_min_", "_best_estimate_", "_max_", "_avg_")
    #: Rule 8 again, on the value side: `12 / 15 / 19` typed into one cell as a band triple.
    BAND_TRIPLE_PATTERN = re.compile(r"^\s*-?[\d.]+\s*/\s*-?[\d.]+\s*/\s*-?[\d.]+\s*$")

    #: Sign convention (§3.2): these expected quantities are *timeline entries* of revenue type and
    #: must therefore be negative or zero. Subsidy awards are deliberately absent: the solver
    #: reports award amounts as positive magnitudes and only the evaluator mirrors them onto the
    #: timeline, so a blanket "subsidy is negative" rule would be wrong for the `subsidies/` group.
    #: The list holds only tokens that actually occur: `anyway_cost_credit` and `loan_disbursement`
    #: matched no label in any example, and the second contradicted the runner's own
    #: `disbursement_in_euro`, so a reader could not tell whether the rule was inert or wrong.
    REVENUE_LABEL_TOKENS = ("feed_in_revenue", "residual_value")
    #: Float slack when checking that sign, so a value that is zero to round-off still passes.
    REVENUE_SIGN_EPSILON = 1e-9

    #: Rule 10: the only number format a value or tolerance cell may carry. Anything else makes the
    #: number a reviewer *sees* differ from the number that ships: Excel's own PMT wizard, for one,
    #: leaves a `[$$-409]` US-dollar format behind, which displayed euro annuities as dollars in two
    #: workbooks of the first round. `General` is also what keeps the displayed precision honest — a
    #: two-decimal currency format hides exactly the digits the declared tolerance is about.
    GENERAL_NUMBER_FORMAT = "General"
    #: Rule 10 applies to the value column and the tolerance column; the label and note columns
    #: hold text, where a format cannot mislead.
    FORMATTED_COLUMNS = (2, 3)


def _cell_text(value: object) -> str:
    """Trimmed string content of a cell; empty string for blanks.

    Normalizes the two ways a spreadsheet says "nothing here" — an absent cell (`None`) and a cell
    holding only spaces — into one, because section detection and metadata parsing both treat blank
    and missing identically. Non-string cell values are stringified, so a numeric label or a
    date-formatted `review_date` still reaches the caller as text.
    """
    if value is None:
        return ""
    return str(value).strip()


def _defined_name_targets(workbook) -> Dict[str, str]:
    """Maps every workbook-scoped defined name to the coordinate it points at.

    This map is the backbone of the whole converter: it is what lets validation rule 1 ("every
    referenced cell has a name") be enforced and what turns click-and-point formulas such as
    `=B4*B5` into the readable `=principal_in_euro*interest_rate` that ships as the derivation.
    The sheet part and the `$` anchors are stripped because a name's target is compared against
    plain coordinates like `B7` everywhere downstream.
    """
    targets: Dict[str, str] = {}
    for name, definition in workbook.defined_names.items():
        attr_text = str(definition.attr_text or "")
        _, _, reference = attr_text.rpartition("!")
        targets[name] = reference.replace("$", "").upper()
    return targets


def _collect_sections(sheet) -> Dict[str, List[Tuple[int, List[object]]]]:
    """Splits the sheet into its METADATA / INPUTS / EXPECTED row blocks.

    Implements the template layout of §3.2 with the minimum of structure an author can get wrong:
    an all-caps marker in column A opens a section and the next blank column-A cell closes it, so
    the sections may sit anywhere on the sheet and be spaced out freely. Row numbers are carried
    along with the cells because every later error message, and the defined-name check, refer to
    the spreadsheet row the author is looking at.

    Returns:
        One list of `(row_index, [A, B, C, D] values)` per marker, in sheet order.

    Raises:
        ValidationError: If a labeled row appears before any section marker — usually a stray note
            typed into column A, which would otherwise be silently dropped from the fixture.
    """
    sections: Dict[str, List[Tuple[int, List[object]]]] = {
        marker: [] for marker in WorkbookTemplate.SECTION_MARKERS
    }
    current: Optional[str] = None
    for row_index in range(1, sheet.max_row + 1):
        label = _cell_text(sheet.cell(row_index, 1).value)
        if label in WorkbookTemplate.SECTION_MARKERS:
            current = label
            continue
        if not label:
            current = None  # a blank row in column A closes the current section
            continue
        if current is None:
            raise ValidationError(f"row {row_index}: label {label!r} outside any section marker.")
        cells = [sheet.cell(row_index, column).value for column in range(1, WorkbookTemplate.ROW_WIDTH + 1)]
        sections[current].append((row_index, cells))
    return sections


def _check_label(label: str, row_index: int, seen: Dict[str, int]) -> None:
    """Validation rules 2, 6 and 8 for one label.

    Labels are the one identifier that has to line up across three layers — Excel defined name,
    YAML key, Python field name — so a reviewer can trace a quantity through all of them by name
    (§3.2). Hence the identifier pattern (rule 6) and the workbook-wide uniqueness check (rule 2,
    shared between the INPUTS and EXPECTED tables via the mutable `seen` map, which this function
    updates in place). Rule 8 additionally rejects anything that looks like uncertainty-band
    syntax, because worked examples are authored with degenerate bands only.

    Args:
        label: The column-A text of the row.
        row_index: Spreadsheet row, used in the error messages and recorded in `seen`.
        seen: Labels already accepted in this workbook, mapped to their row; extended here.

    Raises:
        ValidationError: If the label is not an identifier, duplicates an earlier row, or carries
            min/best_estimate/max band vocabulary.
    """
    if not WorkbookTemplate.LABEL_PATTERN.match(label):
        raise ValidationError(f"row {row_index}: label {label!r} is not a valid identifier (rule 6).")
    if label in seen:
        raise ValidationError(f"row {row_index}: label {label!r} duplicates row {seen[label]} (rule 2).")
    if label.endswith(WorkbookTemplate.BAND_LABEL_SUFFIXES) or any(
        token in label for token in WorkbookTemplate.BAND_LABEL_TOKENS
    ):
        raise ValidationError(
            f"row {row_index}: label {label!r} looks like uncertainty-band syntax; worked examples "
            "use degenerate bands only (rule 8)."
        )
    seen[label] = row_index


def check_number_formats(sheet, row_index: int, label: str) -> None:
    """Validation rule 10: a value or tolerance cell carries no number format but `General`.

    The rule exists because a leaked format is invisible in the generated YAML and therefore
    survives review: the converter reads the *stored* value, so a euro annuity displayed as
    `$2,344.61` by a `[$$-409]` format inherited from Excel's PMT wizard converts perfectly while
    telling every human who opens the sheet that the library computes dollars. Two workbooks of the
    first round carried exactly that. Restricting the value and tolerance columns to `General` also
    keeps the displayed precision equal to the stored one, so a reviewer re-deriving a figure by
    hand compares against all the digits the tolerance claims.

    Public rather than private because it is the one row-level rule with a defect class that is
    invisible in a diff, so `tests/test_worked_examples_converter.py` pins it directly.

    Args:
        sheet: The formula sheet (formats live on it, not on the cached-value copy).
        row_index: Spreadsheet row of the INPUTS or EXPECTED row being checked.
        label: Label of the row, for the error message.

    Raises:
        ValidationError: If a checked cell carries any other number format.
    """
    for column in WorkbookTemplate.FORMATTED_COLUMNS:
        cell = sheet.cell(row_index, column)
        if cell.value is None:
            continue
        if cell.number_format != WorkbookTemplate.GENERAL_NUMBER_FORMAT:
            raise ValidationError(
                f"row {row_index}: {label!r} has number format {cell.number_format!r} on "
                f"{cell.coordinate}; value and tolerance cells must be formatted "
                f"{WorkbookTemplate.GENERAL_NUMBER_FORMAT!r} so the displayed number is the stored "
                "one (rule 10)."
            )


def _parse_metadata(rows: Sequence[Tuple[int, List[object]]]) -> Dict[str, str]:
    """Reads the metadata block and validates §3.2 completeness plus the §3.8 review triple.

    Metadata is what makes an example reviewable rather than merely runnable: `spec_section` says
    which rule of **`cost_spec.md`** it pins down (that document, not `roadmap/cost-spec-v2.md`,
    whose §3 describes this library — both have a §3.2 and a §3.6 with different meanings),
    `computed_by` says who or which tool produced the numbers, and `description` states the
    derivation in words. All four mandatory fields therefore have to be present before anything is
    emitted. The three review rows are all-or-nothing on purpose — a half-filled attestation is
    ambiguous, and an unparsable fingerprint would silently downgrade a reviewed example to
    "unreviewed" instead of being reported.

    Args:
        rows: The METADATA section rows from `_collect_sections`.

    Returns:
        Every key of `MetadataKeys.ALL`, with unfilled optional rows as empty strings.

    Raises:
        ValidationError: On an unknown or repeated key, a missing mandatory field, a partially
            filled review triple, or a fingerprint not of the form XXXX-XXXX-XXXX.
    """
    metadata: Dict[str, str] = {key: "" for key in MetadataKeys.ALL}
    seen: Dict[str, int] = {}
    for row_index, cells in rows:
        key = _cell_text(cells[0])
        if key not in MetadataKeys.ALL:
            raise ValidationError(
                f"row {row_index}: unknown metadata key {key!r} (expected one of {MetadataKeys.ALL})."
            )
        if key in seen:
            raise ValidationError(f"row {row_index}: metadata key {key!r} appears twice (rule 2).")
        seen[key] = row_index
        metadata[key] = _cell_text(cells[1])
    for key in MetadataKeys.REQUIRED:
        if not metadata[key]:
            raise ValidationError(f"metadata field {key!r} is mandatory and must not be empty (§3.2).")
    filled = [key for key in MetadataKeys.REVIEW if metadata[key]]
    if filled and len(filled) != len(MetadataKeys.REVIEW):
        raise ValidationError(
            f"review attestation is incomplete: {filled} set, "
            f"{[key for key in MetadataKeys.REVIEW if not metadata[key]]} empty. "
            "All three rows are set together or all three stay empty (§3.8)."
        )
    if metadata["reviewed_fingerprint"] and not FingerprintFormat.PATTERN.match(metadata["reviewed_fingerprint"]):
        raise ValidationError(
            f"reviewed_fingerprint {metadata['reviewed_fingerprint']!r} is not of the form XXXX-XXXX-XXXX (§3.8)."
        )
    return metadata


def _named_cell_values(name_by_coordinate: Dict[str, str], value_sheet) -> Dict[str, float]:
    """Cached numeric value of every named single cell, resolved before any row is converted.

    Resolved up front because formulas may reference rows that appear further down the sheet — year
    N's interest refers to year N-1's remaining debt — so a row-by-row lookup would miss half the
    operands the arithmetic cross-check needs. Non-numeric cells (text inputs, blanks) are simply
    absent from the map; `arithmetic_expression` reports them by name if a formula needs one.
    """
    named_values: Dict[str, float] = {}
    for coordinate, name in name_by_coordinate.items():
        if ":" in coordinate:
            continue
        cell_row, cell_column = coordinate_to_tuple(coordinate)
        cell_value = value_sheet.cell(cell_row, cell_column).value
        if isinstance(cell_value, (int, float)) and not isinstance(cell_value, bool):
            named_values[name] = float(cell_value)
    return named_values


def _coordinate_names(source_name: str, name_targets: Dict[str, str]) -> Dict[str, str]:
    """Inverts the name-to-coordinate map, refusing a cell that carries two names (rule 2).

    Two names on one cell make the derivation ambiguous — the same quantity would appear under two
    spellings depending on which name the rewriter happened to pick — so it is rejected rather than
    resolved.

    Raises:
        ValidationError: If any coordinate is the target of more than one defined name.
    """
    name_by_coordinate: Dict[str, str] = {}
    for name, coordinate in name_targets.items():
        if coordinate in name_by_coordinate:
            raise ValidationError(
                f"{source_name}: cell {coordinate} carries two names "
                f"({name_by_coordinate[coordinate]!r} and {name!r}) (rule 2)."
            )
        name_by_coordinate[coordinate] = name
    return name_by_coordinate


def _convert_inputs(
    rows: Sequence[Tuple[int, List[object]]],
    formula_sheet,
    name_by_coordinate: Dict[str, str],
    seen_labels: Dict[str, int],
) -> List[Tuple[str, Union[float, int, str, bool]]]:
    """Reads the INPUTS table: literal values only, each on a named cell.

    Inputs must be literals because an input derived from something invisible is no longer the
    example's declared starting point — the test feeds these values straight back into the engine,
    so a formula here would mean the YAML and the engine disagree about what the example assumes.

    Raises:
        ValidationError: On an unnamed cell (rule 1), a formula, an empty cell, a band triple
            (rule 8), or any label or number-format violation raised by the row-level checks.
    """
    inputs: List[Tuple[str, Union[float, int, str, bool]]] = []
    for row_index, cells in rows:
        label = _cell_text(cells[0])
        _check_label(label, row_index, seen_labels)
        check_number_formats(formula_sheet, row_index, label)
        coordinate = f"B{row_index}"
        if name_by_coordinate.get(coordinate) != label:
            raise ValidationError(
                f"row {row_index}: input {label!r} has no defined name on {coordinate} "
                f"(found {name_by_coordinate.get(coordinate)!r}); every quantity must be named (rule 1)."
            )
        raw = formula_sheet.cell(row_index, WorkbookTemplate.VALUE_COLUMN).value
        if isinstance(raw, str) and raw.startswith("="):
            raise ValidationError(f"row {row_index}: input {label!r} holds a formula; inputs are literal values.")
        if raw is None:
            raise ValidationError(f"row {row_index}: input {label!r} has no value.")
        if isinstance(raw, str) and WorkbookTemplate.BAND_TRIPLE_PATTERN.match(raw):
            raise ValidationError(
                f"row {row_index}: input {label!r} looks like a min/best_estimate/max triple (rule 8)."
            )
        inputs.append((label, raw))
    return inputs


def _convert_expected(
    rows: Sequence[Tuple[int, List[object]]],
    formula_sheet,
    value_sheet,
    name_by_coordinate: Dict[str, str],
    name_targets: Dict[str, str],
    named_values: Dict[str, float],
    seen_labels: Dict[str, int],
) -> List[ExpectedRow]:
    """Reads the EXPECTED table: a cached number, a positive tolerance, and a derivation or a note.

    The cached number is the expectation and the formula is the derivation that makes it auditable,
    which is why both sheets are needed here. Pure-arithmetic formulas are additionally re-evaluated
    in Python against the workbook's own named values (§3.3), and revenue-type quantities have their
    sign checked under the engine's cost-positive/revenue-negative convention (§3.2).

    Raises:
        ValidationError: On an unnamed cell (rule 1), a missing cached value, a non-positive
            tolerance (rule 6), a bare constant without a note (rule 5), a positive revenue-type
            value, a failed arithmetic cross-check, or anything the formula rewriter rejects.
    """
    expected: List[ExpectedRow] = []
    for row_index, cells in rows:
        label = _cell_text(cells[0])
        _check_label(label, row_index, seen_labels)
        check_number_formats(formula_sheet, row_index, label)
        coordinate = f"B{row_index}"
        if name_by_coordinate.get(coordinate) != label:
            raise ValidationError(
                f"row {row_index}: expected value {label!r} has no defined name on {coordinate} "
                f"(found {name_by_coordinate.get(coordinate)!r}) (rule 1)."
            )
        cached = value_sheet.cell(row_index, WorkbookTemplate.VALUE_COLUMN).value
        if not isinstance(cached, (int, float)) or isinstance(cached, bool):
            raise ValidationError(
                f"row {row_index}: expected value {label!r} has no cached number "
                f"(found {cached!r}); recalculate the workbook in Excel/LibreOffice before converting."
            )
        tolerance_raw = cells[2]
        if not isinstance(tolerance_raw, (int, float)) or isinstance(tolerance_raw, bool) or tolerance_raw <= 0:
            raise ValidationError(
                f"row {row_index}: expected value {label!r} needs a positive numeric tolerance in column C "
                f"(found {tolerance_raw!r}) (rule 6)."
            )
        note = _cell_text(cells[3]) or None
        raw_formula = formula_sheet.cell(row_index, WorkbookTemplate.VALUE_COLUMN).value
        derivation: Optional[str] = None
        if isinstance(raw_formula, str) and raw_formula.startswith("="):
            derivation, tokens = rewrite_formula(
                raw_formula, name_by_coordinate, list(name_targets), formula_sheet.title, label
            )
            if is_pure_arithmetic(tokens):
                cross_check_arithmetic(Tokenizer("=" + derivation).items, named_values, float(cached), label)
        elif note is None:
            raise ValidationError(
                f"row {row_index}: expected value {label!r} is a bare constant without a note explaining "
                "where it comes from (rule 5)."
            )
        if any(token in label for token in WorkbookTemplate.REVENUE_LABEL_TOKENS):
            if float(cached) > WorkbookTemplate.REVENUE_SIGN_EPSILON:
                raise ValidationError(
                    f"row {row_index}: {label!r} is a revenue-type timeline quantity and must be negative or "
                    f"zero (cost positive, revenue negative, §3.2), got {cached!r}."
                )
        expected.append(
            ExpectedRow(
                label=label,
                value=float(cached),
                abs_tol=float(tolerance_raw),
                derivation=derivation,
                note=note,
            )
        )
    return expected


def convert_workbook(path: str) -> Tuple[Example, str]:
    """Converts one workbook into an :class:`Example` and its generated YAML text.

    The heart of the tool. The workbook is opened twice — once for formula text and once for the
    cached values Excel/LibreOffice last computed — because the fixture needs both: the cached
    number is the expectation, the formula is the derivation that makes it auditable. Nothing is
    written to disk here; `cli.run()` decides whether the returned text is saved or only compared.

    Args:
        path: Path to the `.xlsx` file; its parent directory name becomes the example's group.

    Returns:
        `(example, yaml_text)` — the parsed example and the exact text that belongs in the `.yaml`
        next to the workbook.

    Raises:
        ValidationError: On any §3.1/§3.2 layout violation (multiple sheets, sheet-scoped names,
            doubly named cells), any of the ten validation rules, a failed arithmetic
            cross-check, a missing cached value, or an empty INPUTS/EXPECTED table.
    """
    source_name = os.path.basename(path)
    group = os.path.basename(os.path.dirname(path))
    formulas = load_workbook(path, data_only=False)
    values = load_workbook(path, data_only=True)
    if len(formulas.worksheets) != 1:
        raise ValidationError(
            f"{source_name}: workbook holds {len(formulas.worksheets)} sheets; one example is one "
            "workbook with one sheet (§3.1)."
        )
    formula_sheet = formulas.worksheets[0]
    value_sheet = values[formula_sheet.title]

    name_targets = _defined_name_targets(formulas)
    if formula_sheet.defined_names:
        raise ValidationError(
            f"{source_name}: sheet-scoped defined names {sorted(formula_sheet.defined_names)} found; names "
            "must be workbook-scoped (§3.1)."
        )
    name_by_coordinate = _coordinate_names(source_name, name_targets)

    sections = _collect_sections(formula_sheet)
    metadata = _parse_metadata(sections[WorkbookTemplate.METADATA_MARKER])
    file_stem = os.path.splitext(source_name)[0]
    if metadata["name"] != file_stem:
        raise ValidationError(
            f"{source_name}: metadata name {metadata['name']!r} differs from the file stem "
            f"{file_stem!r}; they address the same example everywhere else (test ids, the YAML's "
            "`name`, the spec's cross-references), so they must be the same string (rule 9)."
        )
    example = Example(source_name=source_name, group=group, metadata=metadata)
    seen_labels: Dict[str, int] = {}
    named_values = _named_cell_values(name_by_coordinate, value_sheet)

    example.inputs.extend(
        _convert_inputs(sections[WorkbookTemplate.INPUTS_MARKER], formula_sheet, name_by_coordinate, seen_labels)
    )
    example.expected.extend(
        _convert_expected(
            sections[WorkbookTemplate.EXPECTED_MARKER],
            formula_sheet,
            value_sheet,
            name_by_coordinate,
            name_targets,
            named_values,
            seen_labels,
        )
    )

    if not example.inputs:
        raise ValidationError(f"{source_name}: the INPUTS table is empty.")
    if not example.expected:
        raise ValidationError(f"{source_name}: the EXPECTED table is empty.")
    return example, emit_yaml(example)
