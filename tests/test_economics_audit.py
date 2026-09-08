"""The audit layer: the legacy parity harness and the input audit's size bounds (§9.5, §9.7).

Two things live in `hisim/economics/audit.py` and neither is covered by the report goldens, which
render an evaluation and never look at what was audited. Both are load-bearing in the same way:
they exist to catch a *wiring* mistake — a component priced from the wrong field, a size that
cannot be what the author meant, a legacy CSV whose columns moved — and a wiring check that
silently answers "all clear" is worse than none, because the parity report is the evidence base
for the §10 cutover decision and the input audit is the "review one table instead of 46 files"
deliverable.

The three failure modes pinned here are the ones where the harness looked fine and said nothing
true: a multi-unit subject with an investment override, whose new value was reported without its
`count` and so produced a discrepancy that did not exist; a legacy CSV whose columns are no longer
the ones the harness reads, which turned every component into "not in legacy CSV" without a word
in the log; and a flat implausible-size bound that passed the 5,000 kW heat pump §9.5 uses as its
own example of what the audit catches.

**Error class.** A failure here never changes a published number — the audit and the parity report
are diagnostics. It means a diagnostic went blind, so read a failure as "the safety net has a
hole", not as "the costs are wrong".
"""

# clean

import csv
import os
from typing import List, Optional

import pytest

from hisim.economics.audit import AuditThresholds, build_input_audit, write_parity_report
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: Pinned like the golden oracle's: the price basis year decides which device entries are read, so
#: leaving it implicit would let the default-year policy re-baseline what the audit resolves. 2024
#: is the 1:1-migrated legacy vintage, which is the one the parity harness compares against.
PARAMETERS = EconomicParameters(country="DE", price_basis_year=2024)

#: The header the legacy `get_cost_capex` path writes, verbatim. The harness reads four of these
#: columns by name; spelling them all out is what lets a test rename exactly one of them.
LEGACY_HEADER = [
    "Component",
    "Investment [EUR]",
    "Device CO2-footprint [kg]",
    "Subsidy as percentage of investment [-]",
    "Rest-Investment [EUR]",
    "Lifetime [Years]",
    "Investment for simulated period [EUR]",
    "Rest-Investment for simulated period [EUR]",
    "Device CO2-footprint for simulated period [kg]",
]


def _inputs(*facts: SubjectCostFacts, simulation_year: int = 2024) -> EvaluationInputs:
    """The minimal `EvaluationInputs` the audit needs: declared facts and a year.

    Deliberately not the golden oracle's rich brownfield fixture. The audit reads only the declared
    cost facts, the simulation year and the simulated-period fraction, so a fixture carrying
    tenancy, billing and a subsidy context would make these tests depend on data none of them
    exercises — and every change to that fixture would land here.

    Args:
        facts: The declared subjects, in the order the audit will report them.
        simulation_year: The year the price basis is derived from.

    Returns:
        Inputs covering a full simulated year.
    """
    return EvaluationInputs(
        simulation_year=simulation_year,
        simulated_period_fraction=1.0,
        cost_facts=list(facts),
    )


def _heat_pump(
    size: float = 10.0,
    size_unit: Units = Units.KILOWATT,
    count: int = 1,
    override_in_euro: Optional[float] = None,
    lifetime_in_years: Optional[float] = None,
) -> ComponentCostFacts:
    """One declared heat pump, with only the fields a test varies.

    The asset class is held fixed across these tests because none of them is about heat pumps: the
    audit picks its size bound from the declared `size_unit` and its price from the declared
    override or the database entry, neither of which asks what the component is.
    """
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=size,
        size_unit=size_unit,
        count=count,
        investment_cost_override_in_euro=(
            None if override_in_euro is None else UncertainValue.exact(override_in_euro)
        ),
        lifetime_override_in_years=lifetime_in_years,
        override_source="audit unit test" if override_in_euro is not None else None,
    )


def _write_legacy_capex_csv(directory: str, rows: List[List[object]], header: Optional[List[str]] = None) -> str:
    """Writes an `investment_cost_co2_footprint.csv` for the harness to read.

    Semicolon-separated with the legacy header, because the harness reads the real file the legacy
    path writes and a test that hand-rolled a friendlier format would not exercise the reading.

    Args:
        directory: The result directory the harness will look in.
        rows: Data rows, in `LEGACY_HEADER` order.
        header: An alternative header, for the renamed-column case.

    Returns:
        The path written.
    """
    path = os.path.join(directory, "investment_cost_co2_footprint.csv")
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        writer.writerow(header or LEGACY_HEADER)
        writer.writerows(rows)
    return path


def _parity_rows(path: str) -> List[dict]:
    """The written parity report, as dict rows keyed by its header."""
    with open(path, encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter=";"))


class TestParityHarness:
    """§9.7 shadow-mode parity: what the harness reports as the new value, and when it is silent."""

    def test_an_investment_override_is_multiplied_by_the_declared_count(self, tmp_path):
        """Catches a multi-unit override being reported as a discrepancy that does not exist.

        A subject may declare `count` identical devices, and an `investment_cost_override_in_euro`
        states the price of *one* of them — the engine scales it (`calculators/context_resolution`
        does `override.scale(count)`), and so does this harness's database branch. Its override
        branch did not, so every multi-unit subject priced by an override was reported as costing a
        `count`-th of what the engine actually books, i.e. as a DISCREPANCY, in the report the
        cutover decision reads. Two units at 8,000 each are 16,000, and against a legacy CSV
        carrying 16,000 the row has to be clean.
        """
        directory = str(tmp_path)
        _write_legacy_capex_csv(directory, [["HeatPump", 16000.0, 0, 0, 16000.0, 20.0, 800.0, 800.0, 0]])
        inputs = _inputs(
            SubjectCostFacts("HeatPump", _heat_pump(count=2, override_in_euro=8000.0, lifetime_in_years=20.0))
        )

        path = write_parity_report(inputs, CostDatabase(), PARAMETERS, directory)

        assert path is not None
        rows = {row["Figure"]: row for row in _parity_rows(path)}
        assert float(rows["investment"]["New value"]) == pytest.approx(16000.0)
        assert float(rows["investment"]["Delta"]) == pytest.approx(0.0, abs=0.01)
        assert rows["investment"]["Note"] == ""
        # investment / lifetime x simulated fraction, the legacy annualization.
        assert float(rows["investment_for_simulated_period"]["New value"]) == pytest.approx(800.0)
        assert rows["investment_for_simulated_period"]["Note"] == ""

    def test_a_renamed_legacy_column_is_reported_rather_than_swallowed(self, tmp_path, capsys):
        """Catches a legacy column rename turning the parity report into a silent no-op.

        Every row whose columns cannot be parsed used to be skipped by a bare `continue`. Rename one
        column in the legacy writer and the harness parses nothing, reports every component as "not
        in legacy CSV", exits 0 and writes a report that looks complete — while comparing nothing at
        all. The evidence base for the cutover would then accumulate green runs that never checked
        anything. The failure has to be visible in the log, and the report must not read as clean.
        """
        directory = str(tmp_path)
        renamed = [("Investment [Euro]" if name == "Investment [EUR]" else name) for name in LEGACY_HEADER]
        _write_legacy_capex_csv(
            directory, [["HeatPump", 16000.0, 0, 0, 16000.0, 20.0, 800.0, 800.0, 0]], header=renamed
        )
        inputs = _inputs(SubjectCostFacts("HeatPump", _heat_pump(lifetime_in_years=20.0)))

        path = write_parity_report(inputs, CostDatabase(), PARAMETERS, directory)

        logged = capsys.readouterr().out
        assert "Investment [EUR]" in logged and "HeatPump" in logged
        assert "parsed none of the 1 data rows" in logged
        assert path is not None
        rows = _parity_rows(path)
        assert [row["Note"] for row in rows] == ["not in legacy CSV"]
        assert rows[0]["Legacy value"] == ""

    def test_a_missing_legacy_csv_and_an_unreadable_one_are_reported_differently(self, tmp_path, capsys):
        """Catches a broken parity check reading exactly like a disabled one.

        Both cases produce no report, and both used to log "legacy capex CSV not present
        (COMPUTE_CAPEX off)" — so a legacy file this harness can no longer parse was indistinguishable
        from a run that deliberately computed no capex. The first is a defect to fix, the second is
        nothing at all, and telling them apart is the whole value of the message.
        """
        directory = str(tmp_path)
        inputs = _inputs(SubjectCostFacts("HeatPump", _heat_pump(lifetime_in_years=20.0)))

        assert write_parity_report(inputs, CostDatabase(), PARAMETERS, directory) is None
        absent_log = capsys.readouterr().out
        assert "COMPUTE_CAPEX off" in absent_log

        # A file no CSV parser can turn into a table — a ragged row, not merely a renamed column.
        with open(os.path.join(directory, "investment_cost_co2_footprint.csv"), "w", encoding="utf-8") as file:
            file.write("Component;Investment [EUR]\nHeatPump;1\nragged;2;3;4;5\n")

        assert write_parity_report(inputs, CostDatabase(), PARAMETERS, directory) is None
        unreadable_log = capsys.readouterr().out
        assert "COMPUTE_CAPEX off" not in unreadable_log
        assert "could not be read" in unreadable_log


class TestImplausibleSizeBounds:
    """§9.5: the size bound is per unit, because one number is not a heuristic for five quantities."""

    #: `(size unit, the bound above which a declaration is flagged)`. Mirrors
    #: `AuditThresholds.IMPLAUSIBLE_SIZE_BY_UNIT` on purpose — a test that read the table it checks
    #: would pass for any table at all, including an empty one.
    BOUNDS = [
        (Units.KILOWATT, 1000.0),
        (Units.KWH, 1000.0),
        (Units.SQUARE_METER, 1000.0),
        (Units.LITER, 100000.0),
        (Units.ANY, 10000.0),
    ]

    @pytest.mark.parametrize("size_unit, bound", BOUNDS)
    def test_a_size_at_its_bound_is_not_flagged(self, size_unit, bound):
        """The bound is inclusive: a declaration exactly at it is believable and stays unflagged."""
        audit = build_input_audit(
            _inputs(SubjectCostFacts("Device", _heat_pump(size=bound, size_unit=size_unit))),
            CostDatabase(),
            PARAMETERS,
        )

        assert not [flag for flag in audit.rows[0].flags if "implausible" in flag]

    @pytest.mark.parametrize("size_unit, bound", BOUNDS)
    def test_a_size_above_its_bound_is_flagged(self, size_unit, bound):
        """Catches a unit whose bound is so loose that nothing in it can ever be flagged.

        A single flat bound of 10,000 meant a kW declaration had to be ten times an apartment
        block's heating capacity before anyone was told, while a litre declaration was flagged at a
        third of an ordinary heating-oil tank. Each unit is bounded where a residential declaration
        stops being believable, and just above that has to say so, naming the size and its unit.
        """
        audit = build_input_audit(
            _inputs(SubjectCostFacts("Device", _heat_pump(size=bound * 1.01, size_unit=size_unit))),
            CostDatabase(),
            PARAMETERS,
        )

        flags = [flag for flag in audit.rows[0].flags if "implausible" in flag]
        assert len(flags) == 1
        assert size_unit.value in flags[0]

    def test_the_5000_kw_heat_pump_of_the_spec_is_flagged(self):
        """Catches the audit missing the example §9.5 and the report use to explain what it catches.

        "A config-wiring mistake such as a 5000 kW heat pump is an implausible size in this table
        long before it is a surprising NPV" is what section 1 of the HTML report promises its
        reader. Under the old flat bound of 10,000 it was not: the promise was documentation of a
        behaviour that did not exist, which is the worst kind of safety net — one people rely on.
        """
        audit = build_input_audit(
            _inputs(SubjectCostFacts("HeatPump", _heat_pump(size=5000.0))), CostDatabase(), PARAMETERS
        )

        assert any("implausible" in flag for flag in audit.rows[0].flags)

    def test_an_unbounded_unit_falls_back_instead_of_raising(self):
        """A size unit with no bound of its own degrades to the unitless one, never to a KeyError.

        The audit's contract is that a heuristic never breaks a run: it produces flags, never
        errors. A unit added to `ComponentCostFacts.SUPPORTED_SIZE_UNITS` without a bound here must
        therefore fall back to a looser bound rather than raise halfway through writing the report.
        """
        assert AuditThresholds.implausible_size(Units.CELSIUS) == AuditThresholds.IMPLAUSIBLE_SIZE_BY_UNIT[Units.ANY]
