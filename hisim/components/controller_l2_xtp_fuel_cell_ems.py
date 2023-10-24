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
class XTPControllerConfig(ConfigBase):

    """Configutation of the PtX  Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return XTPController.get_full_classname()

    name: str
    nom_output: float
    min_output: float
    max_output: float
    standby_load: float
    operation_mode: float

    @staticmethod
    def read_config(fuel_cell_name):
        """Read config."""
        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "fuel_cell_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def control_fuel_cell(cls, fuel_cell_name, operation_mode):
        """Sets the according parameters for the chosen fuel cell."""
        config_json = cls.read_config(fuel_cell_name)

        config = XTPControllerConfig(
            name="L2XTPController",  # config_json.get("name", "")
            nom_output=config_json.get("nom_output", 0.0),
            min_output=config_json.get("min_output", 0.0),
            max_output=config_json.get("max_output", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            operation_mode=operation_mode,
        )
        return config


class XTPController(Component):

    """XtP  Controller."""

    # Inputs
    DemandLoad = "DemandLoad"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerFromThird = "PowerFromThird"
    DemandToSystem = "DemandToSystem"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: XTPControllerConfig,
    ) -> None:
        """Initialize the class."""
        self.xtpcontrollerconfig = config

        self.nom_output = config.nom_output
        self.min_output = config.min_output
        self.max_output = config.max_output
        self.standby_load = config.standby_load
        self.operation_mode = config.operation_mode

        super().__init__(
            name=self.xtpcontrollerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels

        self.demand_input: ComponentInput = self.add_input(
            self.component_name,
            XTPController.DemandLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            XTPController.StateOfCharge,
            lt.LoadTypes.DISTRICTHEATING,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_from_battery: ComponentOutput = self.add_output(
            self.component_name,
            XTPController.PowerFromThird,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Discharges the battery in case of no power production",
        )

        self.demand_to_system: ComponentOutput = self.add_output(
            self.component_name,
            XTPController.DemandToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes demand to the system",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state = "OFF"
        self.threshold_exceeded = False
        self.standby_time_count = 0.0

        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count

    def system_operation(self, operation_mode, demand_load):
        """System operation."""
        if operation_mode == "StandbyLoad":
            if self.min_output <= demand_load <= self.max_output:
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.max_output < demand_load:
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.standby_load <= demand_load < self.min_output:
                # standby_load <= demand_load < min_output and demand_load < standby_load:
                demand_to_system = demand_load
                power_from_battery = 0.0
            else:
                demand_to_system = self.standby_load
                power_from_battery = -self.standby_load

        elif operation_mode == "StandbyandOffLoad":
            if self.min_output <= demand_load <= self.max_output:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.max_output < demand_load:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            elif self.standby_load <= demand_load < self.min_output:
                self.standby_time_count = 0.0
                demand_to_system = demand_load
                power_from_battery = 0.0
            else:  # demand_load <= self.standby_load
                standby_operation_time = 7200.0  # 7200.0
                if self.standby_time_count >= standby_operation_time:
                    demand_to_system = 0.0
                    power_from_battery = 0.0
                else:
                    demand_to_system = self.standby_load
                    power_from_battery = -self.standby_load
                    self.standby_time_count += (
                        self.my_simulation_parameters.seconds_per_timestep
                    )

        else:
            demand_to_system = demand_load
            power_from_battery = 0.0

        return demand_to_system, power_from_battery

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded
        self.standby_time_count_previous = self.standby_time_count

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous
        self.standby_time_count = self.standby_time_count_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        demand_load = abs(
            stsv.get_input_value(self.demand_input) / 1000
        )  # WATT input to KILOWATT

        """ Only for household testing
        if self.system_state == "OFF" and soc < 0.2:
            power_from_battery = demand_load
            demand_to_system = 0.0
        elif self.system_state == "OFF" and soc >= 0.2:
            (demand_to_system, power_from_battery) = self.system_operation(
                self.operation_mode, demand_load
            )
            self.system_state = "ON"
        else:
            (demand_to_system, power_from_battery) = self.system_operation(
                self.operation_mode, demand_load
            )
            self.system_state = "ON"
        """
        (demand_to_system, power_from_battery) = self.system_operation(
            self.operation_mode, demand_load
        )

        """
        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_from_battery = demand_load
            demand_to_system = 0.0
        """

        stsv.set_output_value(
            self.load_from_battery, power_from_battery * 1000
        )  # Battery Output in WATT
        stsv.set_output_value(self.demand_to_system, demand_to_system)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        return self.xtpcontrollerconfig.get_string_dict()
