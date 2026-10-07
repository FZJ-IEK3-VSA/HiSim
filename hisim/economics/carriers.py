"""Energy carriers priced at the system boundary, and the vocabularies of measured flows (cost_spec.md §3.2).

Simulation I/O uses ``loadtypes.LoadTypes`` for what flows anywhere inside the system; an `EnergyCarrier` names what is
bought or sold across the system boundary and therefore has a price entry, an emission factor and a tariff. Billing
only at carrier boundaries is what rules out double counting (§3.1). The module is a leaf: it holds only vocabulary.
"""

from __future__ import annotations

import enum
from typing import Dict, FrozenSet


@enum.unique
class EnergyCarrier(str, enum.Enum):
    """Carriers priced at the system boundary, one per key of the country price files.

    Adding a carrier means adding price entries for every shipped country. Members are `str`-valued, so they serialize
    to their names and work directly as data-file keys. ``ELECTRICITY_FEED_IN`` is not a purchased commodity: its
    "working price" is the feed-in remuneration, so exported electricity is priced by the same lookup as imported
    electricity with its own rate. Every carrier is billed per kWh; a price entry quoted per liter or per ton (its
    ``quantity_unit``) is divided by the carrier's heating value when the database resolves it.
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


@enum.unique
class EnergyFlowRole(str, enum.Enum):
    """The role of a measured device flow in the household electricity balance.

    Example: a battery has two roles, one for charging and one for discharging. Roles carry positive magnitudes; the
    direction is the role, not the sign. `GRID_IMPORT` and `GRID_EXPORT` are the meter's flows and the only roles that
    sit at a priced carrier boundary; the others are internal device flows no bill is computed from. A flow without a
    role is not recorded.
    """

    PV_GENERATION = "PV_GENERATION"
    BATTERY_CHARGE = "BATTERY_CHARGE"
    BATTERY_DISCHARGE = "BATTERY_DISCHARGE"
    HEAT_PUMP_ELECTRICITY = "HEAT_PUMP_ELECTRICITY"
    HOUSEHOLD_ELECTRICITY = "HOUSEHOLD_ELECTRICITY"
    GRID_IMPORT = "GRID_IMPORT"
    GRID_EXPORT = "GRID_EXPORT"


@enum.unique
class UsefulHeatKind(str, enum.Enum):
    """What a measured useful heat is spent on: the rooms, or the hot water drawn at the tap.

    The two kinds are the denominator of the system cost per kWh of heat (`adapter.UsefulHeatSources`) and are recorded
    apart in `economic_inputs.json`. A run with no hot-water source divides by the rooms' heat alone, and the split
    lets the engine say so instead of publishing a figure that reads too high.
    """

    ROOM_HEATING = "ROOM_HEATING"
    HOT_WATER = "HOT_WATER"


#: The carriers whose sold kilowatt hours are booked under a subject other than their own: the
#: electricity fed into the grid is priced by the ``ELECTRICITY_FEED_IN`` row and booked under that
#: subject. Every other carrier books a feed-in credit, if its contract grants one, under itself.
_REVENUE_SUBJECT_BY_CARRIER: Dict[str, str] = {
    EnergyCarrier.ELECTRICITY.value: EnergyCarrier.ELECTRICITY_FEED_IN.value,
}


def revenue_subject(carrier: str) -> str:
    """Return the timeline subject a carrier's ``FEED_IN_REVENUE`` entries are booked under.

    A subject is the name a timeline entry is booked under (a device, a carrier, or a synthetic label). The energy
    calculator books under this subject and the views read it back through `bill_subjects`, so the two cannot drift
    apart.

    Args:
        carrier: An `EnergyCarrier` or its value.

    Returns:
        The revenue subject; the carrier's own value for every carrier without a separate one.
    """
    key = carrier.value if isinstance(carrier, EnergyCarrier) else carrier
    return _REVENUE_SUBJECT_BY_CARRIER.get(key, key)


def bill_subjects(carrier: str) -> FrozenSet[str]:
    """Return every timeline subject a carrier's bill is booked under: its own and its revenue's.

    Args:
        carrier: An `EnergyCarrier` or its value.

    Returns:
        The carrier's own value and `revenue_subject` of it (one element when they coincide).
    """
    key = carrier.value if isinstance(carrier, EnergyCarrier) else carrier
    return frozenset((key, revenue_subject(key)))


def validate_energy_attribution(attribution: Dict[str, Dict[str, float]], context: str) -> None:
    """Refuse a per-subject energy record that carries a negative quantity.

    Every value is a magnitude whose direction is its `EnergyFlowRole`, so a negative number would silently shift the
    balance. The check runs where a record enters the system: the extraction, the annualization and the deserializer.

    Args:
        attribution: Subject -> `EnergyFlowRole` value -> kWh.
        context: What is being validated (a field or function name), for the message.

    Raises:
        ValueError: If any quantity is negative; the message names every one of them.
    """
    negative = [
        f"{subject}.{role}={value!r}"
        for subject, by_role in attribution.items()
        for role, value in by_role.items()
        if value < 0
    ]
    if negative:
        raise ValueError(
            f"{context} carries negative energy: {', '.join(sorted(negative))}. Attribution "
            "values are magnitudes and the direction is the role (a battery charges under one "
            "role and discharges under another), so a negative quantity would be drawn on the "
            "wrong side of the household balance."
        )
