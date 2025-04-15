"""Air Conditioner Component."""

from dataclasses import dataclass
from typing import Any, List
from dataclasses_json import dataclass_json

import numpy as np
import pandas as pd
from hisim import log

from hisim import component as cp
from hisim.component import CapexCostDataClass, ConfigBase, DisplayConfig, OpexCostDataClass
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters
from hisim.loadtypes import LoadTypes, Units
from hisim.components.weather import Weather
from hisim.components.building import Building
from hisim import utils
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

__authors__ = "Marwa Alfouly, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class AirConditionerConfig(ConfigBase):
    """Class for configuration of air-conditioner."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return AirConditioner.get_full_classname()

    building_name: str
    name: str
    manufacturer: str
    model_name: str
    cost: float
    lifetime: int
    co2_emissions_kg_co2_eq: float

    @classmethod
    def get_default_air_conditioner_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get default configuration of air-conditioner."""
        config = AirConditionerConfig(
            building_name=building_name,
            name="AirConditioner",
            manufacturer="Panasonic",
            model_name="CS-RE18JKE/CU-RE18JKE",
            cost=0,
            lifetime=0,
            co2_emissions_kg_co2_eq=0
        )
        return config

class AirConditionerState:
    """Data class that saves the state of the air conditioner."""

    def __init__(self, operating_mode: str, activation_time_step: int, deactivation_time_step: int, modulating_percentage: float) -> None:
        """Initializes the air conditioner state."""
        self.operating_mode: str = operating_mode
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step
        self.modulating_percentage: float = modulating_percentage

    def clone(self) -> "AirConditionerState":
        """Copies the current instance."""
        return AirConditionerState(
            operating_mode=self.operating_mode,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
            modulating_percentage=self.modulating_percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate_heating(self, timestep: int) -> None:
        """Activates heating and remembers the time step."""
        self.operating_mode = "heating"
        self.activation_time_step = timestep

    def activate_cooling(self, timestep: int) -> None:
        """Activates cooling and remembers the time step."""
        self.operating_mode = "cooling"
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        self.operating_mode = "off"
        self.deactivation_time_step = timestep   

class AirConditioner(cp.Component):
    """Class for air-conditioner."""

    # Inputs
    OperatingState = "State"
    ModulatingPowerSignal = "ModulatingPowerSignal"
    OutdoorAirTemperature = "TemperatureOutside"
    # IndoorAirTemperature = "IndoorAirTemperature"
    GridImport = "GridImport"
    PV2load = "PV2load"
    Battery2Load = "Battery2Load"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ElectricityConsumption = "ElectricityConsumption"
    Efficiency = "EnergyEfficiencyRatio"
    CoefficientOfPerformance = "CoefficientOfPerformance"


    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Constructs all the necessary attributes."""

        self.air_conditioner_config = config

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        self.state = AirConditionerState("off", 0, 0, 0)
        self.previous_state = self.state
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.build(
            manufacturer=self.air_conditioner_config.manufacturer,
            model_name=self.air_conditioner_config.model_name,
        )
        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.OutdoorAirTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.modulating_power_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            False,
        )

        self.optimal_electric_power_pv_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.PV2load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_grid_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.GridImport,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_battery_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Battery2Load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )

        self.thermal_energy_generation_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description="Delivered thermal energy",
        )

        self.electricity_consumption: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description="Electricity consumption",
        )
        self.efficiency: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Efficiency,
            LoadTypes.ANY,
            Units.ANY,
            output_description="Energy efficiency ratio for cooling.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_controller())


    def get_default_connections_from_weather(self):
        """Get default inputs from the weather component."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditioner.OutdoorAirTemperature,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    
    def get_default_connections_from_controller(self):
        """Get default inputs from the controller."""

        connections = []
        controller_classname = AirConditionerController.get_classname()

        connections.append(
            cp.ComponentConnection(
                AirConditioner.ModulatingPowerSignal,
                controller_classname,
                AirConditionerController.ModulatingPowerSignal,
            )
        )
        return connections

    @staticmethod
    def get_cost_capex(config: AirConditionerConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.cost / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_emissions_kg_co2_eq / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.cost,
            device_co2_footprint_in_kg=config.co2_emissions_kg_co2_eq,
            lifetime_in_years=config.lifetime,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX"""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=0,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER
        )

        return opex_cost_data_class
    

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(
        self,
        manufacturer,
        model_name,
    ):
        """Build function: The function retrieves air conditioner from databasesets sets important constants and parameters for the calculations."""
        # Simulation parameters

        # Retrieves air conditioner from database - BEGIN
        air_conditioners_database = utils.load_smart_appliance("Air Conditioner")

        air_conditioner = None
        for air_conditioner_iterator in air_conditioners_database:
            if (
                air_conditioner_iterator["Manufacturer"] == manufacturer
                and air_conditioner_iterator["Model"] == model_name
            ):
                air_conditioner = air_conditioner_iterator
                break

        if air_conditioner is None:
            raise Exception("Air conditioner model not registered in the database")

        self.manufacturer = manufacturer
        self.model = model_name

        # Interpolates COP, cooling capacities, power input data from the database
        self.eer_ref = []
        self.t_out_cooling_ref = []
        self.t_out_heating_ref = []
        self.cooling_capacity_ref = []
        self.heating_capacity_ref = []
        self.cop_ref = []

        """
        Typical relations between COPs and Heating capacities are found here:  https://www.everysolarthing.com/blog/heat-pumps/

        """

        for air_conditioner_tout in air_conditioner["Outdoor temperature range - cooling"]:
            self.t_out_cooling_ref.append([air_conditioner_tout][0])
        for air_conditioner_tout in air_conditioner["Outdoor temperature range - heating"]:
            self.t_out_heating_ref.append([air_conditioner_tout][0])

        for air_conditioner_eers in air_conditioner["EER W/W"]:
            self.eer_ref.append([air_conditioner_eers][0])
        for air_conditioner_cops in air_conditioner["COP W/W"]:
            self.cop_ref.append([air_conditioner_cops][0])

        for air_conditioner_cooling_capacities in air_conditioner["Cooling capacity W"]:
            self.cooling_capacity_ref.append([air_conditioner_cooling_capacities][0])
        for air_conditioner_heating_capacities in air_conditioner["Heating capacity W"]:
            self.heating_capacity_ref.append([air_conditioner_heating_capacities][0])

        self.eer_coef = np.polyfit(self.t_out_cooling_ref, self.eer_ref, 1)
        self.cooling_capacity_coef = np.polyfit(self.t_out_cooling_ref, self.cooling_capacity_ref, 1)

        self.cop_coef = np.polyfit(self.t_out_heating_ref, self.cop_ref, 1)
        self.heating_capacity_coef = np.polyfit(self.t_out_heating_ref, self.heating_capacity_ref, 1)

        # Retrieves air conditioner from database - END

        SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.COEFFICIENT_OF_PERFORMANCE_HEATING, entry=self.cop_coef)
        SingletonSimRepository().set_entry(key=SingletonDictKeyEnum.ENERGY_EFFICIENY_RATIO_COOLING, entry=self.eer_coef)


    def calculate_energy_efficiency_ratio(self, t_out):
        """Calculate cooling energy efficiency ratio as a function of outside temperature."""
        return np.polyval(self.eer_coef, t_out)

    def calculate_cooling_capacity(self, t_out):
        """Calculate cooling capacity as a function of outside temperature."""
        return np.polyval(self.cooling_capacity_coef, t_out)

    def calculate_coefficient_of_performance(self, t_out):
        """Calculate heating coefficient of performance as a function of outside temperature."""
        return np.polyval(self.cop_coef, t_out)

    def calculate_heating_capacity(self, t_out):
        """Calculate heating capacity as a function of outside temperature."""
        return np.polyval(self.heating_capacity_coef, t_out)

    def i_save_state(self) -> None:
        """Saves the internal state at the beginning of each timestep."""
        pass

    def i_restore_state(self) -> None:
        """Restores the internal state after each iteration."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self):
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("Name: Air Conditioner")
        lines.append(f"Manufacturer: {self.manufacturer}")
        lines.append(f"Model {self.model}")
        return self.air_conditioner_config.get_string_dict() + lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:  # noqa: C901
        """Core simulation function."""
        if force_convergence:
            pass
        outside_air_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        modulating_signal = stsv.get_input_value(self.modulating_power_signal_channel)
        self.state.modulating_percentage = modulating_signal

        test_factor = 0.2

        efficiency = 0
        if modulating_signal > 0: # heating
            efficiency = self.calculate_coefficient_of_performance(outside_air_temperature_deg_c)
            heating_capacity = self.calculate_heating_capacity(outside_air_temperature_deg_c)
            # print("heating capacity: " + str(heating_capacity))
            thermal_energy_delivered = heating_capacity * test_factor * modulating_signal
        elif modulating_signal < 0: # cooling
            if self.previous_state.operating_mode != "cooling":
                self.state.activate_cooling(timestep)
            efficiency = self.calculate_energy_efficiency_ratio(outside_air_temperature_deg_c)
            thermal_energy_delivered = self.calculate_cooling_capacity(outside_air_temperature_deg_c) * test_factor * modulating_signal
        else: # off
            if self.previous_state.operating_mode != "off":
                self.state.deactivate(timestep)
            thermal_energy_delivered = 0

        electricity_consumption = self.calculate_electricity_consumption(thermal_energy_delivered, efficiency)

        stsv.set_output_value(self.efficiency, efficiency)
        stsv.set_output_value(self.electricity_consumption, electricity_consumption)
        stsv.set_output_value(self.thermal_energy_generation_channel, thermal_energy_delivered)
        # print("thermal energy delivered: " + str(thermal_energy_delivered))


    def calculate_electricity_consumption(self, themal_energy: float, efficiency: float) -> float:
        if efficiency == 0:
            return 0
        return abs(themal_energy / efficiency)

@dataclass_json
@dataclass
class AirConditionerControllerConfig(ConfigBase):
    """Class for configuration of air-conditioner controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return AirConditionerController.get_full_classname()

    name: str
    minimum_runtime_s: float
    minimum_idle_time_s: float
    heating_set_temperature_deg_c: float 
    cooling_set_temperature_deg_c: float
    offset: float
    temperature_difference_full_power_deg_c: float

    @classmethod
    def get_default_air_conditioner_controller_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get default configuration of air-conditioner controller."""
        config = AirConditionerControllerConfig(
            building_name=building_name,
            name="AirConditionerControllerConfig",
            heating_set_temperature_deg_c=18.0,
            cooling_set_temperature_deg_c=26.0,
            minimum_runtime_s=60 * 60,
            minimum_idle_time_s=15 * 60,
            offset=5,
            temperature_difference_full_power_deg_c=5,
        )

        return config

class AirConditionerControllerState:
    """Data class that saves the state of the controller."""

    def __init__(self, mode: str, activation_time_step: int, deactivation_time_step: int, percentage: float,) -> None:
        """Initializes the heat pump controller state."""
        self.mode = mode
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step
        self.percentage: float = percentage

    def clone(self) -> "AirConditionerControllerState":
        """Copies the current instance."""
        return AirConditionerControllerState(
            mode=self.mode,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
            percentage=self.percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate_heating(self, timestep: int) -> None:
        """Activates heating and remembers the time step."""
        self.mode = "heating"
        self.activation_time_step = timestep

    def activate_cooling(self, timestep: int) -> None:
        """Activates cooling and remembers the time step."""
        self.mode = "cooling"
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        self.mode = "off"
        self.deactivation_time_step = timestep   

class AirConditionerController(cp.Component):
    """Class for air-conditioner controller.

    Sends signal to the air conditioner for
    activation or deactivation based on current
    indoor air temperature, target temperature,
    and restrictions for minimum/maximum idle
    and operation time.

    """

    # Inputs
    TemperatureIndoorAir = "TemperatureIndoorAir"
    ElectricityInput = "ElectricityInput"

    # States
    OperatingState = "OperatingState"
    ModulatingPowerSignal = "ModulatingPowerSignal"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Constructs all the neccessary attributes."""

        self.config = config

        self.minimum_runtime_in_timesteps = int(
            self.config.minimum_runtime_s / my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            self.config.minimum_idle_time_s / my_simulation_parameters.seconds_per_timestep
        )

        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.state: AirConditionerControllerState = AirConditionerControllerState("off", 0, 0, 0)
        self.previous_state: AirConditionerControllerState = self.state.clone()
        self.processed_state: AirConditionerControllerState = self.state.clone()

        self.add_connections()

    def add_connections(self):

        self.indoor_air_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureIndoorAir,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.operation_modulating_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            output_description="The power modulation signal for the air conditioner",
        )

        self.add_default_connections(self.get_default_connections_from_building())

    def get_default_connections_from_building(self):
        """Get default inputs from the building component."""
        log.information("setting building default connections in AirConditionerController")
        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                AirConditionerController.TemperatureIndoorAir,
                building_classname,
                Building.TemperatureIndoorAir,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double check results after iteration."""
        pass

    def write_to_report(self):
        """Logs the most important config stuff to the report."""
        lines = []
        lines.append("Air Conditioner Controller")
        lines.append("Control algorith of the Air conditioner is: on-off control\n")
        lines.append(f"Controller heating set temperature is {format(self.config.heating_set_temperature_deg_c)} Deg C \n")
        lines.append(f"Controller cooling set temperature is {format(self.config.cooling_set_temperature_deg_c)} Deg C \n")
        return self.config.get_string_dict() + lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Core simulation."""
        # print("#i_simulate controller")
        if force_convergence:
            self.state = self.processed_state.clone()
            new_operating_mode = self.state.mode
        else:
            indoor_air_temperature_deg_c = stsv.get_input_value(self.indoor_air_temperature_channel)
            new_operating_mode = self.determine_operating_mode(indoor_air_temperature_deg_c, timestep)
            modulating_percentage = self.modulate_power(indoor_air_temperature_deg_c, new_operating_mode)
            self.state.mode = new_operating_mode
            self.state.percentage = modulating_percentage
            self.processed_state = self.state.clone()
                    
        if new_operating_mode == "heating":
            return_value_state = 1
        elif new_operating_mode == "cooling":
            return_value_state = -1
        else:
            return_value_state = 0

        stsv.set_output_value(self.operation_modulating_signal_channel, return_value_state * self.state.percentage)


    def determine_operating_mode(self, current_temperature_deg_c: float, timestep: int) -> str:
        """Controller takes action to maintain defined comfort range."""
        print("current temperature: " + str(current_temperature_deg_c))
        print("previous state: " + self.state.mode)
        print("active till: " + str(self.state.activation_time_step + self.minimum_runtime_in_timesteps))
        print("inactive till: " + str(self.state.deactivation_time_step + self.minimum_resting_time_in_timesteps))

        # Keep state as is because minimum operation and idle time not reached
        if (self.state.mode == "heating") and (self.state.activation_time_step + self.minimum_runtime_in_timesteps > timestep):
            # minimum runtime for heating not reached
            return "heating"
        if (self.state.mode == "cooling") and (self.state.activation_time_step + self.minimum_runtime_in_timesteps > timestep):
            # minimum runtime for cooling not reached
            return "cooling"
        if (self.state.mode == "off") and (self.state.deactivation_time_step + self.minimum_resting_time_in_timesteps > timestep):
            # minimum idle time not reached
            return "off"
        
        # Keep state in deadband
        if (self.processed_state.mode == "heating" or self.state.mode == "heating") and current_temperature_deg_c < self.config.heating_set_temperature_deg_c + self.config.offset:
            if self.state.mode != 'heating':
                self.state.activate_heating(timestep)
            return "heating"
        if (self.state.mode == "cooling") and current_temperature_deg_c > self.config.cooling_set_temperature_deg_c - self.config.offset:
            return "cooling"
        
        # Change state based on temperature
        if current_temperature_deg_c > self.config.cooling_set_temperature_deg_c + self.config.offset:
            if self.state.mode != 'cooling':
                self.state.activate_cooling(timestep)
            return "cooling"
        if current_temperature_deg_c < self.config.heating_set_temperature_deg_c - self.config.offset:
            if self.state.mode != 'heating':
                self.state.activate_heating(timestep)
            return "heating"

        if self.config.heating_set_temperature_deg_c - self.config.offset < current_temperature_deg_c < self.config.cooling_set_temperature_deg_c + self.config.offset: 
            if self.state.mode != 'off':
                self.state.deactivate(timestep)
            return "off"
        
        raise ValueError("Not handled")
        
    def modulate_power(self, current_temperature_deg_c: float, operating_mode: str) -> float:
        """
        Modulates power non-linearly (quadratic) based on the temperature difference.
        Power drops off more aggressively as the temperature nears the setpoint.
        """
        if operating_mode == "off":
            return 0.0
        elif operating_mode == "heating":
            temperature_difference = max((self.config.heating_set_temperature_deg_c + 1.5) - current_temperature_deg_c, 0)
        elif operating_mode == "cooling":
            temperature_difference = max(current_temperature_deg_c - (self.config.cooling_set_temperature_deg_c - 1.5), 0)
        else:
            raise ValueError(f"Unknown operating mode: {operating_mode}")
        
        # Apply quadratic scaling
        capped_ratio = min(temperature_difference / self.config.temperature_difference_full_power_deg_c, 1.0)
        percentage = max(1 - (1 - capped_ratio) ** 2, 0.1)

        return percentage
