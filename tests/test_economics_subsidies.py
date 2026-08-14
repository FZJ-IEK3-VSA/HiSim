"""Unit tests for the subsidy catalog, conditions and benefits (§5.1-§5.3).

First of the two subsidy unit-test files (split per the PR-3 review's 500-line rule): typed
benefit payloads and catalog parsing errors, the condition AST with its field vocabulary
(W2.3), D25's named ineligibility reasons, catalog/scheme provenance, the deleted dead
surface, and the §4.6 scenario data overlays. The solver-side tests — cumulation, caps,
subsidy modes and the shipped BEG/§35c behavior — are in
`test_economics_subsidy_solver.py`; everything that needs the evaluator is in
`test_economics_subsidy_integration.py`. The placement rule is unchanged: a test importing
any module above the rule engines belongs in the integration file.
"""

import dataclasses
import json
import os
import pytest
from hisim.economics import subsidies
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.facts import ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    BenefitKind,
    Condition,
    EligibleCostSpec,
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
    scheme_context_fields,
    solve_cumulation,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
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

ALWAYS_ELIGIBLE = Condition(kind="all")

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
            {"devices_DE.HEAT_PUMP.specific_investment": {"min": 900, "best_estimate": 1100, "max": 1400}}, "cheap_hp"
        )
        entry = overlaid.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        assert entry.specific_investment.best_estimate == pytest.approx(1100.0)
        # The shipped database is untouched.
        assert database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.best_estimate == 1600.0
        assert overlaid.overlay_records and overlaid.overlay_records[0].detail == "cheap_hp"

    def test_legacy_flat_subsidy_share_is_not_overlayable(self):
        """W2.6: the §10.1 shim is subsidy data and left the device overlay surface."""
        from hisim.economics.database import CostDataError

        database = CostDatabase()
        with pytest.raises(CostDataError, match="is not overlayable"):
            database.with_overlays({"devices_DE.HEAT_PUMP.legacy_flat_subsidy_share": 0.0}, "no_subsidy")


