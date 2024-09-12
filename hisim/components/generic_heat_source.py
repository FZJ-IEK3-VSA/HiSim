""" Generic Heat Source (Oil, Gas or DistrictHeating) together with Configuration and State. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
import importlib
from dataclasses import dataclass
from typing import List, Optional

from dataclasses_json import dataclass_json
import pandas as pd

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import controller_l1_heatpump
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.components.configuration import HouseholdWarmWaterDemandConfig, EmissionFactorsAndCostsForFuelsConfig
from hisim.component import OpexCostDataClass, CapexCostDataClass

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
    """Configuration of a generic HeatSource."""

    building_name: str
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
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatSource.get_full_classname()

    @classmethod
    def get_default_config_heating(
        cls,
        building_name: str = "BUI1",
    ) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = HeatSourceConfig(
            building_name=building_name,
            name="HeatingHeatSource",
            source_weight=1,
            fuel=lt.LoadTypes.DISTRICTHEATING,
            power_th=6200.0,
            water_vs_heating=lt.InandOutputType.HEATING,
            efficiency=1.0,
            co2_footprint=0,
            cost=0,
            lifetime=1
        )
        return config

    @classmethod
    def get_default_config_waterheating_with_district_heating(
        cls,
        building_name: str = "BUI1",
    ) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for water heating (DHW)."""
        config = HeatSourceConfig(
            building_name=building_name,
            name="DHWHeatSource",
            source_weight=1,
            fuel=lt.LoadTypes.DISTRICTHEATING,
            power_th=3000.0,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            efficiency=1.0,
            co2_footprint=0,
            cost=0,
            lifetime=1
        )
        return config

    @classmethod
    def get_default_config_waterheating_with_gas(
        cls,
        max_warm_water_demand_in_liter: float,
        scaling_factor_according_to_number_of_apartments: float,
        seconds_per_timestep: int,
        building_name: str = "BUI1",
    ) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for water heating (DHW)."""
        # use importlib for importing the other component in order to avoid circular-import errors

        component_module_name = "hisim.modular_household.component_connections"
        component_module = importlib.import_module(name=component_module_name)
        get_heating_system_efficiency = getattr(component_module, "get_heating_system_efficiency")
        thermal_power_in_watt = (
            max_warm_water_demand_in_liter
            * (4180 / 3600)
            * 0.5
            * (3600 / seconds_per_timestep)
            * (
                HouseholdWarmWaterDemandConfig.ww_temperature_demand
                - HouseholdWarmWaterDemandConfig.temperature_difference_hot
                - HouseholdWarmWaterDemandConfig.freshwater_temperature
            )
            * scaling_factor_according_to_number_of_apartments
        )
        efficiency = get_heating_system_efficiency(
            heating_system_installed=lt.HeatingSystems.GAS_HEATING,
            water_vs_heating=lt.InandOutputType.HEATING,
        )
        config = HeatSourceConfig(
            building_name=building_name,
            name="DHWHeatSource",
            source_weight=1,
            fuel=lt.LoadTypes.GAS,
            power_th=thermal_power_in_watt,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            efficiency=efficiency,
            co2_footprint=thermal_power_in_watt * 1e-3 * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=7416,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
        )
        return config


class HeatSourceState:
    """Heat source state class saves the state of the heat source."""

    def __init__(self, state: int = 0):
        """Initializes state."""
        self.state = state

    def clone(self) -> "HeatSourceState":
        """Creates copy of a state."""
        return HeatSourceState(state=self.state)


class HeatSource(cp.Component):
    """Heat Source implementation.

    District Heating, Oil Heating or Gas Heating. Heat is converted with given efficiency.

    Components to connect to:
    (1) Heat Pump Controller (controller_l1_heatpump)
    """

    # Inputs
    L1HeatSourceTargetPercentage = "L1HeatSourceTargetPercentage"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    FuelDelivered = "FuelDelivered"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatSourceConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
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

        self.add_default_connections(self.get_default_connections_controller_l1_heatpump())

    def get_default_connections_controller_l1_heatpump(
        self,
    ) -> List[cp.ComponentConnection]:
        """Sets default connections of heat source controller."""
        log.information("setting l1 default connections in Generic Heat Source")
        connections = []
        controller_classname = controller_l1_heatpump.L1HeatPumpController.get_classname()
        connections.append(
            cp.ComponentConnection(
                HeatSource.L1HeatSourceTargetPercentage,
                controller_classname,
                controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage,
            )
        )
        return connections

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""

        lines = []
        lines.append(f"Name: {self.config.name + str(self.config.source_weight)})")
        lines.append(f"Fuel: {self.config.fuel}")
        lines.append(f"Power: {(self.config.power_th) * 1e-3:4.0f} kW")
        lines.append(f"Efficiency : {(self.config.efficiency) * 100:4.0f} %")
        return lines

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the simulation of the heat source model."""

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
            power_modifier = min(power_modifier, 1)

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
                power_modifier * self.config.power_th * self.my_simulation_parameters.seconds_per_timestep / 3.6e3,
            )

    @staticmethod
    def get_cost_capex(config: HeatSourceConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        if config.fuel == lt.LoadTypes.GAS:
            seconds_per_year = 365 * 24 * 60 * 60
            capex_per_simulated_period = (config.cost / config.lifetime) * (
                simulation_parameters.duration.total_seconds() / seconds_per_year
            )
            device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
                simulation_parameters.duration.total_seconds() / seconds_per_year
            )

            capex_cost_data_class = CapexCostDataClass(
                capex_investment_cost_in_euro=config.cost,
                device_co2_footprint_in_kg=config.co2_footprint,
                lifetime_in_years=config.lifetime,
                capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
                device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
                kpi_tag=KpiTagEnumClass.GAS_HEATER_DOMESTIC_HOT_WATER
            )
            return capex_cost_data_class
        return NotImplemented

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        gas_consumption_in_kilowatt_hour: Optional[float] = None
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.load_type == lt.LoadTypes.GAS:
                gas_consumption_in_kilowatt_hour = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)
        if gas_consumption_in_kilowatt_hour is not None:
            emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                self.my_simulation_parameters.year
            )
            co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
            co2_per_simulated_period_in_kg = gas_consumption_in_kilowatt_hour * co2_per_unit

            # energy costs and co2 and everything will be considered in gas meter
            opex_cost_data_class = OpexCostDataClass(
                opex_energy_cost_in_euro=0,
                opex_maintenance_cost_in_euro=0,  # TODO: needs o be implemented still
                co2_footprint_in_kg=co2_per_simulated_period_in_kg,
                consumption_in_kwh=gas_consumption_in_kilowatt_hour,
                loadtype=lt.LoadTypes.GAS,
                kpi_tag=KpiTagEnumClass.GAS_HEATER_DOMESTIC_HOT_WATER
            )
        else:
            opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        gas_consumption_in_kilowatt_hour: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.FuelDelivered and output.load_type == lt.LoadTypes.GAS:
                    gas_consumption_in_kilowatt_hour = round(postprocessing_results.iloc[:, index].sum() * 1e-3, 1)
                    break
        my_kpi_entry = KpiEntry(
            name="Gas consumption for domestic hot water",
            unit="kWh",
            value=gas_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.GAS_HEATER_DOMESTIC_HOT_WATER,
            description=self.component_name,
        )

        list_of_kpi_entries.append(my_kpi_entry)
        return list_of_kpi_entries
