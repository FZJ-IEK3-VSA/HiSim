"""Tariff contracts and the pure billing engine (cost_spec.md §8).

One :class:`TariffContract` per energy carrier is the single price source for both the in-simulation price provider
(``hisim/components/tariff_provider.py``) and the billing engine, so a controller never optimizes against a different
price than the one billed (§8.1). The module bills one year of one carrier (:func:`apply_tariff`); escalation over the
horizon lives in ``calculators/energy.py``.

Contracts loaded from ``cost_database/tariffs/*.json`` must cite ids from ``sources.json``. Contracts built in memory
(tests, worked examples, the provider's ``SYNTHETIC_TEST``) cite sources as ``inline:<citation>``, which is rendered
verbatim as an INLINE source and never appears in shipped data.
"""

from __future__ import annotations

import enum
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDataError, SourceRegistry
from hisim.economics.facts import BillingDeterminants
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue

# =========================================================================== data
# Contract data, the §8.2 schema and catalog loading; this half knows nothing about bills. `TariffContract`'s
# `marginal_purchase_price_components` stays here because it is derived from the contract's own fields.


class SupplyKind(str, enum.Enum):
    """How the energy part of the price varies over time (§8.2).

    FLAT needs annual kWh only, TIME_OF_USE needs kWh per time-of-use band, and DYNAMIC needs the integral of load
    times the spot price series at native resolution. The kind selects the price rule
    (:func:`energy_price_in_euro_per_kwh`) and which billing determinants the meter must produce.
    """

    FLAT = "FLAT"
    TIME_OF_USE = "TIME_OF_USE"
    DYNAMIC = "DYNAMIC"


class CapacityChargeKind(str, enum.Enum):
    """Which power peaks a capacity (demand) charge bills (§8.2).

    The single annual maximum, one peak per month, or only peaks inside declared high-load windows. A peak is always
    the mean power over the billing interval, never an instantaneous value (§8.4).
    """

    NONE = "NONE"
    ANNUAL_PEAK = "ANNUAL_PEAK"
    MONTHLY_PEAK = "MONTHLY_PEAK"
    PEAK_WINDOW = "PEAK_WINDOW"


class FeedInKind(str, enum.Enum):
    """How exported energy is paid for (§8.2).

    A fixed statutory tariff stays nominally constant for its contract duration; spot-referenced direct marketing
    follows the market. ``calculators/energy.py`` holds the year-1 revenue nominally fixed for ``duration_in_years``
    and escalates it only afterwards (§8.5).
    """

    NONE = "NONE"
    FIXED_TARIFF = "FIXED_TARIFF"  # EEG, nominally constant for its duration
    SPOT_REFERENCED = "SPOT_REFERENCED"  # direct marketing


class ControllabilityKind(str, enum.Enum):
    """How a grid operator pays for the right to curtail a dimmable device, as under §14a EnWG (§8.2).

    Not at all, as a fixed annual credit against the standing charge, or as a percentage off the grid fee. The kind
    selects which amount field of :class:`ControllabilityDiscount` is read.
    """

    NONE = "NONE"
    FIXED_ANNUAL = "FIXED_ANNUAL"  # flat annual credit, booked against the standing charge
    GRID_FEE_SHARE = "GRID_FEE_SHARE"  # fraction taken off the grid-fee component


@dataclass
class TimeOfUseBand:
    """One time-of-use band: a named set of weekday/hour masks with its own working price.

    The name must match the band names the meter reports energy under; :func:`apply_tariff` refuses energy filed under
    a name the contract does not define. Masks may overlap and the first declared match wins
    (:func:`time_of_use_band_for`), so a catch-all band declared last expresses "everything else".
    """

    name: str  # must match the key the meter reports energy under
    price_in_euro_per_kwh: UncertainValue  # energy-only; the additive components are added on top
    weekdays: List[int] = field(default_factory=lambda: list(range(7)))  # 0 = Monday
    hours: List[int] = field(default_factory=lambda: list(range(24)))  # local clock hour of the day


@dataclass
class TariffSupply:
    """Supply side of a contract: what one purchased kWh costs (§8.2).

    The time-varying energy part (flat price, bands, or spot series times a factor) is kept apart from three per-kWh
    components that do not vary with time: supplier markup, grid fee, and taxes and levies. The split lets a §14a
    discount reduce the grid fee alone and lets the flexibility decomposition use the energy part only. The
    macroeconomic view strips taxes via the price entry's ``tax_and_levy_share``, not via these fields.
    """

    kind: SupplyKind
    # FLAT: the all-in energy price; TIME_OF_USE/DYNAMIC: energy-only parts below.
    working_price_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    bands: List[TimeOfUseBand] = field(default_factory=list)  # TIME_OF_USE only
    spot_series: Optional[str] = None  # reference to a price series in the database
    spot_factor: float = 1.0  # DYNAMIC: multiplier on the spot price (1.0 = pass-through)
    markup_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    grid_fee_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    taxes_and_levies_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    vat_rate: float = 0.0


@dataclass
class CapacityCharge:
    """Capacity charge terms: a price per kW applied to the measured peaks (§8.4).

    Peaks are mean power over the billing interval, never instantaneous; a 15-minute mean is very different from a
    one-minute spike. The interval must be a whole multiple of the simulation timestep, checked before the run by
    :func:`validate_billing_interval`.
    """

    kind: CapacityChargeKind = CapacityChargeKind.NONE
    price_in_euro_per_kw: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    billing_interval_in_minutes: int = 15  # peaks are means over this interval
    window_hours: List[int] = field(default_factory=list)  # PEAK_WINDOW only
    window_weekdays: List[int] = field(default_factory=list)  # PEAK_WINDOW only


@dataclass
class FeedIn:
    """Feed-in remuneration terms: what each sold kWh earns, for how long, and on what basis.

    Under ``SPOT_REFERENCED`` the payment is `spot_factor` times the natively integrated spot revenue plus
    `markup_in_euro_per_kwh` per kWh sold. The factor is the share of the spot proceeds the direct marketer passes on
    (1.0 = all) and applies to the spot term only.
    """

    kind: FeedInKind = FeedInKind.NONE
    rate_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    duration_in_years: int = 20  # nominal-fixed period for FIXED_TARIFF (EEG convention)
    spot_factor: float = 1.0  # SPOT_REFERENCED: share of the spot proceeds paid out (1.0 = all)
    markup_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


@dataclass
class ControllabilityDiscount:
    """Grid-fee discount for dimmable devices under §14a EnWG, money side only.

    Operators of heat pumps or wallboxes get a fixed annual credit or a percentage off the grid fee in exchange for
    accepting curtailment. The curtailment itself is not simulated, so a run that claims the discount overstates its
    benefit.
    """

    kind: ControllabilityKind = ControllabilityKind.NONE
    annual_amount_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    grid_fee_reduction_share: float = 0.0  # GRID_FEE_SHARE: fraction taken off the grid fee

    def __post_init__(self) -> None:
        """Coerce a plain string kind such as ``"GRID_FEE_SHARE"`` to `ControllabilityKind`.

        Raises:
            ValueError: If the kind names no `ControllabilityKind` member.
        """
        self.kind = ControllabilityKind(self.kind)


@dataclass
class TariffContract:
    """One tariff contract for one energy carrier, referenced by id (§8.2).

    Holds the supply price structure, standing charge, capacity charge, feed-in terms and any controllability discount;
    both the simulation and the billing engine read the same object. Contracts come from
    ``cost_database/tariffs/<id>.json`` (the file name is the id), from in-memory construction in tests and examples,
    or, for a carrier without a contract, from ``calculators/energy.default_contract``, which builds a flat contract
    from the §3.5 price entries and sets ``is_default_contract``.
    """

    #: Default location of shipped tariff contracts.
    DEFAULT_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database", "tariffs"
    )

    #: Infix reserved for the ids of contracts synthesized from the §3.5 price entries. A catalog
    #: file must not use it, or its contract would be mistaken for a synthesized one.
    DEFAULT_ID_INFIX: ClassVar[str] = "_DEFAULT_"

    id: str  # equals the file name for catalog contracts
    carrier: EnergyCarrier
    country: str
    region: Optional[str]  # NUTS code or None for nationwide
    valid_from_year: int
    supply: TariffSupply
    standing_charge_in_euro_per_year: UncertainValue  # Grundpreis, independent of consumption
    capacity_charge: CapacityCharge = field(default_factory=CapacityCharge)
    feed_in: FeedIn = field(default_factory=FeedIn)
    controllability_discount: ControllabilityDiscount = field(default_factory=ControllabilityDiscount)
    source_ids: Tuple[str, ...] = ()
    is_default_contract: bool = False  # generated from the §3.5 price entries

    @classmethod
    def default_contract_id(cls, country: str, carrier: EnergyCarrier, year: int) -> str:
        """Return the id of a contract synthesized from the §3.5 price entries.

        `calculators/energy.contract_from_price_entry` mints it and `serialization.contracts_from_json` recognizes it
        in an archived file, so the format lives in one place.

        Args:
            country: Country code the price entries were read for.
            carrier: The carrier the contract bills.
            year: The price entry's year.

        Returns:
            The synthesized contract's id.
        """
        return f"{country}{cls.DEFAULT_ID_INFIX}{carrier.value}_{year}"

    @classmethod
    def is_default_contract_id(cls, contract_id: str) -> bool:
        """Return whether the id has the shape :meth:`default_contract_id` mints.

        A synthesized contract has no catalog file, so a reader holding only its id needs this to recognize it. The
        whole shape is checked (country, reserved infix, a real carrier value, a numeric year), so a catalog id that
        merely contains the infix is not mistaken for one.

        Args:
            contract_id: The id to classify.

        Returns:
            True if the id has the synthesized-default shape.
        """
        country, _, tail = contract_id.partition(cls.DEFAULT_ID_INFIX)
        carrier_value, _, year = tail.rpartition("_")
        return bool(country) and year.isdigit() and carrier_value in {member.value for member in EnergyCarrier}

    @classmethod
    def from_json(cls, raw: dict, registry: Optional[SourceRegistry] = None) -> "TariffContract":
        """Parse a tariff contract from its JSON form (§8.2 schema).

        Used by the file loader, by ``serialization.py`` and by the data-file checks. Monetary fields may be an exact
        number or a min/best_estimate/max band. Only ``supply``, ``carrier`` and ``source_ids`` are required; an absent
        block means no capacity charge, no feed-in or no discount.

        Args:
            raw: The parsed contract JSON.
            registry: Optional source registry; when given, cited ids are resolved immediately.

        Returns:
            The parsed contract.

        Raises:
            CostDataError: If ``source_ids`` is empty or a cited id is unknown to the registry.
            KeyError / ValueError: On a structurally invalid document (missing ``supply`` or ``carrier``, unknown enum
                member).
        """
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
            kind=ControllabilityKind(discount_raw.get("kind", "NONE")),
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
            is_default_contract=bool(raw.get("is_default_contract", False)),
        )

    def marginal_purchase_price_components(self) -> UncertainValue:
        """Return the per-kWh components charged regardless of time: markup + grid fee + taxes, in EUR/kWh (§8.4).

        A ``GRID_FEE_SHARE`` controllability discount is already applied to the grid-fee part. Keeping these apart lets
        an uncertain component shift a whole bill by ``E × Δcomponent`` without re-integrating the spot series. Both
        the price provider and :func:`apply_tariff` add it to the time-varying energy price.
        """
        grid_fee = self.supply.grid_fee_in_euro_per_kwh
        if self.controllability_discount.kind == ControllabilityKind.GRID_FEE_SHARE:
            grid_fee = grid_fee.scale(1.0 - self.controllability_discount.grid_fee_reduction_share)
        return self.supply.markup_in_euro_per_kwh + grid_fee + self.supply.taxes_and_levies_in_euro_per_kwh


def contract_to_json(contract: TariffContract) -> dict:
    """Serialize a contract in the §8.2 catalog schema, the exact inverse of `TariffContract.from_json`.

    Embeds contracts in ``economic_inputs.json`` so a stored result can be re-priced without a catalog lookup.
    The output is a valid ``cost_database/tariffs/*.json`` file.
    """
    supply = contract.supply
    raw: Dict[str, Any] = {
        "id": contract.id,
        "carrier": contract.carrier.value,
        "jurisdiction": {"country": contract.country, "region": contract.region},
        "valid_from_year": contract.valid_from_year,
        "supply": {
            "kind": supply.kind.value,
            "working_price_in_euro_per_kwh": supply.working_price_in_euro_per_kwh.to_json(),
            "bands": [
                {
                    "name": band.name,
                    "price_in_euro_per_kwh": band.price_in_euro_per_kwh.to_json(),
                    "weekdays": list(band.weekdays),
                    "hours": list(band.hours),
                }
                for band in supply.bands
            ],
            "spot_series": supply.spot_series,
            "formula": {
                "spot_factor": supply.spot_factor,
                "markup_in_euro_per_kwh": supply.markup_in_euro_per_kwh.to_json(),
            },
            "grid_fee_in_euro_per_kwh": supply.grid_fee_in_euro_per_kwh.to_json(),
            "taxes_and_levies_in_euro_per_kwh": supply.taxes_and_levies_in_euro_per_kwh.to_json(),
            "vat_rate": supply.vat_rate,
        },
        "standing_charge_in_euro_per_year": contract.standing_charge_in_euro_per_year.to_json(),
        "capacity_charge": {
            "kind": contract.capacity_charge.kind.value,
            "price_in_euro_per_kw": contract.capacity_charge.price_in_euro_per_kw.to_json(),
            "billing_interval_in_minutes": contract.capacity_charge.billing_interval_in_minutes,
            "window_hours": list(contract.capacity_charge.window_hours),
            "window_weekdays": list(contract.capacity_charge.window_weekdays),
        },
        "feed_in": {
            "kind": contract.feed_in.kind.value,
            "rate_in_euro_per_kwh": contract.feed_in.rate_in_euro_per_kwh.to_json(),
            "duration_in_years": contract.feed_in.duration_in_years,
            "spot_factor": contract.feed_in.spot_factor,
            "markup_in_euro_per_kwh": contract.feed_in.markup_in_euro_per_kwh.to_json(),
        },
        "controllability_discount": {
            "kind": contract.controllability_discount.kind.value,
            "annual_amount_in_euro": contract.controllability_discount.annual_amount_in_euro.to_json(),
            "grid_fee_reduction_share": contract.controllability_discount.grid_fee_reduction_share,
        },
        "source_ids": list(contract.source_ids),
    }
    if contract.is_default_contract:
        raw["is_default_contract"] = True
    return raw


def load_tariff_contract(contract_id: str, base_path: Optional[str] = None) -> TariffContract:
    """Load one contract by id from the tariffs directory.

    The id must equal the file's base name. No source registry is passed, so citations are not resolved here;
    ``validation.validate_tariff_contracts`` checks the shipped catalog's sources.

    Args:
        contract_id: The contract id, equal to the file's base name.
        base_path: Directory to load from; defaults to the shipped ``cost_database/tariffs``.

    Returns:
        The parsed contract.

    Raises:
        CostDataError: If no file of that name exists, or the document fails the schema checks.
    """
    base = base_path or TariffContract.DEFAULT_PATH
    path = os.path.join(base, f"{contract_id}.json")
    if not os.path.isfile(path):
        raise CostDataError(f"No tariff contract {contract_id!r} at {path}.")
    with open(path, encoding="utf-8") as file:
        return TariffContract.from_json(json.load(file))


def load_spot_series(series_id: str, base_path: Optional[str] = None) -> List[float]:
    """Load an hourly spot price series in EUR/kWh from ``cost_database/spot_series/<id>.csv``.

    A DYNAMIC contract names its series by id and the simulation-side price provider reads it. Real EPEX series are not
    shipped for licensing reasons, so users supply their own file. The last comma-separated token of each line is read,
    so a one-column file and a ``timestamp,price`` file both work; blank lines and a non-numeric first line (header)
    are skipped. Expect 8760 values for a full year. Any other unparsable line fails the load, because dropping it
    would shift every later price by one hour.

    Args:
        series_id: The series id, equal to the CSV's base name.
        base_path: Directory to load from; defaults to ``cost_database/spot_series``.

    Returns:
        The prices in file order, in EUR/kWh.

    Raises:
        CostDataError: If the file is missing, holds no parsable value, or has a non-empty line past the header that is
            not a price (the message names file and line).
    """
    base = base_path or os.path.join(os.path.dirname(TariffContract.DEFAULT_PATH), "spot_series")
    path = os.path.join(base, f"{series_id}.csv")
    if not os.path.isfile(path):
        raise CostDataError(f"No spot price series {series_id!r} at {path}.")
    prices: List[float] = []
    with open(path, encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            token = line.strip().split(",")[-1]
            if not token:
                continue
            try:
                prices.append(float(token))
            except ValueError as err:
                if line_number == 1:
                    continue  # the column header, the one non-numeric line a series may have
                raise CostDataError(
                    f"Spot price series {path}, line {line_number}: {token!r} is not a price "
                    "(only blank lines and a header on line 1 are skipped)."
                ) from err
    if not prices:
        raise CostDataError(f"Spot price series {series_id!r} is empty.")
    return prices


def synthetic_reference_spot_series(mean_price: float = 0.08, amplitude: float = 0.04) -> List[float]:
    """Return a synthetic hourly spot price profile for tests: a daily sine with morning and evening structure.

    It backs the shipped ``DE_DYNAMIC_SYNTHETIC_2024`` contract, the price provider's ``SYNTHETIC_TEST`` contract
    (series id ``"__synthetic__"``) and the dynamic-tariff tests. It is a deterministic fixture, not data, and must not
    back a published result.

    Args:
        mean_price: Mean level of the profile in EUR/kWh.
        amplitude: Half-swing of the daily shape in EUR/kWh; a seasonal term of 20 % of it is added, and prices are
            floored at zero (so a large amplitude raises the mean).

    Returns:
        8760 hourly prices in EUR/kWh, starting at hour 0 of January 1.
    """
    prices = []
    for hour in range(8760):
        hour_of_day = hour % 24
        daily = math.sin((hour_of_day - 4) / 24.0 * 2.0 * math.pi)
        seasonal = 0.2 * math.cos(hour / 8760.0 * 2.0 * math.pi)
        prices.append(max(0.0, mean_price + amplitude * (daily + seasonal)))
    return prices


def validate_billing_interval(seconds_per_timestep: int, contract: TariffContract) -> None:
    """Check that `seconds_per_timestep` divides the contract's billing interval (§8.4).

    A pre-run check called by the simulation-side price provider; a mismatch fails before the run starts.
    """
    if contract.capacity_charge.kind == CapacityChargeKind.NONE:
        return
    interval_seconds = contract.capacity_charge.billing_interval_in_minutes * 60
    if interval_seconds % seconds_per_timestep != 0:
        raise CostDataError(
            f"Tariff {contract.id}: seconds_per_timestep={seconds_per_timestep} does not divide the "
            f"billing interval of {contract.capacity_charge.billing_interval_in_minutes} min (§8.4)."
        )


# =========================================================================== engine
# The pure year-1 billing engine and the price selection it defines. Building a default contract from a §3.5
# price entry lives with its only caller, `calculators/energy.default_contract`.


@dataclass
class Year1Bill:
    """One carrier's bill for one year, by category, as bands (§8.4); the output of :func:`apply_tariff`.

    Categories are working (energy) cost, standing charge, capacity charge and feed-in revenue, kept apart because each
    escalates at its own rate. An absent category means the contract has no such component, not zero. Costs are
    positive; feed-in revenue is negative (mirrored with ``as_revenue``).

    The three scalar fields are the §8.5 decomposition on the best-estimate slot. Only ``flexibility_value_in_euro`` is
    used downstream: ``calculators/energy.py`` escalates it with the spot spread rate and subtracts it as a positive
    saving.
    """

    by_category: Dict[CostCategory, UncertainValue] = field(default_factory=dict)
    # Decomposition for the horizon projection (§8.5), BEST_ESTIMATE-slot figures:
    volume_effect_in_euro: float = 0.0  # E_bought x mean price
    flexibility_value_in_euro: float = 0.0  # savings of load shifting vs the mean price
    mean_energy_price_in_euro_per_kwh: float = 0.0

    def total(self) -> UncertainValue:
        """Return the signed sum of all categories; a building that exports a lot can total below zero.

        Used for the tariff counterfactual and for reporting; the timeline books the categories separately.
        """
        return UncertainValue.sum(self.by_category.values())


def time_of_use_band_for(
    supply: TariffSupply, weekday: Optional[int] = None, hour: Optional[int] = None
) -> Optional[TimeOfUseBand]:
    """Return the time-of-use band that prices one moment (§8.4).

    The first declared match wins. When nothing matches, or the moment is unknown, the first band applies;
    :func:`apply_tariff` uses the same rule for energy the meter assigned to no band, so the controller's price and the
    billed price agree. Returns None only when the contract declares no bands.
    """
    if not supply.bands:
        return None
    if weekday is not None and hour is not None:
        for band in supply.bands:
            if weekday in band.weekdays and hour in band.hours:
                return band
    return supply.bands[0]


def energy_price_in_euro_per_kwh(
    contract: TariffContract,
    weekday: Optional[int] = None,
    hour: Optional[int] = None,
    spot_price_in_euro_per_kwh: Optional[float] = None,
) -> UncertainValue:
    """Return the energy-only price of one kWh at one moment, before the additive components (§8.4).

    FLAT reads the working price, TIME_OF_USE the band covering the moment, DYNAMIC the given spot price times the
    contract's `spot_factor`. Only this part varies with time, which is what the flexibility decomposition needs.

    Args:
        contract: The contract to price from.
        weekday: 0 = Monday; with `hour` it selects the time-of-use band. Both may be None, and then the first band
            applies.
        hour: Clock hour of the day, 0..23.
        spot_price_in_euro_per_kwh: The spot price of this moment, required for DYNAMIC supply.

    Returns:
        EUR/kWh as a band. DYNAMIC returns an exact value: the spot series is simulation input and stays exact (§3.9),
            so a dynamic contract's uncertainty is all in its additive components.

    Raises:
        CostDataError: If a DYNAMIC contract is priced without a spot price.
    """
    supply = contract.supply
    if supply.kind == SupplyKind.FLAT:
        return supply.working_price_in_euro_per_kwh
    if supply.kind == SupplyKind.TIME_OF_USE:
        band = time_of_use_band_for(supply, weekday, hour)
        return band.price_in_euro_per_kwh if band is not None else UncertainValue.exact(0.0)
    if spot_price_in_euro_per_kwh is None:
        raise CostDataError(
            f"Tariff {contract.id}: a DYNAMIC contract needs the spot price of the moment to "
            "price a kWh (§8.4)."
        )
    return UncertainValue.exact(spot_price_in_euro_per_kwh * supply.spot_factor)


def marginal_purchase_price_in_euro_per_kwh(
    contract: TariffContract,
    weekday: Optional[int] = None,
    hour: Optional[int] = None,
    spot_price_in_euro_per_kwh: Optional[float] = None,
) -> UncertainValue:
    """Return what one more kWh costs at one moment: energy price plus the additive components (§8.4).

    The price provider publishes this per timestep and :func:`apply_tariff` bills with the same rule, so a control
    decision and its bill cannot drift apart.
    """
    return energy_price_in_euro_per_kwh(
        contract, weekday, hour, spot_price_in_euro_per_kwh
    ) + contract.marginal_purchase_price_components()


def apply_tariff(determinants: BillingDeterminants, contract: TariffContract) -> Year1Bill:
    """Bill one year of one carrier under a contract (§8.4).

    This pure function (no database, ledger or state) is the only place where measured consumption becomes money, so it
    also re-prices stored results and bills hypothetical contracts (:func:`tariff_counterfactual`). A flat contract
    reproduces kWh × price exactly, and the capacity charge is monotone in every peak.

    Inputs: ``determinants`` describe one full year of one carrier: energy in kWh (bought, sold, and per band name),
    the integrated dynamic cost and revenue in euro, peaks in kW as billing-interval means, and the unweighted mean
    spot price in EUR/kWh. A shorter simulation must be annualized first.

    Billing per supply kind: FLAT multiplies the marginal price by annual energy. TIME_OF_USE prices each band's energy
    at its own price and bills energy assigned to no band at the fallback (first) band's price; energy under an unknown
    band name is refused. DYNAMIC requires the integral computed at native resolution, since kWh times the average
    price is exactly the error a dynamic tariff exploits. Only DYNAMIC reports a flexibility value, and only when the
    mean spot price is given. VAT is never added or removed; ``supply.vat_rate`` has no effect on the bill.

    Args:
        determinants: One carrier's annual billing determinants, already annualized.
        contract: The tariff contract to bill under.

    Returns:
        The year-1 bill: ``ENERGY_WORKING`` (positive), ``ENERGY_STANDING`` (less a FIXED_ANNUAL controllability
            credit), ``ENERGY_CAPACITY_CHARGE`` (only if the contract has one), ``FEED_IN_REVENUE`` (negative, only if
            something was sold), plus the §8.5 decomposition.

    Raises:
        CostDataError: For a TIME_OF_USE contract without bands or whose determinants name an unknown band, or a
            DYNAMIC contract whose determinants carry no integrated cost.
    """
    bill = Year1Bill()
    energy_bought = determinants.energy_bought_in_kwh
    supply = contract.supply

    if supply.kind == SupplyKind.FLAT:
        working = marginal_purchase_price_in_euro_per_kwh(contract)
        bill.by_category[CostCategory.ENERGY_WORKING] = working.scale(energy_bought)
        bill.mean_energy_price_in_euro_per_kwh = working.best_estimate
        bill.volume_effect_in_euro = energy_bought * working.best_estimate
        bill.flexibility_value_in_euro = 0.0
    elif supply.kind == SupplyKind.TIME_OF_USE:
        if not supply.bands:
            raise CostDataError(f"Tariff {contract.id}: TIME_OF_USE without bands.")
        known_bands = [band.name for band in supply.bands]
        unknown = sorted(set(determinants.energy_bought_per_band_in_kwh) - set(known_bands))
        if unknown:
            raise CostDataError(
                f"Tariff {contract.id}: the billing determinants file energy under band name(s) "
                f"{unknown}, which this contract does not define; its bands are {known_bands}. "
                "Energy under an unknown name would fall through to the fallback band and be "
                "billed at the wrong price, so a name mismatch between the meter and the contract "
                "is a data error (§8.4). Rename the band in whichever of the two is wrong, or "
                "report the energy without a band name to have it billed at the fallback band."
            )
        total = UncertainValue.exact(0.0)
        banded_energy = 0.0
        for band in supply.bands:
            band_energy = determinants.energy_bought_per_band_in_kwh.get(band.name, 0.0)
            banded_energy += band_energy
            total = total + band.price_in_euro_per_kwh.scale(band_energy)
        unbanded = energy_bought - banded_energy
        if unbanded > 1e-6:
            # Energy the meter assigned to no band at all (total minus the banded sum) is billed at the
            # fallback band, as the price provider does for an unmatched moment. Energy under an unknown band
            # name was rejected above.
            fallback = time_of_use_band_for(supply)
            assert fallback is not None  # bands were checked above
            total = total + fallback.price_in_euro_per_kwh.scale(unbanded)
        additive = contract.marginal_purchase_price_components().scale(energy_bought)
        working = total + additive
        bill.by_category[CostCategory.ENERGY_WORKING] = working
        bill.mean_energy_price_in_euro_per_kwh = working.best_estimate / energy_bought if energy_bought else 0.0
        bill.volume_effect_in_euro = working.best_estimate
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
        bill.mean_energy_price_in_euro_per_kwh = mean_spot + contract.marginal_purchase_price_components().best_estimate
        # Decomposition (§8.5): volume effect at the year's average price; the difference
        # between paying the average and the integral is the flexibility value.
        # Additive components are volume-proportional and carry no flexibility.
        if determinants.mean_spot_price_in_euro_per_kwh is not None:
            # The meter passed the year's unweighted mean spot price, so the flexibility value
            # (what load shifting saved vs. paying the average price) is separable (§8.5).
            mean_spot_unweighted = determinants.mean_spot_price_in_euro_per_kwh
            bill.volume_effect_in_euro = energy_bought * (
                mean_spot_unweighted + contract.marginal_purchase_price_components().best_estimate
            )
            bill.flexibility_value_in_euro = energy_bought * mean_spot_unweighted - spot_cost
        else:
            mean_price_for_volume = spot_cost / energy_bought if energy_bought else 0.0
            bill.volume_effect_in_euro = energy_bought * mean_price_for_volume
            bill.flexibility_value_in_euro = 0.0

    # Standing charge and controllability discount. The discount is a credit stated positively, so
    # its band is mirrored (`as_revenue`) before being added to a cost — otherwise the LOW slot of
    # the standing charge would combine a cheap charge with a stingy credit (§3.9).
    standing = contract.standing_charge_in_euro_per_year
    if contract.controllability_discount.kind == ControllabilityKind.FIXED_ANNUAL:
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
                # The marketer's share of the spot proceeds (`spot_factor`) applies to the
                # integrated spot term only; the markup is an agreed per-kWh amount beside it.
                revenue = UncertainValue.exact(
                    determinants.revenue_integrated_in_euro * contract.feed_in.spot_factor
                ) + (contract.feed_in.markup_in_euro_per_kwh.scale(determinants.energy_sold_in_kwh))
            else:
                # No natively integrated revenue available (e.g. a meter that only reported
                # annual totals): fall back to the flat rate rather than dropping the revenue.
                revenue = contract.feed_in.rate_in_euro_per_kwh.scale(determinants.energy_sold_in_kwh)
        bill.by_category[CostCategory.FEED_IN_REVENUE] = revenue.as_revenue()

    return bill


def tariff_counterfactual(
    determinants: BillingDeterminants, active: TariffContract, flat: TariffContract
) -> Dict[str, UncertainValue]:
    """Bill the same load profile under a flat contract to show what the tariff choice earned (§8.5).

    Load profile, control and all physics are held fixed, so no second simulation is needed; the result reads as "the
    tariff difference on this fixed behavior", not as the savings a household would realize with a price-reactive
    controller. That joint effect needs a second simulation compared as a `VariantComparison`.

    Args:
        determinants: The measured annual determinants, billed unchanged under both contracts.
        active: The contract actually in force.
        flat: The flat comparison contract.

    Returns:
        The two totals and their difference as bands; ``tariff_advantage_in_euro`` is positive when the active contract
            is cheaper.
    """
    active_bill = apply_tariff(determinants, active)
    flat_bill = apply_tariff(determinants, flat)
    return {
        "active_total_in_euro": active_bill.total(),
        "flat_total_in_euro": flat_bill.total(),
        "tariff_advantage_in_euro": flat_bill.total() - active_bill.total(),
    }
