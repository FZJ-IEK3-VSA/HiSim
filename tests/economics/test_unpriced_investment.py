"""A plan that buys an unpriced subject states no figure its investment enters, and says why (renovisorissues #87).

An unpriced subject (an envelope measure without a cost block, a measure HiSim holds no price for) is booked at a
placeholder zero. Before hisim-payback-unpriced-null-58d1 the document stated a payback year over that partial
investment: the backend's Irish 1968 house with unpriced windows paid back in years 1 to 3. By owner decision of
2026-11-13 such a plan states ``null`` in every end of ``comparison.discounted_payback_year``; by owner decision of
2026-11-25 (hisim-npv-unpriced-unknown-a0aa) the same holds for every figure the investment enters -- the totals, the
NPV and its split, the annual and cumulative series, the loans and the differences to the reference -- and the
top-level ``unpriced_investment`` names the subjects. There is no lower bound. The energy, CO2 and per-subject figures
stay. A kept envelope element is unpriced too but belongs to the house as it was, so it withholds nothing.
"""

import json
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

import pytest

from hisim.economics.evaluator import SubjectCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import InvestmentOverride, Stage, StagedEvaluator, StagedResult
from hisim.economics.staged_document import CostGroup, StagedDocument, UnpricedInvestment
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    device_facts,
    state_inputs,
    write_database,
)

pytestmark = pytest.mark.base

#: The unpriced envelope measure of the backend's case, which is also its subject.
WINDOWS = "window_replacement"

#: A kept envelope element: unpriced in the reference and the plan alike, with no measure.
KEPT_DOOR = "envelope_door"

#: A heat pump cheap enough that the plan pays back within the synthetic horizon.
HEAT_PUMP_INVESTMENT_IN_EURO = 3000.0


def _row(subject: str, measure_id: Optional[str], unpriced: bool) -> Dict[str, Any]:
    """The three fields of a ``by_subject`` row the rule reads."""
    return {"subject": subject, "measure_id": measure_id, "unpriced": unpriced}


def _unpriced_facts(subject: str, asset_class: ComponentType) -> SubjectCostFacts:
    """A subject booked at a placeholder zero, as the translator books an envelope subject without a price."""
    facts = replace(device_facts(asset_class, 20.0, 0.0), size_unit=Units.SQUARE_METER, price_is_unknown=True)
    return SubjectCostFacts(subject, facts)


def _stage(windows_in_euro: Optional[float], kept_door: bool = False) -> Stage:
    """Stage 1 in year 0: a heat pump replaces the boiler and the windows are replaced.

    Args:
        windows_in_euro: The windows' price; None books them at a placeholder zero, unpriced.
        kept_door: Whether the kept door is a subject of the stage.

    Returns:
        The stage.
    """
    subjects = [(SyntheticPlan.HEAT_PUMP_SUBJECT, ComponentType.HEAT_PUMP, 9.0, HEAT_PUMP_INVESTMENT_IN_EURO)]
    if windows_in_euro is not None:
        subjects.append((WINDOWS, ComponentType.WINDOWS_TRIPLE_GLAZED, 20.0, windows_in_euro))
    inputs = state_inputs(subjects, SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH)
    # The heat pump's electricity is metered, so the plan states a levelized cost of heat.
    inputs = replace(
        inputs,
        energy_attribution_by_subject_in_kwh={
            SyntheticPlan.HEAT_PUMP_SUBJECT: {"HEAT_PUMP_ELECTRICITY": 3000.0},
            "Occupancy": {"HOUSEHOLD_ELECTRICITY": 1000.0},
        },
    )
    if windows_in_euro is None:
        inputs.cost_facts.append(_unpriced_facts(WINDOWS, ComponentType.WINDOWS_TRIPLE_GLAZED))
    if kept_door:
        inputs.cost_facts.append(_unpriced_facts(KEPT_DOOR, ComponentType.WINDOWS_TRIPLE_GLAZED))
    return Stage(inputs=inputs, from_year=0, label="stage 1", measures=("heating_system", WINDOWS), job_id="job-1")


def _baseline(kept_door: bool = False) -> Stage:
    """Stage 0, the house as it is, with or without the kept door as a subject."""
    stage = baseline_stage()
    if kept_door:
        stage.inputs.cost_facts.append(_unpriced_facts(KEPT_DOOR, ComponentType.WINDOWS_TRIPLE_GLAZED))
    return stage


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("unpriced_investment_database")))


@pytest.fixture(name="parameters")
def fixture_parameters() -> EconomicParameters:
    """The assumptions the synthetic plan is priced under."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
    )


def _write(result: StagedResult, parameters: EconomicParameters, path, unpriced: Sequence[str]) -> Dict[str, Any]:
    """Write the plan's document with the translator's measure map and read it back from disk."""
    StagedDocument(
        result,
        parameters,
        brownfield_perspective(),
        measure_ids={SyntheticPlan.HEAT_PUMP_SUBJECT: "heating_system", WINDOWS: WINDOWS, KEPT_DOOR: None},
        unpriced_subjects=unpriced,
    ).write(path)
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


class TestTheUnpricedSubjects:
    """`UnpricedInvestment.subjects` picks the unpriced rows the plan buys."""

    def test_an_unpriced_measure_counts(self):
        """A row with a measure and no price withholds the payback."""
        assert UnpricedInvestment.subjects([_row(WINDOWS, WINDOWS, True)], has_measure_map=True) == [WINDOWS]

    def test_a_kept_element_does_not_count(self):
        """An unpriced row without a measure is the house as it was, renewed alike in the reference."""
        assert UnpricedInvestment.subjects([_row(KEPT_DOOR, None, True)], has_measure_map=True) == []

    def test_a_priced_row_does_not_count(self):
        """A measure with a price, or one a reader's quote priced (its row says unpriced false)."""
        assert UnpricedInvestment.subjects([_row("HeatPump", "heating_system", False)], has_measure_map=True) == []

    def test_without_a_measure_map_every_unpriced_row_counts(self):
        """No map, no telling a measure from a kept element: the payback is withheld rather than guessed."""
        assert UnpricedInvestment.subjects([_row(KEPT_DOOR, None, True)], has_measure_map=False) == [KEPT_DOOR]

    def test_the_subjects_are_sorted(self):
        """The reason lists them in a stable order, so two runs write the same bytes."""
        rows = [_row("roof_insulation", "roof_insulation", True), _row(WINDOWS, WINDOWS, True)]
        assert UnpricedInvestment.subjects(list(reversed(rows)), has_measure_map=True) == ["roof_insulation", WINDOWS]

    def test_a_subject_with_two_rows_is_named_once(self):
        """The reason joins the plan's and the reference's rows, so a subject in both is listed once."""
        rows = [_row(WINDOWS, WINDOWS, True), _row(WINDOWS, WINDOWS, True)]
        assert UnpricedInvestment.subjects(rows, has_measure_map=True) == [WINDOWS]


class TestTheReason:
    """`UnpricedInvestment.reason` is the machine-readable reason, or None."""

    def test_nothing_unpriced_gives_no_reason(self):
        """A plan that buys nothing unpriced states its payback and no reason."""
        assert UnpricedInvestment.reason([_row("HeatPump", "heating_system", False)], []) is None

    def test_the_reason_names_the_subjects_and_their_measures(self):
        """Code, subjects, the distinct measures of their rows, and a sentence naming the subjects."""
        rows: List[Dict[str, Any]] = [_row(WINDOWS, WINDOWS, True), _row("HeatPump", "heating_system", False)]
        reason = UnpricedInvestment.reason(rows, [WINDOWS])
        assert reason is not None
        assert reason["code"] == UnpricedInvestment.CODE == "unpriced_subjects"
        assert reason["unpriced_subjects"] == [WINDOWS]
        assert reason["measure_ids"] == [WINDOWS]
        assert WINDOWS in reason["note"]

    def test_a_subject_without_a_measure_adds_no_measure(self):
        """Without a measure map a kept element can withhold the payback; it names no measure."""
        reason = UnpricedInvestment.reason([_row(KEPT_DOOR, None, True)], [KEPT_DOOR])
        assert reason is not None and reason["measure_ids"] == []


def _band(value: float) -> Dict[str, float]:
    """A band of three equal ends."""
    return {"min": value, "best": value, "max": value}


def _as_band(value: UncertainValue) -> Dict[str, float]:
    """An engine band as the document writes it."""
    return {"min": value.minimum, "best": value.best_estimate, "max": value.maximum}


def _evaluation_block() -> Dict[str, Any]:
    """A small evaluation block in the document's shape, with one loan, for the withholding functions."""
    stack = {group.value: _band(1.0) for group in CostGroup}
    return {
        "totals": {"npv_in_euro": _band(100.0), "sunk_cost_written_off_in_euro": _band(5.0)},
        "by_group": dict(stack),
        "by_category": {"INVESTMENT": _band(80.0), "ENERGY_WORKING": _band(20.0), "ENERGY_CO2_PRICE": _band(2.0)},
        "by_payer": {"OWNER": _band(100.0)},
        "by_subject": [_row(WINDOWS, WINDOWS, True)],
        "annual": [
            {"year": 0, "total_nominal_in_euro": _band(80.0), "total_discounted_in_euro": _band(80.0),
             "by_group": dict(stack), "events": [{"kind": "investment", "subject": WINDOWS}]},
        ],
        "cumulative": [{"year": 0, "nominal_in_euro": _band(80.0), "discounted_in_euro": _band(80.0)}],
        "energy_year1": [{"carrier": "ELECTRICITY", "cost_in_euro": _band(20.0)}],
        "co2": {"lifecycle_in_kg": _band(900.0)},
        "subsidies": [],
        "financing": {"loans": [{"stage": 1, "principal_in_euro": _band(80.0), "rate": 0.03, "term_years": 10,
                                 "debt_service_by_year_in_euro": [_band(0.0), _band(9.4)]}]},
    }


class TestTheKeptTotals:
    """`UnpricedInvestment.kept_totals` keeps the levelized cost of heat unless a heat subject is unpriced."""

    def test_unpriced_windows_leave_the_levelized_cost_of_heat_stated(self):
        """Windows are no heat cost, so their unknown price does not enter the figure."""
        rows = [{**_row(WINDOWS, WINDOWS, True), "asset_class": ComponentType.WINDOWS_TRIPLE_GLAZED.value}]
        assert UnpricedInvestment.kept_totals(rows, [WINDOWS]) == UnpricedInvestment.PRICE_FREE_TOTALS | {
            UnpricedInvestment.HEAT_COST_TOTAL
        }

    def test_an_unpriced_buffer_withholds_it(self):
        """A heat storage is a heat cost: its unknown price makes the figure unknown."""
        rows = [{**_row("Buffer", "heating_system", True), "asset_class": ComponentType.BUFFER.value}]
        assert UnpricedInvestment.kept_totals(rows, ["Buffer"]) == UnpricedInvestment.PRICE_FREE_TOTALS

    def test_a_measure_without_an_asset_class_is_no_heat_cost(self):
        """A measure HiSim holds no class for (the hot-water lagging) is not counted, priced or not."""
        rows = [{**_row("lagging", "lagging", True), "asset_class": None}]
        assert UnpricedInvestment.HEAT_COST_TOTAL in UnpricedInvestment.kept_totals(rows, ["lagging"])


class TestTheWithheldGroups:
    """`UnpricedInvestment.withheld_groups` keeps the energy and CO2 stacks and withholds the rest."""

    def test_only_energy_and_co2_stay(self):
        """A group an investment enters (replacements, subsidies, ...) is None; the order is kept."""
        stack = {group.value: _band(1.0) for group in CostGroup}
        withheld = UnpricedInvestment.withheld_groups(stack)
        assert list(withheld) == list(stack)
        assert {group for group, band in withheld.items() if band is not None} == {
            CostGroup.ENERGY.value,
            CostGroup.CO2.value,
        }

    def test_an_unknown_group_is_refused(self):
        """A key that is no cost group is a bug in the caller, not a group to guess about."""
        with pytest.raises(ValueError):
            UnpricedInvestment.withheld_groups({"Heat": _band(1.0)})


class TestTheWithheldEvaluation:
    """`UnpricedInvestment.withheld_evaluation` nulls every figure the investment enters and nothing else."""

    def test_the_totals_but_the_sunk_cost_are_null(self):
        """The NPV contains the unknown investment; the book value written off does not."""
        totals = UnpricedInvestment.withheld_evaluation(_evaluation_block(), UnpricedInvestment.PRICE_FREE_TOTALS)["totals"]
        assert totals == {"npv_in_euro": None, "sunk_cost_written_off_in_euro": _band(5.0)}

    def test_the_npv_split_keeps_only_the_energy_and_co2_categories(self):
        """By category the energy and carbon-price entries stay; by payer every entry mixes the investment in."""
        withheld = UnpricedInvestment.withheld_evaluation(_evaluation_block(), UnpricedInvestment.PRICE_FREE_TOTALS)
        assert withheld["by_category"] == {
            "INVESTMENT": None,
            "ENERGY_WORKING": _band(20.0),
            "ENERGY_CO2_PRICE": _band(2.0),
        }
        assert withheld["by_payer"] == {"OWNER": None}
        assert withheld["by_group"][CostGroup.INVESTMENT_AND_FINANCING.value] is None

    def test_the_series_and_loans_lose_their_money_and_keep_their_years(self):
        """Every annual total, cumulative row and loan amount is null; years, events and loan terms stay."""
        withheld = UnpricedInvestment.withheld_evaluation(_evaluation_block(), UnpricedInvestment.PRICE_FREE_TOTALS)
        year = withheld["annual"][0]
        assert (year["total_nominal_in_euro"], year["total_discounted_in_euro"]) == (None, None)
        assert year["by_group"][CostGroup.ENERGY.value] == _band(1.0)
        assert year["events"] == [{"kind": "investment", "subject": WINDOWS}]
        assert withheld["cumulative"] == [{"year": 0, "nominal_in_euro": None, "discounted_in_euro": None}]
        loan = withheld["financing"]["loans"][0]
        assert (loan["principal_in_euro"], loan["debt_service_by_year_in_euro"]) == (None, [None, None])
        assert (loan["stage"], loan["rate"], loan["term_years"]) == (1, 0.03, 10)

    def test_rows_energy_co2_and_subsidies_stay_and_the_input_is_unchanged(self):
        """The per-subject rows and the price-free blocks are kept as they were; a cash purchase stays None."""
        block = _evaluation_block()
        withheld = UnpricedInvestment.withheld_evaluation(block, UnpricedInvestment.PRICE_FREE_TOTALS)
        for key in ("by_subject", "energy_year1", "co2", "subsidies"):
            assert withheld[key] == block[key]
        assert block["totals"]["npv_in_euro"] == _band(100.0)
        assert UnpricedInvestment.withheld_evaluation({**block, "financing": None}, frozenset())["financing"] is None


class TestTheWithheldComparison:
    """`UnpricedInvestment.withheld_comparison` keeps the CO2 difference and withholds every other one."""

    def test_the_differences_the_payback_and_the_cumulative_rows(self):
        """Money differences are None, the payback is null in every end, the CO2 difference stays."""
        comparison = {
            "npv_delta_in_euro": _band(-100.0),
            "monthly_cost_year1_delta_in_euro": None,
            "discounted_payback_year": {"min": 3, "best": 4, "max": 6},
            "cumulative_delta": [{"year": 0, "nominal_in_euro": _band(80.0), "discounted_in_euro": _band(80.0)}],
            "lifecycle_co2_delta_in_kg": _band(-5000.0),
            "warm_rent_change_in_euro_per_m2_month": _band(0.4),
        }
        withheld = UnpricedInvestment.withheld_comparison(comparison)
        assert list(withheld) == list(comparison)
        assert withheld == {
            "npv_delta_in_euro": None,
            "monthly_cost_year1_delta_in_euro": None,
            "discounted_payback_year": {"min": None, "best": None, "max": None},
            "cumulative_delta": [{"year": 0, "nominal_in_euro": None, "discounted_in_euro": None}],
            "lifecycle_co2_delta_in_kg": _band(-5000.0),
            "warm_rent_change_in_euro_per_m2_month": None,
        }
        assert comparison["npv_delta_in_euro"] == _band(-100.0)

    def test_a_difference_not_declared_price_free_is_withheld(self):
        """A figure added to the comparison later is withheld until someone declares it free of the investment."""
        assert UnpricedInvestment.withheld_comparison({"some_new_delta_in_euro": _band(1.0)}) == {
            "some_new_delta_in_euro": None
        }


#: The figures of `totals` an unknown investment withholds.
WITHHELD_TOTALS = (
    "npv_in_euro",
    "equivalent_annual_cost_in_euro",
    "monthly_equivalent_cost_in_euro",
    "monthly_cost_year1_in_euro",
    "investment_year0_in_euro",
)

#: The differences of `comparison` an unknown investment withholds.
WITHHELD_DIFFERENCES = (
    "npv_delta_in_euro",
    "equivalent_annual_cost_delta_in_euro",
    "monthly_equivalent_cost_delta_in_euro",
    "monthly_cost_year1_delta_in_euro",
    "warm_rent_change_in_euro_per_m2_month",
)


class TestTheDocument:
    """The written document of a plan with unpriced windows (the backend's case of #87), and its priced twins."""

    def test_unpriced_windows_withhold_every_figure_their_investment_enters(self, database, parameters, tmp_path):
        """The engine states an NPV and a payback over the zero; the document states null and names the windows."""
        result = StagedEvaluator(database).evaluate([baseline_stage(), _stage(None)], parameters, brownfield_perspective())
        engine_band = result.comparison.discounted_payback_envelope.to_band()
        assert engine_band["max"] is not None and engine_band["max"] <= 3, engine_band

        document = _write(result, parameters, tmp_path / "unpriced.json", unpriced=[WINDOWS])

        reason = document["unpriced_investment"]
        assert reason["code"] == "unpriced_subjects"
        assert reason["unpriced_subjects"] == [WINDOWS]
        assert reason["measure_ids"] == [WINDOWS]
        plan = document["plan"]
        for key in WITHHELD_TOTALS:
            assert plan["totals"][key] is None, key
        assert plan["totals"]["sunk_cost_written_off_in_euro"] is not None
        # The windows are no heat cost: the levelized cost of heat is the engine's.
        engine_heat_cost = result.plan.levelized_cost_of_heat_in_euro_per_kwh
        assert engine_heat_cost is not None
        assert plan["totals"]["levelized_cost_of_heat_in_euro_per_kwh"] == _as_band(engine_heat_cost)
        comparison = document["comparison"]
        for key in WITHHELD_DIFFERENCES:
            assert comparison[key] is None, key
        assert comparison["discounted_payback_year"] == {"min": None, "best": None, "max": None}
        assert all(row["discounted_in_euro"] is None for row in comparison["cumulative_delta"])
        assert comparison["lifecycle_co2_delta_in_kg"] is not None
        assert plan["by_group"][CostGroup.ENERGY.value] is not None
        assert plan["by_group"][CostGroup.INVESTMENT_AND_FINANCING.value] is None
        assert all(row["total_nominal_in_euro"] is None for row in plan["annual"])
        rows = {row["subject"]: row for row in plan["by_subject"]}
        assert rows[WINDOWS]["unpriced"] is True
        assert rows[SyntheticPlan.HEAT_PUMP_SUBJECT]["investment_in_euro"]["best"] == HEAT_PUMP_INVESTMENT_IN_EURO
        assert plan["energy_year1"] and plan["co2"]["lifecycle_in_kg"] is not None
        # The reference buys nothing unpriced: its figures stand.
        assert document["reference"]["totals"]["npv_in_euro"] is not None

    def test_priced_windows_state_every_figure_as_the_engine_does(self, database, parameters, tmp_path):
        """With every subject priced nothing is withheld: the totals and the payback are the engine's."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), _stage(4000.0)], parameters, brownfield_perspective()
        )

        document = _write(result, parameters, tmp_path / "priced.json", unpriced=[])

        assert document["unpriced_investment"] is None
        assert document["plan"]["totals"]["npv_in_euro"] == _as_band(result.plan.total_npv_in_euro)
        comparison = document["comparison"]
        assert comparison["npv_delta_in_euro"] == _as_band(result.comparison.npv_delta_in_euro)
        assert comparison["discounted_payback_year"] == result.comparison.discounted_payback_envelope.to_band()
        assert comparison["discounted_payback_year"]["best"] is not None
        best = comparison["discounted_payback_year"]["best"]
        discounted = [row["discounted_in_euro"]["best"] for row in comparison["cumulative_delta"]]
        assert all(value <= 0 for value in discounted[best:])
        assert best == 0 or discounted[best - 1] > 0

    def test_a_quote_prices_the_windows_and_nothing_is_withheld(self, database, parameters, tmp_path):
        """A reader's quote is a price: the row says priced, and the figures are stated over the quoted amount."""
        quote = InvestmentOverride(
            stage=1, measure_id=WINDOWS, amount_in_euro=4000.0, source="window quote", main_subject=WINDOWS
        )
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), _stage(None)], parameters, brownfield_perspective(), investment_overrides=[quote]
        )

        document = _write(result, parameters, tmp_path / "quoted.json", unpriced=[WINDOWS])

        assert document["unpriced_investment"] is None
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[WINDOWS]["unpriced"] is False
        assert document["plan"]["totals"]["npv_in_euro"] == _as_band(result.plan.total_npv_in_euro)
        investment = document["plan"]["totals"]["investment_year0_in_euro"]["best"]
        assert investment == pytest.approx(HEAT_PUMP_INVESTMENT_IN_EURO + 4000.0)

    def test_a_kept_unpriced_element_withholds_nothing(self, database, parameters, tmp_path):
        """The kept door is unpriced in the reference and the plan alike and carries no measure."""
        result = StagedEvaluator(database).evaluate(
            [_baseline(kept_door=True), _stage(4000.0, kept_door=True)], parameters, brownfield_perspective()
        )

        document = _write(result, parameters, tmp_path / "kept.json", unpriced=[KEPT_DOOR])

        assert document["unpriced_investment"] is None
        assert document["plan"]["totals"]["npv_in_euro"] == _as_band(result.plan.total_npv_in_euro)
        assert document["comparison"]["discounted_payback_year"] == result.comparison.discounted_payback_envelope.to_band()
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert rows[KEPT_DOOR]["unpriced"] is True and rows[KEPT_DOOR]["measure_id"] is None
