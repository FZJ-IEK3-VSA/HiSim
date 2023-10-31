""" L2 Controller for PtX Buffer Battery operation. """
# clean
import os
from typing import List
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
class PTXControllerConfig(ConfigBase):

    """Configutation of the PtX  Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return PTXController.get_full_classname()

    name: str
    nom_load: float
    min_load: float
    max_load: float
    standby_load: float
    operation_mode: float

    @staticmethod
    def read_config(electrolyzer_name):
        """Read config."""
        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "electrolyzer_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Electrolyzer variants", {}).get(electrolyzer_name, {})

    @classmethod
    def control_electrolyzer(cls, electrolyzer_name, operation_mode):
        """Sets the according parameters for the chosen electrolyzer.

        The operations mode can be used to select how the electrolyser is operated:
        Nominal Load: Operated with a constant nominal load.
        Minimum Load: Operated within the part load range.
        Standby Load: Operated so that the system is not switched off.
        """
        config_json = cls.read_config(electrolyzer_name)

        config = PTXControllerConfig(
            name="L2PtXController",  # config_json.get("name", "")
            nom_load=config_json.get("nom_load", 0.0),
            min_load=config_json.get("min_load", 0.0),
            max_load=config_json.get("max_load", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            operation_mode=operation_mode,
        )
        return config


class PTXController(Component):

    """PtX  Controller."""

    # Inputs
    RESLoad = "RESLoad"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerToThird = "PowerToThird"
    PowerToSystem = "PowerToSystem"
    EnergyToThird = "EnergyToThird"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: PTXControllerConfig,
    ) -> None:
        """Initialize the class."""
        self.ptxcontrollerconfig = config

        self.nom_load = config.nom_load
        self.min_load = config.min_load
        self.max_load = config.max_load
        self.standby_load = config.standby_load
        self.operation_mode = config.operation_mode

        super().__init__(
            name=self.ptxcontrollerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels

        self.load_input: ComponentInput = self.add_input(
            self.component_name,
            PTXController.RESLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,  # for EMS
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            PTXController.StateOfCharge,
            lt.LoadTypes.DISTRICTHEATING,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.PowerToThird,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Charges or discharges the battery",
        )
        self.energy_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.EnergyToThird,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Charges or discharges the battery",
        )

        self.load_to_system: ComponentOutput = self.add_output(
            self.component_name,
            PTXController.PowerToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes RES load to the system",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state = "OFF"
        self.threshold_exceeded = False
        self.standby_time_count = 0.0
        self.total_energy_to_battery = 0.0

        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count
        self.total_energy_to_battery_previous = self.total_energy_to_battery

    def system_operation(self, operation_mode, res_load):
        """System operation."""
        if operation_mode == "NominalLoad":
            load_to_system = self.nom_load
            power_to_battery = (
                res_load - self.nom_load
            )  # postive battery charge, negative battery discharges

        elif operation_mode == "MinimumLoad":
            if self.min_load <= res_load <= self.max_load:
                load_to_system = res_load
                power_to_battery = 0.0
            elif res_load < self.min_load:
                load_to_system = self.min_load
                power_to_battery = res_load - self.min_load
            else:
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load

        elif operation_mode == "StandbyLoad":
            if self.min_load <= res_load <= self.max_load:
                load_to_system = res_load
                power_to_battery = 0.0
            elif self.max_load < res_load:
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load
            else:
                # standby_load <= res_load < min_load and res_load < standby_load:
                load_to_system = self.standby_load
                power_to_battery = res_load - self.standby_load  # if

        elif operation_mode == "StandbyandOffLoad":
            if self.min_load <= res_load <= self.max_load:
                self.standby_time_count = 0.0
                load_to_system = res_load
                power_to_battery = 0.0
            elif self.max_load < res_load:
                self.standby_time_count = 0.0
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load
            elif self.standby_load <= res_load < self.min_load:
                self.standby_time_count = 0.0
                load_to_system = self.standby_load
                power_to_battery = res_load - self.standby_load
            else:  # res_load <= self.standby_load
                standby_operation_time = 3600.0  # 7200.0
                if self.standby_time_count >= standby_operation_time:
                    load_to_system = 0.0
                    power_to_battery = 0.0
                else:
                    load_to_system = self.standby_load
                    power_to_battery = res_load - self.standby_load
                    self.standby_time_count += (
                        self.my_simulation_parameters.seconds_per_timestep
                    )

        else:
            if res_load <= self.max_load:
                load_to_system = res_load
                power_to_battery = 0.0
            else:  # max_power < power_delta:
                load_to_system = self.max_load
                power_to_battery = res_load - self.max_load

        return load_to_system, power_to_battery

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count
        self.total_energy_to_battery_previous = self.total_energy_to_battery

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous
        self.standby_time_count = self.standby_time_count_previous
        self.total_energy_to_battery = self.total_energy_to_battery_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        res_load = stsv.get_input_value(self.load_input)

        """ Only for household testing
        if self.system_state == "OFF" and soc < 0.2:
            power_to_battery = res_load
            load_to_system = 0.0
        elif self.system_state == "OFF" and soc >= 0.2:
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )
            self.system_state = "ON"
        else:
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )
            self.system_state = "ON"

        """
        (load_to_system, power_to_battery) = self.system_operation(
            self.operation_mode, res_load
        )

        """
        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_to_battery = res_load
            load_to_system = 0.0
        """
        self.total_energy_to_battery += power_to_battery * (
            self.my_simulation_parameters.seconds_per_timestep / 3600
        )

        stsv.set_output_value(self.load_to_battery, power_to_battery)
        stsv.set_output_value(self.load_to_system, load_to_system)
        stsv.set_output_value(self.energy_to_battery, self.total_energy_to_battery)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        return self.ptxcontrollerconfig.get_string_dict()
