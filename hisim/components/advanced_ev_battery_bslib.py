""" Car Battery implementation built upon the bslib library. It contains a CarBattery Class together with its Configuration and State. """

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import Any, List, Tuple

import pandas as pd
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim import log
from hisim.component import (Component, ComponentConnection, ComponentInput,
                             ComponentOutput, ConfigBase, SingleTimeStepValues)
from hisim.components import controller_l1_generic_ev_charge
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"


@dataclass_json
@dataclass
class CarBatteryConfig(ConfigBase):
    """Configuration of a Car Battery. """
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: name of battery to search in database (bslib)
    system_id: str
    #: charging and discharging power in Watt
    p_inv_custom: float
    #: battery capacity in in kWh
    e_bat_custom: float
    #: amount of energy used to charge the car battery
    charge: float
    #: amount of energy discharged from the battery
    discharge: float

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return CarBattery.get_full_classname()

    @classmethod
    def get_default_config(cls) -> "CarBatteryConfig":
        """Returns default configuration of a Car Battery."""
        config = CarBatteryConfig(
            name="CarBattery",
            system_id="SG1",
            p_inv_custom=1e4,
            e_bat_custom=30,
            source_weight=1,
            charge=0,
            discharge=0,
        )
        return config


class CarBattery(Component):
    """
    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) CarBattery controller (controller_l1_generic_ev_charge)
    """

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"  # W

    # Outputs
    AcBatteryPower = "AcBatteryPower"  # W
    DcBatteryPower = "DcBatteryPower"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: CarBatteryConfig
    ):
        """
        Loads the parameters of the specified battery storage.
        """
        self.battery_config = config
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.p_inv_custom = self.battery_config.p_inv_custom

        self.e_bat_custom = self.battery_config.e_bat_custom

        # Component has states
        self.state = EVBatteryState()
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.BAT = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.p_inv_custom,
            e_bat_custom=self.e_bat_custom,
        )

        # Define component inputs
        self.p_set: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.LoadingPowerInput,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.p_bs: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.CAR_BATTERY,
            ],
            output_description="Charging power of the battery in Watt (Alternating current)",
        )

        self.p_bat: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Charging power of the battery in Watt (Direct current).",
        )

        self.soc: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description="State of charge of the battery.",
        )

        self.add_default_connections(
            self.get_default_connections_from_charge_controller()
        )

    def get_default_connections_from_charge_controller(self) -> Any:
        log.information(
            "setting ev charge controller default connections in car battery"
        )
        connections: List[ComponentConnection] = []
        ev_charge_controller_classname = (
            controller_l1_generic_ev_charge.L1Controller.get_classname()
        )
        connections.append(
            ComponentConnection(
                CarBattery.LoadingPowerInput,
                ev_charge_controller_classname,
                controller_l1_generic_ev_charge.L1Controller.ToOrFromBattery,
            )
        )
        return connections

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:

        # Parameters
        dt = self.my_simulation_parameters.seconds_per_timestep

        # Load input values
        p_set = stsv.get_input_value(self.p_set)
        soc = self.state.soc

        # Simulate on timestep
        results = self.BAT.simulate(p_load=p_set, soc=soc, dt=dt)
        p_bs = results[0]
        p_bat = results[1]
        soc = results[2]

        # write values for output time series
        stsv.set_output_value(self.p_bs, p_bs)
        stsv.set_output_value(self.p_bat, p_bat)
        stsv.set_output_value(self.soc, soc)

        # write values to state
        self.state.soc = soc

    def write_to_report(self) -> List[str]:
        """Writes Car Battery values to report."""
        return self.battery_config.get_string_dict()

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame, ) -> Tuple[float, float]:
        for index, output in enumerate(all_outputs):
            if output.postprocessing_flag is not None and \
                    output.component_name == self.battery_config.name + "_w" + str(self.battery_config.source_weight):
                if InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    self.battery_config.charge = round(
                        postprocessing_results.iloc[:, index].clip(lower=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep / 3.6e6, 1)
                    self.battery_config.discharge = round(
                        postprocessing_results.iloc[:, index].clip(upper=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep / 3.6e6, 1)
        return 0, 0


@dataclass
class EVBatteryState:
    # state of charge of the battery
    soc: float = 0

    def clone(self):
        "Creates a copy of the Car Battery State."
        return EVBatteryState(soc=self.soc)
