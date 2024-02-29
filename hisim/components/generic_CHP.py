"""Simple implementation of combined heat and power plant (CHP).

This can be either a natural gas driven turbine producing both electricity and heat,
or also a fuel cell. In this implementation the CHP does not modulate: it is either
on or off. When it runs, it outputs a constant thermal and electrical power signal
and needs a constant input of hydrogen or natural gas."""

from dataclasses import dataclass
from typing import List

from dataclasses_json import dataclass_json
from hisim import utils
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import (controller_l1_chp, controller_predicitve_C4L_electrolyzer_fuelcell, controller_C4L_electrolyzer_fuelcell_1a_1b)
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
    #: demanded lower or upper heat vaule "Brennwert/Heizwert" of fuel input in kWh / kg
    h_fuel: float
    

    
    @staticmethod
    def get_default_config_chp(thermal_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.GAS, p_el=(0.33 / 0.5) * thermal_power, p_th=thermal_power, p_fuel=(1 / 0.5) * thermal_power,
        )
        return config

    @staticmethod
    def get_default_config_fuelcell(thermal_power: float) -> "CHPConfig":
        config = CHPConfig(
            name="CHP", source_weight=1, use=lt.LoadTypes.HYDROGEN, p_el=(0.48 / 0.43) * thermal_power, p_th=thermal_power, p_fuel=(1 / 0.43) * thermal_power
        )
        return config
    
    @staticmethod
    def get_default_config_fuelcell_p_el_based(fuel_cell_power: float) -> "CHPConfig":
        '''
        Assumption based on Dominik Mail of 4. October 2023: 1 Watt fuel power is converted to 58 % electrical voltage. Thermal power corresponds to 1/2 of the electrical voltage; 1 watt --> 0.58 el. + 0.29 th. = 0.87 total              '''
        
        config = CHPConfig(
         #   name="CHP", source_weight=1, use=lt.LoadTypes.HYDROGEN, p_el = fuel_cell_power, p_th = fuel_cell_power * (0.43 / 0.48), p_fuel=(1 / 0.43) * (fuel_cell_power * (0.43 / 0.48)),
        name="CHP", source_weight=1, use=lt.LoadTypes.HYDROGEN, p_el = fuel_cell_power, p_th = fuel_cell_power * 0.5, p_fuel= (fuel_cell_power* (1 / 0.58)), h_fuel = 0.0, 

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
    ThermalPowerOutput = "ThermalPowerOutput"
    ElectricityOutput = "ElectricityOutput"
    FuelDelivered = "FuelDelivered"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: CHPConfig
    ) -> None:
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.config = config
        if self.config.use == lt.LoadTypes.HYDROGEN:
#            self.p_fuel = config.p_fuel / (3.6e3 * 3.939e4)  # converted to kg / s ((old original version until November 6.))

            self.p_fuel = config.p_fuel / (3.6e3 * config.h_fuel * 1000)  # converted to kg / s h_fuel = heating value of vuel (Heizwert) in kWh/kg

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

        self.thermal_power_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutput,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power output from CHP for arbitrary use in Watt."
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
        self.add_default_connections(self.get_default_connections_from_electrolyzerfuelcell_controller())
        self.add_default_connections(self.get_default_connections_from_electrolyzerfuelcell_nonpredictivecontroller())

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
            stsv.set_output_value(self.thermal_power_output_channel, 0)
        elif mode == 1:
            stsv.set_output_value(self.thermal_power_output_dhw_channel, 0)
            stsv.set_output_value(self.thermal_power_output_building_channel, self.state.state * self.config.p_th)
            stsv.set_output_value(self.thermal_power_output_channel, 0)
        elif mode == 2:     # Gives  the global Thermal Power of the Fuel Cell back, if fuel cell is turned on
            stsv.set_output_value(self.thermal_power_output_channel, self.state.state * self.config.p_th)

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
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerHeatingModeSignal,
                controller_classname,
                controller_l1_chp.L1CHPController.CHPControllerHeatingModeSignal,
            )
        )
        return connections
    
    def get_default_connections_from_electrolyzerfuelcell_controller(self,) -> List[cp.ComponentConnection]:
    
        """Sets default connections for the FuelCell Controller."""
        log.information("setting fuel cell controller default connections in L1 CHP/Fuel Cell Controller")
        connections: List[cp.ComponentConnection] = []
        controller_classname = controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerOnOffSignal,
                controller_classname,
                controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController.FuelCellControllerOnOffSignal,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerHeatingModeSignal ,
                controller_classname,
                controller_predicitve_C4L_electrolyzer_fuelcell.C4LelectrolyzerfuelcellpredictiveController.CHPControllerHeatingModeSignal,
            )
        )
        return connections
    
    def get_default_connections_from_electrolyzerfuelcell_nonpredictivecontroller(self,) -> List[cp.ComponentConnection]:
    
        """Sets default connections for the FuelCell non predictive Controller."""
        log.information("setting fuel cell controller default connections in L1 CHP/Fuel Cell Controller")
        connections: List[cp.ComponentConnection] = []
        controller_classname1 = controller_C4L_electrolyzer_fuelcell_1a_1b.C4Lelectrolyzerfuelcell1a1bController.get_classname()
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerOnOffSignal,
                controller_classname1,
                controller_C4L_electrolyzer_fuelcell_1a_1b.C4Lelectrolyzerfuelcell1a1bController.FuelCellControllerOnOffSignal,
            )
        )
        connections.append(
            cp.ComponentConnection(
                SimpleCHP.CHPControllerHeatingModeSignal ,
                controller_classname1,
                controller_C4L_electrolyzer_fuelcell_1a_1b.C4Lelectrolyzerfuelcell1a1bController.CHPControllerHeatingModeSignal,
            )
        )
        return connections

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
