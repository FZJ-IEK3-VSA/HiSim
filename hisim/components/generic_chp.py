"""Simple implementation of combined heat and power plant (CHP).

This can be either a natural gas driven turbine producing both electricity and heat,
or also a fuel cell. In this implementation the CHP does not modulate: it is either
on or off. When it runs, it outputs a constant thermal and electrical power signal
and needs a constant input of hydrogen or natural gas.
"""

# clean

from dataclasses import dataclass
from typing import List

from dataclasses_json import dataclass_json
from hisim import component as cp
from hisim import loadtypes as lt
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

    building_name: str
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
    def get_default_config_chp(
        thermal_power: float,
        building_name: str = "BUI1",
    ) -> "CHPConfig":
        """Get default config chp."""
        config = CHPConfig(
            building_name=building_name,
            name="CHP",
            source_weight=1,
            use=lt.LoadTypes.GAS,
            p_el=(0.33 / 0.5) * thermal_power,
            p_th=thermal_power,
            p_fuel=(1 / 0.5) * thermal_power,
        )
        return config

    @staticmethod
    def get_default_config_fuelcell(
        thermal_power: float,
        building_name: str = "BUI1",
    ) -> "CHPConfig":
        """Get default config fuel cell."""
        config = CHPConfig(
            building_name=building_name,
            name="CHP",
            source_weight=1,
            use=lt.LoadTypes.GREEN_HYDROGEN,
            p_el=(0.48 / 0.43) * thermal_power,
            p_th=thermal_power,
            p_fuel=(1 / 0.43) * thermal_power,
        )
        return config


class GenericCHPState:
    """Generic chp state class saves the state of the CHP."""

    def __init__(self, state: int) -> None:
        """Initialize the class."""
        self.state = state

    def clone(self) -> "GenericCHPState":
        """Clones the state."""
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
        self,
        my_simulation_parameters: SimulationParameters,
        config: CHPConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initializes the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.config = config
        if self.config.use == lt.LoadTypes.GREEN_HYDROGEN:
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
            output_description="Thermal Power output from CHP to building or buffer in Watt.",
        )
        self.thermal_power_output_dhw_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutputBoiler,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP to drain hot water storage in Watt.",
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
            output_description="Electrical Power output of CHP in Watt.",
        )
        if self.config.use == lt.LoadTypes.GREEN_HYDROGEN:
            self.fuel_consumption_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelDelivered,
                load_type=lt.LoadTypes.GREEN_HYDROGEN,
                unit=lt.Units.KG_PER_SEC,
                postprocessing_flag=[lt.LoadTypes.GREEN_HYDROGEN],
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
                    lt.LoadTypes.GAS,
                ],
                output_description="Gas consumption of CHP in Wh.",
            )
        self.add_default_connections(self.get_default_connections_from_chp_controller())

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Inputs
        self.state.state = int(stsv.get_input_value(self.chp_onoff_signal_channel))
        mode = stsv.get_input_value(self.chp_heatingmode_signal_channel)

        # Outputs
        if mode == 0:
            stsv.set_output_value(
                self.thermal_power_output_dhw_channel,
                self.state.state * self.config.p_th,
            )
            stsv.set_output_value(self.thermal_power_output_building_channel, 0)
        elif mode == 1:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, 0)
            stsv.set_output_value(
                self.thermal_power_output_building_channel,
                self.state.state * self.config.p_th,
            )

        stsv.set_output_value(self.electricity_output_channel, self.state.state * self.config.p_el)
        stsv.set_output_value(self.fuel_consumption_channel, self.state.state * self.p_fuel)

    def get_default_connections_from_chp_controller(
        self,
    ) -> List[cp.ComponentConnection]:
        """Sets default connections of the controller in the Fuel Cell / CHP."""

        connections: List[cp.ComponentConnection] = []
        controller_classname = controller_l1_chp.L1CHPController.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerOnOffSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerOnOffSignal,
            )
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
