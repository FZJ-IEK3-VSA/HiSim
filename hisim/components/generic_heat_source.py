""" Generic Heat Source (Oil, Gas or DistrictHeating) together with Configuration and State. """

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import List, Any

from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import controller_l1_heatpump
from hisim.simulationparameters import SimulationParameters

__authors__ = "Johanna Ganglbauer - johanna.ganglbauer@4wardenergy.at"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class HeatSourceConfig(cp.ConfigBase):
    """
    Configuration of a generic HeatSource.
    """

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of heat source classified by fuel (Oil, Gas or DistrictHeating)
    fuel: lt.LoadTypes
    #: maximal thermal power of heat source in kW
    power_th: float
    #: usage of the heatpump: either for heating or for water heating
    water_vs_heating: lt.InandOutputType
    #: efficiency of the fuel to heat conversion
    efficiency: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatSource.get_full_classname()

    @classmethod
    def get_default_config_heating(cls) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating"""
        config = HeatSourceConfig(
            name="HeatingHeatSource",
            source_weight=1,
            fuel=lt.LoadTypes.DISTRICTHEATING,
            power_th=6200.0,
            water_vs_heating=lt.InandOutputType.HEATING,
            efficiency=1.0,
        )
        return config

    @classmethod
    def get_default_config_waterheating(cls) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for water heating (DHW)"""
        config = HeatSourceConfig(
            name="DHWHeatSource",
            source_weight=1,
            fuel=lt.LoadTypes.DISTRICTHEATING,
            power_th=3000.0,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            efficiency=1.0,
        )
        return config


class HeatSourceState:
    """
    This data class saves the state of the heat source.
    """

    def __init__(self, state: int = 0):
        """Initializes state."""
        self.state = state

    def clone(self) -> "HeatSourceState":
        """Creates copy of a state."""
        return HeatSourceState(state=self.state)


class HeatSource(cp.Component):
    """
    Heat Source implementation - District Heating, Oil Heating or Gas Heating. Heat is converted with given efficiency.

    Components to connect to:
    (1) Heat Pump Controller (controller_l1_heatpump)
    """

    # Inputs
    L1HeatSourceTargetPercentage = "L1HeatSourceTargetPercentage"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    FuelDelivered = "FuelDelivered"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: HeatSourceConfig
    ) -> None:

        super().__init__(
            config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # introduce parameters of district heating
        self.config = config
        self.state = HeatSourceState()
        self.previous_state = HeatSourceState()

        # Inputs - Mandatories
        self.l1_heatsource_taget_percentage: cp.ComponentInput = self.add_input(
            self.component_name,
            self.L1HeatSourceTargetPercentage,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            mandatory=True,
        )

        # Outputs
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDelivered,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.THERMAL_PRODUCTION],
            output_description="Thermal Power Delivered",
        )
        self.fuel_delivered_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.FuelDelivered,
            load_type=self.config.fuel,
            unit=lt.Units.ANY,
            postprocessing_flag=[
                lt.InandOutputType.FUEL_CONSUMPTION,
                config.fuel,
                config.water_vs_heating,
            ],
            output_description="Fuel Delivered",
        )

        if config.fuel == lt.LoadTypes.OIL:
            self.fuel_delivered_channel.unit = lt.Units.LITER
        else:
            self.fuel_delivered_channel.unit = lt.Units.WATT_HOUR

        self.add_default_connections(
            self.get_default_connections_controller_l1_heatpump()
        )

    def get_default_connections_controller_l1_heatpump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Sets default connections of heat source controller."""
        log.information("setting l1 default connections in Generic Heat Source")
        connections = []
        controller_classname = (
            controller_l1_heatpump.L1HeatPumpController.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                HeatSource.L1HeatSourceTargetPercentage,
                controller_classname,
                controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage,
            )
        )
        return connections

    def write_to_report(self) -> List[str]:
        """
        Writes relevant data to report.
        """

        lines = []
        lines.append(
            "Name: {}".format(self.config.name + str(self.config.source_weight))
        )
        lines.append("Fuel: {}".format(self.config.fuel))
        lines.append("Power: {:4.0f} kW".format((self.config.power_th) * 1e-3))
        lines.append("Efficiency : {:4.0f} %".format((self.config.efficiency) * 100))
        return lines

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
        """
        Performs the simulation of the heat source model.
        """

        # Inputs
        target_percentage = stsv.get_input_value(self.l1_heatsource_taget_percentage)

        # calculate modulation
        if target_percentage > 0:
            power_modifier = target_percentage
        if target_percentage == 0:
            power_modifier = 0
        if target_percentage < 0:
            power_modifier = 0
        if power_modifier > 1:
            power_modifier = 1

        stsv.set_output_value(
            self.thermal_power_delivered_channel,
            self.config.power_th * power_modifier * self.config.efficiency,
        )

        if self.config.fuel == lt.LoadTypes.OIL:
            # conversion from Wh oil to liter oil
            stsv.set_output_value(
                self.fuel_delivered_channel,
                power_modifier
                * self.config.power_th
                * 1.0526315789474e-4
                * self.my_simulation_parameters.seconds_per_timestep
                / 3.6e3,
            )
        else:
            stsv.set_output_value(
                self.fuel_delivered_channel,
                power_modifier
                * self.config.power_th
                * self.my_simulation_parameters.seconds_per_timestep
                / 3.6e3,
            )
