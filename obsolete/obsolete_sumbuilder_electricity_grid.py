# sumbuilder grid classes

from dataclasses import dataclass
from typing import Any, List, Optional

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import Component
from hisim.simulationparameters import SimulationParameters

@dataclass_json
@dataclass
class ElectricityGridConfig(cp.ConfigBase):

    """Electricity Grid Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ElectricityGrid.get_full_classname()

    name: str
    grid: Optional[List]
    signal: Optional[str]

    @classmethod
    def get_default_electricity_grid(cls):
        """Gets a default Electricity Grid."""
        return ElectricityGridConfig(
            name="ElectrcityGrid_BaseLoad", grid=None, signal=None
        )


class ElectricityGrid(Component):

    """For calculating the sum towards the grid."""

    operations_available = ["Sum", "Subtract"]
    ElectricityOutput = "ElectricityOutput"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectricityGridConfig,
    ) -> None:
        """Initializes class."""
        self.electricity_grid_config = config
        super().__init__(
            name=self.electricity_grid_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.signal = self.electricity_grid_config.signal
        self.grid = self.electricity_grid_config.grid
        self.operations: List[str] = []
        self.loadtype = lt.LoadTypes.ELECTRICITY
        self.unit = lt.Units.WATT

        if self.grid is not None:
            self.connect_all(self.grid)

        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description=f"here a description for {self.ElectricityOutput} will follow.",
        )

    def add_numbered_input(self) -> cp.ComponentInput:
        """Adds numbered input."""
        num_inputs = len(self.inputs)
        label = f"Input{num_inputs + 1}"
        vars(self)[label] = label
        myinput = cp.ComponentInput(
            self.component_name, label, self.loadtype, self.unit, True
        )
        self.inputs.append(myinput)
        return myinput

    def __add__(self, other_electricity_grid: Any) -> Any:
        """Adds values."""
        cfg = ElectricityGridConfig(
            name=f"{self.component_name}Sum{other_electricity_grid.component_name}",
            grid=[self, "Sum", other_electricity_grid],
            signal=None,
        )
        return ElectricityGrid(self.my_simulation_parameters, cfg)

    def __sub__(self, other_electricity_grid: Any) -> Any:
        """Substracts values."""
        cfg = ElectricityGridConfig(
            name=f"{self.component_name}Subtract{other_electricity_grid.component_name}",
            grid=[self, "Subtract", other_electricity_grid],
            signal=None,
        )
        return ElectricityGrid(
            my_simulation_parameters=self.my_simulation_parameters, config=cfg
        )

    def write_to_report(self) -> List[str]:
        """Writes to report."""
        return self.electricity_grid_config.get_string_dict()

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def connect_electricity_input(self, component: Component) -> None:
        """Connects an electricity input."""
        if hasattr(component, "ElectricityOutput") is False:
            raise Exception("Component does not contain electricity output.")
        next_input = self.add_numbered_input()
        next_input.src_object_name = component.component_name
        next_input.src_field_name = component.ElectricityOutput  # type: ignore

    def add_operation(self, operation: str) -> Any:
        """Adds operation."""
        num_operations = len(self.operations)
        num_inputs = len(self.inputs)
        if num_inputs == num_operations + 1:
            if operation in self.operations_available:
                self.operations.append(operation)
            else:
                raise Exception("Operation not implemented!")
        elif num_inputs >= num_operations + 1:
            raise Exception(
                f"Inputs connected without operation! {num_inputs - (num_operations + 1)} operations are missing!"
            )
        else:
            raise Exception(
                f"Inputs connected without operation! {(num_operations + 1) - num_inputs} operations are missing!"
            )
        return operation

    def connect_all(self, list_of_operations: Any) -> None:
        """Connect all inputs."""
        if isinstance(list_of_operations, list) is False:
            raise Exception("Input has to be a list!")
        if len(list_of_operations) % 2 == 0:
            raise Exception("List of operations is incomplete!")

        for index, element in enumerate(list_of_operations):
            if index % 2 == 0:
                self.connect_electricity_input(element)
            else:
                self.add_operation(element)

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Double checks the results."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Calculates electricity grid interface."""
        total: float = 0
        for index, input_channel in enumerate(self.inputs):
            val1 = stsv.get_input_value(input_channel)
            if index == 0:
                total = val1
            elif self.operations[index - 1] == "Sum":
                total = total + val1
            elif self.operations[index - 1] == "Subtract":
                total = total - val1
            else:
                raise Exception("Operation invalid!")
        if self.signal == "Positive":
            total = max(0, total)
        if self.signal == "Negative":
            total = min(0, total)
        stsv.set_output_value(self.electricity_output_channel, total)
