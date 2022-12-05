import json
import random
from dataclasses import dataclass, field
from typing import List

from dataclasses_json import dataclass_json
from building_sizer.interface_configs.system_config import SystemConfig

class BuildingSizerException(Exception):

    """Exception for errors in the Building Sizer."""


@dataclass_json
@dataclass
class SizingOptions:

    """Contains all relevant information to encode and decode system configs."""

    pv_peak_power: List[float] = field(
        default_factory=lambda: [6e2, 1.2e3, 1.8e3, 3e3, 6e3, 9e3, 12e3, 15e3]
    )
    battery_capacity: List[float] = field(
        default_factory=lambda: [0.3, 0.6, 1.5, 3, 5, 7.5, 10, 15]
    )
    buffer_capacity: List[float] = field(
        default_factory=lambda: [200, 300, 500, 750, 1000, 1500, 3000]
    )
    # these lists define the layout of the individual vectors
    bool_attributes: List[str] = field(
        default_factory=lambda: ["pv_included", "battery_included"]
    )
    discrete_attributes: List[str] = field(
        default_factory=lambda: ["pv_peak_power", "battery_capacity"]
    )
    # this list defines the probabilites of each component to be included at the beginning
    probabilities: List[float] = field(default_factory=lambda: [0.8, 0.4])

    def __post_init__(self):
        """Checks if every element of attribute list bool_attributes and list discrete_attributes
        is also attribute of class SystemConfig."""
        for name in SizingOptions.bool_attributes + SizingOptions.discrete_attributes:
            if not hasattr(SystemConfig, name):
                raise Exception(
                    f"Invalid vector attribute: SystemConfig has no member '{name}'"
                )


@dataclass_json
@dataclass
class Individual:

    """System config as numerical vectors."""

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
        # randomly assign the bool attributes True or False
        for probability in options.probabilities:
            dice = random.uniform(0, 1)  # random number between zero and one
            self.bool_vector.append(dice < probability)
        # randomly assign the discrete attributes depending on the allowed values
        for component in options.discrete_attributes:
            allowed_values = getattr(options, component)
            self.discrete_vector.append(random.choice(allowed_values))


@dataclass_json
@dataclass
class RatedIndividual:

    """System config as numerical vectors with assosiated fitness function value."""

    individual: Individual
    rating: float


def get_individual(system_config: SystemConfig, options: SizingOptions) -> Individual:
    """Creates discrete and boolean vector from given SystemConfig."""
    bool_vector: List[bool] = [
        getattr(system_config, name) for name in options.bool_attributes
    ]
    discrete_vector: List[float] = [
        getattr(system_config, name) for name in options.discrete_attributes
    ]
    return Individual(bool_vector, discrete_vector)

def create_from_individual(
    individual: Individual, options: SizingOptions
) -> "SystemConfig":
    """
    Creates a SystemConfig object from the bool and discrete vectors of an
    Individual object. For this, the SizingOptions object is needed.
    """
    # create a default SystemConfig object
    system_config = SystemConfig()
    # assign the bool attributes
    assert len(options.bool_attributes) == len(
        individual.bool_vector
    ), "Invalid individual: wrong number of bool parameters"
    for i, name in enumerate(options.bool_attributes):
        setattr(system_config, name, individual.bool_vector[i])
    # assign the discrete attributes
    assert len(options.discrete_attributes) == len(
        individual.discrete_vector
    ), "Invalid individual: wrong number of discrete parameters"
    for i, name in enumerate(options.discrete_attributes):
        setattr(system_config, name, individual.discrete_vector[i])
    return system_config

def create_random_system_configs(number: int, options: SizingOptions) -> List[str]:
    """
    Creates the desired number of random SystemConfig objects
    """
    hisim_configs = []
    for _ in range(number):
        # Create a random Individual
        individual = Individual()
        individual.create_random_individual(options=options)
        # Convert the Individual to a SystemConfig object and
        # append it to the list
        hisim_configs.append(create_from_individual(
                individual, options
            ).to_json()  # type: ignore
        )
    return hisim_configs

def save_system_configs_to_file(configs: List[str]) -> None:
    with open("./random_system_configs.json", "w") as f:
        json.dump(configs, f)