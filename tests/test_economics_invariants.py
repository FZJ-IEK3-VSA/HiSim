"""Property / invariant tests for the economics engine (cost-spec-v2 §5.1, §5.3).

Worked examples (§3) are point checks: they miss errors that cancel or sit off the tested
path. The tests here are the complementary half — statements that must hold for *every*
input, checked on seeded pseudo-random cases.

Every case is generated from ``random.Random(seed)`` with a fixed per-property seed, so a
failure is reproducible: each assertion message carries the case index, its seed and the
generated operands.

The properties, in the order of §5.1:

1. uncertainty-band semantics (slot ordering, revenue mirroring, envelope subtraction),
2. timeline algebra (NPV at 0 % = plain sum, pivots, annual series, category filters),
3. tariff billing (linearity of the working cost, fixed standing charge),
4. subsidy <= eligible cost,
5. zero-sum actor allocation,
6. money conservation — the killer invariant, partially expected to fail today (§2.3 W3.4).

§5.3 adds a third implementation for free: the closed-form parts (annuity loan schedule,
annuity factor, NPV) are cross-checked against `numpy_financial`.
"""

# clean

import random
from typing import Callable, Dict, List, Optional, Tuple

import numpy_financial as npf
import pytest

from hisim.economics.actors import AllocationContext, DE2024Ruleset, assert_zero_sum
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope, InstallationContext, Perspective, SubsidyMode
from hisim.economics.subsidies import (
    ApplicantProfile,
    Benefit,
    BenefitKind,
    Condition,
    EligibleCostSpec,
    LumpSumBenefit,
    MeasureForSubsidy,
    OperationalBenefit,
    PayoutKind,
    PerUnitBenefit,
    ShareBenefit,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyScheme,
    TaxCreditBenefit,
    _eligible_cost_basis,  # noqa: PLC2701 — the per-scheme basis the property is stated against
    solve_cumulation,
)
from hisim.economics.tariffs import (
    SupplyKind,
    TariffContract,
    TariffSupply,
    TimeOfUseBand,
    apply_tariff,
)
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: Cases per property. Full evaluations (§5.1 property 6) use FEW_CASES — each one runs the
#: whole engine, so the suite stays inside a few seconds.
CASES = 100
FEW_CASES = 25

#: One fixed seed per property; the per-case seed is SEED + case index.
SEED_BANDS = 20260812
SEED_TIMELINE = 20260813
SEED_TARIFFS = 20260814
SEED_SUBSIDIES = 20260815
SEED_ALLOCATION = 20260816
SEED_CONSERVATION = 20260817
SEED_NUMPY_FINANCIAL = 20260818

#: Categories a randomly generated timeline draws from (revenue kinds included on purpose).
RANDOM_CATEGORIES = [
    CostCategory.INVESTMENT,
    CostCategory.PLANNING,
    CostCategory.REMOVAL,
    CostCategory.REPLACEMENT,
    CostCategory.RESIDUAL_VALUE,
    CostCategory.MAINTENANCE,
    CostCategory.FIXED_OPERATION,
    CostCategory.ENERGY_WORKING,
    CostCategory.ENERGY_STANDING,
    CostCategory.ENERGY_CO2_PRICE,
    CostCategory.ENERGY_CAPACITY_CHARGE,
    CostCategory.FEED_IN_REVENUE,
    CostCategory.SUBSIDY,
    CostCategory.LOAN_INTEREST,
    CostCategory.LOAN_PRINCIPAL,
    CostCategory.LOAN_DISBURSEMENT,
    CostCategory.CO2_DAMAGE,
    CostCategory.ANYWAY_COST_CREDIT,
    CostCategory.REPLACEMENT_RESERVE,
]

#: Categories whose entries are emitted mirrored (negative) by the engine.
NEGATIVE_CATEGORIES = frozenset(
    {
        CostCategory.RESIDUAL_VALUE,
        CostCategory.FEED_IN_REVENUE,
        CostCategory.SUBSIDY,
        CostCategory.ANYWAY_COST_CREDIT,
        CostCategory.LOAN_DISBURSEMENT,
    }
)

SLOTS = ("minimum", "average", "maximum")


# --------------------------------------------------------------------------- generators


def coherent_band(rng: random.Random, low: float = -2000.0, high: float = 2000.0) -> UncertainValue:
    """A random band satisfying min <= avg <= max."""
    values = sorted(rng.uniform(low, high) for _ in range(3))
    return UncertainValue(average=values[1], minimum=values[0], maximum=values[2])


def cost_band(rng: random.Random, high: float = 20000.0) -> UncertainValue:
    """A random non-negative band (cost-type parameter)."""
    values = sorted(rng.uniform(0.0, high) for _ in range(3))
    return UncertainValue(average=values[1], minimum=values[0], maximum=values[2])


def maybe_exact(rng: random.Random, band: UncertainValue) -> UncertainValue:
    """Half the cases collapse to a degenerate band, so both regimes get exercised."""
    return UncertainValue.exact(band.average) if rng.random() < 0.5 else band


def random_timeline(rng: random.Random, horizon: int, count: int = 12) -> CashFlowTimeline:
    """A random timeline: random categories, years (some beyond the horizon) and amounts.

    Sign validation stays **on** here (W3.7): `NEGATIVE_CATEGORIES` above is this file's
    independent statement of which categories are negative-signed, so a validated timeline also
    checks that it still agrees with the engine's `timeline.CategoryRules.NEGATIVE_SIGN_CATEGORIES`.
    """
    timeline = CashFlowTimeline()
    subjects = ["HeatPump", "Battery", EnergyCarrier.ELECTRICITY.value, "financing"]
    for _ in range(count):
        category = rng.choice(RANDOM_CATEGORIES)
        amount = maybe_exact(rng, cost_band(rng, high=5000.0))
        if category in NEGATIVE_CATEGORIES:
            amount = amount.as_revenue()
        subject = rng.choice(subjects)
        timeline.add(
            CashFlowEntry(
                year=rng.randint(0, horizon + 3),
                amount_in_euro=amount,
                category=category,
                subject=subject,
                subject_kind=SubjectKind.CARRIER if subject.isupper() else SubjectKind.COMPONENT,
            )
        )
    return timeline


def slot_values(band: UncertainValue) -> Tuple[float, float, float]:
    """(minimum, average, maximum) as a plain tuple, for compact failure messages."""
    return (band.minimum, band.average, band.maximum)


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


# --------------------------------------------------------------------------- §5.1 (1) bands


class TestUncertaintyBandProperties:
    """§5.1: slot ordering, revenue mirroring, envelope subtraction, slot-wise caps."""

    def test_slot_ordering_survives_every_operation(self):
        """min <= avg <= max holds after +, -, scale, multiply_band, clamp_upper and sum."""
        for case in range(CASES):
            seed = SEED_BANDS + case
            rng = random.Random(seed)
            left = coherent_band(rng)
            right = coherent_band(rng)
            cost_left = cost_band(rng)
            cost_right = cost_band(rng)
            cap = coherent_band(rng)
            factor = rng.uniform(0.0, 5.0)
            context = case_context(
                seed, case, left=left, right=right, cost_left=cost_left, cost_right=cost_right,
                cap=cap, factor=factor,
            )
            results: Dict[str, UncertainValue] = {}
            for name, operation in (
                ("add", lambda: left + right),
                ("sub", lambda: left - right),
                ("scale", lambda: left.scale(factor)),
                ("multiply_band", lambda: cost_left.multiply_band(cost_right)),
                ("clamp_upper", lambda: left.clamp_upper(cap)),
                ("sum", lambda: UncertainValue.sum([left, right, cost_left, cost_right])),
                ("as_revenue", lambda: left.as_revenue()),
            ):
                try:
                    results[name] = operation()
                except ValueError as error:  # the band invariant is enforced in __post_init__
                    pytest.fail(f"{name} broke the band invariant: {error} — {context}")
                band = results[name]
                assert band.minimum <= band.average <= band.maximum, (
                    f"{name} returned an unordered band {slot_values(band)} — {context}"
                )
            clamped = results["clamp_upper"]
            for slot in SLOTS:
                assert getattr(clamped, slot) <= getattr(cap, slot), (
                    f"clamp_upper exceeded the cap in slot {slot}: "
                    f"{slot_values(clamped)} vs cap {slot_values(cap)} — {context}"
                )

    def test_as_revenue_mirrors_the_band_and_is_an_involution(self):
        """The optimistic world takes the revenue maximum; applying it twice is the identity."""
        for case in range(CASES):
            seed = SEED_BANDS + case
            rng = random.Random(seed)
            band = coherent_band(rng)
            context = case_context(seed, case, band=band)
            revenue = band.as_revenue()
            assert revenue.minimum == -band.maximum, f"minimum not mirrored — {context}"
            assert revenue.average == -band.average, f"average not mirrored — {context}"
            assert revenue.maximum == -band.minimum, f"maximum not mirrored — {context}"
            assert slot_values(revenue.as_revenue()) == slot_values(band), (
                f"as_revenue is not an involution: {slot_values(revenue.as_revenue())} — {context}"
            )

    def test_subtraction_is_the_envelope_of_the_three_slot_deltas(self):
        """§3.9: the result envelopes LOW-world, AVERAGE and HIGH-world deltas."""
        for case in range(CASES):
            seed = SEED_BANDS + case
            rng = random.Random(seed)
            left = coherent_band(rng)
            right = coherent_band(rng)
            context = case_context(seed, case, left=left, right=right)
            deltas = (
                left.minimum - right.minimum,
                left.average - right.average,
                left.maximum - right.maximum,
            )
            difference = left - right
            assert difference.average == pytest.approx(deltas[1]), f"average delta wrong — {context}"
            assert difference.minimum == pytest.approx(min(deltas)), (
                f"minimum is not the best-case delta: {difference.minimum} vs {min(deltas)} — {context}"
            )
            assert difference.maximum == pytest.approx(max(deltas)), (
                f"maximum is not the worst-case delta: {difference.maximum} vs {max(deltas)} — {context}"
            )

    def test_exact_band_minus_itself_is_exactly_zero(self):
        """Degenerate bands cancel bit-exactly — the base case every reconciliation relies on."""
        for case in range(CASES):
            seed = SEED_BANDS + case
            rng = random.Random(seed)
            value = rng.uniform(-1e6, 1e6)
            context = case_context(seed, case, value=value)
            difference = UncertainValue.exact(value) - UncertainValue.exact(value)
            assert slot_values(difference) == (0.0, 0.0, 0.0), (
                f"exact self-difference is {slot_values(difference)} — {context}"
            )


# --------------------------------------------------------------------------- §5.1 (2) timeline


class TestTimelineProperties:
    """§5.1: NPV at 0 % = plain sum; pivots and category filters partition the NPV."""

    def test_npv_at_zero_percent_is_the_plain_sum(self):
        """The single most-cited invariant of the discounting layer (§5.1)."""
        for case in range(CASES):
            seed = SEED_TIMELINE + case
            rng = random.Random(seed)
            timeline = random_timeline(rng, horizon=rng.randint(1, 30))
            context = case_context(seed, case, entries=len(timeline.entries))
            plain = UncertainValue.sum(entry.amount_in_euro for entry in timeline.entries)
            assert_bands_equal(timeline.npv(0.0), plain, context)

    def test_npv_by_buckets_sum_to_the_total(self):
        """Pivots partition the timeline: every bucket key adds up to npv() again."""
        keys: List[Tuple[str, Callable[[CashFlowEntry], object]]] = [
            ("category", lambda entry: entry.category),
            ("subject", lambda entry: entry.subject),
            ("year_parity", lambda entry: entry.year % 2),
        ]
        for case in range(CASES):
            seed = SEED_TIMELINE + case
            rng = random.Random(seed)
            timeline = random_timeline(rng, horizon=rng.randint(1, 30))
            rate = rng.choice([0.0, 0.01, 0.03, 0.07])
            total = timeline.npv(rate)
            for name, key in keys:
                context = case_context(seed, case, pivot=name, rate=rate)
                buckets = timeline.npv_by(rate, key)
                assert_bands_equal(UncertainValue.sum(buckets.values()), total, context)

    def test_nominal_annual_series_totals_the_undiscounted_sum_in_the_horizon(self):
        """The liquidity view keeps every euro dated 0..T and drops the rest (§4.3)."""
        for case in range(CASES):
            seed = SEED_TIMELINE + case
            rng = random.Random(seed)
            horizon = rng.randint(1, 20)
            timeline = random_timeline(rng, horizon=horizon)
            context = case_context(seed, case, horizon=horizon, entries=len(timeline.entries))
            series = timeline.nominal_annual_series(horizon)
            assert len(series) == horizon + 1, f"series length {len(series)} — {context}"
            expected = UncertainValue.sum(
                entry.amount_in_euro for entry in timeline.entries if 0 <= entry.year <= horizon
            )
            assert_bands_equal(UncertainValue.sum(series), expected, context)

    def test_dropped_and_kept_categories_partition_the_npv(self):
        """without_categories() plus its complement reproduces the original NPV."""
        for case in range(CASES):
            seed = SEED_TIMELINE + case
            rng = random.Random(seed)
            timeline = random_timeline(rng, horizon=rng.randint(1, 30))
            rate = rng.choice([0.0, 0.02, 0.05])
            dropped = frozenset(rng.sample(RANDOM_CATEGORIES, rng.randint(1, 6)))
            context = case_context(seed, case, rate=rate, dropped=sorted(item.value for item in dropped))
            kept_npv = timeline.without_categories(dropped).npv(rate)
            dropped_npv = timeline.filtered(lambda entry: entry.category in dropped).npv(rate)
            assert_bands_equal(kept_npv + dropped_npv, timeline.npv(rate), context)


# --------------------------------------------------------------------------- §5.1 (3) tariffs


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


# --------------------------------------------------------------------------- §5.1 (4) subsidies

#: Benefit kinds the generator uses. SOFT_LOAN is deliberately absent: §7 B3 (the solver values
#: the repayment grant on the gross measure cost while financing applies it to the principal) is
#: an open fix-or-freeze decision, so no property may freeze its behavior.
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
            # Stated on the AVERAGE slot here; the per-slot statement is the case below (B12).
            assert total.average <= gross.average * cap_share + 1e-6, (
                f"total support exceeds the state-aid cap — {report}"
            )
            assert total.average <= gross.average + 1e-6, (
                f"total support exceeds the eligible cost — {report}"
            )

    def test_total_upfront_support_stays_within_the_eligible_cost_in_every_slot(self):
        """The same bound, stated per slot (B12, fixed): each slot is a coherent world, so the
        state-aid cap and the eligible cost must hold in each of them, not only on average."""
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
                    schedule=[amount.average for amount in award.schedule_amounts],
                )
                for slot in SLOTS:
                    assert getattr(paid, slot) <= getattr(basis, slot) + 1e-6, (
                        f"tax credit schedule exceeds the eligible basis in slot {slot} — {report}"
                    )


# --------------------------------------------------------------------------- §5.1 (5) allocation


def random_allocation_context(rng: random.Random, horizon: int, banded_basis: bool) -> AllocationContext:
    """A context that exercises the CO2 split, the maintenance split and the levy path (§6)."""
    modernization = cost_band(rng, high=60000.0)
    return AllocationContext(
        horizon_years=horizon,
        building_specific_emissions_in_kg_per_m2_a=rng.choice([None, 5.0, 20.0, 34.0, 60.0]),
        heated_floor_area_in_m2=rng.choice([None, 120.0]),
        living_area_in_m2=rng.choice([None, 80.0, 150.0]),
        current_cold_rent_in_euro_per_m2_month=rng.choice([None, 6.0, 9.0]),
        modernization_cost_in_euro=modernization if banded_basis else UncertainValue.exact(modernization.average),
        subsidies_received_in_euro=UncertainValue.exact(rng.uniform(0.0, 20000.0)),
        avoided_maintenance_in_euro=UncertainValue.exact(rng.uniform(0.0, 5000.0)),
    )


class TestActorAllocationProperties:
    """§5.1 / §6.5: allocation only moves money between payers, it never creates any."""

    def test_allocation_is_zero_sum_over_random_timelines(self):
        """sum(payer NPVs) == the pre-allocation SYSTEM NPV, per slot (actors.assert_zero_sum).

        The levy basis is a degenerate band here, so the minted levy pair cancels in every slot
        and the *strong* form of the §6.5 invariant applies (`require_per_slot_equality`).
        """
        ruleset = DE2024Ruleset.load()
        for case in range(CASES):
            seed = SEED_ALLOCATION + case
            rng = random.Random(seed)
            horizon = rng.randint(2, 25)
            timeline = random_timeline(rng, horizon=horizon)
            allocation_context = random_allocation_context(rng, horizon, banded_basis=False)
            rate = rng.choice([0.0, 0.02, 0.05])
            allocated = ruleset.allocate(timeline, allocation_context)
            system_npv = timeline.npv(rate)
            payer_npvs = allocated.npv_by(rate, lambda entry: entry.payer)
            report = case_context(
                seed, case, horizon=horizon, rate=rate, entries=len(timeline.entries),
                levy_basis=slot_values(allocation_context.modernization_cost_in_euro),
            )
            try:
                assert_zero_sum(system_npv, list(payer_npvs.values()), require_per_slot_equality=True)
            except AssertionError as error:
                pytest.fail(f"{error} — {report}")
            # CO2_DAMAGE is socio-economic, not a household cash flow: it stays with SYSTEM by
            # design (actors.py) and is therefore part of the payer sum above, not excluded.
            damage_payers = {
                entry.payer for entry in allocated.entries if entry.category == CostCategory.CO2_DAMAGE
            }
            assert damage_payers <= {Actor.SYSTEM}, f"CO2_DAMAGE left SYSTEM: {damage_payers} — {report}"

    def test_zero_sum_survives_a_banded_modernization_levy_basis(self):
        """The envelope form of §6.5 with a banded levy basis (B11, decided 2026-08-12).

        With a banded basis the minted levy pair no longer cancels per slot: the tenant leg
        carries the levy band, the landlord leg its `as_revenue()` mirror, and the two sum to
        ``(L.min - L.max, 0, L.max - L.min)``. The invariant is therefore: exact equality in the
        AVERAGE slot, and containment of the system band in the payer sum's band. The regression
        assertion below pins the widening to *exactly* the mirror gap, so a future sign or
        mirroring change cannot hide inside the inequality.
        """
        ruleset = DE2024Ruleset.load()
        widened_cases = 0
        for case in range(CASES):
            seed = SEED_ALLOCATION + case
            rng = random.Random(seed)
            horizon = rng.randint(2, 25)
            timeline = random_timeline(rng, horizon=horizon)
            allocation_context = random_allocation_context(rng, horizon, banded_basis=True)
            rate = rng.choice([0.0, 0.02, 0.05])
            allocated = ruleset.allocate(timeline, allocation_context)
            system_npv = timeline.npv(rate)
            payer_npvs = allocated.npv_by(rate, lambda entry: entry.payer)
            report = case_context(
                seed, case, horizon=horizon, rate=rate,
                levy_basis=slot_values(allocation_context.modernization_cost_in_euro),
            )
            try:
                assert_zero_sum(system_npv, list(payer_npvs.values()))
            except AssertionError as error:
                pytest.fail(f"{error} — {report}")

            # The mirror, stated explicitly: the tenant legs carry L, the landlord legs
            # L.as_revenue(), so the payer sum's band is wider than the system band by exactly
            # the discounted (L.max - L.min) on each side, and identical in AVERAGE.
            levy_entries = [
                entry for entry in allocated.entries if entry.category == CostCategory.MODERNIZATION_LEVY
            ]
            gap = sum(
                (entry.amount_in_euro.maximum - entry.amount_in_euro.minimum)
                / ((1.0 + rate) ** entry.year)
                for entry in levy_entries
                if entry.payer == Actor.TENANT
            )
            total = UncertainValue.sum(list(payer_npvs.values()))
            assert total.average == pytest.approx(system_npv.average, abs=1e-6), report
            assert total.minimum == pytest.approx(system_npv.minimum - gap, abs=1e-6), report
            assert total.maximum == pytest.approx(system_npv.maximum + gap, abs=1e-6), report
            if gap > 1e-6:
                widened_cases += 1
        assert widened_cases, "no case actually minted a banded levy — the test would be vacuous"


# --------------------------------------------------------------------------- §5.1 (6) conservation


def property_scheme(
    scheme_id: str, kind: BenefitKind, benefit: Benefit, payout: PayoutKind
) -> SubsidyScheme:
    """An always-eligible synthetic scheme for the conservation cases."""
    return SubsidyScheme(
        id=scheme_id,
        country="DE",
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic conservation-test scheme",
        url="https://example.invalid/conservation",
        asset_classes=[ComponentType.PV],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=Condition(kind="all"),
        benefit_kind=kind,
        benefit=benefit,
        eligible_cost=EligibleCostSpec(),
        cumulation_group=None,
        combined_rate_cap=None,
        excludes=[],
        payout_kind=payout,
    )


def conservation_parameters(rng: Optional[random.Random] = None) -> EconomicParameters:
    """Deterministic (or randomized) parameters with a non-zero discount rate."""
    if rng is None:
        return EconomicParameters(
            price_basis_year=2024,
            observation_period_in_years=10,
            interest_rate=0.05,
            general_price_escalation_rate=0.0,
            investment_price_escalation_rate=0.0,
            energy_price_escalation_rates={carrier: 0.0 for carrier in EnergyCarrier},
        )
    return EconomicParameters(
        price_basis_year=2024,
        observation_period_in_years=rng.randint(5, 25),
        interest_rate=rng.choice([0.0, 0.02, 0.05]),
        general_price_escalation_rate=rng.choice([0.0, 0.02]),
        investment_price_escalation_rate=rng.choice([0.0, 0.02]),
    )


def pv_facts(investment: float, lifetime: float = 20.0) -> ComponentCostFacts:
    """Fully overridden PV facts so the case never depends on database values."""
    return ComponentCostFacts(
        asset_class=ComponentType.PV,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue.exact(investment),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=lifetime,
        maintenance_rate_override=UncertainValue.exact(0.0),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
        embodied_co2_override_in_kg=0.0,
        override_source="cost-spec-v2 §5.1 conservation test",
    )


#: The deterministic violating case of §5.1: an upfront grant plus an OPERATIONAL per-kWh
#: support. Both land in the timeline as SUBSIDY entries, but `_add_subsidies` only accumulates
#: the upfront one into the total that becomes `basis_parts["subsidies"]`.
OPERATIONAL_INVESTMENT_IN_EURO = 20000.0
OPERATIONAL_GRANT_RATE = 0.2
OPERATIONAL_RATE_PER_KWH = 0.05
OPERATIONAL_DURATION_YEARS = 5
OPERATIONAL_ENERGY_SOLD_IN_KWH = 3000.0


def operational_subsidy_case() -> Tuple[EconomicEvaluator, EvaluationInputs, Perspective]:
    """Landlord-scope PV evaluation with an upfront grant and an operational per-kWh support."""
    catalog = SubsidyCatalog(
        schemes=[
            property_scheme(
                "PROPERTY_GRANT",
                BenefitKind.SHARE_OF_ELIGIBLE_COST,
                ShareBenefit(rate=OPERATIONAL_GRANT_RATE),
                PayoutKind.UPFRONT_GRANT,
            ),
            property_scheme(
                "PROPERTY_OPERATIONAL",
                BenefitKind.OPERATIONAL,
                OperationalBenefit(
                    rate_per_kwh=OPERATIONAL_RATE_PER_KWH,
                    carrier=EnergyCarrier.ELECTRICITY,
                    duration_years=OPERATIONAL_DURATION_YEARS,
                ),
                PayoutKind.OPERATIONAL,
            ),
        ],
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country="DE",
    )
    evaluator = EconomicEvaluator(CostDatabase(), conservation_parameters(), catalog)
    inputs = EvaluationInputs(
        simulation_year=2024,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("PV", pv_facts(OPERATIONAL_INVESTMENT_IN_EURO))],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=0.0,
                energy_sold_in_kwh=OPERATIONAL_ENERGY_SOLD_IN_KWH,
            )
        ],
    )
    perspective = Perspective(
        id="conservation_landlord",
        installation_context=InstallationContext.GREENFIELD,
        actor_scope=ActorScope.LANDLORD,
        subsidy_mode=SubsidyMode.full(),
    )
    return evaluator, inputs, perspective


def tax_credit_case() -> Tuple[EconomicEvaluator, EvaluationInputs, Perspective]:
    """Owner-occupier PV evaluation with an upfront grant and a three-year tax credit."""
    catalog = SubsidyCatalog(
        schemes=[
            property_scheme(
                "PROPERTY_GRANT",
                BenefitKind.SHARE_OF_ELIGIBLE_COST,
                ShareBenefit(rate=0.2),
                PayoutKind.UPFRONT_GRANT,
            ),
            property_scheme(
                "PROPERTY_TAX_CREDIT",
                BenefitKind.TAX_CREDIT,
                TaxCreditBenefit(rate=0.2, years=3),
                PayoutKind.TAX_CREDIT_SCHEDULE,
            ),
        ],
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country="DE",
    )
    evaluator = EconomicEvaluator(CostDatabase(), conservation_parameters(), catalog)
    inputs = EvaluationInputs(
        simulation_year=2024,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("PV", pv_facts(OPERATIONAL_INVESTMENT_IN_EURO))],
    )
    perspective = Perspective(
        id="conservation_owner",
        installation_context=InstallationContext.GREENFIELD,
        subsidy_mode=SubsidyMode.full(),
    )
    return evaluator, inputs, perspective


def discounted_sum(entries: List[CashFlowEntry], rate: float) -> UncertainValue:
    """The discounted total of the given entries, computed independently of CashFlowTimeline."""
    total = UncertainValue.exact(0.0)
    for entry in entries:
        total = total + entry.amount_in_euro.scale(1.0 / ((1.0 + rate) ** entry.year))
    return total


class TestMoneyConservation:
    """§5.1: every euro of an aggregate traces back to timeline entries."""

    def _random_evaluation(self, rng: random.Random):
        """A random but fully overridden evaluation (no database price dependence)."""
        parameters = conservation_parameters(rng)
        evaluator = EconomicEvaluator(CostDatabase(), parameters)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts("PV", pv_facts(rng.uniform(5000.0, 30000.0), rng.uniform(8.0, 25.0)))
            ],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY,
                    energy_bought_in_kwh=rng.uniform(0.0, 8000.0),
                    energy_sold_in_kwh=rng.uniform(0.0, 4000.0),
                )
            ],
            living_area_in_m2=rng.choice([None, 120.0]),
            current_cold_rent_in_euro_per_m2_month=rng.choice([None, 8.0]),
            building_specific_emissions_in_kg_per_m2_a=rng.choice([None, 20.0, 45.0]),
        )
        perspective = Perspective(
            id="conservation",
            installation_context=rng.choice(
                [
                    InstallationContext.GREENFIELD,
                    InstallationContext.OPERATING_ONLY,
                ]
            ),
            actor_scope=rng.choice([ActorScope.SYSTEM, ActorScope.LANDLORD, ActorScope.TENANT]),
            subsidy_mode=SubsidyMode.none(),
        )
        return evaluator, inputs, perspective, parameters

    def test_total_npv_equals_the_discounted_scoped_timeline(self):
        """The headline NPV is exactly the discounted sum of the entries it is scoped to."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            scoped = [
                entry
                for entry in result.timeline.entries
                if perspective.actor_scope == ActorScope.SYSTEM or entry.payer == result.scope_payer
            ]
            report = case_context(
                seed, case, perspective=perspective.installation_context.value,
                actor=perspective.actor_scope.value, rate=parameters.interest_rate,
                entries=len(result.timeline.entries),
            )
            assert_bands_equal(
                result.total_npv_in_euro, discounted_sum(scoped, parameters.interest_rate), report
            )

    def test_category_and_component_pivots_sum_to_the_total(self):
        """Both reported pivots partition the same money (§7.4 reconciliation)."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            report = case_context(
                seed, case, actor=perspective.actor_scope.value, rate=parameters.interest_rate
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_category.values()), result.total_npv_in_euro, report
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_component.values()), result.total_npv_in_euro, report
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_payer.values()),
                result.timeline.npv(parameters.interest_rate),
                report,
            )

    def test_component_breakdowns_sum_to_the_total(self):
        """Per-subject breakdowns are a pivot of the same timeline, not a second computation."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            report = case_context(seed, case, actor=perspective.actor_scope.value)
            assert_bands_equal(
                UncertainValue.sum(
                    breakdown.total_npv_in_euro for breakdown in result.component_breakdowns.values()
                ),
                result.total_npv_in_euro,
                report,
            )
            assert result.parameters.interest_rate == parameters.interest_rate

    def test_subsidy_total_is_recoverable_from_the_timeline(self):
        """The levy basis must deduct exactly what the SUBSIDY entries say, in one unit.

        W3.4 (decided 2026-08-12): that unit is the **nominal** sum of every SUBSIDY entry —
        §559 BGB deducts the support *received*. The case pays an upfront grant plus five years
        of operational support, so nominal and discounted differ: the assertion has teeth.
        """
        evaluator, inputs, perspective = operational_subsidy_case()
        result = evaluator.evaluate(inputs, perspective)
        rate = result.parameters.interest_rate
        subsidy_entries = [
            entry for entry in result.timeline.entries if entry.category == CostCategory.SUBSIDY
        ]
        assert subsidy_entries, "the case must produce subsidy entries"
        nominal = UncertainValue.sum(entry.amount_in_euro for entry in subsidy_entries).as_revenue()
        discounted = discounted_sum(subsidy_entries, rate).as_revenue()
        assert nominal.average != pytest.approx(discounted.average, abs=1e-6), (
            "the case must distinguish the two units (payouts after year 0 at a non-zero rate)"
        )
        levy = next(
            entry
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
        )
        ruleset = DE2024Ruleset.load()
        # basis = modernization cost - subsidies received (no avoided maintenance in greenfield).
        implied_basis = levy.amount_in_euro.average / ruleset.levy.levy_rate_per_year
        deducted = OPERATIONAL_INVESTMENT_IN_EURO - implied_basis
        assert deducted == pytest.approx(nominal.average, abs=1e-6), (
            f"the levy basis deducted {deducted:.2f} EUR of subsidies, but the timeline holds "
            f"{nominal.average:.2f} EUR nominal / {discounted.average:.2f} EUR discounted "
            f"(entries: {[(entry.year, entry.amount_in_euro.average) for entry in subsidy_entries]})"
        )

    def test_reported_subsidy_figures_share_one_unit(self):
        """Every reported subsidy figure names its unit and agrees with the timeline in it.

        W3.4: the breakdown used to carry a single `subsidies_in_euro` that summed nominal euros
        while `npv_by_category[SUBSIDY]` discounted them. The result object now reports both,
        each named after its unit, and each must reproduce the timeline exactly.
        """
        evaluator, inputs, perspective = tax_credit_case()
        result = evaluator.evaluate(inputs, perspective)
        breakdown = result.component_breakdowns["PV"]
        rate = result.parameters.interest_rate
        subsidy_entries = [
            entry for entry in result.timeline.entries if entry.category == CostCategory.SUBSIDY
        ]
        nominal = UncertainValue.sum(entry.amount_in_euro for entry in subsidy_entries).as_revenue()
        subsidy_npv = result.npv_by_category[CostCategory.SUBSIDY].as_revenue()
        assert breakdown.subsidies_nominal_in_euro.average == pytest.approx(nominal.average, rel=1e-9), (
            f"breakdown subsidies_nominal_in_euro={breakdown.subsidies_nominal_in_euro.average:.2f} EUR "
            f"vs the timeline's nominal SUBSIDY sum {nominal.average:.2f} EUR"
        )
        assert breakdown.subsidies_npv_in_euro.average == pytest.approx(subsidy_npv.average, rel=1e-9), (
            f"breakdown subsidies_npv_in_euro={breakdown.subsidies_npv_in_euro.average:.2f} EUR "
            f"vs npv_by_category[SUBSIDY]={subsidy_npv.average:.2f} EUR"
        )
        assert breakdown.subsidies_npv_in_euro.average == pytest.approx(
            discounted_sum(subsidy_entries, rate).as_revenue().average, rel=1e-9
        )
        # The tax credit pays out in years 1..3, so the two units must actually differ here —
        # otherwise this test would pass for a mixed-unit implementation too.
        assert breakdown.subsidies_nominal_in_euro.average > breakdown.subsidies_npv_in_euro.average


# --------------------------------------------------------------------------- §5.3 cross-check


class TestNumpyFinancialCrossCheck:
    """§5.3: the closed-form parts checked against an independent implementation.

    `numpy_financial` is a third implementation of the same textbook formulas, so agreement
    on randomized inputs is evidence beyond "the code agrees with itself".

    Sign convention: `npf.pmt` returns the payment as a cash *outflow* (negative) for a
    positive principal, while `financing.loan_flows` reports costs positive — hence the
    leading minus in every comparison below.
    """

    def test_annuity_matches_numpy_financial_pmt(self):
        """Every year's annuity (interest + repayment) equals -npf.pmt(rate, term, principal)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.001, 0.15)
            term = rng.randint(1, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            expected_annuity = -float(npf.pmt(rate, term, principal))
            assert len(schedule) == term, f"schedule has {len(schedule)} years — {context}"
            for year, interest, repayment in schedule:
                annuity = interest.average + repayment.average
                assert annuity == pytest.approx(expected_annuity, rel=1e-6), (
                    f"year {year} annuity {annuity} != npf.pmt {expected_annuity} — {context}"
                )

    def test_annuity_loan_amortizes_exactly(self):
        """Repayments total the principal and the remaining debt after the term is zero."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.choice([0.0, rng.uniform(0.001, 0.15)])
            term = rng.randint(1, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            remaining = principal
            for _year, _interest, repayment in schedule:
                remaining -= repayment.average
            total_repaid = sum(repayment.average for _year, _interest, repayment in schedule)
            assert total_repaid == pytest.approx(principal, rel=1e-9), (
                f"repayments total {total_repaid}, principal {principal} — {context}"
            )
            assert remaining == pytest.approx(0.0, abs=1e-6 * principal), (
                f"remaining debt after the term is {remaining} — {context}"
            )

    def test_interest_per_year_follows_the_outstanding_debt(self):
        """Interest is rate x remaining debt — the amortization identity npf.pmt is built on."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.001, 0.15)
            term = rng.randint(2, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            remaining = principal
            for year, interest, repayment in schedule:
                assert interest.average == pytest.approx(rate * remaining, rel=1e-9), (
                    f"year {year} interest {interest.average} != {rate * remaining} — {context}"
                )
                remaining -= repayment.average

    def test_annuity_factor_matches_numpy_financial_pmt_on_a_unit_principal(self):
        """EconomicParameters.annuity_factor() is PMT on a principal of one euro (§4.4)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.choice([0.0, rng.uniform(0.001, 0.15)])
            years = rng.randint(1, 50)
            context = case_context(seed, case, rate=rate, years=years)
            parameters = EconomicParameters(interest_rate=rate, observation_period_in_years=years)
            expected = -float(npf.pmt(rate, years, 1.0))
            assert parameters.annuity_factor() == pytest.approx(expected, rel=1e-9), (
                f"annuity factor {parameters.annuity_factor()} != npf.pmt {expected} — {context}"
            )

    def test_timeline_npv_matches_numpy_financial_npv(self):
        """CashFlowTimeline.npv equals npf.npv on the year-indexed series.

        Convention: both discount by ``(1 + rate) ** year``, so a year-0 entry is *not*
        discounted and `npf.npv(rate, series)` can be fed the series directly. (Excel's NPV
        differs — it places the first value at the end of period 1 — which is why the
        convention is asserted here rather than assumed.)
        """
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            horizon = rng.randint(1, 30)
            rate = rng.choice([0.0, 0.01, 0.03, 0.07, 0.11])
            timeline = random_timeline(rng, horizon=horizon, count=rng.randint(1, 25))
            # npf.npv works on a dense year-indexed series; entries beyond the last index would
            # be silently dropped, so the series spans every year the timeline uses.
            last_year = max(entry.year for entry in timeline.entries)
            series = [0.0] * (last_year + 1)
            for entry in timeline.entries:
                series[entry.year] += entry.amount_in_euro.average
            context = case_context(
                seed, case, rate=rate, horizon=horizon, entries=len(timeline.entries)
            )
            expected = float(npf.npv(rate, series))
            actual = timeline.npv(rate).average
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), (
                f"timeline NPV {actual} != npf.npv {expected} — {context}"
            )

    def test_year_zero_convention_is_undiscounted(self):
        """A single year-0 entry is worth its face value at any discount rate (§3.6)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.0, 0.2)
            amount = rng.uniform(-50000.0, 50000.0)
            context = case_context(seed, case, rate=rate, amount=amount)
            # The amount is drawn with an arbitrary sign to exercise the discounting algebra, so
            # this synthetic timeline opts out of the §3.9 sign convention (W3.7).
            timeline = CashFlowTimeline(validate=False)
            timeline.add(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=UncertainValue.exact(amount),
                    category=CostCategory.INVESTMENT,
                    subject="device",
                )
            )
            assert timeline.npv(rate).average == pytest.approx(amount, rel=1e-12), context
            assert float(npf.npv(rate, [amount])) == pytest.approx(amount, rel=1e-12), context
