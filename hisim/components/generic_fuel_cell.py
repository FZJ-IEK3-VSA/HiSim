"""Generic fuel cell component modelling hydrogen-to-electricity conversion."""

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from pathlib import Path
import json
from typing import Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from scipy.interpolate import interp1d
import numpy as np

# Import modules from HiSim
from hisim.component import SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.config import ConfigBase, ComponentID, DisplayConfig
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
class FuelCellConfig(ConfigBase):
    """Configuration of the `FuelCell` component.

    Holds configuration parameters for a PEM fuel cell: nominal, minimum
    and maximum electrical power output, nominal hydrogen flow rate,
    Faraday efficiency, nominal cell current, and ramp-up/ramp-down rates.
    """

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return FuelCell.get_full_classname()

    component_id: ComponentID
    type: str
    nom_output_in_kilowatt: float  # [kW]
    max_output_in_kilowatt: float  # [kW]
    min_output_in_kilowatt: float  # [kW]
    nom_h2_flow_rate_in_m3_per_h: float  # [m^3/h]
    faraday_eff: float
    i_cell_nom_in_ampere_per_cm2: float
    ramp_up_rate_in_percent_per_s: float  # [%/s]
    ramp_down_rate_in_percent_per_s: float  # [%/s]
    # H_s_h2 = 33.33 #kWh/kg

    @classmethod
    def get_default_pem_fuel_cell_config(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> "FuelCellConfig":
        """Returns a default `FuelCellConfig` for a PEM fuel cell."""
        if component_id is None:
            component_id = ComponentID(name="PEM_Fuel_Cell")
        return FuelCellConfig(
            component_id=component_id,
            type="PEM",
            nom_output_in_kilowatt=100.0,  # [kW]
            max_output_in_kilowatt=110.0,  # [kW]
            min_output_in_kilowatt=10.0,  # [kW]
            nom_h2_flow_rate_in_m3_per_h=65.64,  # [m^3/h]
            faraday_eff=0.90,
            i_cell_nom_in_ampere_per_cm2=0.52,
            ramp_up_rate_in_percent_per_s=0.1,  # [%/s]
            ramp_down_rate_in_percent_per_s=0.2,  # [%/s]
            # H_s_h2 = 33.33,
        )

    @staticmethod
    def read_config(fuel_cell_name):
        """Read config."""
        config_file = Path(utils.HISIMPATH["inputs"]) / "fuel_cell_manufacturer_config.json"
        with config_file.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def config_fuel_cell(
        cls,
        fuel_cell_name: str,
        component_id: Optional[ComponentID] = None,
    ) -> "FuelCellConfig":
        """Get config of fuel cell."""
        if component_id is None:
            component_id = ComponentID(name="FuelCell")
        config_json = cls.read_config(fuel_cell_name)
        config = FuelCellConfig(
            component_id=component_id,  # config_json.get("name", "")
            type=config_json.get("type", ""),
            nom_output_in_kilowatt=config_json.get("nom_output", 0.0),
            max_output_in_kilowatt=config_json.get("max_output", 0.0),
            min_output_in_kilowatt=config_json.get("min_output", 0.0),
            nom_h2_flow_rate_in_m3_per_h=config_json.get("nom_h2_flow_rate", 0.0),
            faraday_eff=config_json.get("faraday_eff", 0.0),
            i_cell_nom_in_ampere_per_cm2=config_json.get("i_cell_nom", 0.0),
            ramp_up_rate_in_percent_per_s=config_json.get("ramp_up_rate", 0.0),
            ramp_down_rate_in_percent_per_s=config_json.get("ramp_down_rate", 0.0),
        )
        return config


class FuelCell(cp.Component):
    """PEM fuel cell component that converts hydrogen into electricity.

    The fuel cell consumes hydrogen and oxygen to generate electrical power,
    producing water as a by-product. The power output tracks the configured
    electricity demand subject to minimum/maximum power limits and ramp-rate
    constraints. Efficiency is derived from a polarization curve that maps the
    cell current density to the cell voltage, from which the hydrogen
    consumption rate is interpolated for the current operating point.

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
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Constructs all the neccessary attributes."""
        self.fuelcellconfig = config

        self.technology_type = config.type
        self.nom_output_in_kilowatt = config.nom_output_in_kilowatt
        self.max_output_in_kilowatt = config.max_output_in_kilowatt
        self.min_output_in_kilowatt = config.min_output_in_kilowatt
        self.nom_h2_flow_rate_in_m3_per_h = config.nom_h2_flow_rate_in_m3_per_h
        self.faraday_eff = config.faraday_eff
        self.i_cell_nom_in_ampere_per_cm2 = config.i_cell_nom_in_ampere_per_cm2
        self.ramp_up_rate_in_percent_per_s = config.ramp_up_rate_in_percent_per_s
        self.ramp_down_rate_in_percent_per_s = config.ramp_down_rate_in_percent_per_s

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # =================================================================================================================================
        # Input channels
        self.demand_profile_target: ComponentInput = self.add_input(
            self.fuelcellconfig.component_id.name,
            FuelCell.DemandProfile,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        self.control_signal: ComponentInput = self.add_input(
            self.fuelcellconfig.component_id.name,
            FuelCell.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )
        # =================================================================================================================================
        # Output channels
        # Set state output
        self.fuel_cell_state: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.FuelCellState,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            output_description="Current state of the fuel cell",
        )

        self.total_energy_produced: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalEnergyProduced,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Total energy produced for demand",
        )

        self.produced_water: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.WaterflowOutput,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description="Current water flow rate",
        )

        self.current_power_output: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
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
            self.fuelcellconfig.component_id.name,
            FuelCell.HydrogenDemand,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current hydrogen demand",
        )

        self.number_cycles: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.NumberofCycles,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Total number of activation cycles",
        )

        # Set total ramp-up time output
        self.total_ramp_up_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalRampUpTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-up time",
        )

        # Set total ramp-down time output
        self.total_ramp_down_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalRampDownTime,
            lt.LoadTypes.TIME,
            lt.Units.SECONDS,
            output_description="Total ramp-down time",
        )

        # current hydrogen output
        self.hydrogen_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.CurrentHydrogenFlowRate,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current hydrogen flow rate",
        )
        # Total hydrogen produced
        self.total_hydrogen: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalHydrogenConsumed,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            output_description="Total hydrogen produced during simulation time",
        )
        # current oxygen output
        self.oxygen_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.CurrentOxygenFlowRate,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current oxygen flow rate",
        )
        # Total oxygen produced
        self.total_oxygen: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalOxygenConsumed,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG,
            output_description="Total oxygen produced during simulation time",
        )
        # current water demand
        self.water_flow_rate: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.CurrentWaterFlowRate,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description="Current water flow rate",
        )
        # Total water demand
        self.total_water: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.TotalWaterProduced,
            lt.LoadTypes.WATER,
            lt.Units.KG,
            output_description="Total water demand during simulation time",
        )
        # Current efficiency
        self.current_efficiency_state: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.CurrentEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current efficiency based on the efficiency curve",
        )

        # Total operating time
        self.operating_time: ComponentOutput = self.add_output(
            self.fuelcellconfig.component_id.name,
            FuelCell.OperatingTime,
            lt.LoadTypes.TIME,
            lt.Units.HOURS,
            output_description="Total time the electorlyzer is operating (on)",
        )

        # =================================================================================================================================
        # Transfer and storage of states
        self.current_power_state_in_kilowatt = 0.0
        self.total_ramp_up_count_state_in_s = 0.0
        self.total_ramp_down_count_state_in_s = 0.0
        self.total_warm_start_count = 0.0
        self.total_cold_start_count = 0.0
        self.total_warm_start_cycles = 0
        self.total_cold_start_cycles = 0
        self.current_warm_start_count = 0.0
        self.current_cold_start_count = 0.0
        self.total_hydrogen_consumed_in_kg = 0.0
        self.total_oxygen_consumed_in_kg = 0.0
        self.total_water_produced_in_kg = 0.0
        self.total_operating_time_in_h = 0.0
        self.total_energy_in_kilowatt_hour = 0.0

        self.current_power_state_in_kilowatt_previous = self.current_power_state_in_kilowatt
        self.total_ramp_up_count_state_in_s_previous = self.total_ramp_up_count_state_in_s
        self.total_ramp_down_count_state_in_s_previous = self.total_ramp_down_count_state_in_s
        self.total_warm_start_count_previous = self.total_warm_start_count
        self.total_cold_start_count_previous = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_consumed_in_kg_previous = self.total_hydrogen_consumed_in_kg
        self.total_oxygen_consumed_in_kg_previous = self.total_oxygen_consumed_in_kg
        self.total_water_produced_in_kg_previous = self.total_water_produced_in_kg
        self.total_operating_time_in_h_previous = self.total_operating_time_in_h
        self.total_energy_in_kilowatt_hour_previous = self.total_energy_in_kilowatt_hour

    @staticmethod
    def spec_el_stack_consumption_and_polarization_data_config(
        fuel_cell_type, nominal_power_in_kilowatt, h2_flow_rate_in_kg_per_h, faraday_eff, i_cell_nom_in_ampere_per_cm2
    ):
        """Polarization curve data is provided corresponding to the used fuel cell technology.

        Following this, the auxiliary power of the system and the cell volatge is calculated,
        based on the nominal current density.
        """
        # Load data from the JSON file
        data_file = Path(utils.HISIMPATH["inputs"]) / "polarization_curve_data_fc.json"
        with data_file.open("r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if the provided technology is valid
        if fuel_cell_type not in data:
            raise ValueError(
                f"{fuel_cell_type} is invalid technology. Supported technologies are: {', '.join(data.keys())}"
            )

        # Extract the x and y data points for the selected technology
        i_cell_in_ampere_per_cm2 = data[fuel_cell_type]["i_cell"]
        u_cell_in_volt = data[fuel_cell_type]["U_cell"]

        # constants
        f_constant_in_coulomb_per_mol = 96485  # C/mol
        m_h2_in_g_per_mol = 2.01588  # g/mol

        # from nom_current_density to aux_power_in_kilowatt
        spec_el_stack_consumption_nom_in_kilowatt_hour_per_kg = (
            faraday_eff * (np.array(u_cell_in_volt) * (2 * f_constant_in_coulomb_per_mol)) / (m_h2_in_g_per_mol * 3600)
        )  # kWh/kg
        spec_el_consumption_stack_in_kilowatt_hour_per_kg = np.interp(
            i_cell_nom_in_ampere_per_cm2, i_cell_in_ampere_per_cm2, spec_el_stack_consumption_nom_in_kilowatt_hour_per_kg
        )

        # calculating aux_power_in_kilowatt
        aux_power_in_kilowatt = -nominal_power_in_kilowatt + (
            spec_el_consumption_stack_in_kilowatt_hour_per_kg * h2_flow_rate_in_kg_per_h
        )  # might needs to be set to a constant value

        # interpolarization function
        u_cell_nom_in_volt = np.interp(i_cell_nom_in_ampere_per_cm2, i_cell_in_ampere_per_cm2, u_cell_in_volt)  # V

        return i_cell_in_ampere_per_cm2, u_cell_in_volt, i_cell_nom_in_ampere_per_cm2, u_cell_nom_in_volt, aux_power_in_kilowatt

    def h2_consumption_rate(
        self,
        i_cell_nom_in_ampere_per_cm2,
        u_cell_nom_in_volt,
        nominal_power_in_kilowatt,
        min_power_in_kilowatt,
        i_cell_in_ampere_per_cm2,
        u_cell_in_volt,
        h2_flow_rate_in_kg_per_h,
        aux_power_in_kilowatt,
        current_power_in_kilowatt,
        state,
    ):
        """H2 consumption rate.

        Based on the polarisation curve, the spec. electricity demand and
        the current load, the H2 demand and the spec. H2 demand rate
        is calculated.
        """
        nominal_power_density_in_watt_per_cm2 = i_cell_nom_in_ampere_per_cm2 * u_cell_nom_in_volt  # W/cm²

        h2_consumption_rate_in_kg_per_h = np.array(i_cell_in_ampere_per_cm2) / i_cell_nom_in_ampere_per_cm2 * h2_flow_rate_in_kg_per_h  # kg/h

        p_cell_in_watt_per_cm2 = np.array(i_cell_in_ampere_per_cm2) * np.array(u_cell_in_volt)  # W/cm²

        stack_power_in_kilowatt = p_cell_in_watt_per_cm2 / nominal_power_density_in_watt_per_cm2 * nominal_power_in_kilowatt

        # Calculates system_power_in_kilowatt from stack power
        system_power_in_kilowatt = stack_power_in_kilowatt - aux_power_in_kilowatt

        interp_function_h2_consumption_rate = interp1d(system_power_in_kilowatt, h2_consumption_rate_in_kg_per_h, kind="quadratic")

        negative_indices = np.where(system_power_in_kilowatt < 0.13081412540092785)

        # Entfernen Sie die negativen Werte aus beiden Listen
        filtered_system_power = np.delete(system_power_in_kilowatt, negative_indices)
        filtered_system_power[0] = 0.0
        filtered_h2_consumption_rate = np.delete(h2_consumption_rate_in_kg_per_h, negative_indices)

        spec_h2_consumption_rate = (
            filtered_system_power / filtered_h2_consumption_rate
        )  # m³/kWh (proportional to the system efficiency)

        interp_function_spec_h2_demand_rate = interp1d(
            filtered_system_power, spec_h2_consumption_rate, kind="quadratic"
        )

        if state == 1 and current_power_in_kilowatt > min_power_in_kilowatt:
            # Only consume hydrogen if the system is "on"
            current_h2_demand_rate_in_kg_per_h = float(interp_function_h2_consumption_rate(current_power_in_kilowatt))

            current_spec_h2_demand_rate = float(interp_function_spec_h2_demand_rate(current_power_in_kilowatt))
            current_eff = current_spec_h2_demand_rate / 33.33  # LHV H2 33.33 kWh/kg
        elif state == 0 and current_power_in_kilowatt >= min_power_in_kilowatt:
            # Only consume hydrogen if the system is "on"
            current_h2_demand_rate_in_kg_per_h = float(interp_function_h2_consumption_rate(current_power_in_kilowatt))

            current_eff = 0.0

        else:
            # No hydrogen consumption if the system is in "standby" or "off"
            current_h2_demand_rate_in_kg_per_h = 0.0
            current_eff = 0.0

        return current_h2_demand_rate_in_kg_per_h, current_eff

    def oxygen_demand(self, current_h2_demand_rate_in_kg_per_h):
        """Oxygen demand.

        Returns the demand flow rate of oxygen,
        based on the current hydrogen flow rate.
        """
        m_o2 = 31.9988
        m_h2_in_g_per_mol = 2.01588
        m_dot_o2 = (m_o2 / m_h2_in_g_per_mol) * 0.5 * current_h2_demand_rate_in_kg_per_h  # Kurzweil (2018) - Elektrolyse von Wasser
        return m_dot_o2

    def water_produced(self, current_h2_demand_rate_in_kg_per_h):
        """Water produced.

        Returns the produced water flow rate,
        based on the current hydrogen flow rate.
        """
        m_h2o = 18.01528
        m_h2_in_g_per_mol = 2.01588
        m_dot_h2o = (m_h2o / m_h2_in_g_per_mol) * current_h2_demand_rate_in_kg_per_h  # Kurzweil (2018) - Elektrolyse von Wasser
        return m_dot_h2o

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.current_power_state_in_kilowatt_previous = self.current_power_state_in_kilowatt
        self.total_ramp_up_count_state_in_s_previous = self.total_ramp_up_count_state_in_s
        self.total_ramp_down_count_state_in_s_previous = self.total_ramp_down_count_state_in_s
        self.total_warm_start_count_previous = self.total_warm_start_count
        self.total_cold_start_count_previous = self.total_cold_start_count
        self.total_warm_start_cycles_previous = self.total_warm_start_cycles
        self.total_cold_start_cycles_previous = self.total_cold_start_cycles
        self.current_warm_start_count_previous = self.current_warm_start_count
        self.current_cold_start_count_previous = self.current_cold_start_count
        self.total_hydrogen_consumed_in_kg_previous = self.total_hydrogen_consumed_in_kg
        self.total_oxygen_consumed_in_kg_previous = self.total_oxygen_consumed_in_kg
        self.total_water_produced_in_kg_previous = self.total_water_produced_in_kg
        self.total_operating_time_in_h_previous = self.total_operating_time_in_h
        self.total_energy_in_kilowatt_hour_previous = self.total_energy_in_kilowatt_hour

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.current_power_state_in_kilowatt = self.current_power_state_in_kilowatt_previous
        self.total_ramp_up_count_state_in_s = self.total_ramp_up_count_state_in_s_previous
        self.total_ramp_down_count_state_in_s = self.total_ramp_down_count_state_in_s_previous
        self.total_warm_start_count = self.total_warm_start_count_previous
        self.total_cold_start_count = self.total_cold_start_count_previous
        self.total_warm_start_cycles = self.total_warm_start_cycles_previous
        self.total_cold_start_cycles = self.total_cold_start_cycles_previous
        self.current_warm_start_count = self.current_warm_start_count_previous
        self.current_cold_start_count = self.current_cold_start_count_previous
        self.total_hydrogen_consumed_in_kg = self.total_hydrogen_consumed_in_kg_previous
        self.total_oxygen_consumed_in_kg = self.total_oxygen_consumed_in_kg_previous
        self.total_water_produced_in_kg = self.total_water_produced_in_kg_previous
        self.total_operating_time_in_h = self.total_operating_time_in_h_previous
        self.total_energy_in_kilowatt_hour = self.total_energy_in_kilowatt_hour_previous

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""
        if force_convergence:
            return

        # Variables
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep  # [s/timestep]

        power_demand_in_kilowatt = stsv.get_input_value(self.demand_profile_target)
        # print("power_demand: ", power_demand_in_kilowatt)
        state = stsv.get_input_value(self.control_signal)

        # ramp up per timestep calculation
        ramp_up_per_timestep_in_kilowatt = self.nom_output_in_kilowatt * self.ramp_up_rate_in_percent_per_s * seconds_per_timestep
        total_ramp_up_per_timestep = ramp_up_per_timestep_in_kilowatt

        # ramp down per timestep calculation
        ramp_down_per_timestep_in_kilowatt = self.nom_output_in_kilowatt * self.ramp_down_rate_in_percent_per_s * seconds_per_timestep
        total_ramp_down_per_timestep = ramp_down_per_timestep_in_kilowatt

        # calculating the current power demand based on the previous state
        new_target = abs(power_demand_in_kilowatt - self.current_power_state_in_kilowatt)
        if state == 1:
            self.total_operating_time_in_h += seconds_per_timestep / 3600
            """the ramping process"""
            if new_target == self.nom_output_in_kilowatt:
                # print("punkt 2")
                self.current_power_state_in_kilowatt = self.nom_output_in_kilowatt
                self.total_ramp_up_count_state_in_s += 0
                self.total_ramp_down_count_state_in_s += 0
            if new_target == 0:
                # print("punkt 3")
                self.current_power_state_in_kilowatt = self.current_power_state_in_kilowatt
                self.total_ramp_up_count_state_in_s += 0
                self.total_ramp_down_count_state_in_s += 0

            # Ramping up
            if new_target >= total_ramp_up_per_timestep and self.current_power_state_in_kilowatt < power_demand_in_kilowatt:
                # print("punkt 4")
                self.total_ramp_up_count_state_in_s += seconds_per_timestep
                self.current_power_state_in_kilowatt += total_ramp_up_per_timestep
                # print("self.current_power_state_in_kilowatt nach punkt 4: ", self.current_power_state_in_kilowatt)
            elif self.current_power_state_in_kilowatt < power_demand_in_kilowatt and new_target < total_ramp_up_per_timestep:
                # print("punkt 5")
                percentage_ramp_up_per_timestep = new_target / total_ramp_up_per_timestep
                self.total_ramp_up_count_state_in_s += percentage_ramp_up_per_timestep * seconds_per_timestep
                if self.current_power_state_in_kilowatt == 0:
                    # print("punkt 6")
                    self.current_power_state_in_kilowatt += power_demand_in_kilowatt
                else:
                    # print("punkt 7")
                    self.current_power_state_in_kilowatt += new_target

            # Ramping down
            elif total_ramp_down_per_timestep <= new_target and power_demand_in_kilowatt < self.current_power_state_in_kilowatt:
                # print("punkt 8")
                self.total_ramp_down_count_state_in_s += seconds_per_timestep
                self.current_power_state_in_kilowatt -= new_target

            elif power_demand_in_kilowatt < self.current_power_state_in_kilowatt and new_target < total_ramp_down_per_timestep:
                # print("punkt 9")
                percentage_ramp_down_per_timestep = new_target / total_ramp_down_per_timestep
                self.total_ramp_down_count_state_in_s += percentage_ramp_down_per_timestep * seconds_per_timestep
                self.current_power_state_in_kilowatt -= new_target

        elif state == 0:
            # print("punkt 10")
            self.total_ramp_up_count_state_in_s += 0
            self.total_ramp_down_count_state_in_s += 0
            self.current_power_state_in_kilowatt = 0.0

        elif state == -1:
            # print("punkt 11")
            self.total_ramp_up_count_state_in_s += 0
            self.total_ramp_down_count_state_in_s += 0
            self.current_power_state_in_kilowatt = 0.0

        # Applying polarization curve data
        (
            i_cell_in_ampere_per_cm2,
            u_cell_in_volt,
            i_cell_nom_in_ampere_per_cm2,
            u_cell_nom_in_volt,
            aux_power_in_kilowatt,
        ) = self.spec_el_stack_consumption_and_polarization_data_config(
            self.technology_type,
            self.nom_output_in_kilowatt,
            self.nom_h2_flow_rate_in_m3_per_h,
            self.faraday_eff,
            self.i_cell_nom_in_ampere_per_cm2,
        )
        # Current hydrogen prduction and specific hydrogen production rate

        # self.current_spec_h2_demand_rate,
        current_h2_demand_rate_in_kg_per_h, current_eff = self.h2_consumption_rate(
            i_cell_nom_in_ampere_per_cm2,
            u_cell_nom_in_volt,
            self.nom_output_in_kilowatt,
            self.min_output_in_kilowatt,
            i_cell_in_ampere_per_cm2,
            u_cell_in_volt,
            self.nom_h2_flow_rate_in_m3_per_h,
            aux_power_in_kilowatt,
            self.current_power_state_in_kilowatt,
            state,
        )
        # Current oxygen and water flow rate
        current_flow_rate_oxygen_in_kg_per_h = self.oxygen_demand(current_h2_demand_rate_in_kg_per_h)
        current_flow_rate_water_in_kg_per_h = self.water_produced(current_h2_demand_rate_in_kg_per_h)
        # Calculating total amount of hydrogen, oxygen and water
        total_hydrogen_consumed_in_kg = current_h2_demand_rate_in_kg_per_h * (seconds_per_timestep / 3600)
        self.total_hydrogen_consumed_in_kg += total_hydrogen_consumed_in_kg
        total_oxygen_consumed_in_kg = current_flow_rate_oxygen_in_kg_per_h * (seconds_per_timestep / 3600)
        self.total_oxygen_consumed_in_kg += total_oxygen_consumed_in_kg
        total_water_produced_in_kg = current_flow_rate_water_in_kg_per_h * (seconds_per_timestep / 3600)
        self.total_water_produced_in_kg += total_water_produced_in_kg

        self.total_energy_in_kilowatt_hour += self.current_power_state_in_kilowatt * (seconds_per_timestep / 3600)
        # Initializing outputs
        stsv.set_output_value(self.hydrogen_flow_rate, current_h2_demand_rate_in_kg_per_h)
        stsv.set_output_value(self.oxygen_flow_rate, current_flow_rate_oxygen_in_kg_per_h)
        stsv.set_output_value(self.water_flow_rate, current_flow_rate_water_in_kg_per_h)
        stsv.set_output_value(self.fuel_cell_state, state)
        stsv.set_output_value(
            self.current_power_output, (self.current_power_state_in_kilowatt * 1000)
        )  # transform kW to WATT for EMS
        stsv.set_output_value(self.total_energy_produced, self.total_energy_in_kilowatt_hour)
        stsv.set_output_value(self.total_ramp_up_time, self.total_ramp_up_count_state_in_s)
        stsv.set_output_value(self.total_ramp_down_time, self.total_ramp_down_count_state_in_s)
        stsv.set_output_value(self.total_hydrogen, self.total_hydrogen_consumed_in_kg)
        stsv.set_output_value(self.total_oxygen, self.total_oxygen_consumed_in_kg)
        stsv.set_output_value(self.total_water, self.total_water_produced_in_kg)
        stsv.set_output_value(self.operating_time, self.total_operating_time_in_h)
        stsv.set_output_value(self.current_efficiency_state, current_eff)

    def write_to_report(self) -> list[str]:
        """Writes a report."""
        lines = list(self.fuelcellconfig.get_string_dict())
        lines.append(f"Component Name{self.component_name}")
        lines.append(f"Total operating time during simulation: {self.total_operating_time_in_h} [h]")
        lines.append(f"Total hydrogen consumed during simulation: {self.total_hydrogen_consumed_in_kg} [kg]")
        lines.append(f"Total oxygen consumed during simulation: {self.total_oxygen_consumed_in_kg} [kg]")
        lines.append(f"Total water demand during simulation: {self.total_water_produced_in_kg} [kg]")
        lines.append(f"Total energy produced during simulation: {self.total_energy_in_kilowatt_hour} [kWh]")
        lines.append(f"Total ramp-up time during simulation: {self.total_ramp_up_count_state_in_s} [s]")
        lines.append(f"Total ramp-down time during simulation: {self.total_ramp_down_count_state_in_s} [s]")
        return lines
