"""Unit tests for the worked-example converter's formula arithmetic and cell checks (§3.3).

`tools/convert_worked_examples.py` re-evaluates every pure-arithmetic workbook formula in Python
and refuses to export the example when its own result disagrees with the value Excel cached. That
cross-check is the library's defense against a workbook edited without recalculating, so it is
only as good as its claim to compute *the same expression* — a transliteration that quietly means
something else in Python turns the check into a source of false accusations ("the workbook was
most likely edited without recalculating it") against workbooks that are perfectly fresh.

**What is covered here and why it is a separate file.** `tests/test_worked_examples.py` reads the
generated YAML only and never imports the converter (§3.8): the tool is deliberately free of any
`hisim` import so it can run in a bare environment, and the test module that consumes its output
is the wrong place to reach back into it. These tests therefore load the tool by path and exercise
two things: the Excel→Python translation of an operator precedence the two languages disagree
about, and the two cell-level rules added in the PR-561 review round (rule 9, metadata name equals
the file stem; rule 10, value and tolerance cells carry no number format but `General`) — both of
which guard against a defect that is *invisible in the generated YAML* and therefore cannot be
caught by reading a diff.

**Error class.** A failure here is a *tooling* failure and cannot corrupt a stored number: the
converter refuses to write on a mismatch, so the worst outcome in the field is a workbook that
cannot be converted. It is still worth pinning, because the failure mode it guards is a message
that blames the author for a defect in the tool.
"""

# clean

import importlib.util
import os
import sys

import pytest

pytestmark = pytest.mark.base


def _converter():
    """Loads `tools/convert_worked_examples.py` by path, without importing `tools` as a package.

    The tool sits outside any package (it has no `__init__.py` and is run as a script), and it
    must stay importable with nothing but openpyxl available — loading it from its file keeps
    both properties intact and keeps this module from depending on the working directory. The
    `sys.modules` registration before execution is not optional: the tool defines dataclasses, and
    `@dataclass` resolves its string annotations through `sys.modules[cls.__module__]`.
    """
    if "convert_worked_examples" in sys.modules:
        return sys.modules["convert_worked_examples"]
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools",
        "convert_worked_examples.py",
    )
    spec = importlib.util.spec_from_file_location("convert_worked_examples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _excel_value(formula: str, values=None) -> float:
    """Evaluates one Excel formula the way the converter's cross-check does."""
    from openpyxl.formula.tokenizer import Tokenizer

    converter = _converter()
    tokens = Tokenizer(formula).items
    # The two helpers are exactly the unit under test: the converter's Excel arithmetic is
    # what these cases pin, and it has no public entry point that stops before the file write.
    # pylint: disable=protected-access
    expression = converter._arithmetic_expression(  # noqa: SLF001 — the unit under test
        tokens, values or {}, "test"
    )
    return float(converter._evaluate_arithmetic(expression))  # noqa: SLF001 — the unit under test


class TestExcelUnaryMinusPrecedence:
    """Excel binds a leading `-` tighter than `^`; Python binds `**` tighter than unary minus."""

    @pytest.mark.parametrize(
        "formula, expected",
        [
            ("=-2^2", 4.0),  # Excel: (-2)^2. A naive `-2**2` in Python is -4.
            ("=-2^3", -8.0),  # odd power: the two readings agree only by luck of the sign
            ("=-(1+1)^2", 4.0),  # the operand may be a parenthesized group
            ("=--2^2", 4.0),  # stacked prefixes still bind before the power
            ("=(-2)^2", 4.0),  # already parenthesized in the workbook: unchanged
            ("=2^-2", 0.25),  # a prefix on the exponent side
        ],
    )
    def test_leading_minus_binds_before_the_power(self, formula, expected):
        """The re-evaluation reproduces Excel's reading, not Python's."""
        assert _excel_value(formula) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "formula, expected",
        [
            ("=2-3^2", -7.0),  # binary minus is *looser* than ^ in both languages
            ("=10-2-3", 5.0),
            ("=2+3*4", 14.0),
            ("=(2+3)*4", 20.0),
        ],
    )
    def test_binary_operators_are_untouched(self, formula, expected):
        """Only the prefix form moved: ordinary precedence must not have been re-parenthesized."""
        assert _excel_value(formula) == pytest.approx(expected)

    def test_named_operands_are_substituted_and_still_bind_correctly(self):
        """The same rule applies once a defined name has been replaced by its cached value."""
        assert _excel_value("=-rate^2", {"rate": 3.0}) == pytest.approx(9.0)
        assert _excel_value("=base-rate^2", {"base": 1.0, "rate": 3.0}) == pytest.approx(-8.0)


class TestNumberFormatRule:
    """Rule 10: a value or tolerance cell may carry no number format but `General`.

    A leaked format never reaches the YAML — the converter reads the stored value, not the
    displayed one — so this is the one class of workbook defect a reviewer cannot see in a diff at
    all. Two workbooks of the first round displayed euro annuities as US dollars because Excel's
    PMT wizard left a `[$$-409]` format behind, which is what these cases pin.
    """

    @staticmethod
    def _sheet(number_format: str):
        """A one-row sheet whose B and C cells carry `number_format`."""
        from openpyxl import Workbook

        sheet = Workbook().active
        sheet["A5"] = "annuity_in_euro"
        sheet["B5"] = 2344.61
        sheet["C5"] = 0.01
        sheet["B5"].number_format = number_format
        sheet["C5"].number_format = number_format
        return sheet

    def test_general_is_accepted(self):
        """The format every authored cell is expected to carry raises nothing."""
        converter = _converter()
        # pylint: disable=protected-access
        converter._check_number_formats(  # noqa: SLF001 — the unit under test
            self._sheet("General"), 5, "annuity_in_euro"
        )

    @pytest.mark.parametrize(
        "number_format",
        [
            '[$$-409]#,##0.00;[RED]\\-[$$-409]#,##0.00',  # what Excel's PMT wizard leaves behind
            "0.00",  # hides the digits the declared tolerance is about
            "0.00%",  # a rate displayed as a percentage is a factor of 100 away from its value
        ],
    )
    def test_other_formats_are_rejected(self, number_format):
        """Anything else aborts the conversion, naming the cell and the rule."""
        converter = _converter()
        with pytest.raises(converter.ValidationError, match="rule 10"):
            # pylint: disable=protected-access
            converter._check_number_formats(  # noqa: SLF001 — the unit under test
                self._sheet(number_format), 5, "annuity_in_euro"
            )

    def test_an_empty_cell_carries_no_requirement(self):
        """An EXPECTED row without a note, or a blank column C, must not trip the rule."""
        from openpyxl import Workbook

        sheet = Workbook().active
        sheet["A5"] = "annuity_in_euro"
        sheet["C5"].number_format = "0.00"  # formatted but empty: nothing is displayed
        converter = _converter()
        # pylint: disable=protected-access
        converter._check_number_formats(sheet, 5, "annuity_in_euro")  # noqa: SLF001
