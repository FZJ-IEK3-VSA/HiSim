"""Unit tests for the chart views of the visualization extension (visualization spec §5).

Every chart of the V1-V15 set gets its numbers from a view function, and every one of those views
either carries an invariant it validates at runtime or has a reconciliation a reviewer is expected
to check by hand. This module is the second half of that promise: one test per row of the
visualization spec's §5 invariant table, on **hand-built timelines** rather than on evaluated
runs, so that the expected figures are arithmetic a reader can redo on paper.

**Why hand-built.** `tests/test_economics_views.py` pins the older views against an evaluated
result, which is the right shape for views that re-arrange a real evaluation. The chart views are
different: most of them *validate* something (a transfer that must net to zero, bars that must sum
to a band, two columns that must balance), and a validation is only tested by feeding it data that
violates it — which an evaluator will not produce on request. Building the timeline directly, in
the style of `tests/test_economics_kernel.py`, is what makes both the passing and the failing case
expressible.

**What a failure means.** A failure here is a *derivation* failure in the chart layer: the engine's
numbers are unaffected, but a chart would draw something that does not reconcile with them — which
is exactly the class of defect the self-validating views exist to make impossible. It is distinct
from a rendering failure (`test_economics_reporting.py`, the goldens) and from an engine failure
(`test_economics_engine.py`).

The one deliberate exception to "hand-built" is `TestWorkedExampleActorFlows`, which re-states the
§559e worked example's expected actor-flow matrix as a direct assertion (owner decision Q7); see
its docstring for why the figures live here rather than in the workbook.
"""

# clean

import dataclasses
import re

import matplotlib
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  — backend must be set before pyplot
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402

from hisim.economics import report_plots, reporting, views  # noqa: E402
from hisim.economics.calculators.aggregation import aggregate_timeline  # noqa: E402
from hisim.economics.catalog_entries import CostDataError  # noqa: E402
from hisim.economics.parameters import EconomicParameters  # noqa: E402
from hisim.economics.perspectives import ActorScope  # noqa: E402
from hisim.economics.presentation_style import (  # noqa: E402
    PresentationStyle,
    SankeyLayout,
    _place_nodes,
    sankey_node_boxes,
)
from hisim.economics.results import (  # noqa: E402
    CashFlowTimeline,
    LifecycleCo2Result,
    LifecycleCostResult,
    compare,
    cumulative_discounted_savings,
    discounted_payback_year,
)
from hisim.economics.timeline import Actor, CashFlowEntry, CostCategory, SubjectKind, discount_factor  # noqa: E402
from hisim.economics.uncertainty import Slot, UncertainValue  # noqa: E402

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
    """One timeline entry, with an optional symmetric band around the average.

    `band` is a half-width in euros applied on both sides, which keeps the fixtures readable: a
    100 EUR entry with `band=20` is 80/100/120 for a cost and mirrors correctly for a credit,
    because the credit fixtures pass negative averages and a negative half-width is not needed —
    the constructor's own min <= avg <= max check catches any fixture that gets it wrong.
    """
    amount_band = (
        UncertainValue.exact(amount)
        if not band
        else UncertainValue(average=amount, minimum=amount - band, maximum=amount + band)
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
        energy_attribution_by_subject_in_kwh=attribution or {},
        annual_energy_quantities_by_carrier=quantities or {},
    )


def simple_investment_timeline(horizon: int = 20):
    """A one-component fixture: buy in year 0, run it, get a residual credit at the horizon.

    The smallest timeline that still exercises the investment, energy, replacement and residual
    machinery the strip, treemap and equity views read, and the base most tests below extend.
    """
    entries = [entry(0, 20000.0, CostCategory.INVESTMENT)]
    entries += [entry(year, 1000.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                      subject_kind=SubjectKind.CARRIER) for year in range(1, horizon + 1)]
    entries.append(entry(horizon, -5000.0, CostCategory.RESIDUAL_VALUE))
    return entries


class TestActorFlowMatrix:
    """V1: transfers net to zero, per-actor nets reconcile, an unmapped category raises."""

    def make_levy_result(self) -> LifecycleCostResult:
        """A landlord/tenant case: the landlord invests, the tenant pays energy and the levy."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT, payer=Actor.LANDLORD),
            entry(0, -6000.0, CostCategory.SUBSIDY, payer=Actor.LANDLORD, scheme_id="SCHEME_A"),
            entry(1, 900.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                  subject_kind=SubjectKind.CARRIER, payer=Actor.TENANT),
            entry(1, 600.0, CostCategory.MODERNIZATION_LEVY, payer=Actor.TENANT),
            entry(1, -600.0, CostCategory.MODERNIZATION_LEVY, payer=Actor.LANDLORD),
        ]
        return make_result(entries)

    def test_transfer_ribbons_net_to_zero_across_payers(self):
        """The levy is drawn tenant -> landlord once, not as two external stubs."""
        matrix = views.actor_flow_matrix(self.make_levy_result())
        transfers = [flow for flow in matrix.flows if flow.is_transfer]
        assert len(transfers) == 1
        assert (transfers[0].source, transfers[0].target) == (Actor.TENANT.value, Actor.LANDLORD.value)
        assert transfers[0].amount_in_euro == pytest.approx(600.0)

    def test_per_actor_net_equals_the_scoped_nominal_sum(self):
        """Outflows minus inflows per actor is that actor's nominal lifetime cost."""
        result = self.make_levy_result()
        nets = views.actor_flow_matrix(result).net_by_actor()
        for actor in (Actor.LANDLORD, Actor.TENANT):
            expected = sum(
                item.amount_in_euro.average
                for item in result.timeline.entries
                if item.payer == actor
            )
            assert nets[actor.value] == pytest.approx(expected, abs=0.01)

    def test_unbalanced_transfer_raises(self):
        """A levy pair that does not cancel means the allocation created money."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT, payer=Actor.LANDLORD),
            entry(1, 600.0, CostCategory.MODERNIZATION_LEVY, payer=Actor.TENANT),
            entry(1, -400.0, CostCategory.MODERNIZATION_LEVY, payer=Actor.LANDLORD),
        ]
        with pytest.raises(CostDataError, match="net to zero"):
            views.actor_flow_matrix(make_result(entries))

    def test_unmapped_category_raises(self):
        """A category with no declared counterparty is a defect, not a default bucket."""
        with pytest.raises(CostDataError, match="counterparty"):
            views.FlowCounterparties.counterparty_of(CostCategory.MODERNIZATION_LEVY)

    def test_every_non_transfer_category_has_a_counterparty(self):
        """The taxonomy is total: no engine category can fall through it unnoticed."""
        for category in CostCategory:
            if category in views.FlowCounterparties.TRANSFER_CATEGORIES:
                continue
            assert views.FlowCounterparties.counterparty_of(category)

    def test_single_actor_result_has_one_node(self):
        """The skip condition of the chart is visible in the view's own output."""
        matrix = views.actor_flow_matrix(make_result(simple_investment_timeline()))
        assert matrix.actors == [Actor.SYSTEM.value]


class TestLiquidityFanSeries:
    """V2: the cumulative series re-sums the annual one; the crossing helper agrees with payback."""

    def test_cumulative_nominal_matches_the_annual_series_slotwise(self):
        """Slot-wise cumsum of the published annual series, recomputed the dumb way."""
        result = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT, band=1000.0)]
            + [entry(year, 500.0, CostCategory.MAINTENANCE, band=50.0) for year in range(1, 6)],
            horizon=5,
        )
        series = views.cumulative_nominal_cost_series(result)
        for slot in Slot:
            running = 0.0
            for year, band in enumerate(result.annual_cost_series_nominal_in_euro):
                running += band.slot(slot)
                assert series[slot][year] == pytest.approx(running)

    def test_zero_crossing_helper_agrees_with_discounted_payback_year(self):
        """`band_zero_crossings` is `discounted_payback_year` applied per slot, not a copy of it."""
        curves = {
            "low": [-100.0, -50.0, 10.0, 40.0],
            "average": [-100.0, -80.0, -20.0, 5.0],
            "high": [-100.0, -90.0, -70.0, -60.0],
        }
        crossings = views.band_zero_crossings(curves)
        assert crossings == {slot: discounted_payback_year(curve) for slot, curve in curves.items()}
        assert crossings == {"low": 2, "average": 3, "high": None}

    def test_worst_liquidity_position_is_the_curve_maximum(self):
        """Cost is plotted upward, so the deepest out-of-pocket point is the maximum (Q4)."""
        result = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT)]
            + [entry(year, -3000.0, CostCategory.FEED_IN_REVENUE, subject="ELECTRICITY",
                     subject_kind=SubjectKind.CARRIER) for year in range(1, 5)],
            horizon=4,
        )
        assert views.worst_liquidity_position(result) == (0, pytest.approx(10000.0))


class TestUncertaintyAttribution:
    """V3: the deltas sum to the band edges, fold included, and mirrored revenue lands correctly."""

    def make_banded_result(self, subjects: int = 12) -> LifecycleCostResult:
        """Enough subjects to trigger the top-N fold, plus one mirrored revenue subject."""
        entries = [
            entry(0, 1000.0 * (index + 1), CostCategory.INVESTMENT, subject=f"Device{index}",
                  band=100.0 * (index + 1))
            for index in range(subjects)
        ]
        entries.append(
            CashFlowEntry(
                year=1,
                amount_in_euro=UncertainValue(average=-500.0, minimum=-700.0, maximum=-300.0),
                category=CostCategory.FEED_IN_REVENUE,
                subject="PV",
                subject_kind=SubjectKind.CARRIER,
            )
        )
        return make_result(entries)

    def test_deltas_sum_to_the_total_band_including_the_fold(self):
        """The invariant the view validates, checked independently of the view's own check."""
        result = self.make_banded_result()
        rows = views.uncertainty_attribution(result)
        total = result.total_npv_in_euro
        assert sum(row.low_delta_in_euro for row in rows) == pytest.approx(
            total.minimum - total.average, abs=0.005
        )
        assert sum(row.high_delta_in_euro for row in rows) == pytest.approx(
            total.maximum - total.average, abs=0.005
        )

    def test_rows_are_folded_to_the_declared_cut_off(self):
        """Thirteen subjects become ten rows plus one fold — no silent dropping."""
        rows = views.uncertainty_attribution(self.make_banded_result())
        assert len(rows) == views.AttributionThresholds.TOP_N + 1
        assert rows[-1].is_fold and rows[-1].subject == views.AttributionThresholds.FOLD_LABEL

    def test_mirrored_revenue_subject_lands_on_the_correct_side(self):
        """A credit's optimistic world is the one where it earns more, so its LOW delta is negative."""
        result = make_result(
            [
                entry(0, 10000.0, CostCategory.INVESTMENT, band=1000.0),
                CashFlowEntry(
                    year=1,
                    amount_in_euro=UncertainValue(average=-500.0, minimum=-700.0, maximum=-300.0),
                    category=CostCategory.FEED_IN_REVENUE,
                    subject="PV",
                    subject_kind=SubjectKind.CARRIER,
                ),
            ]
        )
        rows = {row.subject: row for row in views.uncertainty_attribution(result)}
        assert rows["PV"].low_delta_in_euro < 0 < rows["PV"].high_delta_in_euro
        assert rows["HeatPump"].low_delta_in_euro < 0 < rows["HeatPump"].high_delta_in_euro

    def test_degenerate_band_attributes_nothing(self):
        """An exact result yields all-zero deltas rather than raising — the renderer skips it."""
        rows = views.uncertainty_attribution(make_result(simple_investment_timeline()))
        assert all(row.low_delta_in_euro == 0.0 and row.high_delta_in_euro == 0.0 for row in rows)


class TestComparisonBridge:
    """V4: the steps sum to the NPV delta, and a one-sided subject folds in."""

    def test_steps_sum_to_the_published_npv_delta(self):
        """The bridge's own validation, checked against the two results' published totals."""
        reference = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT)]
            + [entry(year, 2000.0, CostCategory.ENERGY_WORKING, subject="GAS",
                     subject_kind=SubjectKind.CARRIER) for year in range(1, 11)],
            horizon=10,
        )
        variant = make_result(
            [entry(0, 25000.0, CostCategory.INVESTMENT)]
            + [entry(year, 900.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                     subject_kind=SubjectKind.CARRIER) for year in range(1, 11)],
            horizon=10,
        )
        steps = views.comparison_bridge(reference, variant, PresentationStyle.CATEGORY_TO_GROUP)
        expected = variant.total_npv_in_euro.average - reference.total_npv_in_euro.average
        assert sum(step.delta_in_euro for step in steps) == pytest.approx(expected, abs=0.005)

    def test_group_present_in_one_variant_only_folds_in(self):
        """A group the reference does not have is its own step, at its full variant value."""
        reference = make_result([entry(0, 10000.0, CostCategory.INVESTMENT)], horizon=5)
        variant = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT),
             entry(0, -2000.0, CostCategory.SUBSIDY, scheme_id="S")],
            horizon=5,
        )
        steps = {step.group: step.delta_in_euro for step in views.comparison_bridge(
            reference, variant, PresentationStyle.CATEGORY_TO_GROUP
        )}
        subsidy_group = PresentationStyle.CATEGORY_TO_GROUP[CostCategory.SUBSIDY]
        assert steps[subsidy_group] == pytest.approx(-2000.0)

    def test_step_order_is_the_display_group_order(self):
        """IBCS: stable semantics across reports, so steps are never sorted by magnitude."""
        reference = make_result([entry(0, 10000.0, CostCategory.INVESTMENT)], horizon=5)
        variant = make_result(
            [entry(0, 12000.0, CostCategory.INVESTMENT),
             entry(1, 500.0, CostCategory.MAINTENANCE),
             entry(0, -2000.0, CostCategory.SUBSIDY, scheme_id="S")],
            horizon=5,
        )
        groups = [step.group for step in views.comparison_bridge(
            reference, variant, PresentationStyle.CATEGORY_TO_GROUP
        )]
        assert groups == sorted(groups)


class TestLoanViews:
    """V5: the balance runs to zero for both plan shapes, and the grant shows up as a credit."""

    def annuity_entries(self, principal: float = 10000.0, rate: float = 0.04, term: int = 10):
        """A textbook annuity schedule booked the way `financing_application` books it."""
        from hisim.economics.financing import FinancingPlan, loan_flows

        plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
        disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
        entries = [
            entry(0, principal, CostCategory.INVESTMENT),
            entry(0, -disbursement.average, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        for year, interest, repayment in schedule:
            entries.append(entry(year, interest.average, CostCategory.LOAN_INTEREST, subject="financing"))
            entries.append(entry(year, repayment.average, CostCategory.LOAN_PRINCIPAL, subject="financing"))
        return entries

    def bullet_entries(self, principal: float = 10000.0, rate: float = 0.05, term: int = 8):
        """An interest-only loan with the whole principal repaid in the final year."""
        entries = [
            entry(0, principal, CostCategory.INVESTMENT),
            entry(0, -principal, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        for year in range(1, term + 1):
            entries.append(entry(year, principal * rate, CostCategory.LOAN_INTEREST, subject="financing"))
        entries.append(entry(term, principal, CostCategory.LOAN_PRINCIPAL, subject="financing"))
        return entries

    def test_annuity_balance_ends_at_zero(self):
        """A fully amortizing annuity leaves no debt at the end of its term."""
        amortization = views.loan_amortization_series(make_result(self.annuity_entries()))
        assert amortization.disbursement_in_euro == pytest.approx(10000.0)
        assert amortization.outstanding_balance_in_euro[-1] == pytest.approx(0.0, abs=0.01)
        assert amortization.loan_free_year() == 10

    def test_bullet_balance_stays_flat_and_then_drops(self):
        """An interest-only plan repays nothing until the bullet year."""
        amortization = views.loan_amortization_series(make_result(self.bullet_entries()))
        assert amortization.outstanding_balance_in_euro[7] == pytest.approx(10000.0)
        assert amortization.outstanding_balance_in_euro[8] == pytest.approx(0.0, abs=0.01)
        assert amortization.loan_free_year() == 8

    def test_total_cost_of_credit_decomposes_the_repayment(self):
        """Principal + interest + fees - grants is what the loan entries add up to."""
        result = make_result(self.annuity_entries())
        credit = views.total_cost_of_credit(result)
        interest = sum(
            item.amount_in_euro.average
            for item in result.timeline.entries
            if item.category == CostCategory.LOAN_INTEREST
        )
        assert credit.principal_in_euro == pytest.approx(10000.0)
        assert credit.interest_in_euro == pytest.approx(interest)
        assert credit.fees_in_euro == 0.0
        assert credit.total_repaid_in_euro == pytest.approx(10000.0 + interest)

    def test_effective_rate_equals_the_nominal_rate_for_a_plain_annuity(self):
        """The null test: no fees, no grant, annual periods — Effektivzins == nominal rate."""
        credit = views.total_cost_of_credit(make_result(self.annuity_entries(rate=0.04)))
        assert credit.effective_annual_rate == pytest.approx(0.04, abs=1e-6)

    def test_a_repayment_grant_strictly_lowers_the_effective_rate(self):
        """A grant is money received without more debt, so the borrower's rate falls."""
        entries = self.annuity_entries()
        entries.append(entry(0, -1500.0, CostCategory.SUBSIDY, subject="financing", scheme_id="SOFT"))
        credit = views.total_cost_of_credit(make_result(entries))
        assert credit.grants_in_euro == pytest.approx(1500.0)
        assert credit.effective_annual_rate is not None
        assert credit.effective_annual_rate < 0.04

    def test_unfinanced_result_has_no_flows(self):
        """The skip condition every loan chart shares."""
        amortization = views.loan_amortization_series(make_result(simple_investment_timeline()))
        assert not amortization.has_flows()


class TestTimelineHeatmapNumbers:
    """V6: the matrix the heatmap draws reconciles by rows and by columns."""

    def test_column_sums_equal_the_nominal_annual_series(self):
        """Each year's column adds up to that year's published nominal figure."""
        result = make_result(simple_investment_timeline(horizon=6), horizon=6)
        matrix = views.nominal_annual_matrix_by_category(result)
        for year, row in enumerate(matrix):
            assert sum(row.values()) == pytest.approx(
                result.annual_cost_series_nominal_in_euro[year].average
            )

    def test_row_sums_equal_the_per_category_nominal_totals(self):
        """Each category's row adds up to what that category booked over the horizon."""
        result = make_result(simple_investment_timeline(horizon=6), horizon=6)
        matrix = views.nominal_annual_matrix_by_category(result)
        for category in {item.category for item in result.timeline.entries}:
            expected = sum(
                item.amount_in_euro.average
                for item in result.timeline.entries
                if item.category == category
            )
            assert sum(row.get(category, 0.0) for row in matrix) == pytest.approx(expected)


class TestComponentEventStrip:
    """V7: the residual gate is an output property, and spans tile the horizon per row."""

    def test_residual_without_an_investment_raises(self):
        """The package-A gate: only a charged installation may be written down."""
        entries = [
            entry(1, 500.0, CostCategory.MAINTENANCE, subject="Kept"),
            entry(20, -3000.0, CostCategory.RESIDUAL_VALUE, subject="Kept"),
        ]
        with pytest.raises(CostDataError, match="residual"):
            views.component_event_strip(make_result(entries))

    def test_service_spans_tile_the_horizon_without_overlap(self):
        """Spans run event to event and the last one to the horizon; none overlap."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT),
            entry(15, 22000.0, CostCategory.REPLACEMENT),
            entry(20, -5000.0, CostCategory.RESIDUAL_VALUE),
        ]
        rows = views.component_event_strip(make_result(entries))
        spans = rows[0].spans
        assert [(span.start_year, span.end_year) for span in spans] == [(0, 15), (15, 20)]
        for left, right in zip(spans, spans[1:]):
            assert left.end_year <= right.start_year

    def test_rows_are_sorted_by_year_zero_investment(self):
        """Biggest asset first, so the row order is a statement rather than an accident."""
        entries = [
            entry(0, 5000.0, CostCategory.INVESTMENT, subject="Small"),
            entry(0, 30000.0, CostCategory.INVESTMENT, subject="Large"),
        ]
        rows = views.component_event_strip(make_result(entries))
        assert [row.subject for row in rows] == ["Large", "Small"]

    def test_carrier_subjects_are_not_rows(self):
        """Energy is billed per carrier, not per device; only components get a lane."""
        rows = views.component_event_strip(make_result(simple_investment_timeline()))
        assert [row.subject for row in rows] == ["HeatPump"]


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
            result.total_npv_in_euro.average, abs=0.005
        )
        assert tiles.net_npv_in_euro == pytest.approx(result.total_npv_in_euro.average)

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
                result.total_npv_in_euro.average, abs=0.005
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
                twin = [other for other in gross.tiles if other.subject == "HeatPump"
                        and other.group == tile.group]
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
        assert heat_pump == [
            tile.area_in_euro for tile in gross.tiles if tile.subject == "HeatPump"
        ]


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
        """The range bar is exactly V2's interval — the same helper, not a second derivation."""
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
        crossings = views.band_zero_crossings(comparison.cumulative_discounted_savings_in_euro)
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
            costs = sum(f.amount_in_euro for f in flows if f.subject == subject and not f.is_credit)
            credits = sum(f.amount_in_euro for f in flows if f.subject == subject and f.is_credit)
            assert costs - credits == pytest.approx(published.average, abs=0.005)

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
            assert drawn == pytest.approx(published.average, abs=0.005)

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
            assert margins.net_of(subject) == pytest.approx(published.average, abs=0.005)
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
            assert drawn == pytest.approx(published.average, abs=0.005)

    def test_the_rendered_nodes_and_ribbons_carry_those_amounts(self):
        """Q28 R6: every node states its sides, every ribbon its exact euros, and the caption reconciles."""
        import re

        result = self.make_pv_result()
        flows = views.subject_category_flows(result, PresentationStyle.CATEGORY_TO_GROUP)
        margins = views.subject_flow_margins(flows)
        html = reporting._subject_flows_section_html(
            result, reporting._ChapterContext(chapter=("building", "The building"))
        )
        assert html, "the fixture has two subjects, so the section renders"
        for subject in margins.costs_by_subject:
            cost = margins.costs_by_subject[subject]
            credit = margins.credits_by_subject.get(subject, 0.0)
            expected = f"costs {reporting._fmt(cost)}"
            if credit:
                expected += f" | credits -{reporting._fmt(credit)}"
            # Small nodes degrade to their total, with the split kept in the tooltip.
            assert expected in html or f"{subject}: costs {cost:,.2f} EUR" in html
        for flow in flows:
            assert f"{flow.amount_in_euro:,.2f} EUR" in html
        widest = margins.widest_subject()
        caption = re.search(r"<b>How a block reconciles\.</b>(.*?)</p>", html, re.S).group(1)
        assert reporting._fmt(margins.extent_of(widest)) in caption
        assert reporting._fmt(margins.net_of(widest)) in caption
        assert reporting._fmt(result.npv_by_component[widest].average) in caption


class TestEnergyBalanceFlows:
    """V12: the balance closes, the grid nodes match the meter, a meter-only record skips."""

    def make_balanced_result(self) -> LifecycleCostResult:
        """A PV/battery/heat-pump house whose flows balance exactly, with a year-1 bill."""
        from hisim.economics.results import AnnualEnergyQuantities

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
            quantities={
                "ELECTRICITY": AnnualEnergyQuantities(bought_in_kwh=3000.0, sold_in_kwh=5000.0)
            },
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
        from hisim.economics.results import AnnualEnergyQuantities

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
        from hisim.economics.results import AnnualEnergyQuantities

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
            node for node in flows.sinks
            if node.label == views.EnergyBalanceLayout.RESIDUAL_LABEL
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
        with pytest.raises(CostDataError, match="energy_attribution_by_subject_in_kwh"):
            views.energy_balance_flows(result)


# ------------------------------------------------------------------ Sankey geometry (rule 2.7)

def _svg_ribbon_widths(svg: str):
    """Every ribbon path of an inline-SVG Sankey as (left width, right width) in user units.

    Parses the `d` attribute the renderer emits — `M … C … L … C … Z`, sixteen numbers — rather
    than trusting the renderer's own variables, because the point of these tests is that what
    lands in the file has constant-width ribbons. Index 1 and 15 are the two y of the left face,
    7 and 9 the two y of the right face.
    """
    widths = []
    for path in re.findall(r'<path d="(M [^"]+)"', svg):
        numbers = [float(value) for value in re.findall(r"-?\d+\.?\d*", path)]
        if len(numbers) != 16:
            continue
        widths.append((numbers[15] - numbers[1], numbers[9] - numbers[7]))
    return widths


def _svg_ribbon_faces(svg: str):
    """Every ribbon path as (left x, left y-top, right x, right y-top, width), in user units."""
    faces = []
    for path in re.findall(r'<path d="(M [^"]+)"', svg):
        numbers = [float(value) for value in re.findall(r"-?\d+\.?\d*", path)]
        if len(numbers) != 16:
            continue
        faces.append((numbers[0], numbers[1], numbers[6], numbers[7], numbers[15] - numbers[1]))
    return faces


def _svg_ribbon_curves(svg: str):
    """Every ribbon path as its sixteen raw path numbers, for curve sampling (Q29 R7)."""
    curves = []
    for path in re.findall(r'<path d="(M [^"]+)"', svg):
        numbers = [float(value) for value in re.findall(r"-?\d+\.?\d*", path)]
        if len(numbers) == 16:
            curves.append(numbers)
    return curves


def _bezier(points, t: float):
    """A point on a cubic Bézier — the actual curve the browser draws, not the chord."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    u = 1.0 - t
    return (
        u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
        u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
    )


def _svg_stub_rects(svg: str):
    """The net-position stubs as (x, y, width, height); they carry a class the node rects do not."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect class="net-stub" x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"',
            svg,
        )
    ]


def _svg_node_rects(svg: str):
    """The node rectangles of an inline-SVG Sankey as (x, y, width, height)."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]


def _crossings(anchors, boxes, ribbons):
    """Pairwise anchor-order inversions between ribbons sharing a column pair — the crossing count.

    Two ribbons cross when their vertical order at the source end is the opposite of their order
    at the target end. Counting inversions is the standard proxy for "how tangled is this
    picture"; it is exact for straight ribbons and monotone in the same direction for the Bézier
    ones actually drawn.
    """
    total = 0
    for first in range(len(ribbons)):
        for second in range(first + 1, len(ribbons)):
            source_a, target_a, _ = ribbons[first]
            source_b, target_b, _ = ribbons[second]
            if boxes[source_a][0] != boxes[source_b][0] or boxes[target_a][0] != boxes[target_b][0]:
                continue
            start = (boxes[source_a][1] + anchors[first][0]) - (boxes[source_b][1] + anchors[second][0])
            end = (boxes[target_a][1] + anchors[first][1]) - (boxes[target_b][1] + anchors[second][1])
            if start * end < 0:
                total += 1
    return total


def _naive_anchors(ribbons, boxes, unit_scale):
    """Ribbon anchors without any ordering: stacked in the order the caller listed the flows.

    The baseline the crossing-minimization test compares against — what the layout produced
    before Q19, and what a renderer would do if it simply appended.
    """
    anchors = []
    out_offset, in_offset = {}, {}
    for source, target, amount in ribbons:
        anchors.append((out_offset.get(source, 0.0), in_offset.get(target, 0.0)))
        out_offset[source] = out_offset.get(source, 0.0) + amount * unit_scale
        in_offset[target] = in_offset.get(target, 0.0) + amount * unit_scale
    return anchors


class TestSankeyGeometry:
    """Rule 2.7 and Q19, asserted on what each renderer actually emits, not on the layout's math.

    The defect these pin was visible only in the output: the layout normalized every column to
    fill the drawing height, so a middle column carrying each unit twice got half its neighbours'
    scale and every ribbon changed width in flight. The tests therefore parse the SVG path data
    and read the matplotlib patch vertices back off the axis.
    """

    def tangled_diagram(self):
        """A three-column diagram whose given node order is deliberately the tangled one.

        Every source feeds the actor whose listed position is furthest from its own, so the naive
        layout crosses on both sides; the middle column carries each unit twice, which is exactly
        the situation that produced the two different per-column scales.
        """
        columns = [["bank", "state", "market"], ["landlord", "tenant"], ["fees", "energy", "works"]]
        ribbons = [
            ("bank", "tenant", 400.0),
            ("state", "tenant", 300.0),
            ("market", "landlord", 500.0),
            ("landlord", "energy", 200.0),
            ("landlord", "fees", 300.0),
            ("tenant", "works", 500.0),
            ("tenant", "energy", 200.0),
        ]
        return columns, ribbons

    def coloured(self, ribbons, color="#2a78d6"):
        """The same flows in the (source, target, amount, colour, is_credit) shape renderers take."""
        return [(source, target, amount, color, False) for source, target, amount in ribbons]

    def test_one_global_unit_scale_across_all_columns(self):
        """The defect itself: every node's height is the same euros-per-pixel, column-independent."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        carried = {}
        for source, target, amount in ribbons:
            carried[source] = carried.get(source, 0.0) + amount
        incoming = {}
        for source, target, amount in ribbons:
            incoming[target] = incoming.get(target, 0.0) + amount
        for node, (_x, _y, height) in geometry.boxes.items():
            value = max(carried.get(node, 0.0), incoming.get(node, 0.0))
            assert height == pytest.approx(value * geometry.unit_scale)

    def test_svg_ribbons_keep_one_width_end_to_end(self):
        """A ribbon is one flow; the two ends of its path must measure the same."""
        columns, ribbons = self.tangled_diagram()
        svg = reporting._sankey_svg(columns, self.coloured(ribbons), {})
        widths = _svg_ribbon_widths(svg)
        assert len(widths) == len(ribbons)
        for left, right in widths:
            assert left == pytest.approx(right, abs=0.2)

    def test_svg_ribbons_tile_each_node_face(self):
        """The ribbons on a node's fuller face fill its rectangle exactly, with no overflow."""
        columns, ribbons = self.tangled_diagram()
        svg = reporting._sankey_svg(columns, self.coloured(ribbons), {})
        faces = _svg_ribbon_faces(svg)
        for x, y, width, height in _svg_node_rects(svg):
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in faces
                if abs(left_x - (x + width)) < 0.5 and y - 0.5 <= left_y + band / 2 <= y + height + 0.5
            )
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in faces
                if abs(right_x - x) < 0.5 and y - 0.5 <= right_y + band / 2 <= y + height + 0.5
            )
            assert max(outgoing, incoming) == pytest.approx(height, abs=0.3)

    def test_matplotlib_ribbons_keep_one_width_end_to_end(self):
        """The PNG companion draws the same geometry; its patch vertices say so."""
        columns, ribbons = self.tangled_diagram()
        figure, axis = plt.subplots()
        report_plots._draw_sankey(axis, columns, self.coloured(ribbons))
        bands = [
            patch.get_path().vertices
            for patch in axis.patches if isinstance(patch, PathPatch)
        ]
        plt.close(figure)
        assert len(bands) == len(ribbons)
        for vertices in bands:
            left_width = vertices[0][1] - vertices[7][1]
            right_width = vertices[3][1] - vertices[4][1]
            assert left_width == pytest.approx(right_width, abs=1e-12)

    def test_matplotlib_ribbons_tile_each_node_face(self):
        """Same tiling check on the raster side: node rectangles are exactly filled."""
        columns, ribbons = self.tangled_diagram()
        figure, axis = plt.subplots()
        report_plots._draw_sankey(axis, columns, self.coloured(ribbons))
        bands, rectangles = [], []
        for patch in axis.patches:
            if isinstance(patch, PathPatch):
                vertices = patch.get_path().vertices
                bands.append((
                    float(vertices[7][0]), float(vertices[7][1]),
                    float(vertices[4][0]), float(vertices[4][1]),
                    float(vertices[0][1] - vertices[7][1]),
                ))
            elif isinstance(patch, Rectangle):
                rectangles.append((
                    patch.get_x(), patch.get_y(), patch.get_width(), patch.get_height()
                ))
        plt.close(figure)
        for x, y, width, height in rectangles:
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in bands
                if abs(left_x - (x + width)) < 1e-6 and y - 1e-6 <= left_y + band / 2 <= y + height + 1e-6
            )
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in bands
                if abs(right_x - x) < 1e-6 and y - 1e-6 <= right_y + band / 2 <= y + height + 1e-6
            )
            assert max(outgoing, incoming) == pytest.approx(height, abs=1e-9)

    def test_ordering_reduces_crossings_against_the_unordered_layout(self):
        """Q19: barycenter node order plus anchor sorting untangles a deliberately tangled input."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        naive_boxes = _place_nodes(
            columns,
            {
                node: max(
                    sum(a for s, _t, a in ribbons if s == node),
                    sum(a for _s, t, a in ribbons if t == node),
                )
                for column in columns for node in column
            },
            geometry.unit_scale,
        )
        before = _crossings(
            _naive_anchors(ribbons, naive_boxes, geometry.unit_scale), naive_boxes, ribbons
        )
        after = _crossings(geometry.ribbon_anchors, geometry.boxes, ribbons)
        assert before > 0
        assert after <= before

    def test_layout_is_deterministic(self):
        """A report re-rendered from the same result must be byte-identical; no hash order here."""
        columns, ribbons = self.tangled_diagram()
        first = sankey_node_boxes(columns, ribbons)
        second = sankey_node_boxes(columns, ribbons)
        assert first.boxes == second.boxes
        assert first.ribbon_anchors == second.ribbon_anchors
        assert first.ribbon_segments == second.ribbon_segments
        assert first.net_stubs == second.net_stubs
        assert first.unit_scale == second.unit_scale

    def skipping_diagram(self):
        """Four columns in which two ribbons skip a column each, in both directions of imbalance.

        The shape of run 1's rented view reduced to its defect: sources paying a party two columns
        along (their ribbons used to cross the intervening party's block), a party paying a sink
        two columns along, and a middle party that receives more than it passes on (its outgoing
        face used to be a third empty).
        """
        columns = [["bank", "state"], ["tenant"], ["landlord"], ["market", "suppliers"]]
        ribbons = [
            ("bank", "landlord", 600.0),
            ("state", "landlord", 300.0),
            ("tenant", "landlord", 500.0),
            ("tenant", "suppliers", 200.0),
            ("landlord", "market", 700.0),
        ]
        return columns, ribbons

    def test_a_column_skipping_ribbon_is_routed_through_a_corridor(self):
        """Q29 R7: it becomes one leg per column gap, with a virtual node holding the space."""
        columns, ribbons = self.skipping_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        chains = geometry.ribbon_segments
        assert [len(chain) for chain in chains] == [2, 2, 1, 2, 1]
        virtual = [node for node in geometry.boxes if node.startswith(SankeyLayout.VIRTUAL_NODE_PREFIX)]
        assert len(virtual) == 3
        for node in virtual:
            assert node not in [name for column in columns for name in column]
        # A corridor node is exactly as tall as the ribbon it carries — that is what reserving
        # the space means, and it is why the ribbon has somewhere to go.
        for chain, (_source, _target, amount) in zip(chains, ribbons):
            for leg in chain:
                for node in (leg.source, leg.target):
                    if node.startswith(SankeyLayout.VIRTUAL_NODE_PREFIX):
                        assert geometry.boxes[node][2] == pytest.approx(amount * geometry.unit_scale)

    def test_no_ribbon_path_intersects_a_node_rectangle(self):
        """The Q29 invariant, sampled off the actual Bézier the report emits — not a chord."""
        columns, ribbons = self.skipping_diagram()
        svg = reporting._sankey_svg(columns, self.coloured(ribbons), {})
        nodes = _svg_node_rects(svg)
        assert nodes
        hits = []
        for numbers in _svg_ribbon_curves(svg):
            top = [(numbers[0], numbers[1]), (numbers[2], numbers[3]),
                   (numbers[4], numbers[5]), (numbers[6], numbers[7])]
            band = numbers[15] - numbers[1]
            for step in range(201):
                x, y = _bezier(top, step / 200.0)
                for node_x, node_y, node_w, node_h in nodes:
                    inside_x = node_x + 0.05 < x < node_x + node_w - 0.05
                    inside_y = node_y - 0.05 < y + band / 2.0 < node_y + node_h + 0.05
                    if inside_x and inside_y:
                        hits.append((x, y))
        assert hits == []

    def test_both_faces_of_every_node_tile_at_full_height(self):
        """Q29 R7: with the net stub counted, no face of any node is left partly empty."""
        columns, ribbons = self.skipping_diagram()
        svg = reporting._sankey_svg(columns, self.coloured(ribbons), {})
        faces = _svg_ribbon_faces(svg)
        stubs = _svg_stub_rects(svg)
        assert stubs, "the landlord receives 1,400 and pays out 700; that gap must be drawn"
        for x, y, width, height in _svg_node_rects(svg):
            outgoing = sum(
                band for left_x, left_y, _rx, _ry, band in faces
                if abs(left_x - (x + width)) < 0.5 and y - 0.5 <= left_y + band / 2 <= y + height + 0.5
            ) + sum(stub_h for stub_x, _sy, _sw, stub_h in stubs if abs(stub_x - (x + width)) < 0.5)
            incoming = sum(
                band for _lx, _ly, right_x, right_y, band in faces
                if abs(right_x - x) < 0.5 and y - 0.5 <= right_y + band / 2 <= y + height + 0.5
            ) + sum(
                stub_h for stub_x, _sy, stub_w, stub_h in stubs if abs(stub_x + stub_w - x) < 0.5
            )
            for used in (outgoing, incoming):
                if used > 0.5:  # a first-column source has no incoming face at all
                    assert used == pytest.approx(height, abs=0.3)

    def test_the_stub_is_the_nodes_own_net_position(self):
        """The stub's size is the imbalance itself, and its label is the caller's wording for it."""
        columns, ribbons = self.skipping_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        stubs = {stub.node: stub for stub in geometry.net_stubs}
        assert set(stubs) == {"landlord"}
        assert stubs["landlord"].amount == pytest.approx(1400.0 - 700.0)
        assert stubs["landlord"].is_outgoing
        svg = reporting._sankey_svg(
            columns, self.coloured(ribbons), {}, stub_labels={"landlord": "net -700 EUR"}
        )
        assert "net -700 EUR" in svg
        assert "net +700 EUR" not in svg

    def test_the_matplotlib_twin_draws_the_same_corridors_and_stubs(self):
        """The PNG companion routes and closes identically; a stub is labelled, not a node."""
        columns, ribbons = self.skipping_diagram()
        figure, axis = plt.subplots()
        report_plots._draw_sankey(axis, columns, self.coloured(ribbons))
        bands = [patch for patch in axis.patches if isinstance(patch, PathPatch)]
        stubs = [
            patch for patch in axis.patches
            if isinstance(patch, Rectangle) and patch.get_label() == report_plots._SankeyStyle.STUB_LABEL
        ]
        plt.close(figure)
        assert len(bands) == 8, "five ribbons, three of which are cut into two legs"
        assert len(stubs) == 1
        assert stubs[0].get_height() == pytest.approx(
            700.0 * sankey_node_boxes(columns, ribbons).unit_scale
        )

    def test_a_transfer_is_an_ordinary_ribbon_between_distinct_columns(self):
        """Q23: each party has its own column, so the §559e levy is a plain left-to-right ribbon.

        The levy used to connect two nodes of one shared middle column, which has no horizontal
        extent, so both renderers drew it as a band looping out of the column and back — the one
        mark on the page that did not read left to right, and a special case in two places. With
        the payer's column placed before the payee's it is a ribbon like any other: the same
        constant width as every other flow, and its two ends at *different* x.
        """
        columns = [["market"], ["tenant"], ["landlord"], ["works"]]
        ribbons = [
            ("market", "landlord", 1000.0),
            ("tenant", "landlord", 400.0),
            ("landlord", "works", 1400.0),
        ]
        geometry = sankey_node_boxes(columns, ribbons)
        assert geometry.boxes["landlord"][2] == pytest.approx(1400.0 * geometry.unit_scale)
        assert geometry.boxes["tenant"][2] == pytest.approx(400.0 * geometry.unit_scale)
        assert geometry.boxes["tenant"][0] < geometry.boxes["landlord"][0]
        svg = reporting._sankey_svg(columns, self.coloured(ribbons), {})
        for left, right in _svg_ribbon_widths(svg):
            assert left == pytest.approx(right, abs=0.2)
        # Every ribbon in the file travels: no zero-width horizontal span anywhere.
        for left_x, _left_y, right_x, _right_y, _width in _svg_ribbon_faces(svg):
            assert right_x > left_x

    def test_the_levy_column_order_puts_the_payer_before_the_payee(self):
        """The view's topological sort, on the shape the 559e worked example produces."""
        matrix = views.ActorFlowMatrix(
            flows=[
                views.ActorFlow(source="tenant", target="landlord", amount_in_euro=600.0,
                                category=CostCategory.MODERNIZATION_LEVY, is_transfer=True),
                views.ActorFlow(source="landlord", target="market", amount_in_euro=1000.0,
                                category=CostCategory.INVESTMENT),
            ],
            actors=["landlord", "tenant"],
            sources=[],
            sinks=["market"],
            total_band=UncertainValue.exact(1600.0),
        )
        assert matrix.actor_columns() == [["tenant"], ["landlord"]]

    def test_actor_columns_are_deterministic_without_transfers(self):
        """No transfer means no ordering constraint, so the timeline order decides — stably."""
        matrix = views.ActorFlowMatrix(
            flows=[
                views.ActorFlow(source="landlord", target="market", amount_in_euro=1000.0,
                                category=CostCategory.INVESTMENT),
                views.ActorFlow(source="tenant", target="suppliers", amount_in_euro=500.0,
                                category=CostCategory.ENERGY_WORKING),
            ],
            actors=["landlord", "tenant"],
            sources=[],
            sinks=["market", "suppliers"],
            total_band=UncertainValue.exact(1500.0),
        )
        assert matrix.actor_columns() == [["landlord"], ["tenant"]]
        assert matrix.actor_columns() == matrix.actor_columns()


class TestLandlordStatement:
    """Q21/Q25: the landlord's cash and his book value are separated, and both pictures agree.

    The section exists because a landlord perspective can publish a strongly negative NPV — an
    advantage — of which only part is money. The invariant that makes the split trustworthy is
    that it is a *partition*: cash subtotal plus accounting subtotal is the perspective's NPV, to
    the cent, because nothing is recomputed. The Sankey below the table draws the same partition,
    so its ribbons have to reconcile with it as well.
    """

    def landlord_result(self, net_cost: bool = False):
        """A landlord perspective: investment and maintenance out, levy, subsidy and credits in.

        With `net_cost` the levy is small enough that the renovation is a net cost to the
        landlord, which is the other sign of the income Sankey's leftover ribbon.
        """
        levy = 400.0 if net_cost else 3000.0
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT, payer=Actor.LANDLORD),
            entry(0, -4000.0, CostCategory.SUBSIDY, payer=Actor.LANDLORD),
            entry(5, 900.0, CostCategory.REPLACEMENT, payer=Actor.LANDLORD),
            entry(20, -5000.0, CostCategory.RESIDUAL_VALUE, payer=Actor.LANDLORD),
            entry(2, -3000.0, CostCategory.ANYWAY_COST_CREDIT, payer=Actor.LANDLORD),
        ]
        entries.extend(
            entry(year, 300.0, CostCategory.MAINTENANCE, payer=Actor.LANDLORD) for year in range(1, 21)
        )
        entries.extend(
            entry(year, -levy, CostCategory.MODERNIZATION_LEVY, subject="modernization levy",
                  payer=Actor.LANDLORD)
            for year in range(1, 21)
        )
        return make_result(entries, scope=ActorScope.LANDLORD)

    def test_the_two_sides_partition_the_perspective_npv(self):
        """The invariant: cash + accounting == the landlord's NPV, exactly."""
        statement = views.landlord_statement(self.landlord_result())
        assert statement.cash_subtotal_in_euro + statement.accounting_subtotal_in_euro == pytest.approx(
            statement.net_position_in_euro
        )
        assert statement.net_position_in_euro == pytest.approx(
            statement.net_position_band.average, abs=0.005
        )

    def test_the_accounting_side_is_exactly_the_two_non_cash_categories(self):
        """Residual value and the anyway credit, and nothing else, are book entries."""
        statement = views.landlord_statement(self.landlord_result())
        assert {line.category for line in statement.accounting_lines} == {
            CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT
        }
        assert all(not line.is_accounting_credit for line in statement.cash_lines)
        assert CostCategory.MODERNIZATION_LEVY in {line.category for line in statement.cash_lines}

    def test_a_dropped_category_is_refused_rather_than_drawn(self):
        """The partition is validated: a statement that does not reconcile raises (D25)."""
        result = self.landlord_result()
        broken = dataclasses.replace(
            result,
            npv_by_category={
                category: band for category, band in result.npv_by_category.items()
                if category != CostCategory.MAINTENANCE
            },
        )
        with pytest.raises(CostDataError, match="does not reconcile"):
            views.landlord_statement(broken)

    def test_the_income_sankey_ribbons_reconcile_with_the_statement(self):
        """Income minus expenses is the net position — the identity the picture rests on."""
        statement = views.landlord_statement(self.landlord_result())
        flows, net_is_inflow = statement.income_flows()
        node = views.LandlordStatementCategories.LANDLORD_NODE
        net_node = views.LandlordStatementCategories.NET_POSITION_NODE
        income = sum(amount for _s, target, amount, _c, _cat in flows if target == node)
        expense = sum(amount for source, _t, amount, _c, _cat in flows if source == node)
        # The leftover ribbon *is* the bottom line, so it is one of the two sums above.
        assert not net_is_inflow  # this fixture is advantageous for the landlord
        assert income - expense == pytest.approx(0.0, abs=0.01)
        leftover = [amount for _s, target, amount, _c, _cat in flows if target == net_node]
        assert leftover and leftover[0] == pytest.approx(-statement.net_position_in_euro)

    def test_a_net_cost_draws_the_bottom_line_as_an_inflow(self):
        """The other sign: a loss enters from the left, because the money has to come from somewhere."""
        statement = views.landlord_statement(self.landlord_result(net_cost=True))
        assert statement.net_position_in_euro > 0
        flows, net_is_inflow = statement.income_flows()
        node = views.LandlordStatementCategories.LANDLORD_NODE
        net_node = views.LandlordStatementCategories.NET_POSITION_NODE
        assert net_is_inflow
        assert any(source == net_node and target == node for source, target, _a, _c, _cat in flows)

    def test_the_accounting_ribbons_are_drawn_in_the_credit_style(self):
        """Hatched/translucent, so the cash-versus-book split is visible in the picture (Q25)."""
        statement = views.landlord_statement(self.landlord_result())
        flows, _net = statement.income_flows()
        credit_categories = {
            category for _s, _t, _a, is_credit, category in flows if is_credit
        }
        assert credit_categories == {CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT}

    def test_the_rendered_sankey_keeps_constant_ribbon_widths(self):
        """Rule 2.7 applies to the new chart like to every other Sankey."""
        statement = views.landlord_statement(self.landlord_result())
        svg = reporting._landlord_statement_sankey(statement)
        widths = _svg_ribbon_widths(svg)
        assert widths
        for left, right in widths:
            assert left == pytest.approx(right, abs=0.2)


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
        delta = variant.total_npv_in_euro.average - reference.total_npv_in_euro.average
        assert (benchmark.terminal_at_parameter_rate() > 0) == (delta < 0)

    def test_break_even_rates_stay_inside_the_grid_window(self):
        """Nothing is extrapolated outside the rates actually drawn."""
        reference, variant = self.make_pair(investment=9000.0, saving=800.0)
        benchmark = views.wealth_benchmark(reference, variant)
        for crossing in benchmark.break_even_rates:
            assert benchmark.rates[0] <= crossing <= benchmark.rates[-1]

    def test_a_double_sign_change_reports_both_crossings(self):
        """A non-unique internal rate is reported as several, never collapsed into one."""
        crossings = views._terminal_zero_crossings({0.01: -100.0, 0.02: 50.0, 0.03: -20.0})
        assert len(crossings) == 2

    def test_cumulative_savings_and_the_benchmark_use_the_same_differential(self):
        """The chart's flows are the payback curve's flows, undiscounted."""
        reference, variant = self.make_pair()
        benchmark = views.wealth_benchmark(reference, variant)
        savings = cumulative_discounted_savings(reference, variant)
        rate = variant.parameters.interest_rate
        assert savings["average"][-1] == pytest.approx(
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
        entries += [entry(year, 1200.0, CostCategory.ENERGY_WORKING, subject="ELECTRICITY",
                          subject_kind=SubjectKind.CARRIER, band=120.0) for year in range(1, 11)]
        entries += [entry(year, 300.0, CostCategory.MAINTENANCE) for year in range(1, 11)]
        entries.append(entry(8, 5000.0, CostCategory.REPLACEMENT))
        return make_result(entries, horizon=10)

    def test_year_zero_carries_no_burden(self):
        """The financing event is not a monthly burden; the funding statement shows it instead."""
        burden = views.monthly_burden_series(self.make_result_with_investment())
        assert burden.series[0].average == 0.0

    def test_twelve_times_year_one_is_the_recurring_annual_figure(self):
        """The unit conversion is exactly that — twelve months of the same year."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        recurring = sum(
            item.amount_in_euro.average
            for item in result.timeline.entries
            if item.year == 1 and item.category in views.BurdenCategories.RECURRING
        )
        assert burden.series[1].average * 12.0 == pytest.approx(recurring)

    def test_series_re_sums_to_the_recurring_subset_of_the_annual_series(self):
        """Nothing is lost or gained between the annual view and the monthly one."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        for year in range(len(burden.series)):
            recurring = sum(
                item.amount_in_euro.average
                for item in result.timeline.entries
                if item.year == year and item.category in views.BurdenCategories.RECURRING
            )
            assert burden.series[year].average * 12.0 == pytest.approx(recurring)

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
        assert burden.series[8].average == pytest.approx(burden.series[7].average)

    def test_reserve_reconciles_with_the_replacement_npv_via_the_annuity_factor(self):
        """The dashed line is the replacement NPV smoothed by the module's own EAC machinery."""
        result = self.make_result_with_investment()
        burden = views.monthly_burden_series(result)
        replacement_npv = sum(
            value.average
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
            assert sum(row.values()) == pytest.approx(burden.series[year].average)


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


class TestWorkedExampleActorFlows:
    """The §559e worked example's expected actor-flow matrix (owner decision Q7).

    The spec asks for this attestation to live in the workbook
    (`tests/worked_examples/end_to_end/heating_levy_559e_mixed_package.xlsx`) so that the Sankey's
    numbers are attested like every other figure of that example. That workbook carries a content
    fingerprint and a human review attestation (§3.8), which an automated edit cannot renew, so
    the expected matrix is hand-computed **here** instead and the workbook extension is deferred.
    The figures below are taken from that example's own yaml: a 30,000 EUR heat pump with a 9,000
    EUR grant and a 50,000 EUR insulation with a 10,000 EUR grant, and a capped rent increase of
    3,600 EUR a year over 20 years, which the tenant pays and the landlord receives.
    """

    HORIZON = 20
    ANNUAL_LEVY = 3600.0

    def make_worked_example_result(self) -> LifecycleCostResult:
        """The §559e package as a timeline, landlord and tenant tagged as the ruleset tags them."""
        entries = [
            entry(0, 30000.0, CostCategory.INVESTMENT, subject="HeatPump", payer=Actor.LANDLORD),
            entry(0, 50000.0, CostCategory.INVESTMENT, subject="WallInsulation", payer=Actor.LANDLORD),
            entry(0, -9000.0, CostCategory.SUBSIDY, subject="HeatPump", payer=Actor.LANDLORD,
                  scheme_id="HEATING_GRANT"),
            entry(0, -10000.0, CostCategory.SUBSIDY, subject="WallInsulation", payer=Actor.LANDLORD,
                  scheme_id="ENVELOPE_GRANT"),
        ]
        for year in range(1, self.HORIZON + 1):
            entries.append(entry(year, self.ANNUAL_LEVY, CostCategory.MODERNIZATION_LEVY,
                                 subject="HeatPump", payer=Actor.TENANT))
            entries.append(entry(year, -self.ANNUAL_LEVY, CostCategory.MODERNIZATION_LEVY,
                                 subject="HeatPump", payer=Actor.LANDLORD))
        return make_result(entries, horizon=self.HORIZON, interest=0.03)

    def test_expected_actor_flow_matrix(self):
        """Every ribbon of the three-party case, hand-entered from the worked example."""
        matrix = views.actor_flow_matrix(self.make_worked_example_result())
        drawn = {
            (flow.source, flow.target): flow.amount_in_euro
            for flow in matrix.flows
        }
        expected = {
            ("landlord", "market"): 80000.0,           # both investments, nominal
            ("state", "landlord"): 19000.0,            # both grants, nominal
            ("tenant", "landlord"): 20 * 3600.0,       # the capped levy over the horizon
        }
        assert set(drawn) == set(expected)
        for pair, amount in expected.items():
            assert drawn[pair] == pytest.approx(amount, abs=0.01)

    def test_expected_actor_nets(self):
        """Landlord and tenant nets, which must equal each side's nominal lifetime cost."""
        result = self.make_worked_example_result()
        nets = views.actor_flow_matrix(result).net_by_actor()
        assert nets["tenant"] == pytest.approx(20 * self.ANNUAL_LEVY)
        assert nets["landlord"] == pytest.approx(80000.0 - 19000.0 - 20 * self.ANNUAL_LEVY)
        assert nets["landlord"] + nets["tenant"] == pytest.approx(80000.0 - 19000.0)


class TestPartyStatements:
    """Q26 F4: the owner, the tenant and society get the landlord's treatment, and it reconciles.

    The rule-2.9 review found three of the four parties publishing a single number where the
    landlord published a statement. The generalization is only worth anything if the invariant
    generalizes with it, so what is checked here is the same one in all three: the two sides of
    the partition are a partition — they sum to the perspective's NPV to the cent, because
    nothing is recomputed on the way. The tenant additionally has to mirror the landlord's levy
    income exactly, since the two are the halves of one booked transfer pair.
    """

    ANNUAL_LEVY = 3000.0

    def rented_entries(self):
        """One rented case with both parties on it: levy pair, investment, subsidy and bills."""
        entries = [
            entry(0, 20000.0, CostCategory.INVESTMENT, payer=Actor.LANDLORD),
            entry(0, -4000.0, CostCategory.SUBSIDY, payer=Actor.LANDLORD),
            entry(20, -5000.0, CostCategory.RESIDUAL_VALUE, payer=Actor.LANDLORD),
            entry(2, -3000.0, CostCategory.ANYWAY_COST_CREDIT, payer=Actor.LANDLORD),
        ]
        for year in range(1, 21):
            entries.append(entry(year, 300.0, CostCategory.MAINTENANCE, payer=Actor.LANDLORD))
            entries.append(
                entry(year, 800.0, CostCategory.ENERGY_WORKING, subject="electricity",
                      payer=Actor.TENANT)
            )
            entries.append(
                entry(year, -self.ANNUAL_LEVY, CostCategory.MODERNIZATION_LEVY,
                      subject="modernization levy", payer=Actor.LANDLORD)
            )
            entries.append(
                entry(year, self.ANNUAL_LEVY, CostCategory.MODERNIZATION_LEVY,
                      subject="modernization levy", payer=Actor.TENANT)
            )
        return entries

    def owner_result(self):
        """An owner-occupier perspective with both cash flows and both accounting credits."""
        entries = [
            entry(0, 30000.0, CostCategory.INVESTMENT, payer=Actor.OWNER_OCCUPIER),
            entry(0, -6000.0, CostCategory.SUBSIDY, payer=Actor.OWNER_OCCUPIER),
            entry(20, -7000.0, CostCategory.RESIDUAL_VALUE, payer=Actor.OWNER_OCCUPIER),
            entry(2, -2500.0, CostCategory.ANYWAY_COST_CREDIT, payer=Actor.OWNER_OCCUPIER),
        ]
        entries.extend(
            entry(year, 900.0, CostCategory.ENERGY_WORKING, subject="electricity",
                  payer=Actor.OWNER_OCCUPIER)
            for year in range(1, 21)
        )
        return make_result(entries, scope=ActorScope.OWNER_OCCUPIER)

    def society_result(self):
        """A macroeconomic perspective: resources plus CO2 damage, with a levy pair on the side.

        The levy pair is kept on the timeline deliberately, even though the shipped macroeconomic
        perspective strips transfers at source: it is the case the society statement's transfer
        side exists for, and a fixture without it could not show the two halves cancelling.
        """
        entries = [
            entry(0, 30000.0, CostCategory.INVESTMENT),
            entry(20, -7000.0, CostCategory.RESIDUAL_VALUE),
        ]
        for year in range(1, 21):
            entries.append(entry(year, 700.0, CostCategory.ENERGY_WORKING, subject="electricity"))
            entries.append(entry(year, 120.0, CostCategory.CO2_DAMAGE, subject="co2 damage"))
        return make_result(entries, scope=ActorScope.SYSTEM)

    def test_the_owner_statement_partitions_the_owner_npv(self):
        """Cash + accounting credits == the owner's NPV, to the cent."""
        statement = views.perspective_statement(
            self.owner_result(), views.StatementPartitions.OWNER
        )
        assert statement.cash_subtotal_in_euro + statement.accounting_subtotal_in_euro == (
            pytest.approx(statement.net_position_in_euro)
        )
        assert statement.net_position_in_euro == pytest.approx(
            statement.net_position_band.average, abs=0.005
        )
        assert {line.category for line in statement.accounting_lines} == {
            CostCategory.RESIDUAL_VALUE,
            CostCategory.ANYWAY_COST_CREDIT,
        }

    def test_the_tenant_statement_partitions_the_tenant_npv_with_an_empty_credit_side(self):
        """The tenant's sides sum to the NPV, and the credit side is empty by construction."""
        result = make_result(self.rented_entries(), scope=ActorScope.TENANT)
        statement = views.perspective_statement(result, views.StatementPartitions.TENANT)
        assert statement.accounting_lines == ()
        assert statement.accounting_subtotal_in_euro == 0.0
        assert statement.cash_subtotal_in_euro == pytest.approx(statement.net_position_in_euro)
        assert statement.net_position_in_euro == pytest.approx(
            statement.net_position_band.average, abs=0.005
        )

    def test_the_tenant_levy_mirrors_the_landlord_levy_income(self):
        """The two halves of one transfer pair: same magnitude, opposite sign, both stated."""
        entries = self.rented_entries()
        tenant = views.perspective_statement(
            make_result(entries, scope=ActorScope.TENANT), views.StatementPartitions.TENANT
        )
        landlord = views.landlord_statement(make_result(entries, scope=ActorScope.LANDLORD))
        tenant_levy = next(
            line for line in tenant.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        landlord_levy = next(
            line for line in landlord.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        assert tenant_levy.npv_in_euro == pytest.approx(-landlord_levy.npv_in_euro, abs=0.005)
        assert tenant_levy.npv_in_euro > 0 > landlord_levy.npv_in_euro

    def test_the_society_statement_partitions_the_macroeconomic_npv(self):
        """Real resources + transfers == the macroeconomic NPV, with CO2 on the resource side."""
        statement = views.perspective_statement(
            self.society_result(), views.StatementPartitions.SOCIETY
        )
        assert statement.cash_subtotal_in_euro + statement.accounting_subtotal_in_euro == (
            pytest.approx(statement.net_position_in_euro)
        )
        assert statement.net_position_in_euro == pytest.approx(
            statement.net_position_band.average, abs=0.005
        )
        assert CostCategory.CO2_DAMAGE in {line.category for line in statement.cash_lines}
        assert statement.accounting_lines == ()

    def test_society_transfers_are_shown_paired_and_sum_to_zero(self):
        """Both halves of a levy pair reach the transfer side, and the side nets to zero."""
        result = make_result(self.rented_entries(), scope=ActorScope.SYSTEM)
        statement = views.perspective_statement(result, views.StatementPartitions.SOCIETY)
        levy_lines = [
            line for line in statement.accounting_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        ]
        assert {line.payer for line in levy_lines} == {Actor.TENANT, Actor.LANDLORD}
        assert sum(line.npv_in_euro for line in levy_lines) == pytest.approx(0.0, abs=0.005)
        assert statement.cash_subtotal_in_euro + statement.accounting_subtotal_in_euro == (
            pytest.approx(statement.net_position_in_euro)
        )

    def test_a_broken_partition_raises_rather_than_drawing(self):
        """A statement that does not reconcile is a defect, not a picture to render."""
        result = self.owner_result()
        result.total_npv_in_euro = result.total_npv_in_euro + UncertainValue.exact(1000.0)
        with pytest.raises(CostDataError):
            views.perspective_statement(result, views.StatementPartitions.OWNER)


class TestAssumptionsTable:
    """Q26 F2: the report publishes every assumption it was priced under, with a source.

    Rule 2.9 says the report must be sufficient to understand every number it publishes, and the
    causes behind every consequence are the parameters, the escalation rates, the tariffs and the
    building quantities. What is checked here is completeness against what the run actually had —
    a rate the evaluator resolved but the table drops is exactly the silent gap the round is
    about — and that every row carries a source rather than a blank.
    """

    def result_with_assumptions(self):
        """A result carrying a resolved assumption record, as the evaluator fills one."""
        from hisim.economics.results import (
            EconomicAssumptions,
            RateOrigin,
            ReferenceAreas,
            ResolvedRate,
            TariffAssumption,
        )

        result = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT)],
            quantities=None,
        )
        result.reference_areas = ReferenceAreas(heated_floor_area_in_m2=160.0, living_area_in_m2=150.0)
        result.assumptions = EconomicAssumptions(
            escalation_rates={
                "general": ResolvedRate(rate=0.02, origin=RateOrigin.CONFIGURATION),
                "investment": ResolvedRate(rate=0.02, origin=RateOrigin.CONFIGURATION),
                "feed-in": ResolvedRate(rate=0.0, origin=RateOrigin.CONFIGURATION),
                "energy:electricity": ResolvedRate(
                    rate=0.025, origin=RateOrigin.COUNTRY_DEFAULTS, source_ids=["src_defaults"]
                ),
                "investment:HEAT_PUMP": ResolvedRate(rate=0.01, origin=RateOrigin.GENERAL_FALLBACK),
            },
            tariffs={
                "electricity": TariffAssumption(
                    carrier="electricity",
                    contract_id="DE_FLAT_2024",
                    working_price_in_euro_per_kwh=UncertainValue.exact(0.32),
                    standing_charge_in_euro_per_year=UncertainValue.exact(160.0),
                    feed_in_kind="FIXED_TARIFF",
                    feed_in_rate_in_euro_per_kwh=UncertainValue.exact(0.075),
                    source_ids=["src_prices"],
                )
            },
            annual_heat_demand_in_kwh=15000.0,
        )
        return result

    def test_every_resolved_rate_and_tariff_term_reaches_the_table(self):
        """Completeness: one row per rate, three per carrier, and the quantities."""
        rows = views.economic_assumptions(self.result_with_assumptions())
        names = [row.name for row in rows]
        for label in ("general prices", "investment prices", "feed-in prices",
                      "energy: electricity", "investment: HEAT_PUMP"):
            assert label in names, label
        assert "electricity: working price (DE_FLAT_2024)" in names
        assert "electricity: standing charge" in names
        assert "electricity: feed-in rate (FIXED_TARIFF)" in names
        assert "living area" in names and "heated floor area" in names
        assert "annual heat demand" in names
        assert "interest rate (discount rate)" in names and "annuity factor" in names

    def test_the_feed_in_rate_and_the_escalation_rates_carry_their_values(self):
        """The values are the run's own, not defaults invented by the view."""
        rows = {row.name: row.value for row in views.economic_assumptions(self.result_with_assumptions())}
        assert rows["electricity: feed-in rate (FIXED_TARIFF)"] == "0.0750 EUR/kWh"
        assert rows["energy: electricity"] == "2.50% per year"
        assert rows["investment: HEAT_PUMP"] == "1.00% per year"
        assert rows["annual heat demand"] == "15,000 kWh/a"

    def test_every_row_states_a_source_and_the_computed_one_says_so(self):
        """A blank source is the defect; `configuration` is an answer, not a placeholder."""
        rows = views.economic_assumptions(self.result_with_assumptions())
        assert all(row.source for row in rows)
        computed = [row for row in rows if row.is_computed]
        assert [row.name for row in computed] == ["annuity factor"]
        by_name = {row.name: row for row in rows}
        assert by_name["energy: electricity"].source == "src_defaults"
        assert by_name["investment: HEAT_PUMP"].source == "configuration (general fallback)"
        assert by_name["interest rate (discount rate)"].source == "configuration"

    def test_the_damage_cost_path_appears_only_where_one_was_priced(self):
        """The macroeconomic row is a property of the run, so the caller decides it."""
        result = self.result_with_assumptions()
        names = {row.name for row in views.economic_assumptions(result)}
        assert "CO2 damage cost (flat over the horizon)" not in names
        with_damage = {
            row.name for row in views.economic_assumptions(result, co2_damage_priced=True)
        }
        assert "CO2 damage cost (flat over the horizon)" in with_damage

    def test_a_result_without_the_record_still_states_its_parameters(self):
        """An archived result contributes the frame it does have rather than nothing at all."""
        rows = views.economic_assumptions(make_result([entry(0, 100.0, CostCategory.INVESTMENT)]))
        assert {row.name for row in rows} >= {"interest rate (discount rate)", "annuity factor"}


class TestCo2FactorsAndLevelizedHeatCost:
    """Q26 F3/F6: a mass and a per-kWh figure are each one visible multiplication (rule 2.9)."""

    def co2_result(self):
        """A result whose CO2 accounting carries the factors the engine recorded."""
        from hisim.economics.results import AnnualEnergyQuantities, EmbodiedCo2Basis

        result = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT)],
            quantities={"electricity": AnnualEnergyQuantities(bought_in_kwh=2000.0)},
        )
        result.lifecycle_co2_result = LifecycleCo2Result(
            embodied_co2_in_kg=1300.0,
            operational_co2_by_carrier_in_kg={"electricity": 12000.0},
            embodied_by_subject_in_kg={"HeatPump": 1300.0},
            total_co2_in_kg=13300.0,
            emission_factor_by_carrier_in_kg_per_kwh={"electricity": 0.3},
            embodied_basis_by_subject={
                "HeatPump": EmbodiedCo2Basis(
                    factor_in_kg_per_unit=130.0, size=5.0, size_unit="kW",
                    per_installation_in_kg=650.0, installations=2,
                )
            },
        )
        return result

    def test_the_operational_row_multiplies_out_to_the_charted_mass(self):
        """factor x annual kWh x horizon == the carrier total the chart draws."""
        rows = {row.subject: row for row in views.co2_factor_rows(self.co2_result())}
        electricity = rows["electricity"]
        assert electricity.annual_mass_in_kg == pytest.approx(0.3 * 2000.0)
        assert electricity.annual_mass_in_kg * electricity.installations == pytest.approx(
            electricity.total_in_kg
        )

    def test_the_embodied_row_multiplies_out_to_the_charted_mass(self):
        """factor x size == per installation, times the installations booked == the total."""
        rows = {row.subject: row for row in views.co2_factor_rows(self.co2_result())}
        heat_pump = rows["HeatPump"]
        assert heat_pump.factor_in_kg_per_unit * heat_pump.quantity == pytest.approx(
            heat_pump.per_installation_in_kg
        )
        assert heat_pump.per_installation_in_kg * heat_pump.installations == pytest.approx(
            heat_pump.total_in_kg
        )

    def test_a_result_without_recorded_factors_yields_no_rows(self):
        """No division-derived factors: an archived result simply shows the masses it has."""
        assert views.co2_factor_rows(make_result([entry(0, 100.0, CostCategory.INVESTMENT)])) == []

    def test_the_levelized_heat_cost_states_its_own_division(self):
        """The caption's numerator, denominator and quotient reproduce the published KPI."""
        from hisim.economics.results import EconomicAssumptions

        result = make_result([entry(year, 1000.0, CostCategory.ENERGY_WORKING) for year in range(1, 21)])
        result.levelized_cost_of_heat_in_euro_per_kwh = result.total_npv_in_euro.scale(
            result.parameters.annuity_factor() / 15000.0
        )
        result.assumptions = EconomicAssumptions(annual_heat_demand_in_kwh=15000.0)
        derivation = views.levelized_heat_cost_derivation(result)
        assert derivation is not None
        assert derivation.numerator_npv_in_euro == pytest.approx(result.total_npv_in_euro.average)
        assert derivation.equivalent_annual_cost_in_euro / derivation.annual_heat_demand_in_kwh == (
            pytest.approx(derivation.levelized_cost_in_euro_per_kwh)
        )
        # The attribution set is stated truthfully: everything the perspective books.
        assert derivation.attributed_subjects == ("HeatPump",)

    def test_a_levelized_figure_that_does_not_reconcile_raises(self):
        """A caption describing a different figure from the KPI table is a defect, not a caption."""
        from hisim.economics.results import EconomicAssumptions

        result = make_result([entry(1, 1000.0, CostCategory.ENERGY_WORKING)])
        result.levelized_cost_of_heat_in_euro_per_kwh = UncertainValue.exact(9.99)
        result.assumptions = EconomicAssumptions(annual_heat_demand_in_kwh=15000.0)
        with pytest.raises(CostDataError):
            views.levelized_heat_cost_derivation(result)


class TestScenarioAssumptionLabels:
    """Q26 F1: a scenario row states the assumption it changed and both of its values."""

    class _Scenario:
        """The two override dicts a `ScenarioCube` carries per cell, duck-typed."""

        def __init__(self, scenario_id, parameter_overrides=None, data_overlays=None):
            self.id = scenario_id
            self.parameter_overrides = parameter_overrides or {}
            self.data_overlays = data_overlays or {}

    class _Cube:
        """The one attribute the view reads off a cube."""

        def __init__(self, scenarios):
            self.scenarios = scenarios

    def base_result(self):
        """A central case with an explicit interest rate and a resolved electricity rate."""
        from hisim.economics.results import EconomicAssumptions, RateOrigin, ResolvedRate

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
        """"interest_rate 5.00% (central 3.00%)" — the swing is never a label without a cause."""
        cube = self._Cube([
            self._Scenario("central"),
            self._Scenario("interest=high", {"interest_rate": 0.05}),
        ])
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels == {"interest=high": "interest_rate 5.00% (central 3.00%)"}

    def test_a_dict_axis_falls_back_to_the_rate_the_run_resolved(self):
        """A carrier with no configured rate still has a central value — the one it was priced at."""
        cube = self._Cube([
            self._Scenario("electricity=flat", {"energy_price_escalation_rates.ELECTRICITY": 0.0}),
        ])
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels["electricity=flat"] == (
            "energy_price_escalation_rates.ELECTRICITY 0.00% (central 2.00%)"
        )

    def test_a_data_overlay_states_the_band_it_replaced_the_shipped_data_with(self):
        """An overlay has no central parameter, so the central case is named "as shipped"."""
        cube = self._Cube([
            self._Scenario(
                "hp_price=cheap",
                data_overlays={"devices_DE.HEAT_PUMP.specific_investment":
                               {"min": 800, "avg": 1050, "max": 1450}},
            ),
        ])
        labels = views.scenario_assumption_labels(cube, self.base_result())
        assert labels["hp_price=cheap"] == (
            "devices_DE.HEAT_PUMP.specific_investment min 800/avg 1,050/max 1,450 "
            "(central as shipped)"
        )

    def test_the_base_cell_carries_no_assumption(self):
        """The central case changed nothing, and the table says so rather than inventing a row."""
        cube = self._Cube([self._Scenario("central")])
        assert views.scenario_assumption_labels(cube, self.base_result()) == {}
        assert views.scenario_assumption_labels(None, self.base_result()) == {}


class TestAwardArithmetic:
    """Q26 F8: an applied award shows the multiplication and the ceiling verdict behind it."""

    def award(self, **overrides):
        """A percentage award with the solver's recorded factors on it."""
        from hisim.economics.subsidies import PayoutKind, SubsidyAward

        fields = dict(
            scheme_id="DE_BEG_EM_HP",
            payout_kind=PayoutKind.UPFRONT_GRANT,
            upfront_amount=UncertainValue.exact(3000.0),
            benefit_rate=0.30,
            eligible_basis_in_euro=UncertainValue.exact(10000.0),
            eligible_basis_cap_in_euro=30000.0,
            caps_binding_per_slot={"low": False, "average": False, "high": False},
        )
        fields.update(overrides)
        return SubsidyAward(**fields)

    def test_a_percentage_award_shows_rate_times_basis_equals_amount(self):
        """The three numbers a reader needs to check the euros, in one phrase."""
        presentation = views.describe_award(self.award())
        assert presentation.arithmetic == "30.0% x 10,000 EUR eligible basis = 3,000 EUR"
        assert presentation.cap_verdict == "cap not binding (30,000 EUR eligible cost)"

    def test_a_group_capped_rate_shows_both_rates(self):
        """A cumulation group's combined-rate cap is stated, not folded into a rate nobody chose."""
        presentation = views.describe_award(
            self.award(upfront_amount=UncertainValue.exact(2625.0), benefit_rate=0.2625,
                       benefit_rate_before_group_cap=0.30)
        )
        assert presentation.arithmetic == (
            "26.2% (of 30.0%, cut back by the cumulation group's combined-rate cap) x "
            "10,000 EUR eligible basis = 2,625 EUR"
        )

    def test_a_binding_cap_names_the_ceiling_and_the_slots(self):
        """At the ceiling the same measure costing more earns the same euros — so it is stated."""
        presentation = views.describe_award(
            self.award(caps_binding_per_slot={"low": False, "average": True, "high": True})
        )
        assert presentation.cap_verdict == "capped at 30,000 EUR eligible cost (average, high)"

    def test_a_form_without_a_rate_states_its_own_terms_instead(self):
        """Loan terms have no rate and no basis; the payout note already carries their terms."""
        from hisim.economics.subsidies import PayoutKind

        presentation = views.describe_award(
            self.award(payout_kind=PayoutKind.LOAN_TERMS, benefit_rate=None,
                       eligible_basis_in_euro=None, eligible_basis_cap_in_euro=None,
                       upfront_amount=UncertainValue.exact(0.0), loan_interest_rate=0.01)
        )
        assert presentation.arithmetic == ""
        assert presentation.cap_verdict == ""
        assert "1.00% interest" in presentation.payout_note
