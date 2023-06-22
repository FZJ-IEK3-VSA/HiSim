from dataclasses import dataclass
from typing import List, Any

from dataclasses_json import dataclass_json
from hisim import utils
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import controller_l2_generic_heat_simple
from hisim.components import generic_hydrogen_storage
from hisim.simulationparameters import SimulationParameters

__authors__ = "Frank Burkrad, Maximilian Hillen,"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"


@dataclass_json
@dataclass
class GCHPConfig(cp.ConfigBase):
    """
    GCHP Config
    """

    name: str
    source_weight: int
    p_el: float
    p_th: float
    p_fuel: float


    @staticmethod
    def get_default_config() -> Any:
        config = GCHPConfig(
            name="CHP", source_weight=1, p_el=2000, p_th=3000, p_fuel=6000
        )
        return config


class GenericCHPState:
    """
    This data class saves the state of the CHP.
    """

    def __init__(self, state: float = 0) -> None:
        self.state: float = state

    def clone(self) -> Any:
        return GenericCHPState(state=self.state)


class GCHP(cp.Component):
    """
    Simulates CHP operation with constant electical and thermal power as well as constant fuel consumption.
    """

    # Inputs
    L1DeviceSignal = "L1DeviceSignal"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ElectricityOutput = "ElectricityOutput"
    FuelDelivered = "FuelDelivered"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: GCHPConfig
    ) -> None:
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.build(config)

        # Inputs
        self.L1DeviceSignalC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.L1DeviceSignal,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            mandatory=True,
        )

        # Component outputs
        self.ThermalPowerDeliveredC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDelivered,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power Delivered",
        )
        self.ElectricityOutputC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.FUEL_CELL,
            ],
            output_description="Electricity Output",
        )
        self.FuelDeliveredC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.FuelDelivered,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Fuel Delivered",
        )
        self.add_default_connections(
            self.get_default_connections_from_l1_generic_chp_runtime_controller()
        )
        self.state: GenericCHPState
        self.previous_state: GenericCHPState

    def build(self, config: GCHPConfig) -> None:
        self.state = GenericCHPState()
        self.previous_state = GenericCHPState()
        self.name = config.name
        self.source_weight = config.source_weight
        self.p_th = config.p_th
        self.p_el = config.p_el
        self.p_fuel = config.p_fuel * 1e-8 / 1.41  # converted to kg / s

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        # Inputs
        self.state.state = stsv.get_input_value(self.L1DeviceSignalC)

        # Outputs
        stsv.set_output_value(self.ThermalPowerDeliveredC, self.state.state * self.p_th)
        stsv.set_output_value(self.ElectricityOutputC, self.state.state * self.p_el)

        # heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
        stsv.set_output_value(self.FuelDeliveredC, self.state.state * self.p_fuel)

    def get_default_connections_from_l1_generic_chp_runtime_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information("setting l1 default connections in generic CHP")
        connections: List[cp.ComponentConnection] = []
        controller_classname = L1GenericCHPRuntimeController.get_classname()
        connections.append(
            cp.ComponentConnection(
                GCHP.L1DeviceSignal,
                controller_classname,
                L1GenericCHPRuntimeController.L1DeviceSignal,
            )
        )
        return connections

    def write_to_report(self):
        lines = []
        lines.append(
            "CHP operation with constant electical and thermal power: {}".format(
                self.name + str(self.source_weight)
            )
        )
        lines.append("P_el {:4.0f} kW".format(self.p_el))
        lines.append("P_th {:4.0f} kW".format(self.p_th))
        return lines


@dataclass_json
@dataclass
class L1CHPConfig(cp.ConfigBase):
    """
    L1CHP Config
    """

    name: str
    source_weight: int
    min_operation_time: int
    min_idle_time: int
    min_h2_soc: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return L1GenericCHPRuntimeController.get_full_classname()

    @classmethod
    def get_default_config(cls) -> Any:
        config = L1CHPConfig(
            name="L1CHPRunTimeController",
            source_weight=1,
            min_operation_time=14400,
            min_idle_time=7200,
            min_h2_soc=5,
        )
        return config


class L1GenericCHPControllerState:
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
        return L1GenericCHPControllerState(
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


class L1GenericCHPRuntimeController(cp.Component):
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
    l2_DeviceSignal = "l2_DeviceSignal"
    ElectricityTarget = "ElectricityTarget"
    HydrogenSOC = "HydrogenSOC"

    # Outputs
    L1DeviceSignal = "L1DeviceSignal"

    # Similar components to connect to:
    # 1. Building
    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: L1CHPConfig
    ):

        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.build(config)

        # add inputs
        self.l2_DeviceSignalC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.l2_DeviceSignal,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            mandatory=True,
        )
        self.ElectricityTargetC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityTarget,
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

        self.add_default_connections(
            self.get_default_connections_from_l2_generic_heat_controller()
        )
        self.add_default_connections(
            self.get_default_connections_from_generic_hydrogen_storage()
        )

        # add outputs
        self.L1DeviceSignalC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.L1DeviceSignal,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            output_description="L1 Device Signal C",
        )
        self.state0: L1GenericCHPControllerState
        self.state: L1GenericCHPControllerState
        self.previous_state: L1GenericCHPControllerState

    def get_default_connections_from_l2_generic_heat_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information("setting l2 default connections in l1")
        connections: List[cp.ComponentConnection] = []
        controller_classname = (
            controller_l2_generic_heat_simple.L2GenericHeatController.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1GenericCHPRuntimeController.l2_DeviceSignal,
                controller_classname,
                controller_l2_generic_heat_simple.L2GenericHeatController.l2_device_signal,
            )
        )
        return connections

    def get_default_connections_from_generic_hydrogen_storage(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information(
            "setting generic H2 storage default connections in L1 of generic CHP"
        )
        connections: List[cp.ComponentConnection] = []
        h2storage_classname = (
            generic_hydrogen_storage.GenericHydrogenStorage.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                L1GenericCHPRuntimeController.HydrogenSOC,
                h2storage_classname,
                generic_hydrogen_storage.GenericHydrogenStorage.HydrogenSOC,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self, config: L1CHPConfig) -> None:
        self.on_time = int(
            config.min_operation_time
            / self.my_simulation_parameters.seconds_per_timestep
        )
        self.off_time = int(
            config.min_idle_time / self.my_simulation_parameters.seconds_per_timestep
        )
        self.SOCmin = config.min_h2_soc
        self.name = config.name
        self.source_weight = config.source_weight

        self.state0 = L1GenericCHPControllerState()
        self.state = L1GenericCHPControllerState()
        self.previous_state = L1GenericCHPControllerState()

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

        l2_devicesignal = stsv.get_input_value(self.l2_DeviceSignalC)
        electricity_target = stsv.get_input_value(self.ElectricityTargetC)
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
        # return device off if minimum idle time is not fulfilled and device was off in previous state
        elif (
            self.state0.state == 0
            and self.state0.timestep_of_last_action + self.off_time >= timestep
        ):
            self.state.state = 0  # catch cases where hydrogen storage is close to maximum level and signals oscillate -> just turn off electrolyzer
        elif force_convergence:
            if self.state0.state == 0:
                self.state.state = 0
            else:
                self.state.deactivation(timestep)
            electricity_target = 0
        # check signal from l2 and turn on or off if it is necesary
        else:
            if (
                (l2_devicesignal == 0)
                or (electricity_target <= 0)
                or (H2_SOC < self.SOCmin)
            ) and self.state0.state == 1:
                self.state.deactivation(timestep)
            elif (
                (l2_devicesignal == 1)
                and (electricity_target > 0)
                and (H2_SOC >= self.SOCmin)
            ) and self.state0.state == 0:
                self.state.activation(timestep)

        stsv.set_output_value(self.L1DeviceSignalC, self.state.state)

    def prin1t_outpu1t(self, t_m: float, state: L1GenericCHPControllerState) -> None:
        log.information("==========================================")
        log.information(f"T m: {t_m}")
        log.information(f"State: {state}")

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("Generic CHP L1 Controller: " + self.component_name)
        return lines
