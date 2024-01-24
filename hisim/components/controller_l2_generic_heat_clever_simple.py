"""Controller l2 generic heat clever simple module."""

# clean

# -*- coding: utf-8 -*-

# Generic/Built-in
from typing import Optional, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim import utils
from hisim import component as cp
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.components import controller_l1_generic_runtime
from hisim.components.building import Building
from hisim.components import generic_hot_water_storage_modular


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
class L2HeatSmartConfig(cp.ConfigBase):

    """L2 Config class."""

    name: str
    source_weight: int
    temperature_min_heating: float
    temperature_max_heating: float
    temperature_tolerance: float
    p_threshold: float
    cooling_considered: bool
    temperature_min_cooling: Optional[float]
    temperature_max_cooling: Optional[float]
    heating_season_begin: Optional[int]
    heating_season_end: Optional[int]

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return L2HeatSmartController.get_full_classname()


class L2HeatSmartControllerState:

    """Controller state class that saves the state of the heat pump."""

    def __init__(
        self,
        timestep_actual: int = -1,
        state: int = 0,
        compulsory: int = 0,
        count: int = 0,
    ):
        """Initialize the class."""
        self.timestep_actual = timestep_actual
        self.state = state
        self.compulsory = compulsory
        self.count = count

    def clone(self):
        """Clones the state."""
        return L2HeatSmartControllerState(
            timestep_actual=self.timestep_actual,
            state=self.state,
            compulsory=self.compulsory,
            count=self.count,
        )

    def is_first_iteration(self, timestep):
        """Is first iteration."""
        if self.timestep_actual + 1 == timestep:
            self.timestep_actual += 1
            self.compulsory = 0
            self.count = 0
            return True
        return False

    def is_compulsory(self):
        """Is compulsory."""
        if self.count <= 1:
            self.compulsory = 0
        else:
            self.compulsory = 1

    def activate(self):
        """Activate."""
        self.state = 1
        self.compulsory = 1
        self.count += 1

    def deactivate(self):
        """Deactivate."""
        self.state = 0
        self.compulsory = 1
        self.count += 1


class L2HeatSmartController(cp.Component):

    """L2 heat pump controller.

    Processes signals ensuring comfort temperature of building.
    Gets available surplus electricity and the temperature of the storage or building to control as input,
    and outputs control signal 0/1 for turn off/switch on based on comfort temperature limits and available electricity.
    It optionally has different modes for cooling and heating selected by the time of the year.

    Parameters
    ----------
    source_weight : int, optional
        Weight of component, relevant if there is more than one component of same type, defines hierachy in control. The default is 1.
    T_min_heating: float, optional
        Minimum comfortable temperature for residents during heating period, in °C. The default is 19 °C.
    T_max_heating: float, optional
        Maximum comfortable temperature for residents during heating period, in °C. The default is 23 °C.
    T_tolerance : float, optional
        Temperature difference the building may go below or exceed the comfort temperature band with, because of recommendations from L3. The default is 1 °C.
    P_threshold : float, optional
        Estimated power to drive heat source. The defauls is 1500 W.
    T_min_cooling: float, optional
        Minimum comfortable temperature for residents during cooling period, in °C. The default is 23 °C.
    T_max_cooling: float, optional
        Maximum comfortable temperature for residents during cooling period, in °C. The default is 26 °C.
    heating_season_begin : int, optional
        Day( julian day, number of day in year ), when heating season starts - and cooling season ends. The default is 270.
    heating_season_end : int, optional
        Day( julian day, number of day in year ), when heating season ends - and cooling season starts. The default is 150.

    """

    # Inputs
    ReferenceTemperature = "ReferenceTemperature"
    ElectricityTarget = "ElectricityTarget"
    L1RunTimeSignal = "L1RunTimeSignal"

    # Outputs
    L2DeviceSignal = "L2DeviceSignal"

    # #Forecasts
    # HeatPumpLoadForecast = "HeatPumpLoadForecast"

    # Similar components to connect to:
    # 1. Building
    # 2. HeatPump

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: L2HeatSmartConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        if not config.__class__.__name__ == L2HeatSmartConfig.__name__:
            raise ValueError("Wrong config class: " + config.__class__.__name__)
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.build(config)

        # Component Outputs
        self.l2_devicesignal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.L2DeviceSignal,
            LoadTypes.ON_OFF,
            Units.BINARY,
            output_description="L2 Device Signal from Heating Controller",
        )

        # Component Inputs
        self.referencetemperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ReferenceTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=True,
        )
        self.l1_runtime_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name, self.L1RunTimeSignal, LoadTypes.ANY, Units.ANY, True
        )

        self.add_default_connections(self.get_default_connections_from_buildings())
        self.add_default_connections(self.get_default_connections_from_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_controller_l1_generic_runtime())

        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityTarget,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            mandatory=True,
        )
        self.state: Any
        self.previous_state: Any

    def get_default_connections_from_buildings(self):
        """Get default connections from buildings."""

        connections = []
        building_classname = Building.get_classname()
        connections.append(
            cp.ComponentConnection(
                L2HeatSmartController.ReferenceTemperature,
                building_classname,
                Building.TemperatureMean,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def get_default_connections_from_hot_water_storage(self):
        """Get default connections from hot water storage."""

        connections = []
        boiler_classname = generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        connections.append(
            cp.ComponentConnection(
                L2HeatSmartController.ReferenceTemperature,
                boiler_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def get_default_connections_from_controller_l1_generic_runtime(self):
        """Get default connections from controller l1 generic runtime."""

        connections = []
        l1_classname = controller_l1_generic_runtime.L1GenericRuntimeController.get_classname()
        connections.append(
            cp.ComponentConnection(
                L2HeatSmartController.l1_RunTimeSignal,
                l1_classname,
                controller_l1_generic_runtime.L1GenericRuntimeController.l1_RunTimeSignal,
            )
        )
        return connections

    @staticmethod
    def get_default_config_heating():
        """Get default config heating."""
        config = L2HeatSmartConfig(
            name="L2HeatingTemperatureController",
            source_weight=1,
            temperature_min_heating=20.0,
            temperature_max_heating=22.0,
            temperature_tolerance=1.0,
            p_threshold=1500,
            cooling_considered=False,
            temperature_min_cooling=23.0,
            temperature_max_cooling=25.0,
            heating_season_begin=270,
            heating_season_end=150,
        )
        return config

    @staticmethod
    def get_default_config_buffer_heating():
        """Get default config buffer heating."""
        config = L2HeatSmartConfig(
            name="L2BufferTemperatureController",
            source_weight=1,
            temperature_min_heating=40.0,
            temperature_max_heating=60.0,
            temperature_tolerance=10.0,
            p_threshold=1500,
            cooling_considered=False,
            temperature_min_cooling=5.0,
            temperature_max_cooling=15.0,
            heating_season_begin=270,
            heating_season_end=150,
        )
        return config

    @staticmethod
    def get_default_config_waterheating():
        """Get default config waterheating."""
        config = L2HeatSmartConfig(
            name="L2DHWTemperatureController",
            source_weight=1,
            temperature_min_heating=50.0,
            temperature_max_heating=80.0,
            temperature_tolerance=5.0,
            p_threshold=1500,
            cooling_considered=False,
            temperature_min_cooling=None,
            temperature_max_cooling=None,
            heating_season_begin=None,
            heating_season_end=None,
        )
        return config

    def build(self, config):
        """Build function."""
        self.name = config.name
        self.source_weight = config.source_weight
        self.temperature_min_heating = config.temperature_min_heating
        self.temperature_max_heating = config.temperature_max_heating
        self.temperature_tolerance = config.temperature_tolerance
        self.p_threshold = config.p_threshold
        self.cooling_considered = config.cooling_considered
        if self.cooling_considered:
            self.temperature_min_cooling = config.temperature_min_cooling
            self.temperature_max_cooling = config.temperature_max_cooling
            self.heating_season_begin = (
                config.heating_season_begin * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
            self.heating_season_end = (
                config.heating_season_end * 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
        self.state = L2HeatSmartControllerState()
        self.previous_state = L2HeatSmartControllerState()

    def control_cooling(
        self, temperature_control: float, temperature_min_cooling: float, temperature_max_cooling: float, l3state: Any
    ) -> None:
        """Control cooling."""
        if temperature_control > temperature_max_cooling:
            # start cooling if temperature exceeds upper limit
            self.state.activate()
            self.previous_state.activate()

        elif temperature_control < temperature_min_cooling:
            # stop cooling if temperature goes below lower limit
            self.state.deactivate()
            self.previous_state.deactivate()

        else:
            if self.state.compulsory == 1:
                # use previous state if it is compulsory
                pass
            elif self.electricity_target_channel.source_output is not None:
                # use recommendation from l3 if available and not compulsory
                self.state.state = l3state
            else:
                # use previous state if l3 was not available
                self.state = self.previous_state.clone()

    def control_heating(self, temperature_control: float, temperature_min_heating: float, l3state: Any) -> int:
        """Control heating."""
        if l3state > 0:
            temperature_min_heating = temperature_min_heating + 5
        if temperature_control < temperature_min_heating:
            return 1
        return 0

    def i_save_state(self):
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self):
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        if force_convergence:
            return
        # check demand, and change state of self.has_heating_demand, and self._has_cooling_demand
        temperature_control = stsv.get_input_value(self.referencetemperature_channel)

        # get l3 recommendation if available
        electricity_target = stsv.get_input_value(self.electricity_target_channel)
        if electricity_target >= self.p_threshold:
            l3state = 1
        else:
            l3state = 0

        # reset temperature limits if recommended from l3
        # if self.cooling_considered:
        #     if l3state == 1 :
        #         if RunTimeSignal > 0:
        #             T_min_cooling = self.T_min_cooling - self.T_tolerance
        #         else:
        #             T_min_cooling = ( self.T_min_cooling + self.T_max_cooling ) / 2
        #         T_max_cooling = self.T_max_cooling
        #     elif l3state == 0:
        #         T_max_cooling = self.T_max_cooling + self.T_tolerance
        #         T_min_cooling = self.T_min_cooling

        # if l3state == 1:
        #     # if RunTimeSignal > 0:
        #     #     T_max_heating = self.T_max_heating + self.T_tolerance
        #     # else:
        #     T_max_heating = ( self.T_min_heating + self.T_max_heating ) / 2
        #     T_min_heating = self.T_min_heating
        #     self.state.is_compulsory( )
        #     self.previous_state.is_compulsory( )
        # elif l3state == 0:
        #      T_max_heating = self.T_max_heating
        #      T_min_heating = self.T_min_heating - self.T_tolerance
        #      self.state.is_compulsory( )
        #      self.previous_state.is_compulsory( )

        # if self.cooling_considered:
        #     #check out during cooling season
        #     if timestep < self.heating_season_begin and timestep > self.heating_season_end:
        #         self.control_cooling( T_control = T_control, T_min_cooling = T_min_cooling, T_max_cooling = T_max_cooling, l3state = l3state )
        #     #check out during heating season
        #     else:
        #         self.control_heating( T_control = T_control, T_min_heating = T_min_heating, T_max_heating = T_max_heating, l3state = l3state )
        #
        # #check out during heating season
        # else:
        control_signal = self.control_heating(
            temperature_control=temperature_control,
            temperature_min_heating=self.temperature_min_heating,
            l3state=l3state,
        )
        stsv.set_output_value(self.l2_devicesignal_channel, control_signal)

    def write_to_report(self):
        """Writes to report."""
        lines = []
        lines.append(f"Name: {self.source_weight}")
        lines.append(f"upper set temperature: {self.temperature_max_heating:4.0f} °C")
        lines.append(f"lower set temperature: {self.temperature_min_heating:4.0f} °C")
        lines.append(f"tolerance: {self.temperature_tolerance:4.0f} °C")
        return lines
