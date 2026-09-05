"""Example Storage."""

# clean

# Generic/Built-in
import copy
from typing import Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt


class ExampleStorageState:
    """A class to simulate the Example Storage State."""

    def __init__(self, min_val_in_kwh: float, max_val_in_kwh: float) -> None:
        """Constructs all the neccessary attributes for the ExampleStorage object."""

        self.fill_in_kwh: float = 0
        self.max_val_in_kwh: float = max_val_in_kwh
        self.min_val_in_kwh: float = min_val_in_kwh

    def store(self, val_in_kwh: float) -> float:
        """Returns how much is put in the storage."""

        if self.fill_in_kwh + val_in_kwh < self.max_val_in_kwh:
            # fits completely
            self.fill_in_kwh += val_in_kwh
            return val_in_kwh
        if self.fill_in_kwh >= self.max_val_in_kwh:
            # full
            return 0
        if self.fill_in_kwh < self.max_val_in_kwh:
            # fits partially
            amount = self.max_val_in_kwh - self.fill_in_kwh
            self.fill_in_kwh += amount
            return amount
        raise ValueError("forgotten case")

    def withdraw(self, val_in_kwh: float) -> float:
        """Returns how much is taken out of the storage."""

        if self.fill_in_kwh > val_in_kwh:
            # has enough
            self.fill_in_kwh -= val_in_kwh
            return val_in_kwh
        if self.fill_in_kwh <= self.min_val_in_kwh:
            # empty
            return 0
        if self.fill_in_kwh < val_in_kwh:
            # fits partially
            amount = self.fill_in_kwh
            self.fill_in_kwh = 0
            return amount
        raise ValueError("forgotten case")


@dataclass_json
@dataclass
class SimpleStorageConfig(ConfigBase):
    """Configuration of the Simple Storage."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return SimpleStorage.get_full_classname()

    component_id: ComponentID
    loadtype: lt.LoadTypes
    unit: lt.Units
    capacity_in_kwh: float

    @classmethod
    def get_default_thermal_storage(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "SimpleStorageConfig":
        """Gets a default Simple Storage."""
        if component_id is None:
            component_id = ComponentID(name="SimpleThermalStorage")
        return SimpleStorageConfig(
            component_id=component_id,
            loadtype=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.KWH,
            capacity_in_kwh=50,
        )


class SimpleStorage(Component):
    """A class to simulate the Simple Storage."""

    ChargingAmount: str = "ChargingAmount"
    DischargingAmount: str = "DischargingAmount"
    ActualStorageDelta: str = "ActualStorageDelta"
    CurrentFillLevel: str = "CurrentFillLevelAbsolute"
    CurrentFillLevelPercent: str = "CurrentFillLevelPercent"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleStorageConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Constructs all the neccessary attributes for the SimpleStorage object."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.simplestorageconfig: SimpleStorageConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: SimpleStorageConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        # Initialized variables
        self.state: ExampleStorageState = ExampleStorageState(0, self.simplestorageconfig.capacity_in_kwh)
        self.capacity_in_kwh: float = self.simplestorageconfig.capacity_in_kwh
        self.previous_state: ExampleStorageState = copy.copy(self.state)

        self.charging_input: ComponentInput = self.add_input(
            self.simplestorageconfig.component_id.name,
            SimpleStorage.ChargingAmount,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            True,
        )
        self.discharging_input: ComponentInput = self.add_input(
            self.simplestorageconfig.component_id.name,
            SimpleStorage.DischargingAmount,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            True,
        )
        self.actual_delta: ComponentOutput = self.add_output(
            self.simplestorageconfig.component_id.name,
            SimpleStorage.ActualStorageDelta,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            output_description="Actual Storage Delta",
        )
        self.current_fill: ComponentOutput = self.add_output(
            self.simplestorageconfig.component_id.name,
            SimpleStorage.CurrentFillLevel,
            self.simplestorageconfig.loadtype,
            self.simplestorageconfig.unit,
            output_description="Current Fill Level",
        )
        self.current_fill_percent: ComponentOutput = self.add_output(
            self.simplestorageconfig.component_id.name,
            SimpleStorage.CurrentFillLevelPercent,
            self.simplestorageconfig.loadtype,
            lt.Units.PERCENT,
            output_description="Current Fill Level in Percent",
        )

    def i_save_state(self) -> None:
        """Saves the current state of the storage."""
        self.previous_state = copy.copy(self.state)

    def i_restore_state(self) -> None:
        """Restores the previous state of the storage."""
        self.state = copy.copy(self.previous_state)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the storage."""

        charging_in_kwh = stsv.get_input_value(self.charging_input)
        discharging_in_kwh = stsv.get_input_value(self.discharging_input)
        if charging_in_kwh < 0:
            raise ValueError("trying to charge with negative amount" + str(charging_in_kwh))
        if discharging_in_kwh > 0:
            raise ValueError("trying to discharge with positive amount: " + str(discharging_in_kwh))
        charging_delta_in_kwh = self.state.store(charging_in_kwh)
        discharging_delta_in_kwh = self.state.withdraw(discharging_in_kwh * -1) * -1
        actual_delta_in_kwh = charging_delta_in_kwh + discharging_delta_in_kwh
        stsv.set_output_value(self.actual_delta, actual_delta_in_kwh)
        stsv.set_output_value(self.current_fill, self.state.fill_in_kwh)
        percent_fill = self.state.fill_in_kwh / self.capacity_in_kwh
        stsv.set_output_value(self.current_fill_percent, percent_fill)
