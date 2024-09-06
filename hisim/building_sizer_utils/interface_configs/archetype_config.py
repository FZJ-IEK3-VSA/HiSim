"""Archetype config module."""

# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Optional

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

    #: modular household template of the LoadProfileGenerator, used to get the electrical- and hot water consumption profile (https://www.loadprofilegenerator.de/)
    # for an interface to the LoadProfileGenerator the UTSP is needed
    occupancy_profile_utsp: Optional[JsonReference] = None
    # field(
    #     default_factory=lambda: Households.CHR02_Couple_30_64_age_with_work  # type: ignore
    # )
    #: reference to stored electricity consumption and hot water consumption data, no interface to LoadProfileGenerator needed, no obligatory UTSP connection
    # available options: "AVG" - average consumption profile over Europe and "CHR01 Couple both at Work" - system setup output of the LPG
    occupancy_profile: Optional[str] = "AVG"
    #: building code of considered type of building originated from the Tabula data base (https://episcope.eu/building-typology/webtool/)
    building_code: str = "DE.N.SFH.05.Gen.ReEx.001.002"  # "DE.N.SFH.05.Gen.ReEx.001.002"
    #: absolute area considered for heating and cooling
    absolute_conditioned_floor_area: Optional[float] = None
    #: type of water heating system
    water_heating_system_installed: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    #: type of heating system
    heating_system_installed: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    #: considered mobility options, passed as inputs to the LoadProfileGenerator and considered to model cars
    mobility_set: Optional[JsonReference] = None
    # field(
    #     default_factory=lambda: TransportationDeviceSets.Bus_and_one_30_km_h_Car  # type: ignore
    #     )
    #: average daily commuting distance in kilometers, passed as input to the LoadProfileGenerator and considered to model consumption of cars
    mobility_distance: Optional[JsonReference] = field(
        default_factory=lambda: TravelRouteSets.Travel_Route_Set_for_15km_Commuting_Distance
    )  # type: ignore


# def create_archetype_config_file() -> None:
#     """Component Cost file is created."""

#     config_file=ArcheTypeConfig()
#     config_file_written = config_file.to_json()

#     with open('arche_type_config.json', 'w', encoding="utf-8") as outfile:
#         outfile.write(config_file_written)
