"""This module simlates the energy grid and should replace the GridEnergyBalancer and parts of the energy management system. """
# clean
from dataclasses import dataclass
from typing import Any, List, Optional

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import Component, ConfigBase, ComponentInput, ComponentOutput
from hisim.dynamic_component import DynamicComponent, DynamicConnectionInput, DynamicConnectionOutput
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class GridEnergyBalancerConfig(cp.ConfigBase):

    """Electricity Grid Config."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GridEnergyBalancer.get_full_classname()

    name: str


    @classmethod
    def get_GridEnergyBalancer_default_config(cls):
        """Gets a default GridEnergyBalancer."""
        return GridEnergyBalancerConfig(
            name="GridEnergyBalancer"
        )



class GridEnergyBalancer(DynamicComponent):
    
    """Dynamic electricity grid module.
    
    It calculates the electricity production and consumption dynamically for all components.
    """
    
    # Outputs
    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    
    def __init__(self, my_simulation_parameters: SimulationParameters, config: GridEnergyBalancerConfig):
        """Initialize the component."""
        self.grid_energy_balancer_config = config
        self.name = self.grid_energy_balancer_config.name
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        super().__init__(self.my_component_inputs, self.my_component_outputs, self.name, my_simulation_parameters, my_config=config)
        
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        
        # Outputs
        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToOrFromGrid} will follow.",
        )

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
        )

        
    def write_to_report(self):
        """Writes relevant information to report."""
        return self.grid_energy_balancer_config.get_string_dict()

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks values."""
        pass
    
    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the grid energy balancer."""

        if timestep == 0:

            self.production_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION]
            )
            self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(
                tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
            )

        # ELECTRICITY #

        # get sum of production and consumption for all inputs for each iteration
        production = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.production_inputs
            ]
        )
        consumption_uncontrolled = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.consumption_uncontrolled_inputs
            ]
        )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        electricity_to_or_from_grid = (
            production - consumption_uncontrolled
        )

        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_or_from_grid)
        stsv.set_output_value(
            self.total_electricity_consumption_channel,
            consumption_uncontrolled,
        )
