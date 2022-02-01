# Generic/Built-in
import copy

# Owned
import component as cp
import loadtypes as lt
import utils
from components.ev_charger import SimpleStorageState

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class ControllableState:
    def __init__(self, init_stored_energy=0, max_stored_energy=None, min_stored_energy=None, max_var_stored_energy=None, min_var_stored_energy=None):
        self.stored_energy = init_stored_energy
        self.max_stored_energy = max_stored_energy
        self.min_stored_energy = min_stored_energy
        self.max_var_stored_energy = max_var_stored_energy
        self.min_var_stored_energy = min_var_stored_energy

    def charge(self, energy):
        energy = abs(energy)
        if self.stored_energy + energy < self.max_stored_energy:
            charge = energy
        else:
            charge = self.max_stored_energy - self.stored_energy

        if charge > self.max_var_stored_energy:
            charge = self.max_var_stored_energy

        self.stored_energy = charge + self.stored_energy
        self.chargeWh = charge

    def discharge(self, energy):
        energy = - abs(energy)
        if self.stored_energy + energy > self.min_stored_energy:
            discharge = energy
        else:
            discharge = self.stored_energy - self.min_stored_energy

        if discharge < self.min_var_stored_energy:
            discharge = self.min_var_stored_energy

        self.stored_energy = discharge + self.stored_energy
        self.chargeWh = discharge


class Battery(cp.Component):
    # Imports
    ElectricityInput = "ElectricityInput"
    State = "State"

    # Outputs
    StoredEnergy = "StoredEnergy"
    StateOfCharge = "StateOfCharge"
    ElectricityOutput = "ElectricityOutput"

    def __init__(self,
                 manufacturer="sonnen",
                 model="sonnenBatterie 10 - 11,5 kWh",
                 soc=10/15,
                 base=False,
                 sim_params=None):
        super().__init__("Battery")

        self.build(manufacturer=manufacturer, model=model, base=base, sim_params=sim_params)

        self.state = SimpleStorageState(max_var_val=self.max_var_stored_energy,
                                        min_var_val=self.min_var_stored_energy,
                                        stored_energy=self.max_stored_energy*soc)
        self.previous_state = copy.deepcopy(self.state)

        self.inputC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                      self.ElectricityInput,
                                                      lt.LoadTypes.Electricity,
                                                      lt.Units.Watt,
                                                      True)
        self.stateC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                      self.State,
                                                      lt.LoadTypes.Any,
                                                      lt.Units.Any,
                                                      True)

        self.state_of_chargeC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                          self.StateOfCharge,
                                                          lt.LoadTypes.Any,
                                                          lt.Units.Any)

        self.stored_energyC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                               self.StoredEnergy,
                                                               lt.LoadTypes.Electricity,
                                                               lt.Units.Wh)

        self.electricity_outputC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                               self.ElectricityOutput,
                                                               lt.LoadTypes.Electricity,
                                                               lt.Units.Watt)

    def build(self, manufacturer, model, base, sim_params):
        self.base = base
        self.time_correction_factor = 1 / sim_params.seconds_per_timestep
        self.seconds_per_timestep = sim_params.seconds_per_timestep

        # Gets flexibilities, including heat pump
        battery_database = utils.load_smart_appliance("Battery")

        battery_found = False
        for battery in battery_database:
            if battery["Manufacturer"] == manufacturer and battery["Model"] == model:
                battery_found = True
                break

        if battery_found == False:
            raise Exception("Heat pump model not registered in the database")

        self.max_stored_energy = battery['Capacity'] * 1E3
        self.min_stored_energy = self.max_stored_energy * 0.0
        self.efficiency = battery['Efficiency']
        self.efficiency_inverter = battery['Inverter Efficiency']
        self.max_var_stored_energy = battery['Maximal Charging Power'] * 1E3 * self.time_correction_factor
        if 'Maximal Discharging Power' in battery:
            self.min_var_stored_energy = - battery['Maximal Discharging Power'] * 1E3 * self.time_correction_factor
        else:
            self.min_var_stored_energy = - self.max_var_stored_energy

    def write_to_report(self):
        lines =[]
        lines.append("MaxStoredEnergy: {}".format(self.max_stored_energy))
        return lines

    #def i_save_state(self):
    #    self.previous_state = copy.copy(self.state)

    #def i_restore_state(self):
    #    self.state = copy.copy(self.previous_state)
    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        load = stsv.get_input_value(self.inputC)
        state = stsv.get_input_value(self.stateC)

        load = - load / self.seconds_per_timestep

        capacity = self.state.stored_energy
        max_capacity = self.max_stored_energy
        min_capacity = self.min_stored_energy

        if state == 1:
            charging_delta, after_capacity = self.state.store(max_capacity=max_capacity,
                                                              current_capacity=capacity,
                                                              val=load,
                                                              efficiency=self.efficiency_inverter)
        elif state == -1:
            charging_delta, after_capacity = self.state.withdraw(min_capacity=min_capacity,
                                                                 current_capacity=capacity,
                                                                 val=load,
                                                                 efficiency=self.efficiency_inverter*self.efficiency)
        else:
            charging_delta = 0
            after_capacity = capacity

        stsv.set_output_value(self.state_of_chargeC, after_capacity/max_capacity)
        stsv.set_output_value(self.stored_energyC, after_capacity)
        stsv.set_output_value(self.electricity_outputC, charging_delta)

class BatteryController(cp.Component):
    ElectricityInput = "ElectricityInput"
    State = "State"

    def __init__(self):
        super().__init__(name="BatteryController")

        self.inputC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                      self.ElectricityInput,
                                                      lt.LoadTypes.Electricity,
                                                      lt.Units.Watt,
                                                      True)
        self.stateC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                        self.State,
                                                        lt.LoadTypes.Any,
                                                        lt.Units.Any)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        load = stsv.get_input_value(self.inputC)

        if load < 0.0:
            state = 1
        elif load > 0.0:
            state = - 1
        else:
            state = 0.0

        stsv.set_output_value(self.stateC, state)



