"""Archetype config module."""

from dataclasses import dataclass, field
from typing import Optional

from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class ArcheTypeConfig:
    """Archetype config class.

    Defines the system config for the modular household.

    Photovoltaic orientation is given by two angle fields whose values are
    always expressed in **degrees**, never radians. This matches the convention
    used by the downstream PV component
    (:class:`~hisim.components.generic_pv_system.PVSystemConfig`), which
    interprets azimuth "from north in °" and tilt "from horizontal":

    - ``pv_azimuth``: panel azimuth angle in degrees, measured clockwise from
      north. The default ``180`` corresponds to a south-facing panel.
    - ``pv_tilt``: panel tilt angle in degrees from the horizontal plane. The
      default ``30`` corresponds to a 30° tilt.

    Callers must pass these values in degrees; do not mix degrees and radians.
    """

    building_name: str = "BUI1"
    building_id: str = "default_building"
    #: PV panel azimuth in degrees, measured clockwise from north (180° = south).
    pv_azimuth: float = 180
    #: PV panel tilt in degrees from the horizontal plane.
    pv_tilt: float = 30
    pv_rooftop_capacity_in_kilowatt: Optional[float] = None
    pv_rooftop_generation_in_kilowatthour: Optional[float] = None
    building_code: str = "DE.N.SFH.05.Gen.ReEx.001.002"
    conditioned_floor_area_in_m2: float = 121.2
    number_of_dwellings_per_building: int = 1
    norm_heating_load_in_kilowatt: Optional[float] = None
    weather_location: str = "AACHEN"
    weather_try_region: int = 6

    weather_filepath: Optional[str] = None
    weather_datasource: Optional[str] = None

    building_postal_code: str = "52062"
    building_location: str = "Aachen"
    lpg_households: list[str] = field(default_factory=lambda: ["CHR01_Couple_both_at_Work"])
    commodity: str = "electric"
    supply_level: str = "central_heating"
    building_density_within_buffer_area_of_100m_radius: float = 0.09
    nearest_neighbor_distance_m: float = 20.0
    construction_year: int = 1964
    coordinates_latitude: float = 50.77664
    coordinates_longitude: float = 6.0834

    # Optional building-envelope override. If any of these are set, the value is passed through to
    # the Building component and used instead of the TABULA archetype default. If left None (default),
    # the envelope is derived from the TABULA building_code exactly as before (opt-in, backward compatible).
    building_heat_capacity_class: Optional[str] = None
    floor_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    floor_area_in_m2: Optional[float] = None
    facade_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    facade_area_in_m2: Optional[float] = None
    roof_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    roof_area_in_m2: Optional[float] = None
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    window_area_in_m2: Optional[float] = None
    door_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    door_area_in_m2: Optional[float] = None
    thermal_bridging_heat_conductance_in_watt_per_kelvin: Optional[float] = None
    ventilation_heat_conductance_in_watt_per_kelvin: Optional[float] = None
