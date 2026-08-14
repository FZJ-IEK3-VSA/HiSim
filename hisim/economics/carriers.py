"""Energy carrier enum for pricing (cost_spec.md §3.2).

Replaces the ad-hoc use of ``LoadTypes`` for pricing purposes. Simulation I/O keeps using
``loadtypes.LoadTypes``; only the billing boundary speaks ``EnergyCarrier``.

The separation is deliberate and is what makes "energy is billed only at carrier boundaries, so
nothing can be double counted by construction" (§3.1) checkable: `LoadTypes` describes what flows
along a wire or pipe anywhere inside the system, while an `EnergyCarrier` names something that is
*bought or sold across the system boundary* and therefore has a price entry, an emission factor and
a tariff contract. A member of this enum is simultaneously the key of an `energy_prices_<COUNTRY>`
row, the carrier field of `facts.EnergyFlowFacts`/`facts.BillingDeterminants` that meters declare,
and the subject of the resulting energy cash-flow entries.

The module owns only the vocabulary — no prices, no emission factors, no conversion between carriers
and `LoadTypes` (the adapter and the meter components make that mapping where they need it). Like
`uncertainty.py` it is a leaf, imported by facts, catalog entries, tariffs and parameters alike.
"""

from __future__ import annotations

import enum


@enum.unique
class EnergyCarrier(str, enum.Enum):
    """Carriers priced at the system boundary.

    One member per thing a building can buy or sell, and therefore per key of the country price
    files: adding a carrier means adding price entries for every shipped country, not changing code.
    Members are `str`-valued so they serialize to their own names in JSON exports and can be used
    directly as data-file keys.

    ``ELECTRICITY_FEED_IN`` is the one member that is not a purchased commodity: it carries the
    feed-in remuneration as its "working price", which is what lets exported electricity be priced
    by exactly the same lookup as imported electricity while keeping the two rates independent
    (feed-in also has its own escalation rate, nominally fixed for EEG-style contracts). Every
    member is *billed* per kWh; the price entry's ``quantity_unit`` says only how the data file
    quotes it — per liter for oil and diesel, per ton for pellets and wood chips, as the cited
    sources publish them — and the database divides such a quote by the carrier's heating value
    when it resolves the entry (D26).
    """

    ELECTRICITY = "ELECTRICITY"
    ELECTRICITY_FEED_IN = "ELECTRICITY_FEED_IN"
    NATURAL_GAS = "NATURAL_GAS"
    HEATING_OIL = "HEATING_OIL"
    PELLETS = "PELLETS"
    WOOD_CHIPS = "WOOD_CHIPS"
    DISTRICT_HEATING = "DISTRICT_HEATING"
    HYDROGEN = "HYDROGEN"
    DIESEL = "DIESEL"
