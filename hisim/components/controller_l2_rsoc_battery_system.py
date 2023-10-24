"""L2 Controller for PtX Buffer Battery operation."""
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
class RsocBatteryControllerConfig(ConfigBase):

    """Configutation of the rSOC and Battery Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return RsocBatteryController.get_full_classname()

    name: str
    nom_load_soec: float
    min_load_soec: float
    max_load_soec: float
    standby_load: float
    nom_power_sofc: float
    min_power_sofc: float
    max_power_sofc: float
    # standby_load_sofc: float

    operation_mode: float

    @staticmethod
    def read_config(rsoc_name):
        """Opens the according JSON-file, based on the rSOC_name."""

        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "rSOC_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("rSOC variants", {}).get(rsoc_name, {})

    @classmethod
    def confic_rsoc(cls, rsoc_name, operation_mode):
        """Configure rsoc."""
        config_json = cls.read_config(rsoc_name)

        config = RsocBatteryControllerConfig(
            name="rSOC and Battery Controller",  # config_json.get("name", "")
            nom_load_soec=config_json.get("nom_load_soec", 0.0),
            min_load_soec=config_json.get("min_load_soec", 0.0),
            max_load_soec=config_json.get("max_load_soec", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            nom_power_sofc=config_json.get("nom_power_sofc", 0.0),
            min_power_sofc=config_json.get("min_power_sofc", 0.0),
            max_power_sofc=config_json.get("max_power_sofc", 0.0),
            # standby_load_sofc=config_json.get("standby_load_sofc", 0.0),
            operation_mode=operation_mode,
        )
        return config


class RsocBatteryController(Component):

    """rSOC and Battery  Controller."""

    # Inputs
    RESLoad = "RESLoad"
    Demand = "Demand"
    StateOfCharge = "StateOfCharge"

    # Outputs
    PowerToBattery = "PowerToBattery"
    PowerToSystem = "PowerToSystem"
    Power = "Power"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: RsocBatteryControllerConfig,
    ) -> None:
        """Initialize the class."""
        self.ptxcontrollerconfig = config

        self.nom_load_soec = config.nom_load_soec
        self.min_load_soec = config.min_load_soec
        self.max_load_soec = config.max_load_soec
        self.standby_load_soec = config.standby_load
        self.nom_power_sofc = config.nom_power_sofc
        self.min_power_sofc = config.min_power_sofc
        self.max_power_sofc = config.max_power_sofc
        self.standby_load_sofc = config.standby_load
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
            RsocBatteryController.RESLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.demand_input: ComponentInput = self.add_input(
            self.component_name,
            RsocBatteryController.Demand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )

        self.soc: ComponentInput = self.add_input(
            self.component_name,
            RsocBatteryController.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.load_to_battery: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.PowerToBattery,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Charges or discharges the battery",
        )

        self.load_to_system: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.PowerToSystem,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="distributes RES load to the system",
        )
        self.power: ComponentOutput = self.add_output(
            self.component_name,
            RsocBatteryController.Power,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="power delta between drovided and demand.",
        )

        # =================================================================================================================================
        # Initialize variables
        self.system_state = "OFF"
        self.threshold_exceeded = False

        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded

    def system_operation(
        self,
        operation_mode,
        power_delta,
        nom_power,
        min_power,
        max_power,
    ):
        """System operation."""

        if operation_mode == "NominalLoad":
            load_to_system = nom_power
            power_to_battery = (
                power_delta - nom_power
            )  # postive battery charge, negative battery discharges

            # pdb.set_trace()
        elif operation_mode == "MinimumLoad":

            # pdb.set_trace()
            if min_power <= power_delta <= max_power:
                load_to_system = power_delta
                power_to_battery = 0.0
            elif power_delta < min_power:
                load_to_system = min_power
                power_to_battery = power_delta - min_power
            else:
                load_to_system = max_power
                power_to_battery = power_delta - max_power

        elif operation_mode == "StandbyLoad":
            if min_power <= power_delta <= max_power:
                load_to_system = power_delta
                power_to_battery = 0.0
            elif max_power < power_delta:
                load_to_system = max_power
                power_to_battery = power_delta - max_power
            else:
                # standby_load <= power_delta < min_load and power_delta < standby_load:
                load_to_system = min_power
                power_to_battery = power_delta - min_power  # if
        else:
            if power_delta <= max_power:
                load_to_system = power_delta
                power_to_battery = 0.0
            else:  # max_power < power_delta:
                load_to_system = max_power
                power_to_battery = power_delta - max_power

        return load_to_system, power_to_battery

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.system_state_previous = self.system_state
        self.threshold_exceeded_previous = self.threshold_exceeded

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.system_state = self.system_state_previous
        self.threshold_exceeded = self.threshold_exceeded_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        # first a power deman evaluation
        if timestep < 10:
            print(self.min_load_soec)
            print(self.min_power_sofc)
            print(self.standby_load_soec)

        res_load = stsv.get_input_value(self.load_input) / 1000  # to use KILOWATT
        demand = stsv.get_input_value(self.demand_input) / 1000  # to use KILOWATT
        power_delta = demand - res_load

        if power_delta < 0.0:
            # pdb.set_trace()
            # SOEC
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode,
                abs(power_delta),
                self.nom_load_soec,
                self.min_load_soec,
                self.max_load_soec,
            )
            load_to_system = -load_to_system
        elif power_delta > 0.0:
            # pdb.set_trace()
            # SOFC
            (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode,
                abs(power_delta),
                self.nom_power_sofc,
                self.min_power_sofc,
                self.max_power_sofc,
            )
        else:
            # pdb.set_trace()
            # power_delta = 0
            load_to_system = 0.0
            power_to_battery = 0.0

        """
        (load_to_system, power_to_battery) = self.system_operation(
                self.operation_mode, res_load
            )

        if self.system_state == "OFF":
            if 0.30 < stsv.get_input_value(self.soc):
                print(stsv.get_input_value(self.soc))
                self.system_state = "ON"
                print(self.system_state)

            power_to_battery = res_load
            load_to_system = 0.0
            # pdb.set_trace()
        """

        stsv.set_output_value(
            self.load_to_battery, (power_to_battery * 1000)
        )  # Output: WATT
        stsv.set_output_value(self.load_to_system, load_to_system)
        stsv.set_output_value(self.power, power_delta)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        return self.ptxcontrollerconfig.get_string_dict()
