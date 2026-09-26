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
from typing import Dict, List, Optional, Tuple

import pytest

from hisim.economics.calculators.energy import StatedPriceError, StatedPrices
from hisim.economics.carriers import EnergyCarrier, revenue_subject
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, effective_price_basis_year
from hisim.economics.facts import BillingDeterminants, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters, StatedEnergyPrice
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.provenance import ParameterOrigin
from hisim.economics.staged import Stage, StagedEvaluationError, StagedEvaluator
from hisim.economics.staged_parameters import EchoOrigin
from hisim.economics.tariffs import SupplyKind, TariffContract, TariffSupply
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
    inventory_register,
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
        merged = evaluator._staged_inputs(stages, 2, charged, SyntheticPlan.YEAR)
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
        merged = evaluator._staged_inputs(stages, 1, charged, SyntheticPlan.YEAR)
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
        merged = evaluator._staged_inputs(stages, 1, charged, SyntheticPlan.YEAR)
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


class TestOnePlanOneLedger:
    """Every stage records into one provenance ledger, and every id of the result resolves in it.

    An id on a :class:`~hisim.economics.timeline.CashFlowEntry` is an index into the ledger it was
    recorded in. With one fresh ledger per stage the plan carried stage 0's, so the ids the splice
    copied from stage 1 and stage 2 pointed at stage 0's records — or past its end. One shared
    ledger is what ``cost_provenance.json`` publishes (``economics-backend-spec.md`` §2.3).
    """

    @pytest.fixture(name="result")
    def fixture_result(self, database, parameters):
        """The three-stage synthetic plan, with the heat pump in year 4."""
        return StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0), heat_pump_stage(4)], parameters, brownfield_perspective()
        )

    def test_the_reference_every_stage_and_the_plan_share_the_ledger(self, result):
        """One object, reachable from every result the plan carries."""
        assert result.ledger is not None
        assert result.plan.ledger is result.ledger
        assert result.reference.ledger is result.ledger
        assert all(stage.ledger is result.ledger for stage in result.per_stage)

    def test_every_id_on_every_timeline_resolves(self, result):
        """The reference's, each stage's and the spliced plan's entries point inside the ledger."""
        size = len(result.ledger)
        timelines = [result.reference.timeline, result.plan.timeline] + [
            stage.timeline for stage in result.per_stage
        ]
        for timeline in timelines:
            for entry in timeline.entries:
                assert all(0 <= record_id < size for record_id in entry.provenance_ids), entry

    def test_a_later_stages_purchase_cites_its_own_prices(self, result):
        """The spliced purchases of stages 1 and 2 resolve to their own subject's records.

        The case the per-stage ledgers got wrong: the ids were right in the ledger they came from
        and named another subject's records — or none — in the one the plan carried.
        """
        purchases = [
            (result.stage_of_timeline_entry(position), entry)
            for position, entry in enumerate(result.plan.timeline.entries)
            if entry.category is CostCategory.INVESTMENT
        ]
        assert {stage for stage, _entry in purchases} >= {1, 2}
        for _stage, entry in purchases:
            assert entry.provenance_ids
            cited = [result.ledger.get(record_id).parameter for record_id in entry.provenance_ids]
            assert all(parameter.startswith(f"{entry.subject}.") for parameter in cited), (entry.subject, cited)

    def test_one_record_has_one_id_across_the_stages(self, result):
        """A price every stage reads is interned once: no record appears under two ids."""
        records = result.ledger.records
        assert len(set(records)) == len(records)
        shared = [
            record_id
            for record_id, record in enumerate(records)
            if record.parameter.startswith("energy_prices_")
        ]
        cited_by_stage = [
            {record_id for entry in stage.timeline.entries for record_id in entry.provenance_ids}
            for stage in result.per_stage
        ]
        # The electricity price is read by every stage, and all of them cite the same id for it.
        assert shared and all(set(shared) & cited for cited in cited_by_stage)


def _by_year(result, subject: str, *categories: CostCategory) -> Dict[int, UncertainValue]:
    """One subject's entries of the given categories, summed per year, on one result's timeline."""
    totals: Dict[int, UncertainValue] = {}
    for entry in result.timeline.entries:
        if entry.subject == subject and entry.category in categories:
            totals[entry.year] = totals.get(entry.year, UncertainValue.exact(0.0)) + entry.amount_in_euro
    return totals


def _close(left: UncertainValue, right: UncertainValue) -> bool:
    """Slot-wise equality up to float noise."""
    return all(
        abs(a - b) <= 1e-9 * max(1.0, abs(b))
        for a, b in zip(
            (left.minimum, left.best_estimate, left.maximum), (right.minimum, right.best_estimate, right.maximum)
        )
    )


def _problem_codes(error: StagedEvaluationError) -> List[str]:
    """The codes of the ``problems.json`` rows a refusal carries."""
    return [row["code"] for row in error.problems]


class TestStatedEnergyPrices:
    """``EconomicParameters.energy_prices`` bills year 1 at the stated price (renovisorissues #52).

    The synthetic database prices electricity at 0.30 EUR/kWh with no carbon exposure, so a stated
    price is the whole year-1 working price here; the Irish cases below use the shipped database,
    whose gas and oil rows book their carbon price separately.
    """

    #: The stated all-in electricity working price, as a band.
    WORKING = UncertainValue(best_estimate=0.25, minimum=0.2, maximum=0.3)

    #: The stated standing charge.
    STANDING = UncertainValue.exact(150.0)

    #: The electricity escalation rate the cases state, distinct from the general rate.
    RATE = 0.05

    @pytest.fixture(name="stated")
    def fixture_stated(self, parameters) -> EconomicParameters:
        """The synthetic assumptions with a stated electricity price and rate."""
        stated: EconomicParameters = replace(
            parameters,
            energy_price_escalation_rates={EnergyCarrier.ELECTRICITY: self.RATE},
            energy_prices={
                EnergyCarrier.ELECTRICITY: StatedEnergyPrice(
                    working_price_in_euro_per_kwh=self.WORKING, standing_charge_in_euro_per_year=self.STANDING
                )
            },
        )
        return stated

    @pytest.fixture(name="result")
    def fixture_result(self, database, stated):
        """The baseline against the envelope stage from year 0, under the stated price."""
        return StagedEvaluator(database).evaluate(
            [baseline_stage(), envelope_stage(0)], stated, brownfield_perspective()
        )

    def test_year_one_costs_the_kwh_times_the_stated_price_in_reference_and_plan(self, result):
        """Both evaluations bill the stated band, each for its own consumption."""
        for evaluation, kwh in (
            (result.reference, SyntheticPlan.BASELINE_ELECTRICITY_IN_KWH),
            (result.plan, SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH),
        ):
            working = _by_year(evaluation, "ELECTRICITY", CostCategory.ENERGY_WORKING)
            assert _close(working[1], self.WORKING.scale(kwh))

    def test_the_working_price_escalates_at_the_carrier_rate_from_year_two(self, result):
        """Year t costs the stated price times ``(1 + rate)^(t - 1)``."""
        working = _by_year(result.plan, "ELECTRICITY", CostCategory.ENERGY_WORKING)
        kwh = SyntheticPlan.RENOVATED_ELECTRICITY_IN_KWH
        for year in (2, 5, SyntheticPlan.HORIZON):
            assert _close(working[year], self.WORKING.scale(kwh * (1 + self.RATE) ** (year - 1)))

    def test_the_standing_charge_replaces_the_databases_and_escalates_with_general(self, result, stated):
        """The synthetic database charges nothing; the stated 150 EUR/a is billed and escalated."""
        standing = _by_year(result.plan, "ELECTRICITY", CostCategory.ENERGY_STANDING)
        general = stated.general_price_escalation_rate
        assert _close(standing[1], self.STANDING)
        assert _close(standing[4], self.STANDING.scale((1 + general) ** 3))

    def test_the_contract_billed_is_a_stated_one_with_its_own_id(self, result):
        """The assumptions table does not cite a database entry for the household's own bill."""
        for evaluation in result.per_stage:
            tariff = evaluation.assumptions.tariffs["ELECTRICITY"]
            assert tariff.contract_id == StatedPrices.contract_id(
                SyntheticPlan.COUNTRY, EnergyCarrier.ELECTRICITY, SyntheticPlan.YEAR
            )
            assert not tariff.is_default_contract
            assert StatedPrices.SOURCE_ID in tariff.source_ids

    def test_the_stated_fields_are_request_records_in_the_ledger(self, result):
        """The working and the standing entries cite the plan's statement, not the database row."""
        ledger = result.ledger
        assert ledger is not None
        for category, field_name, value in (
            (CostCategory.ENERGY_WORKING, "working_price_in_euro_per_kwh", self.WORKING),
            (CostCategory.ENERGY_STANDING, "standing_charge_in_euro_per_year", self.STANDING),
        ):
            entry = next(
                entry
                for entry in result.plan.timeline.entries
                if entry.subject == "ELECTRICITY" and entry.category is category
            )
            record = ledger.get(entry.provenance_ids[0])
            assert record.origin is ParameterOrigin.REQUEST
            assert record.parameter == f"parameters.energy_prices.ELECTRICITY.{field_name}"
            assert record.value == value
            assert record.source_ids == (StatedPrices.SOURCE_ID,)
        assert not any(
            record.parameter.endswith("ELECTRICITY@2024.working_price_in_euro_per_kwh") for record in ledger.records
        ), "the database's working price billed nothing and is not cited"

    def test_the_echo_states_the_prices_and_rates_used_with_their_origins(self, result):
        """Stated terms say ``stated``; the feed-in rate nobody stated is the database's."""
        echo = result.energy_echo
        assert echo is not None
        assert echo is not None
        assert echo.rates == {EnergyCarrier.ELECTRICITY: (self.RATE, EchoOrigin.STATED)}
        electricity = echo.prices[EnergyCarrier.ELECTRICITY]
        assert electricity.working_price_in_euro_per_kwh == self.WORKING
        assert electricity.working_price_origin is EchoOrigin.STATED
        assert electricity.standing_charge_origin is EchoOrigin.STATED
        feed_in = echo.prices[EnergyCarrier.ELECTRICITY_FEED_IN]
        low, best, high = SyntheticPlan.FEED_IN_RATE_IN_EURO_PER_KWH
        assert feed_in.working_price_in_euro_per_kwh == UncertainValue(best_estimate=best, minimum=low, maximum=high)
        assert feed_in.working_price_origin is EchoOrigin.DATABASE

    def test_a_stated_feed_in_rate_is_the_revenue_per_kwh_sold_for_twenty_years(self, database, parameters):
        """The electricity contract pays the stated rate, nominally fixed over the whole horizon."""
        rate = 0.12
        stated = replace(
            parameters,
            energy_prices={EnergyCarrier.ELECTRICITY_FEED_IN: StatedEnergyPrice(UncertainValue.exact(rate))},
        )
        sold = SyntheticPlan.SOLD_ELECTRICITY_IN_KWH
        result = StagedEvaluator(database).evaluate(
            [baseline_stage(), heat_pump_stage(0, electricity_sold_in_kwh=sold)], stated, brownfield_perspective()
        )
        revenue = _by_year(result.plan, revenue_subject(EnergyCarrier.ELECTRICITY), CostCategory.FEED_IN_REVENUE)
        assert sorted(revenue) == list(range(1, SyntheticPlan.HORIZON + 1))
        for amount in revenue.values():
            assert _close(amount, UncertainValue.exact(-sold * rate))
        entry = next(entry for entry in result.plan.timeline.entries if entry.category is CostCategory.FEED_IN_REVENUE)
        assert result.ledger is not None
        record = result.ledger.get(entry.provenance_ids[0])
        assert record.origin is ParameterOrigin.REQUEST
        assert record.parameter == "parameters.energy_prices.ELECTRICITY_FEED_IN.working_price_in_euro_per_kwh"
        assert result.energy_echo is not None
        assert result.energy_echo.prices[EnergyCarrier.ELECTRICITY_FEED_IN].working_price_origin is EchoOrigin.STATED

    def test_nothing_stated_bills_exactly_as_before(self, database, parameters):
        """An empty block is the database's contract, entry for entry, with its default id."""
        stages = [baseline_stage(), envelope_stage(0)]
        plain = StagedEvaluator(database).evaluate(stages, parameters, brownfield_perspective())
        assert plain.per_stage[0].assumptions is not None
        assert plain.per_stage[0].assumptions.tariffs["ELECTRICITY"].is_default_contract
        echo = plain.energy_echo
        assert echo is not None
        assert echo.prices[EnergyCarrier.ELECTRICITY].working_price_origin is EchoOrigin.DATABASE
        assert echo.rates[EnergyCarrier.ELECTRICITY] == (parameters.general_price_escalation_rate, EchoOrigin.GENERAL)

    def test_a_carrier_without_a_price_entry_is_refused(self, database, parameters):
        """The synthetic country prices no gas: the stated price would have no emission factor."""
        stated = replace(
            parameters,
            energy_prices={EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(UncertainValue.exact(0.1))},
        )
        with pytest.raises(StagedEvaluationError) as caught:
            StagedEvaluator(database).evaluate([baseline_stage()], stated, brownfield_perspective())
        assert _problem_codes(caught.value) == ["parameters.energy_prices.NATURAL_GAS.invalid"]

    def test_a_stated_price_under_an_explicit_contract_is_refused(self, database, parameters):
        """The contract's price signal may have driven the simulation; its terms stay the contract's."""
        contract = TariffContract(
            id="synthetic_dynamic_tariff",
            carrier=EnergyCarrier.ELECTRICITY,
            country=SyntheticPlan.COUNTRY,
            region=None,
            valid_from_year=SyntheticPlan.YEAR,
            supply=TariffSupply(kind=SupplyKind.FLAT, working_price_in_euro_per_kwh=UncertainValue.exact(0.3)),
            standing_charge_in_euro_per_year=UncertainValue.exact(0.0),
            source_ids=("src_staged_test",),
        )
        envelope = envelope_stage(0)
        contracted = replace(
            envelope, inputs=replace(envelope.inputs, tariff_contracts={EnergyCarrier.ELECTRICITY: contract})
        )
        stated = replace(
            parameters,
            energy_prices={
                EnergyCarrier.ELECTRICITY: StatedEnergyPrice(
                    standing_charge_in_euro_per_year=UncertainValue.exact(1.0)
                ),
                EnergyCarrier.ELECTRICITY_FEED_IN: StatedEnergyPrice(UncertainValue.exact(0.1)),
            },
        )
        with pytest.raises(StagedEvaluationError) as caught:
            StagedEvaluator(database).evaluate([baseline_stage(), contracted], stated, brownfield_perspective())
        assert _problem_codes(caught.value) == [
            "parameters.energy_prices.ELECTRICITY.mismatch",
            "parameters.energy_prices.ELECTRICITY_FEED_IN.mismatch",
        ]
        assert "synthetic_dynamic_tariff" in caught.value.problems[0]["message"]

    def test_the_calculator_refuses_the_same_on_its_own(self, database, parameters):
        """A plain evaluation, which has no problems document, refuses too."""
        contract = TariffContract(
            id="synthetic_dynamic_tariff",
            carrier=EnergyCarrier.ELECTRICITY,
            country=SyntheticPlan.COUNTRY,
            region=None,
            valid_from_year=SyntheticPlan.YEAR,
            supply=TariffSupply(kind=SupplyKind.FLAT, working_price_in_euro_per_kwh=UncertainValue.exact(0.3)),
            standing_charge_in_euro_per_year=UncertainValue.exact(0.0),
            source_ids=("src_staged_test",),
        )
        inputs = replace(baseline_stage().inputs, tariff_contracts={EnergyCarrier.ELECTRICITY: contract})
        stated = replace(
            parameters,
            energy_prices={EnergyCarrier.ELECTRICITY: StatedEnergyPrice(UncertainValue.exact(0.2))},
        )
        with pytest.raises(StatedPriceError, match="explicit contract"):
            EconomicEvaluator(database, stated).evaluate(inputs, brownfield_perspective())


class TestStatedPricesAgainstTheCarbonPath:
    """Irish gas and oil book their carbon price separately; the stated price is all-in (#52).

    Priced against the shipped database, whose ``NATURAL_GAS`` and ``HEATING_OIL`` rows of Ireland
    declare a carbon exposure of 1, so the year-1 bill is the working price *plus* the carbon price
    read off the ``central`` path. Every synthetic subject is an override, so no shipped device
    price reaches these cases.
    """

    #: Gas and oil bought per year, in kWh.
    GAS_IN_KWH = 12000.0
    OIL_IN_KWH = 5000.0

    #: The stated all-in prices.
    GAS_PRICE = 0.15
    OIL_PRICE = UncertainValue(best_estimate=0.13, minimum=0.11, maximum=0.16)

    #: The Irish price basis year of the shipped data.
    YEAR = 2026

    @pytest.fixture(name="shipped", scope="class")
    def fixture_shipped(self) -> CostDatabase:
        """The shipped cost database."""
        return CostDatabase()

    @pytest.fixture(name="irish")
    def fixture_irish(self) -> EconomicParameters:
        """Irish assumptions with the central carbon path."""
        return EconomicParameters(
            observation_period_in_years=SyntheticPlan.HORIZON,
            interest_rate=SyntheticPlan.INTEREST_RATE,
            country="IE",
            price_basis_year=self.YEAR,
            co2_price_scenario="central",
            apply_subsidies=False,
        )

    def _stage(self) -> Stage:
        """The baseline burning gas and oil instead of buying electricity."""
        stage = baseline_stage()
        return replace(
            stage,
            inputs=replace(
                stage.inputs,
                billing=[
                    BillingDeterminants(carrier=EnergyCarrier.NATURAL_GAS, energy_bought_in_kwh=self.GAS_IN_KWH),
                    BillingDeterminants(carrier=EnergyCarrier.HEATING_OIL, energy_bought_in_kwh=self.OIL_IN_KWH),
                ],
            ),
        )

    def _co2_per_kwh(self, shipped: CostDatabase, carrier: EnergyCarrier, year: int) -> float:
        """The carbon price per kWh the engine books for one carrier in one calendar year."""
        entry = shipped.get_energy_price(carrier, self.YEAR, "IE")
        path = shipped.get_co2_price_path("IE", "central")
        assert path is not None and entry.co2_price_exposure > 0
        return entry.co2_price_exposure * entry.emission_factor_in_kg_per_kwh * path.price(year) / 1000.0

    def test_year_one_costs_exactly_the_stated_all_in_price(self, shipped, irish):
        """Working price plus carbon price in year 1 is the kWh times the stated price."""
        stated = replace(
            irish,
            energy_prices={
                EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(UncertainValue.exact(self.GAS_PRICE)),
                EnergyCarrier.HEATING_OIL: StatedEnergyPrice(self.OIL_PRICE),
            },
        )
        result = StagedEvaluator(shipped).evaluate([self._stage()], stated, brownfield_perspective())
        both = (CostCategory.ENERGY_WORKING, CostCategory.ENERGY_CO2_PRICE)
        gas = _by_year(result.plan, "NATURAL_GAS", *both)
        oil = _by_year(result.plan, "HEATING_OIL", *both)
        assert _close(gas[1], UncertainValue.exact(self.GAS_IN_KWH * self.GAS_PRICE))
        assert _close(oil[1], self.OIL_PRICE.scale(self.OIL_IN_KWH))

    def test_later_years_escalate_the_working_price_and_follow_the_carbon_path(self, shipped, irish):
        """Year 3 is 2028: the ex-carbon price escalated twice, plus 2028's carbon price."""
        stated = replace(
            irish, energy_prices={EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(UncertainValue.exact(self.GAS_PRICE))}
        )
        result = StagedEvaluator(shipped).evaluate([self._stage()], stated, brownfield_perspective())
        rate = EconomicEvaluator(shipped, stated).carrier_escalation_rate(EnergyCarrier.NATURAL_GAS)
        carbon_today = self._co2_per_kwh(shipped, EnergyCarrier.NATURAL_GAS, self.YEAR)
        working = _by_year(result.plan, "NATURAL_GAS", CostCategory.ENERGY_WORKING)
        carbon = _by_year(result.plan, "NATURAL_GAS", CostCategory.ENERGY_CO2_PRICE)
        carbon_2028 = self._co2_per_kwh(shipped, EnergyCarrier.NATURAL_GAS, 2028)
        expected = self.GAS_IN_KWH * (self.GAS_PRICE - carbon_today) * (1 + rate) ** 2
        assert _close(working[3], UncertainValue.exact(expected))
        assert _close(carbon[3], UncertainValue.exact(self.GAS_IN_KWH * carbon_2028))
        assert self._co2_per_kwh(shipped, EnergyCarrier.NATURAL_GAS, 2028) > carbon_today, "the path must move"

    def test_the_echo_states_the_database_price_all_in(self, shipped, irish):
        """Unstated gas is echoed as the database's working price plus the year-1 carbon price."""
        result = StagedEvaluator(shipped).evaluate([self._stage()], irish, brownfield_perspective())
        echo = result.energy_echo
        assert echo is not None
        entry = shipped.get_energy_price(EnergyCarrier.NATURAL_GAS, self.YEAR, "IE")
        carbon = self._co2_per_kwh(shipped, EnergyCarrier.NATURAL_GAS, self.YEAR)
        gas = echo.prices[EnergyCarrier.NATURAL_GAS]
        all_in = entry.working_price_in_euro_per_kwh + UncertainValue.exact(carbon)
        assert gas.working_price_in_euro_per_kwh is not None
        assert _close(gas.working_price_in_euro_per_kwh, all_in)
        assert gas.working_price_origin is EchoOrigin.DATABASE
        assert gas.standing_charge_in_euro_per_year == entry.standing_charge_in_euro_per_year
        assert echo.rates[EnergyCarrier.NATURAL_GAS][1] is EchoOrigin.COUNTRY_DEFAULT
        assert EnergyCarrier.ELECTRICITY not in echo.prices, "no stage bills electricity and the plan names none"

    def test_feeding_the_echo_back_prices_the_same_plan(self, shipped, irish):
        """The database's all-in price, stated, gives the same year-1 bill and the same NPV."""
        first = StagedEvaluator(shipped).evaluate([self._stage()], irish, brownfield_perspective())
        echo = first.energy_echo
        assert echo is not None
        stated = replace(
            irish,
            energy_price_escalation_rates={carrier: rate for carrier, (rate, _origin) in echo.rates.items()},
            energy_prices={
                carrier: StatedEnergyPrice(price.working_price_in_euro_per_kwh, price.standing_charge_in_euro_per_year)
                for carrier, price in echo.prices.items()
            },
        )
        second = StagedEvaluator(shipped).evaluate([self._stage()], stated, brownfield_perspective())
        assert _close(second.plan.total_npv_in_euro, first.plan.total_npv_in_euro)
        assert second.energy_echo is not None
        assert {price.working_price_origin for price in second.energy_echo.prices.values()} == {EchoOrigin.STATED}

    def test_a_stated_price_below_the_year_one_carbon_price_is_refused(self, shipped, irish):
        """No working price would be left: the refusal names the carbon price it fell below."""
        carbon = self._co2_per_kwh(shipped, EnergyCarrier.NATURAL_GAS, self.YEAR)
        stated = replace(
            irish,
            energy_prices={EnergyCarrier.NATURAL_GAS: StatedEnergyPrice(UncertainValue.exact(carbon / 2))},
        )
        with pytest.raises(StagedEvaluationError) as caught:
            StagedEvaluator(shipped).evaluate([self._stage()], stated, brownfield_perspective())
        assert _problem_codes(caught.value) == [
            "parameters.energy_prices.NATURAL_GAS.working_price_in_euro_per_kwh.invalid"
        ]
        assert f"{carbon:.6g}" in caught.value.problems[0]["message"]


class TestThePlanStartYear:
    """The weather year and the plan's calendar are different things (renovisorissues #57).

    The stages' ``simulation_year`` is the year of the weather they were simulated with. A plan
    that states when it starts is dated and — when nothing states a price basis year — priced
    from that year, never from its weather.
    """

    #: A weather year no price data covers, so a basis year re-derived from it would show.
    WEATHER_YEAR = SyntheticPlan.YEAR + 6

    @classmethod
    def _stages(cls) -> List[Stage]:
        """The baseline and a heat pump in year 3, simulated with the weather of another year."""
        return [
            replace(stage, inputs=replace(stage.inputs, simulation_year=cls.WEATHER_YEAR))
            for stage in (baseline_stage(), heat_pump_stage(3))
        ]

    def test_the_basis_year_falls_back_to_the_plan_start_year(self, database, parameters):
        """No ``price_basis_year`` stated: every evaluation of the plan prices at its start year."""
        unstated = replace(parameters, price_basis_year=None)
        result = StagedEvaluator(database).evaluate(
            self._stages(), unstated, brownfield_perspective(), plan_start_year=SyntheticPlan.YEAR
        )
        assert result.plan_start_year == SyntheticPlan.YEAR
        for evaluated in (result.reference, result.plan, *result.per_stage):
            assert evaluated.parameters.price_basis_year == SyntheticPlan.YEAR
            assert evaluated.simulation_year == self.WEATHER_YEAR

    def test_a_stated_basis_year_wins_over_the_plan_start_year(self, database, parameters):
        """The start year dates the plan; it does not overrule a basis year the caller named."""
        result = StagedEvaluator(database).evaluate(
            self._stages(), parameters, brownfield_perspective(), plan_start_year=SyntheticPlan.YEAR + 2
        )
        assert result.plan.parameters.price_basis_year == SyntheticPlan.YEAR
        assert result.plan_start_year == SyntheticPlan.YEAR + 2

    def test_without_a_start_year_the_result_states_none(self, database, parameters):
        """Nothing is invented: no start year in, no start year out."""
        result = StagedEvaluator(database).evaluate([baseline_stage()], parameters, brownfield_perspective())
        assert result.plan_start_year is None

    def test_the_policy_itself(self, database):
        """Stated basis year, else the plan start year, else the simulation year; clamped to the data."""
        unstated = EconomicParameters(country=SyntheticPlan.COUNTRY)
        later = SyntheticPlan.YEAR + 6
        assert effective_price_basis_year(unstated, database, later) == later
        assert effective_price_basis_year(unstated, database, later, plan_start_year=later + 1) == later + 1
        assert effective_price_basis_year(unstated, database, 1990, plan_start_year=later) == later
        stated = replace(unstated, price_basis_year=SyntheticPlan.YEAR)
        assert effective_price_basis_year(stated, database, later, plan_start_year=later + 1) == SyntheticPlan.YEAR

    def test_a_start_year_before_the_data_moves_to_the_earliest_covered_year(self):
        """The clamp the simulation year always had applies to the start year the same way."""

        class _DataFrom2024(CostDatabase):
            """A database whose device data begins in 2024; nothing else of it is read."""

            def __init__(self) -> None:  # pylint: disable=super-init-not-called
                """Skip loading any file: only :meth:`earliest_device_year` is asked."""

            def earliest_device_year(self, country: str) -> Optional[int]:
                """The first year the stub prices devices at."""
                return 2024

        unstated = EconomicParameters(country=SyntheticPlan.COUNTRY)
        assert effective_price_basis_year(unstated, _DataFrom2024(), 2030, plan_start_year=2000) == 2024
        assert effective_price_basis_year(unstated, _DataFrom2024(), 2030, plan_start_year=2026) == 2026


class TestOnePlanYearZero:
    """Plan year 0 is ``plan_start_year``, else the price basis year (hisim-dutz, hisim-nl6j).

    Owner decision 2026-09-26: one anchor ages the house's own equipment, dates what a stage buys
    (``year 0 + from_year``) and is the year ``calendar_year`` counts from whenever the plan states
    a start year. The stages' ``simulation_year`` is the year of their weather and dates nothing;
    before this, a stage's purchase was dated from it and a later stage aged it
    ``price basis year - weather year`` years too old.

    Every case below is simulated with the weather of 2019 and priced at 2026, the RenoVisor
    mockup's seven-year gap, over a 20-year horizon so a replacement ``from_year + L`` falls inside
    it. The service life of every synthetic subject is ``L = 10``.
    """

    WEATHER_YEAR = 2019
    BASIS_YEAR = 2026
    HORIZON = 20

    #: The register boiler of the kept-asset cases: installed 2020, so with ``L = 10`` it is due
    #: in the calendar year 2030 whatever year the plan starts in.
    KEPT_BOILER_YEAR = 2020

    @pytest.fixture(name="gap_parameters")
    def fixture_gap_parameters(self, parameters) -> EconomicParameters:
        """The synthetic assumptions, priced at 2026 over 20 years."""
        gapped: EconomicParameters = replace(
            parameters, price_basis_year=self.BASIS_YEAR, observation_period_in_years=self.HORIZON
        )
        return gapped

    @classmethod
    def _weathered(cls, stage: Stage, register: Optional[ExistingAssetRegister] = None) -> Stage:
        """The stage simulated with 2019's weather, optionally over another register."""
        inputs = replace(stage.inputs, simulation_year=cls.WEATHER_YEAR)
        if register is not None:
            inputs = replace(inputs, existing_assets=register)
        return replace(stage, inputs=inputs)

    @classmethod
    def _three_stages(cls) -> List[Stage]:
        """Baseline, a heat pump (and the facade) bought in year 3, a later stage that keeps both.

        The third stage, from year 5, buys nothing new: it is there so the heat pump is *kept* by
        a stage evaluated with the ageing register, which is the path hisim-dutz is about.
        """
        later = replace(heat_pump_stage(5), label="stage 3", job_id="job-later")
        return [cls._weathered(stage) for stage in (baseline_stage(), heat_pump_stage(3), later)]

    @staticmethod
    def _years(result, subject: str, category: CostCategory) -> List[int]:
        """The plan years of one subject's entries in one category."""
        return [
            entry.year
            for entry in result.plan.timeline.entries
            if entry.subject == subject and entry.category is category
        ]

    def _kept_boiler_register(self) -> ExistingAssetRegister:
        """The inventory boiler, installed :attr:`KEPT_BOILER_YEAR` instead of 2010."""
        (boiler,) = inventory_register().assets
        return ExistingAssetRegister(assets=[replace(boiler, installation_year=self.KEPT_BOILER_YEAR)])

    def test_a_stage_purchase_is_dated_from_the_plan_start_year(self, database, gap_parameters):
        """Weather 2019, basis 2026, start 2026: the heat pump bought in year 3 is installed in 2029.

        f = 3, L = 10, T = 20.

            installation_year = plan year 0 + f = 2026 + 3 = 2029   (it was 2019 + 3 = 2022)
            age in the third stage, at 2026   = 2026 - 2029  = -3  (not floored: a stage purchase)
            first replacement                 = L - age      = 10 + 3 = 13 = f + L

        Before, the third stage aged the 2022 unit at 2026, age 4, and replaced it in year
        L - 4 = 6 = f + L - 7: seven years early, the weather-to-basis gap.
        """
        result = StagedEvaluator(database).evaluate(
            self._three_stages(), gap_parameters, brownfield_perspective(), plan_start_year=self.BASIS_YEAR
        )
        for stage in (1, 2):
            life = result.lives_by_stage[stage][SyntheticPlan.HEAT_PUMP_SUBJECT]
            assert life.installation_year_origin is not None
            assert (life.installation_year, life.installation_year_origin.value) == (2029, "stage"), stage
        assert self._years(result, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.INVESTMENT) == [3]
        assert self._years(result, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT) == [
            3 + int(SyntheticPlan.LIFETIME_IN_YEARS)
        ]
        assert result.plan_start_year == self.BASIS_YEAR
        assert self.BASIS_YEAR + 13 == 2029 + SyntheticPlan.LIFETIME_IN_YEARS  # the calendar year it is due

    def test_without_a_start_year_a_stage_purchase_is_dated_from_the_basis_year(self, database, gap_parameters):
        """No start year: plan year 0 is the price basis year 2026, never the weather year 2019.

        The same plan and the same arithmetic as above, anchored on the basis year: installed
        2026 + 3 = 2029, replaced in year 13. The result states no start year, so the document
        dates no year (``calendar_year`` null). This pins the change from the weather year.
        """
        result = StagedEvaluator(database).evaluate(self._three_stages(), gap_parameters, brownfield_perspective())
        assert result.plan_start_year is None
        assert result.lives_by_stage[1][SyntheticPlan.HEAT_PUMP_SUBJECT].installation_year == 2029
        assert result.lives_by_stage[2][SyntheticPlan.HEAT_PUMP_SUBJECT].installation_year == 2029
        assert self._years(result, SyntheticPlan.HEAT_PUMP_SUBJECT, CostCategory.REPLACEMENT) == [13]

    @pytest.mark.parametrize("start, due", [(2026, 4), (2029, 1)])
    def test_a_kept_asset_is_aged_at_plan_year_zero(self, database, gap_parameters, start, due):
        """The house's boiler (2020, L = 10) is due in 2030 whichever year the plan starts in.

            start 2026 (= basis): due = L - (2026 - 2020) = 10 - 6 = 4, calendar 2026 + 4 = 2030
            start 2029 (basis + 3): due = L - (2029 - 2020) = 10 - 9 = 1, calendar 2029 + 1 = 2030

        Three years earlier in plan years, the same calendar year: the price basis year (2026 in
        both) no longer ages it.
        """
        baseline = self._weathered(baseline_stage(), self._kept_boiler_register())
        result = StagedEvaluator(database).evaluate(
            [baseline], gap_parameters, brownfield_perspective(), plan_start_year=start
        )
        assert self._years(result, SyntheticPlan.BOILER_SUBJECT, CostCategory.REPLACEMENT)[0] == due
        assert start + due == self.KEPT_BOILER_YEAR + SyntheticPlan.LIFETIME_IN_YEARS
        assert result.plan.parameters.price_basis_year == self.BASIS_YEAR

    def test_without_a_start_year_a_kept_asset_ages_as_an_unstaged_evaluation_does(self, database, gap_parameters):
        """No start year: the boiler is aged at the price basis year, exactly as before and as unstaged.

            due = L - (2026 - 2020) = 4

        which is the replacement year a plain evaluation books too.
        """
        baseline = self._weathered(baseline_stage(), self._kept_boiler_register())
        staged = StagedEvaluator(database).evaluate([baseline], gap_parameters, brownfield_perspective())
        plain = EconomicEvaluator(database, gap_parameters).evaluate(baseline.inputs, brownfield_perspective())
        assert self._years(staged, SyntheticPlan.BOILER_SUBJECT, CostCategory.REPLACEMENT)[0] == 4
        assert entry_signature(staged.plan.timeline.entries) == entry_signature(plain.timeline.entries)

    def test_the_anchor_itself(self):
        """``plan_start_year`` when stated, else the price basis year; never a weather year."""
        assert StagedEvaluator.plan_year_zero(2029, 2026) == 2029
        assert StagedEvaluator.plan_year_zero(None, 2026) == 2026
