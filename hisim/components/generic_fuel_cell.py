"""Example Transformer."""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
import os
from typing import List
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from scipy.interpolate import interp1d
import numpy as np

# Import modules from HiSim
from hisim.component import (
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
)
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

# from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

from hisim import (
    component as cp,
)

__authors__ = "Franz Oldopp"
__copyright__ = "Copyright 2023, FZJ-IEK-3"
__credits__ = ["Franz Oldopp"]
__license__ = "-"
__version__ = "1.0"
__maintainer__ = "Franz Oldopp"
__status__ = "development"


@dataclass_json
@dataclass
class FuelCellConfig(cp.ConfigBase):

    """Configuration of the Example Transformer."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return FuelCell.get_full_classname()

    # parameter_string: str
    # my_simulation_parameters: SimulationParameters
    name: str
    type: str
    nom_output: float  # [kW]
    max_output: float  # [kW]
    min_output: float  # [kW]
    nom_h2_flow_rate: float  # [m^3/h]
    faraday_eff: float
    i_cell_nom: float
    ramp_up_rate: float  # [%/s]
    ramp_down_rate: float  # [%/s]
    # H_s_h2 = 33.33 #kWh/kg

    @classmethod
    def get_default_pem_fuel_cell_config(cls):
        """Gets a default PEM Eletrolyzer."""
        return FuelCellConfig(
            name="PEM_Fuel_Cell",
            type="PEM",
            nom_output=100.0,  # [kW]
            max_output=110.0,  # [kW]
            min_output=10.0,  # [kW]
            nom_h2_flow_rate=65.64,  # [m^3/h]
            faraday_eff=0.90,
            i_cell_nom=0.52,
            ramp_up_rate=0.1,  # [%/s]
            ramp_down_rate=0.2,  # [%/s]
            # H_s_h2 = 33.33,
        )

    @staticmethod
    def read_config(fuel_cell_name):
        """Read config."""
        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "fuel_cell_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def config_fuel_cell(cls, fuel_cell_name):
        """Get config of fuel cell."""
        config_json = cls.read_config(fuel_cell_name)
        config = FuelCellConfig(
            name="FuelCell",  # config_json.get("name", "")
            type=config_json.get("type", ""),
            nom_output=config_json.get("nom_output", 0.0),
            max_output=config_json.get("max_output", 0.0),
            min_output=config_json.get("min_output", 0.0),
            nom_h2_flow_rate=config_json.get("nom_h2_flow_rate", 0.0),
            faraday_eff=config_json.get("faraday_eff", 0.0),
            i_cell_nom=config_json.get("i_cell_nom", 0.0),
            ramp_up_rate=config_json.get("ramp_up_rate", 0.0),
            ramp_down_rate=config_json.get("ramp_down_rate", 0.0),
        )
        return config


class FuelCell(cp.Component):

    """The Example Transformer class.

    It is used to modify input values and return them as new output values.

    Parameters
    ----------
    name : str
        Name of the electrolyzer variant.

    loadtype : lt.LoadTypes
        A :py:class:`~hisim.loadtypes.LoadTypes` object that represents
        the type of the loaded data.

    unit : lt.Units
        A :py:class:`~hisim.loadtypes.Units` object that represents
        the unit of the loaded data.

    min_load_electrolyzer : float
        Minimum capable load of thr electrolyzer.

    max_load_electrolyzer : float
        Maximum capable load of thr electrolyzer.

    """

    # Inputs
    DemandProfile = "DemandProfile"
    ControlSignal = "ControlSignal"

    # Outputs
    FuelCellState = "FuelCellState"
    WaterflowOutput = "WaterflowOutput"
    PowerOutput = "PowerOutput"
    TotalEnergyProduced = "TotalEnergyProduced"

    HydrogenDemand = "HydrogenDemand"
    OxygenDemand = "OxygenDemand"
    CurrentHydrogenFlowRate = "CurrentHydrogenFlowRate"
    CurrentOxygenFlowRate = "CurrentOxygenFlowRate"
    CurrentWaterFlowRate = "CurrentWaterFlowRate"
    TotalHydrogenConsumed = "TotalHydrogenConsumed"
    TotalOxygenConsumed = "TotalOxygenConsumed"
    TotalWaterProduced = "TotalWaterProduced"
    NumberofCycles = "NumberofCycles"
    TotalRampUpTime = "TotalRampUpTime"
    TotalRampDownTime = "TotalRampDownTime"
    CurrentEfficiency = "CurrentEfficiency"
    OperatingTime = "OperatingTime"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: FuelCellConfig,
    ):
        """Constructs all the neccessary attributes."""
        self.fuelcellconfig = config

        self.technology_type = config.type
        self.nom_output = config.nom_output
        self.max_output = config.max_output
        self.min_output = config.min_output
        self.nom_h2_flow_rate = config.nom_h2_flow_rate
        self.faraday_eff = config.faraday_eff
        self.i_cell_nom = config.i_cell_nom
        self.ramp_up_rate = config.ramp_up_rate
        self.ramp_down_rate = config.ramp_down_rate

        super().__init__(
            name=self.fuelcellconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels
        self.demand_profile_target: ComponentInput = self.add_input(
            self.fuelcellconfig.name,
            FuelCell.DemandProfile,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        self.control_signal: ComponentInput = self.add_input(
            self.fuelcellconfig.name,
            FuelCell.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )
        # =================================================================================================================================
        # Output channels
        # Set state output
        self.fuel_cell_state: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.FuelCellState,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="Current state of the fuel cell",
        )

        self.total_energy_produced: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalEnergyProduced,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Total energy produced for demand",
        )

        self.produced_water: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.WaterflowOutput,
            lt.LoadTypes.WATER,
            lt.Units.ANY,
            output_description="Current water flow rate",
        )

        self.current_power_output: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.PowerOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,  # for hosuehold use case
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.FUEL_CELL,
            ],
            output_description="Current power output",
        )

        self.current_hydrogen_demand: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.HydrogenDemand,
            lt.LoadTypes.HYDROGEN,
            lt.Units.ANY,
            output_description="Current hydrogen demand",
        )

        self.number_cycles: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.NumberofCycles,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total number of activation cycles",
        )

        # Set total ramp-up time output
        self.total_ramp_up_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalRampUpTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-up time",
        )

        # Set total ramp-down time output
        self.total_ramp_down_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalRampDownTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-down time",
        )

        # current hydrogen output
        self.hydrogen_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.CurrentHydrogenFlowRate,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current hydrogen flow rate",
        )
        # Total hydrogen produced
        self.total_hydrogen: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalHydrogenConsumed,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG,
            output_description="Total hydrogen produced during simulation time",
        )
        # current oxygen output
        self.oxygen_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.CurrentOxygenFlowRate,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current oxygen flow rate",
        )
        # Total oxygen produced
        self.total_oxygen: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalOxygenConsumed,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG,
            output_description="Total oxygen produced during simulation time",
        )
        # current water demand
        self.water_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.CurrentWaterFlowRate,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description="Current water flow rate",
        )
        # Total water demand
        self.total_water: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.TotalWaterProduced,
            lt.LoadTypes.WATER,
            lt.Units.KG,
            output_description="Total water demand during simulation time",
        )
        # Current efficiency
        self.current_efficiency_state: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.CurrentEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current efficiency based on the efficiency curve",
        )

        # Total operating time
        self.operating_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.name,
            FuelCell.OperatingTime,
            lt.LoadTypes.TIME,
            lt.Units.HOURS,
            output_description="Total time the electorlyzer is operating (on)",
        )

        # =================================================================================================================================
        # Transfer and storage of states
        self.current_power_state = 0.0
        self.total_ramp_up_count_state = 0.0
        self.total_ramp_down_count_state = 0.0
        self.total_warm_start_count = 0.0
        self.total_cold_start_count = 0.0
        self.total_warm_start_cycles = 0
        self.total_cold_start_cycles = 0
        self.current_warm_start_count = 0.0
        self.current_cold_start_count = 0.0
        self.total_hydrogen_consumed = 0.0
        self.total_oxygen_consumed = 0.0
        self.total_water_produced = 0.0
        self.total_operating_time = 0.0
        self.total_energy = 0.0

        self.current_power_state_previous = self.current_power_state
        self.total_ramp_up_count_state_previous = self.total_ramp_up_count_state
        self.total_ramp_down_count_state_previous = self.total_ramp_down_count_state
        self.total_warm_start_count_previous = self.total_warm_start_count
        self.total_cold_start_count_previous = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_consumed_previous = self.total_hydrogen_consumed
        self.total_oxygen_consumed_previous = self.total_oxygen_consumed
        self.total_water_produced_previous = self.total_water_produced
        self.total_operating_time_previous = self.total_operating_time
        self.total_energy_previous = self.total_energy

    @staticmethod
    def spec_el_stack_consumption_and_polarization_data_config(
        fuel_cell_type, nominal_power, h2_flow_rate, faraday_eff, i_cell_nom
    ):
        """Polarization curve data is provided corresponding to the used fuel cell technology.

        Following this, the auxiliary power of the system and the cell volatge is calculated,
        based on the nominal current density.
        """
        # Load data from the JSON file
        data_file = os.path.join(
            utils.HISIMPATH["inputs"], "polarization_curve_data_fc.json"
        )
        with open(data_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if the provided technology is valid
        if fuel_cell_type not in data:
            raise ValueError(
                f"{fuel_cell_type} is invalid technology. Supported technologies are: {', '.join(data.keys())}"
            )

        # Extract the x and y data points for the selected technology
        i_cell = data[fuel_cell_type]["i_cell"]
        u_cell = data[fuel_cell_type]["U_cell"]

        # constants
        f_constant = 96485  # C/mol
        m_h2 = 2.01588  # g/mol

        # from nom_current_density to aux_power
        spec_el_stack_consumption_nom = (
            faraday_eff * (np.array(u_cell) * (2 * f_constant)) / (m_h2 * 3600)
        )  # kWh/kg
        spec_el_consumption_stack = np.interp(
            i_cell_nom, i_cell, spec_el_stack_consumption_nom
        )

        # calculating aux_power
        aux_power = -nominal_power + (
            spec_el_consumption_stack * h2_flow_rate
        )  # might needs to be set to a constant value

        # interpolarization function
        u_cell_nom = np.interp(i_cell_nom, i_cell, u_cell)  # V

        return i_cell, u_cell, i_cell_nom, u_cell_nom, aux_power

    def h2_consumption_rate(
        self,
        i_cell_nom,
        u_cell_nom,
        nominal_power,
        min_power,
        i_cell,
        u_cell,
        h2_flow_rate,
        aux_power,
        current_power,
        state,
    ):
        """H2 consumption rate.

        Based on the polarisation curve, the spec. electricity demand and
        the current load, the H2 demand and the spec. H2 demand rate
        is calculated.
        """
        nominal_power_density = i_cell_nom * u_cell_nom  # W/cm²

        h2_consumption_rate = np.array(i_cell) / i_cell_nom * h2_flow_rate  # kg/h

        p_cell = np.array(i_cell) * np.array(u_cell)  # W/cm²

        stack_power = p_cell / nominal_power_density * nominal_power

        # Calculates system_power from stack power
        system_power = stack_power - aux_power

        interp_function_h2_consumption_rate = interp1d(
            system_power, h2_consumption_rate, kind="quadratic"
        )

        negative_indices = np.where(system_power < 0.13081412540092785)

        # Entfernen Sie die negativen Werte aus beiden Listen
        filtered_system_power = np.delete(system_power, negative_indices)
        filtered_system_power[0] = 0.0
        filtered_h2_consumption_rate = np.delete(h2_consumption_rate, negative_indices)

        spec_h2_consumption_rate = (
            filtered_system_power / filtered_h2_consumption_rate
        )  # m³/kWh (proportional to the system efficiency)

        interp_function_spec_h2_demand_rate = interp1d(
            filtered_system_power, spec_h2_consumption_rate, kind="quadratic"
        )

        if state == 1 and current_power > min_power:
            # Only consume hydrogen if the system is "on"
            current_h2_demenad_rate = float(
                interp_function_h2_consumption_rate(current_power)
            )

            current_spec_h2_demand_rate = float(
                interp_function_spec_h2_demand_rate(current_power)
            )
            current_eff = current_spec_h2_demand_rate / 33.33  # LHV H2 33.33 kWh/kg
        elif state == 0 and current_power >= min_power:
            # Only consume hydrogen if the system is "on"
            current_h2_demenad_rate = float(
                interp_function_h2_consumption_rate(current_power)
            )

            current_eff = 0.0

        else:
            # No hydrogen consumption if the system is in "standby" or "off"
            current_h2_demenad_rate = 0.0
            current_eff = 0.0

        return current_h2_demenad_rate, current_eff

    def oxygen_demand(self, current_h2_demenad_rate):
        """Oxygen demand.

        Returns the demand flow rate of oxygen,
        based on the current hydrogen flow rate.
        """
        m_o2 = 31.9988
        m_h2 = 2.01588
        m_dot_o2 = (
            (m_o2 / m_h2) * 0.5 * current_h2_demenad_rate
        )  # Kurzweil (2018) - Elektrolyse von Wasser
        return m_dot_o2

    def water_produced(self, current_h2_demenad_rate):
        """Water produced.

        Returns the produced water flow rate,
        based on the current hydrogen flow rate.
        """
        m_h2o = 18.01528
        m_h2 = 2.01588
        m_dot_h2o = (
            m_h2o / m_h2
        ) * current_h2_demenad_rate  # Kurzweil (2018) - Elektrolyse von Wasser
        return m_dot_h2o

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.current_power_state_previous = self.current_power_state
        self.total_ramp_up_count_state_previous = self.total_ramp_up_count_state
        self.total_ramp_down_count_state_previous = self.total_ramp_down_count_state
        self.total_warm_start_count_previous = self.total_warm_start_count
        self.total_cold_start_count_previous = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_consumed_previous = self.total_hydrogen_consumed
        self.total_oxygen_consumed_previous = self.total_oxygen_consumed
        self.total_water_produced_previous = self.total_water_produced
        self.total_operating_time_previous = self.total_operating_time
        self.total_energy_previous = self.total_energy

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.current_power_state = self.current_power_state_previous
        self.total_ramp_up_count_state = self.total_ramp_up_count_state_previous
        self.total_ramp_down_count_state = self.total_ramp_down_count_state_previous
        self.total_warm_start_count = self.total_warm_start_count_previous
        self.total_cold_start_count = self.total_cold_start_count_previous
        self.total_warm_start_cycles = self.total_warm_start_cycles_previous
        self.total_cold_start_cycles = self.total_cold_start_cycles_previous
        self.current_warm_start_count = self.current_warm_start_count_previous
        self.current_cold_start_count = self.current_cold_start_count_previous
        self.total_hydrogen_consumed = self.total_hydrogen_consumed_previous
        self.total_oxygen_consumed = self.total_oxygen_consumed_previous
        self.total_water_produced = self.total_water_produced_previous
        self.total_operating_time = self.total_operating_time_previous
        self.total_energy = self.total_energy_previous

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        # Variables
        seconds_per_timestep = (
            self.my_simulation_parameters.seconds_per_timestep
        )  # [s/timestep]

        power_demand = stsv.get_input_value(self.demand_profile_target)
        # print("power_demand: ", power_demand)
        state = stsv.get_input_value(self.control_signal)

        # ramp up per timestep calculation
        ramp_up_per_timestep = (
            self.nom_output * self.ramp_up_rate * seconds_per_timestep
        )
        total_ramp_up_per_timestep = ramp_up_per_timestep

        # ramp down per timestep calculation
        ramp_down_per_timestep = (
            self.nom_output * self.ramp_down_rate * seconds_per_timestep
        )
        total_ramp_down_per_timestep = ramp_down_per_timestep

        # calculating the current power demand based on the previous state
        new_target = abs(power_demand - self.current_power_state)
        if state == 1:
            self.total_operating_time += seconds_per_timestep / 3600
            """the ramping process"""
            if new_target == self.nom_output:
                # print("punkt 2")
                self.current_power_state = self.nom_output
                self.total_ramp_up_count_state += 0
                self.total_ramp_down_count_state += 0
            if new_target == 0:
                # print("punkt 3")
                self.current_power_state = self.current_power_state
                self.total_ramp_up_count_state += 0
                self.total_ramp_down_count_state += 0

            # Ramping up
            if (
                new_target >= total_ramp_up_per_timestep
                and self.current_power_state < power_demand
            ):
                # print("punkt 4")
                self.total_ramp_up_count_state += seconds_per_timestep
                self.current_power_state += total_ramp_up_per_timestep
                # print("self.current_power_state nach punkt 4: ", self.current_power_state)
            elif (
                self.current_power_state < power_demand
                and new_target < total_ramp_up_per_timestep
            ):
                # print("punkt 5")
                percentage_ramp_up_per_timestep = (
                    new_target / total_ramp_up_per_timestep
                )
                self.total_ramp_up_count_state += (
                    percentage_ramp_up_per_timestep * seconds_per_timestep
                )
                if self.current_power_state == 0:
                    # print("punkt 6")
                    self.current_power_state += power_demand
                else:
                    # print("punkt 7")
                    self.current_power_state += new_target

            # Ramping down
            elif (
                total_ramp_down_per_timestep <= new_target
                and power_demand < self.current_power_state
            ):
                # print("punkt 8")
                self.total_ramp_down_count_state += seconds_per_timestep
                self.current_power_state -= new_target

            elif (
                power_demand < self.current_power_state
                and new_target < total_ramp_down_per_timestep
            ):
                # print("punkt 9")
                percentage_ramp_down_per_timestep = (
                    new_target / total_ramp_down_per_timestep
                )
                self.total_ramp_down_count_state += (
                    percentage_ramp_down_per_timestep * seconds_per_timestep
                )
                self.current_power_state -= new_target

        elif state == 0:
            # print("punkt 10")
            self.total_ramp_up_count_state += 0
            self.total_ramp_down_count_state += 0
            self.current_power_state = 0.0

        elif state == -1:
            # print("punkt 11")
            self.total_ramp_up_count_state += 0
            self.total_ramp_down_count_state += 0
            self.current_power_state = 0.0

        # Applying polarization curve data
        (
            i_cell,
            u_cell,
            i_cell_nom,
            u_cell_nom,
            aux_power,
        ) = self.spec_el_stack_consumption_and_polarization_data_config(
            self.technology_type,
            self.nom_output,
            self.nom_h2_flow_rate,
            self.faraday_eff,
            self.i_cell_nom,
        )
        # Current hydrogen prduction and specific hydrogen production rate

        # self.current_spec_h2_demand_rate,
        current_h2_demand_rate, current_eff = self.h2_consumption_rate(
            i_cell_nom,
            u_cell_nom,
            self.nom_output,
            self.min_output,
            i_cell,
            u_cell,
            self.nom_h2_flow_rate,
            aux_power,
            self.current_power_state,
            state,
        )
        # Current oxygen and water flow rate
        current_flow_rate_oxygen = self.oxygen_demand(current_h2_demand_rate)
        current_flow_rate_water = self.water_produced(current_h2_demand_rate)
        # Calculating total amount of hydrogen, oxygen and water
        total_hydrogen_consumed_in_timestep = current_h2_demand_rate * (
            seconds_per_timestep / 3600
        )
        self.total_hydrogen_consumed += total_hydrogen_consumed_in_timestep
        total_oxygen_consumed_in_timestep = current_flow_rate_oxygen * (
            seconds_per_timestep / 3600
        )
        self.total_oxygen_consumed += total_oxygen_consumed_in_timestep
        total_water_produced_in_timestep = current_flow_rate_water * (
            seconds_per_timestep / 3600
        )
        self.total_water_produced += total_water_produced_in_timestep

        self.total_energy += self.current_power_state * (seconds_per_timestep / 3600)
        # Initializing outputs
        stsv.set_output_value(self.hydrogen_flow_rate, current_h2_demand_rate)
        stsv.set_output_value(self.oxygen_flow_rate, current_flow_rate_oxygen)
        stsv.set_output_value(self.water_flow_rate, current_flow_rate_water)
        stsv.set_output_value(self.fuel_cell_state, state)
        stsv.set_output_value(
            self.current_power_output, (self.current_power_state * 1000)
        )  # transform kW to WATT for EMS
        stsv.set_output_value(self.total_energy_produced, self.total_energy)
        stsv.set_output_value(self.total_ramp_up_time, self.total_ramp_up_count_state)
        stsv.set_output_value(
            self.total_ramp_down_time, self.total_ramp_down_count_state
        )
        stsv.set_output_value(self.total_hydrogen, self.total_hydrogen_consumed)
        stsv.set_output_value(self.total_oxygen, self.total_oxygen_consumed)
        stsv.set_output_value(self.total_water, self.total_water_produced)
        stsv.set_output_value(self.operating_time, self.total_operating_time)
        stsv.set_output_value(self.current_efficiency_state, current_eff)

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        lines = []
        for config_string in self.fuelcellconfig.get_string_dict():
            lines.append(config_string)
        lines.append("Component Name" + str(self.component_name))
        lines.append(
            "Total operating time during simulation: "
            + str(self.total_operating_time)
            + " [h]"
        )
        lines.append(
            "Total hydrogen consumed during simulation: "
            + str(self.total_hydrogen_consumed)
            + " [kg]"
        )
        lines.append(
            "Total oxygen consumed during simulation: "
            + str(self.total_oxygen_consumed)
            + " [kg]"
        )
        lines.append(
            "Total water demand during simulation: "
            + str(self.total_water_produced)
            + " [kg]"
        )
        lines.append(
            "Total energy produced during simulation: "
            + str(self.total_energy)
            + " [kWh]"
        )
        lines.append(
            "Total ramp-up time during simulation: "
            + str(self.total_ramp_up_count_state)
            + " [kg]"
        )
        lines.append(
            "Total ramp-down time during simulation: "
            + str(self.total_ramp_down_count_state)
            + " [kg]"
        )
        return lines
