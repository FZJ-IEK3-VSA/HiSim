"""Generic Heat Pump module.

This module contains the following classes:
    1. GenericHeatPump State
    2. GenericHeatPump
    3. HeatPumpController

"""
# clean

# Generic/Built-in
import copy
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import numpy as np

from hisim import component as cp
from hisim import log
from hisim import utils
from hisim.components.configuration import PhysicsConfig
from hisim.components.weather import Weather
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class GenericHeatPumpConfigNew(cp.ConfigBase):

    """HeatPump Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GenericHeatPumpNew.get_full_classname()

    name: str
    manufacturer: str
    heat_pump_name: str
    min_operation_time_in_seconds: int
    min_idle_time_in_seconds: int

    @classmethod
    def get_default_generic_heat_pump_config(cls):
        """Gets a default Generic Heat Pump."""
        return GenericHeatPumpConfigNew(
            name="HeatPump",
            heat_pump_name="Vitocal 300-A AWO-AC 301.B07",
            manufacturer="Viessmann Werke GmbH & Co KG",
            min_operation_time_in_seconds=60 * 60,
            min_idle_time_in_seconds=15 * 60,
        )


@dataclass_json
@dataclass
class HeatPumpControllerConfigNew(cp.ConfigBase):

    """HeatPump Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpControllerNew.get_full_classname()

    name: str
    set_water_storage_temperature_for_heating_in_celsius: float
    set_water_storage_temperature_for_cooling_in_celsius: float
    offset: float
    mode: int

    @classmethod
    def get_default_generic_heat_pump_controller_config(cls):
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpControllerConfigNew(
            name="HeatPumpController",
            set_water_storage_temperature_for_heating_in_celsius=49,
            set_water_storage_temperature_for_cooling_in_celsius=55,
            offset=0.0,
            mode=1,
        )


class GenericHeatPumpStateNew:

    """Heat Pump State class.

    It determines the state of the heat pump.

    """

    def __init__(
        self,
        start_timestep: int = 0,
        thermal_power_delivered_in_watt: float = 0.0,
        cop: float = 1.0,
        cycle_number: int = 0,
    ) -> None:
        """Contruct all the necessary attributes."""
        self.start_timestep = start_timestep
        self.thermal_power_delivered_in_watt = thermal_power_delivered_in_watt
        self.cycle_number = cycle_number

        if thermal_power_delivered_in_watt == 0.0:
            self.activation = 0
            self.heating_power_in_watt = 0.0
            self.cooling_power_in_watt = 0.0
            self.cop = 1.0
            self.electricity_input_in_watt = abs(
                self.thermal_power_delivered_in_watt / self.cop
            )
        elif self.thermal_power_delivered_in_watt > 0.0:
            self.activation = -1
            self.heating_power_in_watt = self.thermal_power_delivered_in_watt
            self.cooling_power_in_watt = 0.0
            self.cop = cop
            self.electricity_input_in_watt = abs(
                self.thermal_power_delivered_in_watt / self.cop
            )
        elif self.thermal_power_delivered_in_watt < 0.0:
            self.activation = 1
            self.heating_power_in_watt = 0
            self.cooling_power_in_watt = self.thermal_power_delivered_in_watt
            self.cop = cop
            self.electricity_input_in_watt = abs(
                self.thermal_power_delivered_in_watt / self.cop
            )
        else:
            raise Exception("Impossible Heat Pump State.")

    def clone(self) -> Any:
        """Clone heat pump state."""
        return GenericHeatPumpStateNew(
            self.start_timestep,
            self.thermal_power_delivered_in_watt,
            self.cop,
            self.cycle_number,
        )


class GenericHeatPumpNew(cp.Component):

    """Heat pump class.

    It does support a refrigeration cycle.
    Thermal output is delivered straight to
    the component object.

    Parameters
    ----------
    manufacturer : str
        Heat pump manufacturer
    name : str
        Heat pump model
    min_operation_time : int, optional
        Minimum time duration that the heat pump operates under one cycle, in seconds. The default is 3600.
    min_idle_time : int, optional
        Minimum time duration that the heat pump has to stay idle, in seconds. The default is 900.

    """

    # Inputs
    State = "State"
    TemperatureOutside = "TemperatureOutside"
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )
    MaxThermalBuildingDemand = "MaxThermalBuildingDemand"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    Heating = "Heating"
    Cooling = "Cooling"
    ElectricityOutput = "ElectricityOutput"
    NumberOfCycles = "NumberOfCycles"
    WaterTemperatureOutput = "WaterTemperatureOutput"
    HeatPumpWaterMassFlowRate = "HeatPumpWaterMassFlowRate"

    # Similar components to connect to:
    # 1. Weather
    # 2. HeatPumpController
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericHeatPumpConfigNew,
    ) -> None:
        """Construct all the necessary attributes."""
        self.heatpump_config = config
        super().__init__(
            self.heatpump_config.name, my_simulation_parameters=my_simulation_parameters
        )
        self.manufacturer = self.heatpump_config.manufacturer
        self.heatpump_name = self.heatpump_config.heat_pump_name
        self.min_operation_time_in_seconds = (
            self.heatpump_config.min_operation_time_in_seconds
        )
        self.min_idle_time_in_seconds = self.heatpump_config.min_idle_time_in_seconds
        self.build(
            self.manufacturer,
            self.heatpump_name,
            self.min_operation_time_in_seconds,
            self.min_idle_time_in_seconds,
        )
        self.specific_heat_capacity_of_water_in_joule_per_kg_per_celsius: float = 1
        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = GenericHeatPumpStateNew(start_timestep=int(0), cycle_number=0)
        self.previous_state = self.state.clone()
        self.has_been_converted: Any

        self.water_temperature_input_in_celsius: float = 50.0
        self.heatpump_water_mass_flow_rate_in_kg_per_second: float = 0
        self.water_temperature_output_in_celsius: float = 50.0
        self.max_thermal_building_demand_in_watt: float = 0
        self.temperature_outside: float = 0

        self.state_from_heat_pump_controller: float = 0

        # Inputs - Mandatories
        self.state_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.State, LoadTypes.ANY, Units.ANY, True
        )
        self.temperature_outside_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            LoadTypes.ANY,
            Units.CELSIUS,
            True,
        )

        # Inputs - Not Mandatories

        self.water_temperature_input_from_heat_water_storage_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.max_thermal_building_demand_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxThermalBuildingDemand,
            LoadTypes.HEATING,
            Units.WATT,
            True,
        )
        # Outputs

        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for {self.ThermalPowerDelivered} will follow.",
        )

        self.heating_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Heating,
            LoadTypes.HEATING,
            Units.WATT,
            output_description=f"here a description for {self.Heating} will follow.",
        )

        self.cooling_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Cooling,
            LoadTypes.COOLING,
            Units.WATT,
            output_description=f"here a description for {self.Cooling} will follow.",
        )

        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description=f"here a description for Heat Pump {self.ElectricityOutput} will follow.",
        )
        self.water_temperature_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterTemperatureOutput,
            LoadTypes.WARM_WATER,
            Units.CELSIUS,
            output_description=f"here a description for {self.WaterTemperatureOutput} will follow.",
        )

        self.heatpump_water_mass_flow_rate_input_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatPumpWaterMassFlowRate,
            LoadTypes.WARM_WATER,
            Units.KG_PER_SEC,
            output_description=f"here a description for {self.HeatPumpWaterMassFlowRate} will follow.",
        )

        self.number_of_cycles_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.NumberOfCycles,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.NumberOfCycles} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_heatpump_controller())

    def get_default_connections_from_weather(self) -> List[cp.ComponentConnection]:
        """Get weather default connections."""
        log.information("setting weather default connections in HeatPump")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHeatPumpNew.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def get_default_connections_heatpump_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get heat pump controller default connections."""
        log.information("setting controller default connections in HeatPump")
        connections = []
        controller_classname = HeatPumpControllerNew.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHeatPumpNew.State,
                controller_classname,
                HeatPumpControllerNew.State,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def build(
        self,
        manufacturer: str,
        name: str,
        min_operation_time_in_seconds: float,
        min_idle_time_in_seconds: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Simulation parameters

        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        heat_pump = None
        for heat_pump in heat_pumps_database:
            if heat_pump["Manufacturer"] == manufacturer and heat_pump["Name"] == name:
                heat_pump_found = True
                break

        if not heat_pump_found or heat_pump is None:
            raise Exception("Heat pump model not registered in the database")

        # Interpolates COP data from the database
        self.cop_ref = []
        self.temperature_outside_ref = []
        for heat_pump_cops in heat_pump["COP"]:
            self.temperature_outside_ref.append(
                float([*heat_pump_cops][0][1:].split("/")[0])
            )
            self.cop_ref.append(float([*heat_pump_cops.values()][0]))
        self.cop_coef = np.polyfit(self.temperature_outside_ref, self.cop_ref, 1)

        self.max_heating_power_in_watt = float(
            heat_pump["Nominal Heating Power A2/35"] * 1e3
        )
        # self.max_heating_power = 11 * 1E3
        self.max_cooling_power_in_watt = -self.max_heating_power_in_watt
        # Retrieves heat pump from database - END

        # Sets the power variation restrictions
        # Default values: 15 minutes to full power
        # Used only for non-clocked heat pump
        self.max_heating_power_variation_restriction_in_watt = (
            self.max_heating_power_in_watt
            * self.my_simulation_parameters.seconds_per_timestep
            / 900
        )
        self.max_cooling_power_variation_restriction_in_watt = (
            -self.max_heating_power_in_watt
            * self.my_simulation_parameters.seconds_per_timestep
            / 900
        )

        # Sets the time operation restricitions
        self.min_operation_time = int(
            min_operation_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.min_idle_time = int(
            min_idle_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )

        # Writes info to report
        self.write_to_report()

        # Applies correction due to timestep
        self.set_time_correction()  # self.set_time_correction(self.time_correction_factor)

    def set_time_correction(self, factor: float = 1) -> None:
        """Set time correction."""
        if factor == 1:
            self.has_been_converted = False
        if self.has_been_converted is True:
            raise Exception("It has been already converted!")
        self.max_heating_power_in_watt *= factor
        self.max_cooling_power_in_watt *= factor
        self.max_heating_power_variation_restriction_in_watt *= factor
        self.max_cooling_power_variation_restriction_in_watt *= factor
        if factor != 1:
            self.has_been_converted = True

    def calc_cop(self, temperature_outside: float) -> float:
        """Calculate cop."""
        val: float = self.cop_coef[0] * temperature_outside + self.cop_coef[1]
        return val

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state.clone()
        self.number_of_cycles_previous = self.number_of_cycles

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state.clone()
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines = []
        lines.append(
            f"Max heating power: {(self.max_heating_power_in_watt) * 1e-3:4.3f} kW"
        )
        lines.append(
            f"Max heating power variation restriction: {self.max_heating_power_variation_restriction_in_watt:4.3f} W"
        )
        return self.heatpump_config.get_string_dict() + lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate heat pump."""

        if force_convergence:
            pass
        else:
            # Inputs
            self.state_from_heat_pump_controller = stsv.get_input_value(
                self.state_channel
            )
            self.temperature_outside = stsv.get_input_value(
                self.temperature_outside_channel
            )
            self.water_temperature_input_in_celsius = stsv.get_input_value(
                self.water_temperature_input_from_heat_water_storage_channel
            )
            self.max_thermal_building_demand_in_watt = stsv.get_input_value(
                self.max_thermal_building_demand_channel
            )

            # Calculation of water mass flow rate of heat pump
            self.heatpump_water_mass_flow_rate_in_kg_per_second = (
                self.calc_heat_pump_water_mass_flow_rate(
                    self.max_thermal_building_demand_in_watt
                )
            )

            # Calculation.ThermalEnergyDelivery
            # Heat Pump is on
            if self.state.activation != 0:

                number_of_cycles = self.state.cycle_number
                # Checks if the minimum running time has been reached
                if (
                    timestep >= self.state.start_timestep + self.min_operation_time
                    and self.state_from_heat_pump_controller == 0
                ):

                    self.state = GenericHeatPumpStateNew(
                        start_timestep=timestep, cycle_number=number_of_cycles
                    )
                    self.water_temperature_output_in_celsius = self.calculate_water_temperature_after_heat_transfer(
                        input_power_in_watt=self.state.thermal_power_delivered_in_watt,
                        water_mass_flow_rate_in_kg_per_second=self.heatpump_water_mass_flow_rate_in_kg_per_second,
                        water_input_temperature_in_celsius=self.water_temperature_input_in_celsius,
                    )

                stsv.set_output_value(
                    self.thermal_power_delivered_channel,
                    self.state.thermal_power_delivered_in_watt,
                )
                stsv.set_output_value(
                    self.heating_channel, self.state.heating_power_in_watt
                )
                stsv.set_output_value(
                    self.cooling_channel, self.state.cooling_power_in_watt
                )
                stsv.set_output_value(
                    self.electricity_output_channel,
                    self.state.electricity_input_in_watt,
                )
                stsv.set_output_value(
                    self.number_of_cycles_channel, self.number_of_cycles
                )
                stsv.set_output_value(
                    self.water_temperature_output_channel,
                    self.water_temperature_output_in_celsius,
                )

                stsv.set_output_value(
                    self.heatpump_water_mass_flow_rate_input_channel,
                    self.heatpump_water_mass_flow_rate_in_kg_per_second,
                )

                return

            # Heat Pump is Off
            if self.state_from_heat_pump_controller != 0 and (
                timestep >= self.state.start_timestep + self.min_idle_time
            ):
                self.number_of_cycles = self.number_of_cycles + 1
                number_of_cycles = self.number_of_cycles

                if self.state_from_heat_pump_controller == 1:

                    self.state = GenericHeatPumpStateNew(
                        start_timestep=timestep,
                        thermal_power_delivered_in_watt=self.max_heating_power_in_watt,
                        cop=self.calc_cop(self.temperature_outside),
                        cycle_number=number_of_cycles,
                    )

                else:
                    self.state = GenericHeatPumpStateNew(
                        start_timestep=timestep,
                        thermal_power_delivered_in_watt=self.max_cooling_power_in_watt,
                        cop=self.calc_cop(self.temperature_outside),
                        cycle_number=number_of_cycles,
                    )

            self.water_temperature_output_in_celsius = self.calculate_water_temperature_after_heat_transfer(
                input_power_in_watt=self.state.thermal_power_delivered_in_watt,
                water_mass_flow_rate_in_kg_per_second=self.heatpump_water_mass_flow_rate_in_kg_per_second,
                water_input_temperature_in_celsius=self.water_temperature_input_in_celsius,
            )

            # Outputs
            stsv.set_output_value(
                self.thermal_power_delivered_channel,
                self.state.thermal_power_delivered_in_watt,
            )
            stsv.set_output_value(
                self.heating_channel, self.state.heating_power_in_watt
            )
            stsv.set_output_value(
                self.cooling_channel, self.state.cooling_power_in_watt
            )
            stsv.set_output_value(
                self.electricity_output_channel, self.state.electricity_input_in_watt
            )
            stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)

            stsv.set_output_value(
                self.water_temperature_output_channel,
                self.water_temperature_output_in_celsius,
            )

            stsv.set_output_value(
                self.heatpump_water_mass_flow_rate_input_channel,
                self.heatpump_water_mass_flow_rate_in_kg_per_second,
            )
            # log.information("hp timestep " + str(timestep))
            # # log.information("hp hpc state " + str(self.state_from_heat_pump_controller))
            # log.information(
            #     "hp thermal power delivered "
            #     + str(self.state.thermal_power_delivered_in_watt)
            # )
            # log.information("hp water temperature input " + str(self.water_temperature_input_in_celsius))
            # log.information(
            #     "hp water temperature output "
            #     + str(self.water_temperature_output_in_celsius))

    def calculate_water_temperature_after_heat_transfer(
        self,
        input_power_in_watt: float,
        water_input_temperature_in_celsius: float,
        water_mass_flow_rate_in_kg_per_second: float,
    ) -> float:
        """Calculate the heated water temperture after the heat transfer from heat pump heating power to water."""
        heated_water_temperature_in_celsius = (
            input_power_in_watt
            / (
                water_mass_flow_rate_in_kg_per_second
                * self.specific_heat_capacity_of_water_in_joule_per_kg_per_celsius
            )
            + water_input_temperature_in_celsius
        )
        return heated_water_temperature_in_celsius

    def calc_heat_pump_water_mass_flow_rate(
        self,
        max_thermal_building_demand_in_watt: float,
    ) -> Any:
        """Calculate water mass flow between heat pump and hot water storage."""
        self.specific_heat_capacity_of_water_in_joule_per_kg_per_celsius = float(
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )
        # information from Noah: deltaT = 3-5 K or °C
        delta_temperature_for_heat_pumps_in_celsius = 4
        heat_pump_water_mass_flow_in_kg_per_second = (
            max_thermal_building_demand_in_watt
            / (
                self.specific_heat_capacity_of_water_in_joule_per_kg_per_celsius
                * delta_temperature_for_heat_pumps_in_celsius
            )
        )
        return heat_pump_water_mass_flow_in_kg_per_second


class HeatPumpControllerNew(cp.Component):

    """Heat Pump Controller.

    It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.

    Parameters
    ----------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump

    """

    # Inputs
    # TemperatureMean = "Residence Temperature"
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )
    ElectricityInput = "ElectricityInput"

    # Outputs
    State = "State"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpControllerConfigNew,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        super().__init__(
            self.heatpump_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
        )
        self.water_temperature_input_from_heat_water_storage_in_celsius: float = 50
        self.build(
            set_water_storage_temperature_for_heating_in_celsius=self.heatpump_controller_config.set_water_storage_temperature_for_heating_in_celsius,
            set_water_storage_temperature_for_cooling_in_celsius=self.heatpump_controller_config.set_water_storage_temperature_for_cooling_in_celsius,
            offset=self.heatpump_controller_config.offset,
            mode=self.heatpump_controller_config.mode,
        )

        self.water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.electricity_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityInput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )

        self.state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

        self.controller_heatpumpmode: Any
        self.previous_heatpump_mode: Any

    def build(
        self,
        set_water_storage_temperature_for_heating_in_celsius: float,
        set_water_storage_temperature_for_cooling_in_celsius: float,
        offset: float,
        mode: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heatpumpmode = "off"
        self.previous_heatpump_mode = self.controller_heatpumpmode

        # Configuration
        self.set_water_storage_temperature_for_heating_in_celsius = (
            set_water_storage_temperature_for_heating_in_celsius
        )
        self.set_water_storage_temperature_for_cooling_in_celsius = (
            set_water_storage_temperature_for_cooling_in_celsius
        )
        self.offset = offset

        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_heatpump_mode = self.controller_heatpumpmode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heatpumpmode = self.previous_heatpump_mode

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_config.get_string_dict()

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            self.water_temperature_input_from_heat_water_storage_in_celsius = (
                stsv.get_input_value(self.water_temperature_input_channel)
            )

            electricity_input = stsv.get_input_value(self.electricity_input_channel)

            if self.mode == 1:
                self.conditions(
                    water_temperature_input_in_celsius=self.water_temperature_input_from_heat_water_storage_in_celsius,
                )
            elif self.mode == 2:
                self.smart_conditions(
                    set_temperature=self.water_temperature_input_from_heat_water_storage_in_celsius,
                    electricity_input=electricity_input,
                )

            if self.controller_heatpumpmode == "heating":
                state = 1
            if self.controller_heatpumpmode == "cooling":
                state = -1
            if self.controller_heatpumpmode == "off":
                state = 0

            stsv.set_output_value(self.state_channel, state)

    def conditions(
        self,
        water_temperature_input_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode."""

        maximum_heating_set_temperature = (
            self.set_water_storage_temperature_for_heating_in_celsius + self.offset
        )
        minimum_heating_set_temperature = (
            self.set_water_storage_temperature_for_heating_in_celsius
        )
        minimum_cooling_set_temperature = (
            self.set_water_storage_temperature_for_cooling_in_celsius - self.offset
        )
        maximum_cooling_set_temperature = (
            self.set_water_storage_temperature_for_cooling_in_celsius
        )

        if self.controller_heatpumpmode == "heating":
            if water_temperature_input_in_celsius > maximum_heating_set_temperature:
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "cooling":
            if water_temperature_input_in_celsius < minimum_cooling_set_temperature:
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "off":
            if water_temperature_input_in_celsius < minimum_heating_set_temperature:
                self.controller_heatpumpmode = "heating"
                return
            if water_temperature_input_in_celsius > maximum_cooling_set_temperature:
                self.controller_heatpumpmode = "cooling"
                return
        else:
            raise ValueError("unknown mode")

    def smart_conditions(
        self, set_temperature: float, electricity_input: float
    ) -> None:
        """Set smart conditions for the heat pump controller mode."""
        smart_offset_upper = 3
        smart_offset_lower = 0.5
        # maximum_heating_set_temperature = self.set_residence_temperature_heating + self.offset
        # if electricity_input < 0:
        #     maximum_heating_set_temperature += smart_offset_upper
        # # maximum_heating_set_temp = self.t_set_heating
        # minimum_heating_set_temperature = self.set_residence_temperature_heating
        # if electricity_input < 0:
        #     minimum_heating_set_temperature += smart_offset_lower
        # minimum_cooling_set_temperature = self.set_residence_temperature_cooling - self.offset
        # # minimum_cooling_set_temp = self.t_set_cooling
        # maximum_cooling_set_temperature = self.set_residence_temperature_cooling

        maximum_heating_set_temperature = (
            self.set_water_storage_temperature_for_heating_in_celsius + self.offset
        )
        if electricity_input < 0:
            maximum_heating_set_temperature += smart_offset_upper
        # maximum_heating_set_temp = self.t_set_heating
        minimum_heating_set_temperature = (
            self.set_water_storage_temperature_for_heating_in_celsius
        )
        if electricity_input < 0:
            minimum_heating_set_temperature += smart_offset_lower
        minimum_cooling_set_temperature = (
            self.set_water_storage_temperature_for_cooling_in_celsius - self.offset
        )
        # minimum_cooling_set_temp = self.t_set_cooling
        maximum_cooling_set_temperature = (
            self.set_water_storage_temperature_for_cooling_in_celsius
        )

        if self.controller_heatpumpmode == "heating":  # and daily_avg_temp < 15:
            if set_temperature > maximum_heating_set_temperature:  # 23
                self.controller_heatpumpmode = "off"
                return
        if self.controller_heatpumpmode == "cooling":
            if set_temperature < minimum_cooling_set_temperature:  # 24
                self.controller_heatpumpmode = "off"
                return
        if self.controller_heatpumpmode == "off":
            # if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
            if set_temperature < minimum_heating_set_temperature:  # 21
                self.controller_heatpumpmode = "heating"
                return
            if set_temperature > maximum_cooling_set_temperature:  # 26
                self.controller_heatpumpmode = "cooling"
                return

        # if timestep >= 60*24*30*3 and timestep <= 60*24*30*9:  #    state = 0

        # log.information("Final state: {}\n".format(state))

    def prin1t_outpu1t(self, t_m: float, state: Any) -> None:
        """Print output of heat pump controller."""
        log.information("==========================================")
        log.information(f"T m: {t_m}")
        log.information(f"State: {state}")
