# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hplib import hplib as hpl

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ConfigBase,
)
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim.components.heat_distribution_system import HeatingSystemType
from typing import Any, List, Optional

__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"


@dataclass_json
@dataclass
class HeatPumpHplibConfig:
    model: str
    group_id: int
    t_in: float
    t_out_val: float
    p_th_set: float
    cycling_mode: bool
    minimum_running_time_in_seconds: Optional[int]
    minimum_idle_time_in_seconds: Optional[int]


class HeatPumpHplib(Component):
    """
    Simulate heat pump efficiency (cop) as well as electrical (p_el) &
    thermal power (p_th), massflow (m_dot) and output temperature (t_out).
    Relevant simulation parameters are loaded within the init for a
    specific or generic heat pump type.
    """

    # Inputs
    OnOffSwitch = "OnOffSwitch"  # 1 = on, 0 = 0ff
    TemperatureInputPrimary = "TemperatureInputPrimary"  # °C
    TemperatureInputSecondary = "TemperatureInputSecondary"  # °C
    TemperatureAmbient = "TemperatureAmbient"  # °C

    # Outputs
    ThermalOutputPower = "ThermalOutputPower"  # W
    ElectricalInputPower = "ElectricalInputPower"  # W
    COP = "COP"  # -
    EER = "EER"  # -
    TemperatureOutput = "TemperatureOutput"  # °C
    MassFlowOutput = "MassFlowOutput"  # kg/s
    TimeOn = "TimeOn"  # s
    TimeOff = "TimeOff"  # s

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibConfig,
    ):
        """
        Loads the parameters of the specified heat pump.

        Parameters
        ----------
        model : str
            Name of the heat pump model or "Generic".
        group_id : numeric, default 0
            only for model "Generic": Group ID for subtype of heat pump. [1-6].
        t_in : numeric, default 0
            only for model "Generic": Input temperature :math:`T` at primary side of the heat pump. [°C]
        t_out_val : numeric, default 0
            only for model "Generic": Output temperature :math:`T` at secondary side of the heat pump. [°C]
        p_th_set : numeric, default 0
            only for model "Generic": Thermal output power at setpoint t_in, t_out. [W]

        Returns
        ----------
        parameters : pd.DataFrame
            Data frame containing the model parameters.
        """
        super().__init__(
            name="HeatPump", my_simulation_parameters=my_simulation_parameters
        )

        self.model = config.model

        self.group_id = config.group_id

        self.t_in = config.t_in

        self.t_out_val = config.t_out_val

        self.p_th_set = config.p_th_set

        self.cycling_mode = config.cycling_mode
        
        self.minimum_running_time_in_seconds = config.minimum_running_time_in_seconds
        
        self.minimum_idle_time_in_seconds = config.minimum_idle_time_in_seconds

        # Component has states
        self.state = HeatPumpState(
            time_on=0, time_off=0, time_on_cooling=0, on_off_previous=0
        )
        self.previous_state = self.state.self_copy()

        # Load parameters from heat pump database
        self.parameters = hpl.get_parameters(
            self.model, self.group_id, self.t_in, self.t_out_val, self.p_th_set
        )

        # Define component inputs
        self.on_off_switch: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.OnOffSwitch,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=True,
        )

        self.t_in_primary: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputPrimary,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_in_secondary: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputSecondary,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_amb: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureAmbient,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        # Define component outputs
        self.p_th: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPower,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=("Thermal output power in Watt"),
        )

        self.p_el: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power in Watt",
        )

        self.cop: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.COP,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="COP",
        )
        self.eer: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.EER,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="EER",
        )
        self.t_out: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutput,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Output in °C",
        )

        self.m_dot: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutput,
            load_type=LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow output",
        )

        self.time_on: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOn,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned on",
        )

        self.time_off: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOff,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned off",
        )

    @staticmethod
    def get_defaul_config():
        config = HeatPumpHplibConfig(
            model="Generic", group_id=-1, t_in=-300, t_out_val=-300, p_th_set=-30, minimum_running_time_in_seconds=600, minimum_idle_time_in_seconds=600
        )
        return config

    def write_to_report(self):
        """Write configuration to the report."""
        lines = []
        lines.append("Name: " + str(self.component_name))
        lines.append("Model: " + str(self.model))
        lines.append("T_in: " + str(self.t_in))
        lines.append("T_out_val: " + str(self.t_out_val))
        lines.append("P_th_set: " + str(self.p_th_set))
        return lines

    def i_save_state(self) -> None:
        self.previous_state = self.state.self_copy()
        # pass

    def i_restore_state(self) -> None:
        self.state = self.previous_state.self_copy()
        # pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:

        # Load input values
        on_off: float = stsv.get_input_value(self.on_off_switch)
        t_in_primary = stsv.get_input_value(self.t_in_primary)
        t_in_secondary = stsv.get_input_value(self.t_in_secondary)
        t_amb = stsv.get_input_value(self.t_amb)
        time_on = self.state.time_on
        time_on_cooling = self.state.time_on_cooling
        time_off = self.state.time_off

        # cycling means periodic turning on and off of the heat pump
        if self.cycling_mode == True:

            # Parameter
            time_on_min = self.minimum_running_time_in_seconds # [s]
            time_off_min = self.minimum_idle_time_in_seconds
            on_off_previous = self.state.on_off_previous
            
            if time_on_min is None or time_off_min is None:
                raise ValueError("When the cycling mode is true, the minimum running time and minimum idle time of the heat pump must be given an integer value.")

            # Overwrite on_off to realize minimum time of or time off
            if on_off_previous == 1 and time_on < time_on_min:
                on_off = 1
            elif on_off_previous == -1 and time_on_cooling < time_on_min:
                on_off = -1
            elif on_off_previous == 0 and time_off < time_off_min:
                on_off = 0

        # heat pump is turned on and off only according to heat pump controller
        elif self.cycling_mode == False:
            pass
        else:
            raise ValueError("Cycling mode of the advanced hplib unknown.")

        # OnOffSwitch
        if on_off == 1:
            # Calulate outputs for heating mode
            results = hpl.simulate(
                t_in_primary, t_in_secondary, self.parameters, t_amb, mode=1
            )
            p_th = results["P_th"].values[0]
            p_el = results["P_el"].values[0]
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out = results["T_out"].values[0]
            m_dot = results["m_dot"].values[0]
            time_on = time_on + self.my_simulation_parameters.seconds_per_timestep
            time_on_cooling = 0
            time_off = 0

        elif on_off == -1:
            # Calulate outputs for cooling mode
            results = hpl.simulate(
                t_in_primary, t_in_secondary, self.parameters, t_amb, mode=2
            )
            p_th = results["P_th"].values[0]
            p_el = results["P_el"].values[0]
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out = results["T_out"].values[0]
            m_dot = results["m_dot"].values[0]
            time_on_cooling = (
                time_on_cooling + self.my_simulation_parameters.seconds_per_timestep
            )
            time_on = 0
            time_off = 0
        elif on_off == 0:
            # Calulate outputs for off mode
            p_th = 0
            p_el = 0
            # None values or nans will cause troubles in post processing, that is why there are not used here
            # cop = None
            # t_out = None
            cop = 0
            eer = 0
            t_out = t_in_secondary
            m_dot = 0
            time_off = time_off + self.my_simulation_parameters.seconds_per_timestep
            time_on = 0
            time_on_cooling = 0

        else:
            raise ValueError("Unknown mode for Advanced HPLib On_Off.")

        # write values for output time series
        stsv.set_output_value(self.p_th, p_th)
        stsv.set_output_value(self.p_el, p_el)
        stsv.set_output_value(self.cop, cop)
        stsv.set_output_value(self.eer, eer)
        stsv.set_output_value(self.t_out, t_out)
        stsv.set_output_value(self.m_dot, m_dot)
        stsv.set_output_value(self.time_on, time_on)
        stsv.set_output_value(self.time_off, time_off)

        # write values to state
        self.state.time_on = time_on
        self.state.time_on_cooling = time_on_cooling
        self.state.time_off = time_off
        self.state.on_off_previous = on_off


@dataclass
class HeatPumpState:
    time_on: int = 0
    time_off: int = 0
    time_on_cooling: int = 0
    on_off_previous: float = 0

    def self_copy(
        self,
    ):
        """Copy the Heat Pump State."""
        return HeatPumpState(
            self.time_on, self.time_off, self.time_on_cooling, self.on_off_previous
        )


# ===========================================================================
# try to implement a hplib controller l1
@dataclass_json
@dataclass
class HeatPumpHplibControllerL1Config(ConfigBase):

    """HeatPump Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibControllerL1.get_full_classname()

    name: str
    mode: int
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]

    @classmethod
    def get_default_generic_heat_pump_controller_config(cls):
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpHplibControllerL1Config(
            name="HeatPumpController",
            mode=1,
            set_heating_threshold_outside_temperature_in_celsius=None,
        )


class HeatPumpHplibControllerL1(Component):

    """Heat Pump Controller.

    It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.
    On/off Switch with respect to water temperature from storage.

    Parameters
    ----------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump

    """

    # Inputs
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )
    HeatingFlowTemperatureFromHeatDistributionSystem = (
        "HeatingFlowTemperatureFromHeatDistributionSystem"
    )
    StatefromHeatDistributionSystem = "StatefromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    State = "State"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibControllerL1Config,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        super().__init__(
            self.heatpump_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
        )

        if SingletonSimRepository().exist_entry(key=SingletonDictKeyEnum.HEATINGSYSTEM):
            self.heating_system = SingletonSimRepository().get_entry(
                key=SingletonDictKeyEnum.HEATINGSYSTEM
            )
        else:
            raise KeyError(
                "Keys for heating system was not found in the singleton sim repository."
                + "This might be because the heat distribution system  was not initialized before the advanced hplib controller."
                + "Please check the order of the initialization of the components in your example."
            )
        self.build(
            mode=self.heatpump_controller_config.mode,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.DailyAverageOutsideTemperature,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        )

        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State} will follow.",
        )

        self.controller_heatpumpmode: Any
        self.previous_heatpump_mode: Any

    def build(
        self,
        mode: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heatpumpmode = "off"
        self.previous_heatpump_mode = self.controller_heatpumpmode

        # Configuration
        self.mode = mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_heatpump_mode = self.controller_heatpumpmode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heatpumpmode = self.previous_heatpump_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_config.get_string_dict()

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_from_heat_water_storage_in_celsius = (
                stsv.get_input_value(self.water_temperature_input_channel)
            )

            heating_flow_temperature_from_heat_distribution_system = (
                stsv.get_input_value(
                    self.heating_flow_temperature_from_heat_distribution_system_channel
                )
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            if self.mode == 1:
                self.conditions_on_off(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            # cooling only for floor heating possible
            elif (
                self.mode == 2 and self.heating_system == HeatingSystemType.FLOORHEATING
            ):
                self.conditions_heating_cooling_off(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )

            else:
                raise ValueError(
                    "Either the Advanced HP Lib Controller Mode is neither 1 nor 2 or the heating system is not floor heating which is the condition for cooling (mode 2)."
                )

            # no heating threshold for the heat pump
            if (
                self.heatpump_controller_config.set_heating_threshold_outside_temperature_in_celsius
                is None
            ):
                summer_mode = "on"

            # turning heat pump off when the average daily outside temperature is above a certain threshold
            else:
                summer_mode = self.summer_condition(
                    daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                    set_heating_threshold_temperature_in_celsius=self.heatpump_controller_config.set_heating_threshold_outside_temperature_in_celsius,
                )

            # state of heat distribution controller is off when daily avg outside temperature is > 16°C
            # in that case the heat pump should not be heating
            if self.controller_heatpumpmode == "heating" and summer_mode == "on":
                state = 1
            elif self.controller_heatpumpmode == "heating" and summer_mode == "off":
                state = 0
            elif self.controller_heatpumpmode == "off":
                state = 0
            elif self.controller_heatpumpmode == "cooling":
                state = -1
            else:
                raise ValueError("Advanced HP Lib Controller State unknown.")

            stsv.set_output_value(self.state_channel, state)

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode."""

        if self.controller_heatpumpmode == "heating":
            if (
                water_temperature_input_in_celsius
                > set_heating_flow_temperature_in_celsius + 0.5
            ):  # + 1:
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":
            if (
                water_temperature_input_in_celsius
                < set_heating_flow_temperature_in_celsius - 0.5
            ):  # - 1:
                self.controller_heatpumpmode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def conditions_heating_cooling_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode."""

        heating_set_temperature = set_heating_flow_temperature_in_celsius - 1.5
        cooling_set_temperature = set_heating_flow_temperature_in_celsius + 1.5

        if self.controller_heatpumpmode == "heating":
            if water_temperature_input_in_celsius >= heating_set_temperature:
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "cooling":
            if water_temperature_input_in_celsius <= cooling_set_temperature:
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "off":
            if water_temperature_input_in_celsius < heating_set_temperature - 0.5:
                self.controller_heatpumpmode = "heating"
                return
            if water_temperature_input_in_celsius > cooling_set_temperature + 0.5:
                self.controller_heatpumpmode = "cooling"
                return

        else:
            raise ValueError("unknown mode")

    def summer_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: float,
    ) -> str:
        """Set conditions for the valve in heat distribution."""

        if (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            summer_mode = "off"
            return summer_mode
        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            summer_mode = "on"
            return summer_mode

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
