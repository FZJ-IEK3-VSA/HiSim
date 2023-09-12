"""Green hydrogen electrolyzer."""

import os
from typing import List
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from scipy.interpolate import interp1d
import numpy as np
from scipy import interpolate

# Import modules from HiSim
from hisim.component import (
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

from hisim import component as cp

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "-"
__version__ = "1.0"
__maintainer__ = "Franz Oldopp"
__status__ = "development"


@dataclass_json
@dataclass
class ElectrolyzerConfig(cp.ConfigBase):
    """Configuration of the Electrolyzer."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Electrolyzer.get_full_classname()

    name: str
    electrolyzer_type: str
    nom_load: float  # [kW]
    max_load: float  # [kW]
    nom_h2_flow_rate: float  # [m^3/h]
    faraday_eff: float
    i_cell_nom: float
    ramp_up_rate: float  # [%/s]
    ramp_down_rate: float  # [%/s]
    # H_s_h2 = 33.33 #kWh/kg

    @classmethod
    def get_default_Alkaline_electrolyzer_config(cls):
        """Gets a default Alkaline Eletrolyzer."""
        config = ElectrolyzerConfig(
            name="Alkaline_electrolyzer",
            electrolyzer_type="Alkaline",
            nom_load=100.0,  # [kW]
            max_load=110.0,  # [kW]
            nom_h2_flow_rate=100.0,  # [m^3/h]
            faraday_eff=1.0,
            i_cell_nom=0.3,
            ramp_up_rate=0.1,  # [%/s]
            ramp_down_rate=0.2,  # [%/s]
            # H_s_h2 = 33.33,
        )
        return config

    @staticmethod
    def read_config(electrolyzer_name):
        """Opens the according JSON-file, based on the electrolyzer_name."""

        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "electrolyzer_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Electrolyzer variants", {}).get(electrolyzer_name, {})

    @classmethod
    def config_electrolyzer(cls, electrolyzer_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(electrolyzer_name)
        config = ElectrolyzerConfig(
            name=electrolyzer_name,  # config_json.get("name", "")
            electrolyzer_type=config_json.get("electrolyzer_type", ""),
            nom_load=config_json.get("nom_load", 0.0),
            max_load=config_json.get("max_load", 0.0),
            nom_h2_flow_rate=config_json.get("nom_h2_flow_rate", 0.0),
            faraday_eff=config_json.get("faraday_eff", 0.0),
            i_cell_nom=config_json.get("i_cell_nom", 0.0),
            ramp_up_rate=config_json.get("ramp_up_rate", 0.0),
            ramp_down_rate=config_json.get("ramp_down_rate", 0.0),
        )
        return config


class Electrolyzer(cp.Component):

    """The Electrolszer class receives load and state inputs and calculates different outputs.

    Parameters
    ----------
    name : str
        Name of the electrolyzer variant.

    electrolyzer_type : str
        Technology type of the electrolyzer.

    nom_load : float
        Nominal load of the electrolyzer in [kW].

    max_load : float
        Maximum load of the electrolyzer in [kW].

    nom_h2_flow_rate : float
        Hydrogen flow rate at nominal conditions in [m^3/h].

    faraday_eff : float
        Measure of the efficiency of the given electrochemical transformation.
        It is defined by given literature.

    i_cell_nom : float
        Nominal current density of the electrolyzer in [A/cm^2].

    ramp_up_rate : float
        Ramp up rate of the electrolyzer in [% of nom_load/s].

    ramp_down_rate : float
        Ramp down rate of the electrolyzer in [% of nom_load/s].
    """

    # Inputs
    LoadInput = "LoadInput"
    InputState = "InputState"

    # Outputs
    ElectrolyzerState = "ElectrolyzerState"
    CurrentLoad = "CurrentLoad"  # current load regarding the ramp-up and -down process

    CurrentHydrogenFlowRate = "CurrentHydrogenFlowRate"
    CurrentOxygenFlowRate = "CurrentOxygenFlowRate"
    CurrentWaterFlowRate = "CurrentWaterFlowRate"
    TotalHydrogenProduced = "TotalHydrogenProduced"
    TotalOxygenProduced = "TotalOxygenProduced"
    TotalWaterDemand = "TotalWaterDemand"

    CurrentEfficiency = "CurrentEfficiency"
    OperatingTime = "OperatingTime"

    TotalWarmStartTime = "TotalWarmStartTime"
    WarmStartCycles = "WarmStartCycles"
    TotalColdStartTime = "TotalColdStartTime"
    ColdStartCycles = "ColdStartCycles"
    TotalRampUpTime = "TotalRampUpTime"
    TotalRampDownTime = "TotalRampDownTime"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectrolyzerConfig,
    ):
        """Constructs all the neccessary attributes."""
        self.electrolyzerconfig = config

        self.technology_type = config.electrolyzer_type
        self.nom_load = config.nom_load
        self.max_load = config.max_load
        self.nom_h2_flow_rate = config.nom_h2_flow_rate
        self.faraday_eff = config.faraday_eff
        self.i_cell_nom = config.i_cell_nom
        self.ramp_up_rate = config.ramp_up_rate
        self.ramp_down_rate = config.ramp_down_rate


        self.state = 0
        self.current_h2_production_rate = 0.0
        self.current_spec_h2_production_rate = 0.0
        self.current_flow_rate_oxygen = 0.0
        self.current_flow_rate_water = 0.0

        super().__init__(
            name=self.electrolyzerconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels
        self.load_input: ComponentInput = self.add_input(
            self.electrolyzerconfig.name,
            Electrolyzer.LoadInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            False,
        )

        # get the state from the controller
        self.input_state: ComponentInput = self.add_input(
            self.electrolyzerconfig.name,
            Electrolyzer.InputState,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            True,
        )
        # =================================================================================================================================
        # Output channels

        self.current_load: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.CurrentLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            output_description="Current load following the input load",
        )

        # Set total ramp-up time output
        self.total_ramp_up_time: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.TotalRampUpTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-up time",
        )

        # Set total ramp-down time output
        self.total_ramp_down_time: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.TotalRampDownTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-down time",
        )

        # Set state output
        self.electrolyzer_state: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.ElectrolyzerState,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="Current state of electrolyzer",
        )

        # current hydrogen output
        self.hydrogen_flow_rate: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.CurrentHydrogenFlowRate,
            lt.LoadTypes.HYDROGEN,
            lt.Units.CUBICMETERS_PER_SECOND,
            output_description="Current hydrogen flow rate",
        )
        # Total hydrogen produced
        self.total_hydrogen: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.TotalHydrogenProduced,
            lt.LoadTypes.HYDROGEN,
            lt.Units.CUBICMETERS,
            output_description="Total hydrogen produced during simulation time",
        )
        # current oxygen output
        self.oxygen_flow_rate: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.CurrentOxygenFlowRate,
            lt.LoadTypes.OXYGEN,
            lt.Units.CUBICMETERS_PER_SECOND,
            output_description="Current oxygen flow rate",
        )
        # Total oxygen produced
        self.total_oxygen: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.TotalOxygenProduced,
            lt.LoadTypes.OXYGEN,
            lt.Units.CUBICMETERS,
            output_description="Total oxygen produced during simulation time",
        )
        # current water demand
        self.water_flow_rate: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.CurrentWaterFlowRate,
            lt.LoadTypes.WATER,
            lt.Units.CUBICMETERS_PER_SECOND,
            output_description="Current water flow rate",
        )
        # Total water demand
        self.total_water: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.TotalWaterDemand,
            lt.LoadTypes.WATER,
            lt.Units.CUBICMETERS,
            output_description="Total water demand during simulation time",
        )
        # Current efficiency
        self.current_efficiency_state: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.CurrentEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current efficiency based on the efficiency curve",
        )

        # Total operating time
        self.operating_time: ComponentOutput = self.add_output(
            self.electrolyzerconfig.name,
            Electrolyzer.OperatingTime,
            lt.LoadTypes.TIME,
            lt.Units.HOURS,
            output_description="Total time the electorlyzer is operating (on)",
        )
        # =================================================================================================================================
        # Transfer and storage of states

        self.current_load_state = 0.0
        self.total_ramp_up_count_state = 0.0
        self.total_ramp_down_count_state = 0.0
        self.total_warm_start_count = 0.0
        self.total_cold_start_count = 0.0
        self.total_warm_start_cycles = 0
        self.total_cold_start_cycles = 0
        self.current_warm_start_count = 0.0
        self.current_cold_start_count = 0.0
        self.total_hydrogen_produced = 0.0
        self.total_oxygen_produced = 0.0
        self.total_water_demand = 0.0
        self.total_operating_time = 0.0

        self.current_load_previous_state = self.current_load_state
        self.total_ramp_up_count_previous_state = self.total_ramp_up_count_state
        self.total_ramp_down_count_previous_state = self.total_ramp_down_count_state
        self.total_warm_start_count_previous_state = self.total_warm_start_count
        self.total_cold_start_count_previous_state = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_produced_previous = self.total_hydrogen_produced
        self.total_oxygen_produced_previous = self.total_oxygen_produced
        self.total_water_demand_previous = self.total_water_demand
        self.total_operating_time_previous = self.total_operating_time

    @staticmethod
    def spec_el_stack_demand_and_polarization_data_config(
        electrolyzer_type, nominal_load, h2_flow_rate, faraday_eff, i_cell_nom
    ):
        """
        Polarization curve data is provided corresponding to the used electrolyzer technology.
        Following this, the auxiliary power of the system and the cell volatge is calculated,
        based on the nominal current density.
        """
        # Load data from the JSON file
        data_file = os.path.join(
            utils.HISIMPATH["inputs"], "electrolyzer_polarization_curve_data.json"
        )
        with open(data_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if the provided technology is valid
        if electrolyzer_type not in data:
            raise ValueError(
                f"Invalid technology. Supported technologies are: {', '.join(data.keys())}"
            )

        # Extract the x and y data points for the selected technology
        i_cell = data[electrolyzer_type]["i_cell"]
        U_cell = data[electrolyzer_type]["U_cell"]

        # constants
        F = 96485  # C/mol
        std_vol = 0.022413  # m³/mol (at 1 atm; 0 °C)

        # from nom_current_density to aux_power
        spec_el_stack_demand_nom = (
            faraday_eff * np.array(U_cell) * (2 * F) / std_vol / 1000 / 3600
        )  # kWh/m³
        spec_el_demand_stack = np.interp(i_cell_nom, i_cell, spec_el_stack_demand_nom)

        # calculating aux_power
        aux_power = nominal_load - (
            spec_el_demand_stack * h2_flow_rate
        )  # might needs to be set to a constant value

        # interpolarization function
        U_cell_nom = np.interp(i_cell_nom, i_cell, U_cell)  # V

        return i_cell, U_cell, i_cell_nom, U_cell_nom, aux_power

    def h2_production_rate(
        self,
        i_cell_nom,
        U_cell_nom,
        nominal_load,
        i_cell,
        U_cell,
        h2_flow_rate,
        aux_power,
        current_load,
        state,
    ):
        """
        Based on the polarisation curve, the spec. electricity demand and
        the current load, the H2 production and the spec. H2 production rate
        is calculated.
        """

        nominal_power_density = i_cell_nom * U_cell_nom  # W/cm²

        h2_production_rate = np.array(i_cell) / i_cell_nom * h2_flow_rate  # m³/h

        p_cell = np.array(i_cell) * np.array(U_cell)  # W/cm²

        stack_power = p_cell / nominal_power_density * nominal_load

        # Calculates system_power from stack power
        system_power = stack_power + aux_power
        # print("system_power: ", system_power)

        interp_function_h2_production_rate = interpolate.interp1d(
            system_power, h2_production_rate, kind="quadratic"
        )
        spec_h2_production_rate = (
            h2_production_rate / system_power
        )  # m³/kWh (proportional to the system efficiency)

        interp_function_spec_h2_production_rate = interpolate.interp1d(
            system_power, spec_h2_production_rate, kind="quadratic"
        )

        if state == 1:
            # Only produced hydrogen if the system is "on"
            current_h2_production_rate = float(
                interp_function_h2_production_rate(current_load)
            )
            current_spec_h2_production_rate = float(
                interp_function_spec_h2_production_rate(current_load)
            )

        else:
            # No hydrogen production of the system is in "standby" or "off"
            current_h2_production_rate = 0.0
            current_spec_h2_production_rate = 0.0

        return current_h2_production_rate, current_spec_h2_production_rate

    def oxygen_productin(self, current_h2_production_rate):
        """
        Returns the produced flow rate of oxygen,
        based on the current hydrogen flow rate.
        """

        V_dot_O2 = (
            0.5 * current_h2_production_rate
        )  # Kurzweil (2018) - Elektrolyse von Wasser
        return V_dot_O2

    def water_demand(self, current_h2_production_rate):
        """
        Returns the water demand flow rate,
        based on the current hydrogen flow rate.
        """

        V_dot_H2O = (
            2.5 * current_h2_production_rate
        )  # Kurzweil (2018) - Elektrolyse von Wasser
        return V_dot_H2O

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.current_load_previous_state = self.current_load_state
        self.total_ramp_up_count_previous_state = self.total_ramp_up_count_state
        self.total_ramp_down_count_previous_state = self.total_ramp_down_count_state
        self.total_warm_start_count_previous_state = self.total_warm_start_count
        self.total_cold_start_count_previous_state = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_produced_previous = self.total_hydrogen_produced
        self.total_oxygen_produced_previous = self.total_oxygen_produced
        self.total_water_demand_previous = self.total_water_demand
        self.total_operating_time_previous = self.total_operating_time

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.current_load_state = self.current_load_previous_state
        self.total_ramp_up_count_state = self.total_ramp_up_count_previous_state
        self.total_ramp_down_count_state = self.total_ramp_down_count_previous_state
        self.total_warm_start_count = self.total_warm_start_count_previous_state
        self.total_cold_start_count = self.total_cold_start_count_previous_state
        self.total_warm_start_cycles = self.total_warm_start_cycles_previous
        self.total_cold_start_cycles = self.total_cold_start_cycles_previous
        self.current_warm_start_count = self.current_warm_start_count_previous
        self.current_cold_start_count = self.current_cold_start_count_previous
        self.total_hydrogen_produced = self.total_hydrogen_produced_previous
        self.total_oxygen_produced = self.total_oxygen_produced_previous
        self.total_water_demand = self.total_water_demand_previous
        self.total_operating_time = self.total_operating_time_previous

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        if force_convergence:
            return

        # get provided electricity
        P_el = stsv.get_input_value(self.load_input)

        # get state input
        self.state = stsv.get_input_value(self.input_state)

        # Variables
        electrolyzer_type = self.technology_type
        nominal_load = self.nom_load
        nom_h2_flow_rate = self.nom_h2_flow_rate
        faraday_eff = self.faraday_eff
        i_cell_nom = self.i_cell_nom
        ramp_up_rate = self.ramp_up_rate
        ramp_down_rate = self.ramp_down_rate
        seconds_per_timestep = (
            self.my_simulation_parameters.seconds_per_timestep
        )  # [s/timestep]

        # ramp up per timestep calculation
        ramp_up_per_timestep = nominal_load * ramp_up_rate * seconds_per_timestep
        total_ramp_up_per_timestep = ramp_up_per_timestep

        # ramp down per timestep calculation
        ramp_down_per_timestep = nominal_load * ramp_down_rate * seconds_per_timestep
        total_ramp_down_per_timestep = ramp_down_per_timestep

        # calculating load input
        new_load = abs(P_el - self.current_load_state)

        if self.state == 1:
            self.total_operating_time += seconds_per_timestep / 3600

            """the ramping process"""
            if new_load == nominal_load:
                self.current_load_state = nominal_load
                self.total_ramp_up_count_state += 0
                self.total_ramp_down_count_state += 0
            if new_load == 0:
                self.current_load_state = self.current_load_state
                self.total_ramp_up_count_state += 0
                self.total_ramp_down_count_state += 0

            # Ramping up
            if (
                new_load >= total_ramp_up_per_timestep
                and self.current_load_state < P_el
            ):
                self.total_ramp_up_count_state += seconds_per_timestep
                self.current_load_state += total_ramp_up_per_timestep

            elif (
                self.current_load_state < P_el and new_load < total_ramp_up_per_timestep
            ):
                percentage_ramp_up_per_timestep = new_load / total_ramp_up_per_timestep
                self.total_ramp_up_count_state += (
                    percentage_ramp_up_per_timestep * seconds_per_timestep
                )
                if self.current_load_state == 0:
                    self.current_load_state += P_el
                else:
                    self.current_load_state += new_load

            # Ramping down
            elif (
                total_ramp_down_per_timestep <= new_load
                and P_el < self.current_load_state
            ):
                self.total_ramp_down_count_state += seconds_per_timestep
                self.current_load_state -= new_load

            elif (
                P_el < self.current_load_state
                and new_load < total_ramp_down_per_timestep
            ):
                percentage_ramp_down_per_timestep = (
                    new_load / total_ramp_down_per_timestep
                )
                self.total_ramp_down_count_state += (
                    percentage_ramp_down_per_timestep * seconds_per_timestep
                )
                self.current_load_state -= new_load

        elif self.state == 0:
            self.total_ramp_up_count_state += 0
            self.total_ramp_down_count_state += 0
            self.current_load_state = P_el

        elif self.state == -1:
            self.total_ramp_up_count_state += 0
            self.total_ramp_down_count_state += 0
            self.current_load_state = P_el

        # Applying polarization curve data
        (
            i_cell,
            U_cell,
            i_cell_nom,
            U_cell_nom,
            aux_power,
        ) = self.spec_el_stack_demand_and_polarization_data_config(
            electrolyzer_type, nominal_load, nom_h2_flow_rate, faraday_eff, i_cell_nom
        )
        # Current hydrogen prduction and specific hydrogen production rate
        # print("self.state: ", self.state)
        # print("self.current_load_state: ", self.current_load_state)
        (
            self.current_h2_production_rate,
            self.current_spec_h2_production_rate,
        ) = self.h2_production_rate(
            i_cell_nom,
            U_cell_nom,
            nominal_load,
            i_cell,
            U_cell,
            nom_h2_flow_rate,
            aux_power,
            self.current_load_state,
            self.state,
        )
        # Current oxygen and water flow rate
        self.current_flow_rate_oxygen = self.oxygen_productin(
            self.current_h2_production_rate
        )
        self.current_flow_rate_water = self.water_demand(
            self.current_h2_production_rate
        )

        # Calculating total amount of hydrogen, oxygen and water
        total_hydrogen_produced_in_timestep = self.current_h2_production_rate * (
            seconds_per_timestep / 3600
        )
        self.total_hydrogen_produced += total_hydrogen_produced_in_timestep
        total_oxygen_produced_in_timestep = self.current_flow_rate_oxygen * (
            seconds_per_timestep / 3600
        )
        self.total_oxygen_produced += total_oxygen_produced_in_timestep
        total_water_demand_in_timestep = self.current_flow_rate_water * (
            seconds_per_timestep / 3600
        )
        self.total_water_demand += total_water_demand_in_timestep

        # Initializing outputs
        stsv.set_output_value(self.hydrogen_flow_rate, self.current_h2_production_rate)
        stsv.set_output_value(self.oxygen_flow_rate, self.current_flow_rate_oxygen)
        stsv.set_output_value(self.water_flow_rate, self.current_flow_rate_water)
        stsv.set_output_value(self.electrolyzer_state, self.state)
        stsv.set_output_value(self.current_load, self.current_load_state)
        stsv.set_output_value(self.total_ramp_up_time, self.total_ramp_up_count_state)
        stsv.set_output_value(
            self.total_ramp_down_time, self.total_ramp_down_count_state
        )
        stsv.set_output_value(self.total_hydrogen, self.total_hydrogen_produced)
        stsv.set_output_value(self.total_oxygen, self.total_oxygen_produced)
        stsv.set_output_value(self.total_water, self.total_water_demand)
        stsv.set_output_value(self.operating_time, self.total_operating_time)
        stsv.set_output_value(
            self.current_efficiency_state, self.current_spec_h2_production_rate
        )

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        for config_string in self.electrolyzerconfig.get_string_dict():
            lines.append(config_string)
        lines.append("Component Name" + str(self.component_name))
        lines.append(
            "Total operating time during simulation: " + str(self.total_operating_time)
        )
        lines.append(
            "Total hydrogen produced during simulation: "
            + str(self.total_hydrogen_produced)
        )
        lines.append(
            "Total oxygen produced during simulation: "
            + str(self.total_oxygen_produced)
        )
        lines.append(
            "Total water demand during simulation: " + str(self.total_water_demand)
        )
        return lines
