""" For setting the configuration of the household. """
# clean
import random

from typing import List, Optional
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json

from utspclient.helpers.lpgdata import ChargingStationSets
from utspclient.helpers.lpgpythonbindings import JsonReference
from building_sizer.heating_system_enums import HeatingSystems


@dataclass_json
@dataclass
class Individual:
    bool_vector: List[bool] = field(default_factory=list)
    discrete_vector: List[float] = field(default_factory=list)

    def create_random_individual(self, probabilities: List[float]):
        for probability in probabilities:
            dice = random.uniform(0, 1)  # random number between zero and one
            if dice < probability:
                self.bool_vector.append(True)
            else:
                self.bool_vector.append(False)

@dataclass_json
@dataclass
class RatedIndividual:
    individual: Individual
    rating: float


@dataclass_json
@dataclass
class SystemConfig:

    """ Defines the system config for the modular household. """

    water_heating_system_installed: HeatingSystems
    heating_system_installed: HeatingSystems
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
    charging_station: JsonReference
    utsp_connect: bool
    url: str
    api_key: str

    def __init__(self, water_heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP,
            clever: bool = True, predictive: bool = False, prediction_horizon: int = 0, pv_included: bool = True,
            pv_peak_power: Optional[float] = 9000, smart_devices_included: bool = False,
            buffer_included: bool = True, buffer_volume: Optional[float] = 500, battery_included: bool = False, battery_capacity: Optional[float] = 5,
            chp_included: bool = False, chp_power: Optional[float] = 12, h2_storage_included: bool = True, h2_storage_size: Optional[float] = 100,
            electrolyzer_included: bool = True, electrolyzer_power: Optional[float] = 5e3, ev_included: bool = True,
            charging_station: JsonReference = ChargingStationSets.Charging_At_Home_with_03_7_kW,
            utsp_connect: bool = False, url: str = "http://134.94.131.167:443/api/v1/profilerequest",
            api_key: str = 'OrjpZY93BcNWw8lKaMp0BEchbCc'):  # noqa
        self.water_heating_system_installed = water_heating_system_installed
        self.heating_system_installed = heating_system_installed
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
        self.utsp_connect = utsp_connect
        self.url = url
        self.api_key = api_key

    def create_system_config_file() -> None:
        """Component Cost file is created."""

        config_file = SystemConfig()
        config_file_written = config_file.to_json()

        with open('system_config.json', 'w', encoding="utf-8") as outfile:
            outfile.write(config_file_written)

    def get_individual(self) -> Individual:
        bool_vector = [self.pv_included, self.battery_included]
        discrete_vector = []
        return Individual(bool_vector, discrete_vector)

    def create_from_individual(individual: Individual) -> "SystemConfig":
        bool_vector = individual.bool_vector
        system_config = SystemConfig()
        system_config.pv_included = bool_vector[0]
        system_config.battery_included = bool_vector[1]
        return system_config
