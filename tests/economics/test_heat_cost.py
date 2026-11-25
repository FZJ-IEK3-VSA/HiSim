"""The levelized cost of heat counts heat costs only (hisim-lcoh-heat-costs-only-9f07).

Before, the figure divided a perspective's whole NPV -- appliance electricity, a photovoltaic array and a battery
included -- by the useful heat, so a PV-only plan moved the "cost of heat" of a gas house. By owner decision of
2026-11-13 it counts the heat generators', storages' and emitters' own cash flows, the fuels, and the heating devices'
share of the electricity bill (`HeatCostAttribution`).
"""

from dataclasses import replace
from typing import Dict, List

import pytest

from hisim.economics.calculators.heat_cost import HeatCostAttribution
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import Stage, StagedEvaluator
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory, SubjectKind, discount_factor
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    state_inputs,
    write_database,
)

pytestmark = pytest.mark.base

#: A photovoltaic array's subject.
PV = "PVSystem"

#: The heat pump's and the appliances' metered electricity of the heat-pump stage, in kWh: a share of 0.6.
HEAT_PUMP_ATTRIBUTION: Dict[str, Dict[str, float]] = {
    SyntheticPlan.HEAT_PUMP_SUBJECT: {"HEAT_PUMP_ELECTRICITY": 3000.0},
    "Occupancy": {"HOUSEHOLD_ELECTRICITY": 2000.0},
}


def _entry(year: int, amount: float, category: CostCategory, subject: str) -> CashFlowEntry:
    """One exact timeline entry; a carrier subject is booked as a carrier."""
    kind = SubjectKind.CARRIER if subject.isupper() else SubjectKind.COMPONENT
    return CashFlowEntry(
        year=year, amount_in_euro=UncertainValue.exact(amount), category=category, subject=subject, subject_kind=kind
    )


class TestTheHeatSubjects:
    """`HeatCostAttribution.heat_subjects` names generators, storages and emitters, and nothing else."""

    def test_generators_storages_and_emitters_are_heat_subjects(self):
        """A heat pump, a buffer and radiators count; their cash flows are the heating system's."""
        classes = {
            "HeatPump": ComponentType.HEAT_PUMP,
            "Buffer": ComponentType.BUFFER,
            "Radiators": ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR,
        }
        assert HeatCostAttribution.heat_subjects(classes) == frozenset(classes)

    def test_pv_battery_envelope_and_classless_subjects_are_not(self):
        """A photovoltaic array, a battery, an insulation measure and a subject without a class are not heat costs."""
        classes = {
            PV: ComponentType.PV,
            "Battery": ComponentType.BATTERY,
            "facade_insulation": ComponentType.WALL_EXTERNAL_INSULATION,
            "hot_water_tank_and_pipe_insulation": None,
        }
        assert HeatCostAttribution.heat_subjects(classes) == frozenset()


class TestTheElectricityHeatShare:
    """`HeatCostAttribution.electricity_heat_share` splits the electricity bill by metered consumption."""

    def test_the_heat_pumps_share_of_the_metered_consumption(self):
        """3,000 kWh of heat pump beside 2,000 kWh of appliances: the heat bears 60 % of the bill."""
        share = HeatCostAttribution.electricity_heat_share(HEAT_PUMP_ATTRIBUTION, [ComponentType.HEAT_PUMP])
        assert share == pytest.approx(0.6)

    def test_pv_and_the_grid_are_in_neither_term(self):
        """Generation and grid flows are supply, not consumption: they leave the share as it is."""
        attribution = {
            **HEAT_PUMP_ATTRIBUTION,
            PV: {"PV_GENERATION": 4000.0},
            "Meter": {"GRID_IMPORT": 2500.0, "GRID_EXPORT": 1500.0},
        }
        assert HeatCostAttribution.electricity_heat_share(attribution, [ComponentType.HEAT_PUMP]) == pytest.approx(0.6)

    def test_a_house_heated_without_electricity_bears_none_of_it(self):
        """A gas house: no heating device runs on electricity, so none of the bill is heat cost."""
        attribution = {"Occupancy": {"HOUSEHOLD_ELECTRICITY": 2000.0}}
        assert HeatCostAttribution.electricity_heat_share(attribution, [ComponentType.GAS_HEATER]) == 0.0

    def test_an_unmetered_electric_heater_makes_the_share_unknown(self):
        """A heat pump or a resistive heater whose electricity no role meters: unknown, not zero."""
        attribution = {"Occupancy": {"HOUSEHOLD_ELECTRICITY": 2000.0}}
        assert HeatCostAttribution.electricity_heat_share(attribution, [ComponentType.ELECTRIC_HEATING_DHW]) is None
        assert HeatCostAttribution.electricity_heat_share({}, [ComponentType.HEAT_PUMP]) is None

    def test_a_heat_pump_without_appliances_bears_the_whole_bill(self):
        """No metered appliance electricity: the heat pump is the only consumer."""
        attribution = {SyntheticPlan.HEAT_PUMP_SUBJECT: {"HEAT_PUMP_ELECTRICITY": 3000.0}}
        assert HeatCostAttribution.electricity_heat_share(attribution, [ComponentType.HEAT_PUMP]) == 1.0


class TestTheHeatCostNpv:
    """`HeatCostAttribution.heat_cost_npv_in_euro` weights and discounts each entry."""

    def test_each_entry_by_its_weight(self):
        """Heat pump and gas in full, electricity by the year's share, PV and the feed-in revenue not at all."""
        timeline = CashFlowTimeline(
            entries=[
                _entry(0, 1000.0, CostCategory.INVESTMENT, SyntheticPlan.HEAT_PUMP_SUBJECT),
                _entry(0, 5000.0, CostCategory.INVESTMENT, PV),
                _entry(1, 100.0, CostCategory.ENERGY_WORKING, "NATURAL_GAS"),
                _entry(1, 200.0, CostCategory.ENERGY_WORKING, "ELECTRICITY"),
                _entry(1, -50.0, CostCategory.FEED_IN_REVENUE, "ELECTRICITY_FEED_IN"),
            ],
            validate=False,
        )
        npv = HeatCostAttribution.heat_cost_npv_in_euro(
            timeline, frozenset({SyntheticPlan.HEAT_PUMP_SUBJECT}), (0.0, 0.5), 0.03
        )
        assert npv.best_estimate == pytest.approx(1000.0 + (100.0 + 0.5 * 200.0) * discount_factor(0.03, 1))

    def test_a_year_without_a_share_is_refused(self):
        """An electricity entry outside the years the shares cover is a caller's bug, not a zero."""
        timeline = CashFlowTimeline(entries=[_entry(2, 200.0, CostCategory.ENERGY_WORKING, "ELECTRICITY")])
        with pytest.raises(IndexError):
            HeatCostAttribution.heat_cost_npv_in_euro(timeline, frozenset(), (0.0, 0.5), 0.03)

    def test_nothing_heat_related_is_an_exact_zero(self):
        """A timeline of PV alone has no heat cost."""
        timeline = CashFlowTimeline(entries=[_entry(0, 5000.0, CostCategory.INVESTMENT, PV)])
        assert HeatCostAttribution.heat_cost_npv_in_euro(timeline, frozenset(), (0.0,), 0.03) == UncertainValue.exact(0.0)


class TestTheStagedShares:
    """`StagedEvaluator.electricity_heat_share_by_year` takes each year's share from the stage active in it."""

    def test_a_heat_pump_from_year_two_raises_the_share_from_year_two(self):
        """The gas house's years have share 0, the heat pump's 0.6."""
        stages = (baseline_stage(), _heat_pump_stage(2))
        assert StagedEvaluator.electricity_heat_share_by_year(stages, (0, 0, 1, 1)) == (0.0, 0.0, 0.6, 0.6)

    def test_an_unknown_share_in_an_active_year_makes_all_unknown(self):
        """A heat pump with no metered electricity leaves the plan's figure out."""
        unmetered = replace(_heat_pump_stage(1), inputs=replace(_heat_pump_stage(1).inputs, energy_attribution_by_subject_in_kwh={}))
        assert StagedEvaluator.electricity_heat_share_by_year((baseline_stage(), unmetered), (0, 1)) is None

    def test_a_stage_never_active_does_not_count(self):
        """An unmetered heat pump replaced before it is active decides nothing."""
        unmetered = replace(_heat_pump_stage(1), inputs=replace(_heat_pump_stage(1).inputs, energy_attribution_by_subject_in_kwh={}))
        assert StagedEvaluator.electricity_heat_share_by_year((baseline_stage(), unmetered), (0, 0)) == (0.0, 0.0)


def _heat_pump_stage(from_year: int) -> Stage:
    """A stage that replaces the boiler with a heat pump, with its electricity metered (share 0.6)."""
    inputs = state_inputs(
        [(SyntheticPlan.HEAT_PUMP_SUBJECT, ComponentType.HEAT_PUMP, 9.0, SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO)],
        SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
    )
    inputs = replace(inputs, energy_attribution_by_subject_in_kwh=HEAT_PUMP_ATTRIBUTION)
    return Stage(inputs=inputs, from_year=from_year, label="heat pump", measures=("heating_system",))


def _pv_stage() -> Stage:
    """A stage in year 0 that keeps the boiler and adds a photovoltaic array, which halves the bought electricity."""
    subjects: List = [
        (SyntheticPlan.BOILER_SUBJECT, ComponentType.GAS_HEATER, 15.0, SyntheticPlan.BOILER_INVESTMENT_IN_EURO),
        (PV, ComponentType.PV, 8.0, 12000.0),
    ]
    inputs = state_inputs(subjects, SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH / 2, electricity_sold_in_kwh=1500.0)
    return Stage(inputs=inputs, from_year=0, label="pv", measures=("solar_pv",))


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("heat_cost_database")))


@pytest.fixture(name="parameters")
def fixture_parameters() -> EconomicParameters:
    """The assumptions the synthetic plans are priced under."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        co2_price_scenario="none",
        apply_subsidies=False,
    )


class TestThePlan:
    """The bead's done-when: a PV-only plan leaves the figure unchanged, a heat pump changes it."""

    def test_a_pv_only_plan_leaves_the_levelized_cost_of_heat_unchanged(self, database, parameters):
        """The gas house's heat costs are its boiler's: the array and the smaller electricity bill are not in them."""
        result = StagedEvaluator(database).evaluate([baseline_stage(), _pv_stage()], parameters, brownfield_perspective())
        reference, plan = result.reference.levelized_cost_of_heat_in_euro_per_kwh, result.plan.levelized_cost_of_heat_in_euro_per_kwh
        assert reference is not None and plan is not None
        assert plan.best_estimate == pytest.approx(reference.best_estimate)
        assert result.plan.total_npv_in_euro.best_estimate != pytest.approx(result.reference.total_npv_in_euro.best_estimate)

    def test_a_heat_pump_plan_changes_it(self, database, parameters):
        """The heat pump and its 60 % of the electricity bill replace the boiler's cash flows."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), _heat_pump_stage(0)], parameters, brownfield_perspective()
        )
        reference, plan = result.reference.levelized_cost_of_heat_in_euro_per_kwh, result.plan.levelized_cost_of_heat_in_euro_per_kwh
        assert reference is not None and plan is not None
        assert plan.best_estimate != pytest.approx(reference.best_estimate)

    def test_the_figure_is_the_heat_cost_npv_annualized_over_the_heat(self, database, parameters):
        """The heat-cost NPV is smaller than the whole NPV, and the figure is it times the annuity over the heat."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), _heat_pump_stage(0)], parameters, brownfield_perspective()
        )
        plan = result.plan
        assert plan.heat_cost_npv_in_euro is not None and plan.levelized_cost_of_heat_in_euro_per_kwh is not None
        assert plan.heat_cost_npv_in_euro.best_estimate < plan.total_npv_in_euro.best_estimate
        heat = plan.assumptions.annual_heat_demand_in_kwh if plan.assumptions is not None else None
        assert heat
        assert plan.levelized_cost_of_heat_in_euro_per_kwh.best_estimate == pytest.approx(
            plan.heat_cost_npv_in_euro.best_estimate * parameters.annuity_factor() / heat
        )
