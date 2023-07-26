""" Battery implementation built upon the bslib library. It contains a Battery Class together with its Configuration and State. """

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from typing import List, Any
from dataclasses import dataclass
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ConfigBase,
)
from hisim.loadtypes import LoadTypes, Units, InandOutputType, ComponentType
from hisim.simulationparameters import SimulationParameters
from typing import Optional

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
class BatteryConfig(ConfigBase):
    """Battery Configuration."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return Battery.get_full_classname()

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: name of battery to search in database (bslib)
    system_id: str
    #: charging and discharging power in Watt
    custom_pv_inverter_power_generic_in_watt: float
    #: battery capacity in in kWh
    custom_battery_capacity_generic_in_kilowatt_hour: float

    @classmethod
    def get_default_config(cls) -> "BatteryConfig":
        """Returns default configuration of battery."""
        config = BatteryConfig(
            name="Battery",
            # https://www.energieinstitut.at/die-richtige-groesse-von-batteriespeichern/
            custom_battery_capacity_generic_in_kilowatt_hour=10,  # size/capacity of battery should be approx. the same as default pv power
            custom_pv_inverter_power_generic_in_watt=10
            * 0.5
            * 1e3,  # c-rate is 0.5C (0.5/h) here
            source_weight=1,
            system_id="SG1",
        )
        return config


class Battery(Component):
    """
    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) Energy Management System
    """

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"  # W

    # Outputs
    AcBatteryPower = "AcBatteryPower"  # W
    DcBatteryPower = "DcBatteryPower"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: BatteryConfig
    ):
        """
        Loads the parameters of the specified battery storage.
        """
        self.battery_config = config
        super().__init__(
            name=self.battery_config.name
            + "_w"
            + str(self.battery_config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.custom_pv_inverter_power_generic_in_watt = (
            self.battery_config.custom_pv_inverter_power_generic_in_watt
        )

        self.custom_battery_capacity_generic_in_kilowatt_hour = (
            self.battery_config.custom_battery_capacity_generic_in_kilowatt_hour
        )

        # Component has states
        self.state = BatteryState()
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.ac_coupled_battery_object = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.custom_pv_inverter_power_generic_in_watt,
            e_bat_custom=self.custom_battery_capacity_generic_in_kilowatt_hour,
        )

        # Define component inputs
        self.loading_power_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.LoadingPowerInput,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.ac_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.BATTERY,
            ],
            output_description=f"here a description for {self.AcBatteryPower} will follow.",
        )

        self.dc_battery_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description=f"here a description for {self.DcBatteryPower} will follow.",
        )

        self.state_of_charge_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description=f"here a description for {self.StateOfCharge} will follow.",
        )

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
        time_increment_in_seconds = self.my_simulation_parameters.seconds_per_timestep

        # Load input values
        set_point_for_ac_battery_power_in_watt = stsv.get_input_value(
            self.loading_power_input_channel
        )
        state_of_charge = self.state.state_of_charge

        # Simulate on timestep
        results = self.ac_coupled_battery_object.simulate(
            p_load=set_point_for_ac_battery_power_in_watt,
            soc=state_of_charge,
            dt=time_increment_in_seconds,
        )
        ac_battery_power_in_watt = results[0]
        dc_battery_power_in_watt = results[1]
        state_of_charge = results[2]

        # write values for output time series
        stsv.set_output_value(self.ac_battery_power_channel, ac_battery_power_in_watt)
        stsv.set_output_value(self.dc_battery_power_channel, dc_battery_power_in_watt)
        stsv.set_output_value(self.state_of_charge_channel, state_of_charge)

        # write values to state
        self.state.state_of_charge = state_of_charge

    def write_to_report(self) -> List[str]:
        return self.battery_config.get_string_dict()


@dataclass
class BatteryState:
    #: state of charge of the battery
    state_of_charge: float = 0

    def clone(self):
        "Creates a copy of the Battery State."
        return BatteryState(state_of_charge=self.state_of_charge)
