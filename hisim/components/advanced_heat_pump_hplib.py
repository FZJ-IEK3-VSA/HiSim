# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hplib import hplib as hpl

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
)
from hisim.loadtypes import LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from typing import Optional

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

        # Component has states
        self.state = HeatPumpState()
        self.previous_state = deepcopy(self.state)

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
            output_description=("Thermal output power in Watt")
        )

        self.p_el: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power in Watt"
        )

        self.cop: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.COP,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="COP"
        )

        self.t_out: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutput,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Output in °C"
        )

        self.m_dot: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutput,
            load_type=LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow output"
        )

        self.time_on: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOn,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned on"
        )

        self.time_off: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOff,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned off"
        )

    @staticmethod
    def get_defaul_config():
        config = HeatPumpHplibConfig(
            model="Generic", group_id=-1, t_in=-300, t_out_val=-300, p_th_set=-30
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
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self) -> None:
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        # Parameter
        time_on_min = 600  # [s]
        time_off_min = time_on_min

        # Load input values
        on_off: float = stsv.get_input_value(self.on_off_switch)
        t_in_primary = stsv.get_input_value(self.t_in_primary)
        t_in_secondary = stsv.get_input_value(self.t_in_secondary)
        t_amb = stsv.get_input_value(self.t_amb)
        time_on = self.state.time_on
        time_off = self.state.time_off
        on_off_previous = self.state.on_off_previous

        # Overwrite on_off to realize minimum time of or time off
        if on_off_previous == 1 & time_on < time_on_min:
            on_off = 1
        elif on_off_previous == 0 & time_off < time_off_min:
            on_off = 0

        # OnOffSwitch
        if on_off == 1:
            # Calulate outputs
            results = hpl.simulate(t_in_primary, t_in_secondary, self.parameters, t_amb)
            p_th = results["P_th"].values[0]
            p_el = results["P_el"].values[0]
            cop = results["COP"].values[0]
            t_out = results["T_out"].values[0]
            m_dot = results["m_dot"].values[0]
            time_on = time_on + self.my_simulation_parameters.seconds_per_timestep
            time_off = 0
        else:
            # Calulate outputs
            p_th = 0
            p_el = 0
            cop = None
            t_out = None
            m_dot = 0
            time_off = time_off + self.my_simulation_parameters.seconds_per_timestep
            time_on = 0

        # write values for output time series
        stsv.set_output_value(self.p_th, p_th)
        stsv.set_output_value(self.p_el, p_el)
        stsv.set_output_value(self.cop, cop)
        stsv.set_output_value(self.t_out, t_out)
        stsv.set_output_value(self.m_dot, m_dot)
        stsv.set_output_value(self.time_on, time_on)
        stsv.set_output_value(self.time_off, time_off)

        # write values to state
        self.state.time_on = time_on
        self.state.time_off = time_off
        self.state.on_off_previous = on_off


@dataclass
class HeatPumpState:
    time_on: int = 0
    time_off: int = 0
    on_off_previous: float = 0
