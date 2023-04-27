"""Simple implementation of combined heat and power plant (CHP).

This can be either a natural gas driven turbine producing both electricity and heat,
or also a fuel cell. In this implementation the CHP does not modulate: it is either
on or off. When it runs, it outputs a constant thermal and electrical power signal
and needs a constant input of hydrogen or natural gas."""

from dataclasses import dataclass
from typing import List, Any

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
class CHPConfig:
    """Defininition of configuration of combined heat and power plant (CHP)."""
    #: name of the CHP
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of CHP (fuel cell or gas driven):
    use: lt.LoadTypes
    #: electrical power of the CHP, when activated
    p_el: float
    #: thermal power of the CHP, when activated
    p_th: float
    #: demanded power of fuel input, when activated
    p_fuel: float

    @staticmethod
    def get_default_config_chp(total_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.GAS, p_el=0.33 * total_power, p_th=0.5 * total_power, p_fuel=total_power
        )
        return config

    @staticmethod
    def get_default_config_fuelcell(total_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.HYDROGEN, p_el=0.48 * total_power, p_th=0.43 * total_power, p_fuel=total_power
        )
        return config


class GenericCHPState:
    """This data class saves the state of the CHP."""

    def __init__(self, state: int = 0) -> None:
        self.state = state

    def clone(self) -> "GenericCHPState":
        return GenericCHPState(state=self.state)


class CHP(cp.Component):
    """Simulates CHP operation with constant electical and thermal power as well as constant fuel consumption."""

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
        )

        self.config = config
        if self.config.use == lt.LoadTypes.HYDROGEN:
            self.p_fuel = config.p_fuel * 1e-8 / 1.41  # converted to kg / s
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
                self.component_name,
                self.FuelDelivered,
                lt.LoadTypes.HYDROGEN,
                lt.Units.KG_PER_SEC,
                output_description="Hydrogen consumption of CHP in kg / s."
            )
        if self.config.use == lt.LoadTypes.GAS:
            self.fuel_consumption_channel: cp.ComponentOutput = self.add_output(
                self.component_name,
                self.FuelDelivered,
                lt.LoadTypes.GAS,
                lt.Units.WATT_HOUR,
                output_description="Gas consumption of CHP in Wh."
            )

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
        self.state.state = stsv.get_input_value(self.chp_onoff_signal_channel)
        mode = stsv.get_input_value(self.chp_heatingmode_signal_channel)

        # Outputs
        if mode == 0:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, self.state.state * self.config.p_th)
            stsv.set_output_value(self.thermal_power_output_building_channel, 0)
        elif mode == 1:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, 0)
            stsv.set_output_value(self.thermal_power_output_building_channel, self.state.state * self.config.p_th)

        stsv.set_output_value(self.electricity_output_channel, self.state.state * self.config.p_el)

        # heat of combustion hydrogen: 141.8 MJ / kg; conversion W = J/s to kg / s
        stsv.set_output_value(self.fuel_consumption_channel, self.state.state * self.p_fuel)

    def get_default_connections_from_chp_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information("setting l1 default connections in generic CHP")
        connections: List[cp.ComponentConnection] = []
        controller_classname = controller_l1_chp.L1CHPController.get_classname()
        connections.append(
            cp.ComponentConnection(
                CHP.CHPControllerOnOffSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerOnOffSignal,
            )
        )
        connections.append(
            cp.ComponentConnection(
                CHP.CHPControllerHeatingModeSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerHeatingModeSignal,
            )
        )
        return connections

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
