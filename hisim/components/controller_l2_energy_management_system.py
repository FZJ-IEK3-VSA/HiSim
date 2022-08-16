# Generic/Built-in

# Owned
import copy
import numpy as np

from hisim import dynamic_component
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim import utils
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class ControllerHeatConfig:
    temperature_storage_target_warm_water: float = 50
    temperature_storage_target_heating_water: float = 35
    temperature_storage_target_hysteresis_ww: float = 45
    temperature_storage_target_hysteresis_hw: float = 35


@dataclass_json
@dataclass
class ControllerElectricityConfig:
    strategy: str = "optimize_own_consumption"
    # strategy=["optimize_own_consumption","peak_shaving_from_grid", "peak_shaving_into_grid","seasonal_storage"]
    limit_to_shave: float = 0


# class ControllerState
class ControllerState:
    """
    Save State if Heater Components were supposed to run
    in last timestep (Control_Signal).
    Saves timestep of hysteresis of storages and  the
    changing target temperature of storages. The
    target temperature in states changes when target
    temperature of storage is reached.
    """

    def __init__(self, control_signal_gas_heater: float,
                 control_signal_chp: float,
                 control_signal_heat_pump: float,
                 temperature_storage_target_ww_C: float,
                 temperature_storage_target_hw_C: float,
                 timestep_of_hysteresis_ww: int,
                 timestep_of_hysteresis_hw: int):
        self.control_signal_gas_heater: float = control_signal_gas_heater
        self.control_signal_chp: float = control_signal_chp
        self.control_signal_heat_pump: float = control_signal_heat_pump
        self.temperature_storage_target_ww_C: float = temperature_storage_target_ww_C
        self.temperature_storage_target_hw_C: float = temperature_storage_target_hw_C
        self.timestep_of_hysteresis_ww: int = timestep_of_hysteresis_ww
        self.timestep_of_hysteresis_hw: int = timestep_of_hysteresis_hw

    def clone(self):
        return ControllerState(control_signal_gas_heater=self.control_signal_gas_heater,
                               control_signal_chp=self.control_signal_chp,
                               control_signal_heat_pump=self.control_signal_heat_pump,
                               temperature_storage_target_ww_C=self.temperature_storage_target_ww_C,
                               temperature_storage_target_hw_C=self.temperature_storage_target_hw_C,
                               timestep_of_hysteresis_ww=self.timestep_of_hysteresis_ww,
                               timestep_of_hysteresis_hw=self.timestep_of_hysteresis_hw)


class ControllerHeat(cp.Component):
    """
    Controlls energy flows for heat demand.
    Heat Demand will be controlled by the storage temperature.
    For this storage provides heat for a load profile or for the
    building. As well Heat Demand can be simulated without storage.
    """

    # Inputs
    StorageTemperatureHeatingWater = "StorageTemperatureHeatingWater"
    StorageTemperatureWarmWater = "StorageTemperatureWarmWater"
    ResidenceTemperature = "ResidenceTemperature"
    ThermalDemandBuilding = "ThermalDemandBuilding"

    # Outputs
    ControlSignalGasHeater = "ControlSignalGasHeater"
    ControlSignalChp = "ControlSignalChp"
    ControlSignalHeatPump = "ControlSignalHeatPump"
    ControlSignalChooseStorage = "ControlSignalChooseStorage"

    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config: ControllerHeatConfig):
        super().__init__(name="ControllerHeat", my_simulation_parameters=my_simulation_parameters)

        self.temperature_storage_target_warm_water = config.temperature_storage_target_warm_water
        self.temperature_storage_target_heating_water = config.temperature_storage_target_heating_water
        self.temperature_storage_target_hysteresis_hw = config.temperature_storage_target_hysteresis_hw
        self.temperature_storage_target_hysteresis_ww = config.temperature_storage_target_hysteresis_ww

        self.state = ControllerState(control_signal_heat_pump=0,
                                     control_signal_gas_heater=0,
                                     control_signal_chp=0,
                                     temperature_storage_target_ww_C=self.temperature_storage_target_warm_water,
                                     temperature_storage_target_hw_C=self.temperature_storage_target_heating_water,
                                     timestep_of_hysteresis_ww=0,
                                     timestep_of_hysteresis_hw=0)
        self.previous_state = self.state.clone()

        ###Inputs
        self.temperature_storage_warm_water: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                self.StorageTemperatureWarmWater,
                                                                                lt.LoadTypes.WATER,
                                                                                lt.Units.CELSIUS,
                                                                                False)
        self.temperature_storage_heating_water: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                   self.StorageTemperatureHeatingWater,
                                                                                   lt.LoadTypes.WATER,
                                                                                   lt.Units.CELSIUS,
                                                                                   False)
        self.temperature_residence: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                       self.ResidenceTemperature,
                                                                       lt.LoadTypes.TEMPERATURE,
                                                                       lt.Units.CELSIUS,
                                                                       False)

        # Outputs
        self.control_signal_gas_heater: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                             self.ControlSignalGasHeater,
                                                                             lt.LoadTypes.ANY,
                                                                             lt.Units.PERCENT,
                                                                             False)
        self.control_signal_chp: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                      self.ControlSignalChp,
                                                                      lt.LoadTypes.ANY,
                                                                      lt.Units.PERCENT,
                                                                      False)
        self.control_signal_heat_pump: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                            self.ControlSignalHeatPump,
                                                                            lt.LoadTypes.ANY,
                                                                            lt.Units.PERCENT,
                                                                            False)
        self.control_signal_choose_storage: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                                 self.ControlSignalChooseStorage,
                                                                                 lt.LoadTypes.ANY,
                                                                                 lt.Units.ANY,
                                                                                 False)

    @staticmethod
    def get_default_config():
        config = ControllerHeatConfig(temperature_storage_target_warm_water=50,
                                      temperature_storage_target_heating_water=35,
                                      temperature_storage_target_hysteresis_ww=45,
                                      temperature_storage_target_hysteresis_hw=35)
        return config

    def build(self, mode):
        self.mode = mode

    def write_to_report(self):
        pass

    def i_save_state(self):
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    # Simulates and defines the control signals to heat up storages
    # work as a 2-point Ruler with Hysteresis
    def simulate_storage(self, delta_temperature: float,
                         stsv: cp.SingleTimeStepValues,
                         timestep: int, temperature_storage: float,
                         temperature_storage_target: float, temperature_storage_target_hysteresis: float,
                         temperature_storage_target_C: float, timestep_of_hysteresis: int):
        control_signal_chp: float = 0
        control_signal_gas_heater: float = 0
        control_signal_heat_pump: float = 0
        temperature_storage_target_C = temperature_storage_target_C
        timestep_of_hysteresis = timestep_of_hysteresis

        max_temperature_limit = 5
        if temperature_storage > 0:
            if delta_temperature > max_temperature_limit:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1
                temperature_storage_target_C = temperature_storage_target


            elif delta_temperature > 0 and delta_temperature <= max_temperature_limit:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1

                if self.state.control_signal_chp < control_signal_chp:
                    control_signal_chp = 1
                elif self.state.control_signal_gas_heater < control_signal_gas_heater:
                    control_signal_gas_heater = 1
                temperature_storage_target_C = temperature_storage_target

                # Storage warm enough. Try to turn off Heaters
            elif delta_temperature <= 0:
                if temperature_storage_target_C == temperature_storage_target and timestep_of_hysteresis != timestep:
                    temperature_storage_target_C = temperature_storage_target_hysteresis
                    timestep_of_hysteresis = timestep
                elif temperature_storage_target_C != temperature_storage_target and timestep_of_hysteresis != timestep:
                    control_signal_heat_pump = 0
                    control_signal_gas_heater = 0
                    control_signal_chp = 0

        self.state.control_signal_gas_heater = control_signal_gas_heater
        self.state.control_signal_chp = control_signal_chp
        self.state.control_signal_heat_pump = control_signal_heat_pump
        stsv.set_output_value(self.control_signal_heat_pump, control_signal_heat_pump)
        stsv.set_output_value(self.control_signal_gas_heater, control_signal_gas_heater)
        stsv.set_output_value(self.control_signal_chp, control_signal_chp)

        return temperature_storage_target_C, \
               timestep_of_hysteresis

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool):
        if force_convergence:
            return
        #######HEAT########
        # Logic of regulating HeatDemand:
        # First heat up WarmWaterStorage->more important, than heat up HeatingWater
        # But only one Storage can be heated up in a TimeStep!
        # Simulate WarmWater
        delta_temperature_ww = self.state.temperature_storage_target_ww_C - stsv.get_input_value(
            self.temperature_storage_warm_water)
        delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
            self.temperature_storage_heating_water)
        if stsv.get_input_value(self.temperature_storage_warm_water) == 0 and stsv.get_input_value(
                self.temperature_storage_heating_water) != 0:
            control_signal_choose_storage = 2
        elif stsv.get_input_value(self.temperature_storage_warm_water) != 0 and stsv.get_input_value(
                self.temperature_storage_heating_water) == 0:
            control_signal_choose_storage = 1
        else:
            # Choose which Storage should be heated up
            if delta_temperature_ww >= 0 and delta_temperature_hw >= 0:
                if delta_temperature_hw <= delta_temperature_ww:
                    control_signal_choose_storage = 1
                else:
                    control_signal_choose_storage = 2
            elif delta_temperature_ww < 0 and delta_temperature_hw < 0:
                if delta_temperature_hw <= delta_temperature_ww:
                    control_signal_choose_storage = 1
                else:
                    control_signal_choose_storage = 2
            elif delta_temperature_ww <= 0 and delta_temperature_hw >= 0:
                control_signal_choose_storage = 2
            elif delta_temperature_ww >= 0 and delta_temperature_hw <= 0:
                control_signal_choose_storage = 1

        # Heats up storage
        if control_signal_choose_storage == 1:
            self.state.temperature_storage_target_ww_C, self.state.timestep_of_hysteresis_ww = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_ww,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_warm_water),
                temperature_storage_target=self.temperature_storage_target_warm_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_ww,
                temperature_storage_target_C=self.state.temperature_storage_target_ww_C,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_ww)
        elif control_signal_choose_storage == 2:
            delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
                self.temperature_storage_heating_water)
            self.state.temperature_storage_target_hw_C, self.state.timestep_of_hysteresis_hw = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_hw,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_heating_water),
                temperature_storage_target=self.temperature_storage_target_heating_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_hw,
                temperature_storage_target_C=self.state.temperature_storage_target_hw_C,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_hw)

        stsv.set_output_value(self.control_signal_choose_storage, control_signal_choose_storage)


class ControllerElectricity(cp.Component):
    """
    Controlls energy flows for electricity and heat demand.
    Electricity storages can be ruled in 4 different strategies.
    """
    # Inputs
    ElectricityConsumptionBuilding = "ElectricityConsumptionBuilding"
    ElectricityOutputPvs = "ElectricityOutputPvs"
    ElectricityDemandHeatPump = "ElectricityDemandHeatPump"
    ElectricityToOrFromBatteryReal = "ElectricityToOrFromBatteryReal"
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
    ElectricityFromCHPReal = "ElectricityFromCHPReal"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"
    ElectricityToOrFromBatteryTarget = "ElectricityToOrFromBatteryTarget"
    ElectricityFromCHPTarget = "ElectricityFromCHPTarget"
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"

    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 config: ControllerElectricityConfig):
        super().__init__(name="ControllerElectricity", my_simulation_parameters=my_simulation_parameters)

        self.strategy = config.strategy
        self.limit_to_shave = config.limit_to_shave

        ###Inputs
        self.electricity_consumption_building: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                  self.ElectricityConsumptionBuilding,
                                                                                  lt.LoadTypes.ELECTRICITY,
                                                                                  lt.Units.WATT,
                                                                                  False)
        self.electricity_output_pvs: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                        self.ElectricityOutputPvs,
                                                                        lt.LoadTypes.ELECTRICITY,
                                                                        lt.Units.WATT,
                                                                        False)

        self.electricity_to_or_from_battery_real: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                     self.ElectricityToOrFromBatteryReal,
                                                                                     lt.LoadTypes.ELECTRICITY,
                                                                                     lt.Units.WATT,
                                                                                     False)
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                    self.ElectricityToElectrolyzerUnused,
                                                                                    lt.LoadTypes.ELECTRICITY,
                                                                                    lt.Units.WATT,
                                                                                    False)
        self.electricity_from_chp_real: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                           self.ElectricityFromCHPReal,
                                                                           lt.LoadTypes.ELECTRICITY,
                                                                           lt.Units.WATT,
                                                                           False)
        self.electricity_demand_heat_pump: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                              self.ElectricityDemandHeatPump,
                                                                              lt.LoadTypes.ELECTRICITY,
                                                                              lt.Units.WATT,
                                                                              False)

        # Outputs

        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                               self.ElectricityToOrFromGrid,
                                                                               lt.LoadTypes.ELECTRICITY,
                                                                               lt.Units.WATT,
                                                                               False)
        self.electricity_from_chp_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                               self.ElectricityFromCHPTarget,
                                                                               lt.LoadTypes.ELECTRICITY,
                                                                               lt.Units.WATT,
                                                                               False)
        self.electricity_to_electrolyzer_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                                      self.ElectricityToElectrolyzerTarget,
                                                                                      lt.LoadTypes.ELECTRICITY,
                                                                                      lt.Units.WATT,
                                                                                      False)
        self.electricity_to_or_from_battery_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                                         self.ElectricityToOrFromBatteryTarget,
                                                                                         lt.LoadTypes.ELECTRICITY,
                                                                                         lt.Units.WATT,
                                                                                         False)

    def build(self, mode):
        self.mode = mode

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def optimize_own_consumption(self, delta_demand: float, stsv: cp.SingleTimeStepValues):

        electricity_to_or_from_battery_target: float = 0
        electricity_from_chp_target: float = 0
        electricity_to_or_from_grid: float = 0

        # Check if Battery is Component of Simulation
        if self.electricity_to_or_from_battery_real.SourceOutput is not None:
            electricity_to_or_from_battery_target = delta_demand

        # electricity_not_used_battery of Charge or Discharge
        electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
            self.electricity_to_or_from_battery_real)
        # more electricity than needed
        if delta_demand > 0:
            # Negative sign, because Electricity will flow into grid->Production of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(self.electricity_to_or_from_battery_real)

        # less electricity than needed
        elif delta_demand < 0:
            if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_from_chp_real.SourceOutput is not None:
                electricity_from_chp_target = -delta_demand + stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)

            # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)

    # seasonal storaging is almost the same as own_consumption, but a electrolyzer is added
    # follows strategy to first charge battery than produce H2
    def seasonal_storage(self, delta_demand: float, stsv: cp.SingleTimeStepValues):

        electricity_to_or_from_battery_target: float = 0
        electricity_from_chp_target: float = 0
        electricity_to_or_from_grid: float = 0
        electricity_to_electrolyzer_target: float = 0

        # Check if Battery is Component of Simulation
        if self.electricity_to_or_from_battery_real.SourceOutput is not None:
            electricity_to_or_from_battery_target = delta_demand

        # electricity_not_used_battery of Charge or Discharge
        electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
            self.electricity_to_or_from_battery_real)
        # more electricity than needed
        if delta_demand > 0:
            # Check if enough electricity is there to charge CHP (finds real solution after 2 Iteration-Steps)
            if self.electricity_to_electrolyzer_unused.SourceOutput is not None:
                # possibility to  produce H2
                electricity_to_electrolyzer_target = delta_demand - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)
                if electricity_to_electrolyzer_target < 0:
                    electricity_to_electrolyzer_target = 0

            # Negative sign, because Electricity will flow into grid->Production of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) + (electricity_to_electrolyzer_target - stsv.get_input_value(
                self.electricity_to_electrolyzer_unused))

        # less electricity than needed
        elif delta_demand < 0:

            if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_from_chp_real.SourceOutput is not None:
                electricity_from_chp_target = -delta_demand + stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)

            # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
        stsv.set_output_value(self.electricity_to_electrolyzer_target, electricity_to_electrolyzer_target)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)

    # peak-shaving from grid tries to reduce/shave electricity from grid to an defined boarder
    # just used for industry, trade and service
    # so far no chp is added. But produces elect. has to be addded to delta demand
    def peak_shaving_from_grid(self, delta_demand: float, limit_to_shave: float, stsv: cp.SingleTimeStepValues):
        electricity_to_or_from_battery_target: float = 0
        check_peak_shaving: float = 0

        # more electricity than needed
        if delta_demand > 0:
            electricity_to_or_from_battery_target = delta_demand
        elif -delta_demand > limit_to_shave:
            check_peak_shaving = 1
            electricity_to_or_from_battery_target = delta_demand + limit_to_shave
            if -delta_demand + limit_to_shave + stsv.get_input_value(
                    self.electricity_to_or_from_battery_real) > 0:
                check_peak_shaving = -delta_demand + limit_to_shave + stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)
        electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
            self.electricity_to_or_from_battery_real)

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)

    # peak-shaving from grid tries to reduce/shave electricity into grid to an defined boarder
    # so far no chp is added. But produces elect. has to be addded to delta demand
    def peak_shaving_into_grid(self, delta_demand: float, limit_to_shave: float, stsv: cp.SingleTimeStepValues):
        # Hier delta Demand noch die Leistung aus CHP hinzufügen
        electricity_to_or_from_battery_target: float = 0
        check_peak_shaving: float = 0

        if delta_demand > limit_to_shave:
            electricity_to_or_from_battery_target = delta_demand - limit_to_shave

            if delta_demand - limit_to_shave - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real) > 0:
                check_peak_shaving = delta_demand - limit_to_shave - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)  # Peak Shaving didnt work
            else:
                check_peak_shaving = 1
        elif delta_demand < 0:
            electricity_to_or_from_battery_target = delta_demand

        electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
            self.electricity_to_or_from_battery_real)
        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool):
        if force_convergence:
            return

        ###ELECTRICITY#####
        limit_to_shave = self.limit_to_shave
        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        delta_demand = stsv.get_input_value(self.electricity_output_pvs) \
                       - stsv.get_input_value(self.electricity_consumption_building) \
                       - stsv.get_input_value(self.electricity_demand_heat_pump)

        if self.strategy == "optimize_own_consumption":
            self.optimize_own_consumption(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "seasonal_storage":
            self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "peak_shaving_into_grid":
            self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave, stsv=stsv)
        elif self.strategy == "peak_shaving_from_grid":
            self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave, stsv=stsv)


class ControllerElectricityGeneric(dynamic_component.DynamicComponent):
    """
    Controlls energy flows for electricity.
    Electricity storages can be ruled in 4 different strategies.
    Component can be added generically
    """
    # Inputs
    my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"
    my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 strategy: str = "optimize_own_consumption",
                 # strategy=["optimize_own_consumption","peak_shaving_from_grid", "peak_shaving_into_grid","seasonal_storage"]
                 limit_to_shave: float = 0):
        super().__init__(my_component_inputs=self.my_component_inputs,
                         my_component_outputs=self.my_component_inputs,
                         name="Controller",
                         my_simulation_parameters=my_simulation_parameters)

        self.strategy = strategy
        self.limit_to_shave = limit_to_shave
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                    self.ElectricityToElectrolyzerUnused,
                                                                                    lt.LoadTypes.ELECTRICITY,
                                                                                    lt.Units.WATT,
                                                                                    False)

        # Outputs
        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                               self.ElectricityToOrFromGrid,
                                                                               lt.LoadTypes.ELECTRICITY,
                                                                               lt.Units.WATT,
                                                                               False)

        self.check_peak_shaving: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                      self.CheckPeakShaving,
                                                                      lt.LoadTypes.ANY,
                                                                      lt.Units.ANY,
                                                                      False)

    def sort_source_weights_and_components(self):
        SourceTags = [elem.source_tags[0] for elem in self.my_component_inputs]
        SourceWeights = [elem.source_weight for elem in self.my_component_outputs]
        sortindex = sorted(range(len(SourceWeights)), key=lambda k: SourceWeights[k])
        self.source_weights_sorted = [SourceWeights[i] for i in sortindex]
        self.components_sorted = [SourceTags[i] for i in sortindex]

    def get_entries_of_type(self, component_type: lt.ComponentType):
        return self.components_sorted.index(component_type)

    def build(self, mode):
        self.mode = mode

    def write_to_report(self):
        pass

    def i_save_state(self):
        # abändern, siehe Storage
        pass
        # self.previous_state = self.state

    def i_restore_state(self):
        pass
        # self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def control_electricity_component(self, demand: float,
                                      stsv: cp.SingleTimeStepValues,
                                      my_component_inputs: list,
                                      my_component_outputs: list,
                                      weight_counter: int,
                                      component_type: list,
                                      input_type: lt.InandOutputType,
                                      output_type: lt.InandOutputType):
        # to do: add that to much chp-electricty is charged in Battery and doesnt go in to grid
        for index, element in enumerate(my_component_outputs):
            for tags in element.source_tags:
                if tags.__class__ == lt.ComponentType and tags in component_type:
                    if element.source_weight == weight_counter:
                        # more electricity than needed
                        if tags == lt.ComponentType.BATTERY:
                            stsv.set_output_value(self.__getattribute__(element.source_component_class), demand)
                            break
                        elif tags == lt.ComponentType.FUEL_CELL:
                            if demand < 0:
                                stsv.set_output_value(self.__getattribute__(element.source_component_class), -demand)
                            else:
                                stsv.set_output_value(self.__getattribute__(element.source_component_class), 0)
                            break
            else:
                continue
            break
        for index, element in enumerate(my_component_inputs):
            for tags in element.source_tags:
                if tags.__class__ == lt.ComponentType and tags in component_type:
                    if element.source_weight == weight_counter:
                        if tags == lt.ComponentType.BATTERY:
                            demand = demand - stsv.get_input_value(self.__getattribute__(element.source_component_class))
                            break
                        elif tags == lt.ComponentType.FUEL_CELL:
                            demand = demand + stsv.get_input_value(self.__getattribute__(element.source_component_class))
                            break
            else:
                continue
            break
        return demand

    def control_electricity_component_iterative(self, deltademand: float,
                                                stsv: cp.SingleTimeStepValues,
                                                weight_counter: int,
                                                component_type: lt.ComponentType):
        is_battery = None
        # get previous signal and substract from total balance
        previous_signal = self.get_dynamic_input(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_REAL],
                                                 weight_counter=weight_counter)

        # control from substracted balance
        if component_type == lt.ComponentType.BATTERY:
            self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET],
                                    weight_counter=weight_counter, output_value=deltademand)
            deltademand = deltademand - previous_signal

        elif component_type == lt.ComponentType.FUEL_CELL:
            if deltademand < 0:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET],
                                        weight_counter=weight_counter, output_value=-deltademand)
                deltademand = deltademand + previous_signal
                if deltademand > 0:
                    is_battery = self.get_entries_of_type(lt.ComponentType.BATTERY)
            else:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET],
                                        weight_counter=weight_counter, output_value=0)

        elif component_type == lt.ComponentType.ELECTROLYZER:
            if deltademand > 0:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET],
                                        weight_counter=weight_counter, output_value=deltademand)
                deltademand = deltademand - previous_signal
            else:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET],
                                        weight_counter=weight_counter, output_value=0)

        return deltademand, is_battery

    def postprocess_battery(self, deltademand: float,
                            stsv: cp.SingleTimeStepValues,
                            ind: int):
        previous_signal = self.get_dynamic_input(stsv=stsv, tags=[self.components_sorted[ind]],
                                                 weight_counter=self.source_weights_sorted[ind])
        self.set_dynamic_output(stsv=stsv, tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
                                weight_counter=self.source_weights_sorted[ind],
                                output_value=deltademand + previous_signal)
        return deltademand - previous_signal

    def optimize_own_consumption(self, delta_demand: float, stsv: cp.SingleTimeStepValues):
        electricity_to_or_from_grid: float = 0
        my_component_inputs = self.my_component_inputs
        my_component_outputs = self.my_component_outputs
        # Rule Battery
        for weight_counter in range(1, len(my_component_inputs) + 1):
            delta_demand = self.control_electricity_component(demand=delta_demand, stsv=stsv,
                                                              my_component_inputs=my_component_inputs,
                                                              my_component_outputs=my_component_outputs,
                                                              weight_counter=weight_counter,
                                                              component_type=[lt.ComponentType.BATTERY,
                                                                              lt.ComponentType.FUEL_CELL],
                                                              input_type=lt.InandOutputType.ELECTRICITY_REAL,
                                                              output_type=lt.InandOutputType.ELECTRICITY_TARGET)

        # more electricity than needed
        if delta_demand > 0:
            # Negative sign, because Electricity will flow into grid->Production of Electricity
            electricity_to_or_from_grid = -delta_demand

        # less electricity than needed
        elif delta_demand < 0:
            # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
            electricity_to_or_from_grid = -delta_demand

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)

    def optimize_own_consumption_iterative(self, delta_demand: float, stsv: cp.SingleTimeStepValues):
        skip_CHP = False
        for ind in range(len(self.source_weights_sorted)):
            component_type = self.components_sorted[ind]
            source_weight = self.source_weights_sorted[ind]
            if component_type in [lt.ComponentType.BATTERY, lt.ComponentType.FUEL_CELL, lt.ComponentType.ELECTROLYZER]:
                if not skip_CHP or component_type in [lt.ComponentType.BATTERY, lt.ComponentType.ELECTROLYZER]:
                    delta_demand, is_battery = self.control_electricity_component_iterative(deltademand=delta_demand,
                                                                                            stsv=stsv,
                                                                                            weight_counter=source_weight,
                                                                                            component_type=component_type)
                else:
                    self.set_dynamic_output(stsv=stsv,
                                            tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
                                            weight_counter=source_weight, output_value=0)
                if is_battery is not None:
                    delta_demand = self.postprocess_battery(deltademand=delta_demand,
                                                            stsv=stsv,
                                                            ind=is_battery)
                    skip_CHP = True

    # seasonal storaging is almost the same as own_consumption, but a electrolyzer is added
    # follows strategy to first charge battery than produce H2
    '''
    def seasonal_storage(self, delta_demand: float, stsv: cp.SingleTimeStepValues):

        electricity_to_or_from_battery_target:float = 0
        electricity_from_chp_target:float = 0
        electricity_to_or_from_grid:float = 0
        electricity_to_electrolyzer_target:float = 0

        # Check if Battery is Component of Simulation
        if self.electricity_to_or_from_battery_real.SourceOutput is not None:
            electricity_to_or_from_battery_target = delta_demand

        # electricity_not_used_battery of Charge or Discharge
        electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
            self.electricity_to_or_from_battery_real)
        # more electricity than needed
        if delta_demand > 0:
            # Check if enough electricity is there to charge CHP (finds real solution after 2 Iteration-Steps)
            if self.electricity_to_electrolyzer_unused.SourceOutput is not None:
                # possibility to  produce H2
                electricity_to_electrolyzer_target = delta_demand - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)
                if electricity_to_electrolyzer_target<0:
                    electricity_to_electrolyzer_target=0

            # Negative sign, because Electricity will flow into grid->Production of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) + (electricity_to_electrolyzer_target-stsv.get_input_value(self.electricity_to_electrolyzer_unused))

        # less electricity than needed
        elif delta_demand < 0:

            if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_from_chp_real.SourceOutput is not None:
                electricity_from_chp_target = -delta_demand + stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)

            # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
        stsv.set_output_value(self.electricity_to_electrolyzer_target, electricity_to_electrolyzer_target)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)


    #peak-shaving from grid tries to reduce/shave electricity from grid to an defined boarder
    #just used for industry, trade and service
    #so far no chp is added. But produces elect. has to be addded to delta demand
    def peak_shaving_from_grid(self,delta_demand:float,limit_to_shave: float,stsv: cp.SingleTimeStepValues):
        electricity_to_or_from_battery_target:float=0
        check_peak_shaving:float=0

        # more electricity than needed
        if delta_demand > 0:
            electricity_to_or_from_battery_target = delta_demand
        elif -delta_demand >  limit_to_shave:
            check_peak_shaving=1
            electricity_to_or_from_battery_target= delta_demand+limit_to_shave
            if -delta_demand + limit_to_shave + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) > 0:
                check_peak_shaving = -delta_demand + limit_to_shave + stsv.get_input_value(
                self.electricity_to_or_from_battery_real)
        electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
            self.electricity_to_or_from_battery_real)

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
        stsv.set_output_value(self.check_peak_shaving, check_peak_shaving)

    #peak-shaving from grid tries to reduce/shave electricity into grid to an defined boarder
    #so far no chp is added. But produces elect. has to be addded to delta demand
    def peak_shaving_into_grid(self,delta_demand:float,limit_to_shave: float,stsv: cp.SingleTimeStepValues):
        #Hier delta Demand noch die Leistung aus CHP hinzufügen
        electricity_to_or_from_battery_target:float=0
        check_peak_shaving:float=0

        if delta_demand > limit_to_shave:
            electricity_to_or_from_battery_target=delta_demand-limit_to_shave

            if delta_demand - limit_to_shave - stsv.get_input_value(
                self.electricity_to_or_from_battery_real) > 0:
                check_peak_shaving = delta_demand - limit_to_shave - stsv.get_input_value(
                self.electricity_to_or_from_battery_real) # Peak Shaving didnt work
            else:
                check_peak_shaving = 1
        elif delta_demand<0:
            electricity_to_or_from_battery_target=delta_demand

        electricity_to_or_from_grid=-delta_demand + stsv.get_input_value(
            self.electricity_to_or_from_battery_real)
        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)
        stsv.set_output_value(self.check_peak_shaving, check_peak_shaving)
    '''

    # Simulates waterstorages and defines the control signals to heat up storages
    # work as a 2-point Ruler with Hysteresis

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool):
        if force_convergence:
            return

        if timestep == 0:
            self.sort_source_weights_and_components()

        ###ELECTRICITY#####
        limit_to_shave = self.limit_to_shave

        # get production
        production = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.PRODUCTION]))
        consumption = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.CONSUMPTION]))

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        delta_demand = production - consumption

        if self.strategy == "optimize_own_consumption":
            self.optimize_own_consumption_iterative(delta_demand=delta_demand, stsv=stsv)
            stsv.set_output_value(self.electricity_to_or_from_grid, delta_demand)
        '''
        elif self.strategy == "seasonal_storage":
            self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "peak_shaving_into_grid":
            self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        elif self.strategy == "peak_shaving_from_grid":
            self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        '''

        #######HEAT########
        # If comftortable temperature of building is to low heat with WarmWaterStorage the building
        # Solution with Control Signal Residence
        # not perfect solution!
        '''
        if self.temperature_residence<self.min_comfortable_temperature_residence:


            #heat
            #here has to be added how "strong" HeatingWater Storage can be discharged
            #Working with upper boarder?

        elif self.temperature_residence > self.max_comfortable_temperature_residence:
            #cool
        elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
        '''


class ControllerHeatGeneric(dynamic_component.DynamicComponent):
    """
    Controlls energy flows for heat demand.
    Heat Demand can be simulated by a sinking storage temperature.
    For this storage provides heat for a load profile or for the
    building. As well Heat Demand can be simulated without storage.
    Heating component can be added generically.
    """
    # Inputs

    StorageTemperatureHeatingWater = "StorageTemperatureHeatingWater"
    StorageTemperatureWarmWater = "StorageTemperatureWarmWater"
    ResidenceTemperature = "ResidenceTemperature"
    my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []

    # Outputs
    my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []

    ControlSignalGasHeater = "ControlSignalGasHeater"
    ControlSignalChp = "ControlSignalChp"
    ControlSignalHeatPump = "ControlSignalHeatPump"
    ControlSignalChooseStorage = "ControlSignalChooseStorage"

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 temperature_storage_target_warm_water: float = 50,
                 temperature_storage_target_heating_water: float = 35,
                 temperature_storage_target_hysteresis_ww: float = 45,
                 temperature_storage_target_hysteresis_hw: float = 30,
                 max_comfortable_temperature_residence: float = 23,
                 min_comfortable_temperature_residence: float = 19):
        super().__init__(my_component_inputs=self.my_component_inputs,
                         my_component_outputs=self.my_component_outputs,
                         name="Controller",
                         my_simulation_parameters=my_simulation_parameters)

        self.temperature_storage_target_warm_water = temperature_storage_target_warm_water
        self.temperature_storage_target_heating_water = temperature_storage_target_heating_water
        self.temperature_storage_target_hysteresis_hw = temperature_storage_target_hysteresis_hw
        self.temperature_storage_target_hysteresis_ww = temperature_storage_target_hysteresis_ww
        self.max_comfortable_temperature_residence = max_comfortable_temperature_residence
        self.min_comfortable_temperature_residence = min_comfortable_temperature_residence
        self.state = ControllerState(control_signal_heat_pump=0,
                                     control_signal_gas_heater=0,
                                     control_signal_chp=0,
                                     temperature_storage_target_ww_C=self.temperature_storage_target_warm_water,
                                     temperature_storage_target_hw_C=self.temperature_storage_target_heating_water,
                                     timestep_of_hysteresis_ww=0,
                                     timestep_of_hysteresis_hw=0)
        self.previous_state = self.state.clone()
        ###Inputs
        self.temperature_storage_warm_water: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                self.StorageTemperatureWarmWater,
                                                                                lt.LoadTypes.WATER,
                                                                                lt.Units.CELSIUS,
                                                                                False)
        self.temperature_storage_heating_water: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                   self.StorageTemperatureHeatingWater,
                                                                                   lt.LoadTypes.WATER,
                                                                                   lt.Units.CELSIUS,
                                                                                   False)
        self.temperature_residence: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                       self.ResidenceTemperature,
                                                                       lt.LoadTypes.TEMPERATURE,
                                                                       lt.Units.CELSIUS,
                                                                       False)

        # Outputs
        self.control_signal_gas_heater: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                             self.ControlSignalGasHeater,
                                                                             lt.LoadTypes.ANY,
                                                                             lt.Units.PERCENT,
                                                                             False)
        self.control_signal_chp: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                      self.ControlSignalChp,
                                                                      lt.LoadTypes.ANY,
                                                                      lt.Units.PERCENT,
                                                                      False)
        self.control_signal_heat_pump: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                            self.ControlSignalHeatPump,
                                                                            lt.LoadTypes.ANY,
                                                                            lt.Units.PERCENT,
                                                                            False)
        self.control_signal_choose_storage: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                                 self.ControlSignalChooseStorage,
                                                                                 lt.LoadTypes.ANY,
                                                                                 lt.Units.ANY,
                                                                                 False)

    def build(self, mode):
        self.mode = mode

    def write_to_report(self):
        pass

    def i_save_state(self):
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    '''
    def control_electricity_component(self,demand:float,
                                   stsv:cp.SingleTimeStepValues,
                                   MyComponentInputs: list,
                                   MyComponentOutputs:list,
                                   weight_counter:int,
                                   component_type: list,
                                   input_type: lt.InandOutputType,
                                   output_type:lt.InandOutputType):
        # to do: add that to much chp-electricty is charged in Battery and doesnt go in to grid
        for index, element in enumerate(MyComponentOutputs):
            for tags in element.SourceTags:
                if tags.__class__ == lt.ComponentType and tags in component_type:
                    if element.SourceWeight == weight_counter:
                        # more electricity than needed
                        if tags ==lt.ComponentType.Battery:
                            stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), demand)
                            break
                        elif tags ==lt.ComponentType.FuelCell:
                            if demand < 0:
                                stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), -demand)
                            else:
                                stsv.set_output_value(self.__getattribute__(element.SourceComponentClass), 0)
                            break
            else:
                continue
            break
        for index, element in enumerate(MyComponentInputs):
            for tags in element.SourceTags:
                if tags.__class__ == lt.ComponentType and tags in component_type:
                    if element.SourceWeight == weight_counter:
                        if tags == lt.ComponentType.Battery:
                            demand = demand - stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
                            break
                        elif tags == lt.ComponentType.FuelCell:
                            demand = demand + stsv.get_input_value(self.__getattribute__(element.SourceComponentClass))
                            break
            else:
                continue
            break
        return demand
    '''

    # Simulates waterstorages and defines the control signals to heat up storages
    # work as a 2-point Ruler with Hysteresis
    def simulate_storage(self, delta_temperature: float,
                         stsv: cp.SingleTimeStepValues,
                         timestep: int, temperature_storage: float,
                         temperature_storage_target: float, temperature_storage_target_hysteresis: float,
                         temperature_storage_target_C: float, timestep_of_hysteresis: int):
        control_signal_chp: float = 0
        control_signal_gas_heater: float = 0
        control_signal_heat_pump: float = 0
        temperature_storage_target_C = temperature_storage_target_C
        timestep_of_hysteresis = timestep_of_hysteresis

        # WaterStorage
        # Heating Components get turned on when storage is underneath target temperature
        if temperature_storage > 0:
            if delta_temperature >= 10:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1
                temperature_storage_target_C = temperature_storage_target

            elif delta_temperature > 5 and delta_temperature < 10:
                control_signal_heat_pump = 1
                if self.state.control_signal_chp < 1:
                    control_signal_chp = 1
                    control_signal_gas_heater = 1
                elif self.state.control_signal_chp == 1:
                    control_signal_gas_heater = 1
                temperature_storage_target_C = temperature_storage_target

            elif delta_temperature > 0 and delta_temperature <= 5:
                control_signal_heat_pump = 1
                if self.state.control_signal_chp < 1:
                    control_signal_chp = 1
                elif self.state.control_signal_chp == 1:
                    control_signal_gas_heater = 0.5
                temperature_storage_target_C = temperature_storage_target

                # Storage warm enough. Try to turn off Heaters
            elif delta_temperature <= 0:
                if temperature_storage_target_C == temperature_storage_target and timestep_of_hysteresis != timestep:
                    temperature_storage_target_C = temperature_storage_target_hysteresis
                    timestep_of_hysteresis = timestep
                elif temperature_storage_target_C != temperature_storage_target and timestep_of_hysteresis != timestep:
                    control_signal_heat_pump = 0
                    control_signal_gas_heater = 0
                    control_signal_chp = 0

        self.state.control_signal_gas_heater = control_signal_gas_heater
        self.state.control_signal_chp = control_signal_chp
        self.state.control_signal_heat_pump = control_signal_heat_pump
        stsv.set_output_value(self.control_signal_heat_pump, control_signal_heat_pump)
        stsv.set_output_value(self.control_signal_gas_heater, control_signal_gas_heater)
        stsv.set_output_value(self.control_signal_chp, control_signal_chp)

        return temperature_storage_target_C, \
               timestep_of_hysteresis

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool):
        if force_convergence:
            return
        #######HEAT########
        # If comftortable temperature of building is to low heat with WarmWaterStorage the building
        # Solution with Control Signal Residence
        # not perfect solution!
        '''
        if self.temperature_residence<self.min_comfortable_temperature_residence:


            #heat
            #here has to be added how "strong" HeatingWater Storage can be discharged
            #Working with upper boarder?

        elif self.temperature_residence > self.max_comfortable_temperature_residence:
            #cool
        elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
        '''

        # Logic of regulating HeatDemand:
        # First heat up WarmWaterStorage->more important, than heat up HeatingWater
        # But only one Storage can be heated up in a TimeStep!
        # Simulate WarmWater

        delta_temperature_ww = self.state.temperature_storage_target_ww_C - stsv.get_input_value(
            self.temperature_storage_warm_water)
        delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
            self.temperature_storage_heating_water)

        # Choose which Storage should be heated up
        if delta_temperature_ww >= 0 and delta_temperature_hw >= 0:
            if delta_temperature_hw <= delta_temperature_ww:
                control_signal_choose_storage = 1
            else:
                control_signal_choose_storage = 2
        elif delta_temperature_ww < 0 and delta_temperature_hw < 0:
            if delta_temperature_hw <= delta_temperature_ww:
                control_signal_choose_storage = 1
            else:
                control_signal_choose_storage = 2
        elif delta_temperature_ww <= 0 and delta_temperature_hw >= 0:
            control_signal_choose_storage = 2
        elif delta_temperature_ww >= 0 and delta_temperature_hw <= 0:
            control_signal_choose_storage = 1

        # Heats up storage
        if self.temperature_storage_heating_water.SourceOutput is None:
            control_signal_choose_storage = 0
        if control_signal_choose_storage == 1:
            self.state.temperature_storage_target_ww_C, self.state.timestep_of_hysteresis_ww = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_ww,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_warm_water),
                temperature_storage_target=self.temperature_storage_target_warm_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_ww,
                temperature_storage_target_C=self.state.temperature_storage_target_ww_C,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_ww)
        elif control_signal_choose_storage == 2:
            delta_temperature_hw = self.state.temperature_storage_target_hw_C - stsv.get_input_value(
                self.temperature_storage_heating_water)
            self.state.temperature_storage_target_hw_C, self.state.timestep_of_hysteresis_hw = self.simulate_storage(
                stsv=stsv,
                delta_temperature=delta_temperature_hw,
                timestep=timestep,
                temperature_storage=stsv.get_input_value(self.temperature_storage_heating_water),
                temperature_storage_target=self.temperature_storage_target_heating_water,
                temperature_storage_target_hysteresis=self.temperature_storage_target_hysteresis_hw,
                temperature_storage_target_C=self.state.temperature_storage_target_hw_C,
                timestep_of_hysteresis=self.state.timestep_of_hysteresis_hw)

        stsv.set_output_value(self.control_signal_choose_storage, control_signal_choose_storage)
