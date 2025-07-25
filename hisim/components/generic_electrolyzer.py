"""Simple implementation of an electrolyzer.

The electrolyzer can moderate in a certain range,but the efficiency changes only
linarly. Recovery heat is not considered so far.
"""

from dataclasses import dataclass
from typing import List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import controller_l1_electrolyzer
from hisim.simulationparameters import SimulationParameters

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = ""


@dataclass_json
@dataclass
class GenericElectrolyzerConfig(cp.ConfigBase):
    """Generic electrolyzer config."""

    building_name: str
    #: name of the electrolyer
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: minimal operating power in Watt (electrical power)
    min_power: float
    #: maximal operating power in Watt (electrical power)
    max_power: float
    #: minimal hydrogen production rate (at minimal electrical power) in kg / s
    min_hydrogen_production_rate: float
    #: maximal hydrogen production rate (at maximal electrical power) in kg / s
    max_hydrogen_production_rate: float

    @staticmethod
    def get_default_config(
        p_el: float,
        building_name: str = "BUI1",
    ) -> "GenericElectrolyzerConfig":
        """Returns the default configuration of an electrolyzer."""
        config = GenericElectrolyzerConfig(
            building_name=building_name,
            name="Electrolyzer",
            source_weight=1,
            min_power=p_el * 0.5,
            max_power=p_el,
            min_hydrogen_production_rate=p_el * (1 / 4) * 8.989 / 3.6e4,
            max_hydrogen_production_rate=p_el * (50 / 24) * 8.989 / 3.6e4,
        )
        return config


class ElectrolyzerState:
    """Saves the state of the electrolyzer."""

    def __init__(self, hydrogen: float = 0, electricity: float = 0):
        """Initialize an instance."""
        self.hydrogen = hydrogen
        self.electricity = electricity

    def clone(self) -> "ElectrolyzerState":
        """Return a second instance of this class with same attributes."""
        return ElectrolyzerState(hydrogen=self.hydrogen, electricity=self.electricity)


class GenericElectrolyzer(cp.Component):
    """Generic electrolyzer component.

    The electrolyzer converts electrical energy [kWh] into hydrogen [kg]. It can
    work in a certain range from x to 100% or be switched off = 0%. The
    conversion rate is given by the supplier and is directly used. Maybe a
    change to efficiency can be made but its just making things more complex
    with no benefit. Between the given values, the values are calculated by an
    interpolation. If the load curve is linear a fixed factor could be
    calculated. Therefore it has an operational state. All the min values and
    all the max values are connected and the electrolyzer can operate between
    them. The waste energy in electolyzers is not used to provide heat for the
    households demand. Output pressure may be used in the future.

    Components to connect to: (1) Electrolyzer controller (controller_l1_electrolyzer).

    """

    # Inputs
    AvailableElectricity = "AvailbaleElectricity"

    # Outputs
    HydrogenOutput = "HydrogenOutput"
    ElectricityOutput = "ElectricityOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericElectrolyzerConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
        """Initialize an instance."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.config = config
        self.state = ElectrolyzerState()
        self.previous_state = ElectrolyzerState()

        # Intputs
        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            GenericElectrolyzer.AvailableElectricity,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        # Outputs
        self.hydrogen_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            GenericElectrolyzer.HydrogenOutput,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Hydrogen output",
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=GenericElectrolyzer.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                lt.ComponentType.ELECTROLYZER,
            ],
            output_description="Electricity Output",
        )
        self.add_default_connections(self.get_default_connections_from_l1_generic_electrolyzer_controller())

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def get_default_connections_from_l1_generic_electrolyzer_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Sets default connections of the controller in the Electroylzer."""

        connections: List[cp.ComponentConnection] = []
        controller_classname = controller_l1_electrolyzer.L1GenericElectrolyzerController.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericElectrolyzer.AvailableElectricity,
                controller_classname,
                controller_l1_electrolyzer.L1GenericElectrolyzerController.AvailableElectricity,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Save state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restore state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        """Simulate."""
        if force_convergence:
            pass

        electricity_target = stsv.get_input_value(self.electricity_target_channel)
        if electricity_target < 0:
            raise ValueError("Target electricity needs to be positive in Electrolyzer")
        if 0 <= electricity_target < self.config.min_power:
            self.state.electricity = 0
            electricity_target = 0
        elif electricity_target > self.config.max_power:
            self.state.electricity = self.config.max_power
        else:
            self.state.electricity = electricity_target

        # interpolation between points
        if electricity_target == 0:
            hydrogen_output_in_kg_per_sec = 0.0
        else:
            hydrogen_output_in_kg_per_sec = self.config.min_hydrogen_production_rate + (
                (self.config.max_hydrogen_production_rate - self.config.min_hydrogen_production_rate)
                * (self.state.electricity - self.config.min_power)
                / (self.config.max_power - self.config.min_power)
            )
        self.state.hydrogen = hydrogen_output_in_kg_per_sec
        stsv.set_output_value(self.hydrogen_output_channel, self.state.hydrogen)
        stsv.set_output_value(self.electricity_output_channel, self.state.electricity)

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
