# -*- coding: utf-8 -*-
# clean
from dataclasses import dataclass
from typing import Any, List

from dataclasses_json import dataclass_json

import hisim.utils as utils
from hisim.component import ConfigBase
from hisim import component as cp
from hisim import log
from hisim.components import controller_l2_generic_heat_clever_simple
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class L1Config(ConfigBase):
    """ L1 Runtime Config """

    name: str
    source_weight: int
    min_operation_time_in_seconds: int
    min_idle_time_in_seconds: int

    @staticmethod
    def get_default_config(name: str) -> Any:
        """ Default config """
        config = L1Config(name='RuntimeController_' + name, source_weight=1, min_operation_time_in_seconds=3600, min_idle_time_in_seconds=900)
        return config

    @staticmethod
    def get_default_config_heatpump(name: str) -> Any:
        config = L1Config(name='L1RuntimeController' + name, source_weight=1, min_operation_time_in_seconds=3600 * 3, min_idle_time_in_seconds=3600)
        return config


class L1GenericRuntimeControllerState:
    """
    This data class saves the state of the controller.
    """

    def __init__(self, on_off: int, activation_time_step: int, deactivation_time_step: int) -> None:
        self.on_off: int = on_off
        self.activation_time_step: int = 0
        self.deactivation_time_step: int = 0

    def clone(self) -> Any:
        return L1GenericRuntimeControllerState(activation_time_step=self.activation_time_step, on_off=self.on_off,
                                               deactivation_time_step=self.deactivation_time_step)

    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass

    def activate(self, timestep: int) -> None:
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        self.on_off = 0
        self.deactivation_time_step = timestep


class L1GenericRuntimeController(cp.Component):
    """
    L1 Heat Pump Controller. It takes care of the operation of the heat pump only in terms of running times.
    It gets inputs from an L2-heat controller

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
    l2_DeviceSignal = "l2_DeviceSignal"

    # Outputs
    L1DeviceSignal = "L1DeviceSignal"
    l1_RunTimeSignal = "l1_RunTimeSignal"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(self, my_simulation_parameters: SimulationParameters, config: L1Config) -> None:

        super().__init__(name=config.name + '_w' + str(config.source_weight), my_simulation_parameters=my_simulation_parameters)
        self.config = config
        self.name = config.name
        self.source_weight = config.source_weight
        self.minimum_runtime_in_timesteps = int(config.min_operation_time_in_seconds / self.my_simulation_parameters.seconds_per_timestep)
        self.minimum_resting_time_in_timesteps = int(config.min_idle_time_in_seconds / self.my_simulation_parameters.seconds_per_timestep)

        self.state = L1GenericRuntimeControllerState(0, 0, 0)
        self.previous_state = self.state.clone()
        # add inputs
        self.l2_DeviceSignalC: cp.ComponentInput = self.add_input(self.component_name, self.l2_DeviceSignal, LoadTypes.ON_OFF, Units.BINARY,
                                                                  mandatory=True)
        self.add_default_connections(controller_l2_generic_heat_clever_simple.L2HeatSmartController, self.get_l2_controller_default_connections())

        # add outputs
        self.L1DeviceSignalC: cp.ComponentOutput = self.add_output(self.component_name, self.L1DeviceSignal, LoadTypes.ON_OFF, Units.BINARY)

        if self.my_simulation_parameters.system_config.predictive == True:
            self.l1_RunTimeSignalC: cp.ComponentOutput = self.add_output(self.component_name, self.l1_RunTimeSignal, LoadTypes.ANY, Units.ANY)

    def get_l2_controller_default_connections(self) -> List[cp.ComponentConnection]:
        """ Makes default connections to l2 smart controllers. """
        log.information("setting l2 default connections in l1")
        connections = []
        controller_classname = controller_l2_generic_heat_clever_simple.L2HeatSmartController.get_classname()
        connections.append(cp.ComponentConnection(L1GenericRuntimeController.l2_DeviceSignal, controller_classname,
                                                  controller_l2_generic_heat_clever_simple.L2HeatSmartController.l2_DeviceSignal))
        return connections

    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass

    def i_save_state(self) -> None:
        """ Saves the current state for the next timestep. """
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """ Restores the previous state. """
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """ For checking for problems. """
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """ Main simulation function. """
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        if force_convergence:
            return

        l2_devicesignal = stsv.get_input_value(self.l2_DeviceSignalC)

        # return device on if minimum operation time is not fulfilled and device was on in previous state
        if self.state.on_off == 1 and self.state.activation_time_step + self.minimum_runtime_in_timesteps >= timestep:
            # mandatory on, minimum runtime not reached
            self.state.on_off = 1
            pass
        elif self.state.on_off == 0 and self.state.deactivation_time_step + self.minimum_resting_time_in_timesteps >= timestep:
            self.state.on_off = 0
        # check signal from l2 and turn on or off if it is necesary
        else:
            if l2_devicesignal == 0 and self.state.on_off == 1:
                self.state.deactivate(timestep)
            elif l2_devicesignal == 1 and self.state.on_off == 0:
                self.state.activate(timestep)
        stsv.set_output_value(self.L1DeviceSignalC, self.state.on_off)

    def write_to_report(self) -> List[str]:
        """ Writes config to report. """
        lines: List[str] = []
        lines.append("Generic Controller L1: " + self.component_name)
        lines.extend(self.config.get_string_dict())
        return lines
