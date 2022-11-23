# -*- coding: utf-8 -*-
from typing import Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from utspclient.helpers.lpgdata import TransportationDeviceSets, TravelRouteSets, Households
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.loadtypes import Locations, BuildingCodes

@dataclass_json
@dataclass
class ArcheTypeConfig:

    """ Defines the system config for the modular household. """

    location: Locations
    occupancy_profile: JsonReference
    building_code: BuildingCodes
    mobility_set: JsonReference
    mobility_distance: Optional[JsonReference]

    def __init__(self, location: Locations = Locations.AACHEN,
            occupancy_profile: JsonReference = Households.CHR01_Couple_both_at_Work,
            building_code: BuildingCodes = BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002,
            mobility_set: JsonReference = TransportationDeviceSets.Bus_and_two_30_km_h_Cars,
            mobility_distance: Optional[JsonReference] = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance):  # noqa
        self.location = location
        self.occupancy_profile = occupancy_profile
        self.building_code = building_code
        self.mobility_set = mobility_set
        self.mobility_distance = mobility_distance

# def create_archetype_config_file() -> None:
#     """Component Cost file is created."""

#     config_file=ArcheTypeConfig()
#     config_file_written = config_file.to_json()

#     with open('arche_type_config.json', 'w', encoding="utf-8") as outfile:
#         outfile.write(config_file_written)
