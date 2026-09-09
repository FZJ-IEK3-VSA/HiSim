"""Unit tests for the three caption views of the visualization extension (Q26 F1/F6, Q27 R2).

The last of the view files, and the odd one out: these three views feed *sentences* rather than
charts. `levelized_heat_cost_derivation` spells the heat-cost KPI out as its own division,
`scenario_assumption_labels` says what each scenario row actually changed, and `zero_swing_notes`
says why a row that did not move at all was inert. They belong together because all three exist
for the same reason — a figure a reader cannot reconstruct is a figure they can only trust — and
because all three refuse to guess: the derivation validates itself against the published KPI, and
the zero-swing note names a cause it can read off the timeline or says it cannot name one.

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

import pytest

from hisim.economics import views
from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.results import (
    CashFlowTimeline,
    EconomicAssumptions,
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


def make_result(entries, horizon: int = 20, interest: float = 0.03) -> LifecycleCostResult:
    """A `LifecycleCostResult` built from a hand-written timeline, with consistent aggregates.

    The KPIs come from `calculators.aggregation.aggregate_timeline`, i.e. through the engine's own
    pivot rather than through hand-written totals: the derivation view checks itself against the
    published figure, so a fixture whose aggregates disagreed with its timeline would make the
    check meaningless.

    Args:
        entries: The timeline entries; sign validation is on, as it is for engine timelines.
        horizon: Observation period T.
        interest: Discount rate, and therefore the annuity factor the derivation divides by.

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
        annual_heat_demand_in_kwh=None,
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
        """Twenty years of energy cost, priced per kWh of heat exactly as the engine does it."""
        result = make_result(
            [entry(year, 1000.0, CostCategory.ENERGY_WORKING) for year in range(1, 21)]
        )
        result.levelized_cost_of_heat_in_euro_per_kwh = result.total_npv_in_euro.scale(
            result.parameters.annuity_factor() / demand
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

    def test_an_archived_result_without_the_recorded_demand_still_divides_out(self):
        """The demand field is newer than the KPI; the division is stated backwards from it."""
        result = self.heated_result()
        result.assumptions = None
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.annual_heat_demand_in_kwh == pytest.approx(15000.0)

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


class TestScenarioAssumptionLabels:
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

    def test_a_parameter_axis_states_both_values(self):
        """The row reads `interest_rate 5.00% (central 3.00%)`, never a swing without a cause."""
        cube = _Cube([_Scenario("central"), _Scenario("interest=high", {"interest_rate": 0.05})])
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels == {"interest=high": "interest_rate 5.00% (central 3.00%)"}

    def test_a_dict_axis_falls_back_to_the_rate_the_run_resolved(self):
        """A carrier with no configured rate still has a central value: the one it was priced at."""
        cube = _Cube(
            [_Scenario("electricity=flat", {"energy_price_escalation_rates.ELECTRICITY": 0.0})]
        )
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels["electricity=flat"] == (
            "energy_price_escalation_rates.ELECTRICITY 0.00% (central 2.00%)"
        )

    def test_a_data_overlay_states_the_band_it_replaced_the_shipped_data_with(self):
        """An overlay has no central parameter, so the central case is named "as shipped"."""
        cube = _Cube(
            [
                _Scenario(
                    "hp_price=cheap",
                    data_overlays={
                        "devices_DE.HEAT_PUMP.specific_investment": {
                            "min": 800, "avg": 1050, "max": 1450
                        }
                    },
                )
            ]
        )
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels["hp_price=cheap"] == (
            "devices_DE.HEAT_PUMP.specific_investment min 800/avg 1,050/max 1,450 "
            "(central as shipped)"
        )

    def test_a_non_rate_axis_is_printed_as_it_is_stored(self):
        """No unit is invented for a field whose unit the view cannot know (a horizon in years)."""
        cube = _Cube([_Scenario("horizon=long", {"observation_period_in_years": 40})])
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels["horizon=long"] == "observation_period_in_years 40 (central 20)"

    def test_the_base_cell_carries_no_assumption(self):
        """The central case changed nothing, and the table says so rather than inventing a row."""
        assert not views.scenario_assumption_labels(_Cube([_Scenario("central")]), self.base_result())
        assert not views.scenario_assumption_labels(None, self.base_result())


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

    def test_an_inert_co2_axis_names_the_carriers_that_declare_no_exposure(self):
        """The absence of the category *is* the zero exposure, and the bill says whose it is."""
        cube = _Cube([_Scenario("central"), _Scenario("co2=high", {"co2_price_scenario": "high"})])
        notes = views.zero_swing_notes(
            cube, self.billed_result(), {"central": 0.0, "co2=high": 0.0}
        )
        assert notes["co2=high"] == (
            "no CO2-price flow is booked in this run — the electricity price entry declares no "
            "direct CO2-price exposure (co2_price_exposure = 0)"
        )

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

    def test_an_undiagnosable_axis_says_so_rather_than_guessing(self):
        """The note is derived or it is honest; it is never invented."""
        cube = _Cube([_Scenario("horizon=long", {"observation_period_in_years": 40})])
        notes = views.zero_swing_notes(cube, self.billed_result(), {"horizon=long": 0.0})
        assert notes == {"horizon=long": views.ZeroSwingCauses.UNKNOWN}

    def test_only_an_exact_zero_qualifies(self):
        """A swing of a few cents is a real, tiny effect and must keep reading as one."""
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
