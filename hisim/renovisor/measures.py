"""Application of RenoVisor renovation measures to a home inventory (spec section 3).

Under ``--variant measures`` every entry of the request's ``measures`` array is applied to a
deep copy of the request's ``homeInputs`` field (the JSON key from spec section 3, received in
code as the ``home_inputs`` parameter); the original request is never mutated. Measures are
applied as a single package, sequentially in list order, and the modified copy is then handed
to the mapper (see :mod:`hisim.renovisor.mapping`).

Measure schema
--------------

Each entry in the ``measures`` array is a JSON object ``{"type": "<measure_type>",
"params": {...}}``. The ``"type"`` string selects the measure; ``"params"`` is an optional
object of measure-specific parameters. Entries whose ``"type"`` is missing or not a string,
and entries with an unknown ``"type"``, are recorded as *ignored* mapping-report notes rather
than raising -- v1 treats the measures list as best-effort.

The recognised measure types and their effect on the inventory copy:

- ``heat_pump`` -- sets ``heating.primary = "heat_pump"``, which drives setup selection (spec
  section 4.1). ``params.kW`` is accepted but ignored in v1: the chosen building-sizer setup
  auto-sizes the heat pump; the requested size is echoed in the report note.
- ``pv`` -- sets ``pv.kWp = params.kWp``. ``params.kWp`` must be a non-negative number; a
  missing or non-numeric value leaves the entry *ignored*.
- ``battery`` -- sets ``battery.kWh = params.kWh``. ``params.kWh`` must be a non-negative
  number; a missing or non-numeric value leaves the entry *ignored*. The capacity is later
  auto-sized by the setup, so the applied value is flagged *approximated*.
- ``solar_thermal`` -- sets ``solarThermal.mode = "hot_water"`` only when the current mode is
  absent or ``"none"``; a mode already set on the inventory is preserved (spec section 4.1).
- ``roof_insulation``, ``wall_insulation``, ``floor_insulation``, ``windows``, ``doors``,
  ``air_sealing``, ``ventilation`` -- *envelope* measures (see
  :data:`ENVELOPE_MEASURE_TYPES`). They are collected by distinct type into
  :attr:`MeasureApplication.envelope_measure_types` and later folded into the TABULA
  refurbishment variant (spec section 4.2); they do not set individual U-values in v1.

Combination and interaction rules
---------------------------------

Application is sequential in list order over the shared inventory copy, so later entries
override earlier ones for the same field:

- ``pv.kWp`` and ``battery.kWh`` are *overwritten*, not accumulated: a second ``pv`` or
  ``battery`` measure replaces the value from the first, and duplicate measures do not sum.
  The final capacity is the value carried by the last applicable entry of that type.
- ``heat_pump`` and ``solar_thermal`` write single-valued fields, so repeating them has no
  effect beyond the last value applied.
- Envelope measures are deduplicated by *type*: repeating the same envelope measure type
  counts once towards the refurbishment-variant bump, regardless of how many times it appears.

Downstream, the distinct envelope measure types raise the TABULA refurbishment variant floor
in :mod:`hisim.renovisor.mapping`: one or two distinct types pull it to at least ``.002``,
three or more to ``.003``, taken as the maximum with the variant implied by ``envelopeState``.

Physical-constraint validation (v1 limitation)
----------------------------------------------

v1 does **not** validate measure parameters against physical constraints. There is no maximum
insulation thickness, airtightness (n50) limit, or U-value range check: the ``params`` of
envelope measures are recorded in the mapping report as *approximated* but are otherwise
dropped before the TABULA refurbishment variant is selected (see spec section 9, open items).
Only the numeric ``params.kWp`` / ``params.kWh`` of ``pv`` / ``battery`` are range-checked (for
being non-negative numbers); all other parameters are passed through to the report note or
ignored as described above.
"""

import copy
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set, Tuple, Union

# Measure types that describe the building envelope; they influence the TABULA refurbishment
# variant instead of individual parameters (spec sections 3 and 4.2).
ENVELOPE_MEASURE_TYPES: Set[str] = {
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
    """Result of applying all measures to a copy of the home inventory.

    Attributes:
        home_inputs: The modified deep copy of the original home inventory.
        envelope_measure_types: Distinct envelope measure types encountered,
            later folded into the TABULA refurbishment variant.
        report_notes: One ``(path, status, note)`` triple per measure entry,
            for the mapping report (statuses per spec section 6).
    """

    home_inputs: dict[str, Any]
    envelope_measure_types: Set[str] = field(default_factory=set)
    report_notes: List[ReportNote] = field(default_factory=list)


def apply_measures(home_inputs: dict[str, Any], measures: List[dict[str, Any]]) -> MeasureApplication:
    """Apply every measure to a deep copy of *home_inputs* as a single package.

    The original *home_inputs* is never mutated; a deep copy is taken and modified
    in place. Envelope measures are not written to individual U-values in v1 —
    they are collected by distinct type for the TABULA refurbishment variant.

    Args:
        home_inputs: The original home inventory dict.
        measures: Ordered list of measure entries. Each entry is a dict with a
            ``"type"`` key (str) and an optional ``"params"`` dict. Entries
            lacking a string ``"type"`` and unknown measure types are recorded
            as ignored notes rather than raising.

    Returns:
        A :class:`MeasureApplication` whose ``home_inputs`` is the modified copy,
        ``envelope_measure_types`` collects the distinct envelope measure types
        encountered, and ``report_notes`` holds one ``(path, status, note)``
        triple per measure entry.
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


def _apply_single_measure(result: MeasureApplication, path: str, measure_type: str, params: dict[str, Any]) -> None:
    """Apply one measure to ``result.home_inputs`` and record its report note."""
    home = result.home_inputs
    if measure_type == "heat_pump":
        home.setdefault("heating", {})["primary"] = "heat_pump"
        note = "heating.primary set to 'heat_pump'"
        if "kW" in params:
            note += f" (requested kW={params['kW']} ignored: the setup auto-sizes the heat pump)"
        result.report_notes.append((path, "used", note))
    elif measure_type == "pv":
        power_in_kWp = _numeric_param(params, "kWp")
        if power_in_kWp is None:
            result.report_notes.append((path, "ignored", "pv measure without a numeric 'kWp' param"))
            return
        home.setdefault("pv", {})["kWp"] = power_in_kWp
        result.report_notes.append((path, "used", f"pv.kWp set to {power_in_kWp} (new total)"))
    elif measure_type == "battery":
        energy_in_kWh = _numeric_param(params, "kWh")
        if energy_in_kWh is None:
            result.report_notes.append((path, "ignored", "battery measure without a numeric 'kWh' param"))
            return
        home.setdefault("battery", {})["kWh"] = energy_in_kWh
        result.report_notes.append(
            (path, "approximated", f"battery.kWh set to {energy_in_kWh} (new total); size is auto-sized by the setup")
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


def _numeric_param(params: dict[str, Any], key: str) -> Optional[Union[int, float]]:
    """Return ``params[key]`` if it is a non-negative number, else ``None``."""
    value = params.get(key)
    if isinstance(value, (int, float)) and value >= 0:
        return value
    return None
