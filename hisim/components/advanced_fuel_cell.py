"""Advanced fuel cell module."""

# clean
import os
from dataclasses import dataclass
import math

from typing import Any, List
import copy
from dataclasses_json import dataclass_json

import pandas as pd
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput, ConfigBase, DisplayConfig
from hisim import loadtypes as lt

from hisim.components.configuration import PhysicsConfig
from hisim import utils
from hisim.simulationparameters import SimulationParameters
from hisim import log


__authors__ = "Frank Burkrad, Maximilian Hillen,"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class CHPConfig(ConfigBase):
    """CHP Config class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return CHP.get_full_classname()

    building_name: str
    name: str
    min_operation_time: float
    min_idle_time: float
    gas_type: str
    operating_mode: str
    is_modulating: bool
    p_el_min: float  # [W]
    p_th_min: float  # [W]
    eff_el_min: float  # [-]
    eff_th_min: float  # [-]
    mass_flow_max: float  # kg/s
    p_el_max: float  # [W]
    p_th_max: float  # [W]
    eff_el_max: float  # [-]
    eff_th_max: float  # [-]
    temperature_max: float
    delta_temperature: float

    @classmethod
    def get_default_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get default config."""
        config = CHPConfig(
            building_name=building_name,
            name="CHP",
            min_operation_time=60,
            min_idle_time=15,
            gas_type="Hydrogen",
            operating_mode="both",
            is_modulating=True,
            p_el_min=2_000,
            p_th_min=3_000,
            eff_el_min=0.2,
            eff_th_min=0.5,
            mass_flow_max=0.011,
            p_el_max=3_000,
            p_th_max=4_000,
            eff_el_max=0.4,
            eff_th_max=0.55,
            temperature_max=80,
            delta_temperature=10,
        )
        return config


class CHPConfigAdvanced:
    """CHP config advanced class."""

    def __init__(self) -> None:
        """Initialize the class."""
        # Remark: moved the whole class body into the __init__ function to avoid errors if the file read below
        # does not exist.

        # system_name = "BlueGEN15"
        # system_name = "Dachs 0.8"
        # system_name = "Test_KWK"
        # system_name = "Dachs G2.9"
        # system_name = "HOMER"
        system_name = "BlueGen BG15"

        dataframe = pd.read_excel(
            os.path.join(utils.HISIMPATH["chp_system"], "mock_up_efficiencies.xlsx"),
            index_col=0,
        )

        df_specific = dataframe.loc[str(system_name)]

        if str(df_specific["is_modulating"]) == "Yes":
            self.is_modulating = True
            self.p_el_min = df_specific["P_el_min"]
            self.p_th_min = df_specific["P_th_min"]
            self.p_total_min = df_specific["P_total_min"]
            self.eff_el_min = df_specific["eff_el_min"]
            self.eff_th_min = df_specific["eff_th_min"]

        elif str(df_specific["is_modulating"]) == "No":
            self.is_modulating = False
        else:
            log.error("Modulation is not defined. Modulation must be 'Yes' or 'No'")
            raise ValueError

        self.p_el_max = df_specific["P_el_max"]
        self.p_th_max = df_specific["P_th_max"]
        self.p_total_max = df_specific["P_total_max"]  # maximum fuel consumption
        self.eff_el_max = df_specific["eff_el_max"]
        self.eff_th_max = df_specific["eff_th_max"]
        self.mass_flow_max = df_specific["mass_flow (dT=20°C)"]
        self.temperature_max = df_specific["temperature_max"]
        self.delta_temperature = 10


class CHPState:
    """CHP state class."""

    def __init__(self, start_timestep=None, electricity_output=0.0, cycle_number=None):
        """Initialize the class."""
        self.start_timestep = start_timestep
        self.electricity_output = electricity_output
        self.cycle_number = cycle_number
        if self.electricity_output == 0.0:
            self.activation = 0
        elif self.electricity_output > 0.0:
            self.activation = 1
        else:
            raise Exception("Impossible CHPState.")


class CHP(Component):
    """CHP class.

    Simulate chp efficiency (cop) as well as electrical (p_el) &
    thermal power (p_th), massflow (m_dot) and output temperature (t_out).
    """

    # Inputs
    ControlSignal = "ControlSignal"  # at which Procentage is the CHP modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"
    ElectricityFromCHPTarget = "ElectricityFromCHPTarget"
    HydrogenNotReleased = "HydrogenNotReleased"
    # OperatingModelSignal="OperatingModelSignal" #-->Wärme oder Stromgeführt. Nötig?

    # Output
    MassflowOutput = "Hot Water Energy Output"
    MassflowOutputTemperature = "MassflowOutputTemperature"
    ElectricityOutput = "ElectricityOutput"
    GasDemandTarget = "GasDemandTarget"
    NumberofCycles = "NumberofCycles"
    ThermalOutputPower = "ThermalOutputPower"
    GasDemandReal = "GasDemandReal"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CHPConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        self.chp_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.min_operation_time = self.chp_config.min_operation_time
        self.min_idle_time = self.chp_config.min_idle_time
        self.gas_type = self.chp_config.gas_type  # Gas Type can be "Hydrogen" or "Methan"
        self.operating_mode = self.chp_config.operating_mode  # operating_mode=["both","heat","electricity"]

        self.number_of_cycles = 0
        self.number_of_cycles_previous = copy.deepcopy(self.number_of_cycles)
        self.state = CHPState(start_timestep=int(0), cycle_number=0)
        self.previous_state = copy.deepcopy(self.state)

        # the 3600 comes from Normalised chp from p_el_max=3600. Look up chp_system_lib for more information
        self.p_el_max = self.chp_config.p_el_max
        usually_p_el_max = self.chp_config.p_el_max
        self.p_th_max = self.chp_config.p_th_max * (self.p_el_max / usually_p_el_max)
        self.p_th_min = self.chp_config.p_th_min
        self.p_el_min = self.chp_config.p_el_min

        if self.p_el_max < self.p_el_min or self.p_th_max < self.p_th_min:
            self.p_el_max = self.p_el_min + 100
            self.p_th_max = self.p_th_max + 100

        self.mass_flow_max = self.chp_config.mass_flow_max * (self.p_el_max / usually_p_el_max)
        if self.mass_flow_max < self.chp_config.mass_flow_max:
            self.mass_flow_max = self.chp_config.mass_flow_max
        self.eff_th_min: float = self.chp_config.eff_th_min
        self.eff_th_max = self.chp_config.eff_th_max
        self.eff_el_min: float = self.chp_config.eff_el_min
        self.eff_el_max = self.chp_config.eff_el_max
        self.temperature_max = self.chp_config.temperature_max

        self.delta_t = self.chp_config.delta_temperature

        # Inputs
        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            CHP.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            False,
        )
        # self.operating_mode_signal: ComponentInput = self.add_input(self.ComponentName, CHP.OperatingModelSignal, lt.LoadTypes.Gas, lt.Units.Percent, True)
        self.mass_inp_temp_channel: ComponentInput = self.add_input(
            self.component_name,
            CHP.MassflowInputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            False,
        )
        self.electricity_target_channel: ComponentInput = self.add_input(
            self.component_name,
            CHP.ElectricityFromCHPTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            False,
        )
        self.hydrogen_not_released_channel: ComponentInput = self.add_input(
            self.component_name,
            CHP.HydrogenNotReleased,
            lt.LoadTypes.GAS,
            lt.Units.KG,
            False,
        )

        # Outputs
        self.mass_out_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.MassflowOutput,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.MassflowOutput} will follow.",
        )
        self.mass_out_temp_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.MassflowOutputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.MassflowOutputTemperature} will follow.",
        )
        self.gas_demand_target_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.GasDemandTarget,
            lt.LoadTypes.GAS,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.GasDemandTarget} will follow.",
        )
        self.el_power_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description=f"here a description for CHP {self.ElectricityOutput} will follow.",
        )
        self.number_of_cycles_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.NumberofCycles,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for CHP {self.NumberofCycles} will follow.",
        )
        self.th_power_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.ThermalOutputPower,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for CHP {self.ThermalOutputPower} will follow.",
        )
        self.gas_demand_real_used_channel: ComponentOutput = self.add_output(
            self.component_name,
            CHP.GasDemandReal,
            lt.LoadTypes.GAS,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.GasDemandReal} will follow.",
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = copy.deepcopy(self.state)
        self.number_of_cycles_previous = self.number_of_cycles

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = copy.deepcopy(self.previous_state)
        self.number_of_cycles = self.number_of_cycles_previous

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doubelchecks."""
        pass

    def simulate_chp(self, control_signal: float, stsv: SingleTimeStepValues, timestep: int) -> Any:
        """Simualtes the component."""

        specific_heat_capacity_water = 4182
        # Calculation.Electric Energy deliverd
        # CHP is on
        if self.state.activation != 0:
            self.number_of_cycles = self.state.cycle_number
            # Checks if the minimum running time has been reached

            # Minimium running time has been reached and the CHP wants to shut off -->so it shuts off
            if timestep >= self.state.start_timestep + self.min_operation_time and control_signal == 0.0:
                # all Outputs zero
                mass_out_temp: float = 0
                mass_out: float = 0
                el_power: float = 0
                th_power: float = 0
                eff_el_real: float = 0
                eff_th_real: float = 0

                self.state = CHPState(
                    start_timestep=timestep,
                    cycle_number=self.number_of_cycles,
                    electricity_output=el_power,
                )

                stsv.set_output_value(self.mass_out_temp_channel, mass_out_temp)  # mass output temp
                stsv.set_output_value(self.mass_out_channel, mass_out)  # mass output flow
                stsv.set_output_value(self.el_power_channel, el_power)  # mass output flow
                stsv.set_output_value(self.th_power_channel, th_power)  # mass output flow

                # zu ändern, da gas_demand=!gas_power
                stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)
                return el_power, th_power, eff_el_real, eff_th_real

            # Minimium running time has not been reached and the CHP wants to shut off -->so it won't shut off
            if timestep < self.state.start_timestep + self.min_operation_time and control_signal == 0:
                # CHP doesn't want to run but has to, therefore is going to run in minimum power

                maximum_power_th: float = self.p_th_min
                eff_th_real = self.eff_th_min
                eff_el_real = self.eff_el_min
                maximum_power_el: float = self.p_el_min

                th_power = maximum_power_th * eff_th_real
                el_power = maximum_power_el * eff_el_real

                mass_out_temp = self.delta_t + stsv.get_input_value(self.mass_inp_temp_channel)
                mass_out = th_power / (specific_heat_capacity_water * self.delta_t)

                if mass_out > self.mass_flow_max:
                    mass_out = self.mass_flow_max
                    mass_out_temp = stsv.get_input_value(self.mass_inp_temp_channel) + th_power / (
                        mass_out * specific_heat_capacity_water
                    )

            # CHP doens't want to shut off and its activated--> so its stays activated
            else:
                # Calculate Eff_th
                d_eff_th = self.eff_th_max - self.eff_th_min

                if control_signal * self.p_th_max < self.p_th_min:
                    maximum_power_th = self.p_th_min
                    eff_th_real = self.eff_th_min
                else:
                    maximum_power_th = control_signal * self.p_th_max
                    eff_th_real = self.eff_th_min + d_eff_th * control_signal

                # Calculate Eff_el
                d_eff_el = self.eff_el_max - self.eff_el_min

                if control_signal * self.p_el_max < self.p_el_min:
                    maximum_power_el = self.p_el_min
                    eff_el_real = self.eff_el_min
                else:
                    maximum_power_el = control_signal * self.p_el_max
                    eff_el_real = self.eff_el_min + d_eff_el * control_signal

                th_power = maximum_power_th * eff_th_real
                el_power = maximum_power_el * eff_el_real

                mass_out_temp = self.delta_t + stsv.get_input_value(self.mass_inp_temp_channel)
                mass_out = th_power / (specific_heat_capacity_water * self.delta_t)

                if mass_out > self.mass_flow_max:
                    mass_out = self.mass_flow_max
                    mass_out_temp = stsv.get_input_value(self.mass_inp_temp_channel) + th_power / (
                        mass_out * specific_heat_capacity_water
                    )

            th_power = (
                (mass_out_temp - stsv.get_input_value(self.mass_inp_temp_channel))
                * specific_heat_capacity_water
                * mass_out
            )
            stsv.set_output_value(self.th_power_channel, th_power)  # ThermalPowerOutput
            stsv.set_output_value(self.mass_out_temp_channel, mass_out_temp)  # mass output temp
            stsv.set_output_value(self.mass_out_channel, mass_out)  # mass output flow
            stsv.set_output_value(self.el_power_channel, el_power)  # mass output flow
            # zu ändern, da gas_demand=!gas_power
            stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)
            return el_power, th_power, eff_el_real, eff_th_real
            # run in power of control_signal

        # CHP is Off
        # CHP wants to start and waited long enough since last start--> so it starts
        if control_signal != 0 and (timestep >= self.state.start_timestep + self.min_idle_time):
            self.number_of_cycles = self.number_of_cycles + 1
            number_of_cycles = self.number_of_cycles

            # Calculate Eff_th
            d_eff_th = self.eff_th_max - self.eff_th_min

            if control_signal * self.p_th_max < self.p_th_min:
                maximum_power_th = self.p_th_min
                eff_th_real = self.eff_th_min
            else:
                maximum_power_th = control_signal * self.p_th_max
                eff_th_real = self.eff_th_min + d_eff_th * control_signal

            # Calculate Eff_el
            d_eff_el = self.eff_el_max - self.eff_el_min

            if control_signal * self.p_el_max < self.p_el_min:
                maximum_power_el = self.p_el_min
                eff_el_real = self.eff_el_min
            else:
                maximum_power_el = control_signal * self.p_el_max
                eff_el_real = self.eff_el_min + d_eff_el * control_signal

            th_power = maximum_power_th * eff_th_real
            el_power = maximum_power_el * eff_el_real

            mass_out_temp = self.delta_t + stsv.get_input_value(self.mass_inp_temp_channel)
            mass_out = th_power / (specific_heat_capacity_water * self.delta_t)

            if mass_out > self.mass_flow_max:
                mass_out = self.mass_flow_max
                mass_out_temp = stsv.get_input_value(self.mass_inp_temp_channel) + th_power / (
                    mass_out * specific_heat_capacity_water
                )

            self.state = CHPState(
                start_timestep=timestep,
                electricity_output=el_power,
                cycle_number=number_of_cycles,
            )

        # CHP wants to starts but didn't wait long enough since last start -> so it won't start
        else:
            # all Outputs should be zero, because CHP can't start
            mass_out_temp = 0
            mass_out = 0
            el_power = 0
            th_power = 0
            eff_el_real = 0
            eff_th_real = 0

        stsv.set_output_value(self.th_power_channel, th_power)  # ThermalPowerOutput
        stsv.set_output_value(self.mass_out_temp_channel, mass_out_temp)  # mass output temp
        stsv.set_output_value(self.mass_out_channel, mass_out)  # mass output flow
        stsv.set_output_value(self.el_power_channel, el_power)  # mass output flow
        # zu ändern, da gas_demand=!gas_power
        stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)

        return el_power, th_power, eff_el_real, eff_th_real

    def calculate_control_signal(self, stsv: SingleTimeStepValues) -> float:  # pylint: disable=R0911
        """Calculate control signal."""
        if (stsv.get_input_value(self.electricity_target_channel)) < 30:
            control_signal: float = 0
            return control_signal
        if (stsv.get_input_value(self.electricity_target_channel)) < self.p_el_min * self.eff_el_min:
            control_signal = 0.4
            return control_signal
        if (stsv.get_input_value(self.electricity_target_channel)) > self.p_el_max * self.eff_el_max:
            control_signal = 1
            return control_signal

        x_1 = (
            -self.p_el_max
            - math.sqrt(
                (self.p_el_max * self.eff_el_min) ** 2
                + 4
                * (
                    stsv.get_input_value(self.electricity_target_channel)
                    * self.p_el_max
                    * (self.eff_el_max - self.eff_el_min)
                )
            )
        ) / (2 * self.p_el_max * (self.eff_el_max - self.eff_el_min))
        x_2 = (
            -self.p_el_max
            + math.sqrt(
                (self.p_el_max * self.eff_el_min) ** 2
                + 4
                * (
                    stsv.get_input_value(self.electricity_target_channel)
                    * self.p_el_max
                    * (self.eff_el_max - self.eff_el_min)
                )
            )
        ) / (2 * self.p_el_max * (self.eff_el_max - self.eff_el_min))
        if 0 < x_1 < 1:
            if 0 < x_2 < 1:
                if x_1 < x_2:
                    return x_2

                return x_1

            return x_1

        return x_2

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        control_signal: float = -1
        if self.operating_mode == "heat":
            control_signal = stsv.get_input_value(self.control_signal_channel)
        elif self.operating_mode == "electricity":
            control_signal = self.calculate_control_signal(stsv)
            if control_signal > 1 or control_signal < 0:
                control_signal = 1
        elif self.operating_mode == "both":
            control_signal = self.calculate_control_signal(stsv)
            if control_signal <= stsv.get_input_value(self.control_signal_channel):
                control_signal = stsv.get_input_value(self.control_signal_channel)
            else:
                if control_signal > 1 or control_signal < 0:
                    control_signal = 1

        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")

        el_power, th_power, eff_el_real, eff_th_real = self.simulate_chp(
            control_signal=control_signal, stsv=stsv, timestep=timestep
        )
        # Check if enough hydrogen is in the tank
        gas_demand_real_used = 0
        if el_power == 0 and th_power == 0:
            gas_demand_target = 0
            gas_demand_real_used = 0
        else:
            if self.gas_type == "Hydrogen":
                gas_demand_target = ((el_power / eff_el_real) + (th_power / eff_th_real)) / (
                    PhysicsConfig.hydrogen_specific_fuel_value_per_kg
                )
                if stsv.get_input_value(self.hydrogen_not_released_channel) == 0:
                    # Gas Demand can completly be charged from storage
                    gas_demand_real_used = gas_demand_target

                elif stsv.get_input_value(self.hydrogen_not_released_channel) < 0:
                    log.warning("Fault, bc. GasDemandpossible>gasdemandtarget @ " + str(timestep))

                elif stsv.get_input_value(self.hydrogen_not_released_channel) > 0:
                    # not enough Gas for running CHP on power demanded/calculated before
                    # to simplify, turn of CHP complelty, also when minimum running time isn't reached, bec. no Hydrogen is there

                    stsv.set_output_value(self.th_power_channel, 0)  # ThermalPowerOutput
                    stsv.set_output_value(self.mass_out_temp_channel, 0)  # mass output temp
                    stsv.set_output_value(self.mass_out_channel, 0)  # mass output flow
                    stsv.set_output_value(self.el_power_channel, 0)  # mass output flow
                    self.number_of_cycles = self.state.cycle_number
                    el_power = 0
                    self.state = CHPState(
                        start_timestep=timestep,
                        cycle_number=self.number_of_cycles,
                        electricity_output=el_power,
                    )

                    stsv.set_output_value(self.number_of_cycles_channel, self.number_of_cycles)
                    gas_demand_real_used = 0
            elif self.gas_type == "Methan":
                gas_demand_target = (
                    (el_power / eff_el_real) + (th_power / eff_th_real)
                ) / PhysicsConfig.natural_gas_specific_fuel_value_per_kg
                gas_demand_real_used = gas_demand_target
            else:
                raise Exception("No Gas chosen which is integrated in System")

        stsv.set_output_value(self.gas_demand_target_channel, gas_demand_target)  # CHP runs with
        stsv.set_output_value(self.gas_demand_real_used_channel, gas_demand_real_used)  # ThermalPowerOutput

    def write_to_report(self) -> List[str]:
        """Write to report."""
        lines = []
        for config_string in self.chp_config.get_string_dict():
            lines.append(config_string)
        lines.append("Component Name: " + str(self.component_name))
        lines.append("Name: CHP")
        lines.append("Min Operation Time [Sec]: " + str(self.min_operation_time))
        lines.append("Min Idle Time [Sec]: " + str(self.min_idle_time))
        lines.append("Gas Type: " + str(self.gas_type))
        lines.append("Operating Mode: " + str(self.operating_mode))
        lines.append("P_el_max [P]: " + str(self.p_el_max))
        lines.append("P_el_min [P]: " + str(self.p_el_min))
        lines.append("Eff_el_min: " + str(self.eff_el_min))
        lines.append("Eff_el_max: " + str(self.eff_el_max))
        lines.append("Mass Flow Max: " + str(self.mass_flow_max))
        lines.append("P_th_min [P]: " + str(self.p_th_min))
        lines.append("P_th_max [P]: " + str(self.p_th_max))
        lines.append("Eff_th_min: " + str(self.eff_th_min))
        lines.append("Eff_th_max: " + str(self.eff_th_max))
        lines.append("Max Temperature [°C]: " + str(self.temperature_max))
        lines.append("Delta T [°C]: " + str(self.delta_t))

        return lines
