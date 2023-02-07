"""Example Component."""

# clean

# Generic/Built-in
from typing import List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.simulationparameters import SimulationParameters
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim.component import ConfigBase

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class ExampleComponentConfig(ConfigBase):

    """Configuration of the Example Component."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ExampleComponent.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    loadtype: lt.LoadTypes
    unit: lt.Units
    electricity: Optional[float]
    # heat: float = 0.0,
    capacity: Optional[float]
    initial_temperature: Optional[float]

    @classmethod
    def get_default_example_component(cls):
        """Gets a default Example Component."""
        return ExampleComponentConfig(
            name="Example Component",
            electricity=-1e3,
            loadtype=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            # heat=0.0,
            capacity=45 * 121.2,
            initial_temperature=25.0,
        )


class ExampleComponent(Component):

    """Example Component class.

    It supports multiple Example Component values for fictitious scenarios.
    The values passed to the constructor are taken as constants to build the load profile
    for the entire simulation duration.

    Parameters
    ----------
    electricity : float
        Constant to define electricity output profile
    heat : float
        Constant to define heat output profile
    capacity : float
        Stored energy when starting the simulation
    initial_temperature : float
        Initial temperature when starting the simulation
    sim_params: cp.SimulationParameters
        Simulation parameters used by the setup function:

    """

    ThermalEnergyDelivered = "ThermalEnergyDelivered"

    # Outputs
    ElectricityOutput = "ElectricityOutput"
    TemperatureMean = "Residence Temperature"
    StoredEnergy = "StoredEnergy"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ExampleComponentConfig,
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.examplecomponentconfig = config
        super().__init__(
            self.examplecomponentconfig.name,
            my_simulation_parameters=my_simulation_parameters,
        )

        # Initialized variables
        self.temperature: float = -300
        self.previous_temperature: float

        self.build(
            electricity=config.electricity,
            # heat=config.heat,
            capacity=config.capacity,
            initial_temperature=config.initial_temperature,
        )

        self.thermal_energy_delivered_c: ComponentInput = self.add_input(
            self.examplecomponentconfig.name,
            self.ThermalEnergyDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )

        self.t_m_c: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureMean,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
        )

        self.electricity_output_c: ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
        )
        self.stored_energy_c: ComponentOutput = self.add_output(
            self.component_name,
            self.StoredEnergy,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )

    def build(
        self,
        electricity: Optional[float],
        # heat: float,
        capacity: Optional[float],
        initial_temperature: Optional[float],
    ) -> None:
        """Build load profile for entire simulation duration."""
        self.time_correction_factor: float = (
            1 / self.my_simulation_parameters.seconds_per_timestep
        )
        self.seconds_per_timestep: float = (
            self.my_simulation_parameters.seconds_per_timestep
        )

        if electricity is None:
            self.electricity_output: float = -1e3
        else:
            self.electricity_output = -1e3 * electricity

        if capacity is None:
            self.capacity = 45 * 121.2
        else:
            self.capacity = capacity

        if initial_temperature is None:
            self.temperature = 25.0
            self.initial_temperature = 25.0
        else:
            self.temperature = initial_temperature
            self.initial_temperature = initial_temperature
        self.previous_temperature = self.temperature

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines: List = []
        return lines

    def i_save_state(self) -> None:
        """Saves the current state of the temperature."""
        self.previous_temperature = self.temperature

    def i_restore_state(self) -> None:
        """Restores previous state of the temperature."""
        self.temperature = self.previous_temperature

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates the component."""
        electricity_output: float = 0
        if 60 * 6 <= timestep < 60 * 9:
            electricity_output = self.electricity_output
        elif 60 * 15 <= timestep < 60 * 18:
            electricity_output = -self.electricity_output

        stsv.set_output_value(self.electricity_output_c, electricity_output)

        if timestep <= 60 * 12:
            thermal_delivered_energy: float = 0
            temperature: float = self.initial_temperature
            current_stored_energy = (self.initial_temperature + 273.15) * self.capacity
        else:
            thermal_delivered_energy = stsv.get_input_value(
                self.thermal_energy_delivered_c
            )
            previous_stored_energy = (
                self.previous_temperature + 273.15
            ) * self.capacity
            current_stored_energy = previous_stored_energy + thermal_delivered_energy
            self.temperature = current_stored_energy / self.capacity - 273.15
            temperature = self.temperature

        # thermal_delivered_energy = 0
        # temperature = self.initial_temperature
        # current_stored_energy = ( self.initial_temperature + 273.15) * self.capacity
        #    else:
        # thermal_delivered_energy = stsv.get_input_value(self.thermal_energy_deliveredC)
        # previous_stored_energy = (self.previous_temperature + 273.15) * self.capacity
        # current_stored_energy = previous_stored_energy + thermal_delivered_energy
        # self.temperature = current_stored_energy / self.capacity - 273.15
        # temperature = self.temperature

        stsv.set_output_value(self.stored_energy_c, current_stored_energy)
        stsv.set_output_value(self.t_m_c, temperature)
