"""Unit tests for the subsidy engine (§5), the tariff engine (§8) and scenario data overlays (§4.6).

**Scope, and what is deliberately not here.** Every test in this file needs nothing above the
rule engines themselves — the subsidy engine, the tariff engine and the cost database. The cases
that drive the same rules *through* the evaluator live in `test_economics_subsidy_integration.py`,
because the evaluator sits a layer above and would otherwise make this file untestable until that
layer exists. The split keeps each stage of the review stack self-testing; a test added here that
imports *any* module above the rule engines — `evaluator`, `perspectives`, `scenarios`,
`financing`, `results`, `views` — belongs in the sibling file instead, however small it is.

**Surface.** Three subsystems that share one property: they are driven entirely by data, and
their failure mode is a plausible-looking number rather than a crash. The subsidy engine covers
the condition AST and its tri-state evaluation, typed benefit payloads, eligible-cost caps and
residential-share proration, the cumulation solver with its exponential guard and its EU
state-aid overall cap, the questionnaire, and the provenance a catalog and its schemes carry. The
tariff engine covers the pure year-1 billing function `apply_tariff` across supply kinds, capacity
charges, feed-in and the §8.5 decomposition. `TestScenarioDataOverlays` covers the §4.6 overlay
surface of the cost database; set expansion, the evaluation cube and the break-even search are in
the sibling file.

**How it covers it.** Mostly against *synthetic* catalogs and contracts built in the file, with
round numbers, so an expected value is a percentage a reviewer can check by eye (0.70 x 20 000,
0.8 x 1 800). Where the shipped DE catalog is used it is used on purpose — the stacking, cap and
exclusion behavior of the real BEG/§35c schemes is itself under test — and always with a stated
cost, never a database-resolved one. The overall-cap tests deliberately use *wide* bands, because
every shipped catalog is degenerate and the per-slot defect (B12) is invisible without one; each
of those tests states its hand derivation in its own docstring. Data-file breakage is provoked by
writing single-scheme catalogs into `tmp_path` and asserting the load error names the scheme and
the key.

**Error class.** A failure in the subsidy or tariff sections is a *formula* or *catalog-parsing*
failure, isolated from pricing data by the synthetic fixtures. A failure in
`TestSubsidyProvenance` is neither — it means a number can no longer be traced to its source,
which is a §3.10 auditability defect rather than a wrong result. The B-numbered classes here name
real historical defects and are worth following into roadmap/cost-spec-v2.md §7: B5 (subsidy-mode
filtering applied after the solve, so an EXCLUDE perspective received nothing at all), B6 (the
unguarded 2^n subset enumeration), and B12 (the state-aid cap scaled by the AVERAGE-slot ratio
only, which could leave the LOW world with more support than it had cost).
"""

# clean

import dataclasses
import json
import os

import pytest

from hisim.economics import subsidies
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    BenefitKind,
    Condition,
    CumulationLimits,
    EligibleCostSpec,
    HeritageStatus,
    LoanTermsBenefit,
    LumpSumBenefit,
    MeasureForSubsidy,
    PayoutKind,
    ShareBenefit,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyContextFields,
    SubsidyDataError,
    SubsidyScheme,
    TaxCreditBenefit,
    evaluate_condition,
    failed_condition_descriptions,
    ineligibility_reason,
    parse_condition,
    question_targets,
    required_questions,
    scheme_context_fields,
    solve_cumulation,
)
from hisim.economics.tariffs import (
    CapacityCharge,
    CapacityChargeKind,
    FeedIn,
    FeedInKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    apply_tariff,
    synthetic_reference_spot_series,
    tariff_counterfactual,
    validate_billing_interval,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: The discount function the cumulation solver uses to compare an upfront grant against a
#: multi-year tax credit — the solver optimizes present value, so it needs one. Taken from the
#: default parameters (3 %) rather than hand-written, so the tests compare schemes the same way a
#: real evaluation does.
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
    eligible_cost: EligibleCostSpec = None,
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


def make_catalog(schemes, overall_cap_share: float = None) -> SubsidyCatalog:
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


#: Always true - a condition tree with no children is satisfied by every context.
ALWAYS_ELIGIBLE = Condition(kind="all")
#: Never answered by full_context(), so the scheme stays UNDETERMINED.
NEEDS_HOUSEHOLD_SIZE = Condition(kind="leaf", fieldname="applicant.household_size", op=">=", value=1)


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
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 20000.0)

    def test_eligible_cost_cap_binds(self, catalog):
        """40 kEUR cost, cap 30 kEUR first unit: support = 70 % of 30 kEUR."""
        decision = solve_cumulation(catalog, make_measure(cost=40000.0), full_context(), 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 30000.0)
        assert any(award.caps_binding_per_slot.get("average") for award in decision.applied)

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
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        # What is left without the income bonus: base 30 % + speed 20 % + efficiency 5 % = 55 %,
        # comfortably under the 70 % group cap, so nothing else binds.
        assert upfront == pytest.approx((0.30 + 0.20 + 0.05) * 20000.0)

    def test_residential_share_proration(self, catalog):
        """Mixed use: only the residential share of the cost basis is eligible (§5.2)."""
        context = full_context()
        context.building.commercial_floor_area_in_m2 = 50.0  # share 0.75
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), context, 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.average for award in decision.applied)
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
        assert [amount.average for amount in schedule] == pytest.approx([700.0, 700.0, 600.0])


def write_catalog(tmp_path, benefit: dict, scheme_id: str = "TEST_SCHEME") -> str:
    """Writes a one-scheme catalog with the given benefit object and returns its base path.

    The benefit is the only variable part; everything else is a valid minimal scheme, so a load
    error can only come from the benefit under test and the assertion on the message is
    meaningful. This exercises the *file* path deliberately — W2.2 moved benefit typing to load
    time, so these malformed payloads must be rejected while parsing the catalog, not later
    inside the solver where the scheme id is no longer at hand.
    """
    catalog = {
        "catalog_snapshot_date": "2026-01-01",
        "overall_cap_share": None,
        "schemes": [
            {
                "id": scheme_id,
                "jurisdiction": {"country": "XX", "region": None},
                "valid_from": "2024-01-01",
                "valid_to": None,
                "legal_basis": "synthetic test catalog",
                "url": "https://example.invalid/catalog",
                "applies_to": {"asset_classes": ["HEAT_PUMP"], "measure_kinds": ["INSTALL"]},
                "eligibility": {"all": []},
                "benefit": benefit,
                "eligible_cost": {"categories": ["INVESTMENT"]},
                "cumulation": {"group": None, "combined_rate_cap": None, "excludes": []},
                "payout": {"kind": "UPFRONT_GRANT"},
            }
        ],
    }
    base = str(tmp_path)
    with open(os.path.join(base, "XX.json"), "w", encoding="utf-8") as file:
        json.dump(catalog, file)
    return base


class TestTypedBenefits:
    """W2.2: the catalog JSON is unchanged, but benefits are parsed into typed payloads."""

    def test_shipped_catalog_carries_typed_benefits(self, catalog):
        """Every shipped DE scheme holds the payload type its kind declares."""
        by_id = {scheme.id: scheme for scheme in catalog.schemes}
        assert isinstance(by_id["DE_BEG_EM_HP_BASE_2024"].benefit, ShareBenefit)
        assert by_id["DE_BEG_EM_HP_BASE_2024"].benefit.rate == pytest.approx(0.3)
        tax = by_id["DE_TAX_35C_2024"].benefit
        assert isinstance(tax, TaxCreditBenefit)
        assert tax.years == 3
        assert tax.annual_shares == (0.35, 0.35, 0.3)
        assert tax.schedule_shares() == (0.35, 0.35, 0.3)
        loan = by_id["DE_KFW_358_LOAN_2024"].benefit
        assert isinstance(loan, LoanTermsBenefit)
        assert (loan.interest_rate, loan.term, loan.repayment_grant_rate) == (0.02, 20, 0.0)

    def test_at_catalog_lump_sum_is_typed(self):
        """The shipped AT catalog parses too (LUMP_SUM)."""
        at_catalog = SubsidyCatalog.load("AT")
        benefit = at_catalog.schemes[0].benefit
        assert isinstance(benefit, LumpSumBenefit) and benefit.amount == pytest.approx(7500.0)

    def test_unknown_benefit_kind_is_rejected_at_load(self, tmp_path):
        """A typo in the kind names the scheme."""
        base = write_catalog(tmp_path, {"kind": "SHARE_OF_ELIGABLE_COST", "rate": 0.3})
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME: unknown benefit kind"):
            SubsidyCatalog.load("XX", base)

    def test_missing_benefit_key_is_rejected_at_load(self, tmp_path):
        """A missing mandatory parameter names the scheme and the key."""
        base = write_catalog(tmp_path, {"kind": "SHARE_OF_ELIGIBLE_COST"})
        with pytest.raises(SubsidyDataError, match=r"TEST_SCHEME: .*misses the mandatory key 'rate'"):
            SubsidyCatalog.load("XX", base)

    def test_unknown_benefit_key_is_rejected_at_load(self, tmp_path):
        """A misspelled parameter is caught at load instead of being silently ignored."""
        base = write_catalog(tmp_path, {"kind": "SHARE_OF_ELIGIBLE_COST", "rate": 0.3, "raet": 0.1})
        with pytest.raises(SubsidyDataError, match=r"TEST_SCHEME: .*unknown key\(s\) \['raet'\]"):
            SubsidyCatalog.load("XX", base)

    def test_unparsable_benefit_value_is_rejected_at_load(self, tmp_path):
        """A non-numeric rate fails at load, not as a TypeError inside the solver."""
        base = write_catalog(tmp_path, {"kind": "SHARE_OF_ELIGIBLE_COST", "rate": "thirty percent"})
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME: benefit key 'rate'"):
            SubsidyCatalog.load("XX", base)

    def test_annual_shares_must_sum_to_one(self, tmp_path):
        """The schedule check moved from solve time to load time."""
        base = write_catalog(
            tmp_path, {"kind": "TAX_CREDIT", "rate": 0.2, "years": 3, "annual_shares": [0.5, 0.4, 0.4]}
        )
        with pytest.raises(SubsidyDataError, match="annual_shares must sum to 1"):
            SubsidyCatalog.load("XX", base)

    def test_annual_shares_must_match_the_year_count(self, tmp_path):
        """Shares and years must agree."""
        base = write_catalog(
            tmp_path, {"kind": "TAX_CREDIT", "rate": 0.2, "years": 3, "annual_shares": [0.5, 0.5]}
        )
        with pytest.raises(SubsidyDataError, match="annual_shares for 3 years"):
            SubsidyCatalog.load("XX", base)

    def test_benefit_payload_must_match_the_kind(self):
        """An in-memory scheme cannot pair a kind with a foreign payload."""
        with pytest.raises(SubsidyDataError, match="needs a LumpSumBenefit"):
            SubsidyScheme(
                id="MISMATCH",
                country="DE",
                region=None,
                valid_from="1900-01-01",
                valid_to=None,
                legal_basis="synthetic",
                url="https://example.invalid/scheme",
                asset_classes=[ComponentType.HEAT_PUMP],
                measure_kinds=["INSTALL"],
                eligibility=Condition(kind="all"),
                benefit_kind=BenefitKind.LUMP_SUM,
                benefit=ShareBenefit(rate=0.3),
                eligible_cost=EligibleCostSpec(),
                cumulation_group=None,
                combined_rate_cap=None,
                excludes=[],
                payout_kind=PayoutKind.UPFRONT_GRANT,
            )

    def test_value_estimate_is_the_one_simplified_valuation(self):
        """`required_questions` and the typed payloads agree by construction (§5.7)."""
        assert ShareBenefit(rate=0.3).value_estimate(20000.0, 10.0) == pytest.approx(6000.0)
        assert LumpSumBenefit(amount=7500.0).value_estimate(20000.0, 10.0) == pytest.approx(7500.0)
        assert TaxCreditBenefit(rate=0.2, years=3).value_estimate(20000.0, 10.0) == pytest.approx(4000.0)
        assert LoanTermsBenefit(interest_rate=0.02, term=20).value_estimate(20000.0, 10.0) == 0.0


class TestConditionAstAndFieldVocabulary:
    """W2.3: the condition is inert data, the evaluator is engine, the vocabulary is derived."""

    def test_condition_is_an_inert_frozen_ast(self):
        """The catalog payload carries no behavior and cannot be mutated after parsing."""
        condition = parse_condition({"field": "building.dwelling_units", "op": ">=", "value": 1}, "TEST")
        assert not hasattr(condition, "evaluate") and not hasattr(condition, "parse")
        with pytest.raises(dataclasses.FrozenInstanceError):
            condition.value = 2

    def test_parse_rejects_unknown_fields_and_ops(self):
        """Both grammar errors name the scheme."""
        with pytest.raises(SubsidyDataError, match="TEST references unknown field"):
            parse_condition({"field": "building.dwelling_unitz", "op": "==", "value": 1}, "TEST")
        with pytest.raises(SubsidyDataError, match="TEST: unknown op"):
            parse_condition({"field": "building.dwelling_units", "op": "=~", "value": 1}, "TEST")

    def test_evaluation_semantics_are_tri_state(self):
        """Leaf/all/any/not semantics, unchanged by the split (§5.7)."""
        context = SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.LANDLORD, taxable_household_income_in_euro=None),
            building=SubsidyBuildingContext(construction_year=1985),
        )
        landlord = parse_condition({"field": "applicant.actor", "op": "==", "value": "LANDLORD"}, "T")
        income = parse_condition({"field": "applicant.taxable_household_income_in_euro", "op": "<=", "value": 1}, "T")
        assert evaluate_condition(landlord, context, None) == (True, [])
        assert evaluate_condition(income, context, None) == (None, ["applicant.taxable_household_income_in_euro"])
        assert evaluate_condition(Condition(kind="not", children=(landlord,)), context, None)[0] is False
        assert evaluate_condition(Condition(kind="all", children=(landlord, income)), context, None)[0] is None
        assert evaluate_condition(Condition(kind="any", children=(landlord, income)), context, None)[0] is True
        old = parse_condition({"field": "building.construction_year", "op": "<=", "value": 1900}, "T")
        assert evaluate_condition(Condition(kind="all", children=(old, income)), context, None) == (False, [])

    def test_vocabulary_is_derived_from_the_context_dataclasses(self):
        """Every context dataclass field is addressable — no hand-maintained whitelist."""
        for context_field in dataclasses.fields(ApplicantProfile):
            assert f"applicant.{context_field.name}" in SubsidyContextFields.KNOWN_CONTEXT_FIELDS
        for context_field in dataclasses.fields(SubsidyBuildingContext):
            assert f"building.{context_field.name}" in SubsidyContextFields.KNOWN_CONTEXT_FIELDS
        # one nested level (the existing heating asset) and the derived property:
        assert "building.existing_heating.energy_carrier" in SubsidyContextFields.KNOWN_CONTEXT_FIELDS
        assert "building.residential_share" in SubsidyContextFields.KNOWN_CONTEXT_FIELDS

    def test_a_new_context_field_cannot_be_forgotten(self, monkeypatch):
        """Adding a field to a context dataclass adds it to the vocabulary automatically."""

        @dataclasses.dataclass
        class ExtendedProfile(ApplicantProfile):
            """A context dataclass that grew a field after the whitelist was written."""

            newly_added_flag: bool = False

        monkeypatch.setitem(subsidies.SubsidyContextFields.CONTEXT_ROOTS, "applicant", ExtendedProfile)
        assert "applicant.newly_added_flag" in subsidies._enumerate_context_fields()

    def test_derived_fields_map_to_their_questions(self):
        """The derived-field registry replaces the special cases in engine and validation."""
        assert question_targets("building.residential_share") == (
            "building.residential_floor_area_in_m2",
            "building.commercial_floor_area_in_m2",
        )
        assert question_targets("building.dwelling_units") == ("building.dwelling_units",)

    def test_scheme_context_fields_include_the_implied_ones(self, catalog):
        """Proration and per-unit caps imply context fields no condition mentions."""
        scheme = next(item for item in catalog.schemes if item.id == "DE_BEG_EM_HP_BASE_2024")
        fields = scheme_context_fields(scheme)
        assert "building.residential_share" in fields  # from proration
        assert "building.dwelling_units" in fields  # from the per-dwelling-unit cap
        assert "applicant.actor" in fields  # from the eligibility condition


class TestIneligibilityReasonsNameTheFailingCondition:
    """Issue #22: an INELIGIBLE scheme says *which* condition it failed, not merely that it did."""

    @staticmethod
    def _context() -> SubsidyContext:
        """A 2010 single-family building owned by its occupier — young enough to fail an age test."""
        return SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.OWNER_OCCUPIER, main_residence=True),
            building=SubsidyBuildingContext(construction_year=2010, dwelling_units=1),
        )

    def test_a_single_failing_leaf_is_named_with_its_actual_value(self):
        """The reason quotes the comparison and the answer that failed it."""
        condition = parse_condition({"field": "building.construction_year", "op": "<=", "value": 2004}, "T")
        reason = ineligibility_reason(condition, self._context(), None)
        assert "building.construction_year <= 2004" in reason
        assert "actual: 2010" in reason
        assert reason.startswith("condition not met:")

    def test_every_failing_leaf_of_an_and_is_named(self):
        """Two unmet criteria are two lines of explanation: fixing one would not help."""
        condition = Condition(
            kind="all",
            children=(
                parse_condition({"field": "building.construction_year", "op": "<=", "value": 2004}, "T"),
                parse_condition({"field": "building.dwelling_units", "op": ">=", "value": 3}, "T"),
                parse_condition({"field": "applicant.main_residence", "op": "==", "value": True}, "T"),
            ),
        )
        descriptions = failed_condition_descriptions(condition, self._context(), None)
        assert len(descriptions) == 2  # the satisfied main-residence leaf explains nothing
        assert any("construction_year" in text and "actual: 2010" in text for text in descriptions)
        assert any("dwelling_units" in text and "actual: 1" in text for text in descriptions)

    def test_an_or_is_reported_as_a_group_and_a_not_honestly(self):
        """`any` fails as "none of", `not` fails because its child does hold (§5.3 shapes)."""
        alternatives = Condition(
            kind="any",
            children=(
                parse_condition({"field": "building.dwelling_units", "op": ">=", "value": 3}, "T"),
                parse_condition({"field": "building.construction_year", "op": "<=", "value": 2004}, "T"),
            ),
        )
        [description] = failed_condition_descriptions(alternatives, self._context(), None)
        assert description.startswith("none of:")
        assert "dwelling_units" in description and "construction_year" in description
        negated = Condition(
            kind="not",
            children=(parse_condition({"field": "applicant.main_residence", "op": "==", "value": True}, "T"),),
        )
        [negated_description] = failed_condition_descriptions(negated, self._context(), None)
        assert negated_description.startswith("must not hold, but does:")
        assert "applicant.main_residence" in negated_description

    def test_the_shipped_catalog_rejects_with_a_named_condition(self, catalog):
        """End to end: a real rejection in a real decision record explains itself."""
        measure = make_measure(scop=2.8, refrigerant="R32")  # below the technical minimum
        decision = solve_cumulation(catalog, measure, full_context(), 2024, DISCOUNT)
        reason = next(
            reject["reason"]
            for reject in decision.rejected
            if reject["scheme_id"] == "DE_BEG_EM_HP_BASE_2024"
        )
        assert reason != "failed eligibility condition"
        assert "measure.technical_attributes.scop" in reason
        assert "actual: 2.8" in reason

    def test_an_undetermined_scheme_still_carries_no_reason(self):
        """Only definite failures explain themselves; an unanswered field is not a failure (§5.7)."""
        context = self._context()
        context.applicant.taxable_household_income_in_euro = None
        condition = parse_condition(
            {"field": "applicant.taxable_household_income_in_euro", "op": "<=", "value": 40000}, "T"
        )
        assert failed_condition_descriptions(condition, context, None) == []


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


class TestOverallCapIsAppliedPerSlot:
    """§7 B12: the EU state-aid overall cap binds in every slot, not only on average."""

    def test_cap_binds_in_every_slot_with_a_wide_cost_band(self):
        """Wide band, share + lump sum: the AVERAGE-ratio scale-down overran LOW badly.

        Gross (2 000 / 20 000 / 30 000), cap share 0.8, a 50 % grant and an 8 000 EUR lump sum:
        the old single ratio (16 000/18 000) left 2 667 EUR of support in the LOW world — more
        than that world's entire 2 000 EUR gross cost.
        """
        investment = UncertainValue(average=20000.0, minimum=2000.0, maximum=30000.0)
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
        for slot, gross in (("minimum", 2000.0), ("average", 20000.0), ("maximum", 30000.0)):
            assert getattr(total, slot) <= 0.8 * gross + 1e-6
        assert total.minimum == pytest.approx(0.8 * 2000.0)  # the cap binds exactly in LOW

    def test_non_monotone_cap_ratios_keep_the_award_bands_ordered(self):
        """A statutory lump sum plus a wide-band grant makes the per-slot ratio *fall*.

        Gross (700 / 1 600 / 1 800) with an exact 600 EUR planning cost, a 100 % grant on the
        investment only and a 500 EUR lump sum: the raw per-slot ratios are 0.933 / 0.853 /
        0.847, so a plain slot-wise product would give the lump sum (466.7, 426.7, 423.5) — a
        band with minimum > average, which is not a representable `UncertainValue` at all.
        """
        investment = UncertainValue(average=1000.0, minimum=100.0, maximum=1200.0)
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
            assert amount.minimum <= amount.average <= amount.maximum
        # The lump sum is cut to the tightest (HIGH-slot) ratio in all three worlds:
        assert awards["CAP_LUMP"].minimum == pytest.approx(awards["CAP_LUMP"].maximum)
        total = UncertainValue.sum(awards.values())
        for slot, gross in (("minimum", 700.0), ("average", 1600.0), ("maximum", 1800.0)):
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
        assert award.is_exact() and award.average == pytest.approx(0.5 * 20000.0)


#: The four BEG EM heat-pump schemes (base + speed, income and efficiency bonuses). Named as a set
#: because the B5 tests need to admit or exclude precisely this group: excluding it is what forces
#: the solver onto the §35c tax credit, which the BEG stack otherwise excludes.
BEG_HP_SCHEMES = {
    "DE_BEG_EM_HP_BASE_2024",
    "DE_BEG_EM_HP_SPEED_2024",
    "DE_BEG_EM_HP_INCOME_2024",
    "DE_BEG_EM_HP_EFFICIENCY_2024",
}


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
            amount.average for award in decision.applied for amount in award.schedule_amounts
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
        assert sum(award.upfront_amount.average for award in decision.applied) == pytest.approx(0.2 * 20000.0)

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
        assert unfiltered and filtered == []


class TestSubsidyProvenance:
    """W2.4: subsidy-derived cash flows carry real provenance, never `inline:` pseudo-sources."""

    def test_catalog_keeps_its_resolved_sources(self, catalog):
        """`load` used to throw the registry resolution away; now the catalog holds it."""
        assert "src_beg_em_2023" in catalog.sources
        scheme = catalog.scheme_by_id("DE_BEG_EM_HP_BASE_2024")
        assert [entry.source_id for entry in catalog.resolved_sources(scheme)] == ["src_beg_em_2023"]
        assert catalog.source_resolver()["src_beg_em_2023"].citation

    def test_catalog_scheme_records_registry_ids(self, catalog):
        """A scheme loaded from a catalog file cites its registry entries, plus its legal basis."""
        from hisim.economics.provenance import ParameterOrigin, ProvenanceLedger

        ledger = ProvenanceLedger()
        scheme = catalog.scheme_by_id("DE_BEG_EM_HP_BASE_2024")
        record = ledger.get(catalog.provenance_for_scheme(scheme, ledger, UncertainValue.exact(1000.0)))
        assert record.origin == ParameterOrigin.DATABASE_ENTRY
        assert record.source_ids == ("src_beg_em_2023",)
        assert not any(source_id.startswith("inline:") for source_id in record.source_ids)
        assert scheme.legal_basis in (record.detail or "") and scheme.url in (record.detail or "")

    def test_in_memory_scheme_gets_an_honest_origin(self):
        """Test/worked-example schemes have no registry — they say so instead of faking an id."""
        from hisim.economics.provenance import ParameterOrigin, ProvenanceLedger

        ledger = ProvenanceLedger()
        catalog = make_catalog([make_scheme("TEST_IN_MEMORY", ALWAYS_ELIGIBLE)])
        scheme = catalog.scheme_by_id("TEST_IN_MEMORY")
        record = ledger.get(catalog.provenance_for_scheme(scheme, ledger, UncertainValue.exact(5.0)))
        assert record.origin == ParameterOrigin.IN_MEMORY_DEFINITION
        assert record.source_ids == ()

    def test_catalog_schemes_must_cite_sources(self, tmp_path):
        """An unsourced scheme in a catalog *file* is a load error (§3.10)."""
        payload = {
            "catalog_snapshot_date": "2026-01-01",
            "schemes": [
                {
                    "id": "XX_NO_SOURCE",
                    "jurisdiction": {"country": "XX", "region": None},
                    "legal_basis": "test",
                    "url": "https://example.invalid/x",
                    "applies_to": {"asset_classes": ["HEAT_PUMP"]},
                    "benefit": {"kind": "SHARE_OF_ELIGIBLE_COST", "rate": 0.1},
                }
            ],
        }
        with open(os.path.join(str(tmp_path), "XX.json"), "w", encoding="utf-8") as file:
            json.dump(payload, file)
        with pytest.raises(SubsidyDataError, match="source_ids are mandatory"):
            SubsidyCatalog.load("XX", str(tmp_path))


def make_flat_contract(price: float = 0.30, standing: float = 0.0) -> TariffContract:
    """A flat contract for billing tests.

    Everything optional is left at its neutral default — no markup, no grid fee, no taxes, no
    capacity charge, no feed-in — so a bill is `kWh x price` and nothing else until a test
    explicitly attaches the component it wants to examine. Several tests below do exactly that:
    they take this contract and replace its `supply` or set its `capacity_charge`/`feed_in`, so
    the mutable dataclass is used on purpose rather than by accident.
    """
    return TariffContract(
        id="TEST_FLAT",
        carrier=EnergyCarrier.ELECTRICITY,
        country="DE",
        region=None,
        valid_from_year=2024,
        supply=TariffSupply(kind=SupplyKind.FLAT, working_price_in_euro_per_kwh=UncertainValue.exact(price)),
        standing_charge_in_euro_per_year=UncertainValue.exact(standing),
    )


class TestTariffEngine:
    """§8.4 billing engine properties."""

    def test_flat_contract_reproduces_kwh_times_price(self):
        """The central property test of §8.4."""
        determinants = BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4321.0)
        bill = apply_tariff(determinants, make_flat_contract(price=0.25))
        assert bill.by_category[CostCategory.ENERGY_WORKING].average == pytest.approx(4321.0 * 0.25)

    def test_capacity_charge_monotone_in_every_peak(self):
        """Raising any period peak never lowers the bill."""
        contract = make_flat_contract()
        contract.capacity_charge = CapacityCharge(
            kind=CapacityChargeKind.MONTHLY_PEAK, price_in_euro_per_kw=UncertainValue.exact(8.0)
        )
        base_peaks = [4.0] * 12
        base = apply_tariff(
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0,
                peak_per_billing_period_in_kw=base_peaks, annual_peak_in_kw=4.0,
            ),
            contract,
        )
        for month in range(12):
            raised = list(base_peaks)
            raised[month] += 2.0
            higher = apply_tariff(
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0,
                    peak_per_billing_period_in_kw=raised, annual_peak_in_kw=6.0,
                ),
                contract,
            )
            assert higher.total().average > base.total().average

    def test_dynamic_supply_uses_integrated_cost_and_decomposes(self):
        """DYNAMIC bills the native integral; flexibility value separates from volume (§8.5)."""
        contract = make_flat_contract()
        contract.supply = TariffSupply(
            kind=SupplyKind.DYNAMIC,
            spot_series="test",
            markup_in_euro_per_kwh=UncertainValue.exact(0.02),
        )
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=1000.0,
            cost_integrated_in_euro=70.0,  # load shifted into cheap hours
            mean_spot_price_in_euro_per_kwh=0.08,
        )
        bill = apply_tariff(determinants, contract)
        assert bill.by_category[CostCategory.ENERGY_WORKING].average == pytest.approx(70.0 + 1000.0 * 0.02)
        assert bill.flexibility_value_in_euro == pytest.approx(1000.0 * 0.08 - 70.0)

    def test_uncertain_additive_components_shift_slots(self):
        """Additive per-kWh bands shift each slot by E x delta without re-integration (§8.4)."""
        contract = make_flat_contract()
        contract.supply = TariffSupply(
            kind=SupplyKind.DYNAMIC,
            spot_series="test",
            markup_in_euro_per_kwh=UncertainValue(average=0.02, minimum=0.01, maximum=0.04),
        )
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0, cost_integrated_in_euro=80.0
        )
        working = apply_tariff(determinants, contract).by_category[CostCategory.ENERGY_WORKING]
        # The integrated spot cost (80 EUR) is a measured quantity and stays exact; only the
        # markup band moves the slots: 1000 kWh x 0.01 and x 0.04 EUR/kWh.
        assert working.minimum == pytest.approx(80.0 + 10.0)
        assert working.maximum == pytest.approx(80.0 + 40.0)

    def test_feed_in_revenue_negative(self):
        """Fixed tariff feed-in enters as negative cost."""
        contract = make_flat_contract()
        contract.feed_in = FeedIn(kind=FeedInKind.FIXED_TARIFF, rate_in_euro_per_kwh=UncertainValue.exact(0.08))
        bill = apply_tariff(
            BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=0.0, energy_sold_in_kwh=500.0),
            contract,
        )
        # 500 kWh x 0.08 EUR/kWh = 40 EUR, entered negative: money arriving is negative cost.
        assert bill.by_category[CostCategory.FEED_IN_REVENUE].average == pytest.approx(-40.0)

    def test_spot_referenced_feed_in_applies_the_spot_factor(self):
        """A direct-marketing share below 1.0 scales the spot term, not the markup (§8.4).

        SPOT_REFERENCED feed-in pays the marketer's share of the spot proceeds plus a flat
        markup; `FeedIn.spot_factor` is that share, and it was parsed but never applied, so a
        contract paying 80 % of spot was billed as if it paid all of it. The markup is a
        per-kWh amount agreed independently of the spot price and stays untouched, which is what
        the second assertion separates: only the integrated term moves with the factor.
        """
        contract = make_flat_contract()
        contract.feed_in = FeedIn(
            kind=FeedInKind.SPOT_REFERENCED,
            spot_factor=0.8,
            markup_in_euro_per_kwh=UncertainValue.exact(0.01),
        )
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=0.0,
            energy_sold_in_kwh=500.0,
            revenue_integrated_in_euro=100.0,
        )
        revenue = apply_tariff(determinants, contract).by_category[CostCategory.FEED_IN_REVENUE]
        assert revenue.average == pytest.approx(-(100.0 * 0.8 + 500.0 * 0.01))
        contract.feed_in.spot_factor = 1.0
        unscaled = apply_tariff(determinants, contract).by_category[CostCategory.FEED_IN_REVENUE]
        assert unscaled.average == pytest.approx(-(100.0 + 500.0 * 0.01))

    def test_billing_interval_must_divide(self):
        """seconds_per_timestep must divide the billing interval (§8.4).

        The rejection is pinned to `CostDataError` and to the message naming the interval: a bare
        `Exception` would also be satisfied by a `TypeError` or an `AttributeError` from a
        refactor that broke the check outright, which is the failure this test exists to catch.
        """
        from hisim.economics.database import CostDataError

        contract = make_flat_contract()
        contract.capacity_charge = CapacityCharge(
            kind=CapacityChargeKind.MONTHLY_PEAK,
            price_in_euro_per_kw=UncertainValue.exact(8.0),
            billing_interval_in_minutes=15,
        )
        validate_billing_interval(900, contract)
        validate_billing_interval(60, contract)
        with pytest.raises(CostDataError, match="does not divide the billing interval"):
            validate_billing_interval(7 * 60, contract)

    def test_tariff_counterfactual(self):
        """Billing the same profile under a flat contract isolates the tariff choice (§8.5)."""
        dynamic = make_flat_contract()
        dynamic.supply = TariffSupply(kind=SupplyKind.DYNAMIC, spot_series="test")
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0, cost_integrated_in_euro=200.0
        )
        outcome = tariff_counterfactual(determinants, dynamic, make_flat_contract(price=0.30))
        assert outcome["tariff_advantage_in_euro"].average == pytest.approx(300.0 - 200.0)

    def test_synthetic_series_is_deterministic_and_hourly(self):
        """The Q16 fallback profile."""
        series = synthetic_reference_spot_series()
        assert len(series) == 8760
        assert series == synthetic_reference_spot_series()
        assert min(series) >= 0.0


class TestDeletedDeadSurface:
    """Parsed-but-ignored knobs are gone rather than kept as promises (§8 D23).

    `ApplicantActor.from_actor` was a constructor nothing called. Keeping it alive is worse than
    deleting it — a name that looks like a supported mapping and has no consumer misleads the next
    reader. This assertion keeps it from being reintroduced silently. The other half of D23,
    `FinancingPlan.refinance_replacements`, is pinned in
    `test_economics_subsidy_integration.py`, because naming that dataclass means importing
    `financing`.
    """

    def test_applicant_actor_has_no_from_actor_constructor(self):
        """The unused timeline-actor mapping is deleted; applicants come from the profile."""
        assert not hasattr(ApplicantActor, "from_actor")


class TestScenarioDataOverlays:
    """§4.6 data overlays: changing one shipped datapoint without touching the shipped files.

    The two scenario tests that need nothing but the cost database. Set expansion, the evaluation
    cube and the break-even search all run through the evaluator and live in
    `test_economics_subsidy_integration.py` instead.
    """

    def test_data_overlay_changes_device_price(self):
        """Overlaying a datapoint answers 'what if heat pumps get cheaper' (§4.6)."""
        database = CostDatabase()
        overlaid = database.with_overlays(
            {"devices_DE.HEAT_PUMP.specific_investment": {"min": 900, "avg": 1100, "max": 1400}}, "cheap_hp"
        )
        entry = overlaid.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        assert entry.specific_investment.average == pytest.approx(1100.0)
        # The shipped database is untouched.
        assert database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.average == 1600.0
        assert overlaid.overlay_records and overlaid.overlay_records[0].detail == "cheap_hp"

    def test_legacy_flat_subsidy_share_is_not_overlayable(self):
        """W2.6: the §10.1 shim is subsidy data and left the device overlay surface."""
        from hisim.economics.database import CostDataError

        database = CostDatabase()
        with pytest.raises(CostDataError, match="is not overlayable"):
            database.with_overlays({"devices_DE.HEAT_PUMP.legacy_flat_subsidy_share": 0.0}, "no_subsidy")
