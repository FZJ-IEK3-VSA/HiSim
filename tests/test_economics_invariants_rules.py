"""Property tests: tariff billing and subsidy bounds (cost_spec.md §5, §8).

Second of the three invariant test files (split per the PR-3 review's 500-line rule):
billing monotonicity and decomposition properties across random contracts, and the subsidy
bounds — support never exceeds its basis or cap, in any slot. Band and timeline properties
are in `test_economics_invariants.py`; allocation and money conservation are in
`test_economics_invariants_allocation.py`.
"""

import random
from typing import Tuple
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantProfile,
    Benefit,
    BenefitKind,
    Condition,
    EligibleCostSpec,
    LumpSumBenefit,
    MeasureForSubsidy,
    PayoutKind,
    PerUnitBenefit,
    ShareBenefit,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyScheme,
    TaxCreditBenefit,
    _eligible_cost_basis,
    solve_cumulation,
)
from hisim.economics.tariffs import (
    SupplyKind,
    TariffContract,
    TariffSupply,
    TimeOfUseBand,
    apply_tariff,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

CASES = 100

SEED_TARIFFS = 20260814

SEED_SUBSIDIES = 20260815

SLOTS = ("minimum", "best_estimate", "maximum")


def cost_band(rng: random.Random, high: float = 20000.0) -> UncertainValue:
    """A random non-negative band (cost-type parameter)."""
    values = sorted(rng.uniform(0.0, high) for _ in range(3))
    return UncertainValue(best_estimate=values[1], minimum=values[0], maximum=values[2])


def maybe_exact(rng: random.Random, band: UncertainValue) -> UncertainValue:
    """Half the cases collapse to a degenerate band, so both regimes get exercised."""
    return UncertainValue.exact(band.best_estimate) if rng.random() < 0.5 else band


def slot_values(band: UncertainValue) -> Tuple[float, float, float]:
    """(minimum, best_estimate, maximum) as a plain tuple, for compact failure messages."""
    return (band.minimum, band.best_estimate, band.maximum)


def assert_bands_equal(actual: UncertainValue, expected: UncertainValue, context: str) -> None:
    """Slot-wise equality with a relative tolerance, reporting the generated case on failure."""
    for slot in SLOTS:
        assert getattr(actual, slot) == pytest.approx(getattr(expected, slot), rel=1e-9, abs=1e-9), (
            f"slot {slot}: {slot_values(actual)} != {slot_values(expected)} — {context}"
        )


def case_context(seed: int, case: int, **operands) -> str:
    """A reproduction hint: seed, case index and the generated operands."""
    rendered = ", ".join(f"{name}={value!r}" for name, value in operands.items())
    return f"case={case}, rng=random.Random({seed}), {rendered}"


def random_flat_contract(rng: random.Random) -> TariffContract:
    """A FLAT contract with random price, standing charge and additive components."""
    return TariffContract(
        id="PROPERTY_FLAT",
        carrier=EnergyCarrier.ELECTRICITY,
        country="DE",
        region=None,
        valid_from_year=2024,
        supply=TariffSupply(
            kind=SupplyKind.FLAT,
            working_price_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.4)),
            markup_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.05)),
            grid_fee_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.1)),
            taxes_and_levies_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.08)),
        ),
        standing_charge_in_euro_per_year=maybe_exact(rng, cost_band(rng, high=200.0)),
        source_ids=("inline:property test",),
    )


def random_tou_contract(rng: random.Random) -> TariffContract:
    """A two-band time-of-use contract."""
    contract = random_flat_contract(rng)
    contract.id = "PROPERTY_TOU"
    contract.supply = TariffSupply(
        kind=SupplyKind.TIME_OF_USE,
        bands=[
            TimeOfUseBand(name="day", price_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.5))),
            TimeOfUseBand(name="night", price_in_euro_per_kwh=maybe_exact(rng, cost_band(rng, high=0.3))),
        ],
        markup_in_euro_per_kwh=contract.supply.markup_in_euro_per_kwh,
        grid_fee_in_euro_per_kwh=contract.supply.grid_fee_in_euro_per_kwh,
        taxes_and_levies_in_euro_per_kwh=contract.supply.taxes_and_levies_in_euro_per_kwh,
    )
    return contract


def scale_determinants(determinants: BillingDeterminants, factor: float) -> BillingDeterminants:
    """The same billing determinants with every energy quantity scaled."""
    return BillingDeterminants(
        carrier=determinants.carrier,
        energy_bought_in_kwh=determinants.energy_bought_in_kwh * factor,
        energy_sold_in_kwh=determinants.energy_sold_in_kwh * factor,
        energy_bought_per_band_in_kwh={
            band: energy * factor for band, energy in determinants.energy_bought_per_band_in_kwh.items()
        },
    )


GENERATED_BENEFIT_KINDS = [
    BenefitKind.SHARE_OF_ELIGIBLE_COST,
    BenefitKind.BONUS_SHARE,
    BenefitKind.LUMP_SUM,
    BenefitKind.PER_UNIT,
    BenefitKind.TAX_CREDIT,
]

ELIGIBLE_COST_CATEGORIES = [CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL]


def random_scheme(rng: random.Random, index: int, group: str) -> SubsidyScheme:
    """A synthetic, always-eligible scheme with random benefit terms and eligible-cost spec."""
    kind = rng.choice(GENERATED_BENEFIT_KINDS)
    benefit: Benefit
    payout = PayoutKind.UPFRONT_GRANT
    if kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
        benefit = ShareBenefit(rate=rng.uniform(0.05, 0.6))
    elif kind == BenefitKind.LUMP_SUM:
        benefit = LumpSumBenefit(amount=rng.uniform(500.0, 15000.0))
    elif kind == BenefitKind.PER_UNIT:
        benefit = PerUnitBenefit(amount=rng.uniform(50.0, 800.0))
    else:
        benefit = TaxCreditBenefit(rate=rng.uniform(0.05, 0.3), years=rng.randint(1, 5))
        payout = PayoutKind.TAX_CREDIT_SCHEDULE
    categories = rng.sample(ELIGIBLE_COST_CATEGORIES, rng.randint(1, 3))
    caps = [rng.uniform(5000.0, 40000.0)] if rng.random() < 0.4 else []
    return SubsidyScheme(
        id=f"PROPERTY_SCHEME_{index:02d}",
        country="DE",
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic property-test scheme",
        url="https://example.invalid/property",
        asset_classes=[ComponentType.HEAT_PUMP],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=Condition(kind="all"),  # no children = always satisfied
        benefit_kind=kind,
        benefit=benefit,
        eligible_cost=EligibleCostSpec(
            categories=categories,
            cap_per_dwelling_unit_in_euro=caps,
            basis=rng.choice(["GROSS", "NET"]),
            proration=rng.choice(["NONE", "RESIDENTIAL_SHARE"]),
        ),
        cumulation_group=group if kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE) else None,
        combined_rate_cap=rng.choice([None, 0.4, 0.7, 1.0]),
        excludes=[],
        payout_kind=payout,
    )


def random_subsidy_case(
    rng: random.Random,
) -> Tuple[SubsidyCatalog, MeasureForSubsidy, SubsidyContext, float]:
    """A random catalog / measure / context triple plus the catalog's overall cap share."""
    # The EU state-aid overall cap (§5.4) is what bounds the *total* support, so it is always
    # declared here; without it "sum of all schemes <= eligible cost" is not an engine property.
    overall_cap_share = rng.uniform(0.4, 1.0)
    schemes = [random_scheme(rng, index, "PROPERTY_GROUP") for index in range(rng.randint(1, 4))]
    catalog = SubsidyCatalog(
        schemes=schemes,
        questions={},
        snapshot_date=None,
        overall_cap_share=overall_cap_share,
        base_path="",
        country="DE",
    )
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=rng.uniform(3.0, 25.0),
        size_unit=Units.KILOWATT,
    )
    measure = MeasureForSubsidy(
        subject="HeatPump",
        facts=facts,
        measure_kind=rng.choice(["INSTALL", "REPLACE"]),
        cost_by_category={
            CostCategory.INVESTMENT: maybe_exact(rng, cost_band(rng, high=45000.0)),
            CostCategory.PLANNING: maybe_exact(rng, cost_band(rng, high=3000.0)),
            CostCategory.REMOVAL: maybe_exact(rng, cost_band(rng, high=2000.0)),
        },
        vat_rate=rng.choice([0.0, 0.19]),
    )
    context = SubsidyContext(
        applicant=ApplicantProfile(taxable_household_income_in_euro=rng.uniform(20000.0, 90000.0)),
        building=SubsidyBuildingContext(
            construction_year=rng.randint(1900, 2015),
            dwelling_units=rng.randint(1, 4),
            residential_floor_area_in_m2=rng.uniform(60.0, 400.0),
            commercial_floor_area_in_m2=rng.choice([0.0, rng.uniform(10.0, 120.0)]),
        ),
    )
    return catalog, measure, context, overall_cap_share


class TestTariffBillingProperties:
    """§5.1: doubling consumption doubles the variable part; the standing charge is fixed."""

    def _random_case(self, rng: random.Random) -> Tuple[TariffContract, BillingDeterminants]:
        """A random contract with matching (sometimes deliberately mismatching) determinants."""
        if rng.random() < 0.5:
            contract = random_flat_contract(rng)
            bought = rng.uniform(0.0, 20000.0)
            return contract, BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=bought
            )
        contract = random_tou_contract(rng)
        day = rng.uniform(0.0, 12000.0)
        night = rng.uniform(0.0, 8000.0)
        # Sometimes the meter reports more total energy than the bands cover (billed at band 1).
        unbanded = rng.choice([0.0, rng.uniform(0.0, 500.0)])
        return contract, BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=day + night + unbanded,
            energy_bought_per_band_in_kwh={"day": day, "night": night},
        )

    def test_doubling_consumption_doubles_the_working_cost_only(self):
        """The working cost is linear in energy; the standing charge does not move (§5.1)."""
        for case in range(CASES):
            seed = SEED_TARIFFS + case
            rng = random.Random(seed)
            contract, determinants = self._random_case(rng)
            context = case_context(
                seed, case, supply=contract.supply.kind.value,
                energy=determinants.energy_bought_in_kwh,
                bands=determinants.energy_bought_per_band_in_kwh,
            )
            single = apply_tariff(determinants, contract)
            double = apply_tariff(scale_determinants(determinants, 2.0), contract)
            assert_bands_equal(
                double.by_category[CostCategory.ENERGY_WORKING],
                single.by_category[CostCategory.ENERGY_WORKING].scale(2.0),
                f"working cost is not linear in energy — {context}",
            )
            assert_bands_equal(
                double.by_category[CostCategory.ENERGY_STANDING],
                single.by_category[CostCategory.ENERGY_STANDING],
                f"standing charge moved with consumption — {context}",
            )

    def test_bill_total_equals_the_sum_of_its_categories(self):
        """No bill component is invented or lost between the pivot and the total."""
        for case in range(CASES):
            seed = SEED_TARIFFS + case
            rng = random.Random(seed)
            contract, determinants = self._random_case(rng)
            context = case_context(seed, case, supply=contract.supply.kind.value)
            bill = apply_tariff(determinants, contract)
            expected = UncertainValue.exact(0.0)
            for amount in bill.by_category.values():
                expected = expected + amount
            assert_bands_equal(bill.total(), expected, context)

    def test_zero_consumption_costs_nothing_but_the_standing_charge(self):
        """The working cost vanishes with the energy; the standing charge survives."""
        for case in range(CASES):
            seed = SEED_TARIFFS + case
            rng = random.Random(seed)
            contract, determinants = self._random_case(rng)
            context = case_context(seed, case, supply=contract.supply.kind.value)
            bill = apply_tariff(scale_determinants(determinants, 0.0), contract)
            assert_bands_equal(
                bill.by_category[CostCategory.ENERGY_WORKING], UncertainValue.exact(0.0), context
            )
            assert_bands_equal(
                bill.by_category[CostCategory.ENERGY_STANDING],
                contract.standing_charge_in_euro_per_year,
                context,
            )


class TestSubsidyBoundProperties:
    """§5.1: no award exceeds its eligible basis, and the combination stays inside the cap."""

    def test_every_award_stays_within_its_own_eligible_basis(self):
        """Per scheme, per slot: upfront support <= the scheme's eligible cost basis (§5.2)."""
        discount = EconomicParameters(price_basis_year=2024).discount_factor
        for case in range(CASES):
            seed = SEED_SUBSIDIES + case
            rng = random.Random(seed)
            catalog, measure, context, _cap_share = random_subsidy_case(rng)
            decision = solve_cumulation(catalog, measure, context, 2024, discount)
            schemes = {scheme.id: scheme for scheme in catalog.schemes}
            for award in decision.applied:
                basis, _binding = _eligible_cost_basis(schemes[award.scheme_id], measure, context)
                scheme = schemes[award.scheme_id]
                report = case_context(
                    seed, case, scheme=award.scheme_id, kind=scheme.benefit_kind.value,
                    benefit=scheme.benefit, basis=slot_values(basis),
                    award=slot_values(award.upfront_amount),
                )
                for slot in SLOTS:
                    assert getattr(award.upfront_amount, slot) <= getattr(basis, slot) + 1e-6, (
                        f"award exceeds its eligible basis in slot {slot} — {report}"
                    )

    def test_total_upfront_support_stays_within_the_eligible_cost(self):
        """The combination's upfront support <= the state-aid cap on the total eligible cost."""
        discount = EconomicParameters(price_basis_year=2024).discount_factor
        for case in range(CASES):
            seed = SEED_SUBSIDIES + case
            rng = random.Random(seed)
            catalog, measure, context, cap_share = random_subsidy_case(rng)
            decision = solve_cumulation(catalog, measure, context, 2024, discount)
            gross = UncertainValue.sum(measure.cost_by_category.values())
            total = UncertainValue.sum(award.upfront_amount for award in decision.applied)
            report = case_context(
                seed, case, schemes=[scheme.id for scheme in catalog.schemes],
                cap_share=cap_share, gross=slot_values(gross), total=slot_values(total),
            )
            # Stated on the BEST_ESTIMATE slot here; the per-slot statement is the case below (B12).
            assert total.best_estimate <= gross.best_estimate * cap_share + 1e-6, (
                f"total support exceeds the state-aid cap — {report}"
            )
            assert total.best_estimate <= gross.best_estimate + 1e-6, (
                f"total support exceeds the eligible cost — {report}"
            )

    def test_total_upfront_support_stays_within_the_eligible_cost_in_every_slot(self):
        """The same bound, stated per slot (B12, fixed).

        Each slot is a coherent world, so the state-aid cap and the eligible cost must hold in
        each of them, not only in the best-estimate slot.
        """
        discount = EconomicParameters(price_basis_year=2024).discount_factor
        for case in range(CASES):
            seed = SEED_SUBSIDIES + case
            rng = random.Random(seed)
            catalog, measure, context, cap_share = random_subsidy_case(rng)
            decision = solve_cumulation(catalog, measure, context, 2024, discount)
            gross = UncertainValue.sum(measure.cost_by_category.values())
            total = UncertainValue.sum(award.upfront_amount for award in decision.applied)
            report = case_context(
                seed, case, schemes=[scheme.id for scheme in catalog.schemes],
                cap_share=cap_share, gross=slot_values(gross), total=slot_values(total),
            )
            for slot in SLOTS:
                assert getattr(total, slot) <= getattr(gross, slot) * cap_share + 1e-6, (
                    f"total support exceeds the state-aid cap in slot {slot} — {report}"
                )
                assert getattr(total, slot) <= getattr(gross, slot) + 1e-6, (
                    f"total support exceeds the eligible cost in slot {slot} — {report}"
                )

    def test_tax_credit_schedules_stay_within_the_eligible_basis(self):
        """A schedule pays out the same capped amount, only spread over its years (§5.2)."""
        discount = EconomicParameters(price_basis_year=2024).discount_factor
        for case in range(CASES):
            seed = SEED_SUBSIDIES + case
            rng = random.Random(seed)
            catalog, measure, context, _cap_share = random_subsidy_case(rng)
            decision = solve_cumulation(catalog, measure, context, 2024, discount)
            schemes = {scheme.id: scheme for scheme in catalog.schemes}
            for award in decision.applied:
                if award.payout_kind != PayoutKind.TAX_CREDIT_SCHEDULE:
                    continue
                basis, _binding = _eligible_cost_basis(schemes[award.scheme_id], measure, context)
                paid = UncertainValue.sum(award.schedule_amounts)
                report = case_context(
                    seed, case, scheme=award.scheme_id, basis=slot_values(basis),
                    schedule=[amount.best_estimate for amount in award.schedule_amounts],
                )
                for slot in SLOTS:
                    assert getattr(paid, slot) <= getattr(basis, slot) + 1e-6, (
                        f"tax credit schedule exceeds the eligible basis in slot {slot} — {report}"
                    )
