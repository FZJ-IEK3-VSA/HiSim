"""Example Storage."""

# clean

# Generic/Built-in
import copy
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import (
    Component,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim.component import ConfigBase


class ExampleStorageState:

    """A class to simulate the Example Storage State."""

    def __init__(self, min_val: float, max_val: float) -> None:
        """Constructs all the neccessary attributes for the ExampleStorage object."""

        self.fill: float = 0
        self.max_val: float = max_val
        self.min_val: float = min_val

    def store(self, val: float) -> float:
        """Returns how much is put in the storage."""

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
        raise ValueError("forgotten case")

    def withdraw(self, val: float) -> float:
        """Returns how much is taken out of the storage."""

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
        raise ValueError("forgotten case")


@dataclass_json
@dataclass
class SimpleStorageConfig(ConfigBase):

    """Configuration of the Simple Storage."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SimpleStorage.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    loadtype: lt.LoadTypes
    unit: lt.Units
    capacity: float

    @classmethod
    def get_default_thermal_storage(cls):
        """Gets a default Simple Storage."""
        return SimpleStorageConfig(
            name="Simple Thermal Storage",
            loadtype=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.KWH,
            capacity=50,
        )


class SimpleStorage(Component):

    """A class to simulate the Simple Storage."""

    ChargingAmount = "Charging Amount"
    DischargingAmount = "Discharging Amount"
    ActualStorageDelta = "Actual Storage Delta"
    CurrentFillLevel = "Current Fill Level Absolute"
    CurrentFillLevelPercent = "Current Fill Level Percent"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleStorageConfig,
    ) -> None:
        """Constructs all the neccessary attributes for the SimpleStorage object."""
        self.simplestorageconfig = config
        super().__init__(
            self.simplestorageconfig.name,
            my_simulation_parameters=my_simulation_parameters,
        )
        # Initialized variables
        self.state = ExampleStorageState(0, self.simplestorageconfig.capacity)
        self.capacity = self.simplestorageconfig.capacity
        self.previous_state = copy.copy(self.state)

        self.charging_input: ComponentInput = self.add_input(
            self.simplestorageconfig.name,
            SimpleStorage.ChargingAmount,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            True,
        )
        self.discharging_input: ComponentInput = self.add_input(
            self.simplestorageconfig.name,
            SimpleStorage.DischargingAmount,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            True,
        )
        self.actual_delta: ComponentOutput = self.add_output(
            self.simplestorageconfig.name,
            SimpleStorage.ActualStorageDelta,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            output_description="Actual Storage Delta"
        )
        self.current_fill: ComponentOutput = self.add_output(
            self.simplestorageconfig.name,
            SimpleStorage.CurrentFillLevel,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            output_description="Current Fill Level"
        )
        self.current_fill_percent: ComponentOutput = self.add_output(
            self.simplestorageconfig.name,
            SimpleStorage.CurrentFillLevelPercent,
            self.simplestorageconfig.loadtype,
            lt.Units.PERCENT,
            output_description="Current Fill Level in Percent"
        )

    def i_save_state(self) -> None:
        """Saves the current state of the storage."""
        self.previous_state = copy.copy(self.state)

    def i_restore_state(self) -> None:
        """Restores the previous state of the storage."""
        self.state = copy.copy(self.previous_state)

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates the storage."""

        charging = stsv.get_input_value(self.charging_input)
        discharging = stsv.get_input_value(self.discharging_input)
        if charging < 0:
            raise ValueError("trying to charge with negative amount" + str(charging))
        if discharging > 0:
            raise ValueError(
                "trying to discharge with positive amount: " + str(discharging)
            )
        charging_delta = self.state.store(charging)
        discharging_delta = self.state.withdraw(discharging * -1) * -1
        actual_delta = charging_delta + discharging_delta
        stsv.set_output_value(self.actual_delta, actual_delta)
        stsv.set_output_value(self.current_fill, self.state.fill)
        percent_fill = self.state.fill / self.capacity
        stsv.set_output_value(self.current_fill_percent, percent_fill)
