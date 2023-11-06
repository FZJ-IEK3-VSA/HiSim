""" rSOC controller. """
# clean
import os
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


# from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum


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
class RsocControllerConfig(ConfigBase):

    """Config of the rSOC Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return RsocController.get_full_classname()

    name: str

    nom_load_soec: float
    min_load_soec: float
    max_load_soec: float
    warm_start_time_soec: float
    cold_start_time_soec: float
    switching_time_from_soec_to_sofc: float

    nom_power_sofc: float
    min_power_sofc: float
    max_power_sofc: float
    warm_start_time_sofc: float
    cold_start_time_sofc: float
    switching_time_from_sofc_to_soec: float

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
    def config_rsoc(cls, rsoc_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(rsoc_name)
        config = RsocControllerConfig(
            name="rSCO l1 Controller",
            nom_load_soec=config_json.get("nom_load_soec", 0.0),
            min_load_soec=config_json.get("min_load_soec", 0.0),
            max_load_soec=config_json.get("max_load_soec", 0.0),
            warm_start_time_soec=config_json.get("warm_start_time_soec", 0.0),
            cold_start_time_soec=config_json.get("cold_start_time_soec", 0.0),
            switching_time_from_soec_to_sofc=config_json.get(
                "switching_time_from_soec_to_sofc", 0.0
            ),
            nom_power_sofc=config_json.get("nom_power_sofc", 0.0),
            min_power_sofc=config_json.get("min_power_sofc", 0.0),
            max_power_sofc=config_json.get("max_power_sofc", 0.0),
            warm_start_time_sofc=config_json.get("warm_start_time_sofc", 0.0),
            cold_start_time_sofc=config_json.get("cold_start_time_sofc", 0.0),
            switching_time_from_sofc_to_soec=config_json.get(
                "switching_time_from_sofc_to_soec", 0.0
            ),
        )
        return config


class RsocController(Component):

    """rSOC Controller class."""

    # Inputs
    ProvidedPower = "ProvidedPower"
    PowerDemand = "PowerDemand"

    # Outputs
    PowerToSOEC = "PowerToSOEC"
    StateToRSOC = "StateToRSOC"
    DemandToSOFC = "DemandToSOFC"
    StateToSOFC = "StateToSOFC"
    PowerVsDemand = "PowerVsDemand"
    CurtailedLoad = "CurtailedLoad"
    CurtailedPower = "CurtailedPower"
    TotalOffCount = "# of times the system was switched off"
    TotalStandbyCount = "# of times the system was to standby mode"
    SwitchCount = "SwitchCount"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: RsocControllerConfig,
    ) -> None:
        """Initialize class."""
        self.rsoccontrollerconfig = config

        self.name = config.name

        self.nom_load_soec = config.nom_load_soec
        self.min_load_soec = config.min_load_soec
        self.max_load_soec = config.max_load_soec
        self.warm_start_time_soec = config.warm_start_time_soec
        self.cold_start_time_soec = config.cold_start_time_soec
        self.switching_time_from_soec_to_sofc = config.switching_time_from_soec_to_sofc

        self.nom_power_sofc = config.nom_power_sofc
        self.min_power_sofc = config.min_power_sofc
        self.max_power_sofc = config.max_power_sofc
        self.warm_start_time_sofc = config.warm_start_time_sofc
        self.cold_start_time_sofc = config.cold_start_time_sofc
        self.switching_time_from_sofc_to_soec = config.switching_time_from_sofc_to_soec

        self.cold_start_time = self.cold_start_time_soec
        self.warm_start_time = self.warm_start_time_soec
        self.standby_load = 100.0

        super().__init__(
            name=self.rsoccontrollerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels

        # Getting the load input
        self.provided_power: ComponentInput = self.add_input(
            self.component_name,
            RsocController.ProvidedPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )
        # Getting the demand input
        self.power_demand: ComponentInput = self.add_input(
            self.component_name,
            RsocController.PowerDemand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            False,
        )

        # =================================================================================================================================
        # Output channels

        self.state_rsoc: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.StateToRSOC,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="State to the RSOC",
        )

        self.current_delta: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.PowerVsDemand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Current delta between provided power and power demand",
        )

        self.curtailed_load: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.CurtailedLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Curtailed load",
        )
        self.curtailed_power: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.CurtailedPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Curtailed power",
        )
        self.total_switch_count: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.SwitchCount,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total count of time spend switching between operation modes",
        )
        self.total_off_count: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.TotalOffCount,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total count of off cycles",
        )
        self.total_standby_count: ComponentOutput = self.add_output(
            self.component_name,
            RsocController.TotalStandbyCount,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total count of standby cycles",
        )

        # =================================================================================================================================
        # Initialize variables

        self.standby_count = 0.0
        self.current_state = "OFF"  # standby
        self.warm_count = 0.0
        self.cold_count = 0.0
        self.initial_state = 0
        self.curtailed_load_count = 0.0
        self.cold_up_count = 0.0
        self.warm_up_count = 0.0
        self.standby_count = 0.0
        self.off_count = 0.0
        self.activation_runtime = 0.0
        self.elapsed_time = 0.0
        self.switch_start_time = 0.0
        self.rsoc_state = "SOFC"
        self.total_switch_time_count = 0.0

        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.warm_count_previous = self.warm_count
        self.cold_count_previous = self.cold_count
        self.initial_state_previous = self.initial_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.cold_up_count_previous = self.cold_up_count
        self.warm_up_count_previous = self.warm_up_count
        self.standby_count_previous = self.standby_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime
        self.elapsed_time_previous = self.elapsed_time
        self.rsoc_state_previous = self.rsoc_state
        self.total_switch_time_count_previous = self.total_switch_time_count

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.standby_count_previous = self.standby_count
        self.current_state_previous = self.current_state
        self.warm_count_previous = self.warm_count
        self.cold_count_previous = self.cold_count
        self.initial_state_previous = self.initial_state
        self.curtailed_load_count_previous = self.curtailed_load_count
        self.cold_up_count_previous = self.cold_up_count
        self.warm_up_count_previous = self.warm_up_count
        self.standby_count_previous = self.standby_count
        self.off_count_previous = self.off_count
        self.activation_runtime_previous = self.activation_runtime
        self.elapsed_time_previous = self.elapsed_time
        self.rsoc_state_previous = self.rsoc_state
        self.total_switch_time_count_previous = self.total_switch_time_count

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.standby_count = self.standby_count_previous
        self.current_state = self.current_state_previous
        self.warm_count = self.warm_count_previous
        self.cold_count = self.cold_count_previous
        self.initial_state = self.initial_state_previous
        self.curtailed_load_count = self.curtailed_load_count_previous
        self.cold_up_count = self.cold_up_count_previous
        self.warm_up_count = self.warm_up_count_previous
        self.standby_count = self.standby_count_previous
        self.off_count = self.off_count_previous
        self.activation_runtime = self.activation_runtime_previous
        self.elapsed_time = self.elapsed_time_previous
        self.rsoc_state = self.rsoc_state_previous
        self.total_switch_time_count = self.total_switch_time_count_previous

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return
        """
        self.nom_load_soec = config.nom_load_soec
        self.min_load_soec = config.min_load_soec
        self.max_load_soec = config.max_load_soec
        self.warm_start_time_soec = config.warm_start_time_soec
        self.cold_start_time_soec = config.cold_start_time_soec
        self.switching_time_from_soec_to_sofc = config.switching_time_from_soec_to_sofc

        self.nom_power_sofc = config.nom_power_sofc
        self.min_power_sofc = config.min_power_sofc
        self.max_power_sofc = config.max_power_sofc
        self.warm_start_time_sofc = config.warm_start_time_sofc
        self.cold_start_time_sofc = config.cold_start_time_sofc
        self.switching_time_from_sofc_to_soec = config.switching_time_from_sofc_to_soec

        """
        self.cold_start_time = self.cold_start_time_soec
        self.warm_start_time = self.warm_start_time_soec
        self.standby_load = 100.0

        if timestep < 5:
            print(self.min_load_soec)
            print(self.min_power_sofc)

        power = stsv.get_input_value(self.provided_power)
        # if -self.min_load_soec < power < 0.0:
        #    print("should switch OFF SOEC")
        # elif 0.0 < power < self.min_power_sofc:
        #    print("should switch OFF SOFC")

        if power < -self.min_load_soec or self.min_power_sofc < power:
            if self.current_state == "OFF":
                self.activation_runtime = self.cold_start_time
                self.current_state = "Starting"
                self.off_count += 1
            elif self.current_state == "STANDBY":
                self.activation_runtime = self.warm_start_time
                self.current_state = "Starting"
                self.standby_count += 1
            elif self.current_state == "Starting":
                if (
                    self.activation_runtime
                    <= self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.activation_runtime = 0.0
                    self.current_state = "ON"
                else:
                    self.activation_runtime -= (
                        self.my_simulation_parameters.seconds_per_timestep
                    )
            else:
                self.activation_runtime = 0.0
                self.current_state = "ON"
        elif power in (-self.min_load_soec, self.min_power_sofc):
            if self.current_state == "ON":
                self.current_state = "STANDBY"
            else:
                self.current_state = self.current_state
        else:
            self.current_state = "OFF"

        """
        if power != 0.0:
            if self.current_state == "OFF":
                self.activation_runtime = self.cold_start_time
                self.current_state = "Starting"
            elif self.current_state == "STANDBY":
                self.activation_runtime = self.warm_start_time
                self.current_state = "Starting"
            elif self.current_state == "Starting":
                if self.activation_runtime <= self.my_simulation_parameters.seconds_per_timestep:
                    self.activation_runtime = 0.0
                    self.current_state = "ON"
                else:
                    self.activation_runtime -= self.my_simulation_parameters.seconds_per_timestep
            else:
                self.activation_runtime = 0.0
                self.current_state = "ON"
        """

        if power < 0.0 and self.current_state == "ON":
            if self.rsoc_state == "SOEC":
                power_to_rsco = power
                state_to_rsoc = 1
            elif self.rsoc_state in ("SOFC", "Switching to SOFC"):
                self.elapsed_time = self.switching_time_from_sofc_to_soec
                self.rsoc_state = "Switching to SOEC"
                power_to_rsco = 0.0
                state_to_rsoc = 0
                self.total_switch_time_count += self.switching_time_from_sofc_to_soec
            else:
                if (
                    self.elapsed_time
                    <= self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.elapsed_time = 0.0
                    self.rsoc_state = "SOEC"
                    power_to_rsco = 0.0
                    state_to_rsoc = 0
                else:
                    self.elapsed_time -= (
                        self.my_simulation_parameters.seconds_per_timestep
                    )
                    power_to_rsco = 0.0
                    state_to_rsoc = 0

        elif power > 0.0 and self.current_state == "ON":
            if self.rsoc_state == "SOFC":
                power_to_rsco = power
                state_to_rsoc = 1
            elif self.rsoc_state in ("SOEC", "Switching to SOEC"):
                self.elapsed_time = self.switching_time_from_soec_to_sofc
                self.rsoc_state = "Switching to SOFC"
                power_to_rsco = 0.0
                state_to_rsoc = 0
                self.total_switch_time_count += self.switching_time_from_soec_to_sofc
            else:
                if (
                    self.elapsed_time
                    <= self.my_simulation_parameters.seconds_per_timestep
                ):
                    self.elapsed_time = 0.0
                    self.rsoc_state = "SOFC"
                    power_to_rsco = 0.0
                    state_to_rsoc = 0
                else:
                    self.elapsed_time -= (
                        self.my_simulation_parameters.seconds_per_timestep
                    )
                    power_to_rsco = 0.0
                    state_to_rsoc = 0
        elif power < 0.0 and self.current_state == "STANDBY":
            power_to_rsco = self.min_load_soec
            state_to_rsoc = 0
        elif power > 0.0 and self.current_state == "STANDBY":
            power_to_rsco = self.min_power_sofc
            state_to_rsoc = 0
        else:
            power_to_rsco = 0.0
            state_to_rsoc = -1

        # stsv.set_output_value(self.power_soec, power_to_soec)
        stsv.set_output_value(self.state_rsoc, state_to_rsoc)
        # stsv.set_output_value(self.demand_sofc, power_to_sofc)
        # stsv.set_output_value(self.state_sofc, state_to_sofc)
        stsv.set_output_value(self.current_delta, power_to_rsco)
        stsv.set_output_value(self.total_switch_count, self.total_switch_time_count)
        stsv.set_output_value(self.total_off_count, self.off_count)
        stsv.set_output_value(self.total_standby_count, self.standby_count)
