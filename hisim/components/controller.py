# Generic/Built-in

# Owned
import copy
import numpy as np
import component as cp
import loadtypes as lt


# idead of the class. Save ControlSignal of Componentes in lasttimestep
# on basis of ControlSignals of last timestept, rule ControlSignals of futre timesteps

# class ControllerState:
#    def __init__(self):
class ControllerState:
    def __init__(self, control_signal_gas_heater: float, control_signal_chp: float, control_signal_heat_pump: int):
        self.control_signal_gas_heater = control_signal_gas_heater
        self.control_signal_chp = control_signal_chp
        self.control_signal_heat_pump = control_signal_heat_pump

class Controller(cp.Component):
    #Inputs
    ElectricityConsumptionBuilding="ElectricityConsumptionBuilding"
    StorageTemperature = "StorageTemperature"

    ElectricityOutputPvs = "ElectricityOutputPvs"
    ElectricityToOrFromBatteryReal = "ElectricityToOrFromBatteryReal"
    ElectricityToElectrolyzerReal = "ElectricityToElectrolyzerReal"
    ElectricityFromCHPReal = "ElectricityFromCHPReal"

    # Outputs
    ElectricityToElectrolyzerTarget="ElectricityToElectrolyzerTarget"
    ElectricityToOrFromBatteryTarget="ElectricityToOrFromBatteryTarget"
    ElectricityFromCHPTarget="ElectricityFromCHPTarget"
    ElectricityToOrFromGrid="ElectricityToOrFromGrid"

    ControlSignalGasHeater="ControlSignalGasHeater"
    ControlSignalChp="ControlSignalChp"
    ControlSignalHeatPump="ControlSignalHeatPump"

    def __init__(self,
                 sim_params=None):
        super().__init__("Controller")
        self.state = ControllerState(control_signal_heat_pump=0,control_signal_gas_heater=0, control_signal_chp=0)
        self.previous_state = copy.copy(self.state)

        ###Inputs

        self.temperature_storage: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                     self.StorageTemperature,
                                                                     lt.LoadTypes.Water,
                                                                     lt.Units.Celsius,
                                                                     False)


        self.electricity_consumption_building: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                                  self.ElectricityConsumptionBuilding,
                                                                                  lt.LoadTypes.Electricity,
                                                                                  lt.Units.Watt,
                                                                                  False)

        self.electricity_output_pvs: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                        self.ElectricityOutputPvs,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt,
                                                                        False)

        self.electricity_to_or_from_battery_real: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                              self.ElectricityToOrFromBatteryReal,
                                                                              lt.LoadTypes.Electricity,
                                                                              lt.Units.Watt,
                                                                              False)
        self.electricity_to_electrolyzer_real: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                              self.ElectricityToElectrolyzerReal,
                                                                              lt.LoadTypes.Electricity,
                                                                              lt.Units.Watt,
                                                                              False)
        self.electricity_from_chp_target: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                              self.ElectricityFromCHPReal,
                                                                              lt.LoadTypes.Electricity,
                                                                              lt.Units.Watt,
                                                                              False)

        # Outputs

        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                       self.ElectricityToOrFromGrid,
                                                                       lt.LoadTypes.Electricity,
                                                                       lt.Units.Watt,
                                                                       False)
        self.electricity_from_chp_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ElectricityFromCHPTarget,
                                                                         lt.LoadTypes.Electricity,
                                                                         lt.Units.Watt,
                                                                         False)
        self.electricity_to_electrolyzer_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ElectricityToElectrolyzerTarget,
                                                                         lt.LoadTypes.Electricity,
                                                                         lt.Units.Watt,
                                                                         False)
        self.electricity_to_or_from_battery_target: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ElectricityToOrFromBatteryTarget,
                                                                         lt.LoadTypes.Electricity,
                                                                         lt.Units.Watt,
                                                                         False)
        self.control_signal_gas_heater: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ControlSignalGasHeater,
                                                                         lt.LoadTypes.Any,
                                                                         lt.Units.Percent,
                                                                         False)
        self.control_signal_chp: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ControlSignalChp,
                                                                         lt.LoadTypes.Any,
                                                                         lt.Units.Percent,
                                                                         False)
        self.control_signal_heat_pump: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ControlSignalHeatPump,
                                                                         lt.LoadTypes.Any,
                                                                         lt.Units.Percent,
                                                                         False)


    def build(self, mode):
        self.mode = mode

    def write_to_report(self):
        pass

    def i_save_state(self):
        pass
        self.previous_state = self.state

    def i_restore_state(self):
        pass
        self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def control_battery_raise_own_consumption(self, electricity_input: int):
        # Input=electricity_to_or_from_battery_target

        electricity_to_or_from_battery_target = electricity_input

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues,seconds_per_timestep: int, force_convergence: bool):
        # @Vitor: Was passiert mit Output-Werten die nicht gesetzt werden?

        ###ELECTRICITY
        electricity_to_or_from_battery_target = 0
        electricity_to_electrolyzer_target = 0
        electricity_from_chp_target = 0
        electricity_to_or_from_grid = 0

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        delta_demand = stsv.get_input_value(self.electricity_output_pvs) - stsv.get_input_value(self.electricity_consumption_building)

        # Check if Battery is Component of Simulation
        if self.electricity_to_or_from_battery_real.SourceOutput is not None:
            electricity_to_or_from_battery_target = delta_demand

        # electricity_not_used_battery of Charge or Discharge
        electricity_not_used_battery = electricity_to_or_from_battery_target - stsv.get_input_value(
            self.electricity_to_or_from_battery_real)

        if delta_demand > 0:
            ####Problems here by Calculation? Because won#t calculate Zero if delta_demand=electricity_to_or_from_battery_target, instead calculate 0.0000010
            # Check if enough electricity is there to charge CHP (finds real solution after 2 Iteration-Steps)
            if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery > 0 and self.electricity_to_electrolyzer_real.SourceOutput is not None:
                # possibility to  produce H2
                electricity_to_electrolyzer_target = delta_demand - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)

            # Negative sign, because Electricity will flow into grid->Production of Electricity
            electricity_to_or_from_grid = -delta_demand + stsv.get_input_value(
                self.electricity_to_or_from_battery_real) + stsv.get_input_value(self.electricity_to_electrolyzer_real)

        elif delta_demand < 0:

            if delta_demand - electricity_to_or_from_battery_target + electricity_not_used_battery < 0 and self.electricity_to_or_from_chp_real.SourceOutput is not None:
                electricity_from_chp_target = delta_demand - stsv.get_input_value(
                    self.electricity_to_or_from_battery_real)

            # Positive sing, because Electricity will flow out of grid->Consumption of Electricity
            electricity_to_or_from_grid = -delta_demand - stsv.get_input_value(
                self.electricity_to_or_from_battery_real) - stsv.get_input_value(self.electricity_from_chp_real)

        # else:
        # nothing happens
        # Delta demnand =0

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(self.electricity_from_chp_target, electricity_from_chp_target)
        stsv.set_output_value(self.electricity_to_electrolyzer_target, electricity_to_electrolyzer_target)
        stsv.set_output_value(self.electricity_to_or_from_battery_target, electricity_to_or_from_battery_target)

        # Heat Pump can't bet controlled recording to Tjarko.
        # So Gas_Heater and CHP can heat up Storage, if Heat Pump didn't heat up enough
        # What to do with too much heat?
        # Idea of 2-Punkt-Regelung mit Hysterese
        temperature_storage_target = 50  # festzulegen
        control_signal_chp = 0
        control_signal_gas_heater = 0
        control_signal_heat_pump= 0
        delta_temperature = temperature_storage_target - stsv.get_input_value(self.temperature_storage)

        # Idea: Storage berechnet Bedarf an Wärme der benötigt wird um +5 Grad Celsius von heating and warm zu erreichen

        # WarmWaterStorage
        if stsv.get_input_value(self.temperature_storage) > 0:
            if delta_temperature >= 10:
                control_signal_heat_pump = 1
                control_signal_chp = 1
                control_signal_gas_heater = 1
            elif delta_temperature > 5 and delta_temperature < 10:
                # heat storage
                # look at state of signal of heating componentens
                # if signal was above zero put on more heating systems
                control_signal_heat_pump=1
                if self.state.control_signal_chp < 1:
                    control_signal_chp = 1
                    control_signal_gas_heater = 0.5
                elif self.state.control_signal_chp == 1:
                    control_signal_gas_heater = 1
            elif delta_temperature > 0 and delta_temperature <= 5:
                control_signal_heat_pump = 1
                if self.state.control_signal_chp < 1:
                    control_signal_chp = 1
                elif self.state.control_signal_chp == 1:
                    control_signal_gas_heater = 0.5
                # Storage warm enough. Try to turn off Heaters
            elif delta_temperature <= 0:
                control_signal_heat_pump = 0
                control_signal_gas_heater = 0
                control_signal_chp = 0

        # HeatStorage

        self.state.control_signal_gas_heater = control_signal_gas_heater
        self.state.control_signal_chp = control_signal_chp
        self.state.control_signal_chp = control_signal_heat_pump
        stsv.set_output_value(self.control_signal_heat_pump, control_signal_heat_pump)
        stsv.set_output_value(self.control_signal_gas_heater, control_signal_gas_heater)
        stsv.set_output_value(self.control_signal_chp, control_signal_chp)

