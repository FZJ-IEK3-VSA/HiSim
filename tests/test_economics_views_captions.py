"""Unit tests for the three caption views of the visualization extension (Q26 F1/F6, Q27 R2).

The last of the view files, and the odd one out: these three views feed *sentences* rather than
charts. `levelized_heat_cost_derivation` spells the heat-cost KPI out as its own division,
`scenario_assumptions` says what each scenario row actually changed, and `zero_swing_notes`
says why a row that did not move at all was inert. They belong together because all three exist
for the same reason — a figure a reader cannot reconstruct is a figure they can only trust — and
because all three refuse to guess: the derivation validates itself against the published KPI, and
the zero-swing note states what it observed in the timeline or says it can name no cause.

The two scenario views return **plain data** — the changed value, the central case beside it
and the kind of quantity both are — and the report decides the digits, so the assertions here
are on records rather than on sentences. The one exception is the overlay-band test, which
renders through `assembly._one_assumption_text` on purpose: the defect it pins was a
fractional price rounded to `0` on the way to the page, and that is only visible end to end.

The V1-V7 and V8-V15 chart views live in `tests/test_economics_views_charts_a.py` and `_b.py`;
this file follows their fixture style — **hand-built timelines** through the engine's own
`aggregate_timeline`, so the expected figures are arithmetic a reader can redo on paper — except
for the two scenario views, which are fed duck-typed stand-ins for a `ScenarioCube` because that
is exactly what presentation hands them across seam 4.

**What a failure means.** A *derivation* failure in the caption layer: no stored number moves, but
a sentence in the report would state a division that does not reproduce the KPI beside it, name
the wrong central value for a scenario, or invent a cause for an inert axis. The rendered form of
all three is pinned in `tests/test_economics_sections_d.py`; nothing here asserts on HTML.
"""

# clean

import dataclasses
from typing import Optional

import pytest

from hisim.economics import views
from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.reporting.assembly import _one_assumption_text
from hisim.economics.results import (
    CashFlowTimeline,
    EconomicAssumptions,
    EvaluationMatrix,
    LifecycleCo2Result,
    LifecycleCostResult,
    RateOrigin,
    ResolvedRate,
)
from hisim.economics.timeline import Actor, CashFlowEntry, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base


def entry(
    year: int,
    amount: float,
    category: CostCategory,
    subject: str = "HeatPump",
    subject_kind: SubjectKind = SubjectKind.COMPONENT,
) -> CashFlowEntry:
    """One exact timeline entry; these views read categories and subjects, never bands."""
    return CashFlowEntry(
        year=year,
        amount_in_euro=UncertainValue.exact(amount),
        category=category,
        subject=subject,
        subject_kind=subject_kind,
        payer=Actor.SYSTEM,
    )


def make_result(
    entries, horizon: int = 20, interest: float = 0.03, heat_demand: Optional[float] = None
) -> LifecycleCostResult:
    """A `LifecycleCostResult` built from a hand-written timeline, with consistent aggregates.

    Every KPI comes from `calculators.aggregation.aggregate_timeline`, i.e. through the engine's
    own pivot rather than through hand-written totals — **including the heat-cost figure**, which
    is why `heat_demand` is passed down into the aggregation rather than multiplied out here. The
    derivation view checks itself against the published figure, and a fixture that published a
    figure this file had computed would have been checking the test's arithmetic against itself.

    Args:
        entries: The timeline entries; sign validation is on, as it is for engine timelines.
        horizon: Observation period T.
        interest: Discount rate, and therefore the annuity factor the derivation divides by.
        heat_demand: Annual heat demand in kWh; None publishes no heat-cost figure at all.

    Returns:
        The result, ready to hand to any of the three views.
    """
    parameters = EconomicParameters(observation_period_in_years=horizon, interest_rate=interest)
    timeline = CashFlowTimeline()
    timeline.extend(entries)
    aggregation = aggregate_timeline(
        timeline=timeline,
        actor_scope=ActorScope.SYSTEM,
        facts_by_subject={},
        co2_result=LifecycleCo2Result(),
        parameters=parameters,
        annual_heat_demand_in_kwh=heat_demand,
    )
    return LifecycleCostResult(
        perspective_id="test",
        parameters=parameters,
        total_npv_in_euro=aggregation.total_npv_in_euro,
        equivalent_annual_cost_in_euro=aggregation.equivalent_annual_cost_in_euro,
        npv_by_category=aggregation.npv_by_category,
        npv_by_component=aggregation.npv_by_component,
        npv_by_payer=aggregation.npv_by_payer,
        component_breakdowns=aggregation.component_breakdowns,
        annual_cost_series_nominal_in_euro=aggregation.annual_cost_series_nominal_in_euro,
        monthly_cost_year1_in_euro=aggregation.monthly_cost_year1_in_euro,
        levelized_cost_of_heat_in_euro_per_kwh=aggregation.levelized_cost_of_heat_in_euro_per_kwh,
        timeline=timeline,
        lifecycle_co2_result=LifecycleCo2Result(),
        scope_payer=aggregation.scope_payer,
    )


class TestLevelizedHeatCostDerivation:
    """Q26 F6: the heat-cost KPI as a division a reader can redo, or a refusal."""

    def heated_result(self, demand: float = 15000.0) -> LifecycleCostResult:
        """Twenty years of energy cost, priced per kWh of heat exactly as the engine does it.

        The heat demand goes into `aggregate_timeline`, so the published figure is the engine's
        own quotient and the reconciliation below is a real check rather than a restatement of a
        number this file computed. The recorded demand on `assumptions` is the same one the
        evaluator records, which is what the derivation divides by.
        """
        result = make_result(
            [entry(year, 1000.0, CostCategory.ENERGY_WORKING) for year in range(1, 21)],
            heat_demand=demand,
        )
        result.assumptions = EconomicAssumptions(annual_heat_demand_in_kwh=demand)
        return result

    def test_the_division_reproduces_the_published_figure(self):
        """Numerator, denominator and quotient are the published KPI, not a second calculation."""
        result = self.heated_result()
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.numerator_npv_in_euro == pytest.approx(
            result.total_npv_in_euro.best_estimate
        )
        assert derivation.equivalent_annual_cost_in_euro == pytest.approx(
            derivation.numerator_npv_in_euro * derivation.annuity_factor
        )
        assert (
            derivation.equivalent_annual_cost_in_euro / derivation.annual_heat_demand_in_kwh
        ) == pytest.approx(derivation.levelized_cost_in_euro_per_kwh)

    def test_the_discounted_heat_sum_is_the_stated_equivalent_form(self):
        """NPV / discounted heat is the same quotient — the form the LCOH literature states."""
        derivation = views.levelized_heat_cost_derivation(self.heated_result())
        assert derivation is not None
        assert derivation.discounted_heat_in_kwh == pytest.approx(
            derivation.annual_heat_demand_in_kwh / derivation.annuity_factor
        )
        assert (
            derivation.numerator_npv_in_euro / derivation.discounted_heat_in_kwh
        ) == pytest.approx(derivation.levelized_cost_in_euro_per_kwh)

    def test_the_attribution_set_is_stated_truthfully(self):
        """Every subject the perspective books, because there is no heating-only attribution."""
        result = self.heated_result()
        result.timeline.extend(
            [entry(0, 9000.0, CostCategory.INVESTMENT, subject="PhotovoltaicSystem")]
        )
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.attributed_subjects == ("HeatPump", "PhotovoltaicSystem")

    def test_a_run_without_a_heat_demand_publishes_no_derivation(self):
        """No KPI, no caption: the section stays silent rather than inventing a denominator."""
        assert views.levelized_heat_cost_derivation(
            make_result([entry(0, 100.0, CostCategory.INVESTMENT)])
        ) is None

    def test_an_archived_result_without_the_recorded_demand_says_where_it_got_one(self):
        """The demand is divided back out of the figure, and the record says so rather than not.

        Reconciling that denominator against the figure it was derived from would check nothing,
        so the check is skipped and the flag is set instead — the caption then states where the
        denominator came from rather than presenting it as an independently known quantity.
        """
        result = self.heated_result()
        result.assumptions = None
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.annual_heat_demand_in_kwh == pytest.approx(15000.0)
        assert derivation.demand_inferred_from_published
        recorded = views.levelized_heat_cost_derivation(self.heated_result())
        assert recorded is not None and not recorded.demand_inferred_from_published

    def test_a_perspective_that_books_nothing_states_no_subject_list(self):
        """An empty scoped timeline books no subject, and the record says so with an empty tuple.

        The caption prints "namely <subjects>" after the attribution sentence; with nothing to
        name it must print no clause at all rather than "namely ." — which is what the empty
        tuple lets the renderer decide.
        """
        result = self.heated_result()
        result.timeline = CashFlowTimeline()
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.attributed_subjects == ()

    def test_every_perspective_of_a_matrix_gets_its_own_division(self):
        """The KPI table publishes the figure per perspective, so the caption explains each one."""
        first = self.heated_result()
        second = self.heated_result(demand=20000.0)
        second.perspective_id = "second"
        matrix = EvaluationMatrix(results={"test": first, "second": second})
        derivations = views.levelized_heat_cost_derivations(matrix)
        assert list(derivations) == ["test", "second"]
        assert derivations["test"].stated_figures() != derivations["second"].stated_figures()
        assert derivations["test"].annual_heat_demand_in_kwh == pytest.approx(15000.0)
        assert derivations["second"].annual_heat_demand_in_kwh == pytest.approx(20000.0)

    def test_two_perspectives_that_divide_alike_state_the_same_figures(self):
        """The section collapses to one sentence on exactly this: equal figures, different ids."""
        first = self.heated_result()
        second = self.heated_result()
        second.perspective_id = "second"
        matrix = EvaluationMatrix(results={"test": first, "second": second})
        derivations = views.levelized_heat_cost_derivations(matrix)
        assert (
            derivations["test"].stated_figures() == derivations["second"].stated_figures()
        )
        assert derivations["test"].perspective_id != derivations["second"].perspective_id

    def test_a_figure_that_does_not_reconcile_raises(self):
        """A caption describing a different figure from the KPI table is a defect, not a caption."""
        result = make_result([entry(1, 1000.0, CostCategory.ENERGY_WORKING)])
        result.levelized_cost_of_heat_in_euro_per_kwh = UncertainValue.exact(9.99)
        result.assumptions = EconomicAssumptions(annual_heat_demand_in_kwh=15000.0)
        with pytest.raises(CostDataError):
            views.levelized_heat_cost_derivation(result)


class _Scenario:
    """The two override dicts a `ScenarioCube` carries per cell, duck-typed."""

    def __init__(self, scenario_id, parameter_overrides=None, data_overlays=None):
        self.id = scenario_id
        self.parameter_overrides = parameter_overrides or {}
        self.data_overlays = data_overlays or {}


class _Cube:
    """The two attributes the scenario views read off a cube, and nothing else."""

    def __init__(self, scenarios, base_id="central"):
        self.scenarios = scenarios
        self.base_id = base_id


class TestScenarioAssumptions:
    """Q26 F1: a scenario row states the assumption it changed and both of its values."""

    def base_result(self) -> LifecycleCostResult:
        """A central case with an explicit interest rate and a resolved electricity rate."""
        result = make_result([entry(0, 100.0, CostCategory.INVESTMENT)], interest=0.03)
        result.assumptions = EconomicAssumptions(
            escalation_rates={
                "energy:electricity": ResolvedRate(
                    rate=0.02, origin=RateOrigin.COUNTRY_DEFAULTS, source_ids=["src_defaults"]
                )
            }
        )
        return result

    def only(self, cube, scenario_id: str) -> views.ScenarioAssumption:
        """The single assumption of a one-axis scenario, so a test states one expectation."""
        assumptions = views.scenario_assumptions(cube, self.base_result())
        assert len(assumptions[scenario_id]) == 1
        return assumptions[scenario_id][0]

    def test_a_parameter_axis_states_both_values(self):
        """The record carries 5 % against the central 3 %, and says both are fractions of one."""
        cube = _Cube([_Scenario("central"), _Scenario("interest=high", {"interest_rate": 0.05})])
        item = self.only(cube, "interest=high")
        assert item.field_name == "interest_rate" and not item.key
        assert item.scenario_value == pytest.approx(0.05)
        assert item.central_case is views.CentralCase.VALUE
        assert item.central_value == pytest.approx(0.03)
        assert item.kind is views.AssumptionKinds.PERCENT
        assert _one_assumption_text(item) == "interest_rate 5.00% (central case: 3.00%)"

    def test_a_dict_axis_falls_back_to_the_rate_the_run_resolved(self):
        """A carrier with no configured rate still has a central value: the one it was priced at."""
        cube = _Cube(
            [_Scenario("electricity=flat", {"energy_price_escalation_rates.ELECTRICITY": 0.0})]
        )
        item = self.only(cube, "electricity=flat")
        assert item.field_name == "energy_price_escalation_rates.ELECTRICITY"
        assert item.central_case is views.CentralCase.VALUE
        assert item.central_value == pytest.approx(0.02)
        assert _one_assumption_text(item) == (
            "energy_price_escalation_rates.ELECTRICITY 0.00% (central case: 2.00%)"
        )

    def test_a_data_overlay_states_the_band_it_replaced_the_shipped_data_with(self):
        """The band keys are the ones `UncertainValue.from_json` reads, and a price stays a price.

        The keys are `min`/`best_estimate`/`max` — the universal §3.1 value syntax — and not the
        `avg` this view once looked for and never found, which silently reduced every overlay to
        a `repr`. The rendering is asserted with the record because the defect this pins is a
        0.35 EUR/kWh working price rounding to `0` somewhere between the two.
        """
        cube = _Cube(
            [
                _Scenario(
                    "power=dear",
                    data_overlays={
                        "energy_prices_DE.ELECTRICITY.working_price": {
                            "min": 0.30, "best_estimate": 0.35, "max": 0.40
                        }
                    },
                )
            ]
        )
        item = self.only(cube, "power=dear")
        assert item.scenario_band == pytest.approx((0.30, 0.35, 0.40))
        assert item.scenario_value == pytest.approx(0.35)
        assert item.central_case is views.CentralCase.AS_SHIPPED
        assert _one_assumption_text(item) == (
            "energy_prices_DE.ELECTRICITY.working_price 0.35 [0.3 | 0.4] "
            "(central case: as shipped)"
        )

    def test_a_whole_dict_override_states_one_line_per_key(self):
        """The documented merge form is rendered key by key, never as a dict `repr`."""
        cube = _Cube([_Scenario("gas=dear", {"energy_price_escalation_rates": {"GAS": 0.05}})])
        item = self.only(cube, "gas=dear")
        assert item.field_name == "energy_price_escalation_rates" and item.key == "GAS"
        assert item.central_case is views.CentralCase.NOT_RECORDED
        assert _one_assumption_text(item) == (
            "energy_price_escalation_rates GAS 5.00% (central case: not recorded)"
        )

    def test_a_non_rate_axis_is_printed_in_the_unit_its_field_declares(self):
        """A horizon is a count of years because the field says so, not because 40 looks like one."""
        cube = _Cube([_Scenario("horizon=long", {"observation_period_in_years": 40})])
        item = self.only(cube, "horizon=long")
        assert item.kind is views.AssumptionKinds.YEARS
        assert _one_assumption_text(item) == (
            "observation_period_in_years 40 (central case: 20)"
        )

    def test_a_rate_that_is_not_a_fraction_is_refused_by_name(self):
        """`interest_rate: 5` is a typo the engine reads as 500 %, and the caption will not print it."""
        cube = _Cube([_Scenario("interest=typo", {"interest_rate": 5})])
        with pytest.raises(CostDataError) as error:
            views.scenario_assumptions(cube, self.base_result())
        assert "interest_rate" in str(error.value)

    def test_an_unset_optional_rate_states_what_the_engine_resolved_it_to(self):
        """`grid_fee_escalation_rate` unset is not "no central value": it is the general rate."""
        base = self.base_result()
        base.parameters.grid_fee_escalation_rate = None
        base.parameters.general_price_escalation_rate = 0.018
        cube = _Cube([_Scenario("grid=steep", {"grid_fee_escalation_rate": 0.04})])
        item = views.scenario_assumptions(cube, base)["grid=steep"][0]
        assert item.central_case is views.CentralCase.RESOLVED_FROM_GENERAL
        assert item.central_value == pytest.approx(0.018)
        assert _one_assumption_text(item) == (
            "grid_fee_escalation_rate 4.00% (central case: 1.80%, resolved from the general "
            "escalation rate)"
        )

    def test_a_field_that_is_not_a_parameter_at_all_records_no_central_value(self):
        """A price-entry field has no central *parameter*, and the row says so instead of guessing."""
        cube = _Cube([_Scenario("exposure", {"co2_price_exposure": 0.5})])
        item = self.only(cube, "exposure")
        assert item.central_case is views.CentralCase.NOT_RECORDED
        assert item.kind is views.AssumptionKinds.PERCENT
        assert _one_assumption_text(item) == (
            "co2_price_exposure 50.00% (central case: not recorded)"
        )

    def test_every_economic_parameter_declares_its_kind(self):
        """A new parameter field is a failing test here, never a number printed without a unit."""
        declared = set(views.ScenarioValueKinds.BY_PARAMETER)
        actual = {item.name for item in dataclasses.fields(EconomicParameters)}
        assert actual - declared == set(), "undeclared EconomicParameters fields"
        assert declared - actual == set(), "declared kinds for fields that no longer exist"

    def test_the_base_cell_carries_no_assumption(self):
        """The central case changed nothing, and the table says so rather than inventing a row."""
        assert not views.scenario_assumptions(_Cube([_Scenario("central")]), self.base_result())
        assert not views.scenario_assumptions(None, self.base_result())


class TestZeroSwingNotes:
    """Q27 R2: an exactly-zero swing names its cause from the timeline, or admits it cannot."""

    def billed_result(self) -> LifecycleCostResult:
        """An all-electric run: an electricity bill, and no carbon-price flow anywhere in it."""
        return make_result(
            [entry(0, 20000.0, CostCategory.INVESTMENT)]
            + [
                entry(year, 1000.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                      subject_kind=SubjectKind.CARRIER)
                for year in range(1, 21)
            ]
        )

    def test_an_inert_co2_axis_states_what_the_timeline_shows_and_no_more(self):
        """The note reports the missing category and the bills it is missing from — not a cause.

        A zero `co2_price_exposure` on every carrier's price entry is the likely reason, but a
        stored result carries no price entry: naming the parameter would present an inference as
        a reading, in the one footnote whose whole purpose is that it never does.
        """
        cube = _Cube([_Scenario("central"), _Scenario("co2=high", {"co2_price_scenario": "high"})])
        notes = views.zero_swing_notes(
            cube, self.billed_result(), {"central": 0.0, "co2=high": 0.0}
        )
        assert notes["co2=high"] == (
            "the stored timeline books no CO2-price entry for any carrier, electricity included"
        )
        assert "co2_price_exposure" not in notes["co2=high"]

    def test_a_priced_carbon_axis_gets_no_note_at_all(self):
        """A run that does book a carbon price has a real, non-zero axis and nothing to explain."""
        result = self.billed_result()
        result.timeline.extend(
            [entry(1, 50.0, CostCategory.ENERGY_CO2_PRICE, subject="ELECTRICITY",
                   subject_kind=SubjectKind.CARRIER)]
        )
        cube = _Cube([_Scenario("co2=high", {"co2_price_scenario": "high"})])
        notes = views.zero_swing_notes(cube, result, {"co2=high": 0.0})
        assert notes == {"co2=high": views.ZeroSwingCauses.UNKNOWN}

    def test_an_escalation_axis_on_an_unbilled_carrier_names_the_missing_bill(self):
        """Escalating a gas rate does nothing in a house that buys no gas, and the row says so."""
        cube = _Cube(
            [_Scenario("gas=flat", {"energy_price_escalation_rates.GAS": 0.0})]
        )
        notes = views.zero_swing_notes(cube, self.billed_result(), {"gas=flat": 0.0})
        assert notes["gas=flat"] == "the run books no gas bill for the rate to escalate"

    def test_an_undiagnosable_axis_refuses_rather_than_guessing(self):
        """The note is an observation or it is a refusal to make one; it is never invented."""
        cube = _Cube([_Scenario("horizon=long", {"observation_period_in_years": 40})])
        notes = views.zero_swing_notes(cube, self.billed_result(), {"horizon=long": 0.0})
        assert notes == {"horizon=long": views.ZeroSwingCauses.UNKNOWN}

    def test_only_an_exact_zero_qualifies(self):
        """A swing of a few cents is a real, tiny effect, and the table now prints it as one."""
        cube = _Cube([_Scenario("co2=high", {"co2_price_scenario": "high"})])
        assert not views.zero_swing_notes(cube, self.billed_result(), {"co2=high": 0.02})
        assert not views.zero_swing_notes(None, self.billed_result(), {})

    def test_the_base_cell_never_carries_a_note(self):
        """The central case's swing is zero by construction, which explains nothing."""
        cube = _Cube([_Scenario("central"), _Scenario("co2=high", {"co2_price_scenario": "high"})])
        notes = views.zero_swing_notes(
            cube, self.billed_result(), {"central": 0.0, "co2=high": 0.0}
        )
        assert "central" not in notes
