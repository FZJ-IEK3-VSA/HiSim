"""Heating components module.

This module simulates heating components for the building.

The module contains the following classes:
    1. class BuildingControllerState - constructs the building controller state.
    2. class BuildingControllerConfig -  json dataclass, configurates the building controller class.
    3. class BuildingController - calculates real heating demand and how much building is supposed to be heated up.
"""

# this module is not finished yet!
# clean

# Generic/Built-in
from typing import List, Any

from dataclasses import (
    dataclass,
)
from dataclasses_json import (
    dataclass_json,
)

from hisim import (
    dynamic_component,
    utils,
)
from hisim import (
    component as cp,
)
from hisim import (
    loadtypes as lt,
)

from hisim.components.configuration import (
    PhysicsConfig,
)
from hisim.components.configuration import (
    LoadConfig,
)

from hisim.simulationparameters import (
    SimulationParameters,
)


__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class BuildingControllerState:

    """BuildingControllerState class."""

    def __init__(
        self,
        temperature_building_target_in_celsius: float,
        level_of_utilization: float,
    ):
        """Constructs all the neccessary attributes for the BuildingControllerState object."""
        self.temperature_building_target_in_celsius: float = (
            temperature_building_target_in_celsius
        )
        self.level_of_utilization: float = level_of_utilization

    def clone(self):
        """Copies the BuildingControllerState."""
        return BuildingControllerState(
            temperature_building_target_in_celsius=self.temperature_building_target_in_celsius,
            level_of_utilization=self.level_of_utilization,
        )


@dataclass_json
@dataclass
class BuildingControllerConfig:

    """Configuration of the Building Controller class."""

    minimal_building_temperature_in_celsius: float
    stop_heating_building_temperature_in_celsius: float


@dataclass_json
@dataclass
class HeatingComponentConfig(cp.ConfigBase):

    """Configuration of the heating component class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatingComponent.get_full_classname()

    name: str

    @classmethod
    def get_default_german_single_family_home(
        cls,
    ) -> Any:
        """Gets a default Building."""
        config = HeatingComponentConfig(
            name="Heating Device Config Name",
        )
        return config


class HeatingComponent(dynamic_component.DynamicComponent):

    """Heating component class."""

    # Inputs -> heating device
    # either thermal energy delivered from heat pump
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    # or mass input and temperature input delivered from Thermal Energy Storage (TES)
    MassInput = "MassInput"
    TemperatureInput = "TemperatureInput"

    # Outputs

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatingComponentConfig,
    ):
        """Constructs all the neccessary attributes."""
        self.heatingcomponentconfig = config
        # dynamic
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=self.heatingcomponentconfig.name,
            my_simulation_parameters=my_simulation_parameters,
        )

        # =================================================================================================================================
        # Initialization of variables
        self.temperature_building_target_in_celsius: float
        self.level_of_utilization: float
        self.test_new_temperature_in_celsius: float
        self.build()

        self.state: BuildingControllerState = BuildingControllerState(temperature_building_target_in_celsius=self.temperature_building_target_in_celsius,
        level_of_utilization=self.level_of_utilization)
        self.previous_state = self.state.clone()

        # =================================================================================================================================
        # Input and Output channels

        self.thermal_power_delivered_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ThermalEnergyDelivered,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            False,
        )
        self.mass_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MassInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        self.temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            False,
        )

    # =================================================================================================================================
    # Simulation of the heating component class

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates the heating devices."""

        # With Thermal Energy Storage (TES) [In Development]
        if self.mass_input_channel.source_output is not None:
            if force_convergence:
                return

            thermal_power_delivered_in_watt = stsv.get_input_value(self.thermal_power_delivered_channel)
            mass_input_in_kilogram_per_second = stsv.get_input_value(self.mass_input_channel)

            temperature_input_in_celsius = stsv.get_input_value(self.temperature_input_channel)

            if thermal_power_delivered_in_watt > 0 and (
                mass_input_in_kilogram_per_second == 0
                and temperature_input_in_celsius == 0
            ):
                """first iteration --> random numbers"""
                temperature_input_in_celsius = 40.456
                mass_input_in_kilogram_per_second = 0.0123

            if thermal_power_delivered_in_watt > 0:

                massflows_possible_in_kilogram_per_second = (
                    LoadConfig.possible_massflows_load
                )
                mass_flow_level = 0
                # K = W / (J/kgK * kg/s); delta T in Kelvin = delta T in Celsius; heat capacity J/kgK = J/kg°C
                temperature_delta_heat_in_kelvin = thermal_power_delivered_in_watt / (
                    PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
                    * massflows_possible_in_kilogram_per_second[mass_flow_level]
                )
                while temperature_delta_heat_in_kelvin > LoadConfig.delta_T:
                    mass_flow_level += 1
                    temperature_delta_heat_in_kelvin = thermal_power_delivered_in_watt / (
                        PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
                        * massflows_possible_in_kilogram_per_second[mass_flow_level]
                    )

                mass_input_load_in_kilogram_per_timestep = (
                    massflows_possible_in_kilogram_per_second[mass_flow_level]
                    * self.seconds_per_timestep
                )

                energy_demand_in_joule_per_timestep = (
                    thermal_power_delivered_in_watt * self.seconds_per_timestep
                )
                enthalpy_slice_in_joule_per_timestep = (
                    mass_input_load_in_kilogram_per_timestep
                    * temperature_input_in_celsius
                    * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
                )
                enthalpy_new_in_joule_per_timestep = (
                    enthalpy_slice_in_joule_per_timestep
                    - energy_demand_in_joule_per_timestep
                )
                temperature_new_in_celsius = enthalpy_new_in_joule_per_timestep / (
                    mass_input_load_in_kilogram_per_timestep
                    * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
                )

            else:
                # no water is flowing
                temperature_new_in_celsius = temperature_input_in_celsius
                mass_input_load_in_kilogram_per_timestep = 0

            self.test_new_temperature_in_celsius = temperature_new_in_celsius

        # Only with HeatPump
        elif self.thermal_power_delivered_channel.source_output is not None:
            thermal_power_delivered_in_watt = stsv.get_input_value(self.thermal_power_delivered_channel)
        else:
            thermal_power_delivered_in_watt = sum(
                self.get_dynamic_inputs(
                    stsv=stsv, tags=[lt.InandOutputType.HEAT_TO_BUILDING]
                )
            )

    # =================================================================================================================================

    def i_save_state(
        self,
    ) -> None:
        """Saves the current state."""
        self.previous_state = self.state.clone()

    def i_prepare_simulation(
        self,
    ) -> None:
        """Prepares the simulation."""
        pass

    def i_restore_state(
        self,
    ) -> None:
        """Restores the previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Doublechecks."""
        pass

    def build(
        self,
    ):
        """Build function.

        The function sets important constants and parameters for the calculations.
        """

        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        self.timesteps = self.my_simulation_parameters.timesteps


class BuildingController(cp.Component):

    """BuildingController class.

    It calculates on base of the maximal Building
    Thermal Demand and the difference of the actual Building Tempreature
    to the Target/Minimal Building Tempreature how much the building is suppose
    to be heated up. This Output is called "RealBuildingHeatDemand".

    Parameters
    ----------
    sim_params : Simulator
        Simulator object used to carry the simulation using this class

    """

    # Inputs
    ReferenceMaxHeatBuildingDemand = "ReferenceMaxHeatBuildingDemand"
    ResidenceTemperature = "ResidenceTemperature"
    # Outputs
    RealHeatBuildingDemand = "RealHeatBuildingDemand"
    LevelOfUtilization = "LevelOfUtilization"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BuildingControllerConfig,
    ):
        """Constructs all the neccessary attributes of the Building Controller object."""
        super().__init__(
            name="BuildingController",
            my_simulation_parameters=my_simulation_parameters,
        )
        self.minimal_building_temperature_in_celsius = (
            config.minimal_building_temperature_in_celsius
        )
        self.stop_heating_building_temperature_in_celsius = (
            config.stop_heating_building_temperature_in_celsius
        )
        self.state = BuildingControllerState(
            temperature_building_target_in_celsius=config.minimal_building_temperature_in_celsius,
            level_of_utilization=0,
        )
        self.previous_state = self.state.clone()

        # =================================================================================================================================
        # Inputs and Output channels

        self.ref_max_thermal_build_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ReferenceMaxHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            True,
        )
        self.residence_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ResidenceTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.real_heat_building_demand_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.RealHeatBuildingDemand,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
        )
        self.level_of_utilization_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.LevelOfUtilization,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
        )
        # =================================================================================================================================

    @staticmethod
    def get_default_config():
        """Gets a default configuration of the building controller."""
        config = BuildingControllerConfig(
            minimal_building_temperature_in_celsius=20,
            stop_heating_building_temperature_in_celsius=21,
        )
        return config

    def build(self):
        """Build load profile for entire simulation duration."""
        pass

    def write_to_report(
        self,
    ):
        """Writes a report."""
        pass

    def i_save_state(
        self,
    ):
        """Saves the current state."""
        self.previous_state = self.state.clone()

    def i_restore_state(
        self,
    ):
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(
        self,
    ) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulates the building controller."""
        building_temperature_in_celsius = stsv.get_input_value(
            self.residence_temperature_channel
        )
        minimal_building_temperature_in_celsius = (
            self.minimal_building_temperature_in_celsius
        )
        delta_temp_for_level_of_utilization = 0.4

        # Building is warm enough
        if building_temperature_in_celsius > minimal_building_temperature_in_celsius:
            level_of_utilization: float = 0
        # Building get heated up, when temperature is underneath target temperature
        elif (
            building_temperature_in_celsius
            < minimal_building_temperature_in_celsius
            - delta_temp_for_level_of_utilization
        ):
            level_of_utilization = 1
        else:
            level_of_utilization = (
                minimal_building_temperature_in_celsius
                - building_temperature_in_celsius
            )

        real_heat_building_demand_in_watt = (
            self.state.level_of_utilization
            * stsv.get_input_value(self.ref_max_thermal_build_demand_channel)
        )
        self.state.level_of_utilization = level_of_utilization
        stsv.set_output_value(
            self.level_of_utilization_channel, self.state.level_of_utilization
        )
        stsv.set_output_value(
            self.real_heat_building_demand_channel, real_heat_building_demand_in_watt
        )
