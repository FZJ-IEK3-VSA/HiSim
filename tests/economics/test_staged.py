"""The five invariants of the staged evaluator, and every refusal it is supposed to name.

``economics-hisim-spec.md`` §1.3 lists five properties a staged evaluation must have, and each of
them is one test here: a plan of one stage equals a plain evaluation entry for entry; a stage that
never starts leaves the baseline alone; moving a stage later changes nothing before it; the payer
split is zero-sum; and every breakdown sums to its total per uncertainty slot. They are stated on
synthetic stages (``tests/economics/synthetic_stages.py``) priced against a synthetic country, so
a failure here is a statement about the splice and never about a shipped price.

The second half of the module is the refusal set of step 10 §2: every plan the evaluator declines
to price has a test that it declines it, and that the message names what is wrong. The last class
is step 12 §2.1, where the expectations are hand-computed from the synthetic plan's declared
inputs instead of being compared against another run of the same engine.
"""

from dataclasses import replace
from typing import Dict, Optional, Tuple

import pytest

from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.facts import ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.staged import Stage, StagedEvaluationError, StagedEvaluator
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

from tests.economics.synthetic_stages import (
    SyntheticPlan,
    baseline_stage,
    brownfield_perspective,
    entry_signature,
    envelope_stage,
    flows_by_year,
    heat_pump_stage,
    state_inputs,
    write_database,
)

pytestmark = pytest.mark.base


@pytest.fixture(name="database", scope="module")
def fixture_database(tmp_path_factory):
    """The synthetic cost database, written once for the whole module."""
    return write_database(str(tmp_path_factory.mktemp("staged_cost_database")))


@pytest.fixture(name="parameters")
def fixture_parameters() -> EconomicParameters:
    """The assumptions every synthetic plan is priced under."""
    return EconomicParameters(
        observation_period_in_years=SyntheticPlan.HORIZON,
        interest_rate=SyntheticPlan.INTEREST_RATE,
        country=SyntheticPlan.COUNTRY,
        price_basis_year=SyntheticPlan.YEAR,
        # No carbon price: the synthetic database carries no trajectory, and the CO2 price is not
        # what any of these cases is about.
        co2_price_scenario="none",
        apply_subsidies=False,
    )


class TestTheFiveInvariants:
    """E-spec §1.3, one test per property.

    The whole design of the splice — evaluate every stage with the ordinary evaluator, then take
    each year from the stage active in it — exists so that these hold by construction rather than
    by arithmetic that happens to agree. A failure here therefore means the splice has grown a
    calculation of its own, which is the one thing the module must not do.
    """

    def test_a_plan_of_one_stage_equals_a_plain_evaluation(self, database, parameters):
        """Invariant 1: one stage, entry for entry, amount for amount, in the same order."""
        stage = baseline_stage()
        perspective = brownfield_perspective()
        plain = EconomicEvaluator(database, parameters).evaluate(stage.inputs, perspective)
        staged = StagedEvaluator(database).evaluate([stage], parameters, perspective)
        assert entry_signature(staged.plan.timeline.entries) == entry_signature(plain.timeline.entries)
        assert staged.plan.total_npv_in_euro == plain.total_npv_in_euro
        assert staged.plan.equivalent_annual_cost_in_euro == plain.equivalent_annual_cost_in_euro

    def test_a_stage_that_never_starts_leaves_the_baseline_alone(self, database, parameters):
        """Invariant 2: a second stage at ``T+1`` is never active, so the plan is the baseline."""
        perspective = brownfield_perspective()
        baseline = baseline_stage()
        never = heat_pump_stage(from_year=parameters.observation_period_in_years + 1)
        alone = StagedEvaluator(database).evaluate([baseline], parameters, perspective)
        with_never = StagedEvaluator(database).evaluate([baseline, never], parameters, perspective)
        assert entry_signature(with_never.plan.timeline.entries) == entry_signature(
            alone.plan.timeline.entries
        )
        assert with_never.plan.total_npv_in_euro == alone.plan.total_npv_in_euro

    def test_moving_a_stage_later_changes_no_flow_before_it(self, database, parameters):
        """Invariant 3: the flows of years before ``a`` are the same for a stage at ``a`` and at ``b``."""
        perspective = brownfield_perspective()
        baseline = baseline_stage()
        evaluator = StagedEvaluator(database)
        early = evaluator.evaluate([baseline, heat_pump_stage(3)], parameters, perspective)
        late = evaluator.evaluate([baseline, heat_pump_stage(7)], parameters, perspective)
        early_flows = flows_by_year(early.plan.timeline.entries)
        late_flows = flows_by_year(late.plan.timeline.entries)
        for year in range(0, 3):
            assert early_flows.get(year, 0.0) == pytest.approx(late_flows.get(year, 0.0), abs=1e-9)

    def test_payer_npvs_sum_to_the_system_npv_per_slot(self, database, parameters):
        """Invariant 4: the §6.5 zero-sum property survives staging, slot by slot."""
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, perspective
        )
        total = result.plan.timeline.npv(parameters.interest_rate)
        summed = UncertainValue.exact(0.0)
        for payer, band in result.plan.npv_by_payer.items():
            assert isinstance(payer, Actor)
            summed = summed + band
        for slot in ("minimum", "best_estimate", "maximum"):
            assert getattr(summed, slot) == pytest.approx(getattr(total, slot), abs=0.01)

    def test_every_breakdown_sums_to_its_total_per_slot(self, database, parameters):
        """Invariant 5: the category and per-subject pivots sum to the NPV, to the cent."""
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, perspective
        )
        total = result.plan.total_npv_in_euro
        for pivot in (result.plan.npv_by_category, result.plan.npv_by_component):
            summed = UncertainValue.exact(0.0)
            for band in pivot.values():
                summed = summed + band
            for slot in ("minimum", "best_estimate", "maximum"):
                assert getattr(summed, slot) == pytest.approx(getattr(total, slot), abs=0.01)


class TestStagingSemantics:
    """What the splice is supposed to *do*, beyond the properties it must not break.

    The invariants above are all satisfied by a trivial implementation that ignores every stage
    after the first, so they are not on their own evidence that the plan is staged at all. These
    cases check the three statements that make it one: a later stage's investment falls in its own
    year, a subject carried over is not bought twice, and a stage's equipment ages into the next
    stage's register.
    """

    def test_a_later_stage_books_its_investment_in_its_own_year(self, database, parameters):
        """The heat pump's purchase appears in the year the plan puts it in, and in no other."""
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(5)], parameters, perspective
        )
        years = {
            entry.year
            for entry in result.plan.timeline.entries
            if entry.subject == SyntheticPlan.HEAT_PUMP_SUBJECT
            and entry.category.value == "INVESTMENT"
        }
        assert years == {5}

    def test_a_carried_over_subject_is_not_bought_twice(self, database, parameters):
        """The envelope measure of stage 1 is paid once, although stage 2 still declares it."""
        perspective = brownfield_perspective()
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, perspective
        )
        purchases = [
            entry
            for entry in result.plan.timeline.entries
            if entry.subject == SyntheticPlan.ENVELOPE_SUBJECT and entry.category.value == "INVESTMENT"
        ]
        assert len(purchases) == 1
        assert purchases[0].year == 0

    def test_an_earlier_stages_equipment_ages_into_the_next_register(self, database, parameters):
        """Stage 2's register holds the envelope measure stage 1 installed, at stage 1's year.

        Asserted on the merge itself rather than on a figure downstream of it: the ageing register
        is what makes replacements, residual values and removals fall in the right years, and an
        installation year that is off by the stage's own offset would show up only as a number
        somewhere else being slightly wrong.
        """
        del parameters  # the merge reads only the stages themselves
        evaluator = StagedEvaluator(database)
        stages = (baseline_stage(), envelope_stage(2), heat_pump_stage(6))
        # pylint: disable=protected-access  # the merge has no public surface of its own
        charged = [evaluator._charged_subjects(stages, index) for index in (0, 1)]
        assert charged[1] == {SyntheticPlan.ENVELOPE_SUBJECT: 1.0}
        merged = evaluator._staged_inputs(stages, 2, charged)
        assert merged.existing_assets is not None
        installed = {asset.asset_class.value: asset.installation_year for asset in merged.existing_assets.assets}
        assert installed["WallExternalInsulation"] == SyntheticPlan.YEAR + 2

    def test_the_baselines_own_equipment_keeps_its_inventory_age(self, database, parameters):
        """The boiler the house already has is as old as the inventory says, not as the plan.

        Stage 0 "charges" everything it carries at share 1.0, which is right for the reference's
        own booking, but it must not put that equipment into later registers dated to the
        simulation year: a 2010 boiler that suddenly counts as new would be written off almost in
        full as sunk cost when the heat pump replaces it.
        """
        del parameters
        evaluator = StagedEvaluator(database)
        stages = (baseline_stage(), heat_pump_stage(3))
        # pylint: disable=protected-access  # the merge has no public surface of its own
        charged = [evaluator._charged_subjects(stages, 0)]
        assert charged[0] == {SyntheticPlan.BOILER_SUBJECT: 1.0}
        merged = evaluator._staged_inputs(stages, 1, charged)
        assert merged.existing_assets is not None
        boilers = [asset for asset in merged.existing_assets.assets if asset.asset_class is ComponentType.GAS_HEATER]
        assert len(boilers) == 1
        assert boilers[0].installation_year == SyntheticPlan.INVENTORY_INSTALLATION_YEAR

    def test_the_stage_a_subject_belongs_to_is_reported(self, database, parameters):
        """Every subject the plan pays for knows which stage paid, for the document's waterfall."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)],
            parameters,
            brownfield_perspective(),
        )
        assert result.stage_of_subject(SyntheticPlan.ENVELOPE_SUBJECT) == 1
        assert result.stage_of_subject(SyntheticPlan.HEAT_PUMP_SUBJECT) == 2
        assert result.stage_of_subject("a subject nothing declares") is None

    def test_the_active_stage_of_a_year_is_the_last_one_that_started(self, database, parameters):
        """``stage_of_year`` is the rule the operating flows are spliced by, stated once."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)],
            parameters,
            brownfield_perspective(),
        )
        assert result.stage_of_year(0) == 1
        assert result.stage_of_year(3) == 1
        assert result.stage_of_year(4) == 2
        assert result.stage_of_year(SyntheticPlan.HORIZON) == 2


class TestRefusals:
    """Every plan step 10 §2 says the evaluator must refuse, and the word its message carries.

    A refusal is the product here, not an accident: the message is what a backend puts in front of
    whoever built the plan, so each case asserts that the message names the thing that is wrong
    rather than merely that something was raised.
    """

    def test_a_first_stage_after_year_zero(self, database, parameters):
        """The reference is the state of the house today and cannot start later."""
        stage = Stage(inputs=baseline_stage().inputs, from_year=2, label="baseline")
        with pytest.raises(StagedEvaluationError, match="must start in year 0"):
            StagedEvaluator(database).evaluate([stage], parameters, brownfield_perspective())

    def test_stage_years_that_run_backwards(self, database, parameters):
        """A plan cannot go back in time; two stages sharing a year is a different thing."""
        with pytest.raises(StagedEvaluationError, match="must not run backwards"):
            StagedEvaluator(database).evaluate(
                [baseline_stage(), envelope_stage(5), heat_pump_stage(3)],
                parameters,
                brownfield_perspective(),
            )

    def test_two_stages_in_the_same_year_are_accepted(self, database, parameters):
        """The ordinary baseline-versus-package plan: the package supersedes the baseline at once."""
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0)], parameters, brownfield_perspective()
        )
        assert result.stage_of_year(0) == 1

    def test_a_stage_beyond_the_horizon(self, database, parameters):
        """A stage after the first year outside the horizon prices nothing and is refused."""
        with pytest.raises(StagedEvaluationError, match="past the last year"):
            StagedEvaluator(database).evaluate(
                [baseline_stage(), heat_pump_stage(parameters.observation_period_in_years + 2)],
                parameters,
                brownfield_perspective(),
            )

    def test_stages_simulated_for_different_years(self, database, parameters):
        """Two stages dated to different years cannot share one calendar axis."""
        other = state_inputs(
            [("HeatPump", heat_pump_stage(0).inputs.cost_facts[1].facts.asset_class, 9.0, 18000.0)],
            SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
        )
        other.simulation_year = SyntheticPlan.YEAR + 1
        with pytest.raises(StagedEvaluationError, match="simulation_year"):
            StagedEvaluator(database).evaluate(
                [baseline_stage(), Stage(inputs=other, from_year=3, label="stage 1")],
                parameters,
                brownfield_perspective(),
            )

    def test_stages_simulated_over_different_periods(self, database, parameters):
        """A stage annualized from a different period would put incomparable years side by side."""
        other = heat_pump_stage(3)
        other.inputs.simulated_period_fraction = 0.5
        with pytest.raises(StagedEvaluationError, match="simulated_period_fraction"):
            StagedEvaluator(database).evaluate(
                [baseline_stage(), other], parameters, brownfield_perspective()
            )

    def test_a_country_without_price_data(self, database):
        """A country the database has no energy price for is named, not defaulted to another."""
        elsewhere = EconomicParameters(
            observation_period_in_years=SyntheticPlan.HORIZON,
            country="ZZ",
            price_basis_year=SyntheticPlan.YEAR,
            co2_price_scenario="none",
        )
        with pytest.raises(StagedEvaluationError, match="no energy price data"):
            StagedEvaluator(database).evaluate(
                [baseline_stage()], elsewhere, brownfield_perspective()
            )

    def test_an_empty_plan(self, database, parameters):
        """A plan with no stages is a request for nothing, and says so."""
        with pytest.raises(StagedEvaluationError, match="at least one stage"):
            StagedEvaluator(database).evaluate([], parameters, brownfield_perspective())


class TestAgeingFromTheStagesOwnYear:
    """Step 12 §2.1: a stage's purchases wear out from the year the plan buys them.

    A stage is evaluated alone with its investment in *its* year 0, so its replacement entries sit
    at multiples of the service life counted from there and its residual value writes down an
    asset as old as the whole horizon. Neither is the plan's answer for a stage that starts in year
    ``f``: the unit is bought in year ``f``, is replaced ``f`` years later than the stage says, and
    still has ``f`` years of life left at the horizon that the stage's own write-down never sees.

    Every expectation below is computed from the declared inputs of
    ``tests/economics/synthetic_stages.py`` — the purchase price ``I``, the service life ``L = 10``
    years, the horizon ``T = 12`` years and the engine's default investment escalation rate
    ``e = 2 %`` — and never from the evaluator. The arithmetic is written out in each docstring.
    """

    #: The investment escalation rate every synthetic subject is escalated at: no defaults file is
    #: written for country XX, so the fallback chain ends at the general investment rate, which
    #: `EconomicParameters` declares as 2 %.
    ESCALATION_RATE = 0.02

    @staticmethod
    def _entries(result, subject, category):
        """The ``(year, best estimate)`` pairs of one subject's entries in one category."""
        return [
            (entry.year, entry.amount_in_euro.best_estimate)
            for entry in result.plan.timeline.entries
            if entry.subject == subject and entry.category.value == category
        ]

    def test_a_purchase_that_outlives_the_horizon_earns_a_residual_at_its_own_age(
        self, database, parameters
    ):
        """A heat pump bought in year 3 has one year of life left at year 12, and is never replaced.

        f = 3, L = 10, T = 12, so f + L = 13 > T: nothing is re-bought inside the horizon, and the
        write-down is of the year-3 purchase.

            investment = I(1+e)^f     = 18000 x 1.02^3       = 19101.744
            residual   = investment x (f + L - T) / L
                       = 19101.744 x (3 + 10 - 12) / 10      =  1910.1744, booked as revenue at 12

        The stage's own evaluation says the opposite of both halves: it buys in its year 0, would
        replace in year 10 and would write nothing down at all, because a unit ten years into a
        ten-year life is worth nothing at the horizon.
        """
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(3)], parameters, brownfield_perspective()
        )
        investment = SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + self.ESCALATION_RATE) ** 3
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "INVESTMENT") == [
            (3, pytest.approx(investment, abs=0.01))
        ]
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "REPLACEMENT") == []
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "RESIDUAL_VALUE") == [
            (
                SyntheticPlan.HORIZON,
                pytest.approx(-investment * (3 + SyntheticPlan.LIFETIME_IN_YEARS - 12) / 10, abs=0.01),
            )
        ]

    def test_a_purchase_is_replaced_a_service_life_after_the_year_it_was_bought(
        self, database, parameters
    ):
        """A heat pump bought in year 1 is replaced in year 11, not in year 10.

        f = 1, L = 10, T = 12, so f + L = 11 < T and the unit is re-bought once, at the price level
        of year 11; the second unit then has nine of its ten years left at the horizon.

            replacement = I(1+e)^(f+L)  = 18000 x 1.02^11          = 22380.7376
            residual    = replacement x (11 + 10 - 12) / 10        = 20142.6638, revenue at 12

        Without the shift the stage's own year-10 replacement would be booked a year early and at
        a year-10 price, which is the "a battery bought in year 5 is replaced five years too early"
        case of step 12 §2.1.
        """
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(1)], parameters, brownfield_perspective()
        )
        replacement = SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + self.ESCALATION_RATE) ** 11
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "REPLACEMENT") == [
            (11, pytest.approx(replacement, abs=0.01))
        ]
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "RESIDUAL_VALUE") == [
            (SyntheticPlan.HORIZON, pytest.approx(-replacement * 0.9, abs=0.01))
        ]

    def test_both_stages_purchases_earn_their_residual(self, database, parameters):
        """With a later stage in the plan, the earlier stage's purchase is still written down.

        The envelope measure is bought in year 0 and re-bought in year 10 (its first ten years are
        up and the heat-pump stage, active from year 4, schedules the re-purchase from its own
        register); the heat pump is bought in year 4 and outlives the horizon. Both earn a residual:

            envelope replacement = 24000 x 1.02^10                 = 29255.8661
            envelope residual    = 29255.8661 x (10 + 10 - 12)/10  = 23404.6929, revenue at 12
            heat pump investment = 18000 x 1.02^4                  = 19483.7789
            heat pump residual   = 19483.7789 x (4 + 10 - 12)/10   =  3896.7558, revenue at 12

        Before step 12 §2.1 the envelope earned nothing at all here: the stage holding it at the
        horizon treats it as an asset that predates its own year 0, and §3.6 rule 3 credits such an
        asset with nothing.
        """
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)],
            parameters,
            brownfield_perspective(),
        )
        envelope_replacement = (
            SyntheticPlan.ENVELOPE_INVESTMENT_IN_EURO * (1 + self.ESCALATION_RATE) ** 10
        )
        heat_pump_investment = (
            SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + self.ESCALATION_RATE) ** 4
        )
        assert self._entries(result, SyntheticPlan.ENVELOPE_SUBJECT, "REPLACEMENT") == [
            (10, pytest.approx(envelope_replacement, abs=0.01))
        ]
        assert self._entries(result, SyntheticPlan.ENVELOPE_SUBJECT, "RESIDUAL_VALUE") == [
            (SyntheticPlan.HORIZON, pytest.approx(-envelope_replacement * 0.8, abs=0.01))
        ]
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "RESIDUAL_VALUE") == [
            (SyntheticPlan.HORIZON, pytest.approx(-heat_pump_investment * 0.2, abs=0.01))
        ]

    def test_the_discounted_price_of_a_spliced_purchase_is_pinned_to_the_cent(
        self, database, parameters
    ):
        """One absolute figure the splice cannot agree with itself about.

        Every other staged test compares one code path against another, so a uniformly wrong
        escalation or discount factor would satisfy both. This one states the number outright:
        a heat pump of 18 000 EUR bought in year 3 is paid at the year-3 price level and
        discounted back three years at the 3 % discount rate.

            18000 x 1.02^3 / 1.03^3 = 19101.744 / 1.092727 = 17480.80 EUR
        """
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(3)], parameters, brownfield_perspective()
        )
        breakdown = result.plan.component_breakdowns[SyntheticPlan.HEAT_PUMP_SUBJECT]
        investment = breakdown.npv_by_category[CostCategory.INVESTMENT]
        assert investment.best_estimate == pytest.approx(17480.80, abs=0.01)

    def test_the_replacement_reserve_follows_the_shifted_replacement_years(
        self, database, parameters
    ):
        """The operating view's sinking fund pays for the plan's replacements, not a stage's.

        Under OPERATING_ONLY no capital is charged at all; the replacements are levelized into an
        annual reserve instead (``cost_spec.md`` §4.2). With the envelope and the heat pump bought
        in year 1 their re-purchases fall in year 11, so that is the year the fund has to discount
        from — a fund built on the stage's own year-10 schedule would be a year's interest too
        large.

            flows at 11 = (24000 + 18000) x 1.02^11 = 52221.7209
            reserve     = 52221.7209 x 1.03^-11 x a(12, 3 %)
                        = 52221.7209 x 0.7224213 x 0.10046209 = 3790.04 EUR a year
        """
        operating = Perspective(
            id="operating",
            installation_context=InstallationContext.OPERATING_ONLY,
            subsidy_mode=SubsidyMode.none(),
        )
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(1)], parameters, operating
        )
        reserve = [
            entry
            for entry in result.plan.timeline.entries
            if entry.category is CostCategory.REPLACEMENT_RESERVE
        ]
        assert len(reserve) == SyntheticPlan.HORIZON
        assert reserve[0].amount_in_euro.best_estimate == pytest.approx(3790.04, abs=0.01)

    def test_a_subject_that_grows_out_of_nothing_is_charged_in_full(self, database, parameters):
        """A device declared at size 0 is absent, so the stage that sizes it pays for all of it.

        ``ComponentCostFacts`` allows a size of exactly zero and means "not installed" by it, so
        the increment rule — charge ``(new - old) / new`` for a device that grew — does not apply:
        there was nothing to grow out of. Before step 12 §2.4 such a subject was charged nothing at
        all and quietly counted as carried over.
        """
        # An empty inventory: the house has no boiler for the pump to replace, so the case is
        # about the charge share alone and no like-for-like price is looked up.
        absent = state_inputs(
            [(SyntheticPlan.HEAT_PUMP_SUBJECT, ComponentType.HEAT_PUMP, 0.0, 0.0)],
            SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH,
            register=ExistingAssetRegister(assets=[]),
        )
        installed = state_inputs(
            [
                (
                    SyntheticPlan.HEAT_PUMP_SUBJECT,
                    ComponentType.HEAT_PUMP,
                    9.0,
                    SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO,
                )
            ],
            SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH,
            register=ExistingAssetRegister(assets=[]),
        )
        stages = (
            Stage(inputs=absent, from_year=0, label="baseline"),
            Stage(inputs=installed, from_year=2, label="stage 1"),
        )
        # pylint: disable=protected-access  # the charge table has no public surface of its own
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {
            SyntheticPlan.HEAT_PUMP_SUBJECT: 1.0
        }
        result = StagedEvaluator(database).evaluate(list(stages), parameters, brownfield_perspective())
        # Year 0 is the absent device's own 0 EUR purchase, which stage 0 books like any other.
        assert self._entries(result, SyntheticPlan.HEAT_PUMP_SUBJECT, "INVESTMENT") == [
            (0, 0.0),
            (
                2,
                pytest.approx(
                    SyntheticPlan.HEAT_PUMP_INVESTMENT_IN_EURO * (1 + self.ESCALATION_RATE) ** 2,
                    abs=0.01,
                ),
            ),
        ]

    def test_a_charged_subject_of_size_zero_reaches_no_register(self, database, parameters):
        """A stage that charges a device of size zero does not put one into the next register.

        ``ExistingAsset`` refuses a size of zero, so a stage-0 subject declared at size 0 used to
        raise a ``ValueError`` out of the middle of the next stage's pricing. Nothing was built, so
        nothing ages: the merged register carries the house inventory and no entry for it.
        """
        absent = state_inputs(
            [(SyntheticPlan.HEAT_PUMP_SUBJECT, ComponentType.HEAT_PUMP, 0.0, 0.0)],
            SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH,
            register=ExistingAssetRegister(assets=[]),
        )
        stages = (
            Stage(inputs=absent, from_year=0, label="baseline"),
            envelope_stage(2),
        )
        evaluator = StagedEvaluator(database)
        # pylint: disable=protected-access  # the merge has no public surface of its own
        charged = [evaluator._charged_subjects(stages, 0)]
        merged = evaluator._staged_inputs(stages, 1, charged)
        assert merged.existing_assets is not None
        classes = {asset.asset_class for asset in merged.existing_assets.assets}
        assert ComponentType.HEAT_PUMP not in classes
        evaluator.evaluate(list(stages), parameters, brownfield_perspective())


class TestThePlansHeatCostDividesByItsDiscountedHeat:
    """Decision A of the PR #812 review: the plan's LCOH is NPV(costs) / NPV(heat).

    Each horizon year's heat is the heat of the stage active in that year, discounted with the
    factors the cost NPV uses, over the years 1..T its energy flows are booked in. The expectations
    are computed by hand from those definitions, not by a second run of the engine.
    """

    BASELINE_HEAT_IN_KWH = 20000.0
    RENOVATED_HEAT_IN_KWH = 8000.0

    @staticmethod
    def _with_heat(stage: Stage, heat) -> Stage:
        """The stage with its heat demand replaced."""
        return replace(stage, inputs=replace(stage.inputs, annual_heat_demand_in_kwh=heat))

    def test_two_stages_divide_the_cost_npv_by_the_heat_npv(self, database, parameters):
        """Heat of 20 MWh a year for years 1..3, then 8 MWh from year 4: the hand-made NPV ratio."""
        stages = [
            self._with_heat(baseline_stage(), self.BASELINE_HEAT_IN_KWH),
            self._with_heat(heat_pump_stage(4), self.RENOVATED_HEAT_IN_KWH),
        ]

        plan = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective()).plan

        rate = parameters.interest_rate
        heat_npv = sum(
            (self.BASELINE_HEAT_IN_KWH if year < 4 else self.RENOVATED_HEAT_IN_KWH) / (1.0 + rate) ** year
            for year in range(1, SyntheticPlan.HORIZON + 1)
        )
        levelized = plan.levelized_cost_of_heat_in_euro_per_kwh
        assert levelized is not None
        for slot in ("minimum", "best_estimate", "maximum"):
            assert getattr(levelized, slot) == pytest.approx(
                getattr(plan.total_npv_in_euro, slot) / heat_npv, rel=1e-12
            )
        # The published assumption is the equivalent annual heat the KPI divided by, so the
        # heat-cost derivation (equivalent annual cost / this figure) states the same division.
        assert plan.assumptions is not None
        equivalent_heat = plan.assumptions.annual_heat_demand_in_kwh
        assert equivalent_heat == pytest.approx(parameters.annuity_factor() * heat_npv, rel=1e-12)
        assert equivalent_heat is not None
        assert self.RENOVATED_HEAT_IN_KWH < equivalent_heat < self.BASELINE_HEAT_IN_KWH

    def test_one_stage_over_the_whole_horizon_divides_exactly_as_before(self, database, parameters):
        """A plan as RenoVisor builds it starts every stage in year 0: the last stage's heat, bit for bit."""
        stages = [
            self._with_heat(baseline_stage(), self.BASELINE_HEAT_IN_KWH),
            self._with_heat(envelope_stage(0), self.RENOVATED_HEAT_IN_KWH),
        ]

        plan = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective()).plan

        assert plan.levelized_cost_of_heat_in_euro_per_kwh == plan.total_npv_in_euro.scale(
            parameters.annuity_factor() / self.RENOVATED_HEAT_IN_KWH
        )
        assert plan.assumptions is not None
        assert plan.assumptions.annual_heat_demand_in_kwh == self.RENOVATED_HEAT_IN_KWH

    def test_an_active_stage_without_heat_leaves_the_plans_figure_out(self, database, parameters):
        """A horizon whose heat is known for some years only has no NPV of heat to divide by."""
        stages = [
            self._with_heat(baseline_stage(), None),
            self._with_heat(heat_pump_stage(4), self.RENOVATED_HEAT_IN_KWH),
        ]

        plan = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective()).plan

        assert plan.levelized_cost_of_heat_in_euro_per_kwh is None

    def test_a_stage_that_is_never_active_does_not_count(self, database, parameters):
        """A baseline replaced in year 0 contributes no year of heat, stated or not."""
        stages = [
            self._with_heat(baseline_stage(), None),
            self._with_heat(envelope_stage(0), self.RENOVATED_HEAT_IN_KWH),
        ]

        plan = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective()).plan

        assert plan.assumptions is not None
        assert plan.assumptions.annual_heat_demand_in_kwh == self.RENOVATED_HEAT_IN_KWH


class TestAReplacedBufferIsBoughtWhole:
    """renovisorissues #48: anything the stage's register declares replaced is a new purchase.

    The increment rule charges ``(new - old) / new`` of a subject present in both stages and larger
    in the later one. A heating_system measure does not enlarge the buffer, though: it takes the old
    vessel out and puts a bigger one in, which the stage's own evaluation already prices -- the full
    new price, the old one's removal and write-off, the anyway credit. Charging only the increment of
    that booked a fraction of a replacement and left the rest of the new vessel on the reference.
    The rule holds for every replaced device, not the buffer alone (owner decision 2026-09-26, which
    reversed the buffer-only limit of the same day): the increment is only for enlarging a device
    the house keeps.
    """

    #: The subject both stages declare, as the translator names the space-heating buffer.
    BUFFER_SUBJECT = "SimpleHotWaterStorage"

    #: The old vessel's size and the new one's, in the facts' unit, and the new one's price.
    OLD_SIZE = 400.0
    NEW_SIZE = 1000.0
    NEW_PRICE_IN_EURO = 10000.0

    def _register(self, replaced: bool) -> ExistingAssetRegister:
        """The house's old buffer, declared replaced by a buffer or kept."""
        return ExistingAssetRegister(
            assets=[
                ExistingAsset(
                    asset_class=ComponentType.SPACE_HEATING_STORAGE,
                    size=self.OLD_SIZE,
                    size_unit=Units.KILOWATT,
                    installation_year=SyntheticPlan.YEAR - 19,
                    replaced_by_asset_classes=[ComponentType.SPACE_HEATING_STORAGE] if replaced else [],
                    replacement_cost_override_in_euro=UncertainValue.exact(4000.0),
                )
            ]
        )

    def _stage(self, size: float, replaced: bool, from_year: int, label: str) -> Stage:
        """One state of the house with a buffer of the given size."""
        return Stage(
            inputs=state_inputs(
                [
                    (
                        self.BUFFER_SUBJECT,
                        ComponentType.SPACE_HEATING_STORAGE,
                        size,
                        size / self.NEW_SIZE * self.NEW_PRICE_IN_EURO,
                    )
                ],
                SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH,
                register=self._register(replaced),
            ),
            from_year=from_year,
            label=label,
        )

    def test_a_replaced_subject_is_charged_in_full(self, database):
        """The register says the stage replaces the buffer, so the stage pays for all of it."""
        stages = (self._stage(self.OLD_SIZE, False, 0, "baseline"), self._stage(self.NEW_SIZE, True, 0, "package"))
        # pylint: disable=protected-access  # the charge table has no public surface of its own
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {self.BUFFER_SUBJECT: 1.0}

    def test_an_enlarged_subject_nothing_replaces_is_still_charged_its_increment(self, database):
        """E-spec §1.2 item 2 is unchanged where the register declares no replacement."""
        stages = (self._stage(self.OLD_SIZE, False, 0, "baseline"), self._stage(self.NEW_SIZE, False, 0, "package"))
        # pylint: disable=protected-access
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {
            self.BUFFER_SUBJECT: pytest.approx((self.NEW_SIZE - self.OLD_SIZE) / self.NEW_SIZE)
        }

    @staticmethod
    def _pv_stage(size: float, replaced: Optional[bool], label: str) -> Stage:
        """A state with a PV array of ``size`` kW; ``replaced`` None means the house had no array."""
        assets = []
        if replaced is not None:
            assets.append(
                ExistingAsset(
                    asset_class=ComponentType.PV,
                    size=4.0,
                    size_unit=Units.KILOWATT,
                    installation_year=SyntheticPlan.YEAR - 10,
                    replaced_by_asset_classes=[ComponentType.PV] if replaced else [],
                    replacement_cost_override_in_euro=UncertainValue.exact(6000.0),
                )
            )
        return Stage(
            inputs=state_inputs(
                [("PVSystem", ComponentType.PV, size, size * 1500.0)],
                SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH,
                register=ExistingAssetRegister(assets=assets),
            ),
            from_year=0,
            label=label,
        )

    def test_a_replaced_array_is_bought_whole(self, database):
        """An array the register declares replaced is a new array, not the 6 kW it adds to the old one."""
        stages = (self._pv_stage(4.0, False, "baseline"), self._pv_stage(10.0, True, "package"))
        # pylint: disable=protected-access
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {"PVSystem": 1.0}

    def test_a_same_size_replacement_is_not_free(self, database):
        """A replacement of the same size was charged nothing under the increment rule; it is bought whole."""
        stages = (self._pv_stage(4.0, False, "baseline"), self._pv_stage(4.0, True, "package"))
        # pylint: disable=protected-access
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {"PVSystem": 1.0}

    def test_an_enlarged_kept_array_is_still_charged_its_increment(self, database):
        """An array the house keeps and enlarges from 4 to 10 kW pays for the 6 kW it adds: 6 / 10."""
        stages = (self._pv_stage(4.0, False, "baseline"), self._pv_stage(10.0, False, "package"))
        # pylint: disable=protected-access
        assert StagedEvaluator(database)._charged_subjects(stages, 1) == {"PVSystem": pytest.approx(0.6)}

    def test_a_replacement_an_earlier_stage_made_is_not_bought_again(self, database):
        """Stage 2 still declares stage 1's replacement, which it carries and does not repeat."""
        stages = (
            self._stage(self.OLD_SIZE, False, 0, "baseline"),
            self._stage(self.NEW_SIZE, True, 0, "stage 1"),
            self._stage(self.NEW_SIZE, True, 4, "stage 2"),
        )
        # pylint: disable=protected-access
        assert StagedEvaluator(database)._charged_subjects(stages, 2) == {}

    def test_the_plan_books_the_new_vessel_at_its_price_and_credits_the_old_one(self, database, parameters):
        """The whole new price in the stage's year, and the anyway credit of the worn-out vessel."""
        stages = [self._stage(self.OLD_SIZE, False, 0, "baseline"), self._stage(self.NEW_SIZE, True, 0, "package")]
        result = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective())

        booked: Dict[Tuple[CostCategory, Optional[int], int], float] = {}
        for position, entry in enumerate(result.plan.timeline.entries):
            if entry.subject == self.BUFFER_SUBJECT:
                key = (entry.category, result.stage_of_timeline_entry(position), entry.year)
                booked[key] = booked.get(key, 0.0) + entry.amount_in_euro.best_estimate
        assert booked[(CostCategory.INVESTMENT, 1, 0)] == pytest.approx(self.NEW_PRICE_IN_EURO)
        # The credit falls in the year the worn-out vessel would have been replaced anyway.
        credited = [
            amount
            for (category, stage, _year), amount in booked.items()
            if category is CostCategory.ANYWAY_COST_CREDIT and stage == 1
        ]
        assert len(credited) == 1 and credited[0] < 0.0
        # The baseline keeps its vessel, so it books no purchase of its own.
        assert not [key for key in booked if key[0] is CostCategory.INVESTMENT and key[1] == 0]
