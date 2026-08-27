"""Tests for the result view-model (cost-spec-v2 §2.4, W4.1).

Every view is pinned against an *independent* recomputation — brute-force loops written out
here, never a second call into the view — on one nontrivial evaluated result: banded cost data,
two priced subjects, a loan, catalog subsidies, feed-in revenue and an actor allocation.

**Surface.** `hisim/economics/views.py`: the ~15 derivations that used to happen on the fly inside
chart and table code and now live as pure functions of a `LifecycleCostResult`. The view-model is
what makes "presentation never computes" enforceable (the import lint checks the other half), so
these functions are the *only* place a displayed number may be derived — and therefore the place
that has to be verified hardest.

**How it covers it.** Each test recomputes the view's answer from the timeline with an explicit
loop over entries — the same information, arrived at by the dumbest possible route — and compares.
That is deliberate: a view and its test written in the same style would agree even when both are
wrong. Where a closed form exists it is spelled out rather than imported (the annuity factor in
`test_equivalent_annual_costs_are_npv_times_annuity`, the discount factor `(1+i)**year`). Beyond
per-view equality the file pins the properties a reviewer actually relies on: the cumulative
curve ends exactly at the headline NPV, the annual matrix's row sums are the liquidity series,
folding categories into display groups loses nothing and refuses gaps, the detail table accounts
for the whole timeline, and the payer pivot's row sums are `npv_by_payer`.

**Error class.** A failure here is a *derivation* bug in the boundary layer between engine and
presentation — the number the engine computed is fine, the number a chart would draw is not. It
is distinct from a formula bug (the evaluator's own arithmetic, `test_economics_engine.py`) and
from a rendering bug (`test_economics_reporting.py` / the goldens). The most consequential cases
are called out by name: the `min(subsidy, gross)` clamp that used to be duplicated in two chart
helpers (§7 B8), the D2 switch of `total_subsidies_received` to a timeline-based figure, and the
S4b decision to scope `year_zero_build_up` like the table beside it.
"""

# clean

from typing import Dict

import pytest

from hisim.economics import views
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyMode,
)
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
)
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


def make_inputs() -> EvaluationInputs:
    """Two priced subjects, a banded heat pump, bought *and* sold electricity.

    Every element here exists to keep one view from being tested on a degenerate case: the band on
    the heat pump makes the min/max slots differ, the second subject makes the per-subject views
    non-trivial, the sold electricity produces a FEED_IN_REVENUE entry (the one flow shown in a
    bill but excluded from its effective price), and the applicant/building context is what the DE
    catalog needs before it will award anything. The physical fields at the bottom — heat demand,
    areas, cold rent, specific emissions — feed the per-area, LCOH and actor-split views.
    """
    heat_pump = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(16000.0, 12800.0, 20800.0),
        lifetime_override_in_years=18.0,
        override_source="test",
        technical_attributes={"scop": 4.2, "refrigerant": "R290"},
    )
    windows = ComponentCostFacts(
        asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
        size=28.0,
        size_unit=Units.SQUARE_METER,
    )
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[
            SubjectCostFacts("HeatPump", heat_pump),
            SubjectCostFacts("Envelope.Windows", windows),
        ],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=5000.0,
                energy_sold_in_kwh=2500.0,
            )
        ],
        subsidy_context=SubsidyContext(
            applicant=ApplicantProfile(
                actor=ApplicantActor.OWNER_OCCUPIER,
                taxable_household_income_in_euro=35000.0,
                main_residence=True,
            ),
            building=SubsidyBuildingContext(
                construction_year=1985,
                dwelling_units=1,
                residential_floor_area_in_m2=150.0,
                commercial_floor_area_in_m2=0.0,
            ),
        ),
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=160.0,
        current_cold_rent_in_euro_per_m2_month=8.5,
        building_specific_emissions_in_kg_per_m2_a=25.0,
    )


#: The workhorse perspective: subsidised and 60 % financed, so the timeline carries SUBSIDY,
#: LOAN_DISBURSEMENT, LOAN_INTEREST and LOAN_PRINCIPAL entries and the loan-amortization and
#: subsidy views have something to describe. Actor scope stays SYSTEM — the split is exercised by
#: LANDLORD below, because a scoped result answers several views differently.
FINANCED = Perspective(
    id="financed_net",
    installation_context=InstallationContext.GREENFIELD,
    subsidy_mode=SubsidyMode.full(),
    financing=FinancingPlan(financed_share=0.6, nominal_interest_rate=0.035, term_in_years=12),
)

#: The allocated counterpart: a landlord scope runs the DE_2024 ruleset, so entries carry real
#: payer tags and the payer pivot, the zero-sum reference and the scoped year-0 build-up become
#: testable. Unfinanced, to keep the allocation the only difference from FINANCED.
LANDLORD = Perspective(
    id="landlord",
    installation_context=InstallationContext.GREENFIELD,
    actor_scope=ActorScope.LANDLORD,
    subsidy_mode=SubsidyMode.full(),
)


@pytest.fixture(name="result", scope="module")
def fixture_result():
    """A financed, subsidised, banded greenfield result with feed-in revenue.

    The single object almost every test in this file reads. It is built from the shipped database
    and the shipped DE catalog on purpose — the views must survive real data with its awkward
    edges (schedules, caps, degenerate rows) — and `TestFixtureIsNontrivial` asserts up front that
    it really does contain all the mechanisms, since a view tested on an empty timeline passes
    vacuously. Module-scoped: one evaluation for the whole file.
    """
    evaluator = EconomicEvaluator(
        CostDatabase(), EconomicParameters(country="DE", price_basis_year=2026), SubsidyCatalog.load("DE")
    )
    return evaluator.evaluate(make_inputs(), FINANCED)


@pytest.fixture(name="allocated", scope="module")
def fixture_allocated():
    """The same inputs under a landlord scope, so the timeline carries payer tags (§6).

    Separate from `result` because scoping changes what several views legitimately answer: the
    payer pivot only exists here, and `year_zero_build_up` must be read on the *scoped* timeline
    to stay consistent with the investment table beside it. Same inputs as `result`, so any
    difference between the two fixtures is attributable to the allocation alone.
    """
    evaluator = EconomicEvaluator(
        CostDatabase(), EconomicParameters(country="DE", price_basis_year=2026), SubsidyCatalog.load("DE")
    )
    return evaluator.evaluate(make_inputs(), LANDLORD)


class TestFixtureIsNontrivial:
    """The evidence value of every test below rests on this."""

    def test_fixture_exercises_every_mechanism(self, result, allocated):
        """Bands, two subjects, a loan, subsidies, feed-in and an allocation are all present."""
        assert not result.total_npv_in_euro.is_exact()
        assert len(result.component_breakdowns) >= 3  # 2 devices + the carrier(s)
        categories = {entry.category for entry in result.timeline.entries}
        assert CostCategory.LOAN_INTEREST in categories
        assert CostCategory.LOAN_PRINCIPAL in categories
        assert CostCategory.SUBSIDY in categories
        assert CostCategory.FEED_IN_REVENUE in categories
        assert result.subsidy_decisions
        assert {entry.payer for entry in allocated.timeline.entries} != {Actor.SYSTEM}


class TestTimeSeriesViews:
    """Cumulative NPV, the annual category matrix, loan amortization, cumulative CO2."""

    def test_cumulative_discounted_cost_matches_manual_discounting(self, result):
        """Independent cumulative loop per slot."""
        horizon = result.parameters.observation_period_in_years
        interest = result.parameters.interest_rate
        series = views.cumulative_discounted_cost_series(result)
        for slot, attribute in ((Slot.LOW, "minimum"), (Slot.BEST_ESTIMATE, "best_estimate"), (Slot.HIGH, "maximum")):
            expected, running = [], 0.0
            for year in range(horizon + 1):
                for entry in result.scoped_timeline().entries:
                    if entry.year == year:
                        running += getattr(entry.amount_in_euro, attribute) / ((1 + interest) ** year)
                expected.append(running)
            assert series[slot] == pytest.approx(expected)

    def test_cumulative_series_ends_at_the_reported_npv(self, result):
        """The chart's last point is the headline NPV, in every slot."""
        series = views.cumulative_discounted_cost_series(result)
        npv = result.total_npv_in_euro
        assert series[Slot.LOW][-1] == pytest.approx(npv.minimum)
        assert series[Slot.BEST_ESTIMATE][-1] == pytest.approx(npv.best_estimate)
        assert series[Slot.HIGH][-1] == pytest.approx(npv.maximum)

    def test_annual_category_matrix_matches_manual_accumulation(self, result):
        """Year x category, brute-forced from the scoped timeline."""
        horizon = result.parameters.observation_period_in_years
        matrix = views.nominal_annual_matrix_by_category(result)
        assert len(matrix) == horizon + 1
        for year in range(horizon + 1):
            expected: Dict[CostCategory, float] = {}
            for entry in result.scoped_timeline().entries:
                if entry.year == year:
                    expected[entry.category] = (
                        expected.get(entry.category, 0.0) + entry.amount_in_euro.best_estimate
                    )
            assert matrix[year] == pytest.approx(expected)

    def test_annual_matrix_row_sums_are_the_liquidity_view(self, result):
        """Row sums reproduce `annual_cost_series_nominal_in_euro` (same flows, no grouping)."""
        matrix = views.nominal_annual_matrix_by_category(result)
        for year, row in enumerate(matrix):
            assert sum(row.values()) == pytest.approx(
                result.annual_cost_series_nominal_in_euro[year].best_estimate, abs=1e-9
            )

    def test_fold_categories_sums_groups_and_rejects_gaps(self, result):
        """Folding is a pure regroup: nothing gained, nothing lost, no silent default bucket."""
        mapping = {category: ("energy" if "ENERGY" in category.value else "other") for category in CostCategory}
        folded = views.fold_categories(result.npv_by_category, mapping)
        assert sum(band.best_estimate for band in folded.values()) == pytest.approx(
            sum(band.best_estimate for band in result.npv_by_category.values())
        )
        expected_energy = sum(
            band.best_estimate
            for category, band in result.npv_by_category.items()
            if "ENERGY" in category.value
        )
        assert folded["energy"].best_estimate == pytest.approx(expected_energy)
        with pytest.raises(KeyError):
            views.fold_categories(result.npv_by_category, {})

    def test_fold_category_matrix_folds_every_year(self, result):
        """The year x group matrix is the year x category matrix, regrouped."""
        mapping = {category: category.value[:3] for category in CostCategory}
        matrix = views.nominal_annual_matrix_by_category(result)
        folded = views.fold_category_matrix(matrix, mapping)
        for year, row in enumerate(matrix):
            assert sum(folded[year].values()) == pytest.approx(sum(row.values()))

    def test_loan_series_matches_manual_split(self, result):
        """Interest and principal per year, brute-forced."""
        horizon = result.parameters.observation_period_in_years
        amortization = views.loan_amortization_series(result)
        assert amortization.has_flows()
        for year in range(horizon + 1):
            for category, series in (
                (CostCategory.LOAN_INTEREST, amortization.interest_in_euro),
                (CostCategory.LOAN_PRINCIPAL, amortization.principal_in_euro),
            ):
                expected = sum(
                    entry.amount_in_euro.best_estimate
                    for entry in result.scoped_timeline().entries
                    if entry.year == year and entry.category == category
                )
                assert series[year] == pytest.approx(expected)

    def test_cumulative_operational_co2_is_the_running_total(self, result):
        """Same numbers as the yearly series, accumulated."""
        yearly = result.lifecycle_co2_result.operational_co2_by_year_in_kg
        cumulative = views.cumulative_operational_co2_in_kg(result)
        assert len(cumulative) == len(yearly)
        for index in range(len(yearly)):
            assert cumulative[index] == pytest.approx(sum(yearly[: index + 1]))


class TestDetailTable:
    """The (year, subject, category) verification table."""

    def test_rows_and_subtotals_match_manual_aggregation(self, result):
        """Every row is a hand-summed cell; every subtotal is the sum of its own rows."""
        interest = result.parameters.interest_rate
        years = views.timeline_detail_rows(result)
        for detail_year in years:
            for row in detail_year.rows:
                expected = UncertainValue.sum(
                    entry.amount_in_euro
                    for entry in result.scoped_timeline().entries
                    if entry.year == row.year
                    and entry.subject == row.subject
                    and entry.category == row.category
                )
                assert row.nominal_in_euro.best_estimate == pytest.approx(expected.best_estimate)
                assert row.nominal_in_euro.minimum == pytest.approx(expected.minimum)
                assert row.discounted_best_estimate_in_euro == pytest.approx(
                    expected.best_estimate / ((1 + interest) ** row.year)
                )
            assert detail_year.nominal_total_in_euro.best_estimate == pytest.approx(
                sum(row.nominal_in_euro.best_estimate for row in detail_year.rows)
            )
            assert detail_year.discounted_total_best_estimate_in_euro == pytest.approx(
                detail_year.nominal_total_in_euro.best_estimate / ((1 + interest) ** detail_year.year)
            )

    def test_table_covers_the_whole_timeline(self, result):
        """Nothing but float noise is dropped: the table's total is the nominal total."""
        years = views.timeline_detail_rows(result)
        table_total = sum(
            row.nominal_in_euro.best_estimate for detail_year in years for row in detail_year.rows
        )
        timeline_total = sum(
            entry.amount_in_euro.best_estimate for entry in result.scoped_timeline().entries
        )
        assert table_total == pytest.approx(timeline_total, abs=views.ViewThresholds.DETAIL_ROW_EPSILON * 50)

    def test_years_are_ordered_and_rows_sorted_by_amount(self, result):
        """Presentation relies on the order; it is part of the view's contract."""
        years = views.timeline_detail_rows(result)
        assert [detail_year.year for detail_year in years] == sorted(
            detail_year.year for detail_year in years
        )
        for detail_year in years:
            amounts = [row.nominal_in_euro.best_estimate for row in detail_year.rows]
            assert amounts == sorted(amounts)


class TestPivotsAndAnnuities:
    """Payer x category, and the annuitized figures."""

    def test_payer_pivot_matches_manual_discounting(self, allocated):
        """Each cell is the hand-discounted sum of that payer's entries in that category."""
        interest = allocated.parameters.interest_rate
        pivot = views.payer_category_npv_pivot(allocated)
        assert Actor.SYSTEM not in pivot
        for payer, by_category in pivot.items():
            for category, band in by_category.items():
                expected = UncertainValue.sum(
                    entry.amount_in_euro.scale(1.0 / ((1 + interest) ** entry.year))
                    for entry in allocated.timeline.entries
                    if entry.payer == payer and entry.category == category
                )
                assert band.best_estimate == pytest.approx(expected.best_estimate)
                assert band.maximum == pytest.approx(expected.maximum)

    def test_payer_pivot_reconciles_with_the_payer_npvs(self, allocated):
        """Row sums are `npv_by_payer` — the §6.5 zero-sum view, unchanged."""
        pivot = views.payer_category_npv_pivot(allocated)
        for payer, by_category in pivot.items():
            row_sum = sum(band.best_estimate for band in by_category.values())
            assert row_sum == pytest.approx(allocated.npv_by_payer[payer].best_estimate)

    def test_payer_npv_total_is_the_zero_sum_reference(self, allocated):
        """The system total the §6.5 panel reconciles against — all payers, SYSTEM included."""
        total = views.payer_npv_total(allocated)
        expected = UncertainValue.sum(allocated.npv_by_payer.values())
        assert total.best_estimate == pytest.approx(expected.best_estimate)
        assert total.minimum == pytest.approx(expected.minimum)

    def test_equivalent_annual_costs_are_npv_times_annuity(self, result):
        """Per-category and per-subject EAC, recomputed by hand."""
        annuity = (
            result.parameters.interest_rate
            * (1 + result.parameters.interest_rate) ** result.parameters.observation_period_in_years
            / ((1 + result.parameters.interest_rate) ** result.parameters.observation_period_in_years - 1)
        )
        by_category = views.equivalent_annual_cost_by_category(result)
        for category, band in by_category.items():
            assert band.best_estimate == pytest.approx(result.npv_by_category[category].best_estimate * annuity)
        by_subject = views.subject_equivalent_annual_cost_by_category(result)
        for subject, breakdown in result.component_breakdowns.items():
            for category, npv in breakdown.npv_by_category.items():
                assert by_subject[subject][category].minimum == pytest.approx(npv.minimum * annuity)


class TestEnergyBills:
    """Year-1 bills, quantities, effective prices."""

    def test_bill_decomposition_matches_manual_sums(self, result):
        """Categories, total and price, all recomputed from the timeline."""
        bills = views.carrier_year_one_bills(result)
        bill = bills["ELECTRICITY"]
        expected_by_category: Dict[CostCategory, float] = {}
        for entry in result.scoped_timeline().entries:
            if entry.year == 1 and entry.subject in ("ELECTRICITY", "ELECTRICITY_FEED_IN"):
                expected_by_category[entry.category] = (
                    expected_by_category.get(entry.category, 0.0) + entry.amount_in_euro.best_estimate
                )
        assert bill.by_category_in_euro == pytest.approx(expected_by_category)
        expected_total = sum(
            value
            for category, value in expected_by_category.items()
            if category != CostCategory.FEED_IN_REVENUE
        )
        assert bill.total_excluding_feed_in_in_euro == pytest.approx(expected_total)
        assert bill.annual_quantity_in_kwh == pytest.approx(5000.0)
        assert bill.effective_price_in_euro_per_kwh == pytest.approx(expected_total / 5000.0)

    def test_feed_in_is_shown_but_never_priced_into_the_unit_cost(self, result):
        """The credit appears in the decomposition, not in the numerator or the band."""
        bill = views.carrier_year_one_bills(result)["ELECTRICITY"]
        assert bill.by_category_in_euro[CostCategory.FEED_IN_REVENUE] < 0
        assert bill.effective_price_in_euro_per_kwh > 0
        band_expected = UncertainValue.sum(
            entry.amount_in_euro
            for entry in result.scoped_timeline().entries
            if entry.year == 1 and entry.subject == "ELECTRICITY"
        )
        assert bill.year_one_band_in_euro.best_estimate == pytest.approx(band_expected.best_estimate)
        assert bill.year_one_band_in_euro.best_estimate == pytest.approx(bill.total_excluding_feed_in_in_euro)


class TestYearZeroAndSubsidies:
    """Investment build-up, the subsidy clamp, the total support KPI."""

    def test_year_zero_build_up_matches_manual_sums(self, result):
        """Each step and the net outflow, brute-forced from the scoped timeline."""
        build_ups = views.year_zero_build_up(result)
        assert "HeatPump" in build_ups
        for subject, build_up in build_ups.items():
            expected = {}
            for category in views.ViewCategories.YEAR_ZERO_CATEGORIES:
                value = sum(
                    entry.amount_in_euro.best_estimate
                    for entry in result.scoped_timeline().entries
                    if entry.year == 0 and entry.subject == subject and entry.category == category
                )
                if value:
                    expected[category] = value
            assert build_up.by_category_in_euro == pytest.approx(expected)
            assert build_up.net_outflow_in_euro == pytest.approx(sum(expected.values()))

    def test_financed_subsidised_purchase_has_a_small_net_outflow(self, result):
        """Sanity on the fixture: 60 % financed plus support leaves little cash in year 0."""
        build_up = views.year_zero_build_up(result)["HeatPump"]
        gross = build_up.by_category_in_euro[CostCategory.INVESTMENT]
        assert build_up.net_outflow_in_euro < gross

    def test_year_zero_build_up_agrees_with_the_table_beside_it(self, allocated):
        """S4b: the waterfall and the investment table are scoped alike (§2.4 deferral).

        Under an actor scope the two used to disagree — the build-up read the full timeline
        while `component_breakdowns` (the table) is derived from the scoped one. A subject with
        a year-0 build-up must now be a subject the table has an investment row for.
        """
        build_ups = views.year_zero_build_up(allocated)
        table = views.investment_net_of_subsidies(allocated)
        for subject, build_up in build_ups.items():
            if build_up.by_category_in_euro.get(CostCategory.INVESTMENT):
                assert subject in table, subject
                assert build_up.by_category_in_euro[CostCategory.INVESTMENT] == pytest.approx(
                    allocated.component_breakdowns[subject].investment_gross_in_euro.best_estimate
                )

    def test_subsidy_share_matches_manual_ratio(self, result):
        """Share = nominal support / gross investment, per subject."""
        shares = views.subsidy_share_of_gross(result)
        for subject, share in shares.items():
            breakdown = result.component_breakdowns[subject]
            gross = breakdown.investment_gross_in_euro.best_estimate
            subsidy = min(breakdown.subsidies_nominal_in_euro.best_estimate, gross)
            assert share.gross_in_euro == pytest.approx(gross)
            assert share.subsidy_in_euro == pytest.approx(subsidy)
            assert share.net_in_euro == pytest.approx(gross - subsidy)
            assert share.share_of_gross == pytest.approx(subsidy / gross)
            assert 0.0 <= share.share_of_gross <= 1.0

    def test_support_above_gross_is_clamped(self, result):
        """The business rule that used to live in two chart helpers (§5.4, W4.1)."""
        import copy

        doctored = copy.deepcopy(result)
        breakdown = doctored.component_breakdowns["HeatPump"]
        gross = breakdown.investment_gross_in_euro
        breakdown.subsidies_nominal_in_euro = gross.scale(2.0)
        share = views.subsidy_share_of_gross(doctored)["HeatPump"]
        assert share.subsidy_in_euro == pytest.approx(gross.best_estimate)
        assert share.net_in_euro == pytest.approx(0.0)
        assert share.share_of_gross == pytest.approx(1.0)

    def test_total_subsidies_received_sums_the_timeline_entries(self, result):
        """D2 (§8): the nominal support on the scoped timeline, not the solver's awards."""
        entries = [
            entry
            for entry in result.scoped_timeline().entries
            if entry.category == CostCategory.SUBSIDY
        ]
        assert entries  # the fixture really does receive support
        expected = UncertainValue.sum(entry.amount_in_euro for entry in entries).as_revenue()
        total = views.total_subsidies_received(result)
        assert total is not None
        assert total.best_estimate == pytest.approx(expected.best_estimate)
        assert total.minimum == pytest.approx(expected.minimum)
        assert total.maximum == pytest.approx(expected.maximum)
        assert total.best_estimate > 0  # a received-support figure is reported positive

    def test_total_subsidies_received_includes_support_without_an_award(self, result):
        """The old award-based KPI omitted every euro that reaches the timeline otherwise.

        Scheduled payouts (a tax credit in instalments), the §10.1 flat shim and operational
        support all carry `upfront_amount == 0` or no `SubsidyDecision` at all; the timeline
        figure counts them. This pins the direction of D2: dropping the decisions does not
        change the KPI, dropping the entries does.
        """
        import copy

        awards_only = UncertainValue.exact(0.0)
        for decision in result.subsidy_decisions:
            for award in decision.applied:
                awards_only = awards_only + award.upfront_amount
        total = views.total_subsidies_received(result)
        assert total is not None
        assert total.best_estimate > awards_only.best_estimate  # the fixture has a scheduled payout

        without_decisions = copy.deepcopy(result)
        without_decisions.subsidy_decisions = []
        assert views.total_subsidies_received(without_decisions) == total

    def test_investment_net_is_the_unclamped_band_difference(self, result):
        """The investment table's Net column: gross - support, slot-wise, no clamp."""
        nets = views.investment_net_of_subsidies(result)
        for subject, breakdown in result.component_breakdowns.items():
            if breakdown.investment_gross_in_euro.maximum <= 0:
                assert subject not in nets  # the table skips these rows
                continue
            expected = breakdown.investment_gross_in_euro - breakdown.subsidies_nominal_in_euro
            assert nets[subject].best_estimate == pytest.approx(expected.best_estimate)
            assert nets[subject].minimum == pytest.approx(expected.minimum)
            assert nets[subject].maximum == pytest.approx(expected.maximum)

    def test_award_total_prefers_the_schedule_over_the_upfront_amount(self, result):
        """A scheduled payout is worth its instalments; everything else its upfront amount."""
        awards = [award for decision in result.subsidy_decisions for award in decision.applied]
        assert awards
        for award in awards:
            expected = (
                UncertainValue.sum(award.schedule_amounts)
                if award.schedule_amounts
                else award.upfront_amount
            )
            assert views.award_total_amount(award).best_estimate == pytest.approx(expected.best_estimate)

    def test_total_subsidies_is_none_without_support_flows(self, result):
        """A timeline without a SUBSIDY entry omits the KPI rather than publishing a zero."""
        import copy

        without = copy.deepcopy(result)
        without.timeline.entries = [
            entry for entry in without.timeline.entries if entry.category != CostCategory.SUBSIDY
        ]
        assert views.total_subsidies_received(without) is None
