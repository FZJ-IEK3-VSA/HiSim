"""Controller L1 for the fuel cell."""

# clean
import os
from typing import List, Any
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.component import (
    ConfigBase,
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
)

from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Franz Oldopp"
__email__ = "f.oldopp@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class FuelCellControllerConfig(ConfigBase):

    """Configutation of the Fuel Cell Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return FuelCellController.get_full_classname()

    name: str
    nom_output: float
    min_output: float
    max_output: float
    standby_load: float
    warm_start_time: float
    cold_start_time: float
    # standby_load: float
    # control_strategy_deactivation: str <-- 'standby' or 'off'

    @classmethod
    def get_default_fuel_cell_controller_config(
        cls,
    ) -> Any:
        """Get a default electrolyzer controller config."""
        config = FuelCellControllerConfig(
            name="Default fuel cell controller",
            nom_output=100.0,
            min_output=10.0,
            max_output=110.0,
            standby_load=10.0,
            warm_start_time=70.0,
            cold_start_time=1800.0,
        )
        return config

    @staticmethod
    def read_config(fuel_cell_name):
        """Opens the according JSON-file, based on the fuel_cell_name."""

        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "fuel_cell_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def control_fuel_cell(cls, fuel_cell_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(fuel_cell_name)

        config = FuelCellControllerConfig(
            name="Fuel Cell Controller",  # config_json.get("name", "")
            nom_output=config_json.get("nom_output", 0.0),
            min_output=config_json.get("min_output", 0.0),
            max_output=config_json.get("max_output", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            warm_start_time=config_json.get("warm_start_time", 0.0),
            cold_start_time=config_json.get("cold_start_time", 0.0),
        )
        return config


class FuelCellController(Component):

    """Fuel Cell Controller class."""

    # Inputs
    DemandProfile = "DemandProfile"

    # Outputs
    CurrentMode = "CurrentMode"
    PowerTarger = "PowerTarger"
    ShutdownCount = "ShutdownCount"
    StandbyCount = "StandbyCount"
    PowerNotProvided = "PowerNotProvided"
    OffCount = "OffCount"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: FuelCellControllerConfig,
    ) -> None:
        """Initialize the class."""
        self.controllerconfig = config

        self.nom_output = config.nom_output
        self.min_output = config.min_output
        self.max_output = config.max_output
        self.standby_load = config.standby_load
        self.warm_start_time = config.warm_start_time
        self.cold_start_time = config.cold_start_time
        self.curtailed_load_count = 0

        super().__init__(
            name=self.controllerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels

        # Getting the load input
        self.demand_profile: ComponentInput = self.add_input(
            self.component_name,
            FuelCellController.DemandProfile,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,  # KILOWATT
            True,
        )

        # =================================================================================================================================
        # Output channels

        self.power_target: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.PowerTarger,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Power to be generated",
        )

        self.current_mode_fuel_cell: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.CurrentMode,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="current mode of fuel cell",
        )

        self.shut_down_count: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.ShutdownCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            output_description="Counts the shut down cycles",
        )

        self.standby: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.StandbyCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            output_description="Counts the standby cycles",
        )

        self.power_not_provided: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.PowerNotProvided,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Sums up the power which can not be provided from the system",
        )

        self.total_off_count: ComponentOutput = self.add_output(
            self.component_name,
            FuelCellController.OffCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.ANY,
            output_description="Total count of switching off",
        )

        # =================================================================================================================================
        # Initialize variables

        self.standby_count = 0.0
        self.current_state = "OFF"  # standby
        self.power_not_provided_count = 0.0
        self.off_count = 0.0
        self.activation_runtime = 0.0

        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.power_not_provided_count_previous = self.power_not_provided_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime

    def load_check(self, current_demand, min_output, max_output, standby_load):
        """Make a load check."""

        if current_demand > max_output:
            current_demand_to_system = max_output
            self.power_not_provided_count += current_demand - max_output
            state = "ON"

        elif min_output <= current_demand <= max_output:
            current_demand_to_system = current_demand
            self.power_not_provided_count += 0.0
            state = "ON"

        elif standby_load <= current_demand < min_output:
            current_demand_to_system = standby_load
            self.power_not_provided_count += current_demand - standby_load
            state = "STANDBY"

        else:
            current_demand_to_system = 0.0
            self.power_not_provided_count += current_demand
            state = "OFF"

        return current_demand_to_system, state, self.power_not_provided_count

    def state_check(self, target_state, cold_start_time_to_min, warm_start_time_to_min):
        """Make a state check."""

        if target_state == "OFF":
            # System switches OFF
            if self.current_state == "ON":
                self.current_state = "Switching OFF"
                self.off_count += 1
            else:
                self.current_state = "OFF"

        elif target_state == "STANDBY":
            # System switches STANDY
            if self.current_state in ("OFF", "StartingfromOFF"):
                self.current_state = "OFF"
            if self.current_state == "ON":
                self.current_state = "Switching STANDBY"
                self.standby_count += 1
            else:
                self.current_state = "STANDBY"

        else:
            # Test start
            if self.current_state in ["Starting from OFF", "Starting from STANDBY"]:
                # pdb.set_trace()
                if (
                    self.activation_runtime
                    <= self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.current_state = "Starting to min"
                    # pdb.set_trace()
                else:
                    self.activation_runtime -= (
                        self.my_simulation_parameters.seconds_per_timestep
                    )
                    # pdb.set_trace()
                    # self.current_state = self.current_state
                    # starting to min auch unten aufnehmen um so min_load zu verteilen. "Starting to min" kann verwendet werden,
                    # da wenn wir wir durch die else: bedingungen da wieder raus kommen (theoretisch ;))

            # Test end
            elif self.current_state == "OFF":
                self.current_state = "Starting from OFF"
                self.activation_runtime = cold_start_time_to_min
            elif self.current_state == "STANDBY":
                self.current_state = "Starting from STANDBY"
                self.activation_runtime = warm_start_time_to_min
            else:
                if (
                    self.activation_runtime
                    > self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.activation_runtime -= (
                        self.my_simulation_parameters.seconds_per_timestep
                    )
                    # self.current_state = self.current_state
                # elif self.activation_runtime <= self.my_simulation_parameters.seconds_per_timestep:
                #    self.activation_runtime -= self.my_simulation_parameters.seconds_per_timestep
                #    self.current_state = self.current_state
                # elif self.activation_runtime <= 0.0:
                else:
                    self.activation_runtime = 0.0
                    self.current_state = "ON"

        return self.current_state, self.activation_runtime

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.power_not_provided_count_previous = self.power_not_provided_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.standby_count = self.standby_count_previous
        self.current_state = self.current_state_previous
        self.power_not_provided_count = self.power_not_provided_count_previous
        self.off_count = self.off_count_previous
        self.activation_runtime = self.activation_runtime_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        # current_demand = stsv.get_input_value(self.demand_profile)
        """
        self.nom_output = config.nom_output
        self.min_output = config.min_output
        self.max_output = config.max_output
        self.warm_start_time = config.warm_start_time
        self.cold_start_time = config.cold_start_time
        """
        warm_start_time_to_min = self.warm_start_time * (
            self.min_output / self.nom_output
        )
        cold_start_time_to_min = self.cold_start_time * (
            self.min_output / self.nom_output
        )

        (current_load_to_system, state, self.curtailed_load_count) = self.load_check(
            (abs(stsv.get_input_value(self.demand_profile))),
            self.min_output,
            self.max_output,
            self.standby_load,
        )  # change standby time
        # pdb.set_trace()
        (self.current_state, self.activation_runtime) = self.state_check(
            state, cold_start_time_to_min, warm_start_time_to_min
        )

        # pdb.set_trace
        # print("self.current_state: ", self.current_state)
        if self.current_state in ["OFF", "Starting from OFF", "Switching OFF"]:
            stsv.set_output_value(self.power_target, 0.0)
            stsv.set_output_value(self.current_mode_fuel_cell, -1)
        elif self.current_state in [
            "STANDBY",
            "Starting from STANDBY",
            "Switching STANDBY",
        ]:
            stsv.set_output_value(self.power_target, self.standby_load)
            stsv.set_output_value(self.current_mode_fuel_cell, 0)
        elif self.current_state == "Starting to min":
            # pdb.set_trace()
            stsv.set_output_value(self.power_target, self.min_output)
            stsv.set_output_value(self.current_mode_fuel_cell, 1)

        else:
            stsv.set_output_value(self.power_target, current_load_to_system)
            stsv.set_output_value(self.current_mode_fuel_cell, 1)

        stsv.set_output_value(self.power_not_provided, self.power_not_provided_count)
        stsv.set_output_value(self.total_off_count, self.off_count)
        stsv.set_output_value(self.standby, self.standby_count)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        lines.append("Controller: " + self.component_name)
        return lines
