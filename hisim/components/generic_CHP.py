"""Simple implementation of combined heat and power plant (CHP).

This can be either a natural gas driven turbine producing both electricity and heat,
or also a fuel cell. In this implementation the CHP does not modulate: it is either
on or off. When it runs, it outputs a constant thermal and electrical power signal
and needs a constant input of hydrogen or natural gas."""

from dataclasses import dataclass
from typing import List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import controller_l1_chp
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
class CHPConfig(cp.ConfigBase):
    """Defininition of configuration of combined heat and power plant (CHP)."""
    #: name of the CHP
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of CHP (fuel cell or gas driven)
    use: lt.LoadTypes
    #: electrical power of the CHP, when activated
    p_el: float
    #: thermal power of the CHP in Watt, when activated
    p_th: float
    #: demanded power of fuel input in Watt, when activated
    p_fuel: float

    @staticmethod
    def get_default_config_chp(thermal_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.GAS, p_el=(0.33 / 0.5) * thermal_power, p_th=thermal_power, p_fuel=(1 / 0.5) * thermal_power
        )
        return config

    @staticmethod
    def get_default_config_fuelcell(thermal_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.HYDROGEN, p_el=(0.48 / 0.43) * thermal_power, p_th=thermal_power, p_fuel=(1 / 0.43) * thermal_power
        )
        return config


class GenericCHPState:
    """This data class saves the state of the CHP."""

    def __init__(self, state: int) -> None:
        self.state = state

    def clone(self) -> "GenericCHPState":
        return GenericCHPState(state=self.state)


class SimpleCHP(cp.Component):
    """Simulates CHP operation with constant electical and thermal power as well as constant fuel consumption.

    Components to connect to:
    (1) CHP or fuel cell controller (controller_l1_chp)
    """

    # Inputs
    CHPControllerOnOffSignal = "CHPControllerOnOffSignal"
    CHPControllerHeatingModeSignal = "CHPControllerHeatingModeSignal"

    # Outputs
    ThermalPowerOutputBuilding = "ThermalPowerOutputBuilding"
    ThermalPowerOutputBoiler = "ThermalPowerOutputBoiler"
    ElectricityOutput = "ElectricityOutput"
    FuelDelivered = "FuelDelivered"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: CHPConfig
    ) -> None:
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config
        )

        self.config = config
        if self.config.use == lt.LoadTypes.HYDROGEN:
            self.p_fuel = config.p_fuel / (3.6e3 * 3.939e4)  # converted to kg / s
        else:
            self.p_fuel = config.p_fuel * my_simulation_parameters.seconds_per_timestep / 3.6e3  # converted to Wh

        self.state = GenericCHPState(state=0)
        self.previous_state = self.state.clone()

        # Inputs
        self.chp_onoff_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CHPControllerOnOffSignal,
            lt.LoadTypes.ON_OFF,
            lt.Units.BINARY,
            mandatory=True,
        )

        self.chp_heatingmode_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CHPControllerHeatingModeSignal,
            lt.LoadTypes.ANY,
            lt.Units.BINARY,
            mandatory=True,
        )

        # Component outputs
        self.thermal_power_output_building_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutputBuilding,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP to building or buffer in Watt."
        )
        self.thermal_power_output_dhw_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutputBoiler,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP to drain hot water storage in Watt."
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.FUEL_CELL,
            ],
            output_description="Electrical Power output of CHP in Watt."
        )
        if self.config.use == lt.LoadTypes.HYDROGEN:
            self.fuel_consumption_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelDelivered,
                load_type=lt.LoadTypes.HYDROGEN,
                unit=lt.Units.KG_PER_SEC,
                postprocessing_flag=[
                    lt.LoadTypes.HYDROGEN
                ],
                output_description="Hydrogen consumption of CHP in kg / s.",
            )
        elif self.config.use == lt.LoadTypes.GAS:
            self.fuel_consumption_channel = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelDelivered,
                load_type=lt.LoadTypes.GAS,
                unit=lt.Units.WATT_HOUR,
                postprocessing_flag=[
                    lt.InandOutputType.FUEL_CONSUMPTION,
                    lt.LoadTypes.GAS
                ],
                output_description="Gas consumption of CHP in Wh.",
            )
        self.add_default_connections(self.get_default_connections_from_chp_controller())

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
        self.state.state = int(stsv.get_input_value(self.chp_onoff_signal_channel))
        mode = stsv.get_input_value(self.chp_heatingmode_signal_channel)

        # Outputs
        if mode == 0:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, self.state.state * self.config.p_th)
            stsv.set_output_value(self.thermal_power_output_building_channel, 0)
        elif mode == 1:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, 0)
            stsv.set_output_value(self.thermal_power_output_building_channel, self.state.state * self.config.p_th)

        stsv.set_output_value(self.electricity_output_channel, self.state.state * self.config.p_el)
        stsv.set_output_value(self.fuel_consumption_channel, self.state.state * self.p_fuel)

    def get_default_connections_from_chp_controller(self,) -> List[cp.ComponentConnection]:
        """Sets default connections of the controller in the Fuel Cell / CHP."""
        log.information("setting l1 default connections in generic CHP")
        connections: List[cp.ComponentConnection] = []
        controller_classname = controller_l1_chp.L1CHPController.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerOnOffSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerOnOffSignal,
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
            my_config=config
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
            output_description="L1 Device Signal C"
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
                SimpleCHP.CHPControllerHeatingModeSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerHeatingModeSignal,
            )
        )
        return connections

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
