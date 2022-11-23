""" For setting the configuration of the household. """
# clean
from typing import Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from utspclient.helpers.lpgdata import ChargingStationSets, TransportationDeviceSets, TravelRouteSets
from utspclient.helpers.lpgpythonbindings import JsonReference
from hisim.loadtypes import HeatingSystems, Locations, OccupancyProfiles, BuildingCodes


@dataclass_json
@dataclass
class SystemConfig:

    """ Defines the system config for the modular household. """

    location: Locations
    occupancy_profile: OccupancyProfiles
    building_code: BuildingCodes
    water_heating_system_installed: HeatingSystems
    heating_system_installed: HeatingSystems
    mobility_set: JsonReference
    mobility_distance: Optional[JsonReference]
    clever: bool
    predictive: bool
    prediction_horizon: Optional[int]
    pv_included: bool
    pv_peak_power: Optional[float]
    smart_devices_included: bool
    buffer_included: bool
    buffer_volume: Optional[float]  # in liter
    battery_included: bool
    battery_capacity: Optional[float]  # in Wh
    chp_included: bool
    chp_power: Optional[float]
    h2_storage_included: bool
    h2_storage_size: Optional[float]
    electrolyzer_included: bool
    electrolyzer_power: Optional[float]
    ev_included: bool
    charging_station: Optional[JsonReference]

    def __init__(self, location: Locations = Locations.AACHEN,
            occupancy_profile: OccupancyProfiles = OccupancyProfiles.CH01,
            building_code: BuildingCodes = BuildingCodes.DE_N_SFH_05_GEN_REEX_001_002,
            water_heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            mobility_set: JsonReference = TransportationDeviceSets.Bus_and_two_30_km_h_Cars,
            mobility_distance: Optional[JsonReference] = TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
            clever: bool = True, predictive: bool = False, prediction_horizon: int = 0, pv_included: bool = True,
            pv_peak_power: Optional[float] = 9000, smart_devices_included: bool = True,
            buffer_included: bool = True, buffer_volume: Optional[float] = 500, battery_included: bool = False, battery_capacity: Optional[float] = 5,
            chp_included: bool = False, chp_power: Optional[float] = 12, h2_storage_included: bool = True, h2_storage_size: Optional[float] = 100,
            electrolyzer_included: bool = True, electrolyzer_power: Optional[float] = 5e3, ev_included: bool = True,
            charging_station: Optional[JsonReference] = ChargingStationSets.Charging_At_Home_with_03_7_kW):  # noqa
        self.location = location
        self.occupancy_profile = occupancy_profile
        self.building_code = building_code
        self.water_heating_system_installed = water_heating_system_installed
        self.heating_system_installed = heating_system_installed
        self.mobility_set = mobility_set
        self.mobility_distance = mobility_distance
        self.clever = clever
        self.predictive = predictive
        self.prediction_horizon = prediction_horizon
        self.pv_included = pv_included
        self.pv_peak_power = pv_peak_power
        self.smart_devices_included = smart_devices_included
        self.buffer_included = buffer_included
        self.buffer_volume = buffer_volume
        self.battery_included = battery_included
        self.battery_capacity = battery_capacity
        self.chp_included = chp_included
        self.chp_power = chp_power
        self.h2_storage_included = h2_storage_included
        self.h2_storage_size = h2_storage_size
        self.electrolyzer_included = electrolyzer_included
        self.electrolyzer_power = electrolyzer_power
        self.ev_included = ev_included
        self.charging_station = charging_station
