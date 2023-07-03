"""Implementation of shiftable household devices like washing machines, dish washers or dryers. Takes load profiles and time windows, where the activation can be shifted within from LoadProfileGenerator and
activates the device when surplus from PV is available. The device is activated at the end of the time window when no surplus was available. This file contains the class SmartDevice and SmartDevice State,
the configuration is automatically adopted from the information provided by the LPG. """

# Generic/Built-in
import json
import math as ma
from os import path
from typing import List, Tuple

import pandas as pd
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

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
class SmartDeviceConfig(cp.ConfigBase):

    """Configuration of the smart device."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SmartDevice.get_full_classname()

    name: str
    identifier: str
    source_weight: int
    smart_devices_included: bool

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return SmartDeviceConfig(
            name="Smart Device",
            identifier="Identifier",
            source_weight=1,
            smart_devices_included=True,
        )


class SmartDeviceState:
    """State representing smart appliance."""

    def __init__(
        self,
        actual_power: float = 0,
        timestep_of_activation: int = -999,
        time_to_go: int = 0,
        position: int = 0,
    ):
        """Initilization of state.

        :param actual_power: power of smart appliance at given timestep, defaults to 0
        :type actual_power: float, optional
        :param timestep_of_activation: timestep, where the device was activated, defaults to -999
        :type timestep_of_activation: int, optional
        :param time_to_go: duration of the power profile, which follows for the nex time steps, defaults to 0
        :type time_to_go: int, optional
        :param position: index of demand profile relevent for the given timestep, defaults to 0
        :type position: int, optional
        """
        self.actual_power = actual_power
        self.timestep_of_activation = timestep_of_activation
        self.time_to_go = time_to_go
        self.position = position

    def clone(self) -> "SmartDeviceState":
        """Copy state efficiently."""
        return SmartDeviceState(
            self.actual_power,
            self.timestep_of_activation,
            self.time_to_go,
            self.position,
        )

    def run(self, timestep: int, electricity_profile: List[float]) -> None:
        """Check device state based on previous time step.

        :param timestep: timestep of simulation
        :type timestep: int
        :param electricity_profile: load profile of device for actual or next activation
        :type electricity_profile: List[float]
        """
        # device activation
        if timestep > self.timestep_of_activation + self.time_to_go:
            self.timestep_of_activation = timestep
            self.time_to_go = len(electricity_profile)
            self.actual_power = electricity_profile[0]

        if timestep < self.timestep_of_activation + self.time_to_go:
            # device is running
            self.actual_power = electricity_profile[
                timestep - self.timestep_of_activation
            ]

        # device deactivation
        if timestep == self.timestep_of_activation + self.time_to_go:
            self.position = self.position + 1
            self.time_to_go = 0
            self.actual_power = 0


class SmartDevice(cp.Component):
    """
    Class component that provides availablity and profiles of flexible smart devices like shiftable (in time) washing machines and dishwashers.
    Data provided or based on LPG exports.
    """

    # mandatory Inputs
    l3_DeviceActivation = "l3_DeviceActivation"

    # mandatory Outputs
    ElectricityOutput = "ElectricityOutput"

    # optional Inputs
    ElectricityTarget = "ElectricityTarget"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: SmartDeviceConfig
    ):
        super().__init__(
            name=config.identifier.replace("/", "-") + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.build(
            identifier=config.identifier,
            source_weight=config.source_weight,
            seconds_per_timestep=my_simulation_parameters.seconds_per_timestep,
        )
        self.previous_state: SmartDeviceState
        self.state: SmartDeviceState
        self.consumption = 0
        if my_simulation_parameters.surplus_control and config.smart_devices_included:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                lt.ComponentType.SMART_DEVICE,
            ]
        else:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
            ]

        # mandatory Output
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=postprocessing_flag,
            output_description="Electricity output",
        )

        self.ElectricityTargetC: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityTarget,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Iteration in smart appliance like washing mashine, dish washer or dryer.

        :param timestep: timestep of simulation
        :type timestep: int
        :param stsv: _description_
        :type stsv: cp.SingleTimeStepValues
        :param force_convergence: _description_
        :type force_convergence: bool
        """

        # initialize power
        self.state.actual_power = 0

        # if not already running: check if activation makes sense
        if timestep > self.state.timestep_of_activation + self.state.time_to_go:
            if timestep > self.earliest_start[self.state.position]:  # can be turnod on
                # initialize next activation
                activation: float = timestep + 10
                # if surplus controller is connected get related signal
                if self.ElectricityTargetC.source_output is not None:
                    electricity_target = stsv.get_input_value(self.ElectricityTargetC)
                    if (
                        electricity_target
                        >= self.electricity_profile[self.state.position][0]
                    ):
                        activation = timestep
                # if last possible switch on force activation
                if (
                    timestep >= self.latest_start[self.state.position]
                ):  # needs to be activated
                    activation = timestep

                if timestep == activation:
                    self.state.run(
                        timestep, self.electricity_profile[self.state.position]
                    )

        # run device if it was already activated
        else:
            self.state.run(timestep, self.electricity_profile[self.state.position])

        stsv.set_output_value(self.ElectricityOutputC, self.state.actual_power)

    def build(
        self, identifier: str, source_weight: int, seconds_per_timestep: int = 60
    ) -> None:
        """Initialization of Smart Device information

        :param identifier: name of smart device in LPG
        :type identifier: str
        :param source_weight: priority of smart device in Energy Management System
        :type source_weight: int
        :param seconds_per_timestep: time step size, defaults to 60
        :type seconds_per_timestep: int, optional
        :raises NameError: _description_
        :raises TypeError: _description_
        """

        # load smart device profile
        smart_device_profile = []
        filepath = path.join(
            utils.HISIMPATH["utsp_reports"], "FlexibilityEvents.HH1.json"
        )
        with open(filepath, encoding="utf-8") as f:
            smart_device_profile = json.load(f)

        if not smart_device_profile:
            raise NameError(
                "LPG data for smart appliances is missing or located missleadingly"
            )

        # initializing relevant data
        earliest_start, latest_start, electricity_profile = [], [], []

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
                x = sample["EarliestStart"]["ExternalStep"]
                # skip if occurs in calibration days (negative sign )
                if x < 0:
                    continue
                # timestep (in minutes) the profile is shifted in the first step of the external time resolution
                offset = minutes_per_timestep - x % minutes_per_timestep
                # earliest start in given time resolution -> float value
                x = x / minutes_per_timestep
                # latest start in given time resolution
                y = sample["LatestStart"]["ExternalStep"] / minutes_per_timestep
                # number of timesteps in given time resolution -> integer value
                z = ma.ceil(
                    x + sample["TotalDuration"] / minutes_per_timestep
                ) - ma.floor(x)
                # earliest and latest start in new time resolution -> integer value
                earliest_start.append(ma.floor(x))
                latest_start.append(ma.ceil(y))

                # get shiftable load profile
                el = (
                    sample["Profiles"][2]["TimeOffsetInSteps"] * [0]
                    + sample["Profiles"][2]["Values"]
                )

                # average profiles given in 1 minute resolution to given time resolution
                elem_el = []
                # append first timestep which may not fill  the entire 15 minutes
                elem_el.append(sum(el[:offset]) / offset)

                for i in range(z - 2):
                    elem_el.append(
                        sum(
                            el[
                                offset
                                + minutes_per_timestep * i: offset
                                + (i + 1) * minutes_per_timestep
                            ]
                        )
                        / minutes_per_timestep
                    )

                last = el[offset + (i + 1) * minutes_per_timestep:]
                if offset != minutes_per_timestep:
                    elem_el.append(sum(last) / (minutes_per_timestep - offset))
                electricity_profile.append(elem_el)

        self.source_weight = source_weight
        self.earliest_start = earliest_start + [
            self.my_simulation_parameters.timesteps
        ]  # append value to continue simulation after last necesary run of flexible device at end of year
        self.latest_start = latest_start + [
            self.my_simulation_parameters.timesteps + 999
        ]  # append value to continue simulation after last necesary run of smart device at end of year
        self.electricity_profile = electricity_profile
        self.state = SmartDeviceState()
        self.previous_state = SmartDeviceState()

    def write_to_report(self) -> List[str]:
        """Writes relevant information to report."""
        lines: List[str] = []
        lines.append(f"DeviceName: {self.component_name}")
        lines.append(f"Consumption: {self.consumption:.2f}")
        return lines

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame, ) -> Tuple[float, float]:
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.ELECTRICITY:
                co2_per_unit = 0.4
                euro_per_unit = 0.25
                self.consumption = sum(postprocessing_results.iloc[:, index]) * self.my_simulation_parameters.seconds_per_timestep / 3.6e6
        return self.consumption * euro_per_unit, self.consumption * co2_per_unit
