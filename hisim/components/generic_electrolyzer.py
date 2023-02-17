# -*- coding: utf-8 -*-
"""
Created on Tue Jul  5 12:39:29 2022

@author: Johanna
"""

from dataclasses import dataclass

# Owned
from typing import List, Any

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim import utils
from hisim.components import generic_hydrogen_storage
from hisim.components.configuration import PhysicsConfig
from hisim.simulationparameters import SimulationParameters

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = ""


#


@dataclass_json
@dataclass
class GenericElectrolyzerConfig:
    name: str
    source_weight: int
    min_power: float  # [W]
    max_power: float  # [W]
    min_hydrogen_production_rate_hour: float  # [Nl/h]
    max_hydrogen_production_rate_hour: float  # [Nl/h]

    def __init__(
        self,
        name: str,
        source_weight: int,
        min_power: float,
        max_power: float,
        min_hydrogen_production_rate_hour: float,
        max_hydrogen_production_rate_hour: float,
    ) -> None:
        self.name = name
        self.source_weight = source_weight
        self.min_power = min_power
        self.max_power = max_power
        self.min_hydrogen_production_rate_hour = min_hydrogen_production_rate_hour
        self.max_hydrogen_production_rate_hour = max_hydrogen_production_rate_hour

    @staticmethod
    def get_default_config() -> Any:
        config = GenericElectrolyzerConfig(
            name="Electrolyzer",
            source_weight=1,
            min_power=1200,  # [W]
            max_power=2400,  # [W]
            min_hydrogen_production_rate_hour=300,  # [Nl/h]
            max_hydrogen_production_rate_hour=5000,  # [Nl/h]
        )
        return config


class ElectrolyzerState:
    """
    This data class saves the state of the electrolyzer.
    """

    def __init__(self, hydrogen: float = 0, electricity: float = 0):
        self.hydrogen = hydrogen
        self.electricity = electricity

    def clone(self) -> Any:
        return ElectrolyzerState(hydrogen=self.hydrogen, electricity=self.electricity)


class GenericElectrolyzer(cp.Component):
    ElectricityTarget = "ElectricityTarget"
    HydrogenOutput = "HydrogenOutput"

    ElectricityOutput = "ElectricityOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericElectrolyzerConfig,
    ):
        """
        The electrolyzer converts electrical energy [kWh] into hydrogen [kg]
        It can work in a certain range from x to 100% or be switched off = 0%
        The conversion rate is given by the supplier and is directly used
            maybe a change to efficiency can be made but its just making things more complex with no benefit
        Between the given values, the values are calculated by an interpolation.
            --> If the load curve is linear a fixed factor could be calculated.

        Therefore it has an operational state
        All the min values and  all the max values are connected and the electrolyzer can operate between them.

        The waste energy in electolyzers is not used to provide heat for the households demand
        Output pressure may be used in the future for the
        """

        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.build(config)
        self.min_hydrogen_production_rate: float
        self.ElectricityTargetC: cp.ComponentInput = self.add_input(
            self.component_name,
            GenericElectrolyzer.ElectricityTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        self.HydrogenOutputC: cp.ComponentOutput = self.add_output(
            self.component_name,
            GenericElectrolyzer.HydrogenOutput,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
        )
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=GenericElectrolyzer.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                lt.ComponentType.ELECTROLYZER,
            ],
        )
        self.add_default_connections(
            self.get_default_connections_from_L1GenericElectrolyzerController()
        )
        self.state: ElectrolyzerState
        self.previous_state: ElectrolyzerState

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def get_default_connections_from_L1GenericElectrolyzerController(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information("setting l1 default connections in generic electrolyzer")
        connections: List[cp.ComponentConnection] = []
        controller_classname = L1GenericElectrolyzerController.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericElectrolyzer.ElectricityTarget,
                controller_classname,
                L1GenericElectrolyzerController.ElectricityTarget,
            )
        )
        return connections

    def build(self, config: GenericElectrolyzerConfig) -> None:
        self.state = ElectrolyzerState()
        self.previous_state = ElectrolyzerState()

        self.name = config.name
        self.source_weight = config.source_weight
        self.min_power = config.min_power
        self.max_power = config.max_power
        self.min_hydrogen_production_rate = (
            config.min_hydrogen_production_rate_hour / 3600
        )
        self.max_hydrogen_production_rate = (
            config.max_hydrogen_production_rate_hour / 3600
        )

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            pass

        electricity_target = stsv.get_input_value(self.ElectricityTargetC)
        if electricity_target < 0:
            raise ValueError("Target electricity needs to be positive in Electrolyzer")
        if 0 <= electricity_target < self.min_power:
            self.state.electricity = 0
            electricity_target = 0
        elif electricity_target > self.max_power:
            self.state.electricity = self.max_power
        else:
            self.state.electricity = electricity_target

        # interpolation between points
        # Nl / s
        hydrogen_output_liter: float
        if electricity_target == 0:
            hydrogen_output_liter = 0
        else:
            hydrogen_output_liter = self.min_hydrogen_production_rate + (
                (self.max_hydrogen_production_rate - self.min_hydrogen_production_rate)
                * (self.state.electricity - self.min_power)
                / (self.max_power - self.min_power)
            )
        self.state.hydrogen = (
            hydrogen_output_liter / 1000
        ) * PhysicsConfig.hydrogen_density
        stsv.set_output_value(self.HydrogenOutputC, self.state.hydrogen)
        stsv.set_output_value(self.ElectricityOutputC, self.state.electricity)

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.name + str(self.source_weight)))
        lines.append("electrical power: {:4.0f} kW".format(self.max_power))
        lines.append(
            "hydrogen production rate: {:4.0f} kg / s".format(
                self.max_hydrogen_production_rate
            )
        )
        return lines


@dataclass_json
@dataclass
class L1ElectrolyzerConfig:
    """
    L1Electrolyzer Config
    """

    name: str
    source_weight: int
    min_operation_time: int
    min_idle_time: int
    P_min_electrolyzer: float
    SOC_max_H2: float

    def __init__(
        self,
        name: str,
        source_weight: int,
        min_operation_time: int,
        min_idle_time: int,
        P_min_electrolyzer: float,
        SOC_max_H2: float,
    ) -> None:
        self.name = name
        self.source_weight = source_weight
        self.min_operation_time = min_operation_time
        self.min_idle_time = min_idle_time
        self.P_min_electrolyzer = P_min_electrolyzer
        self.SOC_max_H2 = SOC_max_H2

    @staticmethod
    def get_default_config() -> Any:
        config = L1ElectrolyzerConfig(
            name="L1ElectrolyzerRuntimeController",
            source_weight=1,
            min_operation_time=14400,
            min_idle_time=7200,
            P_min_electrolyzer=1200,
            SOC_max_H2=96,
        )
        return config


class L1ElectrolyzerControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__(
        self,
        timestep_actual: int = -1,
        state: int = 0,
        timestep_of_last_action: int = 0,
    ) -> None:
        self.timestep_actual = timestep_actual
        self.state = state
        self.timestep_of_last_action = timestep_of_last_action

    def clone(self) -> Any:
        return L1ElectrolyzerControllerState(
            timestep_actual=self.timestep_actual,
            state=self.state,
            timestep_of_last_action=self.timestep_of_last_action,
        )

    def is_first_iteration(self, timestep: int) -> bool:
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            return True
        return False

    def activation(self, timestep: int) -> None:
        self.state = 1
        self.timestep_of_last_action = timestep

    def deactivation(self, timestep: int) -> None:
        self.state = 0
        self.timestep_of_last_action = timestep


class L1GenericElectrolyzerController(cp.Component):
    """
    L1 CHP Controller. It takes care of the operation of the CHP only in terms of running times.

    Parameters
    --------------
    min_running_time: int, optional
        Minimal running time of device, in seconds. The default is 3600 seconds.
    min_idle_time : int, optional
        Minimal off time of device, in seconds. The default is 900 seconds.
    source_weight : int, optional
        Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
    component type : str, optional
        Name of component to be controlled
    """

    # Inputs
    l2_ElectricityTarget = "l2_ElectricityTarget"
    HydrogenSOC = "HydrogenSOC"

    # Outputs
    ElectricityTarget = "ElectricityTarget"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: L1ElectrolyzerConfig,
    ) -> None:

        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )

        self.build(config)
        self.state0: L1ElectrolyzerControllerState
        self.state: L1ElectrolyzerControllerState
        self.previous_state: L1ElectrolyzerControllerState
        # add inputs
        self.l2_ElectricityTargetC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.l2_ElectricityTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )
        self.HydrogenSOCC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenSOC,
            lt.LoadTypes.HYDROGEN,
            lt.Units.PERCENT,
            mandatory=True,
        )
        # add outputs
        self.ElectricityTargetC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
        )

        self.add_default_connections(
            self.get_default_connections_from_hydrogenstorage()
        )

    def get_default_connections_from_hydrogenstorage(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information(
            "setting generic H2 storage default connections in L1 of generic electrolyzer"
        )
        connections: List[cp.ComponentConnection] = []
        h2storage_classname = (
            generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1GenericElectrolyzerController.HydrogenSOC,
                h2storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self, config: L1ElectrolyzerConfig) -> None:

        self.on_time = int(
            config.min_operation_time
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.off_time = int(
            config.min_idle_time / self.my_simulation_parameters.seconds_per_timestep
        )
        self.name = config.name
        self.source_weight = config.source_weight
        self.Pmin = config.P_min_electrolyzer
        self.SOCmax = config.SOC_max_H2

        self.state0 = L1ElectrolyzerControllerState()
        self.state = L1ElectrolyzerControllerState()
        self.previous_state = L1ElectrolyzerControllerState()

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:

        l2_electricity_target = stsv.get_input_value(self.l2_ElectricityTargetC)
        H2_SOC = stsv.get_input_value(self.HydrogenSOCC)

        # save reference state state0 in first iteration
        if self.state.is_first_iteration(timestep):
            self.state0 = self.state.clone()

        # return device on if minimum operation time is not fulfilled and device was on in previous state
        if (
            self.state0.state == 1
            and self.state0.timestep_of_last_action + self.on_time >= timestep
        ):
            self.state.state = 1
            l2_electricity_target = max(
                self.Pmin, l2_electricity_target
            )  # return device off if minimum idle time is not fulfilled and device was off in previous state
        elif (
            self.state0.state == 0
            and self.state0.timestep_of_last_action + self.off_time >= timestep
        ):
            self.state.state = 0
            l2_electricity_target = 0

        # catch cases where hydrogen storage is close to maximum level and signals oscillate -> just turn off electrolyzer
        elif force_convergence:
            if self.state0.state == 0:
                self.state.state = 0
            else:
                self.state.deactivation(timestep)
            l2_electricity_target = 0
            pass

        # set point control
        else:
            # if target electricity is too low or hydrogen storage too full: turn off
            if (
                (l2_electricity_target < self.Pmin) or (H2_SOC > self.SOCmax)
            ) and self.state0.state == 1:
                self.state.deactivation(timestep)
            # turns on if electricity is high enough and there is still space in storage, only works if being off is not compulsory
            elif (
                (l2_electricity_target >= self.Pmin) and (H2_SOC <= self.SOCmax)
            ) and self.state0.state == 0:
                self.state.activation(timestep)
            else:
                pass

        stsv.set_output_value(
            self.ElectricityTargetC, self.state.state * l2_electricity_target
        )

    def prin1t_outpu1t(self, t_m: float, state: L1ElectrolyzerControllerState) -> None:
        log.information("==========================================")
        log.information("T m: {}".format(t_m))
        log.information("State: {}".format(state))

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("L1 Controller Electrolyzer: " + self.component_name)
        return lines
