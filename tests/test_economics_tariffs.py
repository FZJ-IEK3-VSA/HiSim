"""Unit tests for the tariff engine (§8).

**Scope.** The pure year-1 billing function `apply_tariff` and its surroundings, needing
nothing above the rule engines: supply kinds (flat, time-of-use, dynamic on the synthetic
reference profile), capacity charges, feed-in and the §8.5 decomposition, and
`validate_billing_interval`. This file and `test_economics_subsidies.py` were one file until
the PR-3 review asked for the split; the placement rule is unchanged — a test that imports any
module above the rule engines (`evaluator`, `perspectives`, `scenarios`, `financing`,
`results`, `views`) belongs in `test_economics_subsidy_integration.py` instead.

**How it covers it.** Against synthetic contracts built by `make_flat_contract` and variants,
with round numbers, so every expected bill is a product a reviewer can check by eye
(0.8 x 1 800). The dynamic-tariff cases run on the deterministic synthetic reference profile,
which is what makes exact-bill assertions possible at all.

**Error class.** A failure here is a billing *formula* failure — the engine turned metered
determinants into the wrong year-1 bill — isolated from pricing data by the synthetic
contracts.
"""

# clean


import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import BillingDeterminants
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

pytestmark = pytest.mark.base

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
        assert bill.by_category[CostCategory.ENERGY_WORKING].best_estimate == pytest.approx(4321.0 * 0.25)

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
            assert higher.total().best_estimate > base.total().best_estimate

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
        assert bill.by_category[CostCategory.ENERGY_WORKING].best_estimate == pytest.approx(70.0 + 1000.0 * 0.02)
        assert bill.flexibility_value_in_euro == pytest.approx(1000.0 * 0.08 - 70.0)

    def test_uncertain_additive_components_shift_slots(self):
        """Additive per-kWh bands shift each slot by E x delta without re-integration (§8.4)."""
        contract = make_flat_contract()
        contract.supply = TariffSupply(
            kind=SupplyKind.DYNAMIC,
            spot_series="test",
            markup_in_euro_per_kwh=UncertainValue(best_estimate=0.02, minimum=0.01, maximum=0.04),
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
        assert bill.by_category[CostCategory.FEED_IN_REVENUE].best_estimate == pytest.approx(-40.0)

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
        assert revenue.best_estimate == pytest.approx(-(100.0 * 0.8 + 500.0 * 0.01))
        contract.feed_in.spot_factor = 1.0
        unscaled = apply_tariff(determinants, contract).by_category[CostCategory.FEED_IN_REVENUE]
        assert unscaled.best_estimate == pytest.approx(-(100.0 + 500.0 * 0.01))

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
        assert outcome["tariff_advantage_in_euro"].best_estimate == pytest.approx(300.0 - 200.0)

    def test_synthetic_series_is_deterministic_and_hourly(self):
        """The Q16 fallback profile."""
        series = synthetic_reference_spot_series()
        assert len(series) == 8760
        assert series == synthetic_reference_spot_series()
        assert min(series) >= 0.0


