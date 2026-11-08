"""From a resolved case to the HiSim configurations of its components (FR2, FR3).

The building is a TABULA building whose every value the 5R1C model reads is overridden from the
case (decision D16): the carrier row is named because the configuration needs one, and
:meth:`BuildingInformation.value_origins` proves afterwards that nothing of it is used. The weather
is the catalogue station the case names, and the night setback of cases 640 and 940 is the existing
controller with a step at the end of the night (decision D18). Everything derived on the way is
collected in :class:`DerivedParameters` for ``parameters/<case>.json``.
"""

import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from hisim import loadtypes as lt
from hisim import utils
from hisim.components.building import BuildingConfig, BuildingInformation, ConfiguredWindow, ValueOrigin
from hisim.components.night_setback_controller import NightSetbackConfig
from hisim.components.weather import LocationEnum, WeatherConfig
from hisim.components.weather.calculation import read_epw_data
from hisim.config import ComponentID
from hisim.verification import derivation
from hisim.verification.schema import Case

#: The TABULA row every case names so that ``BuildingConfig`` has one; nothing of it is used.
CARRIER_BUILDING_CODE: str = "DE.N.SFH.05.Gen.ReEx.001.002"

#: Where the building's technical room is. Left unset, the field is sized from the carrier row's cellar
#: code; no component of a case is placed by it, so it acts on nothing, and setting it keeps the row
#: out (gap analysis §17).
TECHNICAL_ROOM_LOCATION: lt.InstallationLocation = lt.InstallationLocation.HEATED_ZONE

#: The set points a free-float case carries in its configuration, where the fields are mandatory;
#: no heater is connected, so they act on nothing.
FREE_FLOAT_HEATING_SETPOINT_IN_CELSIUS: float = 20.0
FREE_FLOAT_COOLING_SETPOINT_IN_CELSIUS: float = 27.0

#: Door area and U-value of a case: the cases have no door, so the area is 0 and the U-value idle.
DOOR_AREA_IN_M2: float = 0.0
DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN: float = 1.0

#: The transmission adjustment factor of every opaque surface: all face outdoor air (spec §3.2.1, D10).
TRANSMISSION_ADJUSTMENT_FACTOR: float = 1.0

#: Frame fraction and external shading factor of every window: the glazing fills the opening and
#: nothing shades it (ANSI/ASHRAE 140-2023, Table 7-10; the shading cases are not modelled).
FRAME_AREA_FRACTION: float = 0.0
EXTERNAL_SHADING_FACTOR: float = 1.0

#: The year the typical meteorological year is laid onto: a non-leap year, as the EPW has 8760 hours.
DEFAULT_YEAR: int = 2021


class CarrierRowError(ValueError):
    """Raised when a case's building would still take a value from the carrier TABULA row."""


@dataclass(frozen=True)
class DerivedParameters:
    """Everything the builder derived for one case, for ``parameters/<case>.json``."""

    case_id: str
    year: int
    mass: derivation.MassParameters
    specified_air_change_rate_per_hour: float
    air_change_rate_for_hisim_per_hour: float
    window_factors: Tuple[derivation.WindowFactor, ...]
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    u_values_by_kind: Dict[str, Optional[float]]
    areas_by_kind: Dict[str, float]
    heating_reference_temperature_in_celsius: float
    origins: Dict[str, ValueOrigin]

    def to_json_dict(self) -> Dict[str, Any]:
        """The parameters as plain data."""
        return {
            "case": self.case_id,
            "year": self.year,
            "mass": dataclasses.asdict(self.mass),
            "air_change_rate": {
                "specified_per_hour": self.specified_air_change_rate_per_hour,
                "for_hisim_per_hour": self.air_change_rate_for_hisim_per_hour,
                "site_air_density_in_kg_per_m3": derivation.SITE_AIR_DENSITY_IN_KG_PER_M3,
                "standard_air_density_in_kg_per_m3": derivation.STANDARD_AIR_DENSITY_IN_KG_PER_M3,
            },
            "windows": [dataclasses.asdict(factor) for factor in self.window_factors],
            "window_u_value_in_watt_per_m2_per_kelvin": self.window_u_value_in_watt_per_m2_per_kelvin,
            "u_values_by_kind": dict(self.u_values_by_kind),
            "areas_by_kind": dict(self.areas_by_kind),
            "heating_reference_temperature_in_celsius": self.heating_reference_temperature_in_celsius,
            "origins": {quantity: dataclasses.asdict(origin) for quantity, origin in self.origins.items()},
        }


@dataclass(frozen=True)
class BuiltCase:
    """The configurations of one case and what was derived for them."""

    case: Case
    building: BuildingConfig
    weather: WeatherConfig
    setback: Optional[NightSetbackConfig]
    parameters: DerivedParameters


def weather_file_path(case: Case) -> str:
    """The shipped weather file of the case's catalogue station."""
    _display_name, directory, subdirectory, file_stem, _source = LocationEnum[case.site.weather_location].value
    return os.path.join(utils.get_input_directory(), "weather", directory, subdirectory, file_stem)


def load_weather_frame(case: Case, year: int) -> pd.DataFrame:
    """The hourly raw frame of the case's EPW, laid onto ``year``, labelled in the station's standard time."""
    return read_epw_data(weather_file_path(case), year)


def derive(case: Case, weather: pd.DataFrame, year: int) -> Dict[str, Any]:
    """Apply the rules of spec §3.2 to a case: the values the configurations are built from.

    Args:
        case: the resolved case.
        weather: the hourly weather frame of :func:`load_weather_frame`.
        year: the simulated year.

    Returns:
        Dict[str, Any]: the derived values by name, as :func:`build` consumes them.
    """
    by_kind = derivation.surfaces_by_kind(case)
    u_values_by_kind = {
        kind: derivation.area_weighted_u_value(
            tuple((surface.area_in_m2, case.construction_of(surface).u_value_air_to_air_in_watt_per_m2_per_kelvin) for surface in surfaces)
        )
        for kind, surfaces in by_kind.items()
    }
    areas_by_kind = {kind: sum(surface.area_in_m2 for surface in surfaces) for kind, surfaces in by_kind.items()}
    window_factors = tuple(
        derivation.nonperpendicular_reduction_factor(
            window,
            case.glazing_of(window),
            weather,
            case.site.latitude_in_degrees,
            case.site.longitude_in_degrees,
            case.site.ground_reflectance,
        )
        for window in case.windows
    )
    return {
        "year": year,
        "mass": derivation.mass_parameters(case),
        "air_change_rate_for_hisim_per_hour": derivation.altitude_adjusted_air_change_rate_per_hour(case.infiltration_air_changes_per_hour),
        "window_factors": window_factors,
        "window_u_value": derivation.area_weighted_u_value(
            tuple((window.area_in_m2, case.glazing_of(window).u_value_air_to_air_in_watt_per_m2_per_kelvin) for window in case.windows)
        ),
        "u_values_by_kind": u_values_by_kind,
        "areas_by_kind": areas_by_kind,
        "heating_reference_temperature": float(weather["T"].min()),
    }


def building_config(case: Case, derived: Dict[str, Any], weather: WeatherConfig, name: str = "Building") -> BuildingConfig:
    """The building of a case: the carrier row with every value overridden from the case.

    Args:
        case: the resolved case.
        derived: the values of :func:`derive`.
        weather: the weather configuration whose identity the building carries.
        name: the component's instance name.

    Returns:
        BuildingConfig: concrete, ready for ``BuildingInformation`` and the Building.
    """
    thermostat = case.thermostat
    mass: derivation.MassParameters = derived["mass"]
    factors = {factor.window: factor for factor in derived["window_factors"]}
    windows = [
        ConfiguredWindow(
            area_in_m2=window.area_in_m2,
            azimuth_angle_in_degrees=window.azimuth_in_degrees,
            tilt_angle_in_degrees=window.tilt_in_degrees,
            glass_solar_transmittance=case.glazing_of(window).shgc_normal,
            frame_area_fraction_reduction_factor=FRAME_AREA_FRACTION,
            nonperpendicular_reduction_factor=factors[window.name].nonperpendicular_reduction_factor,
            external_shading_vertical_reduction_factor=EXTERNAL_SHADING_FACTOR,
        )
        for window in case.windows
    ]
    heating = FREE_FLOAT_HEATING_SETPOINT_IN_CELSIUS if thermostat is None else thermostat.heating_setpoint_in_celsius
    cooling = FREE_FLOAT_COOLING_SETPOINT_IN_CELSIUS if thermostat is None else thermostat.cooling_setpoint_in_celsius
    return BuildingConfig(
        component_id=ComponentID(name=name),
        building_code=CARRIER_BUILDING_CODE,
        technical_room_location=TECHNICAL_ROOM_LOCATION,
        initial_internal_temperature_in_celsius=heating,
        absolute_conditioned_floor_area_in_m2=case.floor_area_in_m2,
        number_of_apartments=1.0,
        floor_u_value_in_watt_per_m2_per_kelvin=derived["u_values_by_kind"]["floor"],
        floor_area_in_m2=derived["areas_by_kind"]["floor"],
        facade_u_value_in_watt_per_m2_per_kelvin=derived["u_values_by_kind"]["wall"],
        facade_area_in_m2=derived["areas_by_kind"]["wall"],
        roof_u_value_in_watt_per_m2_per_kelvin=derived["u_values_by_kind"]["roof"],
        roof_area_in_m2=derived["areas_by_kind"]["roof"],
        window_u_value_in_watt_per_m2_per_kelvin=derived["window_u_value"],
        door_u_value_in_watt_per_m2_per_kelvin=DOOR_U_VALUE_IN_WATT_PER_M2_PER_KELVIN,
        door_area_in_m2=DOOR_AREA_IN_M2,
        windows=windows,
        use_air_change_rate_per_hour=0.0,
        infiltration_air_change_rate_per_hour=derived["air_change_rate_for_hisim_per_hour"],
        conditioned_air_volume_in_m3=case.volume_in_m3,
        floor_transmission_adjustment_factor=TRANSMISSION_ADJUSTMENT_FACTOR,
        facade_transmission_adjustment_factor=TRANSMISSION_ADJUSTMENT_FACTOR,
        roof_transmission_adjustment_factor=TRANSMISSION_ADJUSTMENT_FACTOR,
        thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin=mass.thermal_capacity_in_joule_per_kelvin,
        effective_mass_area_in_m2=mass.effective_mass_area_in_m2,
        total_internal_surface_area_in_m2=mass.total_internal_surface_area_in_m2,
        set_heating_temperature_in_celsius=heating,
        set_cooling_temperature_in_celsius=cooling,
        enable_opening_windows=False,
        heating_reference_temperature_in_celsius=derived["heating_reference_temperature"],
        weather_identity=weather.identity(),
    )


def weather_config(case: Case, derived: Dict[str, Any], name: str = "Weather") -> WeatherConfig:
    """The weather of a case: its catalogue station, with the EPW's coldest hour as the design temperature."""
    return WeatherConfig.for_location(
        name,
        LocationEnum[case.site.weather_location],
        heating_reference_temperature_in_celsius=derived["heating_reference_temperature"],
    )


def setback_config(case: Case, name: str = "NightSetback") -> Optional[NightSetbackConfig]:
    """The night-setback controller of a case with a setback, stepping at the end of the night (D18); else ``None``."""
    if case.thermostat is None or case.thermostat.setback is None:
        return None
    setback = case.thermostat.setback
    return NightSetbackConfig(
        component_id=ComponentID(name=name),
        setback_delta_in_kelvin=setback.setpoint_in_celsius - case.thermostat.heating_setpoint_in_celsius,
        night_start_time_in_hours=setback.from_hour,
        night_end_time_in_hours=setback.to_hour,
    )


def check_origins(config: BuildingConfig) -> Dict[str, ValueOrigin]:
    """Derive the building once and refuse it if any quantity still comes from the carrier row.

    Args:
        config: the building configuration of :func:`building_config`.

    Returns:
        Dict[str, ValueOrigin]: the origin record, for the parameters output.

    Raises:
        CarrierRowError: naming every quantity whose origin is the TABULA row.
    """
    origins = BuildingInformation(config=config).value_origins()
    from_row = sorted(quantity for quantity, origin in origins.items() if origin.source == ValueOrigin.TABULA_ROW)
    if from_row:
        raise CarrierRowError(
            f"the building of the case still takes {', '.join(from_row)} from the carrier row {CARRIER_BUILDING_CODE}; "
            "every value the 5R1C model reads must come from the case"
        )
    return origins


def build(case: Case, year: int = DEFAULT_YEAR) -> BuiltCase:
    """Build the configurations of a case and the record of what was derived for them.

    Args:
        case: the resolved case.
        year: the year the typical meteorological year is laid onto.

    Returns:
        BuiltCase: the building, weather and setback configurations and the derived parameters.
    """
    weather_frame = load_weather_frame(case, year)
    derived = derive(case, weather_frame, year)
    weather = weather_config(case, derived)
    building = building_config(case, derived, weather)
    origins = check_origins(building)
    parameters = DerivedParameters(
        case_id=case.case_id,
        year=year,
        mass=derived["mass"],
        specified_air_change_rate_per_hour=case.infiltration_air_changes_per_hour,
        air_change_rate_for_hisim_per_hour=derived["air_change_rate_for_hisim_per_hour"],
        window_factors=derived["window_factors"],
        window_u_value_in_watt_per_m2_per_kelvin=derived["window_u_value"],
        u_values_by_kind=derived["u_values_by_kind"],
        areas_by_kind=derived["areas_by_kind"],
        heating_reference_temperature_in_celsius=derived["heating_reference_temperature"],
        origins=origins,
    )
    return BuiltCase(case=case, building=building, weather=weather, setback=setback_config(case), parameters=parameters)
