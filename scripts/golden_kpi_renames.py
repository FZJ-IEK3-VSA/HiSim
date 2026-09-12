#!/usr/bin/env python3
"""The declared translation between the golden references' old and new names.

A golden reference is a flat ``{"BUI1.<component>.<kpi>": number}`` map, one file per
``(setup, parameter_set)`` pair, and both halves of a key have been renamed in the past: a
whole file when a setup was renamed, an individual key when the KPI collection changed how
it spells a component. A history walker that reads every commit literally sees such a
rename as one series ending and another beginning — two half-length panels where there is
one KPI. This module is the table that stitches them back together, and
``scripts/golden_history.py`` is its only consumer.

Every entry is a **claim someone made**: that this old name and this new name are two
spellings of one measurement, renamed by this commit. The claims here were derived
empirically, not guessed — the key sets of consecutive golden commits were diffed for every
file, and a pair was written down only where the diff showed one name leaving and one name
arriving in the same commit with the same value. ``tests/test_golden_history.py`` checks
the table back against the real history: every old name must occur in some historical
golden, every new name in the golden files as they stand today.

Why the KPI table is keyed per pair and not fleet-wide
------------------------------------------------------
The one real renaming so far (#653, "Two components of one name stay two components")
appended the component's class name to every KPI of a component whose *name* collided with
another component's in the same setup: ``BUI1.Fuel Meter.OPEX - CO2 Footprint`` became
``BUI1.Fuel Meter.OPEX - CO2 Footprint (FuelMeter)``. The suffix arrived only where a
collision actually existed, so the very same KPI name is renamed in one pair and untouched
in another: ``household_oil_building_sizer__full_year_60s`` carries
``BUI1.Fuel Meter.OPEX - CO2 Footprint (FuelMeter)`` and a plain, still-unsuffixed
``BUI1.Fuel Meter.OPEX - Energy costs`` side by side to this day. A fleet-wide rule for that
name would therefore merge two different series in the pairs that never renamed it. The
``"*"`` key below is supported for a rename that really is fleet-wide, and is empty today.

How to add a rename
-------------------
1. Find the commit that renamed it: ``git log --format='%H %s' -- golden_references/`` and
   diff the key sets of the two neighbouring commits.
2. Add the pair under the stem it applies to (or under ``"*"``), with the commit that did it
   and the PR number, and a note whenever the choice was not forced by the data.
3. Run ``pytest tests/test_golden_history.py`` — the consistency test fails on a name that
   never existed or a new name nothing carries today.

A renaming is applied only when the old name is present and the new name is absent in the
same snapshot; a snapshot carrying both keeps them apart, because there the two names are
two measurements rather than two spellings of one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Tuple

#: Marker for the pair-independent section of :data:`KPI_RENAMES`.
FLEET_WIDE = "*"

#: The commit that gave every colliding component's KPIs a ``(ClassName)`` suffix, and its PR.
#: Two components of one name used to collapse into one entry of the KPI collection; the fix
#: kept them apart by spelling the class into the key.
_COMPONENT_SPLIT = "dbde2c569faef264f6b3f4d038b1efa34dc07314"
_COMPONENT_SPLIT_PR = 653


@dataclass(frozen=True)
class KpiRename:
    """One claim that ``old`` and ``new`` are the same KPI under two names.

    Attributes:
        old: The key as the golden files spelled it before ``commit``.
        new: The key as they spell it from ``commit`` onwards.
        commit: The full sha of the commit that performed the rename.
        pr: The pull request that carried that commit.
        note: Why the pairing is what it is, whenever the data left a choice.
    """

    old: str
    new: str
    commit: str
    pr: int
    note: str = ""


@dataclass(frozen=True)
class PairRename:
    """One claim that a golden file's stem changed while the pair stayed the same.

    Attributes:
        old: The golden filename stem (no ``.json``) used before ``commit``.
        new: The stem used from ``commit`` onwards.
        commit: The full sha of the commit that renamed the file.
        pr: The pull request that carried that commit.
        note: Why the pairing is what it is.
    """

    old: str
    new: str
    commit: str
    pr: int
    note: str = ""


def _suffixed(component_class: str, commit: str, pr: int, *kpi_names: str) -> Tuple[KpiRename, ...]:
    """Build the ``old -> old + " (<class>)"`` claims of one component-name collision.

    Only the mechanical half of the entry is computed; which keys were renamed, in which
    pair, and to which class name stays written out at the call site, because that is the
    part a reviewer has to check.

    Args:
        component_class: The class name the rename appended, e.g. ``"FuelMeter"``.
        commit: The full sha of the commit that renamed the keys.
        pr: The pull request that carried that commit.
        *kpi_names: The keys as they were spelled before the rename.

    Returns:
        One :class:`KpiRename` per name, in the order given.
    """
    return tuple(
        KpiRename(old=name, new=f"{name} ({component_class})", commit=commit, pr=pr)
        for name in kpi_names
    )


#: Golden filename stems that were renamed, old stem -> the claim. Empty: no golden file has
#: been renamed yet — the 30 files that exist were each added under the name they still
#: carry (verified by diffing the file sets of every consecutive pair of golden commits). The
#: schema is fixed here so the first setup rename is a table entry rather than a code change.
#:
#: An entry looks like::
#:
#:     "household_gas_building_sizer__one_week_60s": PairRename(
#:         old="household_gas_building_sizer__one_week_60s",
#:         new="household_gas_boiler_building_sizer__one_week_60s",
#:         commit="<full sha>",
#:         pr=1234,
#:         note="the setup was renamed; the pair it gates is unchanged",
#:     ),
PAIR_RENAMES: Mapping[str, PairRename] = {}


#: KPI renames, keyed by the golden filename stem they apply to, or by :data:`FLEET_WIDE`
#: for a rename that happened in every pair at once. Within a pair the stem's own entries
#: win over the fleet-wide ones.
KPI_RENAMES: Mapping[str, Tuple[KpiRename, ...]] = {
    # No KPI has been renamed across the whole fleet at once. See the module docstring for
    # why #653 is emphatically not such a case.
    FLEET_WIDE: (),
    "dynamic_components__one_week_60s": (
        # This setup runs two CHPs, and before #653 both wrote into one ``BUI1.CHP.*`` entry
        # — the very collision the fix was about. The old series therefore has no single
        # honest successor; it is continued into the first machine's, and CHP2's keys start
        # as new series in #653. Both CHPs sit at 0.0 over the whole recorded history, so the
        # choice moves no line on any panel; it is written down because it is a choice.
        *_suffixed(
            "CHP1",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.CHP.Electrical energy produced",
            "BUI1.CHP.Fuel consumed",
            "BUI1.CHP.Number of activation cycles",
            "BUI1.CHP.Thermal energy produced",
        ),
    ),
    # The district-heating pairs gained a District Heating component of their own in #653;
    # what the fuel meter measures kept its value and only took the suffix.
    "household_district_heating_building_sizer__full_year_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Energy costs",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
            "BUI1.Fuel Meter.Total energy consumption",
        ),
    ),
    "household_district_heating_building_sizer__one_week_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Energy costs",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
            "BUI1.Fuel Meter.Total energy consumption",
        ),
    ),
    # In the two solar-thermal-plus-gas pairs the collector's KPIs had been covering the gas
    # boiler's as well; the collector kept its numbers under the suffixed name and the boiler
    # appeared beside it.
    "household_gas_solar_thermal__one_week_60s": (
        *_suffixed(
            "SolarThermalSystem",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Solar Thermal.CAPEX - CO2 Footprint",
            "BUI1.Solar Thermal.CAPEX - Investment cost",
            "BUI1.Solar Thermal.OPEX - CO2 Footprint",
            "BUI1.Solar Thermal.OPEX - Fuel costs",
            "BUI1.Solar Thermal.OPEX - Maintenance costs",
            "BUI1.Solar Thermal.Thermal energy delivered for domestic hot water",
            "BUI1.Solar Thermal.Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            "BUI1.Solar Thermal.Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            "BUI1.Solar Thermal.Total thermal energy delivered",
        ),
    ),
    "household_gas_solar_thermal_building_sizer__one_week_60s": (
        *_suffixed(
            "SolarThermalSystem",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Solar Thermal.CAPEX - CO2 Footprint",
            "BUI1.Solar Thermal.CAPEX - Investment cost",
            "BUI1.Solar Thermal.OPEX - CO2 Footprint",
            "BUI1.Solar Thermal.OPEX - Fuel costs",
            "BUI1.Solar Thermal.OPEX - Maintenance costs",
            "BUI1.Solar Thermal.Thermal energy delivered for domestic hot water",
            "BUI1.Solar Thermal.Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            "BUI1.Solar Thermal.Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            "BUI1.Solar Thermal.Total thermal energy delivered",
        ),
    ),
    # The four fuel-boiler pairs: only the two KPIs the boiler also reports were ambiguous,
    # so only those two took the suffix. The meter's other keys are unsuffixed to this day,
    # which is why none of this can be stated fleet-wide.
    "household_oil_building_sizer__full_year_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
    "household_oil_building_sizer__one_week_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
    "household_pellets_building_sizer__full_year_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
    "household_pellets_building_sizer__one_week_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
    "household_wood_chips_building_sizer__full_year_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
    "household_wood_chips_building_sizer__one_week_60s": (
        *_suffixed(
            "FuelMeter",
            _COMPONENT_SPLIT, _COMPONENT_SPLIT_PR,
            "BUI1.Fuel Meter.OPEX - CO2 Footprint",
            "BUI1.Fuel Meter.OPEX - Maintenance costs",
        ),
    ),
}


def canonical_pair(stem: str) -> str:
    """Return the stem a golden file is known by today.

    Args:
        stem: A golden filename stem as some commit spelled it.

    Returns:
        The current stem, following the chain of :data:`PAIR_RENAMES` to its end.

    Raises:
        ValueError: If the renames form a cycle, which would mean two entries disagree.
    """
    seen = [stem]
    current = stem
    while current in PAIR_RENAMES:
        current = PAIR_RENAMES[current].new
        if current in seen:
            raise ValueError(f"PAIR_RENAMES forms a cycle: {' -> '.join(seen + [current])}")
        seen.append(current)
    return current


def kpi_renames_for(stem: str) -> Dict[str, str]:
    """Return the ``old key -> new key`` map that applies to one pair.

    Args:
        stem: The golden filename stem, in its current spelling.

    Returns:
        The fleet-wide claims merged with the pair's own, the pair's winning on a clash.

    Raises:
        ValueError: If one pair declares the same old key twice with two different new keys.
    """
    merged: Dict[str, str] = {}
    for entry in KPI_RENAMES.get(FLEET_WIDE, ()):
        merged[entry.old] = entry.new
    own: Dict[str, str] = {}
    for entry in KPI_RENAMES.get(stem, ()):
        if entry.old in own and own[entry.old] != entry.new:
            raise ValueError(
                f"'{stem}' declares '{entry.old}' to mean both '{own[entry.old]}' and '{entry.new}'."
            )
        own[entry.old] = entry.new
    merged.update(own)
    return merged


def all_kpi_renames() -> Tuple[KpiRename, ...]:
    """Return every KPI rename claim in the table, fleet-wide ones first.

    Returns:
        The claims, flattened, in the table's own order.
    """
    flattened: list[KpiRename] = list(KPI_RENAMES.get(FLEET_WIDE, ()))
    for stem, entries in KPI_RENAMES.items():
        if stem != FLEET_WIDE:
            flattened.extend(entries)
    return tuple(flattened)
