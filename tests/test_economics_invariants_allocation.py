"""Property tests: actor allocation (cost_spec.md §6).

Part of the invariant test files (split per the PR-3 review's 500-line rule): the zero-sum
allocation invariant and the CO2/levy split properties across random timelines. Band and
timeline properties are in `test_economics_invariants.py`; tariff and subsidy properties in
`test_economics_invariants_rules.py`; money conservation and the numpy-financial cross-check
in `test_economics_invariants_conservation.py`.
"""

import random
from typing import Tuple
import pytest
from hisim.economics.actors import AllocationContext, DE2024Ruleset, assert_zero_sum
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
)
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base

CASES = 100

SEED_ALLOCATION = 20260816

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

NEGATIVE_CATEGORIES = frozenset(
    {
        CostCategory.RESIDUAL_VALUE,
        CostCategory.FEED_IN_REVENUE,
        CostCategory.SUBSIDY,
        CostCategory.ANYWAY_COST_CREDIT,
        CostCategory.LOAN_DISBURSEMENT,
    }
)


def cost_band(rng: random.Random, high: float = 20000.0) -> UncertainValue:
    """A random non-negative band (cost-type parameter)."""
    values = sorted(rng.uniform(0.0, high) for _ in range(3))
    return UncertainValue(best_estimate=values[1], minimum=values[0], maximum=values[2])


def maybe_exact(rng: random.Random, band: UncertainValue) -> UncertainValue:
    """Half the cases collapse to a degenerate band, so both regimes get exercised."""
    return UncertainValue.exact(band.best_estimate) if rng.random() < 0.5 else band


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
    """(minimum, best_estimate, maximum) as a plain tuple, for compact failure messages."""
    return (band.minimum, band.best_estimate, band.maximum)


def case_context(seed: int, case: int, **operands) -> str:
    """A reproduction hint: seed, case index and the generated operands."""
    rendered = ", ".join(f"{name}={value!r}" for name, value in operands.items())
    return f"case={case}, rng=random.Random({seed}), {rendered}"


def random_allocation_context(rng: random.Random, horizon: int, banded_basis: bool) -> AllocationContext:
    """A context that exercises the CO2 split, the maintenance split and the levy path (§6)."""
    modernization = cost_band(rng, high=60000.0)
    return AllocationContext(
        horizon_years=horizon,
        building_specific_emissions_in_kg_per_m2_a=rng.choice([None, 5.0, 20.0, 34.0, 60.0]),
        heated_floor_area_in_m2=rng.choice([None, 120.0]),
        living_area_in_m2=rng.choice([None, 80.0, 150.0]),
        current_cold_rent_in_euro_per_m2_month=rng.choice([None, 6.0, 9.0]),
        modernization_cost_in_euro=modernization if banded_basis else UncertainValue.exact(modernization.best_estimate),
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
        BEST_ESTIMATE slot, and containment of the system band in the payer sum's band. The regression
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
            # the discounted (L.max - L.min) on each side, and identical in BEST_ESTIMATE.
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
            assert total.best_estimate == pytest.approx(system_npv.best_estimate, abs=1e-6), report
            assert total.minimum == pytest.approx(system_npv.minimum - gap, abs=1e-6), report
            assert total.maximum == pytest.approx(system_npv.maximum + gap, abs=1e-6), report
            if gap > 1e-6:
                widened_cases += 1
        assert widened_cases, "no case actually minted a banded levy — the test would be vacuous"
