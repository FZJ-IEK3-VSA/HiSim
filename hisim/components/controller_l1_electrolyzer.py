"""Controller of the generic_electrolyzer.

The controller looks at the available surplus electricity and passes the signal to the electrolyzer accordingly.
In addition the controller takes care of minimum operation and indle times and the available capacity in the hydrogen storage.
"""
# clean
from dataclasses import dataclass
from typing import List
from dataclasses_json import dataclass_json

from hisim import utils
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_hydrogen_storage
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class L1ElectrolyzerControllerConfig(cp.ConfigBase):

    """Electrolyzer Controller Config."""

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    # minimal operation time of heat source
    min_operation_time_in_seconds: int
    # minimal resting time of heat source
    min_idle_time_in_seconds: int
    #: minimal electrical power of the electrolyzer
    p_min_electrolyzer: float
    #: maximal allowed content of hydrogen storage for turning the electrolyzer on
    h2_soc_threshold: float

    @staticmethod
    def get_default_config() -> "L1ElectrolyzerControllerConfig":
        """Returns the default configuration of an electrolyzer controller."""
        config = L1ElectrolyzerControllerConfig(
            name="L1 Electrolyzer Controller",
            source_weight=1,
            min_operation_time_in_seconds=14400,
            min_idle_time_in_seconds=7200,
            p_min_electrolyzer=1200,
            h2_soc_threshold=96,
        )
        return config


class L1ElectrolyzerControllerState:

    """Data class for saving the state of the electrolyzer controller."""

    def __init__(
        self,
        state: int = 0,
        activation_time_step: int = 0,
        deactivation_time_step: int = 0,
    ):
        """Initialize the class."""
        self.state: int = state
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step

    def clone(self) -> "L1ElectrolyzerControllerState":
        """Clone function."""
        return L1ElectrolyzerControllerState(
            state=self.state,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
        )

    def activate(self, timestep: int) -> None:
        """Activates the heat pump and remembers the time step."""
        if self.state == 0:
            self.activation_time_step = timestep
        self.state = 1

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        if self.state == 1:
            self.deactivation_time_step = timestep
        self.state = 0


class L1GenericElectrolyzerController(cp.Component):

    """Controller of the Electrolyzer.

    It takes the available surplus electricity of the energy management system and passes it to the electrolyzer.
    If either the available surplus is too low, or the availabel capacity of the hydrogen storage is below the indicated threshold,
    the electricity surplus signal is modified accordingly.

    Components to connect to:
    (1) energy management system (controller_l2_energy_management_system)
    (2) hydrogen storage (generic_h2storage)
    """

    # Inputs
    ElectricityTarget = "ElectricityTarget"
    HydrogenSOC = "HydrogenSOC"

    # Outputs
    AvailableElectricity = "AvailableElectricity"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: L1ElectrolyzerControllerConfig,
    ) -> None:
        """Initialize the class."""

        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.minimum_runtime_in_timesteps = int(
            config.min_operation_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            config.min_idle_time_in_seconds
            / self.my_simulation_parameters.seconds_per_timestep
        )

        self.state = L1ElectrolyzerControllerState()
        self.previous_state = L1ElectrolyzerControllerState()
        self.processed_state = L1ElectrolyzerControllerState()

        # add inputs
        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )
        self.hydrogen_soc_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenSOC,
            lt.LoadTypes.HYDROGEN,
            lt.Units.PERCENT,
            mandatory=True,
        )
        # add outputs
        self.available_electicity_output_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.AvailableElectricity,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Available Electricity for Electrolyzer from Electrolyzer Controller.",
        )

        self.add_default_connections(self.get_default_connections_from_h2_storage())

    def get_default_connections_from_h2_storage(self):
        """Sets default connections for the hydrogen storage in the electrolyzer controller."""
        log.information(
            "setting hydrogen storage default connections in Electrolyzer Controller"
        )
        connections = []
        h2_storage_classname = (
            generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1GenericElectrolyzerController.HydrogenSOC,
                h2_storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restore the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""

        if force_convergence:
            electricity_target = stsv.get_input_value(self.electricity_target_channel)
            self.state = self.processed_state
        else:
            electricity_target = stsv.get_input_value(self.electricity_target_channel)
            h2_soc = stsv.get_input_value(self.hydrogen_soc_channel)
            self.calculate_state(timestep, electricity_target, h2_soc)
            self.processed_state = self.state.clone()

        # minimum power of electrolyzer fulfilled when running
        if (
            self.state.state == 1
            and electricity_target < self.config.p_min_electrolyzer
        ):
            electricity_target = self.config.p_min_electrolyzer
        stsv.set_output_value(
            self.available_electicity_output_channel,
            self.state.state * electricity_target,
        )

    def calculate_state(
        self, timestep: int, electricity_target: float, h2_soc: float
    ) -> None:
        """Calculate the state."""
        # return device on if minimum operation time is not fulfilled and device was on in previous state
        if (
            self.state.state == 1
            and self.state.activation_time_step + self.minimum_runtime_in_timesteps
            >= timestep
        ):
            # mandatory on, minimum runtime not reached
            return
        if (
            self.state.state == 0
            and self.state.deactivation_time_step
            + self.minimum_resting_time_in_timesteps
            >= timestep
        ):
            # mandatory off, minimum resting time not reached
            return

        # available electricity too low or hydrogen storage too full
        if (electricity_target < self.config.p_min_electrolyzer) or (
            h2_soc > self.config.h2_soc_threshold
        ):
            self.state.deactivate(timestep)
            return

        # turns on if electricity is high enough and there is still space in storage
        self.state.activate(timestep)

    def write_to_report(self) -> List[str]:
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
