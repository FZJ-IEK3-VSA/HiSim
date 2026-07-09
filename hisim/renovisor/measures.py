"""Application of RenoVisor renovation measures to a home inventory (spec section 3).

Measures are applied to a deep copy of ``homeInputs``; the original request is never mutated.
Envelope measures are not translated into individual U-values in v1 — they are counted by
distinct type and later folded into the TABULA refurbishment variant (spec section 4.2).
"""

import copy
from dataclasses import dataclass, field
from typing import Any, List, Set, Tuple

# Measure types that describe the building envelope; they influence the TABULA refurbishment
# variant instead of individual parameters (spec sections 3 and 4.2).
ENVELOPE_MEASURE_TYPES = {
    "roof_insulation",
    "wall_insulation",
    "floor_insulation",
    "windows",
    "doors",
    "air_sealing",
    "ventilation",
}

# (path, status, note) triple for the mapping report; statuses as in spec section 6.
ReportNote = Tuple[str, str, str]


@dataclass
class MeasureApplication:
    """Result of applying all measures to a copy of the home inventory."""

    home_inputs: dict
    envelope_measure_types: Set[str] = field(default_factory=set)
    report_notes: List[ReportNote] = field(default_factory=list)


def apply_measures(home_inputs: dict, measures: List[dict]) -> MeasureApplication:
    """Apply every measure to a copy of *home_inputs* (all together, one package).

    Returns the modified inventory, the set of distinct envelope measure types encountered
    and one mapping-report note per measure entry.
    """
    result = MeasureApplication(home_inputs=copy.deepcopy(home_inputs))
    for index, measure in enumerate(measures):
        path = f"measures[{index}]"
        measure_type = measure.get("type")
        params = measure.get("params") or {}
        if not isinstance(measure_type, str):
            result.report_notes.append((path, "ignored", "measure entry without a 'type' field"))
            continue
        _apply_single_measure(result, path, measure_type, params)
    return result


def _apply_single_measure(result: MeasureApplication, path: str, measure_type: str, params: dict) -> None:
    """Apply one measure to ``result.home_inputs`` and record its report note."""
    home = result.home_inputs
    if measure_type == "heat_pump":
        home.setdefault("heating", {})["primary"] = "heat_pump"
        note = "heating.primary set to 'heat_pump'"
        if "kW" in params:
            note += f" (requested kW={params['kW']} ignored: the setup auto-sizes the heat pump)"
        result.report_notes.append((path, "used", note))
    elif measure_type == "pv":
        kwp = _numeric_param(params, "kWp")
        if kwp is None:
            result.report_notes.append((path, "ignored", "pv measure without a numeric 'kWp' param"))
            return
        home.setdefault("pv", {})["kWp"] = kwp
        result.report_notes.append((path, "used", f"pv.kWp set to {kwp} (new total)"))
    elif measure_type == "battery":
        kwh = _numeric_param(params, "kWh")
        if kwh is None:
            result.report_notes.append((path, "ignored", "battery measure without a numeric 'kWh' param"))
            return
        home.setdefault("battery", {})["kWh"] = kwh
        result.report_notes.append(
            (path, "approximated", f"battery.kWh set to {kwh} (new total); size is auto-sized by the setup")
        )
    elif measure_type == "solar_thermal":
        solar_thermal = home.setdefault("solarThermal", {})
        if solar_thermal.get("mode") in (None, "none"):
            solar_thermal["mode"] = "hot_water"
        result.report_notes.append((path, "used", f"solarThermal.mode set to '{solar_thermal['mode']}'"))
    elif measure_type in ENVELOPE_MEASURE_TYPES:
        result.envelope_measure_types.add(measure_type)
        result.report_notes.append(
            (
                path,
                "approximated",
                f"envelope measure '{measure_type}' (params={params}) folded into the TABULA refurbishment variant",
            )
        )
    else:
        result.report_notes.append((path, "ignored", f"unknown measure type '{measure_type}'"))


def _numeric_param(params: dict, key: str) -> Any:
    """Return ``params[key]`` if it is a non-negative number, else ``None``."""
    value = params.get(key)
    if isinstance(value, (int, float)) and value >= 0:
        return value
    return None
