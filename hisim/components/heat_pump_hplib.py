# Import packages from standard library or the environment e.g. pandas, numpy etc.
from copy import deepcopy
from dataclasses import dataclass

# Import modules from HiSim
from component import Component, ComponentInput, ComponentOutput, SingleTimeStepValues
from loadtypes import LoadTypes, Units
from inputs.heat_pump_hplib import hplib as hpl

__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"


class HeatPumpHplib(Component):
    """
    Simulate heat pump efficiency (cop) as well as electrical (p_el) &
    thermal power (p_th), massflow (m_dot) and output temperature (t_out).
    Relevant simulation parameters are loaded within the init for a
    specific or generic heat pump type.
    """

    # Inputs
    Mode = "Mode"                                               # 0 = off, 1 = heating, 2 = cooling
    TemperatureInputPrimary = "TemperatureInputPrimary"         # °C
    TemperatureInputSecondary = "TemperatureInputSecondary"     # °C
    TemperatureAmbient = "TemperatureAmbient"                   # °C

    # Outputs
    ThermalOutputPower = "ThermalOutputPower"                   # W
    ElectricalInputPower = "ElectricalInputPower"               # W
    COP = "COP"                                                 # -
    EER = "EER"                                                 # -
    TemperatureOutput = "TemperatureOutput"                     # °C
    MassFlowOutput = "MassFlowOutput"                           # kg/s
    TimeOn = "TimeOn"                                           # s
    TimeOff = "TimeOff"                                         # s

    def __init__(self, model: str, group_id: int = None, t_in: float = None, t_out: float = None,
                 p_th_set: float = None):
        """
        Loads the parameters of the specified heat pump and creates the object HeatPump 
        for simulation purpose.

        Parameters
        ----------
        model : str
            Name of the heat pump model or "Generic".
        group_id : numeric, default 0
            only for model "Generic": Group ID for subtype of heat pump. [1-6].
        t_in : numeric, default 0
            only for model "Generic": Input temperature :math:`T` at primary side of the heat pump. [°C]
        t_out : numeric, default 0
            only for model "Generic": Output temperature :math:`T` at secondary side of the heat pump. [°C]
        p_th_set : numeric, default 0
            only for model "Generic": Thermal output power at setpoint t_in, t_out. [W]
        """
        super().__init__(name="HeatPumpHplib")

        self.model = model
        self.group_id = group_id
        self.t_in = t_in
        self.t_out = t_out
        self.p_th_set = p_th_set

        # Component has states
        self.state = HeatPumpState()
        self.previous_state = deepcopy(self.state)

        # Load parameters from heat pump database
        self.parameters = hpl.get_parameters(self.model, self.group_id,
                                             self.t_in, self.t_out, self.p_th_set)

        # Create HeatPump object for simulation purpose
        self.HeatPump = hpl.HeatPump(self.parameters)

        # Set minimum on- and off-time of heat pump
        self.time_on_min = 600 # [s]
        self.time_off_min = self.time_on_min

        # Define component inputs
        self.mode: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                            field_name=self.Mode,
                                                            load_type=LoadTypes.Any,
                                                            unit=Units.Any,
                                                            mandatory=True)

        self.t_in_primary: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                           field_name=self.TemperatureInputPrimary,
                                                           load_type=LoadTypes.Temperature,
                                                           unit=Units.Celsius,
                                                           mandatory=True)

        self.t_in_secondary: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                             field_name=self.TemperatureInputSecondary,
                                                             load_type=LoadTypes.Temperature,
                                                             unit=Units.Celsius,
                                                             mandatory=True)

        self.t_amb: ComponentInput = self.add_input(object_name=self.ComponentName,
                                                    field_name=self.TemperatureAmbient,
                                                    load_type=LoadTypes.Temperature,
                                                    unit=Units.Celsius,
                                                    mandatory=True)

        # Define component outputs
        self.p_th: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.ThermalOutputPower,
                                                     load_type=LoadTypes.Heating,
                                                     unit=Units.Watt)

        self.p_el: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                     field_name=self.ElectricalInputPower,
                                                     load_type=LoadTypes.Electricity,
                                                     unit=Units.Watt)

        self.cop: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                    field_name=self.COP,
                                                    load_type=LoadTypes.Any,
                                                    unit=Units.Any)

        self.eer: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                    field_name=self.EER,
                                                    load_type=LoadTypes.Any,
                                                    unit=Units.Any)

        self.t_out: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                      field_name=self.TemperatureOutput,
                                                      load_type=LoadTypes.Heating,
                                                      unit=Units.Celsius)

        self.m_dot: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                      field_name=self.MassFlowOutput,
                                                      load_type=LoadTypes.Volume,
                                                      unit=Units.kg_per_sec)

        self.time_on: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                        field_name=self.TimeOn,
                                                        load_type=LoadTypes.Time,
                                                        unit=Units.Seconds)

        self.time_off: ComponentOutput = self.add_output(object_name=self.ComponentName,
                                                         field_name=self.TimeOff,
                                                         load_type=LoadTypes.Time,
                                                         unit=Units.Seconds)

    def i_save_state(self):
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self):
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, delta_demand: float, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        """
        Performs the simulation of the heat pump model.

        Parameters
        ----------
        timestep : int
            ...
        stsv : SingleTimeStepValue
            ...
        seconds_per_timestep : int
            ...
        force_convergence: bool
            ...

        Returns
        -------
        p_th :  numeric
            Thermal output power. [W]
        p_el : numeric
            Electrical input Power. [W]
        cop : numeric
            Coefficient of performance in case of heating.
        eer : numeric
            Energy efficieny ration in case of cooling.
        t_out : numeric
            Output temperature :math:`T` at secondary side of the heat pump. [°C]
        m_dot : numeric
            Mass flow at secondary side of the heat pump. [kg/s]
        time_on : numeric
            Time how long the heat pump is currently running. [s]
        time_off : numeric
            Time how long the heat pump has not run. [s]
        """ 
        
        # Load input values
        mode = stsv.get_input_value(self.mode)
        t_in_primary = stsv.get_input_value(self.t_in_primary)
        t_in_secondary = stsv.get_input_value(self.t_in_secondary)
        t_amb = stsv.get_input_value(self.t_amb)
        time_on = self.state.time_on
        time_off = self.state.time_off
        mode_previous = self.state.mode_previous
        
        # Overwrite mode to realize minimum time on or time off
        if mode_previous == 1 and time_on < self.time_on_min:
            mode=1
        if mode_previous == 2 and time_on < self.time_on_min:
            mode=2
        elif mode_previous == 0 and time_off < self.time_off_min:
            mode=0

        # Mode (0=off, 1=heating, 2=cooling)
        if mode == 1 or 2:
            # Calulate outputs
            results = self.HeatPump.simulate(t_in_primary, t_in_secondary, t_amb, mode)
            p_th=results['P_th']
            p_el=results['P_el']
            cop=results['COP']
            eer=results['EER']
            t_out=results['T_out']
            m_dot=results['m_dot']
            time_on = time_on + seconds_per_timestep
            time_off = 0
        else:
            # Calulate outputs
            p_th = 0
            p_el = 0
            cop = 0
            eer = 0
            t_out = 0
            m_dot = 0
            time_off = time_off + seconds_per_timestep
            time_on = 0

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
        self.state.time_off = time_off
        self.state.mode_previous = mode

@dataclass
class HeatPumpState:
    """
    This data class saves the state of the simulation results.

    Parameters
    ----------
    runtime : int
        Stores the state of the runtime in seconds value from :py:class:`~hisim.component.HeatPump`.
    """
    time_on: int = 0
    time_off: int = 600
    mode_previous: int = 0
    