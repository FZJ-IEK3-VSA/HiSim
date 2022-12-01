""" For setting the configuration of the household. """
# clean
import random

from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json

from utspclient.helpers.lpgdata import ChargingStationSets
from utspclient.helpers.lpgpythonbindings import JsonReference
from building_sizer.heating_system_enums import HeatingSystems


class BuildingSizerException(Exception):

    """ Exception for errors in the Building Sizer. """


@dataclass_json
@dataclass
class SizingOptions:

    """ Conatains all relevant information to encode and decode system configs. """

    photovoltaic: List[float] = field(default_factory=lambda: [6e2, 1.2e3, 1.8e3, 3e3, 6e3, 9e3, 12e3, 15e3])
    battery: List[float] = field(default_factory=lambda: [0.3, 0.6, 1.5, 3, 5, 7.5, 10, 15])
    translation: List[str] = field(default_factory=lambda: ["photovoltaic", "battery"])
    probabilities: List[float] = field(default_factory=lambda: [0.8, 0.4])


@dataclass_json
@dataclass
class Individual:

    """ System config as numerical vectors."""

    bool_vector: List[bool] = field(default_factory=list)
    discrete_vector: List[float] = field(default_factory=list)

    def create_random_individual(self, options: SizingOptions) -> None:
        """Creates random individual.

        Parameters
        ----------
        options: SizingOptions
            Instance of dataclass sizing options.
            It contains a list of all available options for sizing of each component.

        """

        for probability in options.probabilities:
            dice = random.uniform(0, 1)  # random number between zero and one
            # TODO: include discrete vector
            if dice < probability:
                self.bool_vector.append(True)
            else:
                self.bool_vector.append(False)
        for component in options.translation:
            try:
                attribute = getattr(options, component)
            except Exception as exception:
                raise BuildingSizerException(
                    f"Invalid component name: {component}\n{exception}"
                ) from exception
            self.discrete_vector.append(random.choice(attribute))


@dataclass_json
@dataclass
class RatedIndividual:

    """ System config as numerical vectors with assosiated fitness function value. """

    individual: Individual
    rating: float


@dataclass_json
@dataclass
class SystemConfig:

    """Defines the system config for the modular household."""

    water_heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP
    heating_system_installed: HeatingSystems = HeatingSystems.HEAT_PUMP
    clever: bool = True
    predictive: bool = False
    prediction_horizon: Optional[int] = 0
    pv_included: bool = True
    pv_peak_power: Optional[float] = 9000
    smart_devices_included: bool = False
    buffer_included: bool = True
    buffer_volume: Optional[float] = 500  # in liter
    battery_included: bool = False
    battery_capacity: Optional[float] = 5  # in Wh
    chp_included: bool = False
    chp_power: Optional[float] = 12
    h2_storage_included: bool = True
    h2_storage_size: Optional[float] = 100
    electrolyzer_included: bool = True
    electrolyzer_power: Optional[float] = 5e3
    ev_included: bool = True
    charging_station: JsonReference = ChargingStationSets.Charging_At_Home_with_03_7_kW
    utsp_connect: bool = False
    url: str = "http://134.94.131.167:443/api/v1/profilerequest"
    api_key: str = ""

    def search_pair_from_translation(self, translation: str) -> Tuple[bool, Optional[float]]:
        """ Returns (bool, discrete) value pair for each component."""
        if translation == "photovoltaic":
            return(self.pv_included, self.pv_peak_power)
        if translation == "battery":
            return(self.battery_included, self.battery_capacity)
        raise ValueError("Translation of element impossible.")

    def get_individual(self, translation: List[str]) -> Individual:
        """ Translates system config to numerical vectors. """
        bool_vector = []
        discrete_vector = []
        for elem in translation:
            bool_elem, discrete_elem = self.search_pair_from_translation(translation=elem)
            bool_vector.append(bool_elem)
            discrete_vector.append(discrete_elem)
        discrete_vector_not_none = [elem or 0 for elem in discrete_vector]
        return Individual(bool_vector, discrete_vector_not_none)


def create_from_individual(individual: Individual, translation: List[str]) -> "SystemConfig":
    """ Creates system config from numerical vector. """
    bool_vector = individual.bool_vector
    discrete_vector = individual.discrete_vector
    system_config = SystemConfig()
    # TODO work with options and remove hard coded indices
    system_config.pv_included = bool_vector[translation.index("photovoltaic")]
    system_config.pv_peak_power = discrete_vector[translation.index("photovoltaic")]
    system_config.battery_included = bool_vector[translation.index("battery")]
    system_config.battery_capacity = discrete_vector[translation.index("battery")]
    return system_config


def create_system_config_file() -> None:
    """System Config file is created."""

    config_file = SystemConfig()
    config_file_written = config_file.to_json()  # type: ignore

    with open("system_config.json", "w", encoding="utf-8") as outfile:
        outfile.write(config_file_written)
