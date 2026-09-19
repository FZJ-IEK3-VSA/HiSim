"""The five invariants of the staged evaluator, and every refusal it is supposed to name.

``economics-hisim-spec.md`` §1.3 lists five properties a staged evaluation must have, and each of
them is one test here: a plan of one stage equals a plain evaluation entry for entry; a stage that
never starts leaves the baseline alone; moving a stage later changes nothing before it; the payer
split is zero-sum; and every breakdown sums to its total per uncertainty slot. They are stated on
synthetic stages (``tests/economics/synthetic_stages.py``) priced against a synthetic country, so
a failure here is a statement about the splice and never about a shipped price.

The second half of the module is the refusal set of step 10 §2: every plan the evaluator declines
to price has a test that it declines it, and that the message names what is wrong.
"""

import pytest

from hisim.economics.evaluator import EconomicEvaluator
from hisim.economics.parameters import EconomicParameters
from hisim.economics.staged import Stage, StagedEvaluationError, StagedEvaluator
from hisim.economics.timeline import Actor
from hisim.economics.uncertainty import UncertainValue

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
        evaluator = StagedEvaluator(database)
        stages = (baseline_stage(), envelope_stage(2), heat_pump_stage(6))
        # pylint: disable=protected-access  # the merge has no public surface of its own
        merged = evaluator._staged_inputs(stages, 2, [{}, {SyntheticPlan.ENVELOPE_SUBJECT: 1.0}], parameters)
        assert merged.existing_assets is not None
        installed = {asset.asset_class.value: asset.installation_year for asset in merged.existing_assets.assets}
        assert installed["WallExternalInsulation"] == SyntheticPlan.YEAR + 2

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
