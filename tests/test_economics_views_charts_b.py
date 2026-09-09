"""Unit tests for the V8-V15 chart views of the visualization extension (visualization spec §5).

Every chart of the V1-V15 set gets its numbers from a view function, and every one of those views
either carries an invariant it validates at runtime or has a reconciliation a reviewer is expected
to check by hand. This module is that promise for the second half of the set — the treemap, the
swimlane, the funding statement, the subject flows, the household energy balance, the fixed-interest
benchmark, the monthly burden and the equity build-up — on **hand-built timelines** rather than on
evaluated runs, so that the expected figures are arithmetic a reader can redo on paper. The V1-V7
half lives in `tests/test_economics_views_charts.py`; the two files share no state.

**Why hand-built.** `tests/test_economics_views.py` pins the older views against an evaluated
result, which is the right shape for views that re-arrange a real evaluation. The chart views are
different: most of them *validate* something (tiles that must sum to a published NPV, two columns
that must balance, a future value that must equal the discounted present value run backwards), and
a validation is only tested by feeding it data that violates it — which an evaluator will not
produce on request. Building the timeline directly, in the style of `tests/test_economics_kernel.py`,
is what makes both the passing and the failing case expressible.

**What a failure means.** A failure here is a *derivation* failure in the chart layer: the engine's
numbers are unaffected, but a chart would draw something that does not reconcile with them — which
is exactly the class of defect the self-validating views exist to make impossible. It is distinct
from a rendering failure (`tests/test_economics_reporting.py`, the goldens) and from an engine
failure (`tests/test_economics_engine.py`); nothing here asserts on SVG or on matplotlib artists.
"""

# clean

import pytest

from hisim.economics import views
from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.presentation_style import PresentationStyle
from hisim.economics.results import (
    AnnualEnergyQuantities,
    CashFlowTimeline,
    LifecycleCo2Result,
    LifecycleCostResult,
    compare,
    cumulative_discounted_savings,
)
from hisim.economics.timeline import Actor, CashFlowEntry, CostCategory, SubjectKind, discount_factor
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.views import _terminal_zero_crossings

pytestmark = pytest.mark.base


def entry(
    year: int,
    amount: float,
    category: CostCategory,
    subject: str = "HeatPump",
    payer: Actor = Actor.SYSTEM,
    band: float = 0.0,
    subject_kind: SubjectKind = SubjectKind.COMPONENT,
    scheme_id: str = "",
) -> CashFlowEntry:
    """One timeline entry, with an optional symmetric band around the best estimate.

    `band` is a half-width in euros applied on both sides, which keeps the fixtures readable: a
    100 EUR entry with `band=20` is 80/100/120 for a cost and mirrors correctly for a credit,
    because the credit fixtures pass negative best estimates and a negative half-width is not
    needed — the constructor's own min <= best_estimate <= max check catches any fixture that gets
    it wrong.
    """
    amount_band = (
        UncertainValue.exact(amount)
        if not band
        else UncertainValue(best_estimate=amount, minimum=amount - band, maximum=amount + band)
    )
    return CashFlowEntry(
        year=year,
        amount_in_euro=amount_band,
        category=category,
        subject=subject,
        subject_kind=subject_kind,
        payer=payer,
        subsidy_scheme_id=scheme_id or None,
    )


def make_result(
    entries,
    horizon: int = 20,
    interest: float = 0.03,
    scope: ActorScope = ActorScope.SYSTEM,
    attribution=None,
    quantities=None,
) -> LifecycleCostResult:
    """A `LifecycleCostResult` built from a hand-written timeline, with consistent aggregates.

    The KPIs are derived through `calculators.aggregation.aggregate_timeline`, i.e. through the
    engine's own pivot rather than through hand-written totals: a fixture whose `total_npv_in_euro`
    disagreed with its timeline would make every reconciliation test meaningless. Nothing else of
    the evaluator runs — no cost database, no subsidy catalog, no calculators — so the entries are
    exactly what the test wrote.

    Args:
        entries: The timeline entries; sign validation is on, as it is for engine timelines.
        horizon: Observation period T.
        interest: Discount rate.
        scope: Which payer the perspective reports on.
        attribution: Optional per-subject energy attribution (V12's field).
        quantities: Optional per-carrier annual quantities (V12's middle column).

    Returns:
        The result, ready to hand to any view.
    """
    parameters = EconomicParameters(observation_period_in_years=horizon, interest_rate=interest)
    timeline = CashFlowTimeline()
    timeline.extend(entries)
    aggregation = aggregate_timeline(
        timeline=timeline,
        actor_scope=scope,
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
        annual_energy_attribution_by_subject_in_kwh=attribution or {},
        annual_energy_quantities_by_carrier=quantities or {},
    )


def simple_investment_timeline(horizon: int = 20):
    """A one-component fixture: buy in year 0, run it, get a residual credit at the horizon.

    The smallest timeline that still exercises the investment, energy, replacement and residual
    machinery the treemap and equity views read, and the base several tests below extend.
    """
    entries = [entry(0, 20000.0, CostCategory.INVESTMENT)]
    entries += [
        entry(year, 1000.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
              subject_kind=SubjectKind.CARRIER)
        for year in range(1, horizon + 1)
    ]
    entries.append(entry(horizon, -5000.0, CostCategory.RESIDUAL_VALUE))
    return entries


class TestCostStructureTiles:
    """V8: both bases reconcile, and the net variant discloses exactly what it clamped."""

    def make_pv_result(self) -> LifecycleCostResult:
        """A cost subject and a subject whose credits exceed its costs (the clamping case)."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(0, 8000.0, CostCategory.INVESTMENT, subject="PV"),
            entry(0, -12000.0, CostCategory.SUBSIDY, subject="PV", scheme_id="S"),
            entry(1, 900.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER),
        ]
        return make_result(entries)

    def test_gross_tiles_sum_to_the_gross_cost_npv(self):
        """Including the fold: the treemap's area is the whole cost side, never a sample of it."""
        tiles = views.cost_structure_tiles(
            self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.GROSS
        )
        assert sum(tile.area_in_euro for tile in tiles.tiles) == pytest.approx(
            tiles.gross_cost_npv_in_euro, abs=0.005
        )

    def test_gross_minus_credits_is_the_published_net_npv(self):
        """The caption's arithmetic, which is what stops the gross panel being read as the answer."""
        result = self.make_pv_result()
        tiles = views.cost_structure_tiles(result, PresentationStyle.CATEGORY_TO_GROUP)
        assert tiles.gross_cost_npv_in_euro - tiles.credit_total_in_euro == pytest.approx(
            result.total_npv_in_euro.best_estimate, abs=0.005
        )
        assert tiles.net_npv_in_euro == pytest.approx(result.total_npv_in_euro.best_estimate)

    def test_credit_only_cell_has_no_gross_tile_but_is_in_the_caption_total(self):
        """A pure credit cannot be an area; the caption carries it instead."""
        tiles = views.cost_structure_tiles(
            self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.GROSS
        )
        subsidy_group = PresentationStyle.CATEGORY_TO_GROUP[CostCategory.SUBSIDY]
        assert not [tile for tile in tiles.tiles if tile.group == subsidy_group]
        assert tiles.credit_total_in_euro == pytest.approx(12000.0)

    def make_part_subsidised_result(self) -> LifecycleCostResult:
        """A subject whose subsidy lands in a different display group than its investment.

        This is the case the per-cell netting could not see: the heat pump's investment is booked
        in the investment group and its subsidy in the support group, so a cell-wise subtraction
        finds no credit in the investment cell and leaves the net panel identical to the gross
        one. The heat pump also carries a service cost, so the proportional spread over a
        subject's several cost cells is exercised too.
        """
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(0, -5000.0, CostCategory.SUBSIDY, scheme_id="S"),
            entry(1, 400.0, CostCategory.MAINTENANCE),
            entry(1, 900.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER),
        ]
        return make_result(entries)

    def test_net_variant_discloses_the_clamped_total(self):
        """The euros the clamp erased are the sum of the negative subjects, named individually."""
        tiles = views.cost_structure_tiles(
            self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.NET_OF_CREDITS
        )
        clamped = tiles.clamped_tiles()
        assert [tile.subject for tile in clamped] == ["PV"], "PV earns more than it cost"
        assert tiles.clamped_total_in_euro == pytest.approx(
            -sum(tile.clamped_from_in_euro or 0.0 for tile in clamped), abs=0.005
        )
        assert tiles.clamped_total_in_euro == pytest.approx(12000.0 - 8000.0, abs=0.005)

    def test_net_areas_minus_erased_reproduce_the_published_net_npv(self):
        """The exact identity the net panel stands on, on both the clamping and the shrinking case."""
        for result in (self.make_pv_result(), self.make_part_subsidised_result()):
            tiles = views.cost_structure_tiles(
                result, PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.NET_OF_CREDITS
            )
            area = sum(tile.area_in_euro for tile in tiles.tiles)
            assert area - tiles.clamped_total_in_euro == pytest.approx(
                tiles.gross_cost_npv_in_euro - tiles.credit_total_in_euro, abs=0.005
            )
            assert area - tiles.clamped_total_in_euro == pytest.approx(
                result.total_npv_in_euro.best_estimate, abs=0.005
            )

    def test_credit_in_another_group_shrinks_the_subjects_cost_tiles(self):
        """The defect this netting exists for: a per-cell subtraction would change nothing here."""
        result = self.make_part_subsidised_result()
        gross = views.cost_structure_tiles(
            result, PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.GROSS
        )
        net = views.cost_structure_tiles(
            result, PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.NET_OF_CREDITS
        )
        gross_area = sum(tile.area_in_euro for tile in gross.tiles)
        net_area = sum(tile.area_in_euro for tile in net.tiles)
        assert net_area < gross_area - 4999.0, "the 5,000 EUR subsidy has to move the areas"
        assert not net.clamped_tiles(), "the subsidy is smaller than the cost, so nothing clamps"
        heat_pump_gross = sum(tile.area_in_euro for tile in gross.tiles if tile.subject == "HeatPump")
        heat_pump_net = sum(tile.area_in_euro for tile in net.tiles if tile.subject == "HeatPump")
        factor = (heat_pump_gross - 5000.0) / heat_pump_gross
        assert heat_pump_net == pytest.approx(heat_pump_gross * factor, abs=0.005)
        for tile in net.tiles:
            if tile.subject == "HeatPump":
                twin = [
                    other for other in gross.tiles
                    if other.subject == "HeatPump" and other.group == tile.group
                ]
                assert tile.area_in_euro == pytest.approx(twin[0].area_in_euro * factor, abs=0.005)

    def test_untouched_subject_keeps_its_gross_area_on_the_net_basis(self):
        """Netting is per subject, so a subject with no credits of its own must not move."""
        gross = views.cost_structure_tiles(
            self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.GROSS
        )
        net = views.cost_structure_tiles(
            self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP, views.TileBasis.NET_OF_CREDITS
        )
        heat_pump = [tile.area_in_euro for tile in net.tiles if tile.subject == "HeatPump"]
        assert heat_pump == [tile.area_in_euro for tile in gross.tiles if tile.subject == "HeatPump"]


class TestLifecycleLanes:
    """V9: the composition agrees with the views it delegates to, and stays inside the horizon."""

    def make_financed_result(self) -> LifecycleCostResult:
        """An investment financed by a five-year annuity, with a subsidy and a levy."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(0, -10000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        for year in range(1, 6):
            entries.append(entry(year, 300.0, CostCategory.LOAN_INTEREST, subject="financing"))
            entries.append(entry(year, 2000.0, CostCategory.LOAN_PRINCIPAL, subject="financing"))
        return make_result(entries, horizon=10)

    def test_loan_free_milestone_is_the_first_zero_balance_year(self):
        """The milestone reads the balance series rather than the plan's term."""
        result = self.make_financed_result()
        lanes = views.lifecycle_lanes(result)
        loan_free = [event for event in lanes.financing.events if event.label == "loan-free"]
        assert loan_free[0].year == views.loan_amortization_series(result).loan_free_year() == 5

    def test_payback_range_equals_the_band_crossing_interval(self):
        """The range bar is exactly the payback crossings — the same helper, not a second derivation."""
        reference = make_result(
            [entry(year, 2000.0, CostCategory.ENERGY_WORKING, subject="GAS",
                   subject_kind=SubjectKind.CARRIER) for year in range(1, 11)],
            horizon=10,
        )
        variant = make_result(
            [entry(0, 6000.0, CostCategory.INVESTMENT)]
            + [entry(year, 500.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                     subject_kind=SubjectKind.CARRIER) for year in range(1, 11)],
            horizon=10,
        )
        comparison = compare(reference, variant)
        lanes = views.lifecycle_lanes(variant, comparison)
        crossings = comparison.discounted_payback_years
        payback = [span for span in lanes.milestones.spans if "payback" in span.label]
        assert payback[0].start_year == crossings["low"]
        assert payback[0].end_year == crossings["high"]

    def test_all_lane_events_lie_inside_the_horizon(self):
        """Nothing is drawn off the axis, milestones included."""
        lanes = views.lifecycle_lanes(self.make_financed_result())
        events = list(lanes.milestones.events) + list(lanes.financing.events) + list(lanes.support.events)
        for event in events:
            assert 0 <= event.year <= lanes.horizon
        for row in lanes.assets:
            for item in row.events:
                assert 0 <= item.year <= lanes.horizon

    def test_unfinanced_result_has_an_empty_financing_lane(self):
        """A cash purchase drops the lane rather than drawing an empty one."""
        lanes = views.lifecycle_lanes(make_result(simple_investment_timeline()))
        assert lanes.financing.is_empty()


class TestSourcesAndUses:
    """V10: the statement balances, scheme nodes match the bookings, negative equity raises."""

    def make_funded_result(self) -> LifecycleCostResult:
        """Year 0 funded by two schemes, a loan and the rest from own capital."""
        entries = [
            entry(0, 30000.0, CostCategory.INVESTMENT),
            entry(0, 2000.0, CostCategory.PLANNING),
            entry(0, -6000.0, CostCategory.SUBSIDY, scheme_id="SCHEME_A"),
            entry(0, -3000.0, CostCategory.SUBSIDY, scheme_id="SCHEME_B"),
            entry(0, -12000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        return make_result(entries)

    def test_sources_equal_uses_equal_the_gross_year_zero_investment(self):
        """The double-entry property that makes this a statement rather than a picture."""
        statement = views.funding_sources_and_uses(self.make_funded_result())
        assert statement.total_sources_in_euro() == pytest.approx(32000.0)
        assert statement.total_uses_in_euro() == pytest.approx(32000.0)
        assert statement.gross_year_zero_investment_in_euro == pytest.approx(32000.0)

    def test_scheme_nodes_carry_their_own_amounts(self):
        """Per-scheme nodes are what makes 'which programme funds what' readable."""
        statement = views.funding_sources_and_uses(self.make_funded_result())
        amounts = {node.label: node.amount_in_euro for node in statement.sources}
        assert amounts["SCHEME_A"] == pytest.approx(6000.0)
        assert amounts["SCHEME_B"] == pytest.approx(3000.0)
        assert amounts["own capital"] == pytest.approx(32000.0 - 6000.0 - 3000.0 - 12000.0)

    def test_over_funded_year_zero_raises(self):
        """Support plus debt exceeding the investment is a data defect, not a rendering case."""
        entries = [
            entry(0, 10000.0, CostCategory.INVESTMENT),
            entry(0, -9000.0, CostCategory.SUBSIDY, scheme_id="S"),
            entry(0, -5000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        with pytest.raises(CostDataError, match="own capital"):
            views.funding_sources_and_uses(make_result(entries))

    def test_ribbons_preserve_both_column_totals(self):
        """The drawn allocation cannot invent or lose money on either side."""
        statement = views.funding_sources_and_uses(self.make_funded_result())
        ribbons = statement.ribbons()
        for node in statement.sources:
            drawn = sum(amount for source, _use, amount in ribbons if source == node.label)
            assert drawn == pytest.approx(node.amount_in_euro, abs=0.005)
        for node in statement.uses:
            drawn = sum(amount for _source, use, amount in ribbons if use == node.label)
            assert drawn == pytest.approx(node.amount_in_euro, abs=0.005)

    def test_own_capital_only_purchase_is_the_skip_condition(self):
        """A pure cash purchase has nothing the investment waterfall does not show better."""
        statement = views.funding_sources_and_uses(make_result(simple_investment_timeline()))
        assert not statement.has_external_funding()


class TestSubjectCategoryFlows:
    """V11: both margins of the pivot reconcile, and a PV-style subject keeps both ribbons."""

    def make_pv_result(self) -> LifecycleCostResult:
        """A subject with an investment *and* a revenue — the un-netting case."""
        entries = [
            entry(0, 12000.0, CostCategory.INVESTMENT, subject="PV"),
            entry(0, 20000.0, CostCategory.INVESTMENT, subject="HeatPump"),
            entry(1, -800.0, CostCategory.FEED_IN_REVENUE, subject="PV"),
            entry(1, 900.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER),
        ]
        return make_result(entries)

    def test_cost_minus_credit_is_the_published_component_npv(self):
        """Per subject, the two ribbon stacks net to what `npv_by_component` publishes."""
        result = self.make_pv_result()
        flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
        for subject, published in result.npv_by_component.items():
            cost_sum = sum(f.amount_in_euro for f in flows if f.subject == subject and not f.is_credit)
            credit_sum = sum(f.amount_in_euro for f in flows if f.subject == subject and f.is_credit)
            assert cost_sum - credit_sum == pytest.approx(published.best_estimate, abs=0.005)

    def test_group_ribbons_sum_to_the_folded_category_npv(self):
        """The other margin: per display group, the ribbons are the folded `npv_by_category`."""
        result = self.make_pv_result()
        flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
        folded = views.fold_categories(result.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
        for group, published in folded.items():
            drawn = sum(
                (-1.0 if flow.is_credit else 1.0) * flow.amount_in_euro
                for flow in flows
                if flow.group == group
            )
            assert drawn == pytest.approx(published.best_estimate, abs=0.005)

    def test_pv_subject_shows_both_a_cost_and_a_credit_ribbon(self):
        """Nothing is netted: the investment and the revenue are two ribbons, not one number."""
        flows = views.subject_category_flows(self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP)
        pv = [flow for flow in flows if flow.subject == "PV"]
        assert any(not flow.is_credit for flow in pv) and any(flow.is_credit for flow in pv)

    def test_the_margins_are_the_two_sides_of_the_component_breakdown(self):
        """Q28 R6: the sums the node labels print, against the table they claim to reconcile with.

        The node extent is costs plus the magnitude of credits — a quantity no table publishes,
        which is exactly why the chart looked wrong — and their *difference* is the subject's
        published NPV. Both directions are asserted so a label can never state one while the
        breakdown states the other.
        """
        result = self.make_pv_result()
        flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
        margins = views.subject_flow_margins(flows)
        for subject, published in result.npv_by_component.items():
            assert margins.net_of(subject) == pytest.approx(published.best_estimate, abs=0.005)
            assert margins.extent_of(subject) == pytest.approx(
                margins.costs_by_subject.get(subject, 0.0)
                + margins.credits_by_subject.get(subject, 0.0),
                abs=0.005,
            )
        folded = views.fold_categories(result.npv_by_category, PresentationStyle.CATEGORY_TO_GROUP)
        for group, published in folded.items():
            drawn = sum(
                total for (key, _is_credit), total in margins.signed_total_by_group.items()
                if key == group
            )
            assert drawn == pytest.approx(published.best_estimate, abs=0.005)

    def test_the_widest_subject_is_the_one_with_the_largest_stacked_node(self):
        """The caption's worked example is picked by extent, not by net value."""
        flows = views.subject_category_flows(self.make_pv_result(), PresentationStyle.CATEGORY_TO_GROUP)
        margins = views.subject_flow_margins(flows)
        assert margins.widest_subject() == "HeatPump"
        assert views.subject_flow_margins([]).widest_subject() is None


class TestEnergyBalanceFlows:
    """V12: the balance closes, the grid nodes match the meter, a meter-only record skips."""

    def make_balanced_result(self) -> LifecycleCostResult:
        """A PV/battery/heat-pump house whose flows balance exactly, with a year-1 bill."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(1, 1500.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER),
            entry(1, -600.0, CostCategory.FEED_IN_REVENUE, subject="ELECTRICITY_FEED_IN",
                  subject_kind=SubjectKind.CARRIER),
        ]
        # sources 9000 + 3000 + 900 = 12900; sinks 3500 + 3400 + 5000 + 1000 = 12900.
        attribution = {
            "PVSystem": {"PV_GENERATION": 9000.0},
            "ElectricityMeter": {"GRID_IMPORT": 3000.0, "GRID_EXPORT": 5000.0},
            "Battery": {"BATTERY_CHARGE": 1000.0, "BATTERY_DISCHARGE": 900.0},
            "HeatPump": {"HEAT_PUMP_ELECTRICITY": 3500.0},
            "UTSPConnector": {"HOUSEHOLD_ELECTRICITY": 3400.0},
        }
        return make_result(
            entries,
            attribution=attribution,
            quantities={"ELECTRICITY": AnnualEnergyQuantities(bought_in_kwh=3000.0, sold_in_kwh=5000.0)},
        )

    def test_flow_conservation_holds_at_every_node(self):
        """Both sides of the bus carry the same energy; that is what makes it a balance."""
        flows = views.energy_balance_flows(self.make_balanced_result())
        assert sum(node.quantity_in_kwh for node in flows.sources) == pytest.approx(12900.0)
        assert sum(node.quantity_in_kwh for node in flows.sinks) == pytest.approx(12900.0)
        assert flows.bus_total_in_kwh == pytest.approx(12900.0)

    def test_grid_nodes_equal_the_metered_quantities(self):
        """Import and export are the meter's own figures, or the euro annotation lies."""
        flows = views.energy_balance_flows(self.make_balanced_result())
        by_role = {node.role: node for node in flows.sources + flows.sinks}
        assert by_role[views.EnergyFlowRole.GRID_IMPORT].quantity_in_kwh == pytest.approx(3000.0)
        assert by_role[views.EnergyFlowRole.GRID_EXPORT].quantity_in_kwh == pytest.approx(5000.0)

    def test_euro_annotations_reconcile_with_the_year_one_bills(self):
        """Money only annotates the two grid nodes, and it is the bill, not a re-derivation."""
        result = self.make_balanced_result()
        flows = views.energy_balance_flows(result)
        bills = views.carrier_year_one_bills(result)
        by_role = {node.role: node for node in flows.sources + flows.sinks}
        assert by_role[views.EnergyFlowRole.GRID_IMPORT].annotation_in_euro == pytest.approx(
            bills["ELECTRICITY"].total_excluding_feed_in_in_euro
        )
        assert by_role[views.EnergyFlowRole.GRID_EXPORT].annotation_in_euro == pytest.approx(600.0)
        assert by_role[views.EnergyFlowRole.PV_GENERATION].annotation_in_euro is None

    def test_grid_node_disagreeing_with_the_meter_raises(self):
        """A balance whose import is not the metered import would price the wrong quantity."""
        result = make_result(
            [entry(1, 1500.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                   subject_kind=SubjectKind.CARRIER)],
            attribution={
                "ElectricityMeter": {"GRID_IMPORT": 100.0},
                "PVSystem": {"PV_GENERATION": 9000.0},
                "HeatPump": {"HEAT_PUMP_ELECTRICITY": 9100.0},
            },
            quantities={"ELECTRICITY": AnnualEnergyQuantities(bought_in_kwh=5000.0)},
        )
        with pytest.raises(CostDataError, match="GRID_IMPORT"):
            views.energy_balance_flows(result)

    def test_imbalance_becomes_its_own_node_rather_than_disappearing(self):
        """Battery losses and unmetered loads are energy; they get a node, not a rounding."""
        result = make_result(
            [entry(1, 100.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                   subject_kind=SubjectKind.CARRIER)],
            attribution={
                "PVSystem": {"PV_GENERATION": 9000.0},
                "HeatPump": {"HEAT_PUMP_ELECTRICITY": 4000.0},
            },
            quantities={"ELECTRICITY": AnnualEnergyQuantities(bought_in_kwh=0.0)},
        )
        flows = views.energy_balance_flows(result)
        residual = [
            node for node in flows.sinks if node.label == views.EnergyBalanceLayout.RESIDUAL_LABEL
        ]
        assert residual and residual[0].quantity_in_kwh == pytest.approx(5000.0)
        assert sum(node.quantity_in_kwh for node in flows.sinks) == pytest.approx(9000.0)

    def test_caption_shares_come_from_the_same_flows(self):
        """Self-consumption and autarky are derived here, not recomputed by a renderer."""
        flows = views.energy_balance_flows(self.make_balanced_result())
        assert flows.self_consumption_share == pytest.approx((9000.0 - 5000.0) / 9000.0)
        assert flows.self_sufficiency_share == pytest.approx((6900.0 - 3000.0) / 6900.0)
        assert flows.battery_round_trip_loss_in_kwh == pytest.approx(100.0)

    def test_meter_only_attribution_skips_instead_of_rendering(self):
        """Decision Q16: a content-free record is a skip, not a picture of a meter."""
        result = make_result(
            simple_investment_timeline(),
            attribution={"ElectricityMeter": {"GRID_IMPORT": 2714.0, "GRID_EXPORT": 17262.0}},
        )
        assert not views.has_energy_balance(result)
        with pytest.raises(CostDataError, match="device flows"):
            views.energy_balance_flows(result)

    def test_missing_attribution_raises_a_located_error(self):
        """The skip predicate exists precisely so this error is never reached by a report."""
        result = make_result(simple_investment_timeline())
        assert not views.has_energy_balance(result)
        with pytest.raises(CostDataError, match="annual_energy_attribution_by_subject_in_kwh"):
            views.energy_balance_flows(result)

    def test_an_unknown_role_is_reported_rather_than_dropped(self):
        """A role this reader cannot place stays visible instead of vanishing from the balance.

        A record written by a newer extraction can name a role the `EnergyFlowRole` of this
        version does not have. Such a flow has no side of the busbar, so it cannot become a
        terminal without inventing a direction — and it must not be absorbed by the residual
        either, which is computed from the drawn terminals alone and would therefore hide it
        completely. `unattributed_roles_in_kwh` is where it surfaces, named and quantified.
        """
        result = make_result(
            [entry(1, 100.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                   subject_kind=SubjectKind.CARRIER)],
            attribution={
                "PVSystem": {"PV_GENERATION": 9000.0},
                "HeatPump": {"HEAT_PUMP_ELECTRICITY": 4000.0},
                "WindTurbine": {"WIND_GENERATION": 2500.0},
            },
            quantities={"ELECTRICITY": AnnualEnergyQuantities(bought_in_kwh=0.0)},
        )
        assert views.EnergyFlowRole.__members__.get("WIND_GENERATION") is None
        quantities = views.energy_balance_quantities(result)
        assert all(role.value != "WIND_GENERATION" for role in quantities)
        flows = views.energy_balance_flows(result)
        assert flows.unattributed_roles_in_kwh == {"WIND_GENERATION": 2500.0}
        residual = [
            node for node in flows.sinks if node.label == views.EnergyBalanceLayout.RESIDUAL_LABEL
        ]
        assert residual[0].quantity_in_kwh == pytest.approx(5000.0), (
            "the unplaceable role must not be folded into the residual, which would hide it"
        )
        assert flows.bus_total_in_kwh == pytest.approx(9000.0)

    def test_a_known_only_record_reports_no_unattributed_roles(self):
        """The honest empty case: nothing was dropped, so nothing is disclosed."""
        flows = views.energy_balance_flows(self.make_balanced_result())
        assert flows.unattributed_roles_in_kwh == {}


class TestWealthBenchmark:
    """V13: the future-value identity holds per rate, and a double sign change reports twice."""

    def make_pair(self, investment: float = 6000.0, saving: float = 800.0, horizon: int = 15):
        """A renovation that costs money up front and saves a fixed amount every year."""
        reference = make_result(
            [entry(year, saving, CostCategory.ENERGY_WORKING, subject="GAS",
                   subject_kind=SubjectKind.CARRIER) for year in range(1, horizon + 1)],
            horizon=horizon,
        )
        variant = make_result([entry(0, investment, CostCategory.INVESTMENT)], horizon=horizon)
        return reference, variant

    def test_terminal_wealth_is_the_npv_run_forwards(self):
        """`W_i(T) == (1+i)^T · NPV(i)` for every grid rate — the identity the chart rests on."""
        reference, variant = self.make_pair()
        benchmark = views.wealth_benchmark(reference, variant)
        horizon = variant.parameters.observation_period_in_years
        for rate in benchmark.rates:
            npv = sum(
                flow * discount_factor(rate, year)
                for year, flow in enumerate(benchmark.differential_flow_in_euro)
            )
            assert benchmark.terminal_by_rate[rate] == pytest.approx(
                ((1.0 + rate) ** horizon) * npv, abs=0.01
            )

    def test_verdict_at_the_parameter_rate_agrees_with_the_npv_delta(self):
        """If the variant has the lower present cost, the renovator ends up richer."""
        reference, variant = self.make_pair()
        benchmark = views.wealth_benchmark(reference, variant)
        delta = variant.total_npv_in_euro.best_estimate - reference.total_npv_in_euro.best_estimate
        assert (benchmark.terminal_at_parameter_rate() > 0) == (delta < 0)

    def test_break_even_rates_stay_inside_the_grid_window(self):
        """Nothing is extrapolated outside the rates actually drawn."""
        reference, variant = self.make_pair(investment=9000.0, saving=800.0)
        benchmark = views.wealth_benchmark(reference, variant)
        for crossing in benchmark.break_even_rates:
            assert benchmark.rates[0] <= crossing <= benchmark.rates[-1]

    def test_a_double_sign_change_reports_both_crossings(self):
        """A non-unique internal rate is reported as several, never collapsed into one."""
        crossings = _terminal_zero_crossings({0.01: -100.0, 0.02: 50.0, 0.03: -20.0})
        assert len(crossings) == 2

    def test_cumulative_savings_and_the_benchmark_use_the_same_differential(self):
        """The chart's flows are the payback curve's flows, undiscounted."""
        reference, variant = self.make_pair()
        benchmark = views.wealth_benchmark(reference, variant)
        savings = cumulative_discounted_savings(reference, variant)
        rate = variant.parameters.interest_rate
        assert savings["best_estimate"][-1] == pytest.approx(
            sum(flow * discount_factor(rate, year)
                for year, flow in enumerate(benchmark.differential_flow_in_euro)),
            abs=0.01,
        )


class TestMonthlyBurden:
    """V14: the recurring subset re-sums, and the excluded categories appear nowhere."""

    def make_result_with_investment(self) -> LifecycleCostResult:
        """A year-0 investment plus recurring energy, maintenance and a replacement spike."""
        entries = [
            entry(0, 30000.0, CostCategory.INVESTMENT),
            entry(0, -9000.0, CostCategory.SUBSIDY, scheme_id="S"),
        ]
        entries += [
            entry(year, 1200.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER, band=120.0)
            for year in range(1, 11)
        ]
        entries += [entry(year, 300.0, CostCategory.MAINTENANCE) for year in range(1, 11)]
        entries.append(entry(8, 5000.0, CostCategory.REPLACEMENT))
        return make_result(entries, horizon=10)

    def test_year_zero_carries_no_burden(self):
        """The financing event is not a monthly burden; the funding statement shows it instead."""
        burden = views.monthly_burden_series(self.make_result_with_investment())
        assert burden.series[0].best_estimate == 0.0

    def test_twelve_times_year_one_is_the_recurring_annual_figure(self):
        """The unit conversion is exactly that — twelve months of the same year."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        recurring = sum(
            item.amount_in_euro.best_estimate
            for item in result.timeline.entries
            if item.year == 1 and item.category in views.BurdenCategories.RECURRING
        )
        assert burden.series[1].best_estimate * 12.0 == pytest.approx(recurring)

    def test_series_re_sums_to_the_recurring_subset_of_the_annual_series(self):
        """Nothing is lost or gained between the annual view and the monthly one."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        for year, monthly in enumerate(burden.series):
            recurring = sum(
                item.amount_in_euro.best_estimate
                for item in result.timeline.entries
                if item.year == year and item.category in views.BurdenCategories.RECURRING
            )
            assert monthly.best_estimate * 12.0 == pytest.approx(recurring)

    def test_excluded_categories_appear_in_no_bar(self):
        """Investment, planning, removal, support, disbursement and residual are all out."""
        excluded = {
            CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL,
            CostCategory.SUBSIDY, CostCategory.LOAN_DISBURSEMENT, CostCategory.RESIDUAL_VALUE,
            CostCategory.ANYWAY_COST_CREDIT, CostCategory.CO2_DAMAGE,
        }
        assert not excluded & views.BurdenCategories.RECURRING

    def test_no_capital_event_appears_in_any_bar(self):
        """Q15 revised: a replacement year is capital expenditure, exactly like year 0."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        assert not views.BurdenCategories.REPLACEMENT & views.BurdenCategories.RECURRING
        assert burden.series[8].best_estimate == pytest.approx(burden.series[7].best_estimate)

    def test_reserve_reconciles_with_the_replacement_npv_via_the_annuity_factor(self):
        """The dashed line is the replacement NPV smoothed by the module's own EAC machinery."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        replacement_npv = sum(
            value.best_estimate
            for category, value in result.npv_by_category.items()
            if category in views.BurdenCategories.REPLACEMENT
        )
        assert burden.replacement_reserve_per_month > 0.0
        assert burden.replacement_reserve_per_month * 12.0 / result.parameters.annuity_factor() == (
            pytest.approx(replacement_npv)
        )

    def test_no_replacement_means_no_reserve_line(self):
        """An evaluation without replacements gets no line rather than a flat zero one."""
        entries = [entry(year, 600.0, CostCategory.MAINTENANCE) for year in range(1, 11)]
        burden = views.monthly_burden_series(make_result(entries, horizon=10))
        assert burden.replacement_reserve_per_month == 0.0

    def test_group_split_re_sums_to_the_totals(self):
        """The stacked bars and the whiskered totals are the same money."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        rows = views.monthly_burden_by_group(result, PresentationStyle.CATEGORY_TO_GROUP)
        for year, row in enumerate(rows):
            assert sum(row.values()) == pytest.approx(burden.series[year].best_estimate)


class TestAssetDebtSeries:
    """V15: the endpoint is the residual, install years step, and equity is the difference."""

    def make_financed_asset(self) -> LifecycleCostResult:
        """A 20,000 EUR asset with a residual at the horizon, financed by a 10-year annuity."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(0, -20000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
            entry(20, -5000.0, CostCategory.RESIDUAL_VALUE),
        ]
        for year in range(1, 11):
            entries.append(entry(year, 400.0, CostCategory.LOAN_INTEREST, subject="financing"))
            entries.append(entry(year, 2000.0, CostCategory.LOAN_PRINCIPAL, subject="financing"))
        return make_result(entries)

    def test_horizon_book_value_equals_the_booked_residual_credit(self):
        """The chart's endpoint *is* the residual calculation, tied end to end."""
        series = views.asset_debt_series(self.make_financed_asset())
        assert series.book_value_in_euro[-1] == pytest.approx(5000.0, abs=0.005)
        assert series.residual_credit_in_euro == pytest.approx(5000.0)

    def test_install_year_steps_by_the_charged_investment(self):
        """Year 0's book value is what the timeline charged, not a depreciated figure."""
        series = views.asset_debt_series(self.make_financed_asset())
        assert series.book_value_in_euro[0] == pytest.approx(20000.0)

    def test_equity_is_the_exact_difference_of_the_two_published_series(self):
        """No third derivation: the gap is what the two lines leave between them."""
        series = views.asset_debt_series(self.make_financed_asset())
        for book, debt, equity in zip(
            series.book_value_in_euro, series.debt_in_euro, series.equity_in_euro
        ):
            assert equity == pytest.approx(book - debt)

    def test_replacement_steps_the_book_value_back_up(self):
        """A replacement is a new asset on the same row, so the curve jumps at its year."""
        entries = [
            entry(0, 10000.0, CostCategory.INVESTMENT),
            entry(10, 12000.0, CostCategory.REPLACEMENT),
            entry(20, -6000.0, CostCategory.RESIDUAL_VALUE),
            entry(0, -10000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
            entry(1, 10000.0, CostCategory.LOAN_PRINCIPAL, subject="financing"),
        ]
        series = views.asset_debt_series(make_result(entries))
        assert series.book_value_in_euro[10] > series.book_value_in_euro[9]

    def test_underwater_interval_is_detected(self):
        """Debt above book value is exactly what a lender checks for, so it is reported."""
        entries = [
            entry(0, 10000.0, CostCategory.INVESTMENT),
            entry(0, -10000.0, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        for year in range(1, 21):
            entries.append(entry(year, 200.0, CostCategory.LOAN_INTEREST, subject="financing"))
        entries.append(entry(20, 10000.0, CostCategory.LOAN_PRINCIPAL, subject="financing"))
        series = views.asset_debt_series(make_result(entries))
        assert series.underwater_interval is not None
        start, end = series.underwater_interval
        assert start >= 1 and end <= 20
        assert all(series.equity_in_euro[year] < 0 for year in range(start, end + 1))
