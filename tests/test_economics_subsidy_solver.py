"""Unit tests for the cumulation solver and its caps (§5.4-§5.5).

Second of the two subsidy unit-test files (split per the PR-3 review's 500-line rule): the
exponential-guard on subset enumeration (B6), the shipped BEG/§35c stacking, caps and
exclusions, the per-slot EU state-aid overall cap (B12 — wide synthetic bands, hand
derivations in the docstrings), and subsidy-mode filtering before the solve (B5). Catalog,
condition and provenance tests are in `test_economics_subsidies.py`; evaluator-driven cases
are in `test_economics_subsidy_integration.py`.
"""

from typing import Optional

import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    BenefitKind,
    Condition,
    CumulationLimits,
    EligibleCostSpec,
    HeritageStatus,
    LumpSumBenefit,
    MeasureForSubsidy,
    PayoutKind,
    ShareBenefit,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyDataError,
    SubsidyScheme,
    TaxCreditBenefit,
    required_questions,
    solve_cumulation,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

DISCOUNT = EconomicParameters(price_basis_year=2024).discount_factor


@pytest.fixture(name="catalog", scope="module")
def fixture_catalog() -> SubsidyCatalog:
    """The shipped DE subsidy catalog.

    Used wherever the *real* scheme definitions are the subject — BEG stacking and its 70 % cap,
    the §35c exclusion, the speed and income bonuses, the questionnaire derived from their
    conditions. Tests about solver *mechanics* build synthetic catalogs instead, so a change to
    German subsidy law moves only the tests that are about German subsidy law. Module-scoped
    because loading resolves and validates the whole catalog including its source registry.
    """
    return SubsidyCatalog.load("DE")


def make_measure(cost: float = 30000.0, scop: float = 4.0, refrigerant: str = "R290") -> MeasureForSubsidy:
    """A heat pump measure for subsidy tests.

    The cost is stated rather than resolved from the database, so awards read as percentages of a
    round number; `measure_kind="REPLACE"` is what the BEG schemes require. SCOP and refrigerant
    are the two technical attributes the DE efficiency conditions read: the defaults (4.0, R290)
    satisfy them, and passing 2.8 / R32 is how a test makes the base scheme fail its technical
    minimum.
    """
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        technical_attributes={"scop": scop, "refrigerant": refrigerant},
    )
    return MeasureForSubsidy(
        subject="HeatPump",
        facts=facts,
        measure_kind="REPLACE",
        cost_by_category={CostCategory.INVESTMENT: UncertainValue.exact(cost)},
    )


def full_context(income: float = 35000.0) -> SubsidyContext:
    """Owner-occupier with a functioning gas boiler, everything answered.

    "Everything answered" is the point: no field is None, so no scheme can come back UNDETERMINED
    and every decision below is a definite yes or no. Tests that want the tri-state path
    deliberately blank one field afterwards. The functioning gas boiler is what makes the BEG
    speed bonus eligible, and 35 000 EUR of taxable household income sits below the income-bonus
    threshold — pass a higher figure to drop that bonus. One dwelling unit and no commercial area
    keep the residential-share proration at 1.0.
    """
    return SubsidyContext(
        applicant=ApplicantProfile(
            actor=ApplicantActor.OWNER_OCCUPIER, taxable_household_income_in_euro=income, main_residence=True
        ),
        building=SubsidyBuildingContext(
            construction_year=1985,
            dwelling_units=1,
            residential_floor_area_in_m2=150.0,
            commercial_floor_area_in_m2=0.0,
            existing_heating=ExistingAsset(
                asset_class=ComponentType.GAS_HEATER,
                size=15.0,
                size_unit=Units.KILOWATT,
                installation_year=2005,
                is_functional=True,
                energy_carrier=EnergyCarrier.NATURAL_GAS,
            ),
        ),
    )


def make_scheme(
    scheme_id: str,
    eligibility: Condition,
    benefit_kind: BenefitKind = BenefitKind.SHARE_OF_ELIGIBLE_COST,
    benefit=None,
    eligible_cost: Optional[EligibleCostSpec] = None,
) -> SubsidyScheme:
    """A minimal heat pump scheme whose eligibility is spelled out by the caller.

    Everything a scheme needs to be *valid* is filled in with an inert default — no cumulation
    group, no rate cap, no exclusions, no eligible-cost restriction, an upfront grant — so a test
    varies only the dimension it is about and the rest cannot interfere. The default benefit is a
    deliberately tiny 1 % share: schemes built for the solver's *guard* tests must be eligible and
    stackable without their amounts mattering.
    """
    return SubsidyScheme(
        id=scheme_id,
        country="DE",
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic test scheme",
        url="https://example.invalid/scheme",
        asset_classes=[ComponentType.HEAT_PUMP],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=eligibility,
        benefit_kind=benefit_kind,
        benefit=benefit if benefit is not None else ShareBenefit(rate=0.01),
        eligible_cost=eligible_cost if eligible_cost is not None else EligibleCostSpec(),
        cumulation_group=None,
        combined_rate_cap=None,
        excludes=[],
        payout_kind=PayoutKind.UPFRONT_GRANT,
    )


def make_catalog(schemes, overall_cap_share: Optional[float] = None) -> SubsidyCatalog:
    """An in-memory catalog around the given schemes.

    No file, no country data, no questions — the solver only needs the scheme list and the
    optional EU state-aid `overall_cap_share`, which is exactly the knob the per-slot cap tests
    turn. Schemes constructed this way record `IN_MEMORY_DEFINITION` provenance rather than
    citing a registry entry (W2.4), which is itself asserted in `TestSubsidyProvenance`.
    """
    return SubsidyCatalog(
        schemes=list(schemes),
        questions={},
        snapshot_date=None,
        overall_cap_share=overall_cap_share,
        base_path="",
        country="DE",
    )


ALWAYS_ELIGIBLE = Condition(kind="all")

NEEDS_HOUSEHOLD_SIZE = Condition(kind="leaf", fieldname="applicant.household_size", op=">=", value=1)


def banded_measure(investment: UncertainValue, planning: float = 0.0) -> MeasureForSubsidy:
    """A heat pump measure with a genuinely banded cost basis (§3.9).

    The counterpart to `make_measure`, and the reason it exists separately: every shipped catalog
    and every worked example uses degenerate bands, under which a per-slot bug is invisible. The
    optional exact planning cost sits in a *second* cost category, so two schemes can disagree
    about which categories are eligible — that difference is what makes the per-slot cap ratios
    non-monotone and exposes the B12 ordering problem.
    """
    facts = ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT)
    return MeasureForSubsidy(
        subject="HeatPump",
        facts=facts,
        measure_kind="INSTALL",
        cost_by_category={
            CostCategory.INVESTMENT: investment,
            CostCategory.PLANNING: UncertainValue.exact(planning),
        },
    )


BEG_HP_SCHEMES = {
    "DE_BEG_EM_HP_BASE_2024",
    "DE_BEG_EM_HP_SPEED_2024",
    "DE_BEG_EM_HP_INCOME_2024",
    "DE_BEG_EM_HP_EFFICIENCY_2024",
}


class TestCumulationSolverGuard:
    """§5.4: the subset enumeration is exponential and must refuse to run away."""

    def test_too_many_eligible_schemes_is_a_data_error(self):
        """More eligible schemes than the limit raises instead of enumerating 2^n subsets."""
        count = CumulationLimits.MAX_CUMULATION_SCHEMES + 1
        catalog = make_catalog(make_scheme(f"TEST_ELIGIBLE_{index:02d}", ALWAYS_ELIGIBLE) for index in range(count))
        limit_message = f"cumulation solver limit of {CumulationLimits.MAX_CUMULATION_SCHEMES}"
        with pytest.raises(SubsidyDataError, match=limit_message):
            solve_cumulation(catalog, make_measure(), full_context(), 2024, DISCOUNT)

    def test_too_many_optimistic_schemes_is_a_data_error(self):
        """The optimistic-bound enumeration is capped by the same limit."""
        schemes = [make_scheme("TEST_ELIGIBLE_00", ALWAYS_ELIGIBLE)]
        schemes += [
            make_scheme(f"TEST_UNDETERMINED_{index:02d}", NEEDS_HOUSEHOLD_SIZE)
            for index in range(CumulationLimits.MAX_CUMULATION_SCHEMES)
        ]
        limit_message = f"cumulation solver limit of {CumulationLimits.MAX_CUMULATION_SCHEMES}"
        with pytest.raises(SubsidyDataError, match=limit_message):
            solve_cumulation(make_catalog(schemes), make_measure(), full_context(), 2024, DISCOUNT)

    def test_small_scheme_sets_still_solve(self):
        """A catalog inside the limit is unaffected by the guard."""
        catalog = make_catalog(make_scheme(f"TEST_ELIGIBLE_{index:02d}", ALWAYS_ELIGIBLE) for index in range(4))
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(), 2024, DISCOUNT)
        assert {award.scheme_id for award in decision.applied} == {f"TEST_ELIGIBLE_{index:02d}" for index in range(4)}


class TestSubsidyEngine:
    """§5 scheme mechanics."""

    def test_beg_stacking_capped_at_70_percent(self, catalog):
        """Base 30 + speed 20 + income 30 caps at 70 % of the eligible cost."""
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(), 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.best_estimate for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 20000.0)

    def test_eligible_cost_cap_binds(self, catalog):
        """40 kEUR cost, cap 30 kEUR first unit: support = 70 % of 30 kEUR."""
        decision = solve_cumulation(catalog, make_measure(cost=40000.0), full_context(), 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.best_estimate for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 30000.0)
        assert any(award.caps_binding_per_slot.get("best_estimate") for award in decision.applied)

    def test_35c_excluded_by_beg(self, catalog):
        """§35c EStG never stacks with BEG grants."""
        decision = solve_cumulation(catalog, make_measure(), full_context(), 2024, DISCOUNT)
        applied_ids = {award.scheme_id for award in decision.applied}
        assert "DE_TAX_35C_2024" not in applied_ids or not applied_ids & {
            "DE_BEG_EM_HP_BASE_2024",
            "DE_BEG_EM_HP_SPEED_2024",
        }

    def test_income_bonus_needs_low_income(self, catalog):
        """High income drops the income bonus."""
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(income=80000.0), 2024, DISCOUNT)
        applied_ids = {award.scheme_id for award in decision.applied}
        assert "DE_BEG_EM_HP_INCOME_2024" not in applied_ids
        upfront = sum(award.upfront_amount.best_estimate for award in decision.applied)
        # What is left without the income bonus: base 30 % + speed 20 % + efficiency 5 % = 55 %,
        # comfortably under the 70 % group cap, so nothing else binds.
        assert upfront == pytest.approx((0.30 + 0.20 + 0.05) * 20000.0)

    def test_residential_share_proration(self, catalog):
        """Mixed use: only the residential share of the cost basis is eligible (§5.2)."""
        context = full_context()
        context.building.commercial_floor_area_in_m2 = 50.0  # share 0.75
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), context, 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.best_estimate for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 20000.0 * 0.75)

    def test_heritage_relaxes_scop_threshold(self, catalog):
        """SCOP 2.8 fails normally but passes for a protected building (§5.2 example)."""
        measure = make_measure(scop=2.8, refrigerant="R32")
        context = full_context()
        decision = solve_cumulation(catalog, measure, context, 2024, DISCOUNT)
        assert "DE_BEG_EM_HP_BASE_2024" in {reject["scheme_id"] for reject in decision.rejected}
        context.building.heritage_status = HeritageStatus.LISTED_MONUMENT
        decision = solve_cumulation(catalog, measure, context, 2024, DISCOUNT)
        assert "DE_BEG_EM_HP_BASE_2024" in {award.scheme_id for award in decision.applied}

    def test_tristate_reports_undetermined_upper_bound(self, catalog):
        """Unknown income makes schemes UNDETERMINED, with an optimistic upper bound (§5.7)."""
        context = full_context()
        context.applicant.taxable_household_income_in_euro = None
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), context, 2024, DISCOUNT)
        undetermined_ids = {item["scheme_id"] for item in decision.undetermined}
        assert "DE_BEG_EM_HP_INCOME_2024" in undetermined_ids
        assert decision.undetermined_upper_bound_in_euro > 0

    def test_questionnaire_is_minimal_and_ordered(self, catalog):
        """Only unanswered fields are asked, ordered by pruning power, with asked-because."""
        context = SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.OWNER_OCCUPIER, main_residence=True),
            building=SubsidyBuildingContext(construction_year=1985, dwelling_units=1),
        )
        questions = required_questions(catalog, [make_measure()], context, 2024)
        fields = [question.entry.fieldname for question in questions]
        assert "building.construction_year" not in fields  # already known
        assert "applicant.taxable_household_income_in_euro" in fields
        powers = [question.pruning_power_in_euro for question in questions]
        assert powers == sorted(powers, reverse=True)
        for question in questions:
            assert question.asked_because

    def test_tax_credit_schedule_shares(self, catalog):
        """§35c pays 35/35/30 over three years when BEG is not taken."""
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = next(scheme for scheme in catalog.schemes if scheme.id == "DE_TAX_35C_2024")
        awards = _combination_awards([scheme], make_measure(cost=10000.0), full_context(), None)
        schedule = awards[0].schedule_amounts
        # 20 % of 10,000 EUR = 2,000 EUR, paid out 35 % / 35 % / 30 % over three tax years.
        assert [amount.best_estimate for amount in schedule] == pytest.approx([700.0, 700.0, 600.0])


class TestAwardsRecordTheArithmeticBehindTheirAmount:
    """§5.4 Q26 F8: the factors an award states must be the ones that produced its euros.

    The report's award caption (`views.award_arithmetic`) prints "rate x eligible basis = amount"
    straight from three fields the solver writes — it re-derives nothing, so a rate or a basis
    recorded next to the wrong amount reaches the reader as a multiplication whose result is not
    the number beside it, and nothing downstream would notice. The solver is therefore the only
    place the identity can be checked, and these tests check it against the real catalog.
    """

    def test_every_share_award_records_the_factors_that_multiply_to_its_amount(self, catalog):
        """Every applied share award satisfies `basis x rate = amount`, in all three slots.

        Catches a recorded rate or basis that does not belong to the amount beside it: a rate
        stored before the group cap scaled it, a basis stored before proration or before the
        per-dwelling-unit ceiling clamped it. The cost band is deliberately non-degenerate (and
        wide enough that the 30 kEUR ceiling bites in the HIGH world only), because a bug that
        records the *unclamped* basis is invisible while every slot holds the same number.
        """
        measure = make_measure(cost=20000.0)
        measure.cost_by_category[CostCategory.INVESTMENT] = UncertainValue(
            best_estimate=20000.0, minimum=14000.0, maximum=35000.0
        )
        decision = solve_cumulation(catalog, measure, full_context(), 2024, DISCOUNT)
        benefit_kinds = {scheme.id: scheme.benefit_kind for scheme in catalog.schemes}
        share_awards = [
            award
            for award in decision.applied
            if benefit_kinds[award.scheme_id] in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE)
        ]
        assert share_awards, "no share award was applied — the loop below would prove nothing"
        for award in share_awards:
            assert award.benefit_rate is not None, award.scheme_id
            assert award.eligible_basis_in_euro is not None, award.scheme_id
            expected = award.eligible_basis_in_euro.scale(award.benefit_rate)
            for slot in Slot:
                assert award.upfront_amount.slot(slot) == pytest.approx(expected.slot(slot)), (
                    f"{award.scheme_id} in slot {slot.value}"
                )

    def test_a_group_cap_scales_the_recorded_rate_and_keeps_the_scheme_rate_beside_it(self, catalog):
        """Under the 70 % cap each award reports the applied rate and the scheme's own rate.

        The bonuses stack past the BEG's combined-rate cap (30 + 20 + 30 %), so every award in the
        group is scaled back by the same factor. Catches the two ways the pair can lie: reporting
        the catalog rate as the one that produced the euros (the caption would then multiply to
        more than was awarded), and claiming a scale-down where none happened — checked on a
        second, uncapped solve, where the pre-cap field must stay empty.
        """
        scheme_rates = {
            scheme.id: scheme.benefit.rate
            for scheme in catalog.schemes
            if isinstance(scheme.benefit, ShareBenefit)
        }
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(), 2024, DISCOUNT)
        scaled = [award for award in decision.applied if award.benefit_rate_before_group_cap is not None]
        assert {award.scheme_id for award in scaled} == {
            "DE_BEG_EM_HP_BASE_2024",
            "DE_BEG_EM_HP_SPEED_2024",
            "DE_BEG_EM_HP_INCOME_2024",
        }
        scale_down = 0.70 / sum(scheme_rates[award.scheme_id] for award in scaled)
        for award in scaled:
            scheme_rate = award.benefit_rate_before_group_cap
            assert scheme_rate is not None and award.benefit_rate is not None
            assert award.eligible_basis_in_euro is not None
            assert scheme_rate == pytest.approx(scheme_rates[award.scheme_id])
            assert award.benefit_rate < scheme_rate
            assert award.benefit_rate / scheme_rate == pytest.approx(scale_down)
            # The identity of the test above still has to hold, with the *scaled* rate.
            assert award.upfront_amount.best_estimate == pytest.approx(
                award.eligible_basis_in_euro.best_estimate * award.benefit_rate
            )
        # Without the income bonus the stack is 30 + 20 + 5 %, under the cap: nothing is scaled.
        uncapped = solve_cumulation(
            catalog, make_measure(cost=20000.0), full_context(income=80000.0), 2024, DISCOUNT
        )
        assert uncapped.applied
        for award in uncapped.applied:
            assert award.benefit_rate_before_group_cap is None, award.scheme_id
            assert award.benefit_rate == pytest.approx(scheme_rates[award.scheme_id])

    def test_a_tax_credit_schedule_sums_to_rate_times_basis(self, catalog):
        """§35c states the same multiplication, and its instalments add up to it.

        A tax credit pays over three years, so its amount lives in `schedule_amounts` rather than
        in `upfront_amount`; the caption still prints "rate x basis". Catches a schedule whose
        instalments no longer sum to the product the caption shows — a split rebased on something
        other than the recorded basis, or a rate recorded that the schedule was not built from.
        """
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = next(scheme for scheme in catalog.schemes if scheme.id == "DE_TAX_35C_2024")
        award = _combination_awards([scheme], make_measure(cost=10000.0), full_context(), None)[0]
        assert award.benefit_rate is not None and award.eligible_basis_in_euro is not None
        assert sum(amount.best_estimate for amount in award.schedule_amounts) == pytest.approx(
            award.eligible_basis_in_euro.best_estimate * award.benefit_rate
        )


class TestTheOverallCapKeepsTheAwardArithmeticTrue:
    """Q26 F8 / §7 B12: the state-aid ceiling moves an award's recorded rate with its amount.

    The overall cap rescales `upfront_amount` and used to leave `benefit_rate` and
    `eligible_basis_in_euro` exactly as the share stage had written them, so wherever the ceiling
    bound, `views.award_arithmetic` printed a multiplication whose product was not the amount
    beside it — "50.0 % x 20,000 EUR = 5,000 EUR". The cap is the second of the two ceilings that
    can cut a rate down, and like the first it now states both figures.

    No shipped catalog is affected: DE and AT both declare `overall_cap_share: null`, which is why
    these cases are synthetic.
    """

    def test_a_capped_share_award_states_the_rate_that_produced_its_euros(self):
        """The ceiling halves the grant, so the recorded rate halves with it.

        A 50 % grant on an exact 20 000 EUR measure under a 25 % state-aid ceiling: 10 000 EUR of
        support is cut to 5 000. The pre-cap rate is kept beside the effective one, and the
        effective one is what multiplies the basis back to the amount — which is exactly the
        identity `TestAwardsRecordTheArithmeticBehindTheirAmount` checks everywhere else.
        """
        measure = banded_measure(UncertainValue.exact(20000.0))
        catalog = make_catalog(
            [make_scheme("CAP_SHARE", ALWAYS_ELIGIBLE, benefit=ShareBenefit(rate=0.5))],
            overall_cap_share=0.25,
        )
        decision = solve_cumulation(catalog, measure, SubsidyContext(), 2024, DISCOUNT)
        award = next(award for award in decision.applied if award.scheme_id == "CAP_SHARE")
        # The cap really bound — without this the assertions below would pass vacuously.
        assert award.upfront_amount.best_estimate == pytest.approx(0.25 * 20000.0)
        assert award.benefit_rate_before_overall_cap == pytest.approx(0.5)
        assert award.benefit_rate is not None and award.eligible_basis_in_euro is not None
        assert award.upfront_amount.best_estimate == pytest.approx(
            award.eligible_basis_in_euro.best_estimate * award.benefit_rate
        )
        assert award.benefit_rate == pytest.approx(0.25)
        # The group cap is a different ceiling and did not bite here, so it stays unclaimed.
        assert award.benefit_rate_before_group_cap is None

    def test_an_uncapped_solve_leaves_the_overall_cap_field_empty(self):
        """The control: claiming a cut that did not happen is the other way the pair can lie."""
        measure = banded_measure(UncertainValue.exact(20000.0))
        catalog = make_catalog(
            [make_scheme("CAP_SHARE", ALWAYS_ELIGIBLE, benefit=ShareBenefit(rate=0.5))],
            overall_cap_share=0.8,
        )
        decision = solve_cumulation(catalog, measure, SubsidyContext(), 2024, DISCOUNT)
        award = next(award for award in decision.applied if award.scheme_id == "CAP_SHARE")
        assert award.benefit_rate_before_overall_cap is None
        assert award.benefit_rate == pytest.approx(0.5)
        assert award.upfront_amount.best_estimate == pytest.approx(0.5 * 20000.0)

    def test_a_tax_credit_keeps_its_rate_because_the_cap_never_touched_its_schedule(self):
        """The cap bounds *upfront* support only, so a schedule's arithmetic is already true.

        A tax credit's amounts live in `schedule_amounts`; its `upfront_amount` is zero and scaling
        zero leaves zero. Rewriting its rate to the cap ratio — the naive "every award that has a
        rate" reading — would therefore introduce the very falsehood the fix removes: instalments
        that no longer sum to the rate times the basis the caption prints beside them.
        """
        measure = banded_measure(UncertainValue.exact(20000.0))
        catalog = make_catalog(
            [
                make_scheme("CAP_SHARE", ALWAYS_ELIGIBLE, benefit=ShareBenefit(rate=0.5)),
                make_scheme(
                    "CAP_CREDIT",
                    ALWAYS_ELIGIBLE,
                    benefit_kind=BenefitKind.TAX_CREDIT,
                    benefit=TaxCreditBenefit(rate=0.2, years=3),
                ),
            ],
            overall_cap_share=0.25,
        )
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        awards = {
            award.scheme_id: award
            for award in _combination_awards(catalog.schemes, measure, SubsidyContext(), 0.25)
        }
        assert awards["CAP_SHARE"].benefit_rate_before_overall_cap == pytest.approx(0.5)
        credit = awards["CAP_CREDIT"]
        assert credit.benefit_rate_before_overall_cap is None
        assert credit.benefit_rate is not None and credit.benefit_rate == pytest.approx(0.2)
        assert credit.eligible_basis_in_euro is not None
        assert sum(amount.best_estimate for amount in credit.schedule_amounts) == pytest.approx(
            credit.eligible_basis_in_euro.best_estimate * credit.benefit_rate
        )


class TestOverallCapIsAppliedPerSlot:
    """§7 B12: the EU state-aid overall cap binds in every slot, not only in the best-estimate slot."""

    def test_cap_binds_in_every_slot_with_a_wide_cost_band(self):
        """Wide band, share + lump sum: the BEST_ESTIMATE-ratio scale-down overran LOW badly.

        Gross (2 000 / 20 000 / 30 000), cap share 0.8, a 50 % grant and an 8 000 EUR lump sum:
        the old single ratio (16 000/18 000) left 2 667 EUR of support in the LOW world — more
        than that world's entire 2 000 EUR gross cost.
        """
        investment = UncertainValue(best_estimate=20000.0, minimum=2000.0, maximum=30000.0)
        measure = banded_measure(investment)
        catalog = make_catalog(
            [
                make_scheme("CAP_SHARE", ALWAYS_ELIGIBLE, benefit=ShareBenefit(rate=0.5)),
                make_scheme(
                    "CAP_LUMP",
                    ALWAYS_ELIGIBLE,
                    benefit_kind=BenefitKind.LUMP_SUM,
                    benefit=LumpSumBenefit(amount=8000.0),
                ),
            ],
            overall_cap_share=0.8,
        )
        decision = solve_cumulation(catalog, measure, SubsidyContext(), 2024, DISCOUNT)
        total = UncertainValue.sum(award.upfront_amount for award in decision.applied)
        for slot, gross in (("minimum", 2000.0), ("best_estimate", 20000.0), ("maximum", 30000.0)):
            assert getattr(total, slot) <= 0.8 * gross + 1e-6
        assert total.minimum == pytest.approx(0.8 * 2000.0)  # the cap binds exactly in LOW

    def test_non_monotone_cap_ratios_keep_the_award_bands_ordered(self):
        """A statutory lump sum plus a wide-band grant makes the per-slot ratio *fall*.

        Gross (700 / 1 600 / 1 800) with an exact 600 EUR planning cost, a 100 % grant on the
        investment only and a 500 EUR lump sum: the raw per-slot ratios are 0.933 / 0.853 /
        0.847, so a plain slot-wise product would give the lump sum (466.7, 426.7, 423.5) — a
        band with minimum > best_estimate, which is not a representable `UncertainValue` at all.
        """
        investment = UncertainValue(best_estimate=1000.0, minimum=100.0, maximum=1200.0)
        measure = banded_measure(investment, planning=600.0)
        catalog = make_catalog(
            [
                make_scheme(
                    "CAP_SHARE",
                    ALWAYS_ELIGIBLE,
                    benefit=ShareBenefit(rate=1.0),
                    eligible_cost=EligibleCostSpec(categories=[CostCategory.INVESTMENT]),
                ),
                make_scheme(
                    "CAP_LUMP",
                    ALWAYS_ELIGIBLE,
                    benefit_kind=BenefitKind.LUMP_SUM,
                    benefit=LumpSumBenefit(amount=500.0),
                    eligible_cost=EligibleCostSpec(categories=[CostCategory.INVESTMENT, CostCategory.PLANNING]),
                ),
            ],
            overall_cap_share=0.8,
        )
        decision = solve_cumulation(catalog, measure, SubsidyContext(), 2024, DISCOUNT)
        awards = {award.scheme_id: award.upfront_amount for award in decision.applied}
        assert set(awards) == {"CAP_SHARE", "CAP_LUMP"}
        for amount in awards.values():  # UncertainValue enforces this, state it anyway
            assert amount.minimum <= amount.best_estimate <= amount.maximum
        # The lump sum is cut to the tightest (HIGH-slot) ratio in all three worlds:
        assert awards["CAP_LUMP"].minimum == pytest.approx(awards["CAP_LUMP"].maximum)
        total = UncertainValue.sum(awards.values())
        for slot, gross in (("minimum", 700.0), ("best_estimate", 1600.0), ("maximum", 1800.0)):
            assert getattr(total, slot) <= 0.8 * gross + 1e-6
        assert total.maximum == pytest.approx(0.8 * 1800.0)  # the HIGH slot uses the cap fully

    def test_degenerate_bands_are_unchanged_by_the_per_slot_cap(self):
        """Every shipped catalog is degenerate: the fix must be a no-op there."""
        measure = banded_measure(UncertainValue.exact(20000.0))
        catalog = make_catalog(
            [make_scheme("CAP_SHARE", ALWAYS_ELIGIBLE, benefit=ShareBenefit(rate=0.9))],
            overall_cap_share=0.5,
        )
        decision = solve_cumulation(catalog, measure, SubsidyContext(), 2024, DISCOUNT)
        award = decision.applied[0].upfront_amount
        assert award.is_exact() and award.best_estimate == pytest.approx(0.5 * 20000.0)


class TestSubsidyModeFiltersBeforeTheSolve:
    """§7 B5: the admitted-scheme filter is part of the optimization, not a post-filter."""

    def test_exclude_mode_gets_the_best_admissible_combination(self, catalog):
        """Excluding the BEG grants must yield the §35c tax credit, not the BEG leftovers.

        Filtering after the solve produced nothing here: the solver picked the BEG stack (best
        without a filter), which excludes §35c, and the post-filter then dropped every BEG award.
        """
        decision = solve_cumulation(
            catalog,
            make_measure(cost=20000.0),
            full_context(),
            2024,
            DISCOUNT,
            admits=lambda scheme_id: scheme_id not in BEG_HP_SCHEMES,
        )
        assert "DE_TAX_35C_2024" in {award.scheme_id for award in decision.applied}
        assert sum(
            amount.best_estimate for award in decision.applied for amount in award.schedule_amounts
        ) == pytest.approx(0.2 * 20000.0)

    def test_only_mode_awards_the_named_scheme(self, catalog):
        """ONLY(speed bonus) awards the speed bonus alone, uncapped by its group partners."""
        decision = solve_cumulation(
            catalog,
            make_measure(cost=20000.0),
            full_context(),
            2024,
            DISCOUNT,
            admits=lambda scheme_id: scheme_id == "DE_BEG_EM_HP_SPEED_2024",
        )
        assert {award.scheme_id for award in decision.applied} == {"DE_BEG_EM_HP_SPEED_2024"}
        assert sum(award.upfront_amount.best_estimate for award in decision.applied) == pytest.approx(0.2 * 20000.0)

    def test_non_admitted_schemes_are_not_reported_at_all(self, catalog):
        """Rejections, undetermined schemes and the bound follow the same filter."""
        context = full_context()
        context.applicant.taxable_household_income_in_euro = None  # income bonus undetermined
        decision = solve_cumulation(
            catalog,
            make_measure(cost=20000.0),
            context,
            2024,
            DISCOUNT,
            admits=lambda scheme_id: scheme_id not in BEG_HP_SCHEMES,
        )
        reported = (
            {award.scheme_id for award in decision.applied}
            | {item["scheme_id"] for item in decision.rejected}
            | {item["scheme_id"] for item in decision.undetermined}
        )
        assert not reported & BEG_HP_SCHEMES

    def test_none_mode_solves_to_nothing(self, catalog):
        """A perspective without subsidies sees no scheme, not a filtered award list."""
        decision = solve_cumulation(
            catalog, make_measure(), full_context(), 2024, DISCOUNT, admits=lambda scheme_id: False
        )
        assert decision.applied == [] and decision.rejected == [] and decision.undetermined == []
        assert decision.undetermined_upper_bound_in_euro == 0.0

    def test_questions_follow_the_same_filter(self, catalog):
        """No question is asked for a scheme the perspective would never award (§5.7)."""
        context = SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.OWNER_OCCUPIER, main_residence=True),
            building=SubsidyBuildingContext(construction_year=1985, dwelling_units=1),
        )
        unfiltered = required_questions(catalog, [make_measure()], context, 2024)
        filtered = required_questions(
            catalog, [make_measure()], context, 2024, admits=lambda scheme_id: False
        )
        assert unfiltered and not filtered
