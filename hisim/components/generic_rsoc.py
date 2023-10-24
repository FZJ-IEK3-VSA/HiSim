"""rSOC."""
# clean
import os
import json
from dataclasses import dataclass
from dataclasses_json import dataclass_json
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
class RsocConfig(cp.ConfigBase):

    """Configuration of the rSOC."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Rsoc.get_full_classname()

    name: str
    # SOEC
    nom_load_soec: float  # [kW]
    min_load_soec: float  # [kW]
    max_load_soec: float  # [kW]
    faraday_eff_soec: float
    ramp_up_rate_soec: float  # [%/s]
    ramp_down_rate_soec: float  # [%/s]
    # SOFC
    nom_power_sofc: float  # [kW]
    min_power_sofc: float  # [kW]
    max_power_sofc: float  # [kW]
    faraday_eff_sofc: float
    ramp_up_rate_sofc: float  # [%/s]
    ramp_down_rate_sofc: float  # [%/s]

    @staticmethod
    def read_config(rsoc_name):
        """Opens the according JSON-file, based on the rSOC_name."""

        config_file = os.path.join(
            utils.HISIMPATH["inputs"], "rSOC_manufacturer_config.json"
        )
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("rSOC variants", {}).get(rsoc_name, {})

    @classmethod
    def config_rsoc(cls, rsoc_name):
        """Initializes the config variables based on the JSON-file."""

        config_json = cls.read_config(rsoc_name)
        config = RsocConfig(
            name=rsoc_name,  # config_json.get("name", "")
            nom_load_soec=config_json.get("nom_load_soec", 0.0),
            min_load_soec=config_json.get("min_load_soec", 0.0),
            max_load_soec=config_json.get("max_load_soec", 0.0),
            faraday_eff_soec=config_json.get("faraday_eff_soec", 0.0),
            ramp_up_rate_soec=config_json.get("ramp_up_rate_soec", 0.0),
            ramp_down_rate_soec=config_json.get("ramp_down_rate_soec", 0.0),
            nom_power_sofc=config_json.get("nom_power_sofc", 0.0),
            min_power_sofc=config_json.get("min_power_sofc", 0.0),
            max_power_sofc=config_json.get("max_power_sofc", 0.0),
            faraday_eff_sofc=config_json.get("faraday_eff_sofc", 0.0),
            ramp_up_rate_sofc=config_json.get("ramp_up_rate_sofc", 0.0),
            ramp_down_rate_sofc=config_json.get("ramp_down_rate_sofc", 0.0),
        )
        return config


class Rsoc(cp.Component):

    """Rsoc component class."""

    # Inputs
    PowerInput = "PowerInput"
    RSOCInputState = "RSOCInputState"

    # SOEC Outputs
    SOECState = "SOECState"
    SOECCurrentLoad = "SOECCurrentLoadConsumed"  # current load regarding the ramp-up and -down process
    TotalEnergyConsumed = "TotalEnergyConsumed"

    SOECCurrentHydrogenFlowRate = "SOECCurrentHydrogenFlowRate"
    SOECCurrentOxygenFlowRate = "SOECCurrentOxygenFlowRate"
    SOECCurrentWaterFlowRate = "SOECCurrentWaterFlowRate"
    SOECTotalHydrogenProduced = "SOECTotalHydrogenProduced"
    SOECTotalOxygenProduced = "SOECTotalOxygenProduced"
    SOECTotalWaterDemand = "SOECTotalWaterDemand"

    SOECCurrentEfficiency = "SOECCurrentEfficiency"

    SOECTotalWarmStartTime = "SOECTotalWarmStartTime"
    SOECWarmStartCycles = "SOECWarmStartCycles"
    SOECTotalColdStartTime = "SOECTotalColdStartTime"
    SOECColdStartCycles = "SOECColdStartCycles"
    SOECTotalRampUpTime = "SOECTotalRampUpTime"
    SOECTotalRampDownTime = "SOECTotalRampDownTime"

    # SOFC Outputs
    SOFCState = "SOFCState"
    SOFCCurrentOutput = (
        "SOFCCurrentPowerOutput"  # current load regarding the ramp-up and -down process
    )
    TotalEnergyProduced = "TotalEnergyProduced"

    SOFCCurrentHydrogenFlowRate = "SOFCCurrentHydrogenFlowRate"
    SOFCCurrentOxygenFlowRate = "SOECCurrentOxygenFlowRate"
    SOFCCurrentWaterFlowRate = "SOECCurrentWaterFlowRate"
    SOFCTotalHydrogenConsumed = "SOFCTotalHydrogenConsumed"
    SOFCTotalOxygenConsumed = "SOFCTotalOxygenConsumed"
    SOFCTotalWaterProduced = "SOFCTotalWaterProduced"

    SOFCCurrentEfficiency = "SOFCCurrentEfficiency"

    SOFCTotalWarmStartTime = "SOECTotalWarmStartTime"
    SOFCWarmStartCycles = "SOECWarmStartCycles"
    SOFCTotalColdStartTime = "SOECTotalColdStartTime"
    SOFCColdStartCycles = "SOECColdStartCycles"
    SOFCTotalRampUpTime = "SOECTotalRampUpTime"
    SOFCTotalRampDownTime = "SOECTotalRampDownTime"

    RSOCOperatingTime = "rSOCOperatingTime"  # (withoutswitchingtimes)

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: RsocConfig,
    ):
        """Constructs all the neccessary attributes."""
        self.rsocconfig = config

        self.name = config.name
        self.nom_load_soec = config.nom_load_soec
        self.min_load_soec = config.min_load_soec
        self.max_load_soec = config.max_load_soec
        self.faraday_eff_soec = config.faraday_eff_soec
        self.ramp_up_rate_soec = config.ramp_up_rate_soec
        self.ramp_down_rate_soec = config.ramp_down_rate_soec

        self.nom_power_sofc = config.nom_power_sofc
        self.min_power_sofc = config.min_power_sofc
        self.max_power_sofc = config.max_power_sofc
        self.faraday_eff_sofc = config.faraday_eff_sofc
        self.ramp_up_rate_sofc = config.ramp_up_rate_sofc
        self.ramp_down_rate_sofc = config.ramp_down_rate_sofc

        super().__init__(
            name=self.rsocconfig.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        # =================================================================================================================================
        # Input channels
        self.power_input: ComponentInput = self.add_input(
            self.rsocconfig.name,
            Rsoc.PowerInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KILOWATT,
            True,
        )

        # get the state from the controller
        self.input_state_rsoc: ComponentInput = self.add_input(
            self.rsocconfig.name,
            Rsoc.RSOCInputState,
            lt.LoadTypes.ACTIVATION,
            lt.Units.ANY,
            True,
        )

        # =================================================================================================================================
        # Output channels

        self.soec_current_efficiency_state: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECCurrentEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current efficiency based on the efficiency curve",
        )

        # current hydrogen output
        self.soec_hydrogen_flow_rate: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECCurrentHydrogenFlowRate,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current hydrogen flow rate",
        )

        # current hydrogen consumption
        self.sofc_hydrogen_flow_rate: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCCurrentHydrogenFlowRate,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Current hydrogen flow rate",
        )

        self.sofc_current_efficiency_state: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCCurrentEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current efficiency based on the efficiency curve",
        )

        # Total hydrogen production
        self.total_h2_produced: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECTotalHydrogenProduced,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG,
            output_description="Total hydrogen produced",
        )
        # Total oxygen production
        self.total_o2_produced: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECTotalOxygenProduced,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG,
            output_description="Total oxygen produced",
        )
        # Total water consumed
        self.total_h20_consumed: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECTotalWaterDemand,
            lt.LoadTypes.WATER,
            lt.Units.KG,
            output_description="Total water consumed",
        )
        # Total hydrogen consumed
        self.total_h2_consumed: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCTotalHydrogenConsumed,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG,
            output_description="Total hydrogen consumed",
        )
        # Total oxygen consumed
        self.total_o2_consumed: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCTotalOxygenConsumed,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG,
            output_description="Total oxygen consumed",
        )
        # Total oxygen consumed
        self.total_h20_produced: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCTotalWaterProduced,
            lt.LoadTypes.WATER,
            lt.Units.KG,
            output_description="Total water produced",
        )
        # Total operating time
        self.total_operating_time_rsco: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.RSOCOperatingTime,
            lt.LoadTypes.TIME,
            lt.Units.HOURS,
            output_description="Total operating time",
        )
        self.current_load_soec: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOECCurrentLoad,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Current load consumed by SOEC",
        )
        self.total_energy_consumed: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.TotalEnergyConsumed,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Total load used for hydrogen production",
        )
        self.current_output_sofc: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.SOFCCurrentOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
                lt.ComponentType.FUEL_CELL,
            ],
            output_description="Current power output SOFC",
        )

        self.total_energy_produced: ComponentOutput = self.add_output(
            self.rsocconfig.name,
            Rsoc.TotalEnergyProduced,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.KWH,
            output_description="Total energy produced for demand",
        )
        # =================================================================================================================================
        # Transfer and storage of states
        self.current_state_soec = 0.0
        self.total_ramp_up_count_state_soec = 0.0
        self.total_ramp_down_count_state_soec = 0.0
        self.total_warm_start_count_soec = 0.0
        self.total_cold_start_count_soec = 0.0
        self.total_warm_start_cycles_soec = 0
        self.total_cold_start_cycles_soec = 0
        self.current_warm_start_count_soec = 0.0
        self.current_cold_start_count_soec = 0.0
        self.total_hydrogen_produced_soec = 0.0
        self.total_oxygen_produced_soec = 0.0
        self.total_water_demand_soec = 0.0
        self.total_energy_soec = 0.0

        self.current_state_sofc = 0.0
        self.total_ramp_up_count_state_sofc = 0.0
        self.total_ramp_down_count_state_sofc = 0.0
        self.total_warm_start_count_sofc = 0.0
        self.total_cold_start_count_sofc = 0.0
        self.total_warm_start_cycles_sofc = 0
        self.total_cold_start_cycles_sofc = 0
        self.current_warm_start_count_sofc = 0.0
        self.current_cold_start_count_sofc = 0.0
        self.total_hydrogen_consumed_sofc = 0.0
        self.total_oxygen_consumed_sofc = 0.0
        self.total_water_produced_sofc = 0.0
        self.total_energy_sofc = 0.0

        self.total_operating_time = 0.0

        self.current_state_soec_previous = self.current_state_soec
        self.total_ramp_up_count_state_soec_previous = (
            self.total_ramp_up_count_state_soec
        )
        self.total_ramp_down_count_state_soec_previous = (
            self.total_ramp_down_count_state_soec
        )
        self.total_warm_start_count_soec_previous = self.total_warm_start_count_soec
        self.total_cold_start_count_soec_previous = self.total_cold_start_count_soec
        self.total_warm_start_cycles_soec_previous = self.total_warm_start_cycles_soec
        self.total_cold_start_cycles_soec_previous = self.total_cold_start_cycles_soec
        self.current_warm_start_count_soec_previous = self.current_warm_start_count_soec
        self.current_cold_start_count_soec_previous = self.current_cold_start_count_soec
        self.total_hydrogen_produced_soec_previous = self.total_hydrogen_produced_soec
        self.total_oxygen_produced_soec_previous = self.total_oxygen_produced_soec
        self.total_water_demand_soec_previous = self.total_water_demand_soec
        self.total_energy_soec_previous = self.total_energy_soec

        self.current_state_sofc_previous = self.current_state_sofc
        self.total_ramp_up_count_state_sofc_previous = (
            self.total_ramp_up_count_state_sofc
        )
        self.total_ramp_down_count_state_sofc_previous = (
            self.total_ramp_down_count_state_sofc
        )
        self.total_warm_start_count_sofc_previous = self.total_warm_start_count_sofc
        self.total_cold_start_count_sofc_previous = self.total_cold_start_count_sofc
        self.total_warm_start_cycles_sofc_previous = self.total_warm_start_cycles_sofc
        self.total_cold_start_cycles_sofc_previous = self.total_cold_start_cycles_sofc
        self.current_warm_start_count_sofc_previous = self.current_warm_start_count_sofc
        self.current_cold_start_count_sofc_previous = self.current_cold_start_count_sofc
        self.total_hydrogen_consumed_sofc_previous = self.total_hydrogen_consumed_sofc
        self.total_oxygen_consumed_sofc_previous = self.total_oxygen_consumed_sofc
        self.total_water_produced_sofc_previous = self.total_water_produced_sofc
        self.total_energy_sofc_previous = self.total_energy_sofc

        self.total_operating_time_previous = self.total_operating_time

    def soec_efficiency(self, name, current_load, min_load, max_load):
        """Efficiency curve data is provided corresponding to the used rSOC system."""
        # Load data from the JSON file
        data_file = os.path.join(
            utils.HISIMPATH["inputs"], "rSOC_efficiency_curve_data.json"
        )
        with open(data_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if the provided technology is valid
        if name not in data:
            raise ValueError(
                f"Invalid rSOC name. Supported names are: {', '.join(data.keys())}"
            )

        # Extract the x and y data points for the selected technology
        load_percentage = data[name]["load_percentage_soec"]
        sys_eff = data[name]["sys_eff_soec"]

        if min_load <= current_load <= max_load:
            current_load_percentage = current_load / max_load
            # Interpolation
            current_sys_eff_soec = float(
                np.interp(current_load_percentage, load_percentage, sys_eff)
            )

        else:
            current_sys_eff_soec = 0.0

        return current_sys_eff_soec

    def sofc_efficiency(self, name, current_demand, min_power, max_power):
        """Efficiency curve data is provided corresponding to the used rSOC system."""
        # Load data from the JSON file
        data_file = os.path.join(
            utils.HISIMPATH["inputs"], "rSOC_efficiency_curve_data.json"
        )
        with open(data_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if the provided technology is valid
        if name not in data:
            raise ValueError(
                f"Invalid rSOC name. Supported names are: {', '.join(data.keys())}"
            )

        # Extract the x and y data points for the selected technology
        load_percentage = data[name]["load_percentage_sofc"]
        sys_eff = data[name]["sys_eff_sofc"]

        if min_power < current_demand <= max_power:
            current_demand_percentage = current_demand / max_power
            # Interpolation
            current_sys_eff_sofc = float(
                np.interp(current_demand_percentage, load_percentage, sys_eff)
            )

        else:
            current_sys_eff_sofc = 0.0

        return current_sys_eff_sofc

    def h2_production_rate(self, current_sys_eff_soec, current_load):
        """Based on the calculated efficiency the current h2 production rate is calculated."""
        lhv_h2 = 33.33  # [kWh/kg]

        h2_production_rate = (current_sys_eff_soec * current_load) / (
            lhv_h2 * 3600
        )  # [kg/s]
        return h2_production_rate

    def h2_consumption_rate(self, current_sys_eff_sofc, current_demand):
        """Based on the calculated efficiency the current h2 consumption rate is calculated."""
        lhv_h2 = 33.33  # [kWh/kg]
        if current_sys_eff_sofc > 0.0 and current_demand >= self.min_power_sofc:
            h2_consumption_rate = current_demand / (
                current_sys_eff_sofc * lhv_h2 * 3600
            )  # [kg/s]
        else:
            h2_consumption_rate = 0.0
        return h2_consumption_rate

    def oxygen_rate(self, current_h2_rate):
        """Returns the mass flow rate of oxygen, based on the current hydrogen flow rate."""
        m_o2 = 31.9988  # g/mol
        m_h2 = 2.01588  # g/mol
        o2_flow_rate = (
            (m_o2 / m_h2) * 0.5 * current_h2_rate
        )  # Wang (2021) - Thermodynamic analysis of solid oxide electrolyzer integration with engine waste heat recovery for hydrogen production
        return o2_flow_rate

    def water_rate(self, current_h2_rate):
        """Returns the water mass flow rate, based on the current hydrogen flow rate."""
        m_h2o = 18.01528  # g/mol
        m_h2 = 2.01588  # g/mol
        h2o_flow_rate = (
            m_h2o / m_h2
        ) * current_h2_rate  # Wang (2021) - Thermodynamic analysis of solid oxide electrolyzer integration with engine waste heat recovery for hydrogen production
        return h2o_flow_rate

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.current_state_soec_previous = self.current_state_soec
        self.total_ramp_up_count_state_soec_previous = (
            self.total_ramp_up_count_state_soec
        )
        self.total_ramp_down_count_state_soec_previous = (
            self.total_ramp_down_count_state_soec
        )
        self.total_warm_start_count_soec_previous = self.total_warm_start_count_soec
        self.total_cold_start_count_soec_previous = self.total_cold_start_count_soec
        self.total_warm_start_cycles_soec_previous = self.total_warm_start_cycles_soec
        self.total_cold_start_cycles_soec_previous = self.total_cold_start_cycles_soec
        self.current_warm_start_count_soec_previous = self.current_warm_start_count_soec
        self.current_cold_start_count_soec_previous = self.current_cold_start_count_soec
        self.total_hydrogen_produced_soec_previous = self.total_hydrogen_produced_soec
        self.total_oxygen_produced_soec_previous = self.total_oxygen_produced_soec
        self.total_water_demand_soec_previous = self.total_water_demand_soec
        self.total_energy_soec_previous = self.total_energy_soec

        self.current_state_sofc_previous = self.current_state_sofc
        self.total_ramp_up_count_state_sofc_previous = (
            self.total_ramp_up_count_state_sofc
        )
        self.total_ramp_down_count_state_sofc_previous = (
            self.total_ramp_down_count_state_sofc
        )
        self.total_warm_start_count_sofc_previous = self.total_warm_start_count_sofc
        self.total_cold_start_count_sofc_previous = self.total_cold_start_count_sofc
        self.total_warm_start_cycles_sofc_previous = self.total_warm_start_cycles_sofc
        self.total_cold_start_cycles_sofc_previous = self.total_cold_start_cycles_sofc
        self.current_warm_start_count_sofc_previous = self.current_warm_start_count_sofc
        self.current_cold_start_count_sofc_previous = self.current_cold_start_count_sofc
        self.total_hydrogen_consumed_sofc_previous = self.total_hydrogen_consumed_sofc
        self.total_oxygen_consumed_sofc_previous = self.total_oxygen_consumed_sofc
        self.total_water_produced_sofc_previous = self.total_water_produced_sofc
        self.total_energy_sofc_previous = self.total_energy_sofc

        self.total_operating_time_previous = self.total_operating_time

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.current_state_soec = self.current_state_soec_previous
        self.total_ramp_up_count_state_soec = (
            self.total_ramp_up_count_state_soec_previous
        )
        self.total_ramp_down_count_state_soec = (
            self.total_ramp_down_count_state_soec_previous
        )
        self.total_warm_start_count_soec = self.total_warm_start_count_soec_previous
        self.total_cold_start_count_soec = self.total_cold_start_count_soec_previous
        self.total_warm_start_cycles_soec = self.total_warm_start_cycles_soec_previous
        self.total_cold_start_cycles_soec = self.total_cold_start_cycles_soec_previous
        self.current_warm_start_count_soec = self.current_warm_start_count_soec_previous
        self.current_cold_start_count_soec = self.current_cold_start_count_soec_previous
        self.total_hydrogen_produced_soec = self.total_hydrogen_produced_soec_previous
        self.total_oxygen_produced_soec = self.total_oxygen_produced_soec_previous
        self.total_water_demand_soec = self.total_water_demand_soec_previous
        self.total_energy_soec = self.total_energy_soec_previous

        self.current_state_sofc = self.current_state_sofc_previous
        self.total_ramp_up_count_state_sofc = (
            self.total_ramp_up_count_state_sofc_previous
        )
        self.total_ramp_down_count_state_sofc = (
            self.total_ramp_down_count_state_sofc_previous
        )
        self.total_warm_start_count_sofc = self.total_warm_start_count_sofc_previous
        self.total_cold_start_count_sofc = self.total_cold_start_count_sofc_previous
        self.total_warm_start_cycles_sofc = self.total_warm_start_cycles_sofc_previous
        self.total_cold_start_cycles_sofc = self.total_cold_start_cycles_sofc_previous
        self.current_warm_start_count_sofc = self.current_warm_start_count_sofc_previous
        self.current_cold_start_count_sofc = self.current_cold_start_count_sofc_previous
        self.total_hydrogen_consumed_sofc = self.total_hydrogen_consumed_sofc_previous
        self.total_oxygen_consumed_sofc = self.total_oxygen_consumed_sofc_previous
        self.total_water_produced_sofc = self.total_water_produced_sofc_previous
        self.total_energy_sofc = self.total_energy_sofc_previous

        self.total_operating_time = self.total_operating_time_previous

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""

        seconds_per_timestep = (
            self.my_simulation_parameters.seconds_per_timestep
        )  # [s/timestep]

        if stsv.get_input_value(self.power_input) < 0.0:
            power_to_soec = abs(stsv.get_input_value(self.power_input))
            demand_to_sofc = 0.0
        elif stsv.get_input_value(self.power_input) > 0.0:
            demand_to_sofc = abs(stsv.get_input_value(self.power_input))
            power_to_soec = 0.0
        else:
            power_to_soec = 0.0
            demand_to_sofc = 0.0
        rsoc_state = stsv.get_input_value(self.input_state_rsoc)

        if rsoc_state == 1:
            self.total_operating_time += (
                self.my_simulation_parameters.seconds_per_timestep / 3600
            )
            stsv.set_output_value(
                self.total_operating_time_rsco, self.total_operating_time
            )
        else:
            self.total_operating_time += 0.0
            stsv.set_output_value(
                self.total_operating_time_rsco, self.total_operating_time
            )

        if power_to_soec != 0.0:
            # SOEC operation
            # Variable
            self.current_state_sofc = 0.0
            name = self.name
            nominal_load = self.nom_load_soec
            min_load = self.min_load_soec
            ramp_up_rate = self.ramp_up_rate_soec
            ramp_down_rate = self.ramp_down_rate_soec

            # ramp up per timestep calculation
            ramp_up_per_timestep = nominal_load * ramp_up_rate * seconds_per_timestep
            total_ramp_up_per_timestep = ramp_up_per_timestep

            # ramp down per timestep calculation
            ramp_down_per_timestep = (
                nominal_load * ramp_down_rate * seconds_per_timestep
            )
            total_ramp_down_per_timestep = ramp_down_per_timestep

            # calculating load input
            new_load = abs(power_to_soec - self.current_state_soec)

            if rsoc_state == 1:
                """the ramping process"""
                if new_load == nominal_load:
                    self.current_state_soec = nominal_load
                    self.total_ramp_up_count_state_soec += 0
                    self.total_ramp_down_count_state_soec += 0
                elif new_load == 0:
                    self.current_state_soec = self.current_state_soec
                    self.total_ramp_up_count_state_soec += 0
                    self.total_ramp_down_count_state_soec += 0

                # Ramping up
                if (
                    new_load >= total_ramp_up_per_timestep
                    and self.current_state_soec < power_to_soec
                ):
                    self.total_ramp_up_count_state_soec += seconds_per_timestep
                    self.current_state_soec += total_ramp_up_per_timestep

                elif (
                    self.current_state_soec < power_to_soec
                    and new_load < total_ramp_up_per_timestep
                ):
                    percentage_ramp_up_per_timestep = (
                        new_load / total_ramp_up_per_timestep
                    )
                    self.total_ramp_up_count_state_soec += (
                        percentage_ramp_up_per_timestep * seconds_per_timestep
                    )

                    if self.current_state_soec == 0:
                        self.current_state_soec += power_to_soec

                    else:
                        self.current_state_soec += new_load

                # Ramping down
                elif (
                    total_ramp_down_per_timestep <= new_load
                    and power_to_soec < self.current_state_soec
                ):
                    self.total_ramp_down_count_state_soec += seconds_per_timestep
                    self.current_state_soec -= new_load

                elif (
                    power_to_soec < self.current_state_soec
                    and new_load < total_ramp_down_per_timestep
                ):
                    percentage_ramp_down_per_timestep = (
                        new_load / total_ramp_down_per_timestep
                    )
                    self.total_ramp_down_count_state_soec += (
                        percentage_ramp_down_per_timestep * seconds_per_timestep
                    )
                    self.current_state_soec -= new_load

                current_sys_eff_soec = self.soec_efficiency(
                    name, self.current_state_soec, min_load, self.max_load_soec
                )
                h2_production_rate = self.h2_production_rate(
                    current_sys_eff_soec, self.current_state_soec
                )

            elif rsoc_state == 0:
                self.total_ramp_up_count_state_soec += 0
                self.total_ramp_down_count_state_soec += 0
                self.current_state_soec = 2.315

                current_sys_eff_soec = 0.0
                h2_production_rate = 0.0

            elif rsoc_state == -1:
                self.total_ramp_up_count_state_soec += 0
                self.total_ramp_down_count_state_soec += 0
                self.current_state_soec = 0.0

                current_sys_eff_soec = 0.0
                h2_production_rate = 0.0

            # current_sys_eff_soec = self.soec_efficiency(name, self.current_state_soec, min_load, self.max_load_soec)
            # h2_production_rate = self.h2_production_rate(current_sys_eff_soec, self.current_state_soec)
            o2_flow_rate_soec = self.oxygen_rate(h2_production_rate)
            h2o_flow_rate_soec = self.water_rate(h2_production_rate)

            self.total_hydrogen_produced_soec += (
                h2_production_rate * seconds_per_timestep
            )
            self.total_oxygen_produced_soec += o2_flow_rate_soec * seconds_per_timestep
            self.total_water_demand_soec += h2o_flow_rate_soec * seconds_per_timestep

            stsv.set_output_value(self.soec_hydrogen_flow_rate, h2_production_rate)
            stsv.set_output_value(
                self.soec_current_efficiency_state, current_sys_eff_soec
            )

            # self.total_hydrogen_consumed_sofc += 0.0
            self.total_oxygen_consumed_sofc += 0.0
            self.total_water_produced_sofc += 0.0

            h2_consumption_rate = 0.0
            o2_flow_rate_sofc = 0.0
            h2o_flow_rate_sofc = 0.0

        elif demand_to_sofc != 0:
            # SOFC implementation start
            # Variable
            self.current_state_soec = 0.0

            name = self.name

            seconds_per_timestep = (
                self.my_simulation_parameters.seconds_per_timestep
            )  # [s/timestep]

            # ramp up per timestep calculation
            ramp_up_per_timestep = (
                self.nom_power_sofc * self.ramp_up_rate_sofc * seconds_per_timestep
            )
            total_ramp_up_per_timestep = ramp_up_per_timestep

            # ramp down per timestep calculation
            ramp_down_per_timestep = (
                self.nom_power_sofc * self.ramp_down_rate_sofc * seconds_per_timestep
            )
            total_ramp_down_per_timestep = ramp_down_per_timestep

            # calculating load input
            new_load = abs(demand_to_sofc - self.current_state_sofc)

            if rsoc_state == 1:
                """the ramping process"""
                if new_load == self.nom_power_sofc:
                    self.current_state_sofc = self.nom_power_sofc
                    self.total_ramp_up_count_state_sofc += 0
                    self.total_ramp_down_count_state_sofc += 0

                elif new_load == 0:
                    self.current_state_sofc = self.current_state_sofc
                    self.total_ramp_up_count_state_sofc += 0
                    self.total_ramp_down_count_state_sofc += 0

                # Ramping up
                if (
                    new_load >= total_ramp_up_per_timestep
                    and self.current_state_sofc < demand_to_sofc
                ):
                    self.total_ramp_up_count_state_sofc += seconds_per_timestep
                    self.current_state_sofc += total_ramp_up_per_timestep

                elif (
                    self.current_state_sofc < demand_to_sofc
                    and new_load < total_ramp_up_per_timestep
                ):
                    percentage_ramp_up_per_timestep = (
                        new_load / total_ramp_up_per_timestep
                    )
                    self.total_ramp_up_count_state_sofc += (
                        percentage_ramp_up_per_timestep * seconds_per_timestep
                    )

                    if self.current_state_sofc == 0:
                        self.current_state_sofc += demand_to_sofc

                    else:
                        self.current_state_sofc += new_load

                # Ramping down
                elif (
                    total_ramp_down_per_timestep <= new_load
                    and demand_to_sofc < self.current_state_sofc
                ):
                    self.total_ramp_down_count_state_sofc += seconds_per_timestep
                    self.current_state_sofc -= new_load

                elif (
                    demand_to_sofc < self.current_state_sofc
                    and new_load < total_ramp_down_per_timestep
                ):
                    percentage_ramp_down_per_timestep = (
                        new_load / total_ramp_down_per_timestep
                    )
                    self.total_ramp_down_count_state_sofc += (
                        percentage_ramp_down_per_timestep * seconds_per_timestep
                    )
                    self.current_state_sofc -= new_load

            elif rsoc_state == 0:
                self.total_ramp_up_count_state_sofc += 0
                self.total_ramp_down_count_state_sofc += 0
                self.current_state_sofc = 0.0
                h2_consumption_rate = 0.0

            elif rsoc_state == -1:
                self.total_ramp_up_count_state_sofc += 0
                self.total_ramp_down_count_state_sofc += 0
                self.current_state_sofc = 0.0
                h2_consumption_rate = 0.0

            current_sys_eff_sofc = self.sofc_efficiency(
                name, self.current_state_sofc, self.min_power_sofc, self.max_power_sofc
            )
            h2_consumption_rate = self.h2_consumption_rate(
                current_sys_eff_sofc, self.current_state_sofc
            )
            o2_flow_rate_sofc = self.oxygen_rate(h2_consumption_rate)
            h2o_flow_rate_sofc = self.water_rate(h2_consumption_rate)

            # self.total_hydrogen_consumed_sofc += h2_consumption_rate * seconds_per_timestep
            self.total_oxygen_consumed_sofc += o2_flow_rate_sofc * seconds_per_timestep
            self.total_water_produced_sofc += h2o_flow_rate_sofc * seconds_per_timestep

            stsv.set_output_value(self.sofc_hydrogen_flow_rate, h2_consumption_rate)
            stsv.set_output_value(
                self.sofc_current_efficiency_state, current_sys_eff_sofc
            )

            # end

            self.total_hydrogen_produced_soec += 0.0
            h2_production_rate = 0.0
            o2_flow_rate_soec = 0.0
            h2o_flow_rate_soec = 0.0
            self.total_oxygen_produced_soec += 0.0
            self.total_water_demand_soec += 0.0

        else:
            stsv.set_output_value(self.sofc_hydrogen_flow_rate, 0.0)
            stsv.set_output_value(self.sofc_current_efficiency_state, 0.0)
            stsv.set_output_value(self.soec_hydrogen_flow_rate, 0.0)
            stsv.set_output_value(self.soec_current_efficiency_state, 0.0)
            self.current_state_sofc = 0.0
            self.current_state_soec = 2.315
            # stsv.set_output_value(self.current_output_sofc, -2315) #for WATT output in example
            # stsv.set_output_value(self.current_load_soec, 2315)

            self.total_hydrogen_produced_soec += 0.0
            # self.total_hydrogen_consumed_sofc += 0.0
            h2_consumption_rate = 0.0
            h2_production_rate = 0.0
            o2_flow_rate_soec = 0.0
            h2o_flow_rate_soec = 0.0
            o2_flow_rate_sofc = 0.0
            h2o_flow_rate_sofc = 0.0

        stsv.set_output_value(self.total_h2_produced, self.total_hydrogen_produced_soec)
        stsv.set_output_value(self.total_o2_produced, self.total_oxygen_produced_soec)
        stsv.set_output_value(self.total_h20_consumed, self.total_water_demand_soec)

        self.total_hydrogen_consumed_sofc += h2_consumption_rate * seconds_per_timestep
        stsv.set_output_value(self.total_h2_consumed, self.total_hydrogen_consumed_sofc)
        stsv.set_output_value(self.total_o2_consumed, self.total_oxygen_consumed_sofc)
        stsv.set_output_value(self.total_h20_produced, self.total_water_produced_sofc)

        stsv.set_output_value(
            self.current_output_sofc, (self.current_state_sofc * 1000)
        )  # for WATT output in example
        stsv.set_output_value(
            self.current_load_soec, (self.current_state_soec * 1000)
        )  # for WATT output in example

        self.total_energy_soec += self.current_state_soec * (
            seconds_per_timestep / 3600
        )
        stsv.set_output_value(self.total_energy_consumed, self.total_energy_soec)
        self.total_energy_sofc += self.current_state_sofc * (
            seconds_per_timestep / 3600
        )
        stsv.set_output_value(self.total_energy_produced, self.total_energy_sofc)

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        lines = []
        for config_string in self.rsocconfig.get_string_dict():
            lines.append(config_string)
        lines.append("Component Name" + str(self.component_name))
        lines.append(
            "Total operating time during simulation: "
            + str(self.total_operating_time)
            + " [h]"
        )
        lines.append(
            "Total hydrogen produced during simulation: "
            + str(self.total_hydrogen_produced_soec)
            + " [kg]"
        )
        lines.append(
            "Total hydrogen consumed during simulation: "
            + str(self.total_hydrogen_consumed_sofc)
            + " [kg]"
        )
        lines.append(
            "Total oxygen produced during simulation: "
            + str(self.total_oxygen_produced_soec)
            + " [kg]"
        )
        lines.append(
            "Total oxygen consumed during simulation: "
            + str(self.total_oxygen_consumed_sofc)
            + " [kg]"
        )
        lines.append(
            "Total water demand during simulation: "
            + str(self.total_water_demand_soec)
            + " [kg]"
        )
        lines.append(
            "Total water demand during simulation: "
            + str(self.total_water_produced_sofc)
            + " [kg]"
        )
        lines.append(
            "Total energy consumed during simulation: "
            + str(self.total_energy_soec)
            + " [kg]"
        )
        lines.append(
            "Total energy produced during simulation: "
            + str(self.total_energy_sofc)
            + " [kWh]"
        )
        lines.append(
            "Total ramp-up time during simulation: "
            + str(self.total_ramp_up_count_state_soec)
            + " [sec]"
        )
        lines.append(
            "Total ramp-up time during simulation: "
            + str(self.total_ramp_up_count_state_sofc)
            + " [sec]"
        )
        lines.append(
            "Total ramp-down time during simulation: "
            + str(self.total_ramp_down_count_state_soec)
            + " [sec]"
        )
        lines.append(
            "Total ramp-down time during simulation: "
            + str(self.total_ramp_down_count_state_sofc)
            + " [sec]"
        )
        return lines
