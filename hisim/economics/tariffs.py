"""Tariff contracts and the pure billing engine (cost_spec.md §8).

One :class:`TariffContract` per carrier is the single source of truth: the in-simulation
price provider (``hisim/components/tariff_provider.py``) and the postprocessing billing
engine both read it; neither carries its own price data.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDataError, EnergyPriceEntry, SourceRegistry
from hisim.economics.facts import BillingDeterminants
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue

#: Default location of shipped tariff contracts.
DEFAULT_TARIFFS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database", "tariffs"
)


class SupplyKind(str, enum.Enum):
    """Supply price structures (§8.2)."""

    FLAT = "FLAT"
    TIME_OF_USE = "TIME_OF_USE"
    DYNAMIC = "DYNAMIC"


class CapacityChargeKind(str, enum.Enum):
    """Capacity charge structures (§8.2)."""

    NONE = "NONE"
    ANNUAL_PEAK = "ANNUAL_PEAK"
    MONTHLY_PEAK = "MONTHLY_PEAK"
    PEAK_WINDOW = "PEAK_WINDOW"


class FeedInKind(str, enum.Enum):
    """Feed-in remuneration structures (§8.2)."""

    NONE = "NONE"
    FIXED_TARIFF = "FIXED_TARIFF"  # EEG, nominally constant for its duration
    SPOT_REFERENCED = "SPOT_REFERENCED"  # direct marketing


@dataclass
class TimeOfUseBand:
    """One ToU band: weekday/hour masks with a working price."""

    name: str
    price_in_euro_per_kwh: UncertainValue
    weekdays: List[int] = field(default_factory=lambda: list(range(7)))  # 0 = Monday
    hours: List[int] = field(default_factory=lambda: list(range(24)))


@dataclass
class TariffSupply:
    """Supply side of a contract; non-energy components kept separate (§8.2)."""

    kind: SupplyKind
    # FLAT: the all-in energy price; TIME_OF_USE/DYNAMIC: energy-only parts below.
    working_price_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    bands: List[TimeOfUseBand] = field(default_factory=list)
    spot_series: Optional[str] = None  # reference to a price series in the database
    spot_factor: float = 1.0
    markup_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    grid_fee_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    taxes_and_levies_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    vat_rate: float = 0.0


@dataclass
class CapacityCharge:
    """Capacity charge terms; peaks are billing-interval means, never instantaneous (§8.4)."""

    kind: CapacityChargeKind = CapacityChargeKind.NONE
    price_in_euro_per_kw: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    billing_interval_in_minutes: int = 15
    window_hours: List[int] = field(default_factory=list)  # PEAK_WINDOW only
    window_weekdays: List[int] = field(default_factory=list)


@dataclass
class FeedIn:
    """Feed-in remuneration terms."""

    kind: FeedInKind = FeedInKind.NONE
    rate_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    duration_in_years: int = 20
    spot_factor: float = 1.0
    markup_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


@dataclass
class ControllabilityDiscount:
    """§14a-EnWG-style grid-fee discount, as data only (v1, spec Q19)."""

    kind: str = "NONE"  # NONE | FIXED_ANNUAL | GRID_FEE_SHARE
    annual_amount_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    grid_fee_reduction_share: float = 0.0


@dataclass
class TariffContract:
    """One tariff contract per carrier (§8.2). Data-driven, referenced by id."""

    id: str
    carrier: EnergyCarrier
    country: str
    region: Optional[str]
    valid_from_year: int
    supply: TariffSupply
    standing_charge_in_euro_per_year: UncertainValue
    capacity_charge: CapacityCharge = field(default_factory=CapacityCharge)
    feed_in: FeedIn = field(default_factory=FeedIn)
    controllability_discount: ControllabilityDiscount = field(default_factory=ControllabilityDiscount)
    source_ids: Tuple[str, ...] = ()
    is_default_contract: bool = False  # generated from the §3.5 price entries

    @classmethod
    def default_from_price_entry(cls, entry: EnergyPriceEntry, country: str) -> "TariffContract":
        """The default flat contract generated from a §3.5 energy price entry (behavioral no-op)."""
        return cls(
            id=f"{country}_DEFAULT_{entry.carrier.value}_{entry.year}",
            carrier=entry.carrier,
            country=country,
            region=None,
            valid_from_year=entry.year,
            supply=TariffSupply(
                kind=SupplyKind.FLAT,
                working_price_in_euro_per_kwh=entry.working_price_in_euro_per_kwh,
            ),
            standing_charge_in_euro_per_year=entry.standing_charge_in_euro_per_year,
            source_ids=entry.source_ids,
            is_default_contract=True,
        )

    @classmethod
    def from_json(cls, raw: dict, registry: Optional[SourceRegistry] = None) -> "TariffContract":
        """Parses a tariff contract JSON (§8.2 schema)."""
        contract_id = raw.get("id", "<missing id>")
        source_ids = tuple(raw.get("source_ids", ()))
        if not source_ids:
            raise CostDataError(f"Tariff {contract_id}: source_ids are mandatory (§3.10).")
        if registry is not None:
            registry.resolve(source_ids, f"tariff {contract_id}")
        supply_raw = raw["supply"]
        bands = [
            TimeOfUseBand(
                name=band["name"],
                price_in_euro_per_kwh=UncertainValue.from_json(band["price_in_euro_per_kwh"]),
                weekdays=band.get("weekdays", list(range(7))),
                hours=band.get("hours", list(range(24))),
            )
            for band in supply_raw.get("bands", [])
        ]
        formula = supply_raw.get("formula", {})
        supply = TariffSupply(
            kind=SupplyKind(supply_raw["kind"]),
            working_price_in_euro_per_kwh=UncertainValue.from_json(
                supply_raw.get("working_price_in_euro_per_kwh", 0.0)
            ),
            bands=bands,
            spot_series=supply_raw.get("spot_series"),
            spot_factor=float(formula.get("spot_factor", 1.0)),
            markup_in_euro_per_kwh=UncertainValue.from_json(formula.get("markup_in_euro_per_kwh", 0.0)),
            grid_fee_in_euro_per_kwh=UncertainValue.from_json(supply_raw.get("grid_fee_in_euro_per_kwh", 0.0)),
            taxes_and_levies_in_euro_per_kwh=UncertainValue.from_json(
                supply_raw.get("taxes_and_levies_in_euro_per_kwh", 0.0)
            ),
            vat_rate=float(supply_raw.get("vat_rate", 0.0)),
        )
        capacity_raw = raw.get("capacity_charge", {"kind": "NONE"})
        capacity = CapacityCharge(
            kind=CapacityChargeKind(capacity_raw.get("kind", "NONE")),
            price_in_euro_per_kw=UncertainValue.from_json(capacity_raw.get("price_in_euro_per_kw", 0.0)),
            billing_interval_in_minutes=int(capacity_raw.get("billing_interval_in_minutes", 15)),
            window_hours=capacity_raw.get("window_hours", []),
            window_weekdays=capacity_raw.get("window_weekdays", []),
        )
        feed_in_raw = raw.get("feed_in", {"kind": "NONE"})
        feed_in = FeedIn(
            kind=FeedInKind(feed_in_raw.get("kind", "NONE")),
            rate_in_euro_per_kwh=UncertainValue.from_json(feed_in_raw.get("rate_in_euro_per_kwh", 0.0)),
            duration_in_years=int(feed_in_raw.get("duration_in_years", 20)),
            spot_factor=float(feed_in_raw.get("spot_factor", 1.0)),
            markup_in_euro_per_kwh=UncertainValue.from_json(feed_in_raw.get("markup_in_euro_per_kwh", 0.0)),
        )
        discount_raw = raw.get("controllability_discount", {"kind": "NONE"})
        discount = ControllabilityDiscount(
            kind=discount_raw.get("kind", "NONE"),
            annual_amount_in_euro=UncertainValue.from_json(discount_raw.get("annual_amount_in_euro", 0.0)),
            grid_fee_reduction_share=float(discount_raw.get("grid_fee_reduction_share", 0.0)),
        )
        jurisdiction = raw.get("jurisdiction", {})
        return cls(
            id=contract_id,
            carrier=EnergyCarrier(raw["carrier"]),
            country=jurisdiction.get("country", "DE"),
            region=jurisdiction.get("region"),
            valid_from_year=int(raw.get("valid_from_year", 0)),
            supply=supply,
            standing_charge_in_euro_per_year=UncertainValue.from_json(raw.get("standing_charge_in_euro_per_year", 0.0)),
            capacity_charge=capacity,
            feed_in=feed_in,
            controllability_discount=discount,
            source_ids=source_ids,
        )

    def marginal_purchase_price_components(self) -> UncertainValue:
        """Additive non-spot per-kWh components (markup + grid fee + taxes), §8.4."""
        grid_fee = self.supply.grid_fee_in_euro_per_kwh
        if self.controllability_discount.kind == "GRID_FEE_SHARE":
            grid_fee = grid_fee.scale(1.0 - self.controllability_discount.grid_fee_reduction_share)
        return self.supply.markup_in_euro_per_kwh + grid_fee + self.supply.taxes_and_levies_in_euro_per_kwh


def load_tariff_contract(contract_id: str, base_path: Optional[str] = None) -> TariffContract:
    """Loads one contract JSON by id from the tariffs directory."""
    base = base_path or DEFAULT_TARIFFS_PATH
    path = os.path.join(base, f"{contract_id}.json")
    if not os.path.isfile(path):
        raise CostDataError(f"No tariff contract {contract_id!r} at {path}.")
    with open(path, encoding="utf-8") as file:
        return TariffContract.from_json(json.load(file))


def load_spot_series(series_id: str, base_path: Optional[str] = None) -> List[float]:
    """Loads a spot price series (EUR/kWh, hourly) from the cost database.

    Series are versioned CSVs under ``cost_database/spot_series/<id>.csv`` with one price per
    line (header allowed). A documented loader for user-supplied CSVs (spec Q16).
    """
    base = base_path or os.path.join(os.path.dirname(DEFAULT_TARIFFS_PATH), "spot_series")
    path = os.path.join(base, f"{series_id}.csv")
    if not os.path.isfile(path):
        raise CostDataError(f"No spot price series {series_id!r} at {path}.")
    prices: List[float] = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            token = line.strip().split(",")[-1]
            if not token:
                continue
            try:
                prices.append(float(token))
            except ValueError:
                continue  # header line
    if not prices:
        raise CostDataError(f"Spot price series {series_id!r} is empty.")
    return prices


def synthetic_reference_spot_series(mean_price: float = 0.08, amplitude: float = 0.04) -> List[float]:
    """A synthetic hourly reference profile for tests (spec Q16 fallback).

    A daily sine with morning/evening structure; deterministic, mean ≈ `mean_price`.
    """
    import math

    prices = []
    for hour in range(8760):
        hour_of_day = hour % 24
        daily = math.sin((hour_of_day - 4) / 24.0 * 2.0 * math.pi)
        seasonal = 0.2 * math.cos(hour / 8760.0 * 2.0 * math.pi)
        prices.append(max(0.0, mean_price + amplitude * (daily + seasonal)))
    return prices


@dataclass
class Year1Bill:
    """apply_tariff output: year-1 costs by category, each a slot band (§8.4)."""

    by_category: Dict[CostCategory, UncertainValue] = field(default_factory=dict)
    # Decomposition for the horizon projection (§8.5), AVERAGE-slot figures:
    volume_effect_in_euro: float = 0.0  # E_bought x mean price
    flexibility_value_in_euro: float = 0.0  # savings of load shifting vs the mean price
    mean_energy_price_in_euro_per_kwh: float = 0.0

    def total(self) -> UncertainValue:
        """Signed sum of all categories."""
        return UncertainValue.sum(self.by_category.values())


def validate_billing_interval(seconds_per_timestep: int, contract: TariffContract) -> None:
    """`seconds_per_timestep` must divide the billing interval, else pre-check fails (§8.4)."""
    if contract.capacity_charge.kind == CapacityChargeKind.NONE:
        return
    interval_seconds = contract.capacity_charge.billing_interval_in_minutes * 60
    if interval_seconds % seconds_per_timestep != 0:
        raise CostDataError(
            f"Tariff {contract.id}: seconds_per_timestep={seconds_per_timestep} does not divide the "
            f"billing interval of {contract.capacity_charge.billing_interval_in_minutes} min (§8.4)."
        )


def apply_tariff(determinants: BillingDeterminants, contract: TariffContract) -> Year1Bill:
    """The billing engine: one pure function (§8.4).

    Property-tested invariants: a flat contract reproduces kWh x price exactly; the capacity
    charge is monotone in every peak. Uncertain additive components shift each slot's bill by
    ``E x delta`` without re-integrating the spot series (§8.4).
    """
    bill = Year1Bill()
    energy_bought = determinants.energy_bought_in_kwh
    supply = contract.supply

    if supply.kind == SupplyKind.FLAT:
        working = supply.working_price_in_euro_per_kwh + contract.marginal_purchase_price_components()
        bill.by_category[CostCategory.ENERGY_WORKING] = working.scale(energy_bought)
        bill.mean_energy_price_in_euro_per_kwh = working.average
        bill.volume_effect_in_euro = energy_bought * working.average
        bill.flexibility_value_in_euro = 0.0
    elif supply.kind == SupplyKind.TIME_OF_USE:
        if not supply.bands:
            raise CostDataError(f"Tariff {contract.id}: TIME_OF_USE without bands.")
        total = UncertainValue.exact(0.0)
        banded_energy = 0.0
        for band in supply.bands:
            band_energy = determinants.energy_bought_per_band_in_kwh.get(band.name, 0.0)
            banded_energy += band_energy
            total = total + band.price_in_euro_per_kwh.scale(band_energy)
        unbanded = energy_bought - banded_energy
        if unbanded > 1e-6:
            # Bill unbanded energy at the first band's price and let the meter warn upstream.
            total = total + supply.bands[0].price_in_euro_per_kwh.scale(unbanded)
        additive = contract.marginal_purchase_price_components().scale(energy_bought)
        working = total + additive
        bill.by_category[CostCategory.ENERGY_WORKING] = working
        bill.mean_energy_price_in_euro_per_kwh = working.average / energy_bought if energy_bought else 0.0
        bill.volume_effect_in_euro = working.average
        bill.flexibility_value_in_euro = 0.0
    else:  # DYNAMIC
        if determinants.cost_integrated_in_euro is None:
            raise CostDataError(
                f"Tariff {contract.id}: DYNAMIC supply needs the natively integrated cost "
                "(load x price series) in the billing determinants (§8.4)."
            )
        # Energy-only integral (spot x factor), exact (§3.9); additive components per slot.
        spot_cost = determinants.cost_integrated_in_euro
        additive = contract.marginal_purchase_price_components().scale(energy_bought)
        bill.by_category[CostCategory.ENERGY_WORKING] = UncertainValue.exact(spot_cost) + additive
        mean_spot = spot_cost / energy_bought if energy_bought else 0.0
        bill.mean_energy_price_in_euro_per_kwh = mean_spot + contract.marginal_purchase_price_components().average
        # Decomposition (§8.5): volume effect at the year's average price; the difference
        # between paying the average and the integral is the flexibility value.
        # Additive components are volume-proportional and carry no flexibility.
        if determinants.mean_spot_price_in_euro_per_kwh is not None:
            # The meter passed the year's unweighted mean spot price, so the flexibility value
            # (what load shifting saved vs. paying the average price) is separable (§8.5).
            mean_spot_unweighted = determinants.mean_spot_price_in_euro_per_kwh
            bill.volume_effect_in_euro = energy_bought * (
                mean_spot_unweighted + contract.marginal_purchase_price_components().average
            )
            bill.flexibility_value_in_euro = energy_bought * mean_spot_unweighted - spot_cost
        else:
            mean_price_for_volume = spot_cost / energy_bought if energy_bought else 0.0
            bill.volume_effect_in_euro = energy_bought * mean_price_for_volume
            bill.flexibility_value_in_euro = 0.0

    # Standing charge and controllability discount.
    standing = contract.standing_charge_in_euro_per_year
    if contract.controllability_discount.kind == "FIXED_ANNUAL":
        standing = standing + contract.controllability_discount.annual_amount_in_euro.as_revenue()
    bill.by_category[CostCategory.ENERGY_STANDING] = standing

    # Capacity charge: monotone in every peak (§8.4).
    capacity = contract.capacity_charge
    if capacity.kind != CapacityChargeKind.NONE:
        if capacity.kind == CapacityChargeKind.ANNUAL_PEAK:
            peak_sum = determinants.annual_peak_in_kw
        else:  # MONTHLY_PEAK and PEAK_WINDOW: the meter supplies the relevant period peaks
            peak_sum = sum(determinants.peak_per_billing_period_in_kw)
        bill.by_category[CostCategory.ENERGY_CAPACITY_CHARGE] = capacity.price_in_euro_per_kw.scale(peak_sum)

    # Feed-in revenue (negative).
    if contract.feed_in.kind != FeedInKind.NONE and determinants.energy_sold_in_kwh > 0:
        if contract.feed_in.kind == FeedInKind.FIXED_TARIFF:
            revenue = contract.feed_in.rate_in_euro_per_kwh.scale(determinants.energy_sold_in_kwh)
        else:  # SPOT_REFERENCED
            if determinants.revenue_integrated_in_euro is not None:
                revenue = UncertainValue.exact(determinants.revenue_integrated_in_euro) + (
                    contract.feed_in.markup_in_euro_per_kwh.scale(determinants.energy_sold_in_kwh)
                )
            else:
                revenue = contract.feed_in.rate_in_euro_per_kwh.scale(determinants.energy_sold_in_kwh)
        bill.by_category[CostCategory.FEED_IN_REVENUE] = revenue.as_revenue()

    return bill


def tariff_counterfactual(
    determinants: BillingDeterminants, active: TariffContract, flat: TariffContract
) -> Dict[str, UncertainValue]:
    """Bills the *same* load profile under a flat contract (§8.5 counterfactual 1)."""
    active_bill = apply_tariff(determinants, active)
    flat_bill = apply_tariff(determinants, flat)
    return {
        "active_total_in_euro": active_bill.total(),
        "flat_total_in_euro": flat_bill.total(),
        "tariff_advantage_in_euro": flat_bill.total() - active_bill.total(),
    }
