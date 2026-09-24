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
import shutil
from typing import Any, Dict, Optional

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
    PerUnitBenefit,
    ShareBenefit,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyContextFields,
    SubsidyDataError,
    SubsidyScheme,
    TaxCreditBenefit,
    Tier,
    TieredPerUnitBenefit,
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


def _copy_catalog_side_files(base_path: str, target) -> None:
    """Copies the files a catalog load needs beside `<COUNTRY>.json` into a temporary directory.

    A catalog is three files, not one: the schemes, the source registry every scheme cites (an
    unsourced scheme is refused at load, W2.4) and the questionnaire. A test that edits the scheme
    file into a `tmp_path` has to bring the other two along or it is testing the loader's error
    path instead of the edit it made.

    Args:
        base_path: The shipped catalog directory to copy from.
        target: The `tmp_path` the edited `DE.json` was written into.
    """
    for name in ("sources.json", "questions_DE.json"):
        with open(os.path.join(base_path, name), encoding="utf-8") as file:
            (target / name).write_text(file.read(), encoding="utf-8")


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


def write_catalog(tmp_path, benefit: dict, scheme_id: str = "TEST_SCHEME") -> str:
    """Writes a one-scheme catalog with the given benefit object and returns its base path.

    The benefit is the only variable part; everything else is a valid minimal scheme, so a load
    error can only come from the benefit under test and the assertion on the message is
    meaningful. This exercises the *file* path deliberately — W2.2 moved benefit typing to load
    time, so these malformed payloads must be rejected while parsing the catalog, not later
    inside the solver where the scheme id is no longer at hand.
    """
    catalog: Dict[str, Any] = {
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


class TestTieredPerUnitBenefit:
    """hisim-cyc.3: an amount per unit that changes by band, capped (SEAI's solar PV grant)."""

    SEAI_PV = {
        "kind": "TIERED_PER_UNIT",
        "tiers": [{"up_to": 2, "amount_per_unit": 700}, {"up_to": 4, "amount_per_unit": 200}],
        "size_unit": "kW",
        "cap_in_euro": 1800,
    }

    @staticmethod
    def seai_pv() -> TieredPerUnitBenefit:
        """The SEAI grant, built in Python: 700/kW to 2 kW, 200/kW to 4 kW, at most 1,800 EUR."""
        return TieredPerUnitBenefit(
            tiers=(Tier(2.0, 700.0), Tier(4.0, 200.0)), size_unit=Units.KILOWATT, cap_in_euro=1800.0
        )

    def test_it_parses_into_its_typed_payload(self):
        """The JSON surface: a list of tier objects, a size unit and an optional cap, through the parser."""
        kind, benefit = subsidies.parse_benefit(dict(self.SEAI_PV), "TEST_SCHEME")
        assert kind is BenefitKind.TIERED_PER_UNIT
        assert benefit == self.seai_pv()

    def test_the_shipped_irish_catalogue_carries_it(self):
        """The four SEAI PV steps are one tiered scheme now, paid per kW of array size."""
        by_id = {scheme.id: scheme for scheme in SubsidyCatalog.load("IE").schemes}
        assert by_id["IE_SEAI_SOLAR_PV"].benefit == self.seai_pv()
        assert not [scheme_id for scheme_id in by_id if scheme_id.startswith("IE_SEAI_SOLAR_PV_")]

    @pytest.mark.parametrize(
        "size, expected",
        [(0.0, 0.0), (0.5, 350.0), (2.0, 1400.0), (2.5, 1500.0), (3.9, 1780.0), (4.0, 1800.0), (9.0, 1800.0)],
    )
    def test_each_band_pays_its_rate_on_its_share_of_the_size(self, size, expected):
        """Band sums, the page's 2.5 kWp -> 1,500 EUR example, and nothing beyond a closed last band."""
        benefit = self.seai_pv()
        assert benefit.amount_for(size) == pytest.approx(expected)
        assert benefit.value_estimate(123456.0, size) == pytest.approx(expected)

    def test_an_open_last_band_runs_until_the_cap(self):
        """``up_to: null`` on the last band pays on every further unit, and the cap stops it."""
        benefit = TieredPerUnitBenefit(
            tiers=(Tier(2.0, 700.0), Tier(None, 200.0)), size_unit=Units.KILOWATT, cap_in_euro=2000.0
        )
        assert benefit.amount_for(4.0) == pytest.approx(1800.0)
        assert benefit.amount_for(10.0) == pytest.approx(2000.0)
        uncapped = TieredPerUnitBenefit(tiers=(Tier(None, 100.0),), size_unit=Units.KILOWATT)
        assert uncapped.amount_for(7.0) == pytest.approx(700.0)

    def test_a_single_closed_band_with_a_binding_cap_is_a_flat_rate_with_a_ceiling(self):
        """No later band exists for the cap to starve, so a cap below the band's full value is fine."""
        _kind, benefit = subsidies.parse_benefit(
            {
                "kind": "TIERED_PER_UNIT",
                "tiers": [{"up_to": 4, "amount_per_unit": 700}],
                "size_unit": "kW",
                "cap_in_euro": 1800,
            },
            "TEST_SCHEME",
        )
        assert isinstance(benefit, TieredPerUnitBenefit)
        assert benefit.amount_for(2.0) == pytest.approx(1400.0)
        assert benefit.amount_for(4.0) == pytest.approx(1800.0)

    @pytest.mark.parametrize(
        "tiers, cap, message",
        [
            ([], None, "at least one tier"),
            ([{"up_to": 4, "amount_per_unit": 200}, {"up_to": 2, "amount_per_unit": 700}], 1800, "must ascend"),
            ([{"up_to": 2, "amount_per_unit": -700}], 1000, "cannot be negative"),
            ([{"up_to": None, "amount_per_unit": 700}, {"up_to": 4, "amount_per_unit": 200}], None, "only the last"),
            (
                [{"up_to": 2, "amount_per_unit": 700}, {"up_to": 4, "amount_per_unit": 200}],
                1000,
                "not above the first tier's full value 1400",
            ),
            (
                [{"up_to": 2, "amount_per_unit": 700}, {"up_to": 4, "amount_per_unit": 200}],
                1400,
                "not above the first tier's full value 1400",
            ),
            ([{"up_to": 2, "amount_per_unit": 700}], -1, "not a finite amount above 0"),
            ([{"up_to": 2, "amount_per_unit": 700}], 0, "not a finite amount above 0"),
            ([{"up_to": 2, "amount_per_unit": 700}], float("nan"), "not a finite amount above 0"),
            ([{"up_to": None, "amount_per_unit": 700}], float("inf"), "not a finite amount above 0"),
            (
                [{"up_to": 2, "amount_per_unit": 700}, {"up_to": 4, "amount_per_unit": 200}],
                None,
                "tier 1 is the last tier and ends at 4.0, but no cap_in_euro is set",
            ),
            ([{"up_to": 2, "amount_per_unit": float("nan")}], 1000, "must be finite"),
            ([{"up_to": 2, "amount_per_unit": float("inf")}], 1000, "must be finite"),
            ([{"up_to": float("inf"), "amount_per_unit": 700}], 1000, "a bound is a finite size above 0"),
            ([{"up_to": float("nan"), "amount_per_unit": 700}], 1000, "a bound is a finite size above 0"),
            ([{"up_to": 0, "amount_per_unit": 700}], 1000, "a bound is a finite size above 0"),
        ],
    )
    def test_a_malformed_band_list_is_refused_at_load_naming_the_scheme(self, tmp_path, tiers, cap, message):
        """Every refusal, read from a file as ``json.load`` reads it (NaN and Infinity included)."""
        base = write_catalog(
            tmp_path, {"kind": "TIERED_PER_UNIT", "tiers": tiers, "size_unit": "kW", "cap_in_euro": cap}
        )
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME") as refusal:
            SubsidyCatalog.load("XX", base)
        assert message in str(refusal.value)

    def test_a_tier_with_a_misspelled_key_is_refused_by_scheme_and_key(self, tmp_path):
        """A typo inside a tier fails the load like a typo in the benefit itself."""
        base = write_catalog(
            tmp_path,
            {"kind": "TIERED_PER_UNIT", "tiers": [{"up_to": 2, "amount_per_units": 700}], "size_unit": "kW"},
        )
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME: benefit key 'tiers'"):
            SubsidyCatalog.load("XX", base)

    @pytest.mark.parametrize(
        "up_to, amount_per_unit, message",
        [
            (2.0, -1.0, "cannot be negative"),
            (2.0, float("nan"), "must be finite"),
            (2.0, float("inf"), "must be finite"),
            (0.0, 700.0, "a bound is a finite size above 0"),
            (-2.0, 700.0, "a bound is a finite size above 0"),
            (float("nan"), 700.0, "a bound is a finite size above 0"),
            (float("inf"), 700.0, "a bound is a finite size above 0"),
        ],
    )
    def test_a_tier_refuses_values_outside_its_own_domain(self, up_to, amount_per_unit, message):
        """A band is checked where it is built, not only as part of a benefit."""
        with pytest.raises(SubsidyDataError, match=message):
            Tier(up_to, amount_per_unit)

    def test_tiers_passed_as_a_list_are_frozen_into_a_tuple(self):
        """A caller's list cannot change the benefit after construction."""
        bands = [Tier(2.0, 700.0), Tier(4.0, 200.0)]
        benefit = TieredPerUnitBenefit(
            tiers=bands, size_unit=Units.KILOWATT, cap_in_euro=1800.0  # type: ignore[arg-type]
        )
        bands.append(Tier(8.0, 100.0))
        assert benefit.tiers == (Tier(2.0, 700.0), Tier(4.0, 200.0))
        assert benefit == self.seai_pv()

    def test_a_nan_size_is_refused_and_a_negative_one_pays_nothing(self):
        """A NaN size would fall through every band comparison and price as zero; it is an error instead."""
        with pytest.raises(SubsidyDataError, match="cannot price a measure size of nan"):
            self.seai_pv().amount_for(float("nan"))
        with pytest.raises(SubsidyDataError, match="cannot price a measure size of nan"):
            PerUnitBenefit(amount=100.0, size_unit=Units.KILOWATT).amount_for(float("nan"))
        assert self.seai_pv().amount_for(-3.0) == 0.0


class TestPerUnitSizeUnit:
    """The per-unit kinds state the unit their amount is per, and the solver holds a measure to it."""

    @pytest.mark.parametrize(
        "benefit",
        [
            {"kind": "PER_UNIT", "amount": 100},
            {"kind": "TIERED_PER_UNIT", "tiers": [{"up_to": None, "amount_per_unit": 100}]},
        ],
    )
    def test_a_catalogue_without_the_size_unit_fails_to_load(self, tmp_path, benefit):
        """The unit is mandatory: an amount per nothing-in-particular is not loaded."""
        base = write_catalog(tmp_path, benefit)
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME: .*misses the mandatory key 'size_unit'"):
            SubsidyCatalog.load("XX", base)

    @pytest.mark.parametrize("unit", ["kWp", "W", "", 5])
    def test_a_unit_no_measure_is_sized_in_fails_to_load(self, tmp_path, unit):
        """The vocabulary is ``ComponentCostFacts.size_unit``'s, restricted to the priceable units."""
        base = write_catalog(tmp_path, {"kind": "PER_UNIT", "amount": 100, "size_unit": unit})
        with pytest.raises(SubsidyDataError, match="TEST_SCHEME: benefit key 'size_unit'"):
            SubsidyCatalog.load("XX", base)

    def test_the_per_unit_kind_parses_its_unit(self):
        """``"m2"`` is the ``Units`` value ``ComponentCostFacts`` uses for areas."""
        kind, benefit = subsidies.parse_benefit({"kind": "PER_UNIT", "amount": 40, "size_unit": "m2"}, "TEST")
        assert kind is BenefitKind.PER_UNIT
        assert benefit == PerUnitBenefit(amount=40.0, size_unit=Units.SQUARE_METER)

    @pytest.mark.parametrize(
        "kind, benefit",
        [
            (BenefitKind.PER_UNIT, PerUnitBenefit(amount=40.0, size_unit=Units.SQUARE_METER)),
            (
                BenefitKind.TIERED_PER_UNIT,
                TieredPerUnitBenefit(tiers=(Tier(None, 40.0),), size_unit=Units.SQUARE_METER),
            ),
        ],
    )
    def test_a_measure_sized_in_another_unit_is_refused_by_name(self, kind, benefit):
        """An amount per m² is never multiplied by a heat pump's kilowatts."""
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = make_scheme("PER_M2_SCHEME", ALWAYS_ELIGIBLE, kind, benefit)
        with pytest.raises(SubsidyDataError) as refusal:
            _combination_awards([scheme], make_measure(), full_context(), None)
        message = str(refusal.value)
        assert "PER_M2_SCHEME" in message and "'m2'" in message and "'kW'" in message

    def test_validate_checks_the_unit_against_the_cost_database(self, tmp_path):
        """The shipped kW passes; an amount per m² on PV, which the database prices per kW, is an error."""
        from hisim.economics.validation import validate_subsidy_catalog  # noqa: PLC0415 — one test needs it

        database = CostDatabase()
        assert validate_subsidy_catalog("IE", cost_database=database).errors == []
        base = tmp_path / "catalogue"
        shutil.copytree(SubsidyCatalog.DEFAULT_PATH, base)
        irish = base / "IE.json"
        text = irish.read_text(encoding="utf-8")
        assert text.count('"size_unit": "kW"') == 1
        irish.write_text(text.replace('"size_unit": "kW"', '"size_unit": "m2"'), encoding="utf-8")
        errors = validate_subsidy_catalog("IE", str(base), database).errors
        assert len(errors) == 1 and "IE_SEAI_SOLAR_PV" in errors[0] and "['kW']" in errors[0], errors


class TestOnePerUnitSolverPath:
    """PER_UNIT and TIERED_PER_UNIT share one solver branch: the benefit prices the size."""

    @pytest.mark.parametrize(
        "kind, benefit, expected",
        [
            (BenefitKind.PER_UNIT, PerUnitBenefit(amount=150.0, size_unit=Units.KILOWATT), 1500.0),
            (
                BenefitKind.TIERED_PER_UNIT,
                TieredPerUnitBenefit(
                    tiers=(Tier(2.0, 700.0), Tier(4.0, 200.0), Tier(None, 50.0)), size_unit=Units.KILOWATT
                ),
                2100.0,
            ),
        ],
    )
    def test_the_amounts_are_the_benefits_own_and_clamped_to_the_basis(self, kind, benefit, expected):
        """10 kW: 150 x 10 = 1,500; 2 x 700 + 2 x 200 + 6 x 50 = 2,100. A 1,000 EUR measure caps both."""
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = make_scheme("PER_KW_SCHEME", ALWAYS_ELIGIBLE, kind, benefit)
        award = _combination_awards([scheme], make_measure(cost=30000.0), full_context(), None)[0]
        assert award.upfront_amount.best_estimate == pytest.approx(expected)
        assert award.upfront_amount.minimum == award.upfront_amount.maximum == award.upfront_amount.best_estimate
        assert award.eligible_basis_in_euro is not None
        cheap = _combination_awards([scheme], make_measure(cost=1000.0), full_context(), None)[0]
        assert cheap.upfront_amount.best_estimate == pytest.approx(1000.0)

    @pytest.mark.parametrize(
        "kind, benefit",
        [
            (BenefitKind.PER_UNIT, PerUnitBenefit(amount=150.0, size_unit=Units.KILOWATT)),
            (BenefitKind.TIERED_PER_UNIT, TieredPerUnitBenefit(tiers=(Tier(None, 150.0),), size_unit=Units.KILOWATT)),
        ],
    )
    def test_a_scheme_with_no_eligible_cost_categories_is_not_clamped(self, kind, benefit):
        """The lump sum's rule: no categories means the amount is unconditional, and no basis is stated."""
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = make_scheme(
            "UNCONDITIONAL", ALWAYS_ELIGIBLE, kind, benefit, eligible_cost=EligibleCostSpec(categories=[])
        )
        award = _combination_awards([scheme], make_measure(cost=1000.0), full_context(), None)[0]
        assert award.upfront_amount.best_estimate == pytest.approx(1500.0)
        assert award.eligible_basis_in_euro is None


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
        fields = subsidies._enumerate_context_fields()  # pylint: disable=protected-access
        assert "applicant.newly_added_flag" in fields

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
        assert not failed_condition_descriptions(condition, context, None)


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
        assert scheme is not None
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


class TestSchemeDisplayNames:
    """Q20: a scheme is named for a human, and the id survives beside it.

    Every shipped scheme carries a `display_name` ("BEG EM heat pump — speed bonus (20 %)"); the
    field is optional so a catalog written before Q20 still loads, and `label` is the one place
    the id fallback lives. The award carries the name along because a report is regularly rendered
    from a serialized result in a process that never loaded a catalog.
    """

    def test_every_shipped_scheme_has_a_unique_display_name(self):
        """The shipped DE, AT and IE catalogs are complete and unambiguous."""
        for country in ("DE", "AT", "IE"):
            catalog = SubsidyCatalog.load(country)
            names = [scheme.display_name for scheme in catalog.schemes]
            assert all(names), f"{country}: a scheme ships without a display name"
            assert len(set(names)) == len(names), f"{country}: two schemes share a display name"
            for scheme in catalog.schemes:
                assert scheme.label == scheme.display_name

    def test_a_scheme_without_a_display_name_falls_back_to_its_id(self, tmp_path):
        """Backward compatibility: the field is optional and the id then stands in for it."""
        source = SubsidyCatalog.load("DE")
        with open(os.path.join(source.base_path, "DE.json"), encoding="utf-8") as file:
            raw = json.load(file)
        for item in raw["schemes"]:
            item.pop("display_name", None)
        (tmp_path / "DE.json").write_text(json.dumps(raw), encoding="utf-8")
        _copy_catalog_side_files(source.base_path, tmp_path)
        catalog = SubsidyCatalog.load("DE", base_path=str(tmp_path))
        assert catalog.schemes
        for scheme in catalog.schemes:
            assert scheme.display_name is None
            assert scheme.label == scheme.id

    def test_validate_flags_a_blank_and_a_duplicated_display_name(self, tmp_path):
        """`validate` is the CI gate on the new field (Q20)."""
        from hisim.economics.validation import validate_subsidy_catalog

        source = SubsidyCatalog.load("DE")
        with open(os.path.join(source.base_path, "DE.json"), encoding="utf-8") as file:
            raw = json.load(file)
        raw["schemes"][0]["display_name"] = "   "
        raw["schemes"][1]["display_name"] = raw["schemes"][2]["display_name"]
        (tmp_path / "DE.json").write_text(json.dumps(raw), encoding="utf-8")
        _copy_catalog_side_files(source.base_path, tmp_path)
        report = validate_subsidy_catalog("DE", base_path=str(tmp_path))
        assert any("blank display_name" in error for error in report.errors), report.errors
        assert any("share the display_name" in error for error in report.errors), report.errors

    def test_the_shipped_catalogs_validate_clean_on_display_names(self):
        """No warning about a missing name, no error about a duplicate, on what ships."""
        from hisim.economics.validation import validate_subsidy_catalog

        for country in ("DE", "AT", "IE"):
            report = validate_subsidy_catalog(country)
            assert not [item for item in report.errors if "display_name" in item], report.errors
            assert not [item for item in report.warnings if "display_name" in item], report.warnings

    def test_the_award_carries_the_name_and_survives_serialization(self):
        """The name travels on the award, so a report built from JSON still shows it."""
        from hisim.economics.serialization import _decision_from_json
        from hisim.economics.subsidies import SubsidyAward, SubsidyDecision

        award = SubsidyAward(
            scheme_id="DE_BEG_EM_HP_SPEED_2024",
            payout_kind=PayoutKind.UPFRONT_GRANT,
            upfront_amount=UncertainValue.exact(3000.0),
            display_name="BEG EM heat pump — speed bonus (20 %)",
        )
        restored = _decision_from_json(SubsidyDecision(measure_subject="HeatPump", applied=[award]).to_json())
        assert restored.applied[0].label == "BEG EM heat pump — speed bonus (20 %)"
        assert restored.applied[0].scheme_id == "DE_BEG_EM_HP_SPEED_2024"
        # An award written before Q20 keeps working, with the id as its label.
        legacy = dict(restored.to_json()["applied"][0])
        legacy.pop("display_name")
        older = _decision_from_json({"measure_subject": "HeatPump", "applied": [legacy]})
        assert older.applied[0].label == "DE_BEG_EM_HP_SPEED_2024"


class TestConfiguredCatalogPathResolution:
    """PR-9 finding: a configured catalog path that did not resolve fell through to the shim.

    A `subsidy_catalog_path` is written by a system setup or a scenario file and read back by a
    command whose working directory is unrelated, so a relative path resolved against the cwd alone
    silently missed — and the evaluation then priced the whole run with the §10.1 legacy flat
    percentages from the device catalog, which no legal text backs. Naming a catalog that cannot be
    read is now a fail-fast error (D25); naming none at all is still legitimate and, since the shim
    was retired, books no subsidy.
    """

    def test_a_relative_path_resolves_against_the_installation_root(self, monkeypatch, tmp_path):
        """`hisim/subsidy_catalog` resolves from any working directory, not just the repo root."""
        monkeypatch.chdir(tmp_path)
        resolved = SubsidyCatalog.resolve_base_path(os.path.join("hisim", "subsidy_catalog"))
        assert os.path.isfile(os.path.join(resolved, "DE.json"))
        # The package's own data directory is tried too, so the bare name works as well.
        assert os.path.isfile(os.path.join(SubsidyCatalog.resolve_base_path("subsidy_catalog"), "DE.json"))

    def test_an_unresolvable_path_raises_and_names_what_it_tried(self, monkeypatch, tmp_path):
        """The error names the configured path and every candidate, instead of returning None.

        One error type for both callers: the CLI turns a `CostDataError` into exit code 2 and
        `postprocessing_main` propagates exactly that type, so the bridge's refusal fails the run
        rather than being logged and worked around.
        """
        from hisim.economics.catalog_entries import CostDataError

        monkeypatch.chdir(tmp_path)
        with pytest.raises(CostDataError, match="does not resolve to a directory"):
            SubsidyCatalog.resolve_base_path("catalogs/that/never/existed")
        with pytest.raises(CostDataError, match="priced without subsidies"):
            SubsidyCatalog.load_configured("DE", "catalogs/that/never/existed")

    def test_a_shadowing_directory_in_the_cwd_is_refused_rather_than_preferred(self, monkeypatch, tmp_path):
        """Two candidates exist, so neither is chosen: the answer would depend on the cwd.

        The three roots used to be tried in order with the first hit winning, so a directory named
        `subsidy_catalog/` in whatever directory the command happened to be started from silently
        shadowed the shipped catalog — and the run reported catalog-priced subsidies from a catalog
        nobody had chosen, with nothing in the output saying which one it read. The error has to
        name both places, because the reader's next question is which of the two they meant.
        """
        from hisim.economics.catalog_entries import CostDataError

        shadow = tmp_path / "subsidy_catalog"
        shadow.mkdir()
        monkeypatch.chdir(tmp_path)
        with pytest.raises(CostDataError, match="ambiguous") as raised:
            SubsidyCatalog.resolve_base_path("subsidy_catalog")
        message = str(raised.value)
        assert str(shadow) in message  # the one that would have won
        assert os.path.dirname(SubsidyCatalog.DEFAULT_PATH) in message  # the shipped one
        assert "absolute" in message  # and the fix

    def test_an_absolute_path_is_taken_as_given_even_beside_a_shadow(self, monkeypatch, tmp_path):
        """An absolute path names one directory, so no other root is ever tried against it."""
        shadow = tmp_path / "subsidy_catalog"
        shadow.mkdir()
        monkeypatch.chdir(tmp_path)
        assert SubsidyCatalog.resolve_base_path(str(shadow)) == str(shadow)
        # And the unambiguous relative case still resolves: nothing shadows this one.
        resolved = SubsidyCatalog.resolve_base_path(os.path.join("hisim", "subsidy_catalog"))
        assert os.path.isfile(os.path.join(resolved, "DE.json"))

    def test_no_configured_catalog_is_none(self):
        """A parameter set that names no catalog gets None, and is priced without subsidies."""
        assert SubsidyCatalog.load_configured("IE", None) is None
        assert SubsidyCatalog.load_configured("IE", "") is None

    def test_an_override_path_wins_over_the_configured_one(self, tmp_path):
        """`--subsidy-catalog` replaces the parameters' path rather than being ignored."""
        catalog = SubsidyCatalog.load_configured(
            "DE", str(tmp_path), os.path.join("hisim", "subsidy_catalog")
        )
        assert catalog is not None and catalog.schemes

    def test_the_cli_exits_with_a_message_on_an_unresolvable_catalog(self, tmp_path, capsys):
        """The CLI turns the error into exit 2 and a message, not a traceback (and not a shim run).

        End-to-end over the same path the finding was observed on: a parameters file whose
        `subsidy_catalog_path` does not exist. The observable is that nothing was priced.
        """
        from hisim.economics.__main__ import main
        from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
        from hisim.economics.serialization import write_inputs

        write_inputs(
            EvaluationInputs(
                simulation_year=2024,
                simulated_period_fraction=1.0,
                cost_facts=[
                    SubjectCostFacts(
                        "HeatPump",
                        ComponentCostFacts(
                            asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT
                        ),
                    )
                ],
            ),
            str(tmp_path),
        )
        parameters_path = tmp_path / "parameters.json"
        with open(parameters_path, "w", encoding="utf-8") as file:
            json.dump(
                EconomicParameters(price_basis_year=2024, subsidy_catalog_path="no/such/catalog").to_dict(),
                file,
            )
        assert main(["evaluate", str(tmp_path), "--parameters", str(parameters_path)]) == 2
        assert "does not resolve to a directory" in capsys.readouterr().err
        assert not os.path.isfile(os.path.join(str(tmp_path), "lifecycle_costs.json"))


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

    def test_an_override_that_breaks_the_parameters_is_refused(self):
        """A scenario axis cannot set a value the constructor would have rejected (§4.6).

        `apply_parameter_overrides` assigns with `setattr`, which bypasses
        `EconomicParameters.__post_init__` — so before the re-validation an axis could set
        `interest_rate` to -1.5 or the observation period to 0 and the cube would happily evaluate
        a cell whose discount factor divides by zero or flips sign. Both are meaningless rather
        than merely extreme, which is why they are the two the parameter object validates at all,
        and the refusal has to name them where the axis is declared.
        """
        from hisim.economics.scenarios import apply_parameter_overrides

        base = EconomicParameters(price_basis_year=2024)
        with pytest.raises(ValueError, match="interest_rate"):
            apply_parameter_overrides(base, {"interest_rate": -1.5})
        with pytest.raises(ValueError, match="observation_period_in_years"):
            apply_parameter_overrides(base, {"observation_period_in_years": 0})
        # The caller's parameters are untouched by the refused attempt, and a legal override still
        # comes back applied.
        assert base.interest_rate == EconomicParameters(price_basis_year=2024).interest_rate
        assert apply_parameter_overrides(base, {"interest_rate": 0.05}).interest_rate == 0.05

    def test_legacy_flat_subsidy_share_is_not_overlayable(self):
        """W2.6: the §10.1 shim is subsidy data and left the device overlay surface."""
        from hisim.economics.database import CostDataError

        database = CostDatabase()
        with pytest.raises(CostDataError, match="is not overlayable"):
            database.with_overlays({"devices_DE.HEAT_PUMP.legacy_flat_subsidy_share": 0.0}, "no_subsidy")
