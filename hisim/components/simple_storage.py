# Generic/Built-in
import copy

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt

class SimpleStorageState:
    def __init__(self, min_val: float, max_val: float):
        self.fill:float = 0
        self.max_val:float = max_val
        self.min_val:float = min_val

    def store(self, val: float) -> float:
        if self.fill + val < self.max_val:
            # fits completely
            self.fill += val
            return val
        if self.fill >= self.max_val:
            # full
            return 0
        if self.fill < self.max_val:
            # fits partially
            amount = self.max_val - self.fill
            self.fill += amount
            return amount
        raise Exception("forgotten case")

    def withdraw(self, val: float) -> float:
        if self.fill > val:
            # has enough
            self.fill -= val
            return val
        if self.fill <= self.min_val:
            # empty
            return 0
        if self.fill < val:
            # fits partially
            amount = self.fill
            self.fill = 0
            return amount
        raise Exception("forgotten case")


class SimpleStorage(Component):
    ChargingAmount = "Charging Amount"
    DischargingAmount = "Discharging Amount"
    ActualStorageDelta = "Actual Storage Delta"
    CurrentFillLevel = "Current Fill Level Absolute"
    CurrentFillLevelPercent = "Current Fill Level Percent"

    def __init__(self, component_name, loadtype: lt.LoadTypes, unit: lt.Units, capacity: float):
        super().__init__(component_name)
        self.charging_input: ComponentInput = self.add_input(self.ComponentName, SimpleStorage.ChargingAmount,
                                                             loadtype, unit, True)
        self.discharging_input: ComponentInput = self.add_input(self.ComponentName, SimpleStorage.DischargingAmount,
                                                                loadtype, unit, True)
        self.actual_delta: ComponentOutput = self.add_output(self.ComponentName, SimpleStorage.ActualStorageDelta,
                                                             loadtype, unit)
        self.current_fill: ComponentOutput = self.add_output(self.ComponentName, SimpleStorage.CurrentFillLevel,
                                                             loadtype, unit)
        self.current_fill_percent: ComponentOutput = self.add_output(self.ComponentName, SimpleStorage.CurrentFillLevelPercent,
                                                                     loadtype, lt.Units.Percent)
        self.state = SimpleStorageState(0, capacity)
        self.capacity = capacity
        self.previous_state = copy.copy(self.state)

    def i_save_state(self):
        self.previous_state = copy.copy(self.state)

    def i_restore_state(self):
        self.state = copy.copy(self.previous_state)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        charging = stsv.get_input_value(self.charging_input)
        discharging = stsv.get_input_value(self.discharging_input)
        if charging < 0:
            raise Exception("trying to charge with negative amount" + str(charging))
        if discharging > 0:
            raise Exception("trying to discharge with positive amount: " + str(discharging))
        charging_delta = self.state.store(charging)
        discharging_delta = self.state.withdraw(discharging * -1) * -1
        actual_delta = charging_delta + discharging_delta
        stsv.set_output_value(self.actual_delta, actual_delta)
        stsv.set_output_value(self.current_fill, self.state.fill)
        percent_fill = self.state.fill / self.capacity
        stsv.set_output_value(self.current_fill_percent, percent_fill)
