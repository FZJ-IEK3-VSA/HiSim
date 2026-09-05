"""Implementation of shiftable household devices like washing machines, dish washers or dryers.

Takes load profiles and time windows, where the activation can be shifted within from LoadProfileGenerator and activates the device when surplus from PV is available.
The device is activated at the end of the time window when no surplus was available. This file contains the class SmartDevice and SmartDevice State,
the configuration is automatically adopted from the information provided by the LPG.
"""

# clean

# Generic/Built-in
import json
import math as ma
from os import path
from enum import Enum
from typing import Optional, Any, ClassVar, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd


# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters
from hisim.component import OpexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class SmartDeviceConfig(ConfigBase):
    """Configuration of the smart device."""

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return SmartDevice.get_full_classname()

    component_id: ComponentID
    identifier: str
    source_weight: int
    smart_devices_included: bool

    @classmethod
    def get_default_config(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "SmartDeviceConfig":
        """Gets a default config."""
        if component_id is None:
            component_id = ComponentID(name="Smart Device")
        return SmartDeviceConfig(
            component_id=component_id,
            identifier="Identifier",
            source_weight=1,
            smart_devices_included=True,
        )


class SmartDeviceState:
    """State representing smart appliance."""

    def __init__(
        self,
        actual_power_in_watt: float = 0,
        timestep_of_activation: int = -999,
        time_to_go: int = 0,
        profile_index: int = 0,
    ) -> None:
        """Initilization of state.

        :param actual_power_in_watt: power of smart appliance at given timestep, defaults to 0
        :type actual_power_in_watt: float, optional
        :param timestep_of_activation: timestep, where the device was activated, defaults to -999
        :type timestep_of_activation: int, optional
        :param time_to_go: duration of the power profile, which follows for the nex time steps, defaults to 0
        :type time_to_go: int, optional
        :param profile_index: index of demand profile relevent for the given timestep, defaults to 0
        :type profile_index: int, optional
        """
        self.actual_power_in_watt: float = actual_power_in_watt
        self.timestep_of_activation: int = timestep_of_activation
        self.time_to_go: int = time_to_go
        self.profile_index: int = profile_index

    def clone(self) -> "SmartDeviceState":
        """Copy state efficiently."""
        return SmartDeviceState(
            self.actual_power_in_watt,
            self.timestep_of_activation,
            self.time_to_go,
            self.profile_index,
        )

    def run(self, timestep: int, electricity_profile_in_watt: List[float]) -> None:
        """Check device state based on previous time step.

        :param timestep: timestep of simulation
        :type timestep: int
        :param electricity_profile_in_watt: load profile of device for actual or next activation
        :type electricity_profile_in_watt: List[float]
        """
        # device activation
        if timestep > self.timestep_of_activation + self.time_to_go:
            self.timestep_of_activation = timestep
            self.time_to_go = len(electricity_profile_in_watt)
            self.actual_power_in_watt = electricity_profile_in_watt[0]

        if timestep < self.timestep_of_activation + self.time_to_go:
            # device is running
            self.actual_power_in_watt = electricity_profile_in_watt[timestep - self.timestep_of_activation]

        # device deactivation
        if timestep == self.timestep_of_activation + self.time_to_go:
            self.profile_index += 1
            self.time_to_go = 0
            self.actual_power_in_watt = 0


class SmartDevice(cp.Component):
    """Smart device class.

    Class component that provides availablity and profiles of flexible smart devices like shiftable (in time) washing machines and dishwashers.
    Data provided or based on LPG exports.
    """

    # mandatory Inputs
    L3DeviceActivation: ClassVar[str] = "l3_DeviceActivation"

    # mandatory Outputs
    ElectricityOutput: ClassVar[str] = "ElectricityOutput"

    # optional Inputs
    ElectricityTarget: ClassVar[str] = "ElectricityTarget"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SmartDeviceConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the smart device component.

        Args:
            my_simulation_parameters: Simulation parameters for the current run.
            config: Configuration dataclass specifying the smart device identifier,
                source weight, and whether smart devices are included.
            my_display_config: Display configuration for the component.
        """

        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: SmartDeviceConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build(
            identifier=config.identifier,
            source_weight=config.source_weight,
            seconds_per_timestep=my_simulation_parameters.seconds_per_timestep,
        )
        self.previous_state: SmartDeviceState
        self.state: SmartDeviceState
        self.consumption_in_kilowatt_hour: float = 0
        if my_simulation_parameters.surplus_control and config.smart_devices_included:
            postprocessing_flag: list[Enum] = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                lt.ComponentType.SMART_DEVICE,
            ]
        else:
            postprocessing_flag = [lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]

        # mandatory Output
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=postprocessing_flag,
            output_description="Electricity output",
        )

        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityTarget,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Perform validation checks after simulation step.

        This method is called after each simulation timestep to verify
        state consistency. Currently a no-op placeholder.

        :param timestep: Current simulation timestep.
        :type timestep: int
        :param stsv: Single time step values container.
        :type stsv: cp.SingleTimeStepValues
        """
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate one timestep of the smart device.

        Checks whether the device should be activated based on surplus
        availability and time windows, then advances the device state and
        writes the electricity output.

        Args:
            timestep: Current simulation timestep index.
            stsv: Container for reading input values and writing output values
                for the current timestep.
            force_convergence: Whether to force convergence in the iteration
                (unused by this component but required by the interface).
        """

        # initialize power
        self.state.actual_power_in_watt = 0

        # if not already running: check if activation makes sense
        if timestep > self.state.timestep_of_activation + self.state.time_to_go:
            if timestep > self.earliest_start[self.state.profile_index]:  # can be turnod on
                # initialize next activation
                activation_timestep: int = timestep + 10
                # if surplus controller is connected get related signal
                if self.electricity_target_channel.source_output is not None:
                    electricity_target_in_watt = stsv.get_input_value(self.electricity_target_channel)
                    if electricity_target_in_watt >= self.electricity_profile_in_watt[self.state.profile_index][0]:
                        activation_timestep = timestep
                # if last possible switch on force activation
                if timestep >= self.latest_start[self.state.profile_index]:  # needs to be activated
                    activation_timestep = timestep

                if timestep == activation_timestep:
                    self.state.run(timestep, self.electricity_profile_in_watt[self.state.profile_index])

        # run device if it was already activated
        else:
            self.state.run(timestep, self.electricity_profile_in_watt[self.state.profile_index])

        stsv.set_output_value(self.electricity_output_channel, self.state.actual_power_in_watt)

    def build(self, identifier: str, source_weight: int, seconds_per_timestep: int = 60) -> None:
        """Load and process smart device flexibility profiles from LPG output.

        Reads the FlexibilityEvents JSON exported by the LoadProfileGenerator,
        filters for the device matching `identifier`, and resamples the
        minute-resolution electricity profiles to the simulation time resolution.

        Args:
            identifier: Name of the smart device in the LPG output.
            source_weight: Priority of the smart device in the Energy Management System.
            seconds_per_timestep: Time step size in seconds. Defaults to 60.

        Raises:
            NameError: If the LPG flexibility data file is empty or not found.
            TypeError: If `seconds_per_timestep` is not a multiple of 60 seconds.
        """

        # load smart device profile
        smart_device_profile: list[dict[str, Any]] = []
        filepath = path.join(utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json")
        with open(filepath, encoding="utf-8") as file:
            smart_device_profile = json.load(file)

        if not smart_device_profile:
            raise NameError("LPG data for smart appliances is missing or located missleadingly")

        # initializing relevant data
        earliest_start: list[int] = []
        latest_start: list[int] = []
        electricity_profile_in_watt: list[list[float]] = []

        minutes_per_timestep = seconds_per_timestep / 60

        if not minutes_per_timestep.is_integer():
            raise TypeError(
                "Up to now smart appliances have only been implemented for time resolutions corresponding to multiples of one minute"
            )
        minutes_per_timestep = int(minutes_per_timestep)

        # reading in data from json file and adopting to given time resolution
        for sample in smart_device_profile:
            device_name = str(sample["Device"]["Name"])
            if device_name == identifier:
                # earliest start in given time resolution -> integer value
                earliest_start_timestep = sample["EarliestStart"]["ExternalStep"]
                # skip if occurs in calibration days (negative sign )
                if earliest_start_timestep < 0:
                    continue
                # timestep (in minutes) the profile is shifted in the first step of the external time resolution
                offset = minutes_per_timestep - earliest_start_timestep % minutes_per_timestep
                # earliest start in given time resolution -> float value
                earliest_start_timestep = earliest_start_timestep / minutes_per_timestep
                # latest start in given time resolution
                latest_start_timestep = sample["LatestStart"]["ExternalStep"] / minutes_per_timestep
                # number of timesteps in given time resolution -> integer value
                profile_duration_timesteps = ma.ceil(earliest_start_timestep + sample["TotalDuration"] / minutes_per_timestep) - ma.floor(earliest_start_timestep)
                # earliest and latest start in new time resolution -> integer value
                earliest_start.append(ma.floor(earliest_start_timestep))
                latest_start.append(ma.ceil(latest_start_timestep))

                # get shiftable load profile
                el_shiftable_load_in_watt: list[float] = sample["Profiles"][2]["TimeOffsetInSteps"] * [0] + sample["Profiles"][2]["Values"]

                # average profiles given in 1 minute resolution to given time resolution
                resampled_electricity_profile_in_watt: list[float] = []
                # append first timestep which may not fill  the entire 15 minutes
                resampled_electricity_profile_in_watt.append(sum(el_shiftable_load_in_watt[:offset]) / offset)

                i = 0
                for i in range(profile_duration_timesteps - 2):
                    resampled_electricity_profile_in_watt.append(
                        sum(
                            el_shiftable_load_in_watt[
                                offset + minutes_per_timestep * i : offset + (i + 1) * minutes_per_timestep
                            ]
                        )
                        / minutes_per_timestep
                    )

                remaining_load_values: list[float] = el_shiftable_load_in_watt[offset + (i + 1) * minutes_per_timestep :]
                if offset != minutes_per_timestep:
                    resampled_electricity_profile_in_watt.append(sum(remaining_load_values) / (minutes_per_timestep - offset))
                electricity_profile_in_watt.append(resampled_electricity_profile_in_watt)

        self.source_weight: int = source_weight
        earliest_start = earliest_start + [
            self.my_simulation_parameters.timesteps
        ]  # append value to continue simulation after last necesary run of flexible device at end of year
        self.earliest_start: List[int] = utils.convert_lpg_timestep_to_utc(
            data=earliest_start,
            year=self.my_simulation_parameters.year,
            seconds_per_timestep=seconds_per_timestep,
        )
        latest_start = latest_start + [
            self.my_simulation_parameters.timesteps + 999
        ]  # append value to continue simulation after last necesary run of smart device at end of year
        self.latest_start: List[int] = utils.convert_lpg_timestep_to_utc(
            data=latest_start,
            year=self.my_simulation_parameters.year,
            seconds_per_timestep=seconds_per_timestep,
        )
        self.electricity_profile_in_watt: List[List[float]] = electricity_profile_in_watt
        self.state = SmartDeviceState()
        self.previous_state = SmartDeviceState()

    def write_to_report(self) -> List[str]:
        """Writes relevant information to report."""
        lines: List[str] = []
        lines.append(f"DeviceName: {self.component_name}")
        lines.append(f"Consumption: {self.consumption_in_kilowatt_hour:.2f}")
        return lines

    def get_cost_opex(
        self,
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Get opex costs."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.ELECTRICITY:
                self.consumption_in_kilowatt_hour = (
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6
                )
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,  # TODO: add maintenance costs
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=self.consumption_in_kilowatt_hour,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.SMART_DEVICE
        )

        return opex_cost_data_class
