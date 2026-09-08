"""Rewrites a raw Excel formula into one written in defined names (rules 1, 3, 4 and 7).

A click-and-point formula reads `=B4*B5`; what ships as an example's `derivation` must read
`=principal_in_euro*interest_rate`, because the derivation is the part a human reviewer actually
checks. This module performs that rewrite over openpyxl's formula tokenizer — never a regular
expression, so a cell coordinate inside a string literal or inside a function name can not be
mistaken for a reference — and enforces the four validation rules that are properties of a
*reference* rather than of a spreadsheet row: no unnamed cell, no volatile function, no foreign
sheet or workbook, no range spanning an unnamed row.

Separate from `workbook.py` so the reference rules can be read and tested without the sheet layout,
and the layout without them. Nothing here opens a file: the caller supplies the coordinate-to-name
map and the formula text.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

from tools.worked_examples.model import ValidationError


class FormulaRules:
    """The one closed set the formula rewriter checks against (§3.3, rule 3).

    Class-scoped rather than a free module constant so its owner is visible: the rule is about
    functions inside a formula, which is what this module reads.
    """

    #: Rule 3: functions whose result changes between runs destroy cached-value reproducibility.
    VOLATILE = frozenset({"TODAY", "NOW", "RAND", "RANDBETWEEN", "RANDARRAY", "OFFSET", "INDIRECT"})


def _split_reference(reference: str) -> Tuple[Optional[str], str]:
    """Splits `'Sheet'!$B$5` into (sheet, B5); returns (None, ref) for a plain reference.

    Callers need the two halves separately: the sheet part is what rule 4 checks (one example is
    one sheet, so a cross-sheet reference is an error), while the normalized coordinate is what the
    name lookup keys on. Absolute-reference `$` markers and letter case are stripped so that `$B$5`,
    `B$5` and `b5` all resolve to the same cell.
    """
    if "!" not in reference:
        return None, reference.replace("$", "").upper()
    sheet, _, cell = reference.rpartition("!")
    return sheet.strip("'"), cell.replace("$", "").upper()


def _range_coordinates(start: str, end: str) -> List[str]:
    """Every coordinate of a rectangular range, row-major.

    Used only to enforce rule 7: a `SUM(B5:B14)` may be rewritten to
    `SUM(cash_flow_year_1:cash_flow_year_10)` only if *every* cell in between carries a name, so
    the expansion has to be materialized to check them one by one. Endpoints are normalized so the
    range may be written in either direction.
    """
    start_row, start_column = coordinate_to_tuple(start)
    end_row, end_column = coordinate_to_tuple(end)
    coordinates = []
    for row in range(min(start_row, end_row), max(start_row, end_row) + 1):
        for column in range(min(start_column, end_column), max(start_column, end_column) + 1):
            coordinates.append(f"{get_column_letter(column)}{row}")
    return coordinates


def _rewrite_operand(
    reference: str,
    name_by_coordinate: Dict[str, str],
    known_names: Sequence[str],
    sheet_title: str,
    label: str,
) -> str:
    """Rewrites one operand token to defined names, applying rules 1, 4 and 7.

    This is where "no magic cells" is enforced: an operand that resolves to a cell without a
    defined name aborts the conversion, so every quantity in a derivation chain is named, visible
    in the Expected/Inputs tables and traceable by a reviewer. Operands already written as a name
    pass through untouched, which is the normal case for authors following §3.2.

    Args:
        reference: The raw operand text of the token (`B7`, `$B$7`, `'Sheet1'!B7`, `B5:B14`, or an
            already-defined name).
        name_by_coordinate: Coordinate to defined name, from `_defined_name_targets`.
        known_names: All defined names of the workbook, used to detect the pass-through case.
        sheet_title: The example's only sheet; anything else is a rule-4 violation.
        label: Label of the row being converted, for the error messages.

    Returns:
        The operand rewritten in terms of defined names.

    Raises:
        ValidationError: On an external-workbook reference or a foreign sheet (rule 4), a range
            covering an unnamed cell (rule 7), or an unnamed single cell (rule 1).
    """
    if "[" in reference:
        raise ValidationError(f"{label}: external workbook reference {reference!r} (rule 4).")
    if reference in known_names:
        return reference  # already written in terms of a defined name
    sheet, cell = _split_reference(reference)
    if sheet is not None and sheet != sheet_title:
        raise ValidationError(f"{label}: reference to another sheet ({reference!r}); one sheet per example (rule 4).")
    if ":" in cell:
        start, _, end = cell.partition(":")
        for coordinate in _range_coordinates(start, end):
            if coordinate not in name_by_coordinate:
                raise ValidationError(
                    f"{label}: range {reference!r} covers unnamed cell {coordinate}; ranges must consist "
                    "of individually labeled rows (rule 7)."
                )
        return f"{name_by_coordinate[start]}:{name_by_coordinate[end]}"
    if cell not in name_by_coordinate:
        raise ValidationError(f"{label}: formula references cell {reference!r}, which carries no name (rule 1).")
    return name_by_coordinate[cell]


def rewrite_formula(
    formula: str,
    name_by_coordinate: Dict[str, str],
    known_names: Sequence[str],
    sheet_title: str,
    label: str,
) -> Tuple[str, List[Token]]:
    """Rewrites raw references to names and returns (derivation text, token stream).

    Excel surface syntax is preserved on purpose (§3.3): `^`, `%`, `PMT`, `SUM` and friends stay as
    they are, because the spreadsheet formula *is* the human-readable derivation.

    The rewrite runs over `openpyxl`'s formula tokenizer rather than a regular expression, so that
    a cell coordinate inside a string literal or a function name can never be mistaken for a
    reference. The returned token stream is what the caller inspects to decide whether the formula
    is pure arithmetic and can therefore be re-evaluated as a stale-cache check.

    Args:
        formula: The raw cell formula including its leading `=`.
        name_by_coordinate: Coordinate to defined name, from `_defined_name_targets`.
        known_names: All defined names of the workbook.
        sheet_title: Title of the example's only sheet.
        label: Label of the row being converted, for the error messages.

    Returns:
        `(derivation, tokens)` — the formula in named quantities as it will appear in the YAML
        (without the leading `=`, since the tokenizer drops it), and the raw token list.

    Raises:
        ValidationError: On a volatile function (rule 3), or via `_rewrite_operand` on rules 1, 4
            and 7.
    """
    tokens = Tokenizer(formula).items
    pieces: List[str] = []
    for token in tokens:
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            function_name = token.value.rstrip("(").upper()
            if function_name in FormulaRules.VOLATILE:
                raise ValidationError(f"{label}: volatile function {function_name}() is not allowed (rule 3).")
            pieces.append(token.value)
        elif token.type == Token.FUNC and token.subtype == Token.CLOSE:
            pieces.append(")")
        elif token.type == Token.PAREN:
            pieces.append("(" if token.subtype == Token.OPEN else ")")
        elif token.type == Token.SEP:
            pieces.append("," if token.subtype == Token.ARG else ";")
        elif token.type == Token.OPERAND and token.subtype == Token.TEXT:
            pieces.append('"' + str(token.value).replace('"', '""') + '"')
        elif token.type == Token.OPERAND and token.subtype == Token.RANGE:
            pieces.append(_rewrite_operand(str(token.value), name_by_coordinate, known_names, sheet_title, label))
        else:
            pieces.append(str(token.value))
    return "".join(pieces), list(tokens)
