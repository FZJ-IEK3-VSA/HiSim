""" Modular Heat Pump Class together with Configuration and State. """
# Generic/Built-in
from dataclasses import dataclass
from typing import Optional, List, Any

import numpy as np
from dataclasses_json import dataclass_json

import hisim.loadtypes as lt
from hisim import component as cp
from hisim import log

# Owned
from hisim import utils
from hisim.components import controller_l1_heatpump

from hisim.components.weather import Weather
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
class HeatPumpConfig:
    """ Configuration of a HeatPump. """
    #: name of the device
    name: str
    #: priority of the device in energy management system: the higher the number the lower the priority
    source_weight: int
    #: manufacturer to search heat pump in data base
    manufacturer: str
    #: device name to search heat pump in data base
    device_name: str
    #: maximal thermal power of heat pump in kW
    power_th: float
    #: usage of the heatpump: either for heating or for water heating
    water_vs_heating: lt.InandOutputType

    @staticmethod
    def get_default_config_heating() -> "HeatPumpConfig":
        """ Returns default configuration of a heat pump used for heating. """
        config = HeatPumpConfig(name='HeatingHeatPump', source_weight=1, manufacturer="Viessmann Werke GmbH & Co KG",
                                device_name="Vitocal 300-A AWO-AC 301.B07", power_th=6200, water_vs_heating=lt.InandOutputType.HEATING)
        return config

    @staticmethod
    def get_default_config_waterheating() -> "HeatPumpConfig":
        """ Returns default configuration of a heat pump used for water heating."""
        config = HeatPumpConfig(name='DHWHeatPump', source_weight=1, manufacturer="Viessmann Werke GmbH & Co KG",
                                device_name="Vitocal 300-A AWO-AC 301.B07", power_th=3000, water_vs_heating=lt.InandOutputType.WATER_HEATING)
        return config

    @staticmethod
    def get_default_config_heating_electric() -> "HeatPumpConfig":
        """ Returns default configuartion of simple electrical heating system with a COP of one. """
        config = HeatPumpConfig(name='HeatingHeatingRod', source_weight=1, manufacturer="dummy", device_name="HeatingRod", power_th=6200,
                                water_vs_heating=lt.InandOutputType.HEATING)
        return config

    @staticmethod
    def get_default_config_waterheating_electric() -> "HeatPumpConfig":
        """ Returns default configuration of electrical heating rod for boiler. """
        config = HeatPumpConfig(name='DHWHeatingRod', source_weight=1, manufacturer="dummy", device_name="HeatingRod", power_th=3000,
                                water_vs_heating=lt.InandOutputType.WATER_HEATING)
        return config


class ModularHeatPumpState:
    """
    This data class saves the state of the heat pump.
    """
    def __init__(self, state: int = 0):
        """Initializes state. """
        self.state = state

    def clone(self) -> "ModularHeatPumpState":
        """Creates copy of state. """
        return ModularHeatPumpState(state=self.state)


class ModularHeatPump(cp.Component):
    """
    Heat pump implementation. The generic_heatpump_modular differs to generic_heatpump in the sense that the minimal runtime is not in the component but in the related controller.
    This implementation does not consider cooling of buildings.

    Components to connect to:
    (1) Weather
    (2) Heat Pump Controller (controller_l1_heatpump)
    """

    # Inputs
    TemperatureOutside = "TemperatureOutside"
    HeatControllerTargetPercentage = "HeatControllerTargetPercentage"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ElectricityOutput = "ElectricityOutput"
    PowerModifier = "PowerModifier"

    @utils.measure_execution_time
    def __init__(
        self, config: HeatPumpConfig, my_simulation_parameters: SimulationParameters
    ):

        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )
        self.config = config
        self.build()
        self.state = ModularHeatPumpState()
        self.previous_state = ModularHeatPumpState()

        if my_simulation_parameters.surplus_control:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                self.config.water_vs_heating,
            ]
        else:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
            ]

        # Inputs - Mandatories
        self.temperature_outside_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.ANY,
            lt.Units.CELSIUS,
            mandatory=True,
        )

        self.heat_controller_power_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatControllerTargetPercentage,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            mandatory=True,
        )

        # Outputs
        self.thermal_power_delicered_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDelivered,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.CHARGE, self.config.water_vs_heating],
            output_description="Thermal Power Delivered"
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=postprocessing_flag,
            output_description="Electricity Output"
        )

        self.power_modifier_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.PowerModifier,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            postprocessing_flag=[],
            output_description="Power Modifier"
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(
            self.get_default_connections_from_controller_l1_heatpump()
        )

    def get_default_connections_from_weather(self):
        """ Sets default connections of Weather. """
        log.information("setting weather default connections in HeatPump")
        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                ModularHeatPump.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def get_default_connections_from_controller_l1_heatpump(self):
        """ Sets default connections of heat pump controller. """
        log.information("setting l1 default connections in HeatPump")
        connections = []
        controller_classname = (
            controller_l1_heatpump.L1HeatPumpController.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                ModularHeatPump.HeatControllerTargetPercentage,
                controller_classname,
                controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self):
        """ Initialization function of Modular Heat Pump."""
        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        heat_pump = None
        for heat_pump in heat_pumps_database:
            if (
                heat_pump["Manufacturer"] == self.config.manufacturer
                and heat_pump["Name"] == self.config.device_name
            ):
                heat_pump_found = True
                break

        if not heat_pump_found or heat_pump is None:
            raise Exception("Heat pump model not registered in the database")

        # Interpolates COP data from the database
        self.cop_ref = []
        self.t_out_ref = []
        for heat_pump_cops in heat_pump["COP"]:
            self.t_out_ref.append(float([*heat_pump_cops][0][1:].split("/")[0]))
            self.cop_ref.append(float([*heat_pump_cops.values()][0]))
        self.cop_coef = np.polyfit(self.t_out_ref, self.cop_ref, 1)

        # Writes info to report
        self.write_to_report()

    def cal_cop(self, t_out: float) -> float:
        """ Returns coefficient of performance of selected heat pump. """
        val: float = self.cop_coef[0] * t_out + self.cop_coef[1]
        return val

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def write_to_report(self) -> List[str]:
        """ Writes relevant data to report. """
        lines: List[str] = []
        lines.append("Name: {}".format(self.config.name + str(self.config.source_weight)))
        lines.append("Manufacturer: {}".format(self.config.name))
        lines.append("Max power: {:4.0f} kW".format((self.config.power_th) * 1e-3))
        return lines

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """ Iteration of heat pump simulation. """

        # Inputs
        target_percentage = stsv.get_input_value(self.heat_controller_power_modifier_channel)

        T_outside: float = stsv.get_input_value(self.temperature_outside_channel)
        cop = self.cal_cop(T_outside)
        electric_power = self.config.power_th / cop

        # calculate modulation
        if target_percentage > 0:
            power_modifier = target_percentage
        if target_percentage == 0:
            power_modifier = 0

        power_modifier = min(1, power_modifier)

        stsv.set_output_value(
            self.thermal_power_delicered_channel, self.config.power_th * power_modifier
        )
        stsv.set_output_value(self.power_modifier_channel, power_modifier)

        stsv.set_output_value(self.electricity_output_channel, electric_power * power_modifier)
