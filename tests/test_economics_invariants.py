"""Property tests: uncertainty bands and timeline aggregation (cost_spec.md §3.6, §3.9).

First of the three invariant test files (split per the PR-3 review's 500-line rule; the
hypothesis strategies each file needs travel with it). Band arithmetic properties (ordering,
mirroring, slot-wise linearity) and timeline aggregation properties (NPV linearity, pivot
consistency). Tariff and subsidy bound properties are in
`test_economics_invariants_rules.py`; allocation, money conservation and the
numpy-financial cross-check are in `test_economics_invariants_allocation.py`.
"""

import random
from typing import (
    Callable,
    Dict,
    List,
    Tuple,
)
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.timeline import (
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
)
from hisim.economics.uncertainty import UncertainValue

pytestmark = pytest.mark.base

CASES = 100

SEED_BANDS = 20260812

SEED_TIMELINE = 20260813

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

SLOTS = ("minimum", "best_estimate", "maximum")


def coherent_band(rng: random.Random, low: float = -2000.0, high: float = 2000.0) -> UncertainValue:
    """A random band satisfying min <= best_estimate <= max."""
    values = sorted(rng.uniform(low, high) for _ in range(3))
    return UncertainValue(best_estimate=values[1], minimum=values[0], maximum=values[2])


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


class TestUncertaintyBandProperties:
    """§5.1: slot ordering, revenue mirroring, envelope subtraction, slot-wise caps."""

    def test_slot_ordering_survives_every_operation(self):
        """The ordering min <= best_estimate <= max holds after +, -, scale, multiply_band, clamp_upper and sum."""
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
            # Loop variables are bound as lambda defaults: the operations run inside this same
            # iteration, but the explicit binding keeps the closure honest (cell-var-from-loop).
            for name, operation in (
                ("add", lambda left=left, right=right: left + right),
                ("sub", lambda left=left, right=right: left - right),
                ("scale", lambda left=left, factor=factor: left.scale(factor)),
                (
                    "multiply_band",
                    lambda cost_left=cost_left, cost_right=cost_right: cost_left.multiply_band(cost_right),
                ),
                ("clamp_upper", lambda left=left, cap=cap: left.clamp_upper(cap)),
                (
                    "sum",
                    lambda left=left, right=right, cost_left=cost_left, cost_right=cost_right: UncertainValue.sum(
                        [left, right, cost_left, cost_right]
                    ),
                ),
                ("as_revenue", left.as_revenue),
            ):
                try:
                    results[name] = operation()
                except ValueError as error:  # the band invariant is enforced in __post_init__
                    pytest.fail(f"{name} broke the band invariant: {error} — {context}")
                band = results[name]
                assert band.minimum <= band.best_estimate <= band.maximum, (
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
            assert revenue.best_estimate == -band.best_estimate, f"best estimate not mirrored — {context}"
            assert revenue.maximum == -band.minimum, f"maximum not mirrored — {context}"
            assert slot_values(revenue.as_revenue()) == slot_values(band), (
                f"as_revenue is not an involution: {slot_values(revenue.as_revenue())} — {context}"
            )

    def test_subtraction_is_the_envelope_of_the_three_slot_deltas(self):
        """§3.9: the result envelopes LOW-world, BEST_ESTIMATE and HIGH-world deltas."""
        for case in range(CASES):
            seed = SEED_BANDS + case
            rng = random.Random(seed)
            left = coherent_band(rng)
            right = coherent_band(rng)
            context = case_context(seed, case, left=left, right=right)
            deltas = (
                left.minimum - right.minimum,
                left.best_estimate - right.best_estimate,
                left.maximum - right.maximum,
            )
            difference = left - right
            assert difference.best_estimate == pytest.approx(deltas[1]), f"best-estimate delta wrong — {context}"
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
            dropped_npv = timeline.filtered(lambda entry, dropped=dropped: entry.category in dropped).npv(rate)
            assert_bands_equal(kept_npv + dropped_npv, timeline.npv(rate), context)
