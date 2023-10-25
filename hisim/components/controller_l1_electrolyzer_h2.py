""" Controller for the generic_electrolyzer_h2 component. """
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
from hisim import log

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "MIT"
__version__ = "0.5"
__maintainer__ = "Franz Oldopp"
__email__ = "f.oldopp@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class ElectrolyzerControllerConfig(ConfigBase):

    """Configutation of the Simple Electrolyzer Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ElectrolyzerController.get_full_classname()

    name: str
    nom_load: float
    min_load: float
    max_load: float
    standby_load: float
    warm_start_time: float
    cold_start_time: float

    @classmethod
    def get_default_electrolyzer_controller_config(
        cls,
    ) -> Any:
        """Get a default electrolyzer controller config."""
        config = ElectrolyzerControllerConfig(
            name="DefaultElectrolyzerController",
            nom_load=100.0,
            min_load=10.0,
            max_load=110.0,
            standby_load=5.0,
            warm_start_time=70.0,
            cold_start_time=1800.0,
        )
        return config

    @staticmethod
    def read_config(electrolyzer_name):
        """Opens the according JSON-file, based on the electrolyzer_name."""

        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "electrolyzer_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Electrolyzer variants", {}).get(electrolyzer_name, {})

    @classmethod
    def control_electrolyzer(cls, electrolyzer_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(electrolyzer_name)
        log.information("Electrolyzer config: " + str(config_json))

        config = ElectrolyzerControllerConfig(
            name="L1ElectrolyzerController",  # config_json.get("name", "")
            nom_load=config_json.get("nom_load", 0.0),
            min_load=config_json.get("min_load", 0.0),
            max_load=config_json.get("max_load", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            warm_start_time=config_json.get("warm_start_time", 0.0),
            cold_start_time=config_json.get("cold_start_time", 0.0),
        )
        return config


class ElectrolyzerController(Component):

    """Electrolyzer Controller class."""

    # Inputs
    ProvidedLoad = "ProvidedLoad"

    # Outputs
    DistributedLoad = "DistributedLoad"
    ShutdownCount = "ShutdownCount"
    StandbyCount = "StandbyCount"
    CurrentMode = "CurrentMode"
    CurtailedLoad = "CurtailedLoad"
    OffCount = "OffCount"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectrolyzerControllerConfig,
    ) -> None:
        """Initialize the class."""
        self.controllerconfig = config

        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
        self.standby_load = config.standby_load
        self.warm_start_time = config.warm_start_time
        self.cold_start_time = config.cold_start_time

        super().__init__(
            name=self.controllerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels

        # Getting the load input
        self.load_input: ComponentInput = self.add_input(
            self.component_name,
            ElectrolyzerController.ProvidedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        # =================================================================================================================================
        # Output channels

        self.standby_count_total: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.StandbyCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.ANY,
            output_description="standby count",
        )

        self.distributed_load: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.DistributedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Load to electrolyzer",
        )

        self.current_mode_electrolyzer: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.CurrentMode,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="current mode of electrolyzer",
        )

        self.curtailed_load: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.CurtailedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="amount of curtailed load due to min and max load tresholds",
        )

        self.total_off_count: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.OffCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.ANY,
            output_description="Total count of switching off",
        )
        # =================================================================================================================================
        # Initialize variables

        self.standby_count = 0.0
        self.current_state = "OFF"  # standby
        self.curtailed_load_count = 0.0
        self.off_count = 0.0
        self.activation_runtime = 0.0

        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime

    def load_check(self, current_load, min_load, max_load, standby_load):
        """Load check."""
        if None in (current_load, min_load, max_load, standby_load):
            raise ValueError(
                f"None type not accepted. {current_load}, {min_load}, {max_load}, {standby_load}"
            )
        if current_load > max_load:
            current_load_to_system = max_load
            self.curtailed_load_count += current_load - max_load
            state = "ON"

        elif min_load <= current_load <= max_load:
            current_load_to_system = current_load
            self.curtailed_load_count += 0.0
            state = "ON"

        elif standby_load <= current_load < min_load:
            current_load_to_system = standby_load
            self.curtailed_load_count += current_load - standby_load
            state = "STANDBY"

        else:
            current_load_to_system = 0.0
            self.curtailed_load_count += current_load
            state = "OFF"

        return current_load_to_system, state, self.curtailed_load_count

    def state_check(self, target_state, cold_start_time_to_min, warm_start_time_to_min):
        """State check."""
        if target_state == "OFF":
            # System switches OFF
            if self.current_state == "ON":
                self.current_state = "SwitchingOFF"
                self.off_count += 1
            else:
                self.current_state = "OFF"

        elif target_state == "STANDBY":
            # System switches STANDY
            if self.current_state in ("OFF", "StartingfromOFF"):
                self.current_state = "OFF"
                self.off_count += 1
            if self.current_state == "ON":
                self.current_state = "SwitchingSTANDBY"
                self.standby_count += 1
            else:
                self.current_state = "STANDBY"

        else:
            # Test start
            if self.current_state in ["StartingfromOFF", "StartingfromSTANDBY"]:
                # pdb.set_trace()
                if (
                    self.activation_runtime
                    <= self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.current_state = "Startingtomin"
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
                self.current_state = "StartingfromOFF"
                self.activation_runtime = cold_start_time_to_min
            elif self.current_state == "STANDBY":
                self.current_state = "StartingfromSTANDBY"
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
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.standby_count = self.standby_count_previous
        self.current_state = self.current_state_previous
        self.curtailed_load_count = self.curtailed_load_count_previous
        self.off_count = self.off_count_previous
        self.activation_runtime = self.activation_runtime_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return
        """
        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
        self.warm_start_time = config.warm_start_time
        self.cold_start_time = config.cold_start_time
        """
        if self.nom_load == 0.0:
            self.nom_load = 1.0
        warm_start_time_to_min = self.warm_start_time * (self.min_load / self.nom_load)
        cold_start_time_to_min = self.cold_start_time * (self.min_load / self.nom_load)

        (current_load_to_system, state, self.curtailed_load_count) = self.load_check(
            (stsv.get_input_value(self.load_input)),
            self.min_load,
            self.max_load,
            self.standby_load,
        )  # change standby time
        # pdb.set_trace()
        (self.current_state, self.activation_runtime) = self.state_check(
            state, cold_start_time_to_min, warm_start_time_to_min
        )

        if self.current_state in ["OFF", "StartingfromOFF", "SwitchingOFF"]:
            stsv.set_output_value(self.distributed_load, 0.0)
            stsv.set_output_value(self.current_mode_electrolyzer, -1)
        elif self.current_state in [
            "STANDBY",
            "StartingfromSTANDBY",
            "SwitchingSTANDBY",
        ]:
            stsv.set_output_value(self.distributed_load, (self.nom_load * 0.05))
            stsv.set_output_value(self.current_mode_electrolyzer, 0)
        elif self.current_state == "Startingtomin":
            # pdb.set_trace()
            stsv.set_output_value(self.distributed_load, self.min_load)
            stsv.set_output_value(self.current_mode_electrolyzer, 1)

        else:
            stsv.set_output_value(self.distributed_load, current_load_to_system)
            stsv.set_output_value(self.current_mode_electrolyzer, 1)

        stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)
        stsv.set_output_value(self.total_off_count, self.off_count)
        stsv.set_output_value(self.standby_count_total, self.standby_count)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        for config_string in self.controllerconfig.get_string_dict():
            lines.append(config_string)
        lines.append("Component Name" + str(self.component_name))
        lines.append(
            "Total curtailed load: " + str(self.curtailed_load_count) + " [kW]"
        )
        lines.append(
            "Number of times the system was switched off: "
            + str(self.off_count)
            + " [#]"
        )
        lines.append(
            "Number of times the system was switched to standby mode: "
            + str(self.standby_count)
            + " [#]"
        )
        return lines
