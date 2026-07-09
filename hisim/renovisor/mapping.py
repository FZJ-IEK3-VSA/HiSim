"""Pure translation of a RenoVisor request into a system-setup choice + ModularHouseholdConfig.

This module has no I/O besides the cached TABULA table read in :mod:`tabula_ie`; it turns a
request dict into a :class:`TranslationResult` so the mapping can be unit-tested exhaustively
(spec sections 4 and 6).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from hisim.building_sizer_utils.interface_configs.archetype_config import ArcheTypeConfig
from hisim.building_sizer_utils.interface_configs.modular_household_config import ModularHouseholdConfig
from hisim.building_sizer_utils.interface_configs.system_config import EnergySystemConfig
from hisim.loadtypes import ComponentType, HeatingSystems
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.measures import apply_measures
from hisim.renovisor.schema import RequestValidationError
from hisim.renovisor.tabula_ie import TabulaLookupError, select_building_code

# heating.primary -> (setup file without solar thermal, HeatingSystems member, approximation note or None)
_SETUP_BY_PRIMARY: Dict[str, Tuple[str, HeatingSystems, Optional[str]]] = {
    "gas": ("household_gas_building_sizer.py", HeatingSystems.GAS_HEATING, None),
    "oil": ("household_oil_building_sizer.py", HeatingSystems.OIL_HEATING, None),
    "heat_pump": ("household_heatpump_building_sizer.py", HeatingSystems.HEAT_PUMP, None),
    "direct_electric": ("household_electric_heating_building_sizer.py", HeatingSystems.ELECTRIC_HEATING, None),
    "district": ("household_district_heating_building_sizer.py", HeatingSystems.DISTRICT_HEATING, None),
    "wood": (
        "household_pellets_building_sizer.py",
        HeatingSystems.PELLET_HEATING,
        "wood heating approximated by the pellet heating setup",
    ),
    "solid_fuel": (
        "household_pellets_building_sizer.py",
        HeatingSystems.PELLET_HEATING,
        "solid fuel (coal/peat) approximated by the pellet heating setup — no dedicated setup exists",
    ),
    "irish_cooking_range": (
        "household_pellets_building_sizer.py",
        HeatingSystems.PELLET_HEATING,
        "Irish cooking range approximated by the pellet heating setup",
    ),
}

# Primaries that have a dedicated solar-thermal setup variant (spec section 4.1).
_SOLAR_THERMAL_SETUPS: Dict[str, Tuple[str, HeatingSystems]] = {
    "gas": ("household_gas_solar_thermal_building_sizer.py", HeatingSystems.GAS_SOLAR_THERMAL),
    "heat_pump": ("household_heatpump_solar_thermal_building_sizer.py", HeatingSystems.HEAT_PUMP_SOLAR_THERMAL),
}

_TABULA_TYPE_BY_DWELLING: Dict[str, Tuple[str, Optional[str]]] = {
    "detached_sfh": ("SFH", None),
    "bungalow": ("SFH", "bungalow approximated by the single-family-house archetype"),
    "other": ("SFH", "dwelling type 'other' approximated by the single-family-house archetype"),
    "semi_detached_sfh": ("TH", "semi-detached approximated by the terraced-house archetype"),
    "terraced_sfh": ("TH", None),
    "apartment": ("AB", None),
}

_REFURB_VARIANT_BY_ENVELOPE_STATE = {"unrenovated": 1, "usual_refurb": 2, "advanced_refurb": 3}

_AZIMUTH_BY_ORIENTATION: Dict[str, Tuple[float, Optional[str]]] = {
    "south": (180.0, None),
    "south_east": (135.0, None),
    "south_west": (225.0, None),
    "east_west": (90.0, "east/west split array approximated as a single east-facing array"),
}

# occupants -> LPG predefined household (spec section 4.4); 5+ falls back to the last entry.
_LPG_HOUSEHOLD_BY_OCCUPANTS = {
    1: "CHR07_Single_with_work",
    2: "CHR01_Couple_both_at_Work",
    3: "CHR03_Family_1_child_both_at_work",
    4: "CHR27_Family_both_at_work_2_children",
}
_LPG_HOUSEHOLD_LARGE = "CHR41_Family_with_3_children_both_at_work"

_BUILDING_LOCATION_BY_COUNTRY = {"IE": "Ireland", "DE": "Germany", "GB": "United Kingdom"}


class MappingError(RequestValidationError):
    """Raised when a request cannot be mapped onto the available setups/data (exit code 2)."""


@dataclass
class ReportEntry:
    """One mapping-report line: what happened to a request field (spec section 6)."""

    path: str
    status: str  # used | approximated | defaulted | ignored
    note: str


class MappingReport:
    """Collects per-field report entries and fills in untouched leaves at the end."""

    def __init__(self) -> None:
        """Create an empty report."""
        self._entries: Dict[str, ReportEntry] = {}

    def add(self, path: str, status: str, note: str = "") -> None:
        """Record (or overwrite) the entry for *path*."""
        self._entries[path] = ReportEntry(path=path, status=status, note=note)

    def used(self, path: str, note: str = "") -> None:
        """Record *path* as directly used by the translation."""
        self.add(path, "used", note)

    def approximated(self, path: str, note: str = "") -> None:
        """Record *path* as represented only approximately."""
        self.add(path, "approximated", note)

    def defaulted(self, path: str, note: str = "") -> None:
        """Record *path* as absent and replaced by a default."""
        self.add(path, "defaulted", note)

    def ignored(self, path: str, note: str = "") -> None:
        """Record *path* as accepted but not used."""
        self.add(path, "ignored", note)

    def finalize(self, request: dict) -> None:
        """Add an ``ignored`` entry for every request leaf no other entry covers."""
        for leaf_path in _iter_leaf_paths(request):
            if not self._is_covered(leaf_path):
                self.add(leaf_path, "ignored", "not yet supported by the translator (v1)")

    def _is_covered(self, leaf_path: str) -> bool:
        """Return whether *leaf_path* equals or lies under an already recorded path."""
        if leaf_path in self._entries:
            return True
        for recorded in self._entries:
            if leaf_path.startswith(recorded) and leaf_path[len(recorded)] in ".[":
                return True
        return False

    def to_list(self) -> List[Dict[str, str]]:
        """Return the entries as JSON-ready dicts, sorted by path."""
        return [
            {"path": entry.path, "status": entry.status, "note": entry.note}
            for entry in sorted(self._entries.values(), key=lambda item: item.path)
        ]


@dataclass
class TranslationResult:
    """Outcome of the translation: setup choice, generated config and the mapping report."""

    setup_filename: str
    modular_household_config: ModularHouseholdConfig
    report: MappingReport


def translate(request: dict, variant: str, job_id: str) -> TranslationResult:
    """Translate a validated RenoVisor *request* into a setup selection and module config.

    *variant* is ``"base"`` (inventory as-is) or ``"measures"`` (all measures applied first).
    Raises :class:`MappingError` when the request cannot be mapped (unknown country, missing
    TABULA data, ...); those are validation failures from the caller's point of view.
    """
    report = MappingReport()
    report.used("contractVersion", f"contract {request.get('contractVersion')}")

    home, envelope_measure_types = _resolve_home_inputs(request, variant, report)
    setup_filename, heating_system = _select_setup(home, report)
    energy_system = _build_energy_system_config(home, heating_system, report)
    archetype = _build_archetype_config(request, home, envelope_measure_types, job_id, report)

    report.finalize(request)
    config = ModularHouseholdConfig(energy_system_config_=energy_system, archetype_config_=archetype)
    return TranslationResult(setup_filename=setup_filename, modular_household_config=config, report=report)


def build_mapping_report_dict(result: TranslationResult, job_id: str, variant: str) -> dict:
    """Build the JSON payload of ``renovisor_mapping_report.json`` (spec section 6)."""
    return {
        "jobId": job_id,
        "variant": variant,
        "translatorVersion": TRANSLATOR_VERSION,
        "selectedSetup": result.setup_filename,
        "moduleConfig": result.modular_household_config.to_dict(),  # type: ignore[attr-defined]
        "fields": result.report.to_list(),
    }


def _resolve_home_inputs(request: dict, variant: str, report: MappingReport) -> Tuple[dict, set]:
    """Return the effective home inputs and envelope measure types for the chosen variant."""
    home: dict = request["homeInputs"]
    measures: List[dict] = request["measures"]
    if variant == "measures":
        application = apply_measures(home, measures)
        for path, status, note in application.report_notes:
            report.add(path, status, note)
        if not measures:
            report.used("measures", "empty measure list: identical to the baseline")
        return application.home_inputs, application.envelope_measure_types
    if measures:
        report.ignored("measures", "variant=base: measures not applied")
    else:
        report.used("measures", "empty measure list = baseline")
    return home, set()


def _select_setup(home: dict, report: MappingReport) -> Tuple[str, HeatingSystems]:
    """Pick the system setup from heating.primary and solarThermal.mode (spec section 4.1)."""
    primary = home["heating"]["primary"]
    solar_mode = (home.get("solarThermal") or {}).get("mode") or "none"
    wants_solar_thermal = solar_mode != "none"

    if primary not in _SETUP_BY_PRIMARY:
        raise MappingError(f"No system setup available for heating.primary '{primary}'.")

    if wants_solar_thermal and primary in _SOLAR_THERMAL_SETUPS:
        setup_filename, heating_system = _SOLAR_THERMAL_SETUPS[primary]
        report.used("homeInputs.heating.primary", f"selected setup {setup_filename}")
        report.used("homeInputs.solarThermal.mode", f"'{solar_mode}': solar-thermal setup variant selected")
        return setup_filename, heating_system

    setup_filename, heating_system, approximation_note = _SETUP_BY_PRIMARY[primary]
    if approximation_note is not None:
        report.approximated("homeInputs.heating.primary", f"{approximation_note}; selected setup {setup_filename}")
    else:
        report.used("homeInputs.heating.primary", f"selected setup {setup_filename}")
    if wants_solar_thermal:
        report.ignored(
            "homeInputs.solarThermal.mode",
            f"no setup combines '{primary}' heating with solar thermal; solar thermal dropped",
        )
    else:
        report.used("homeInputs.solarThermal.mode", "'none'")
    return setup_filename, heating_system


def _build_energy_system_config(home: dict, heating_system: HeatingSystems, report: MappingReport) -> EnergySystemConfig:
    """Build the :class:`EnergySystemConfig` (spec section 4.3)."""
    emitter = home["heating"].get("emitter")
    if emitter == "underfloor":
        heat_distribution = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
        report.used("homeInputs.heating.emitter", "floor heating distribution")
    elif emitter in ("steel_panel_radiators", "cast_iron"):
        heat_distribution = ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
        report.used("homeInputs.heating.emitter", "radiator distribution")
    else:
        heat_distribution = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
        note = "no emitter given; using floor heating (building-sizer default)"
        if emitter is not None:
            note = f"unknown emitter '{emitter}'; using floor heating (building-sizer default)"
        report.defaulted("homeInputs.heating.emitter", note)

    pv_kilowatt_peak = float((home.get("pv") or {}).get("kWp") or 0.0)
    share_of_maximum_pv_potential = 1.0 if pv_kilowatt_peak > 0 else 0.0

    battery_kilowatt_hours = float((home.get("battery") or {}).get("kWh") or 0.0)
    use_battery_and_ems = battery_kilowatt_hours > 0
    if use_battery_and_ems:
        report.approximated(
            "homeInputs.battery.kWh",
            f"battery enabled; requested {battery_kilowatt_hours} kWh is auto-sized by the setup",
        )
    else:
        report.used("homeInputs.battery.kWh", "0: battery and EMS disabled")

    return EnergySystemConfig(
        heating_system=heating_system,
        heat_distribution_system=heat_distribution,
        share_of_maximum_pv_potential=share_of_maximum_pv_potential,
        use_battery_and_ems=use_battery_and_ems,
    )


def _build_archetype_config(
    request: dict, home: dict, envelope_measure_types: set, job_id: str, report: MappingReport
) -> ArcheTypeConfig:
    """Build the :class:`ArcheTypeConfig` (spec section 4.2)."""
    location = request["location"]
    country = location["countryCode"]
    weather_location, coordinates = _resolve_weather_location(country, report)

    building_code = _resolve_building_code(home, envelope_measure_types, country, report)
    lpg_household = _resolve_lpg_household(home, report)
    pv_capacity, azimuth, tilt = _resolve_pv(home, report)

    report.used("homeInputs.floorAreaM2", "conditioned floor area")

    postal_code = location.get("eircodeOrPostcode")
    if postal_code:
        report.used("location.eircodeOrPostcode", "stored as building postal code")
    region = location.get("region")
    if region:
        report.ignored("location.region", "single weather station per country in v1")

    archetype = ArcheTypeConfig(
        building_name="BUI1",
        building_id=job_id,
        pv_azimuth=azimuth,
        pv_tilt=tilt,
        pv_rooftop_capacity_in_kilowatt=pv_capacity,
        building_code=building_code,
        conditioned_floor_area_in_m2=float(home["floorAreaM2"]),
        number_of_dwellings_per_building=1,
        weather_location=weather_location,
        building_postal_code=str(postal_code) if postal_code else "",
        building_location=str(region) if region else _BUILDING_LOCATION_BY_COUNTRY.get(country, country),
        lpg_households=[lpg_household],
        construction_year=int(home["constructionYear"]),
    )
    if coordinates is not None:
        archetype.coordinates_latitude, archetype.coordinates_longitude = coordinates
    return archetype


def _resolve_weather_location(country: str, report: MappingReport) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Map the country code to a weather ``LocationEnum`` member name plus station coordinates."""
    from hisim.components.weather import LocationEnum  # heavy import kept out of module load

    member_name = country if hasattr(LocationEnum, country) else ("AACHEN" if country == "DE" else None)
    if member_name is None:
        raise MappingError(f"No weather location available for country code '{country}'.")
    report.used("location.countryCode", f"weather location '{member_name}'")

    coordinates: Optional[Tuple[float, float]] = None
    weather_file = LocationEnum[member_name].value[3]
    coordinate_match = re.search(r"_(-?\d+\.\d+)_(-?\d+\.\d+)_\d{4}\.csv$", str(weather_file))
    if coordinate_match is not None:
        coordinates = (float(coordinate_match.group(1)), float(coordinate_match.group(2)))
    return member_name, coordinates


def _resolve_building_code(home: dict, envelope_measure_types: set, country: str, report: MappingReport) -> str:
    """Select the TABULA building code (spec section 4.2)."""
    dwelling_type = home["dwellingType"]
    tabula_type, type_note = _TABULA_TYPE_BY_DWELLING[dwelling_type]
    if type_note is not None:
        report.approximated("homeInputs.dwellingType", f"{type_note} ({tabula_type})")
    else:
        report.used("homeInputs.dwellingType", f"TABULA building type {tabula_type}")

    envelope_state = home.get("envelopeState")
    if envelope_state is None:
        state_variant = 1
        report.defaulted("homeInputs.envelopeState", "absent; assuming 'unrenovated'")
    elif envelope_state in _REFURB_VARIANT_BY_ENVELOPE_STATE:
        state_variant = _REFURB_VARIANT_BY_ENVELOPE_STATE[envelope_state]
        report.used("homeInputs.envelopeState", f"TABULA refurbishment variant floor .00{state_variant}")
    else:
        state_variant = 1
        report.defaulted("homeInputs.envelopeState", f"unknown value '{envelope_state}'; assuming 'unrenovated'")

    if len(envelope_measure_types) >= 3:
        measure_variant = 3
    elif envelope_measure_types:
        measure_variant = 2
    else:
        measure_variant = 1
    refurbishment_variant = max(state_variant, measure_variant)

    try:
        selection = select_building_code(country, tabula_type, int(home["constructionYear"]), refurbishment_variant)
    except TabulaLookupError as error:
        raise MappingError(str(error)) from error
    note = f"TABULA code {selection.building_code}"
    if selection.notes:
        note += "; " + "; ".join(selection.notes)
        report.approximated("homeInputs.constructionYear", note)
    else:
        report.used("homeInputs.constructionYear", note)

    # Envelope element detail is only represented through the refurbishment variant in v1.
    for element in ("roof", "walls", "floor", "windows", "doors"):
        if element in home:
            report.approximated(
                f"homeInputs.{element}",
                f"envelope detail folded into TABULA refurbishment variant .00{refurbishment_variant}",
            )
    return selection.building_code


def _resolve_lpg_household(home: dict, report: MappingReport) -> str:
    """Map the occupant count to an LPG predefined household (spec section 4.4)."""
    occupants = int(home["occupants"])
    household = _LPG_HOUSEHOLD_BY_OCCUPANTS.get(occupants, _LPG_HOUSEHOLD_LARGE)
    report.used("homeInputs.occupants", f"LPG household {household}")
    return household


def _resolve_pv(home: dict, report: MappingReport) -> Tuple[Optional[float], float, float]:
    """Resolve PV capacity, azimuth and tilt from the inventory (spec section 4.2)."""
    pv: dict = home.get("pv") or {}
    kilowatt_peak = float(pv.get("kWp") or 0.0)
    capacity: Optional[float]
    if kilowatt_peak > 0:
        capacity = kilowatt_peak
        report.used("homeInputs.pv.kWp", f"PV capacity {kilowatt_peak} kWp")
    else:
        capacity = None
        report.used("homeInputs.pv.kWp", "0: no PV built (share of PV potential set to 0)")

    orientation = pv.get("orientation")
    if orientation is None:
        azimuth = 180.0
        report.defaulted("homeInputs.pv.orientation", "absent; assuming south (azimuth 180°)")
    elif orientation in _AZIMUTH_BY_ORIENTATION:
        azimuth, orientation_note = _AZIMUTH_BY_ORIENTATION[orientation]
        if orientation_note is not None:
            report.approximated("homeInputs.pv.orientation", f"{orientation_note} (azimuth {azimuth}°)")
        else:
            report.used("homeInputs.pv.orientation", f"azimuth {azimuth}°")
    else:
        azimuth = 180.0
        report.defaulted("homeInputs.pv.orientation", f"unknown value '{orientation}'; assuming south")

    roof_construction = (home.get("roof") or {}).get("construction")
    tilt = 10.0 if roof_construction == "flat" else 30.0
    return capacity, azimuth, tilt


def _iter_leaf_paths(value: Any, prefix: str = "") -> Iterator[str]:
    """Yield dotted/indexed paths of every leaf in a nested JSON-like structure."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_leaf_paths(child, child_prefix)
    elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
        for index, item in enumerate(value):
            yield from _iter_leaf_paths(item, f"{prefix}[{index}]")
    else:
        if prefix:
            yield prefix
