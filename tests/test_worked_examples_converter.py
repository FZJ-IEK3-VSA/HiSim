"""Unit tests for the worked-example converter: formula arithmetic, cell rules, CLI and attestation.

`tools/worked_examples/` re-evaluates every pure-arithmetic workbook formula in Python and refuses
to export the example when its own result disagrees with the value Excel cached. That cross-check is
the library's defense against a workbook edited without recalculating, so it is only as good as its
claim to compute *the same expression* — a transliteration that quietly means something else in
Python turns the check into a source of false accusations ("the workbook was most likely edited
without recalculating it") against workbooks that are perfectly fresh.

**What is covered here and why it is a separate file.** `tests/test_worked_examples.py` runs the
examples against the engine; this file tests the tool that produced them. The two are separate
because the failure classes are: a failure there is an *engine* failure against an independent
reference, while a failure here is a *tooling* failure that cannot corrupt a stored number — the
converter refuses to write on a mismatch, so the worst outcome in the field is a workbook that
cannot be converted. It is still worth pinning, because the failure mode it guards is a message
that blames the author for a defect in the tool.

The cases fall into five groups:

* the Excel-to-Python translation of the two constructs the languages disagree about — a leading
  `-` against `^`, and Excel's postfix `%`;
* the two cell-level rules whose defect is *invisible in the generated YAML* and therefore cannot
  be caught by reading a diff (rule 9, metadata name equals the file stem; rule 10, value and
  tolerance cells carry no number format but `General`);
* the tolerance round-trip: what the author declared has to be what the fixture stores;
* the tool's own error handling — every failure has to be reported against the workbook that caused
  it, and one unconvertible workbook must not hide the others;
* the §3.8 attestation surface: the enforcement switch's closed value set, and `--list-unreviewed`.

The tool is imported by its dotted path like any other module. It used to be loaded here with
`importlib.util.spec_from_file_location` because `tools/` was not a package; it is one now, which
is what also lets `tests/test_worked_examples.py` share the tool's fingerprint protocol instead of
re-implementing it. The dependency runs test → tool only: the tool must not import `hisim`, so it
can run in the bare CI job of `.github/workflows/worked-examples.yml`, while a test may import
anything.
"""

# clean

import os
import shutil

import pytest
from openpyxl import Workbook
from openpyxl.formula.tokenizer import Tokenizer

from tools.worked_examples.arithmetic import (
    arithmetic_expression,
    cross_check_arithmetic,
    evaluate_arithmetic,
    is_pure_arithmetic,
)
from tools.worked_examples.attestation import EnforcementMode, read_enforcement_mode
from tools.worked_examples.cli import find_workbooks, main
from tools.worked_examples.emitter import decimals_for_tolerance, format_tolerance
from tools.worked_examples.model import ExpectedRow, ValidationError
from tools.worked_examples.workbook import check_number_formats

pytestmark = pytest.mark.base

#: The committed library, which the CLI cases run against read-only.
WORKED_EXAMPLES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worked_examples")


def _excel_value(formula: str, values=None) -> float:
    """Evaluates one Excel formula the way the converter's cross-check does."""
    expression = arithmetic_expression(Tokenizer(formula).items, values or {}, "test")
    return float(evaluate_arithmetic(expression))


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


class TestExcelPostfixPercent:
    """Excel's postfix `%` divides by 100 and binds tighter than everything else.

    Percent formulas used to be classified as *not* pure arithmetic, which meant a cell as ordinary
    as `=30%*40000` shipped with a derivation but without the stale-cache cross-check — a derivation
    that looks checked and is not. Python has no postfix percent, so supporting it is a rewrite, and
    a misplaced `/100` is exactly the false accusation this file exists to prevent: `=2^30%` is
    `2^0.3` in Excel, while a naive `2**30/100` is ten billion times larger.
    """

    @pytest.mark.parametrize(
        "formula, expected",
        [
            ("=30%", 0.3),
            ("=30%*40000", 12000.0),  # the everyday case: a rate typed as a percentage
            ("=40000*30%", 12000.0),  # the same on the right of the operator
            ("=2^30%", 2.0**0.3),  # % binds tighter than ^, so the exponent is 0.3
            ("=-30%", -0.3),
            ("=-30%*2", -0.6),
            ("=(1+1)%", 0.02),  # the operand may be a parenthesized group
            ("=-(1+1)%", -0.02),
            ("=30%%", 0.003),  # stacked postfixes each divide by 100
            ("=100-30%", 99.7),  # binary minus stays looser than %
        ],
    )
    def test_percent_is_translated_as_a_division_by_100(self, formula, expected):
        """The re-evaluation reproduces Excel's reading of `%`, including its binding."""
        assert _excel_value(formula) == pytest.approx(expected)

    def test_a_named_operand_carries_the_percent_too(self):
        """A `%` on a defined name applies to the substituted value, not to the surrounding term."""
        assert _excel_value("=share%*base", {"share": 30.0, "base": 40000.0}) == pytest.approx(12000.0)

    @pytest.mark.parametrize("formula", ["=30%", "=30%*40000", "=2^30%", "=-(1+1)%"])
    def test_percent_formulas_are_pure_arithmetic(self, formula):
        """They must be *classified* as checkable, or the translation above is never reached."""
        assert is_pure_arithmetic(Tokenizer(formula).items) is True

    @pytest.mark.parametrize("formula", ["=PMT(0.03,10,-20000)", "=SUM(a:b)", '=IF(a>0,"x","y")'])
    def test_function_library_formulas_are_still_excluded(self, formula):
        """Adding `%` must not have widened the check to cells whose Excel result is the reference."""
        assert is_pure_arithmetic(Tokenizer(formula).items) is False


class TestArithmeticFailuresNameTheRow:
    """Every way the re-evaluation can fail must become a `ValidationError` naming the label.

    An arithmetic failure that escapes as a raw traceback does not even name the workbook it came
    from, and — worse — used to abort the whole conversion run, so the 24 workbooks after it went
    unchecked while the tool printed a stack trace about a number.
    """

    def test_an_overflow_is_reported_and_not_raised_raw(self):
        """`=rate^5000` is a legal formula whose Python re-evaluation raises `OverflowError`."""
        with pytest.raises(ValidationError, match="cannot re-evaluate"):
            cross_check_arithmetic(Tokenizer("=base^5000").items, {"base": 2.0}, 0.0, "huge_in_euro")

    def test_a_division_by_zero_keeps_its_own_message(self):
        """The most common author mistake stays distinguishable from an unexpected failure."""
        with pytest.raises(ValidationError, match="divides by zero"):
            cross_check_arithmetic(Tokenizer("=base/zero").items, {"base": 1.0, "zero": 0.0}, 0.0, "ratio")

    def test_a_stale_cached_value_is_reported_as_such(self):
        """The check's actual purpose: the cached number and the recomputed one disagree."""
        with pytest.raises(ValidationError, match="most likely edited without recalculating"):
            cross_check_arithmetic(Tokenizer("=base*2").items, {"base": 10.0}, 25.0, "doubled")


class TestToleranceRoundTrip:
    """A stored `abs_tol` must read back as exactly the number the author declared (§3.4).

    Rendering the tolerance with the value's decimal count silently *changed* it: a declared `0.025`
    was stored as `0.03` — looser than declared — a declared `0.15` as `0.1` — stricter — and
    anything below about 5e-13 as `0.000000000000`, which is `0.0`, a tolerance demanding bit-exact
    equality. The text still has to be plain decimal: `1e-06` is legal YAML 1.2 but parses as a
    *string* under a YAML 1.1 loader, which would turn the collector's numeric comparison into a
    type error rather than a failed assertion.
    """

    @pytest.mark.parametrize(
        "tolerance",
        [0.01, 1e-06, 0.025, 0.15, 1e-13, 1e-20, 0.5, 1.0, 100.0, 2.5e-07, 1.0 / 3.0],
    )
    def test_reading_the_emitted_text_back_yields_the_declared_value(self, tolerance):
        """The round-trip property: `float(format_tolerance(t)) == t`, exactly."""
        assert float(format_tolerance(tolerance)) == tolerance

    @pytest.mark.parametrize("tolerance", [0.01, 1e-06, 1e-13, 1e-20, 2.5e-07, 1.0])
    def test_the_text_is_plain_decimal_with_at_least_one_decimal(self, tolerance):
        """No exponent may reach the file, and the value must still read as a float."""
        text = format_tolerance(tolerance)
        assert "e" not in text.lower()
        assert "." in text
        assert len(text.split(".")[1]) >= 1

    @pytest.mark.parametrize(
        "tolerance, expected",
        [(0.01, "0.01"), (1e-06, "0.000001"), (0.025, "0.025"), (0.15, "0.15"), (1.0, "1.0")],
    )
    def test_the_declared_digits_are_the_emitted_digits(self, tolerance, expected):
        """Pins the exact text, since it is part of 25 committed files and of their fingerprints."""
        assert format_tolerance(tolerance) == expected

    @pytest.mark.parametrize("tolerance", [0.0, -0.01, float("nan"), float("inf")])
    def test_an_unusable_tolerance_is_a_validation_error(self, tolerance):
        """`nan <= 0` and `inf <= 0` are both False, so a zero comparison alone let them through."""
        with pytest.raises(ValidationError, match="finite positive"):
            format_tolerance(tolerance)
        with pytest.raises(ValidationError, match="finite positive"):
            decimals_for_tolerance(tolerance)

    def test_the_value_column_still_follows_the_tolerance(self):
        """Full-precision tolerances must not have widened the *value*'s decimals (§3.3)."""
        assert decimals_for_tolerance(0.01) == 2
        assert decimals_for_tolerance(1e-06) == 6


class TestExpectedRowInvariant:
    """`ExpectedRow` refuses the tolerance that validation rule 6 exists to exclude.

    A blank or zeroed column C is the exact author mistake rule 6 guards, and until the type carried
    the invariant only the one construction site in `convert_workbook` excluded it — an
    `ExpectedRow(abs_tol=0.0)` was a legal object whose invalid tolerance surfaced later, in the
    emitter, as an error about a value rather than about the row.
    """

    @pytest.mark.parametrize("abs_tol", [0.0, -0.01, float("nan"), float("inf")])
    def test_a_non_positive_or_non_finite_tolerance_is_refused(self, abs_tol):
        """Constructing the illegal state fails, naming the label and the rule."""
        with pytest.raises(ValidationError, match="rule 6"):
            ExpectedRow(label="annuity_in_euro", value=1.0, abs_tol=abs_tol)

    def test_a_positive_tolerance_constructs(self):
        """The legal case is unaffected."""
        assert ExpectedRow(label="annuity_in_euro", value=1.0, abs_tol=0.01).abs_tol == 0.01


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
        sheet = Workbook().active
        sheet["A5"] = "annuity_in_euro"
        sheet["B5"] = 2344.61
        sheet["C5"] = 0.01
        sheet["B5"].number_format = number_format
        sheet["C5"].number_format = number_format
        return sheet

    def test_general_is_accepted(self):
        """The format every authored cell is expected to carry raises nothing."""
        check_number_formats(self._sheet("General"), 5, "annuity_in_euro")

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
        with pytest.raises(ValidationError, match="rule 10"):
            check_number_formats(self._sheet(number_format), 5, "annuity_in_euro")

    def test_an_empty_cell_carries_no_requirement(self):
        """An EXPECTED row without a note, or a blank column C, must not trip the rule."""
        sheet = Workbook().active
        sheet["A5"] = "annuity_in_euro"
        sheet["C5"].number_format = "0.00"  # formatted but empty: nothing is displayed
        check_number_formats(sheet, 5, "annuity_in_euro")


class TestWorkbookDiscovery:
    """`find_workbooks` is the single owner of "which files are examples" (§3.1).

    `tests/test_worked_examples.py` imports it to check the xlsx-to-yaml pairing rather than
    repeating the walk, so a change to the convention has one place to happen — and these cases pin
    the two exclusions that a re-implementation is most likely to forget.
    """

    def test_the_committed_library_is_discovered(self):
        """Every group directory contributes, and the count matches the fixtures on disk."""
        workbooks = find_workbooks(WORKED_EXAMPLES_ROOT)
        assert len(workbooks) >= 20
        assert workbooks == sorted(workbooks), "the order must not depend on the filesystem"

    def test_the_shared_template_is_skipped(self):
        """`_template.xlsx` is the file authors copy; converting it would fail validation."""
        names = {os.path.basename(path) for path in find_workbooks(WORKED_EXAMPLES_ROOT)}
        assert "_template.xlsx" not in names

    def test_lock_files_and_underscore_names_are_skipped(self, tmp_path):
        """Excel's `~$…` lock file appears whenever an author has a workbook open."""
        (tmp_path / "real.xlsx").write_bytes(b"")
        (tmp_path / "~$real.xlsx").write_bytes(b"")
        (tmp_path / "_template.xlsx").write_bytes(b"")
        (tmp_path / "notes.txt").write_bytes(b"")
        assert [os.path.basename(path) for path in find_workbooks(str(tmp_path))] == ["real.xlsx"]


class TestEnforcementSwitch:
    """The §3.8 warn/error switch is a closed set, validated on read.

    Its value used to be an unchecked free string compared against `"error"`, so `"ERROR"`, `"eror"`
    or any future third mode left the review gate **disabled** while looking configured. A policy
    file that fails open is worse than no policy file, which is why an unrecognized value is an
    error rather than a fallback.
    """

    @pytest.mark.parametrize("text, mode", [("warn", EnforcementMode.WARN), ("error", EnforcementMode.ERROR)])
    def test_the_accepted_spellings_parse(self, text, mode):
        """Exactly the two documented values, lower case."""
        assert EnforcementMode.parse(text, "enforcement.yaml") is mode

    @pytest.mark.parametrize("text", ["ERROR", "Error", "eror", "fail", "", "strict"])
    def test_anything_else_fails_loudly(self, text):
        """A typo must name the file and the accepted values, not silently disable the gate."""
        with pytest.raises(ValidationError, match="not one of"):
            EnforcementMode.parse(text, "enforcement.yaml")

    def test_the_committed_switch_is_readable(self):
        """The shipped policy file parses; D9 keeps it at `warn` for now."""
        assert read_enforcement_mode(WORKED_EXAMPLES_ROOT) is EnforcementMode.WARN

    def test_a_missing_key_is_refused(self, tmp_path):
        """A misspelled *key* used to fall back to `warn`, undoing an enabled gate by one typo."""
        (tmp_path / "enforcement.yaml").write_text("enforcment: error\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="no 'enforcement' key"):
            read_enforcement_mode(str(tmp_path))


class TestCommandLineModes:
    """The three run modes, on the committed library and on a deliberately broken tree.

    `--check` is the exact command `.github/workflows/worked-examples.yml` runs, and
    `--list-unreviewed` is the tool the coming attestation round needs; neither had a test, and the
    review flagged the second as a caller-less flag. Both are read-only, so they run against the
    real fixtures.
    """

    def test_check_passes_on_the_committed_library(self, capsys):
        """The CI drift gate, invoked the way CI invokes it (§3.6)."""
        assert main(["--check", WORKED_EXAMPLES_ROOT]) == 0
        assert "YAML matches the workbooks" in capsys.readouterr().out

    def test_check_reports_the_attestation_count(self, capsys):
        """The §3.8 state has to be one observable line, not 25 individual pytest warnings."""
        main(["--check", WORKED_EXAMPLES_ROOT])
        assert "examples carry a valid attestation" in capsys.readouterr().out

    def test_list_unreviewed_names_every_unattested_example(self, capsys):
        """The flag prints one line per example plus a count, and writes nothing."""
        assert main(["--list-unreviewed", WORKED_EXAMPLES_ROOT]) == 0
        output = capsys.readouterr().out
        assert "UNREVIEWED" in output
        assert "examples are unreviewed or stale." in output

    def test_an_empty_tree_is_reported_rather_than_silently_succeeding(self, tmp_path, capsys):
        """Zero workbooks means a misdirected root, not a clean run."""
        assert main(["--check", str(tmp_path)]) == 1
        assert "No worked-example workbooks found" in capsys.readouterr().out

    def test_one_unconvertible_workbook_does_not_hide_the_others(self, tmp_path, capsys):
        """A non-`ValidationError` used to abort the loop, leaving every later workbook unchecked.

        openpyxl refusing a corrupt file is the realistic case: the run must still convert the
        healthy workbook, report the broken one by name, and exit non-zero.
        """
        group = tmp_path / "financing"
        group.mkdir()
        source = os.path.join(WORKED_EXAMPLES_ROOT, "financing", "loan_10y_3pct")
        shutil.copy(source + ".xlsx", group / "loan_10y_3pct.xlsx")
        shutil.copy(source + ".yaml", group / "loan_10y_3pct.yaml")
        (group / "corrupt.xlsx").write_bytes(b"not a spreadsheet at all")

        assert main(["--check", str(tmp_path)]) == 2
        captured = capsys.readouterr()
        assert "corrupt.xlsx" in captured.err
        assert "1 of 2 workbooks could not be converted." in captured.out
        assert "0 of 1 examples carry a valid attestation" in captured.out
