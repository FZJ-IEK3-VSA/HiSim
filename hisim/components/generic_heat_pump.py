"""Generic Heat Pump module.

This module contains the following classes:
    1. GenericHeatPump State
    2. GenericHeatPump
    3. HeatPumpController

"""
# clean

# Generic/Built-in
import copy
from typing import List, Any, Optional

import numpy as np

from hisim import component as cp
from hisim import log

# Owned
from hisim import utils

# from hisim.components.extended_storage import WaterSlice
# from hisim.components.configuration import WarmWaterStorageConfig
from hisim.components.configuration import PhysicsConfig
from hisim.components.weather import Weather
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class GenericHeatPumpState:

    """Heat Pump State class.

    It determines the state of the heat pump.

    """

    def __init__(
        self,
        start_timestep: int = 0,
        thermal_power_delivered_in_watt: float = 0.0,
        cop: float = 1.0,
        cycle_number: Optional[int] = None,
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
        return GenericHeatPumpState(
            self.start_timestep,
            self.thermal_power_delivered_in_watt,
            self.cop,
            self.cycle_number,
        )


class GenericHeatPump(cp.Component):

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
    WaterConsumption = "WaterConsumption"
    WaterInput_mass = "WaterInput_mass"  # kg/s
    WaterInput_temperature = "WaterInput_temperature"  # °C
    CooledWaterTemperatureFromHeatWaterStorage = (
        "CooledWaterTemperatureFromHeatWaterStorage"
    )
    MaxWaterMassFlowRate = "MaxWaterMassFlowRate"

    # Outputs
    # WaterOutput_mass = "WaterOutput_mass"                           # kg/s
    # WaterOutput_temperature = "WaterOutput_temperature"             # °C
    # WastedEnergyMaxTemperature = "Wasted Energy Max Temperature"    # W

    ThermalPowerDelivered = "ThermalPowerDelivered"
    Heating = "Heating"
    Cooling = "Cooling"
    ElectricityOutput = "ElectricityOutput"
    NumberOfCycles = "NumberOfCycles"
    HeatedWaterTemperature = "HeatedWaterTemperature"

    # Similar components to connect to:
    # 1. Weather
    # 2. HeatPumpController
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        manufacturer: str = "Viessmann Werke GmbH & Co KG",
        name: str = "Vitocal 300-A AWO-AC 301.B07",
        min_operation_time: int = 60 * 60,
        min_idle_time: int = 15 * 60,
    ) -> None:
        """Construct all the necessary attributes."""
        super().__init__("HeatPump", my_simulation_parameters=my_simulation_parameters)

        self.build(manufacturer, name, min_operation_time, min_idle_time)

        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = GenericHeatPumpState(start_timestep=int(0), cycle_number=0)
        self.previous_state = self.state.clone()
        self.has_been_converted: Any
        self.cooled_water_temperature_in_celsius: float = 0
        self.max_water_mass_flow_rate_in_kg_per_second: float = 0
        self.heated_water_temperature_in_celsius: float = 0

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
        # # Inputs - Not Mandatories
        # self.water_load_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.WaterConsumption,
        #     LoadTypes.VOLUME,
        #     Units.LITER,
        #     False,
        # )
        # self.water_input_mass_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.WaterInput_mass,
        #     LoadTypes.WARM_WATER,
        #     Units.KG_PER_SEC,
        #     False,
        # )
        # self.water_input_temperature_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.WaterInput_temperature,
        #     LoadTypes.WARM_WATER,
        #     Units.CELSIUS,
        #     False,
        # )
        self.cooled_water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CooledWaterTemperatureFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.max_water_mass_flow_rate_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.MaxWaterMassFlowRate,
            LoadTypes.WARM_WATER,
            Units.KG_PER_SEC,
            True,
        )
        # Outputs
        # self.water_output_mass: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                          self.WaterOutput_mass,
        #                                                          LoadTypes.WarmWater,
        #                                                          Units.kg_per_sec)
        # self.water_output_temperature: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                                 self.WaterOutput_temperature,
        # ä                                                                      LoadTypes.WarmWater,
        #                                                                 Units.Celsius)
        # self.wasted_energy_max_temperature: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                                      self.WastedEnergyMaxTemperature,
        #                                                                      LoadTypes.WarmWater,
        #                                                                      Units.Watt)

        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            LoadTypes.HEATING,
            Units.WATT,
        )

        self.heating_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.Heating, LoadTypes.HEATING, Units.WATT
        )

        self.cooling_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.Cooling, LoadTypes.COOLING, Units.WATT
        )

        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            LoadTypes.ELECTRICITY,
            Units.WATT,
        )
        self.heated_water_temperature_output_channel: cp.ComponentOutput = (
            self.add_output(
                self.component_name,
                self.HeatedWaterTemperature,
                LoadTypes.WARM_WATER,
                Units.CELSIUS,
            )
        )

        self.number_of_cycles_channel: cp.ComponentOutput = self.add_output(
            self.component_name, self.NumberOfCycles, LoadTypes.ANY, Units.ANY
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
                GenericHeatPump.TemperatureOutside,
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
        controller_classname = HeatPumpController.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHeatPump.State, controller_classname, HeatPumpController.State
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
        min_operation_time: float,
        min_idle_time: float,
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

        self.max_heating_power_in_watt = heat_pump["Nominal Heating Power A2/35"] * 1e3
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
        self.min_operation_time = (
            min_operation_time / self.my_simulation_parameters.seconds_per_timestep
        )
        self.min_idle_time = (
            min_idle_time / self.my_simulation_parameters.seconds_per_timestep
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
        self.state = self.previous_state.clone()  # copy.deepcopy(self.previous_state)
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        lines: List[str] = []
        lines.append("Name: Heat Pump")
        lines.append(
            f"Max heating power: {(self.max_heating_power_in_watt) * 1e-3:4.3f} kW"
        )
        lines.append(
            f"Max heating power variation restriction: {self.max_heating_power_variation_restriction_in_watt:4.3f} W"
        )
        # lines = []
        # lines.append([self.ComponentName,""])
        # lines.append(["Max power:","{:4.2f}".format(self.max_heating_power)])
        # lines.append(["Max power var:","{:4.2f}".format(self.max_heating_power_var)])
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate heat pump."""
        # Inputs
        state_from_heat_pump_controller = stsv.get_input_value(self.state_channel)
        temperature_outside = stsv.get_input_value(self.temperature_outside_channel)
        self.cooled_water_temperature_in_celsius = stsv.get_input_value(
            self.cooled_water_temperature_input_channel
        )
        self.max_water_mass_flow_rate_in_kg_per_second = stsv.get_input_value(
            self.max_water_mass_flow_rate_input_channel
        )
        # log.information("State: {}, Temperature: {}".format(stateC, t_out))
        # log.information("State of Activation: {}".format(self.state.activation))
        # log.information("Timestep special: {}".format(self.state.start_timestep + self.min_idle_time))
        # Calculation

        # Calculation.ThermalEnergyStorage
        # ToDo: Implementation with Thermal Energy Storage - BEGIN
        # if self.water_loadC.SourceOutput is not None:
        #    if stsv.get_input_value(self.water_loadC) != 0:
        #        control_signal = 1
        #    else:
        #        control_signal = 0
        #    # Inputs
        #    water_input_mass_sec = stsv.get_input_value(self.water_input_mass)
        #    water_input_mass = water_input_mass_sec
        #    water_input_temperature = stsv.get_input_value(self.water_input_temperature)

        #    mass_flow_max = self.max_heating_power / (4180 * 25)  # kg/s ## -> ~0.07

        #    if control_signal == 1 and (water_input_mass == 0 and water_input_temperature == 0):
        #        """first iteration"""
        #        water_input_temperature = 40
        #        water_input_mass = mass_flow_max

        #    if control_signal == 1:
        #        volume_flow_gasheater = water_input_mass / PhysicsConfig.water_density
        #        ws = WaterSlice(WarmWaterStorageConfig.tank_diameter,
        #                       (4 * volume_flow_gasheater) / (pi * WarmWaterStorageConfig.tank_diameter ** 2),
        #                        water_input_temperature
        #                       )
        #        ws_output, wasted_energy_max_temperature, thermal_output = self.process_thermal(ws)
        #    else:
        #        height_flow_gasheater = 0
        #        water_input_temperature = 0
        #        ws = WaterSlice(WarmWaterStorageConfig.tank_diameter, height_flow_gasheater, water_input_temperature)
        #        ws_output = ws
        #        wasted_energy_max_temperature = 0

        #    ws_output_mass = ws_output.mass
        #    ws_output_temperature = ws_output.temperature

        #    # Mass is consistent
        #    stsv.set_output_value(self.water_output_mass, ws_output_mass)
        #    stsv.set_output_value(self.water_output_temperature, ws_output_temperature)
        #    stsv.set_output_value(self.wasted_energy_max_temperature, wasted_energy_max_temperature)
        # ToDo: Implementation with Thermal Energy Storage - END

        # Calculation.ThermalEnergyDelivery
        # Heat Pump is on
        if self.state.activation != 0:
            number_of_cycles = self.state.cycle_number
            # Checks if the minimum running time has been reached
            if (
                timestep >= self.state.start_timestep + self.min_operation_time
                and state_from_heat_pump_controller == 0
            ):
                self.state = GenericHeatPumpState(
                    start_timestep=timestep, cycle_number=number_of_cycles
                )

            self.heated_water_temperature_in_celsius = self.calculate_water_temperature_after_heat_transfer(
            input_power_in_watt=self.state.heating_power_in_watt,
            max_water_mass_flow_rate_in_kg_per_second=self.max_water_mass_flow_rate_in_kg_per_second,
            cooled_water_input_temperature_in_celsius=self.cooled_water_temperature_in_celsius,
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
                self.electricity_output_channel, self.state.electricity_input_in_watt
            )
            stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)
            stsv.set_output_value(
            self.heated_water_temperature_output_channel,
            self.heated_water_temperature_in_celsius,
        )
            # log.information("hp timestep " + str(timestep))
            # log.information("hp thermal power delivered " + str(self.state.thermal_power_delivered_in_watt))
            # log.information("hp heating power " + str(self.state.heating_power_in_watt))
            # log.information("hp cooling power " + str(self.state.cooling_power_in_watt))
            # log.information("hp heated water temperature " + str(self.heated_water_temperature_in_celsius))
            return

        # Heat Pump is Off
        if state_from_heat_pump_controller != 0 and (
            timestep >= self.state.start_timestep + self.min_idle_time
        ):
            self.number_of_cycles = self.number_of_cycles + 1
            number_of_cycles = self.number_of_cycles
            if state_from_heat_pump_controller == 1:
                # if stsv.get_input_value(self.stateC) > 0:
                self.state = GenericHeatPumpState(
                    start_timestep=timestep,
                    thermal_power_delivered_in_watt=self.max_heating_power_in_watt,
                    cop=self.calc_cop(temperature_outside),
                    cycle_number=number_of_cycles,
                )

            else:
                self.state = GenericHeatPumpState(
                    start_timestep=timestep,
                    thermal_power_delivered_in_watt=self.max_cooling_power_in_watt,
                    cop=self.calc_cop(temperature_outside),
                    cycle_number=number_of_cycles,
                )

        self.heated_water_temperature_in_celsius = self.calculate_water_temperature_after_heat_transfer(
            input_power_in_watt=self.state.heating_power_in_watt,
            max_water_mass_flow_rate_in_kg_per_second=self.max_water_mass_flow_rate_in_kg_per_second,
            cooled_water_input_temperature_in_celsius=self.cooled_water_temperature_in_celsius,
        )
        # Outputs
        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.state.thermal_power_delivered_in_watt,
        )
        stsv.set_output_value(self.heating_channel, self.state.heating_power_in_watt)
        stsv.set_output_value(self.cooling_channel, self.state.cooling_power_in_watt)
        stsv.set_output_value(
            self.electricity_output_channel, self.state.electricity_input_in_watt
        )
        stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)

        stsv.set_output_value(
            self.heated_water_temperature_output_channel,
            self.heated_water_temperature_in_celsius,
        )
        # log.information("hp timestep " + str(timestep))
        # log.information("hp thermal power delivered " + str(self.state.thermal_power_delivered_in_watt))
        # log.information("hp heating power " + str(self.state.heating_power_in_watt))
        # log.information("hp cooling power " + str(self.state.cooling_power_in_watt))
        # log.information("hp heated water temperature " + str(self.heated_water_temperature_in_celsius))

    def process_thermal(self, ws_in: float) -> None:
        """Process thermal."""
        pass
        # temperature_max = 55
        # heat_capacity = PhysicsConfig.water_specific_heat_capacity
        # thermal_energy_to_add = self.max_heating_power
        # ws_out_mass = ws_in.mass
        # try:
        # ws_out_temperature = ws_in.temperature + thermal_energy_to_add / (heat_capacity * ws_out_mass)
        # except ZeroDivisionError:
        # log.information(heat_capacity)
        # log.information(ws_out_mass)
        # log.information(ws_in.mass)
        # raise ValueError
        # wasted_energy = 0
        # if ws_out_temperature > temperature_max:
        # delta_T = ws_out_temperature - temperature_max
        # wasted_energy = (delta_T * ws_out_mass * PhysicsConfig.water_specific_heat_capacity)
        #  ws_out_temperature = temperature_max
        # ws_out_enthalpy = ws_in.enthalpy + thermal_energy_to_add
        # ws_in.change_slice_parameters(new_temperature=ws_out_temperature, new_enthalpy=ws_out_enthalpy, new_mass=ws_out_mass)
        # return ws_in, wasted_energy, thermal_energy_to_add

    def calculate_water_temperature_after_heat_transfer(
        self,
        input_power_in_watt: float,
        cooled_water_input_temperature_in_celsius: float,
        max_water_mass_flow_rate_in_kg_per_second: float,
    ) -> float:
        """Calculate the heated water temperture after the heat transfer from heat pump heating power to water."""
        heated_water_temperature_in_celsius = (
            input_power_in_watt
            / (
                max_water_mass_flow_rate_in_kg_per_second
                * PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
            )
            + cooled_water_input_temperature_in_celsius
        )
        return heated_water_temperature_in_celsius


class HeatPumpController(cp.Component):

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
    CooledWaterTemperatureFromHeatWaterStorage = (
        "CooledWaterTemperatureFromHeatWaterStorage"
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
        # set_residence_temperature_heating_in_celsius: float = 18.0,
        # set_residence_temperature_cooling_in_celsius: float = 26.0,
        set_water_storage_temperature_for_heating_in_celsius: float = 50,
        set_water_storage_temperature_for_cooling_in_celsius: float = 70,
        offset: float = 0.0,
        mode: int = 1,
    ) -> None:
        """Construct all the neccessary attributes."""
        super().__init__(
            "HeatPumpController", my_simulation_parameters=my_simulation_parameters
        )
        self.build(
            # set_residence_temperature_cooling=set_residence_temperature_cooling_in_celsius,
            # set_residence_temperature_heating=set_residence_temperature_heating_in_celsius,
            set_water_storage_temperature_for_heating_in_celsius=set_water_storage_temperature_for_heating_in_celsius,
            set_water_storage_temperature_for_cooling_in_celsius=set_water_storage_temperature_for_cooling_in_celsius,
            offset=offset,
            mode=mode,
        )

        # self.temperature_mean_channel: cp.ComponentInput = self.add_input(
        #     self.component_name,
        #     self.TemperatureMeanThermalMass,
        #     LoadTypes.TEMPERATURE,
        #     Units.CELSIUS,
        #     True,
        # )

        self.cooled_water_temperature_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CooledWaterTemperatureFromHeatWaterStorage,
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
            self.component_name, self.State, LoadTypes.ANY, Units.ANY
        )

        # self.add_default_connections(self.get_default_connections_from_building())
        self.controller_heatpumpmode: Any
        self.previous_heatpump_mode: Any

    # def get_default_connections_from_building(self) -> List[cp.ComponentConnection]:
    #     """Get building default connections."""
    #     log.information("setting building default connections in Heatpumpcontroller")
    #     connections = []
    #     building_classname = Building.get_classname()
    #     connections.append(
    #         cp.ComponentConnection(
    #             HeatPumpController.TemperatureMean,
    #             building_classname,
    #             Building.TemperatureMeanThermalMass,
    #         )
    #     )
    #     return connections

    def build(
        self,  # set_residence_temperature_heating: float, set_residence_temperature_cooling: float,
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
        # self.set_residence_temperature_heating = set_residence_temperature_heating
        # self.set_residence_temperature_cooling = set_residence_temperature_cooling
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
        lines = []
        lines.append("Heat Pump Controller")
        # todo: add more useful stuff here
        lines.append("tbd")
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass
        else:
            # Retrieves inputs
            # residence_temperature_in_celsius = stsv.get_input_value(self.temperature_mean_channel)
            cooled_water_storage_temperature_in_celsius = stsv.get_input_value(
                self.cooled_water_temperature_input_channel
            )
            electricity_input = stsv.get_input_value(self.electricity_input_channel)

            if self.mode == 1:
                self.conditions(
                    cooled_water_storage_temperature_in_celsius=cooled_water_storage_temperature_in_celsius,
                    # residence_temperature_in_celsius=residence_temperature_in_celsius,
                )
            elif self.mode == 2:
                self.smart_conditions(
                    set_temperature=cooled_water_storage_temperature_in_celsius,
                    # residence_temperature_in_celsius,
                    electricity_input=electricity_input,
                )

            if self.controller_heatpumpmode == "heating":
                state = 1
            if self.controller_heatpumpmode == "cooling":
                state = -1
            if self.controller_heatpumpmode == "off":
                state = 0

            # log.information("hp timestep " + str(timestep))
            # log.information("hp input cool water temp " + str(cooled_water_storage_temperature_in_celsius)) 
            # log.information("hp controller " + str(state))

            stsv.set_output_value(self.state_channel, state)

    def conditions(
        self,
        cooled_water_storage_temperature_in_celsius: float,
    ) -> None:  # residence_temperature_in_celsius: float ) -> None:
        """Set conditions for the heat pump controller mode."""
        # maximum_heating_set_temperature = self.set_residence_temperature_heating + self.offset
        # minimum_heating_set_temperature = self.set_residence_temperature_heating
        # minimum_cooling_set_temperature = self.set_residence_temperature_cooling - self.offset
        # maximum_cooling_set_temperature = self.set_residence_temperature_cooling

        # if self.controller_heatpumpmode == "heating":
        #     if residence_temperature_in_celsius > maximum_heating_set_temperature
        #        and daily_average_outside_temperature_in_celsius > self.set_heating_threshold_temperature:
        #         self.controller_heatpumpmode = "off"
        #         return
        # if self.controller_heatpumpmode == "cooling":
        #     if residence_temperature_in_celsius < minimum_cooling_set_temperature:
        #         self.controller_heatpumpmode = "off"
        #         return
        # if self.controller_heatpumpmode == "off":
        #     # if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
        #     if residence_temperature_in_celsius < minimum_heating_set_temperature
        #       and daily_average_outside_temperature_in_celsius < self.set_heating_threshold_temperature:
        #         self.controller_heatpumpmode = "heating"
        #         return
        #     if residence_temperature_in_celsius > maximum_cooling_set_temperature:
        #         return

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
            if (
                cooled_water_storage_temperature_in_celsius
                > maximum_heating_set_temperature
            ):
                self.controller_heatpumpmode = "off"
                return
        if self.controller_heatpumpmode == "cooling":
            if (
                cooled_water_storage_temperature_in_celsius
                < minimum_cooling_set_temperature
            ):
                self.controller_heatpumpmode = "off"
                return
        if self.controller_heatpumpmode == "off":
            # if pvs_surplus > ? and air_temp < minimum_heating_air + 2:
            if (
                cooled_water_storage_temperature_in_celsius
                < minimum_heating_set_temperature
            ):
                self.controller_heatpumpmode = "heating"
                return
            if (
                cooled_water_storage_temperature_in_celsius
                > maximum_cooling_set_temperature
            ):
                self.controller_heatpumpmode = "cooling"
                return

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
