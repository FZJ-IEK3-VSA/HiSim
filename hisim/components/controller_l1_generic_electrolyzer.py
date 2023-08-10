""" Controller for the generic electrolyzer. """

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
    warm_start_time: float
    cold_start_time: float

    @classmethod
    def get_default_electrolyzer_controller_config(
        cls,
    ) -> Any:
        """Get a default electrolyzer controller config."""
        config = ElectrolyzerControllerConfig(
            name="Default electrolyzer controller",
            nom_load=100.0,
            min_load=10.0,
            max_load=110.0,
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
        with open(config_file, "r") as json_file:
            data = json.load(json_file)
            return data.get("Electrolyzer variants", {}).get(electrolyzer_name, {})

    @classmethod
    def control_electrolyzer(cls, electrolyzer_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(electrolyzer_name)

        config = ElectrolyzerControllerConfig(
            name="Controller",  # config_json.get("name", "")
            nom_load=config_json.get("nom_load", 0.0),
            min_load=config_json.get("min_load", 0.0),
            max_load=config_json.get("max_load", 0.0),
            warm_start_time=config_json.get("warm_start_time", 0.0),
            cold_start_time=config_json.get("cold_start_time", 0.0),
        )
        return config


class ElectrolyzerController(Component):
    # Inputs
    ProvidedLoad = "Provided Load"

    # Outputs
    DistributedLoad = "Distributed Load"
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
        self.controllerconfig = config

        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
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

        self.shutdown_count_timestep: ComponentOutput = self.add_output(
            self.component_name,
            ElectrolyzerController.ShutdownCount,
            lt.LoadTypes.ON_OFF,
            lt.Units.ANY,
            output_description="shutdown count",
        )

        self.standby_count_timestep: ComponentOutput = self.add_output(
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
        self.current_mode = 0  #  standby
        self.warm_count = 0.0
        self.cold_count = 0.0
        self.initial_state = 0
        self.curtailed_load_count = 0.0
        self.cold_up_count = 0.0
        self.warm_up_count = 0.0
        self.off_count = 0.0

        self.standby_count_previous = self.standby_count
        self.current_mode_previous = self.current_mode
        self.warm_count_previous = self.warm_count
        self.cold_count_previous = self.cold_count
        self.initial_state_previous = self.initial_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.cold_up_count_previous = self.cold_up_count
        self.warm_up_count_previous = self.warm_up_count
        self.off_count_previous = self.off_count

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.standby_count_previous = self.standby_count
        self.current_mode_previous = self.current_mode
        self.warm_count_previous = self.warm_count
        self.cold_count_previous = self.cold_count
        self.initial_state_previous = self.initial_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.cold_up_count_previous = self.cold_up_count
        self.warm_up_count_previous = self.warm_up_count
        self.off_count_previous = self.off_count

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.standby_count = self.standby_count_previous
        self.current_mode = self.current_mode_previous
        self.warm_count = self.warm_count_previous
        self.cold_count = self.cold_count_previous
        self.initial_state = self.initial_state_previous
        self.curtailed_load_count = self.curtailed_load_count_previous
        self.cold_up_count = self.cold_up_count_previous
        self.warm_up_count = self.warm_up_count_previous
        self.off_count = self.off_count_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return

        current_power = stsv.get_input_value(self.load_input)
        print("current_power: ", current_power)

        nominal_load = self.nom_load
        min_load = self.min_load
        max_load = self.max_load
        warm_up_time = self.warm_start_time
        cold_up_time = self.cold_start_time

        if current_power >= max_load:
            self.curtailed_load_count += current_power - max_load
            stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)
            current_power = max_load

        if current_power >= min_load and self.current_mode == 1:
                self.current_mode = 1
                stsv.set_output_value(self.distributed_load, current_power)

        if current_power >= min_load and self.initial_state == -1:  # -1 equals to "off"
            self.current_mode = -0.5  # cold starting
            self.initial_state = 111
            self.cold_up_count = 0.0
        elif (
            current_power >= min_load and self.initial_state == 0
        ):  # 0 equals to "standby"
            self.current_mode = 0.5  # warm starting
            self.initial_state = 111
            self.warm_up_count = 0.0

        if self.current_mode == -0.5 and self.cold_up_count < cold_up_time:
            self.cold_up_count += float(self.my_simulation_parameters.seconds_per_timestep)
            current_power = 0
            stsv.set_output_value(self.distributed_load, current_power)

        elif self.current_mode == -0.5 and self.cold_up_count >= cold_up_time:
            self.cold_count += 1
            self.current_mode = 1
            current_power = nominal_load

        if self.current_mode == 0.5 and self.warm_up_count < warm_up_time:
            self.warm_up_count += self.my_simulation_parameters.seconds_per_timestep
            current_power = 0
            stsv.set_output_value(self.distributed_load, current_power)

        elif self.current_mode == 0.5 and self.warm_up_count >= warm_up_time:
            self.warm_count += 1
            self.current_mode = 1
            current_power = nominal_load
            stsv.set_output_value(self.distributed_load, current_power)

        standby_threshold = 0.06 * nominal_load

        if current_power < min_load and current_power >= standby_threshold:
            # Power sufficient for standby mode
            if self.current_mode == 1:
                self.current_mode = 0
                self.initial_state = 0
                self.standby_count += 1
                current_power = standby_threshold  # Set the current power demand to the standby threshold value
                stsv.set_output_value(self.distributed_load, current_power)

            elif self.current_mode == 0:
                self.current_mode = 0
                current_power = standby_threshold  # Set the current power demand to the standby threshold value
                stsv.set_output_value(self.distributed_load, current_power)

            self.curtailed_load_count += current_power - standby_threshold
            stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)

        elif current_power < min_load and current_power < standby_threshold:
            # The system is to be switched off completely
            if self.current_mode == 1:  # Check if the system is in the "on" state
                self.current_mode = -1
                self.initial_state = -1
                self.off_count += 1
                current_power = 0
                stsv.set_output_value(self.distributed_load, current_power)

            elif (
                self.current_mode == 0
            ):  # Check if the system is in the "standby" state
                self.current_mode = -1
                self.initial_state = -1
                self.off_count += 1
                current_power = 0
                stsv.set_output_value(self.distributed_load, current_power)

            self.curtailed_load_count += current_power
            stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)

        print("last self.curtailed_load_count: ", self.curtailed_load_count)
        # Initializing outputs
        stsv.set_output_value(self.standby_count_timestep, self.standby_count)
        stsv.set_output_value(self.current_mode_electrolyzer, self.current_mode)
        # stsv.set_output_value(self.curtailed_load, self.curtailed_load_count)
        stsv.set_output_value(self.total_off_count, self.off_count)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        lines.append("Controller: " + self.component_name)
        return lines
