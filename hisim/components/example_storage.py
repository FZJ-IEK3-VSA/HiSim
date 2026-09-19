"""Example Storage."""

# Generic/Built-in
import copy
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
from hisim.economics.facts import CostRelevance


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
    """Configuration of the example storage: what it holds, in what unit, and how much of it.

    The component is a vessel with two ports and a fill level, indifferent to what it stores, so
    the medium is what distinguishes one instance from another. The one preset it ships::

        SimpleStorageConfig.preset_thermal("SimpleThermalStorage")

    is the 50 kWh warm-water store the example setups use, and ``thermal`` names the medium
    rather than the size: a store of another medium would be another preset, a store of another
    size overrides ``capacity_in_kwh``.
    """

    MAIN_CLASS = "hisim.components.example_storage.SimpleStorage"

    component_id: ComponentID
    #: Physical quantity the two ports and the fill level carry.
    loadtype: lt.LoadTypes = lt.LoadTypes.WARM_WATER
    #: Unit that quantity is in.
    unit: lt.Units = lt.Units.KWH
    #: How much the vessel holds, in the unit above. The fill level is capped at it and the
    #: percentage output is measured against it.
    capacity_in_kwh: float = 50

    @preset
    @classmethod
    def preset_thermal(cls, name: str) -> "SimpleStorageConfig":
        """The 50 kWh warm-water store of the example setups, on every field default.

        Args:
            name: Instance name of the storage in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class SimpleStorage(Component):
    """A class to simulate the Simple Storage."""

    cost_relevance = CostRelevance.FREE_OF_COST

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
