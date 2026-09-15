"""The Excel-to-Python arithmetic translator behind the §3.3 stale-cache cross-check.

A workbook's expected values are the numbers Excel or LibreOffice last *cached*, so a sheet whose
input was edited and saved without a recalculation would export stale expectations that look
perfectly authoritative. Wherever a formula is simple enough to redo independently — `+ - * / ^ %`
over named cells and literal numbers — this module recomputes it in Python and compares, which
catches exactly that, at conversion time rather than in a test failure nobody can interpret.

It is its own module because it is the one part of the converter that is a *language translation*
rather than a file format. The two languages disagree about how a leading `-` binds against `^`,
and about what Excel's postfix `%` attaches to; getting either wrong turns the check into a false
accusation ("the workbook was most likely edited without recalculating it") against a workbook that
is perfectly fresh. Nothing here touches a workbook, a path or the YAML: it takes a token stream
and a value map and returns a number.
"""

from __future__ import annotations

import ast
import operator
from typing import Dict, List, Optional, Sequence

from openpyxl.formula.tokenizer import Token

from tools.worked_examples.model import ValidationError


class ExcelArithmetic:
    """The operator vocabulary the cross-check understands, and how close it has to agree.

    Class-scoped because the four values describe one decision — which formulas are simple enough
    to re-derive — and they are read together: `is_pure_arithmetic` filters on `PURE_OPERATORS`,
    `arithmetic_expression` special-cases `POWER` and `PERCENT`, and `_evaluate_node` dispatches
    through `NODE_EVALUATORS`. Anything outside this vocabulary (`PMT`, `NPV`, `SUM`, `MIN`, …) is
    a function-library cell whose cached value is trusted as the independent reference, which is
    the whole point of the library: those are the cells whose Excel implementation is wanted.
    """

    #: Excel's exponentiation operator; Python spells it `**`.
    POWER = "^"
    #: Excel's postfix percent: a division by 100 that binds tighter than anything else.
    PERCENT = "%"
    #: Operators a formula may use and still be re-evaluated.
    PURE_OPERATORS = frozenset({"+", "-", "*", "/", POWER, PERCENT})
    #: Python equivalents of the Excel binary operators, for the tiny AST interpreter.
    NODE_EVALUATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    #: Relative agreement required between the two evaluations. LibreOffice writes full-precision
    #: floats, so the two agree to round-off, not bit-for-bit.
    RELATIVE_TOLERANCE = 1e-9


def is_pure_arithmetic(tokens: Sequence[Token]) -> bool:
    """Whether the formula uses only `+ - * / ^ % ( )` over names and literal numbers (§3.3).

    Decides which expected cells get the arithmetic cross-check. Cells that call into Excel's
    function library (`PMT`, `NPV`, `SUM`, …) deliberately do not: those are exactly the cells
    whose Excel implementation is wanted as the independent reference, so re-deriving them in
    Python would defeat the purpose of the library.

    Excel's postfix `%` counts as pure. It used to fall out here, which meant a formula as ordinary
    as `=30%*40000` was emitted with a derivation but received *no* stale-cache check — the one
    combination the check is supposed to make impossible.
    """
    for token in tokens:
        if token.type == Token.FUNC:
            return False
        if token.type in (Token.ARRAY, Token.SEP):
            return False
        if token.type == Token.OPERAND and token.subtype not in (Token.RANGE, Token.NUMBER):
            return False
        if token.type in (Token.OP_PRE, Token.OP_IN, Token.OP_POST):
            if str(token.value) not in ExcelArithmetic.PURE_OPERATORS:
                return False
    return True


def arithmetic_expression(tokens: Sequence[Token], values: Dict[str, float], label: str) -> str:
    """Translates a pure-arithmetic token stream into an equivalent Python expression.

    Each named operand is substituted by its cached numeric value (via `repr`, so no precision is
    lost in the round trip) and Excel's `^` becomes Python's `**`; everything else is passed
    through verbatim. The result is a string of numbers and operators only — it contains no names
    at all — which is what allows `evaluate_arithmetic` to parse it without any notion of scope.

    **Unary minus is parenthesized, because the two languages disagree about it.** Excel binds a
    leading `-` *tighter* than `^`, so `=-A1^2` means `(-A1)^2`; Python binds `**` tighter than
    unary minus, so the transliterated `-A1**2` would mean `-(A1**2)` — same text, opposite sign
    for an odd power, and a cross-check failure with a message about a stale workbook that is not
    stale. Every prefix `+`/`-` therefore gets a parenthesis that closes as soon as its operand is
    complete (a number, a substituted value, or a balanced parenthesized group), which reproduces
    Excel's binding exactly and is a no-op wherever the two languages already agreed.

    **Postfix `%` is rewritten as a parenthesized division by 100 around its operand**, for the
    same reason. Python has no postfix percent, and appending `/100` to whatever was emitted last
    would misplace it: `=2^30%` means `2^(0.3)` in Excel, while `2**30/100` in Python is a number
    ten billion times larger. The operand a `%` attaches to is therefore tracked as it is emitted —
    a literal, a substituted value, or the group a closing parenthesis just completed — and the `%`
    wraps exactly that span.

    Args:
        tokens: Token stream of the already-rewritten formula (names, not coordinates).
        values: Cached value per defined name, collected up front in `convert_workbook`.
        label: Label of the row, for the error message.

    Returns:
        A Python expression string equivalent to the Excel formula.

    Raises:
        ValidationError: If an operand has no cached numeric value — e.g. a formula referencing a
            text input, which cannot be cross-checked — or if a `%` has no operand to attach to.
    """
    pieces: List[str] = []
    pending_unary: List[int] = []
    group_starts: List[int] = []
    #: Index in `pieces` at which the most recently completed operand or group begins.
    atom_start: Optional[int] = None
    depth = 0

    def close_completed_unaries() -> None:
        """Closes every prefix operator whose operand has just been fully emitted."""
        while pending_unary and pending_unary[-1] == depth:
            pieces.append(")")
            pending_unary.pop()

    for token in tokens:
        value = str(token.value)
        if token.type == Token.OPERAND and token.subtype == Token.RANGE:
            if value not in values:
                raise ValidationError(f"{label}: cannot re-evaluate {value!r}; it has no numeric value.")
            atom_start = len(pieces)
            pieces.append(f"({values[value]!r})")
            close_completed_unaries()
        elif token.type == Token.OPERAND:
            atom_start = len(pieces)
            pieces.append(value)
            close_completed_unaries()
        elif token.type == Token.PAREN:
            if token.subtype == Token.OPEN:
                group_starts.append(len(pieces))
                pieces.append("(")
                depth += 1
            else:
                pieces.append(")")
                depth -= 1
                atom_start = group_starts.pop() if group_starts else None
                close_completed_unaries()
        elif token.type == Token.OP_PRE:
            pieces.append("(" + value)
            pending_unary.append(depth)
        elif token.type == Token.OP_POST and value == ExcelArithmetic.PERCENT:
            if atom_start is None:
                raise ValidationError(f"{label}: a postfix '%' has no operand to apply to.")
            pieces.insert(atom_start, "(")
            pieces.append("/100)")
        elif token.type in (Token.OP_IN, Token.OP_POST):
            pieces.append("**" if value == ExcelArithmetic.POWER else value)
        else:
            pieces.append(" " if token.type == Token.WSPACE else value)
    return "".join(pieces)


def _evaluate_node(node: ast.AST) -> float:
    """Evaluates one node of the arithmetic expression tree; rejects everything else.

    A deliberately tiny interpreter over the four literal/operator node types the cross-check can
    encounter: numeric constants, unary +/-, and the five binary operators of
    `ExcelArithmetic.NODE_EVALUATORS`. Anything else — a name, a call, a comparison, a subscript —
    raises instead of being evaluated, which is what makes reading a workbook safe even though the
    expression text is derived from file content.
    """
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in ExcelArithmetic.NODE_EVALUATORS:
        evaluate = ExcelArithmetic.NODE_EVALUATORS[type(node.op)]
        return float(evaluate(_evaluate_node(node.left), _evaluate_node(node.right)))
    raise ValidationError(f"unsupported element {type(node).__name__} in the arithmetic cross-check.")


def evaluate_arithmetic(expression: str) -> float:
    """Evaluates a numbers-and-operators expression without ever calling `eval` (§3.3).

    Parsing to an AST and walking it with `_evaluate_node` gives the same arithmetic as `eval`
    while making it structurally impossible for spreadsheet content to execute code. The check
    relies on Python's precedence matching Excel's for the supported operators, with two
    differences that `arithmetic_expression` neutralizes by parenthesizing before the text ever
    reaches here: Excel binds unary minus tighter than `^` and Python does not, and Excel's postfix
    `%` has no Python equivalent at all. One difference remains unhandled — Excel's `^` is
    left-associative (`2^3^2` is 64) while Python's `**` is right-associative (512) — and it is
    left that way deliberately: a chained exponentiation surfaces as a cross-check *failure*, never
    as a silently accepted value, which is the conservative direction for a check whose whole
    purpose is catching wrong numbers.
    """
    return _evaluate_node(ast.parse(expression, mode="eval"))


def cross_check_arithmetic(
    derivation_tokens: Sequence[Token],
    named_values: Dict[str, float],
    cached: float,
    label: str,
) -> None:
    """Re-evaluates a pure-arithmetic formula in Python; stale-cache defense of §3.3.

    The converter reads a workbook's *cached* values, which are only as fresh as the last
    recalculation — so an author who edits an input and saves without letting the spreadsheet
    recompute would otherwise export stale expectations. Where the formula is simple enough to redo
    independently, this catches exactly that.

    Every way the re-evaluation can fail becomes a `ValidationError` naming the row, including the
    ones nobody enumerated in advance: a formula as legal as `=rate^5000` raises `OverflowError`,
    which used to escape as a raw traceback that did not even name the workbook it came from.

    Args:
        derivation_tokens: Tokens of the rewritten formula (`Tokenizer("=" + derivation)`).
        named_values: Cached value per defined name of the workbook.
        cached: The cached value of the cell under check.
        label: Label of the row, for the error message.

    Raises:
        ValidationError: If the formula cannot be re-evaluated for any reason, or if the two
            evaluations disagree by more than `ExcelArithmetic.RELATIVE_TOLERANCE` relative to the
            larger magnitude.
    """
    expression = arithmetic_expression(derivation_tokens, named_values, label)
    try:
        recomputed = evaluate_arithmetic(expression)
    except ZeroDivisionError as error:
        raise ValidationError(f"{label}: re-evaluating the formula divides by zero.") from error
    except ValidationError:
        raise  # already names the row and the reason
    except Exception as error:  # noqa: BLE001 - any failure must be reported against this row
        raise ValidationError(
            f"{label}: cannot re-evaluate the formula ({type(error).__name__}: {error})."
        ) from error
    scale = max(1.0, abs(recomputed), abs(cached))
    if abs(recomputed - cached) > ExcelArithmetic.RELATIVE_TOLERANCE * scale:
        raise ValidationError(
            f"{label}: the cached Excel value {cached!r} disagrees with a Python re-evaluation of the "
            f"same formula ({recomputed!r}). The workbook was most likely edited without recalculating "
            "it (§3.3 arithmetic cross-check)."
        )
