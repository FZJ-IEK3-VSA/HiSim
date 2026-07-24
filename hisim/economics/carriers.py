"""Energy carrier enum for pricing (cost_spec.md §3.2).

Replaces the ad-hoc use of ``LoadTypes`` for pricing purposes. Simulation I/O keeps using
``loadtypes.LoadTypes``; only the billing boundary speaks ``EnergyCarrier``.
"""

from __future__ import annotations

import enum


@enum.unique
class EnergyCarrier(str, enum.Enum):
    """Carriers priced at the system boundary."""

    ELECTRICITY = "ELECTRICITY"
    ELECTRICITY_FEED_IN = "ELECTRICITY_FEED_IN"
    NATURAL_GAS = "NATURAL_GAS"
    HEATING_OIL = "HEATING_OIL"
    PELLETS = "PELLETS"
    WOOD_CHIPS = "WOOD_CHIPS"
    DISTRICT_HEATING = "DISTRICT_HEATING"
    HYDROGEN = "HYDROGEN"
    DIESEL = "DIESEL"
