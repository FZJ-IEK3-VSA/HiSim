"""Tariff contracts and the pure billing engine (cost_spec.md §8).

One :class:`TariffContract` per carrier is the single source of truth: the in-simulation
price provider (``hisim/components/tariff_provider.py``) and the postprocessing billing
engine both read it; neither carries its own price data.

That rule exists because of the consistency problem §8.1 opens with: if an energy management
system optimizes against one price signal and the evaluation bills against another, the resulting
"savings from smart control" are an artifact of the mismatch rather than a result. Everything in
this module therefore serves one of two roles — describing a contract (the §8.2 schema, its
loaders, the spot series) or *applying* it (:func:`apply_tariff`, plus the price-selection
functions the simulation side calls per timestep) — and the two halves are separated by the
``=== engine`` banner, in preparation for the §2.5 package split.

What this module deliberately does NOT own: the horizon projection and escalation of a bill
(§8.5 lives in ``calculators/energy.py``, which also builds the default flat contract from a §3.5
price entry), the CO2 price component, the timeline, and any notion of who pays. It bills exactly
one year of one carrier from measured determinants and returns a :class:`Year1Bill`; that bill is
then decomposed, escalated and booked elsewhere.

**Source ids and the `inline:` convention (W2.4).** Every contract must cite sources (§3.10).
A contract loaded from ``cost_database/tariffs/*.json`` must name entries of the cost
database's ``sources.json``; citing a source in prose as ``inline:<citation>`` is a
validation *error* there (`validation.validate_tariff_contracts`), because a prose citation
cannot be reviewed, deduplicated or checked for staleness. Contracts constructed **in
memory** — worked examples, test fixtures, the provider's ``SYNTHETIC_TEST`` contract — have
no registry behind them and keep using ``inline:<citation>``: it is rendered verbatim as an
``INLINE`` source at a report leaf (`results.LifecycleCostResult.explain`) and never enters a
shipped data file. That is the whole scope of the convention; catalog data does not use it.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDataError, SourceRegistry
from hisim.economics.facts import BillingDeterminants
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue

# =========================================================================== data
# Contract data, the §8.2 schema and catalog loading. This half moves to `economics/data/`
# in the §2.5 package split; it knows nothing about bills. (`TariffContract`'s own
# `marginal_purchase_price_components` stays with the data: it is a derived property of the
# contract's own fields, and both halves read it.)


class SupplyKind(str, enum.Enum):
    """Supply price structures (§8.2).

    How the energy part of the price varies over time, which is the single most consequential
    choice in a tariff because it decides what a bill can even see: FLAT needs annual kWh only,
    TIME_OF_USE needs kWh per band, and DYNAMIC needs the integral of load times the spot series at
    native resolution. The kind therefore selects both the price-selection rule
    (:func:`energy_price_in_euro_per_kwh`) and which billing determinants the meter must have
    produced — the whole reason §8.4 extends the meters at all.
    """

    FLAT = "FLAT"
    TIME_OF_USE = "TIME_OF_USE"
    DYNAMIC = "DYNAMIC"


class CapacityChargeKind(str, enum.Enum):
    """Capacity charge structures (§8.2).

    Capacity (demand) charges bill *power* rather than energy, which is what makes peak shaving
    worth money and is why the engine models them explicitly. The kinds differ only in which peaks
    are counted: the single annual maximum, one per month, or only those inside declared
    high-load windows. In every case a peak is the mean power over the billing interval, never an
    instantaneous timestep value (§8.4).
    """

    NONE = "NONE"
    ANNUAL_PEAK = "ANNUAL_PEAK"
    MONTHLY_PEAK = "MONTHLY_PEAK"
    PEAK_WINDOW = "PEAK_WINDOW"


class FeedInKind(str, enum.Enum):
    """Feed-in remuneration structures (§8.2).

    How exported energy is paid for, which matters over a 20-year horizon mostly through the
    escalation it implies: a fixed statutory tariff stays *nominally* constant for its contract
    duration — and therefore loses real value every year — while a spot-referenced direct-marketing
    revenue follows the market. The kind selects how the year-1 revenue is computed here; the
    projection in ``calculators/energy.py`` then holds that revenue nominally fixed for
    ``duration_in_years`` and escalates it only afterwards (§8.5, spec Q10).
    """

    NONE = "NONE"
    FIXED_TARIFF = "FIXED_TARIFF"  # EEG, nominally constant for its duration
    SPOT_REFERENCED = "SPOT_REFERENCED"  # direct marketing


@dataclass
class TimeOfUseBand:
    """One ToU band: weekday/hour masks with a working price.

    A band is a named set of hours (day/night, peak/off-peak) with its own energy price, and the
    name is load-bearing: the meter reports energy *per band name*, so a band renamed in the
    contract silently stops matching the determinants and its energy falls through to the fallback
    band. Masks may overlap — the first declared match wins (:func:`time_of_use_band_for`) — which
    makes a broad catch-all band declared last a valid way to express "everything else".
    """

    name: str  # must match the key the meter reports energy under
    price_in_euro_per_kwh: UncertainValue  # energy-only; the additive components are added on top
    weekdays: List[int] = field(default_factory=lambda: list(range(7)))  # 0 = Monday
    hours: List[int] = field(default_factory=lambda: list(range(24)))  # local clock hour of the day


@dataclass
class TariffSupply:
    """Supply side of a contract; non-energy components kept separate (§8.2).

    Everything that prices a purchased kWh, split into the time-varying energy part (flat price,
    bands, or spot series times a factor) and three per-kWh components that do *not* vary with the
    moment — supplier markup, grid fee, taxes and levies. The split is not cosmetic: §8.2 keeps the
    components separate so that the macroeconomic view (§4.5) can strip taxes and levies, a §14a
    discount can reduce the grid fee alone, and the flexibility decomposition can attribute value
    to the energy part only. Of those, the grid-fee discount and the decomposition are wired today;
    the macroeconomic view currently strips taxes via the §3.5 price entry's ``tax_and_levy_share``
    rather than through these fields.
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
    """Capacity charge terms; peaks are billing-interval means, never instantaneous (§8.4).

    The power-based part of a bill: a price per kW applied to the peaks the meter measured. The
    billing interval is part of the contract because it decides what "a peak" means — a 15-minute
    mean is a very different number from a one-minute spike, and billing the latter would reward
    peak shaving that no supplier actually pays for. That interval must be a whole multiple of the
    simulation timestep, checked before the run by :func:`validate_billing_interval`.
    """

    kind: CapacityChargeKind = CapacityChargeKind.NONE
    price_in_euro_per_kw: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    billing_interval_in_minutes: int = 15  # peaks are means over this interval
    window_hours: List[int] = field(default_factory=list)  # PEAK_WINDOW only
    window_weekdays: List[int] = field(default_factory=list)  # PEAK_WINDOW only


@dataclass
class FeedIn:
    """Feed-in remuneration terms.

    The export side of the contract: what the building is paid per kWh it sells, for how long, and
    whether that payment is a fixed tariff or spot-referenced direct marketing. It sits on the
    contract rather than in a separate price entry so that one object answers both "what does a kWh
    cost" and "what does a kWh earn" — the two the PV/battery economics turn on.

    Under ``SPOT_REFERENCED`` the payment is `spot_factor` times the natively integrated spot
    revenue plus `markup_in_euro_per_kwh` per kWh sold: the factor is the share of the spot
    proceeds the direct marketer passes on (1.0 = all of it), and it applies to the spot term
    alone, the markup being agreed independently of the price.
    """

    kind: FeedInKind = FeedInKind.NONE
    rate_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    duration_in_years: int = 20  # nominal-fixed period for FIXED_TARIFF (EEG convention)
    spot_factor: float = 1.0  # SPOT_REFERENCED: share of the spot proceeds paid out (1.0 = all)
    markup_in_euro_per_kwh: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))


@dataclass
class ControllabilityDiscount:
    """§14a-EnWG-style grid-fee discount, as data only (v1, spec Q19).

    German §14a EnWG grants operators of dimmable devices (heat pumps, wallboxes) a reduced grid
    fee in exchange for accepting curtailment — either as a fixed annual credit or as a percentage
    off the grid-fee component. Only the *money* side is modeled in v1: the dimming events
    themselves are a control-side work item, so a run claims the discount without ever simulating
    the curtailment it is paid for, which overstates the benefit and is flagged as such in the spec.
    """

    kind: str = "NONE"  # NONE | FIXED_ANNUAL | GRID_FEE_SHARE
    annual_amount_in_euro: UncertainValue = field(default_factory=lambda: UncertainValue.exact(0.0))
    grid_fee_reduction_share: float = 0.0  # GRID_FEE_SHARE: fraction taken off the grid fee


@dataclass
class TariffContract:
    """One tariff contract per carrier (§8.2). Data-driven, referenced by id.

    The complete commercial relationship for one energy carrier — supply price structure, standing
    charge, capacity charge, feed-in terms and any controllability discount — in one object that
    both the simulation and the billing engine read. This is the answer to §8.1: a contract is
    referenced by id from a scenario or a RenoVisor request, loaded once, and handed to both sides,
    so no price can exist in one of them without existing in the other.

    Contracts come from three places: ``cost_database/tariffs/<id>.json`` (the file name *is* the
    id, which is how :func:`load_tariff_contract` resolves it), an in-memory construction in tests
    and worked examples, or — when a carrier has no contract at all — a default flat contract
    generated from the §3.5 price entries by ``calculators/energy.default_contract``, which is
    behaviorally identical to the plain price lookup and is marked ``is_default_contract``.
    """

    #: Default location of shipped tariff contracts.
    DEFAULT_PATH: ClassVar[str] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database", "tariffs"
    )

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
    def from_json(cls, raw: dict, registry: Optional[SourceRegistry] = None) -> "TariffContract":
        """Parses a tariff contract JSON (§8.2 schema).

        The one parser for the contract schema, used by the file loader, by the round-trip in
        ``serialization.py`` and by the data-file CI. Every monetary field goes through
        `UncertainValue.from_json`, so a contract may state either an exact number or a min/best_estimate/max
        band (§3.1) without the parser branching. Almost all keys are optional with neutral
        defaults — absent blocks mean "no capacity charge", "no feed-in", "no discount" — which
        keeps a simple flat contract a handful of lines; the exceptions are ``supply``, ``carrier``
        and ``source_ids``, the last because §3.10 admits no unsourced datapoint.

        Args:
            raw: The parsed contract JSON.
            registry: Optional source registry; when given, the cited ids are resolved against it
                immediately so a dangling citation fails here rather than at report time.

        Returns:
            The parsed contract.

        Raises:
            CostDataError: If ``source_ids`` is empty or a cited id is unknown to the registry.
            KeyError / ValueError: On a structurally invalid document (missing ``supply`` or
                ``carrier``, unknown enum member).
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
            is_default_contract=bool(raw.get("is_default_contract", False)),
        )

    def marginal_purchase_price_components(self) -> UncertainValue:
        """Additive non-spot per-kWh components (markup + grid fee + taxes), §8.4.

        In euro per kWh: everything that is charged on each purchased kWh regardless of *when* it
        was purchased, with a ``GRID_FEE_SHARE`` controllability discount already applied to the
        grid-fee part (and only to it, per §14a). Isolating these is what lets an uncertainty band
        on a tariff component shift a whole bill by ``E × Δcomponent`` without re-integrating the
        spot series per slot — the §8.4 rule that keeps three-slot billing cheap and exact.

        It lives on the contract, on the data side of the module, because it is a derived property
        of the contract's own fields; both the in-simulation price provider and
        :func:`apply_tariff` add it to the time-varying energy price.
        """
        grid_fee = self.supply.grid_fee_in_euro_per_kwh
        if self.controllability_discount.kind == "GRID_FEE_SHARE":
            grid_fee = grid_fee.scale(1.0 - self.controllability_discount.grid_fee_reduction_share)
        return self.supply.markup_in_euro_per_kwh + grid_fee + self.supply.taxes_and_levies_in_euro_per_kwh


def contract_to_json(contract: TariffContract) -> dict:
    """Serializes a contract in the §8.2 catalog schema — the exact inverse of `from_json`.

    Used to embed contracts in `economic_inputs.json` (cost-spec-v2 §2.1, W1.4) so re-pricing a
    stored result needs no catalog lookup and in-memory contracts survive the round trip. One
    schema, one parser: the output is a valid `cost_database/tariffs/*.json` file.
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
            "kind": contract.controllability_discount.kind,
            "annual_amount_in_euro": contract.controllability_discount.annual_amount_in_euro.to_json(),
            "grid_fee_reduction_share": contract.controllability_discount.grid_fee_reduction_share,
        },
        "source_ids": list(contract.source_ids),
    }
    if contract.is_default_contract:
        raw["is_default_contract"] = True
    return raw


def load_tariff_contract(contract_id: str, base_path: Optional[str] = None) -> TariffContract:
    """Loads one contract JSON by id from the tariffs directory.

    Contracts are resolved by file name, so the id in the document and the name of the file that
    holds it must agree — the data-file CI checks exactly that, because a mismatch would make a
    contract referenced by a scenario silently unfindable. Note that no source registry is passed
    here, so citations are not resolved on this path; ``validation.validate_tariff_contracts`` is
    where the shipped catalog's sources are checked.

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
    """Loads a spot price series (EUR/kWh, hourly) from the cost database.

    Series are versioned CSVs under ``cost_database/spot_series/<id>.csv`` with one price per
    line (header allowed). A documented loader for user-supplied CSVs (spec Q16).

    A DYNAMIC contract names its series by id and the simulation-side price provider reads it to
    publish a per-timestep price; the billing engine never opens a series itself, it consumes the
    integral the meter produced. Real EPEX series are not shipped for licensing reasons, so this
    exists mainly so that a user can drop their own hourly file in place — hence the tolerant
    shape handling: it takes the last comma-separated token of each line, so a plain one-column
    file and a ``timestamp,price`` file both work, and it skips blank lines and a non-numeric
    first line (the column header). Prices are in EUR/kWh (not EUR/MWh) and the series is expected
    to be hourly, i.e. 8760 values for a full year.

    What it does *not* do any more is skip whatever else fails to parse (issue #25a). A corrupt
    value in the middle of a user's file used to vanish, shortening the series by one hour and
    silently shifting every later price to the wrong hour of the year — a dynamic-tariff bill
    computed against a shifted price series is wrong in a way nothing downstream can detect. Such
    a line now fails the load, with the line number to go and look at.

    Args:
        series_id: The series id, equal to the CSV's base name.
        base_path: Directory to load from; defaults to ``cost_database/spot_series``.

    Returns:
        The prices in file order, in EUR/kWh.

    Raises:
        CostDataError: If the file is missing, contains no parsable value, or holds a non-empty
            line past the header that is not a price (message names file and line number).
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
    """A synthetic hourly reference profile for tests (spec Q16 fallback).

    A daily sine with morning/evening structure; deterministic, mean ≈ `mean_price`.

    Real day-ahead series cannot be shipped for licensing reasons, so this stands in wherever a
    DYNAMIC contract must be exercised without user data: the shipped ``DE_DYNAMIC_SYNTHETIC_2024``
    contract, the price provider's ``SYNTHETIC_TEST`` contract (whose ``spot_series`` is the
    sentinel ``"__synthetic__"``), and the dynamic-tariff tests. Being a closed formula rather than
    a stored file makes it reproducible bit for bit across machines, which is what lets tests
    assert exact bills; it is a *fixture*, not data, and must never back a published result.

    Args:
        mean_price: Mean level of the profile in EUR/kWh.
        amplitude: Half-swing of the daily shape in EUR/kWh; a seasonal term of 20 % of it is
            superimposed, and prices are floored at zero (so a large amplitude biases the mean up).

    Returns:
        8760 hourly prices in EUR/kWh, starting at hour 0 of January 1.
    """
    import math

    prices = []
    for hour in range(8760):
        hour_of_day = hour % 24
        daily = math.sin((hour_of_day - 4) / 24.0 * 2.0 * math.pi)
        seasonal = 0.2 * math.cos(hour / 8760.0 * 2.0 * math.pi)
        prices.append(max(0.0, mean_price + amplitude * (daily + seasonal)))
    return prices


def validate_billing_interval(seconds_per_timestep: int, contract: TariffContract) -> None:
    """`seconds_per_timestep` must divide the billing interval, else pre-check fails (§8.4).

    A contract-data pre-check, not billing: it confronts the contract's declared billing
    interval with a *simulation* parameter before a run starts, which is why it sits on the data
    side of the §2.5 cut with the schema and the loaders. Its caller is the simulation-side
    price provider.
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
# The pure year-1 billing engine and the price selection it defines. This half moves to
# `economics/engine/` in the §2.5 package split. Default-contract *construction* from a §3.5
# price entry used to live here as `TariffContract.default_from_price_entry`; it is engine
# behavior and now lives with its only caller, `calculators/energy.default_contract`.


@dataclass
class Year1Bill:
    """apply_tariff output: year-1 costs by category, each a slot band (§8.4).

    One carrier's bill for one year, in euro, split into the categories the timeline books
    separately — working (energy) cost, standing charge, capacity charge and feed-in revenue —
    because each escalates at its own rate over the horizon. Absent categories mean "this contract
    does not have that component" rather than zero, so a reader can tell a contract without a
    capacity charge from one whose peaks happened to be zero.

    The three scalar fields are the §8.5 decomposition, all on the BEST_ESTIMATE slot and all
    informational except ``flexibility_value_in_euro``: the projection in ``calculators/energy.py``
    escalates the volume part with the carrier rate and the flexibility part with the (separately
    configurable) spread rate, because the value of load shifting is expected to grow with spot
    spreads rather than with the price level. Sign convention: costs positive, feed-in revenue
    negative and revenue-mirrored (``as_revenue``), while ``flexibility_value_in_euro`` is a
    positive *saving* that the projection subtracts.
    """

    by_category: Dict[CostCategory, UncertainValue] = field(default_factory=dict)
    # Decomposition for the horizon projection (§8.5), BEST_ESTIMATE-slot figures:
    volume_effect_in_euro: float = 0.0  # E_bought x mean price
    flexibility_value_in_euro: float = 0.0  # savings of load shifting vs the mean price
    mean_energy_price_in_euro_per_kwh: float = 0.0

    def total(self) -> UncertainValue:
        """Signed sum of all categories.

        The whole year-1 bill as one band: costs positive, feed-in revenue negative, so a
        self-consuming PV building can legitimately total below zero. Used mainly for the tariff
        counterfactual and for reporting; the timeline itself always books the categories
        separately, since it is their different escalation that the projection depends on.
        """
        return UncertainValue.sum(self.by_category.values())


def time_of_use_band_for(
    supply: TariffSupply, weekday: Optional[int] = None, hour: Optional[int] = None
) -> Optional[TimeOfUseBand]:
    """The ToU band that prices one moment — the single band-selection rule (§8.4).

    First declared match wins. When nothing matches, or when the moment is unknown, the **first**
    band applies: that is the rule `apply_tariff` bills unbanded energy with, so the price a
    controller reacts to and the price the bill charges cannot disagree. `None` only when the
    contract declares no bands at all (a data error the billing engine reports).
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
    """The energy-only price of one kWh at one moment, before the additive components (§8.4).

    FLAT reads the working price, TIME_OF_USE the band covering the moment, DYNAMIC the passed
    spot price times the contract's `spot_factor` (the caller supplies the series value; this
    module does not read series).

    Keeping the *energy-only* price separate from the full marginal price is what makes the
    macroeconomic view and the flexibility decomposition possible at all — only this part varies
    with the moment, and only this part is what a controller can shift its consumption into.

    Args:
        contract: The contract to price from.
        weekday: 0 = Monday; together with `hour` it selects the ToU band. Both may be None, in
            which case the first band applies (the same fallback the price provider uses).
        hour: Clock hour of the day, 0..23.
        spot_price_in_euro_per_kwh: The series value of this moment, required for DYNAMIC supply.

    Returns:
        EUR/kWh as a band. DYNAMIC returns an exact value: the spot series is simulation input and
        stays exact per §3.9, so a dynamic contract's uncertainty lives entirely in its additive
        components.

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
    """What one more kWh costs at one moment: energy price plus the additive components (§8.4).

    **One price-selection rule for both sides of the module boundary.** The in-simulation
    provider (`components/tariff_provider.py`) publishes this figure per timestep and controllers
    optimize against it; `apply_tariff` prices the resulting aggregates with the same function.
    They used to be two implementations of FLAT/ToU/DYNAMIC selection, which is exactly the kind
    of duplication that lets a control decision and its bill drift apart (cost-spec-v2 §2.2).
    """
    return energy_price_in_euro_per_kwh(
        contract, weekday, hour, spot_price_in_euro_per_kwh
    ) + contract.marginal_purchase_price_components()


def apply_tariff(determinants: BillingDeterminants, contract: TariffContract) -> Year1Bill:
    """The billing engine: one pure function (§8.4).

    Property-tested invariants: a flat contract reproduces kWh x price exactly; the capacity
    charge is monotone in every peak. Uncertain additive components shift each slot's bill by
    ``E x delta`` without re-integrating the spot series (§8.4).

    This is where measured consumption becomes money, and it is deliberately the *only* such
    place: every carrier of every perspective is billed through this one function, so a pricing
    rule cannot exist in two versions. Being pure — no database, no ledger, no clock, no state —
    is what makes it property-testable and what lets the same call re-price a stored result years
    later or bill a load profile under a hypothetical contract (:func:`tariff_counterfactual`).

    Units of the inputs. ``determinants`` describes **one full year of one carrier**: energy in
    kWh (bought, sold, and per ToU band name), the natively integrated ``cost_integrated_in_euro``
    and ``revenue_integrated_in_euro`` in euro for dynamic supply, peaks in **kW** as
    billing-interval mean power (never instantaneous), and the unweighted mean spot price in
    EUR/kWh. A shorter simulation must be annualized *before* this call — the function has no
    notion of a period. The contract supplies EUR/kWh, EUR/kW and EUR/year figures as bands.

    What the returned :class:`Year1Bill` contains, per uncertainty slot:
    ``ENERGY_WORKING`` (energy plus the additive per-kWh components, positive),
    ``ENERGY_STANDING`` (the annual standing charge, reduced by a FIXED_ANNUAL controllability
    credit), ``ENERGY_CAPACITY_CHARGE`` (price per kW times the summed relevant peaks — present
    only if the contract has one) and ``FEED_IN_REVENUE`` (negative, revenue-mirrored, present only
    if something was sold) — plus the §8.5 decomposition scalars on the BEST_ESTIMATE slot. Prices are
    taken as stated: VAT is never added or removed here, and ``supply.vat_rate`` currently has no
    consumer at all — the macroeconomic view strips taxes using the price entry's
    ``tax_and_levy_share`` instead (``calculators/energy.py``).

    Three billing paths, one per supply kind. FLAT multiplies the marginal price by annual energy.
    TIME_OF_USE prices each band's energy with its own price and bills whatever the meter did not
    attribute to a band at the fallback band's price — the same fallback the price provider uses,
    so a band-name mismatch produces a consistent (if wrong-looking) bill instead of free energy.
    DYNAMIC refuses to work from annual totals at all: it requires the integral the meter computed
    at native resolution, because kWh times average price is precisely the error a dynamic tariff
    exists to exploit. Only DYNAMIC can report a non-zero flexibility value, and only when the
    meter also supplied the unweighted mean spot price.

    Args:
        determinants: One carrier's annual billing determinants (§8.4), already annualized.
        contract: The tariff contract to bill under.

    Returns:
        The year-1 bill by category, with the §8.5 decomposition attached.

    Raises:
        CostDataError: For a TIME_OF_USE contract without bands, or a DYNAMIC contract whose
            determinants carry no integrated cost.
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
        total = UncertainValue.exact(0.0)
        banded_energy = 0.0
        for band in supply.bands:
            band_energy = determinants.energy_bought_per_band_in_kwh.get(band.name, 0.0)
            banded_energy += band_energy
            total = total + band.price_in_euro_per_kwh.scale(band_energy)
        unbanded = energy_bought - banded_energy
        if unbanded > 1e-6:
            # Bill unbanded energy at the fallback band and let the meter warn upstream — the
            # same fallback the price provider applies to an unmatched moment.
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
    """Bills the *same* load profile under a flat contract (§8.5 counterfactual 1).

    Answers "what did the tariff choice earn, given unchanged behavior": the simulated load profile
    is re-billed under `flat`, and the difference to the active contract's bill is attributed to
    the tariff. The counterfactual is therefore *to a different contract, not to a different
    building or controller* — the load profile, the control strategy and every physical quantity
    are held fixed, which is what makes it free (no second simulation) and also what limits it.

    The complementary question — what the tariff *and* a price-reactive controller earn together —
    is §8.5's behavioral counterfactual and needs a second simulation with a flat tariff and a
    price-blind EMS, compared as an ordinary `VariantComparison`. Re-pricing carries that caveat
    generally — it is why a *scenario* that overlays energy prices consumed by the simulation must
    opt in via ``EconomicParameters.allow_counterfactual_billing`` (§4.6); this function is an
    explicit request and is not gated, so the honest reading of its output is "the tariff
    difference on this fixed behavior", not "the savings a household would realize".

    Args:
        determinants: The measured annual determinants, billed unchanged under both contracts.
        active: The contract actually in force.
        flat: The flat comparison contract.

    Returns:
        The two totals and their difference, all as bands; ``tariff_advantage_in_euro`` is positive
        when the active contract is cheaper than the flat one.
    """
    active_bill = apply_tariff(determinants, active)
    flat_bill = apply_tariff(determinants, flat)
    return {
        "active_total_in_euro": active_bill.total(),
        "flat_total_in_euro": flat_bill.total(),
        "tariff_advantage_in_euro": flat_bill.total() - active_bill.total(),
    }
