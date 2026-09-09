"""Unit tests for the chart views V1-V7 of the visualization extension (visualization spec §5).

Every chart of the V1-V15 set gets its numbers from a view function, and every one of those views
either carries an invariant it validates at runtime or has a reconciliation a reviewer is expected
to check by hand. This module is the first half of that promise — the actor Sankey, the liquidity
fan, the uncertainty tornado, the comparison bridge, the cost of credit, the ledger heatmap's
numbers and the component event strip — with one test per row of the visualization spec's §5
invariant table, on **hand-built timelines** rather than on evaluated runs, so that the expected
figures are arithmetic a reader can redo on paper. The V8-V15 views are tested in
`tests/test_economics_views_charts_b.py`, and the rendering of all of them in the renderer tests.

**Why hand-built.** `tests/test_economics_views.py` pins the older views against an evaluated
result, which is the right shape for views that re-arrange a real evaluation. The chart views are
different: most of them *validate* something (a transfer that must net to zero, bars that must sum
to a band, a statement whose two sides must be a partition), and a validation is only tested by
feeding it data that violates it — which an evaluator will not produce on request. Building the
timeline directly, in the style of `tests/test_economics_kernel.py`, is what makes both the
passing and the failing case expressible.

**What a failure means.** A failure here is a *derivation* failure in the chart layer: the engine's
numbers are unaffected, but a chart would draw something that does not reconcile with them — which
is exactly the class of defect the self-validating views exist to make impossible. It is distinct
from a rendering failure (`tests/test_economics_reporting.py`, the goldens) and from an engine
failure (`tests/test_economics_engine.py`).

The one deliberate exception to "hand-built" is `TestWorkedExampleActorFlows`, which re-states the
§559e worked example's expected actor-flow matrix as a direct assertion (owner decision Q7); see
its docstring for why the figures live here rather than in the workbook.
"""

# clean

import dataclasses

import pytest

from hisim.economics import views
from hisim.economics.calculators.aggregation import aggregate_timeline
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope
from hisim.economics.presentation_style import PresentationStyle
from hisim.economics.results import (
    CashFlowTimeline,
    LifecycleCo2Result,
    LifecycleCostResult,
    discounted_payback_year,
)
from hisim.economics.timeline import Actor, CashFlowEntry, CostCategory, SubjectKind
from hisim.economics.uncertainty import Slot, UncertainValue

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
    )


def simple_investment_timeline(horizon: int = 20):
    """A one-component fixture: buy in year 0, run it, get a residual credit at the horizon.

    The smallest timeline that still exercises the investment, energy and residual machinery the
    strip and the fan read, and the base most tests below extend.
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
                item.amount_in_euro.best_estimate
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

    def test_actor_columns_put_the_payer_before_the_payee(self):
        """The transfer graph is sorted topologically, so the levy reads left to right (Q23)."""
        matrix = views.actor_flow_matrix(self.make_levy_result())
        columns = matrix.actor_columns()
        assert all(len(column) == 1 for column in columns)
        flat = [actor for column in columns for actor in column]
        assert flat.index(Actor.TENANT.value) < flat.index(Actor.LANDLORD.value)


class TestStoryPerspectives:
    """Q24: the three chapters are decided by what a result books, never by its id."""

    def test_a_co2_damage_flow_makes_a_perspective_the_society_story(self):
        """CO2 at damage cost is the structural signature of §4.5 accounting."""
        macro = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT),
             entry(1, 200.0, CostCategory.CO2_DAMAGE, subject="co2 damage")],
            horizon=5,
        )
        assert views.has_macroeconomic_accounting(macro)
        stories = views.story_perspectives([macro])
        assert stories.society == (macro,) and not stories.owner and not stories.rented

    def test_landlord_and_tenant_scopes_are_the_rented_story(self):
        """A perspective scoped to either party of a tenancy belongs to that chapter."""
        entries = [entry(0, 10000.0, CostCategory.INVESTMENT, payer=Actor.LANDLORD)]
        landlord = make_result(entries, horizon=5, scope=ActorScope.LANDLORD)
        stories = views.story_perspectives([landlord])
        assert stories.rented == (landlord,)

    def test_a_run_without_support_still_has_an_owner_story(self):
        """A cash purchase without subsidies is an owner's story, not an empty chapter."""
        gross = make_result([entry(0, 10000.0, CostCategory.INVESTMENT)], horizon=5)
        stories = views.story_perspectives([gross])
        assert stories.owner == (gross,)

    def test_a_net_perspective_is_preferred_over_the_gross_one(self):
        """With support on the page the owner chapter shows the after-subsidy view only."""
        gross = make_result([entry(0, 10000.0, CostCategory.INVESTMENT)], horizon=5)
        net = make_result(
            [entry(0, 10000.0, CostCategory.INVESTMENT),
             entry(0, -2000.0, CostCategory.SUBSIDY, scheme_id="S")],
            horizon=5,
        )
        stories = views.story_perspectives([gross, net])
        assert stories.owner == (net,)


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
            "best_estimate": [-100.0, -80.0, -20.0, 5.0],
            "high": [-100.0, -90.0, -70.0, -60.0],
        }
        assert views.band_zero_crossings(curves) == {"low": 2, "best_estimate": 3, "high": None}
        # The crossing rule itself (year 0 excluded, first crossing only) is pinned once, on the
        # function both the fan and the printed payback year read.
        assert discounted_payback_year(curves["low"]) == 2

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
                amount_in_euro=UncertainValue(best_estimate=-500.0, minimum=-700.0, maximum=-300.0),
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
            total.minimum - total.best_estimate, abs=0.005
        )
        assert sum(row.high_delta_in_euro for row in rows) == pytest.approx(
            total.maximum - total.best_estimate, abs=0.005
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
                    amount_in_euro=UncertainValue(best_estimate=-500.0, minimum=-700.0, maximum=-300.0),
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
        expected = variant.total_npv_in_euro.best_estimate - reference.total_npv_in_euro.best_estimate
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
            entry(0, -disbursement.best_estimate, CostCategory.LOAN_DISBURSEMENT, subject="financing"),
        ]
        for year, interest, repayment in schedule:
            entries.append(
                entry(year, interest.best_estimate, CostCategory.LOAN_INTEREST, subject="financing")
            )
            entries.append(
                entry(year, repayment.best_estimate, CostCategory.LOAN_PRINCIPAL, subject="financing")
            )
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
            item.amount_in_euro.best_estimate
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
        assert amortization.loan_free_year() is None


class TestTimelineHeatmapNumbers:
    """V6: the matrix the ledger heatmap draws reconciles by rows and by columns."""

    def test_column_sums_equal_the_nominal_annual_series(self):
        """Each year's column adds up to that year's published nominal figure."""
        result = make_result(simple_investment_timeline(horizon=6), horizon=6)
        matrix = views.nominal_annual_matrix_by_category(result)
        for year, row in enumerate(matrix):
            assert sum(row.values()) == pytest.approx(
                result.annual_cost_series_nominal_in_euro[year].best_estimate
            )

    def test_row_sums_equal_the_per_category_nominal_totals(self):
        """Each category's row adds up to what that category booked over the horizon."""
        result = make_result(simple_investment_timeline(horizon=6), horizon=6)
        matrix = views.nominal_annual_matrix_by_category(result)
        for category in {item.category for item in result.timeline.entries}:
            expected = sum(
                item.amount_in_euro.best_estimate
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
            statement.net_position_band.best_estimate, abs=0.005
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
            statement.net_position_band.best_estimate, abs=0.005
        )
        assert {line.category for line in statement.accounting_lines} == {
            CostCategory.RESIDUAL_VALUE,
            CostCategory.ANYWAY_COST_CREDIT,
        }

    def test_the_tenant_statement_partitions_the_tenant_npv_with_an_empty_credit_side(self):
        """The tenant's sides sum to the NPV, and the credit side is empty by construction."""
        result = make_result(self.rented_entries(), scope=ActorScope.TENANT)
        statement = views.perspective_statement(result, views.StatementPartitions.TENANT)
        assert not statement.accounting_lines
        assert statement.accounting_subtotal_in_euro == 0.0
        assert statement.cash_subtotal_in_euro == pytest.approx(statement.net_position_in_euro)
        assert statement.net_position_in_euro == pytest.approx(
            statement.net_position_band.best_estimate, abs=0.005
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
            statement.net_position_band.best_estimate, abs=0.005
        )
        assert CostCategory.CO2_DAMAGE in {line.category for line in statement.cash_lines}
        assert not statement.accounting_lines

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
