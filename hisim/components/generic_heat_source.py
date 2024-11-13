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
        cls, building_name: str = "BUI1", thermal_power_in_watt: float = 6200.0, name: str = "HeatingHeatSource"
    ) -> "HeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = HeatSourceConfig(
            building_name=building_name,
            name=name,
            source_weight=1,
            fuel=lt.LoadTypes.DISTRICTHEATING,
            power_th=thermal_power_in_watt,
            water_vs_heating=lt.InandOutputType.HEATING,
            efficiency=1.0,
            co2_footprint=0,
            cost=0,
            lifetime=1,
        )
        return config

    @classmethod
    def get_default_config_waterheating(
        cls,
        heating_system: lt.HeatingSystems,
        max_warm_water_demand_in_liter: float,
        scaling_factor_according_to_number_of_apartments: float,
        seconds_per_timestep: int,
        building_name: str = "BUI1",
        name: str = "DHWHeatSource"
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
            heating_system_installed=heating_system, water_vs_heating=lt.InandOutputType.WATER_HEATING,
        )

        if heating_system == lt.HeatingSystems.GAS_HEATING:
            fuel = lt.LoadTypes.GAS
        elif heating_system == lt.HeatingSystems.OIL_HEATING:
            fuel = lt.LoadTypes.OIL
        elif heating_system == lt.HeatingSystems.DISTRICT_HEATING:
            fuel = lt.LoadTypes.DISTRICTHEATING
        else:
            raise ValueError(
                f"Heating sytem {heating_system} not acceptable for generic heat source component. Muste be ags, oil or district heating."
            )

        config = HeatSourceConfig(
            building_name=building_name,
            name=name,
            source_weight=1,
            fuel=fuel,
            power_th=thermal_power_in_watt,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            efficiency=efficiency,
            # costs, footprint and lifetime will be determined later in opex and capex ost calculation
            co2_footprint=0,
            cost=0,
            lifetime=1,
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
            self.component_name, self.L1HeatSourceTargetPercentage, lt.LoadTypes.ANY, lt.Units.PERCENT, mandatory=True,
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
            postprocessing_flag=[lt.InandOutputType.FUEL_CONSUMPTION, config.fuel, config.water_vs_heating],
            output_description="Fuel Delivered",
        )

        if config.fuel == lt.LoadTypes.OIL:
            self.fuel_delivered_channel.unit = lt.Units.LITER
        else:
            self.fuel_delivered_channel.unit = lt.Units.WATT_HOUR

        self.add_default_connections(self.get_default_connections_controller_l1_heatpump())

    def get_default_connections_controller_l1_heatpump(self,) -> List[cp.ComponentConnection]:
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
            self.thermal_power_delivered_channel, self.config.power_th * power_modifier * self.config.efficiency,
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
        )
        if config.fuel == lt.LoadTypes.GAS and config.water_vs_heating == lt.InandOutputType.WATER_HEATING:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.GAS_HEATER_DOMESTIC_HOT_WATER
        elif config.fuel == lt.LoadTypes.GAS and config.water_vs_heating == lt.InandOutputType.HEATING:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.GAS_HEATER_SPACE_HEATING
        elif config.fuel == lt.LoadTypes.OIL and config.water_vs_heating == lt.InandOutputType.WATER_HEATING:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.OIL_HEATER_DOMESTIC_HOT_WATER
        elif config.fuel == lt.LoadTypes.OIL and config.water_vs_heating == lt.InandOutputType.HEATING:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.OIL_HEATER_SPACE_HEATING
        elif (
            config.fuel == lt.LoadTypes.DISTRICTHEATING and config.water_vs_heating == lt.InandOutputType.WATER_HEATING
        ):
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.DISTRICT_HEATING_DOMESTIC_HOT_WATER
        elif config.fuel == lt.LoadTypes.DISTRICTHEATING and config.water_vs_heating == lt.InandOutputType.HEATING:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING
        else:
            capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()

        return capex_cost_data_class

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        fuel_consumption_in_watt_hour_or_liter: Optional[float] = None
        fuel_consumption_in_kilowatt_hour: Optional[float] = None
        output: Optional[cp.ComponentOutput] = None
        kpi_tag = None
        for index, output_ in enumerate(all_outputs):
            if output_.component_name == self.component_name and output_.field_name == self.FuelDelivered:
                fuel_consumption_in_watt_hour_or_liter = round(sum(postprocessing_results.iloc[:, index]), 1)
                output = output_
                break

        if fuel_consumption_in_watt_hour_or_liter is not None and output is not None:
            emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                self.my_simulation_parameters.year
            )
            if output.load_type == lt.LoadTypes.GAS:
                co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
                euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh
                fuel_consumption_in_kilowatt_hour = fuel_consumption_in_watt_hour_or_liter * 1e-3
                if self.config.water_vs_heating == lt.InandOutputType.WATER_HEATING:
                    kpi_tag = KpiTagEnumClass.GAS_HEATER_DOMESTIC_HOT_WATER
                elif self.config.water_vs_heating == lt.InandOutputType.HEATING:
                    kpi_tag = KpiTagEnumClass.GAS_HEATER_SPACE_HEATING

            elif output.load_type == lt.LoadTypes.OIL:
                co2_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
                euro_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
                # calculate fuel consumption from liter to kWh for oil
                # https://www.energieheld.de/heizung/ratgeber/durchschnittliche-heizkosten#:~:text=Zur%20Veranschaulichung%3A%20Heiz%C3%B6l%20wird%20meistens,etwa%209%2C8%20Kilowattstunden%20Heizenergie.
                # 1l oil = 10 kWh
                fuel_consumption_in_kilowatt_hour = fuel_consumption_in_watt_hour_or_liter * 10
                if self.config.water_vs_heating == lt.InandOutputType.WATER_HEATING:
                    kpi_tag = KpiTagEnumClass.OIL_HEATER_DOMESTIC_HOT_WATER
                elif self.config.water_vs_heating == lt.InandOutputType.HEATING:
                    kpi_tag = KpiTagEnumClass.OIL_HEATER_SPACE_HEATING

            elif output.load_type == lt.LoadTypes.DISTRICTHEATING:
                # TODO: implement district heating costs
                co2_per_unit = 0
                euro_per_unit = 0
                fuel_consumption_in_kilowatt_hour = fuel_consumption_in_watt_hour_or_liter * 1e-3
                if self.config.water_vs_heating == lt.InandOutputType.WATER_HEATING:
                    kpi_tag = KpiTagEnumClass.DISTRICT_HEATING_DOMESTIC_HOT_WATER
                elif self.config.water_vs_heating == lt.InandOutputType.HEATING:
                    kpi_tag = KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING
            else:
                raise ValueError("This loadtype is not implemented for the generic heat source.")

            # energy costs and co2 and everything will be considered in gas meter
            co2_per_simulated_period_in_kg = fuel_consumption_in_watt_hour_or_liter * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = fuel_consumption_in_watt_hour_or_liter * euro_per_unit
            opex_cost_data_class = OpexCostDataClass(
                opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
                opex_maintenance_cost_in_euro=0,  # TODO: needs o be implemented still
                co2_footprint_in_kg=co2_per_simulated_period_in_kg,
                consumption_in_kwh=fuel_consumption_in_kilowatt_hour,
                loadtype=output.load_type,
                kpi_tag=kpi_tag,
            )
        else:
            opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()

        return opex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        list_of_kpi_entries: List[KpiEntry] = []

        opex_dataclass = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        my_kpi_entry = KpiEntry(
            name=f"{opex_dataclass.loadtype.value} consumption for {self.config.water_vs_heating.value}",
            unit="kWh",
            value=opex_dataclass.consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )

        list_of_kpi_entries.append(my_kpi_entry)
        return list_of_kpi_entries
