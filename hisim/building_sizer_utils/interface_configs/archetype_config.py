"""Archetype config module."""

# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Optional, List

from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import TravelRouteSets
from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.loadtypes import HeatingSystems


@dataclass_json
@dataclass
class ArcheTypeConfig:

    """Archetype config class.

    Defines the system config for the modular household.
    """

    building_name: str = "BUI1"
    building_id: str = "default_building"
    pv_azimuth: float = 180
    pv_tilt: float = 30
    pv_rooftop_capacity_in_kilowatt: Optional[float] = None
    building_code: str = "DE.N.SFH.05.Gen.ReEx.001.002"
    conditioned_floor_area_in_m2: float = 121.2
    number_of_dwellings_per_building: int = 1
    norm_heating_load_in_kilowatt: Optional[float] = None
    weather_location: str = "AACHEN"
    lpg_households: List[str] = field(default_factory=lambda: ["CHR01_Couple_both_at_Work"])

    # #: considered mobility options, passed as inputs to the LoadProfileGenerator and considered to model cars
    # mobility_set: Optional[JsonReference] = None
    # # field(
    # #     default_factory=lambda: TransportationDeviceSets.Bus_and_one_30_km_h_Car  # type: ignore
    # #     )
    # #: average daily commuting distance in kilometers, passed as input to the LoadProfileGenerator and considered to model consumption of cars
    # mobility_distance: Optional[JsonReference] = field(
    #     default_factory=lambda: TravelRouteSets.Travel_Route_Set_for_15km_Commuting_Distance
    # )  # type: ignore


# def create_archetype_config_file() -> None:
#     """Component Cost file is created."""

#     config_file=ArcheTypeConfig()
#     config_file_written = config_file.to_json()

#     with open('arche_type_config.json', 'w', encoding="utf-8") as outfile:
#         outfile.write(config_file_written)
