"""Generic Boiler Module.

This heating system can use different energy carriers (natural, gas, oil, hydrogen, ...) for combustion.
The Generic boiler chooses between two modes: a) a conventional boiler (Heizwertheizung) representing older boilers and
b) a condensing boiler (Brennwertheizung) which is more modern and uses heat not only from the combustion process but also from waste gases
which is why it has higher efficiencies than the conventional boiler.
https://www.vaillant.co.uk/advice/understanding-heating-technology/boilers/what-is-a-Generic-boiler/.

The Generic boiler controller can be set as modulating controller (which is often used)
and as non-modulating on_off controller (which is used especially for pellet heating).
"""

# clean
# Owned
import importlib
from dataclasses import dataclass
from typing import List, Any, Optional
from enum import Enum
import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.components.configuration import PhysicsConfig
from hisim.component import (
    Component,
    ComponentConnection,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass,
)
from hisim.components.simple_water_storage import SimpleHotWaterStorage
from hisim.components.weather import Weather
from hisim.components.heat_distribution_system import HeatDistributionController
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

__authors__ = "Frank Burkrad, Maximilian Hillen, Markus Blasberg, Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


class BoilerType(Enum):
    """Set Boiler Types."""

    CONVENTIONAL = 1  # use only heat of combustion -> lower heating value of fuel is used
    CONDENSING = 2  # use also heat from waste gases (from water vapour) -> higher heating value of fuel is used


@dataclass_json
@dataclass
class GenericBoilerConfig(ConfigBase):
    """Configuration of the GenericBoiler class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return GenericBoiler.get_full_classname()

    building_name: str
    name: str
    energy_carrier: lt.LoadTypes
    boiler_type: BoilerType
    minimal_thermal_power_in_watt: float
    maximal_thermal_power_in_watt: float
    eff_th_min: float
    eff_th_max: float
    temperature_delta_in_celsius: float
    maximal_temperature_in_celsius: float
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: energy consumption in kWh
    consumption_in_kilowatt_hour: float

    @classmethod
    def get_default_condensing_gas_boiler_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default condensing gas boiler."""
        maximal_thermal_power_in_watt = 12000
        config = GenericBoilerConfig(
            building_name=building_name,
            name="CondensingGasBoiler",
            boiler_type=BoilerType.CONDENSING,
            energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1000,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            maximal_temperature_in_celsius=80,
            co2_footprint=maximal_thermal_power_in_watt
            * 1e-3
            * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=7416,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_condensing_gas_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default condensing gas boiler scaled to heating load."""
        maximal_thermal_power_in_watt = heating_load_of_building_in_watt
        config = GenericBoilerConfig(
            building_name=building_name,
            name="CondensingGasBoiler",
            boiler_type=BoilerType.CONDENSING,
            energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1 / 12 * maximal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            maximal_temperature_in_celsius=80,
            co2_footprint=maximal_thermal_power_in_watt
            * 1e-3
            * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=7416,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_default_conventional_oil_boiler_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional oil boiler."""
        maximal_thermal_power_in_watt = 12000
        config = GenericBoilerConfig(
            building_name=building_name,
            name="ConventionalOilBoiler",
            boiler_type=BoilerType.CONVENTIONAL,
            energy_carrier=lt.LoadTypes.OIL,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1000,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            maximal_temperature_in_celsius=80,
            co2_footprint=maximal_thermal_power_in_watt * 1e-3 * 19.4,  # value from emission_factros_and_costs_devices.csv
            cost=5562,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_conventional_oil_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional oil boiler scaled to heating load."""
        maximal_thermal_power_in_watt = heating_load_of_building_in_watt
        config = GenericBoilerConfig(
            building_name=building_name,
            name="ConventionalOilBoiler",
            boiler_type=BoilerType.CONVENTIONAL,
            energy_carrier=lt.LoadTypes.OIL,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1 / 12 * maximal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            maximal_temperature_in_celsius=80,
            co2_footprint=maximal_thermal_power_in_watt * 1e-3 * 19.4,  # value from emission_factros_and_costs_devices.csv
            cost=5562,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_conventional_pellet_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional pellet boiler scaled to heating load.
        So far we only have the lower heating value of pellets (see PhysicsConfig),
        so only conventional pellet boilers are used.
        """
        maximal_thermal_power_in_watt = heating_load_of_building_in_watt
        config = GenericBoilerConfig(
            building_name=building_name,
            name="ConventionalPelletBoiler",
            boiler_type=BoilerType.CONVENTIONAL,
            energy_carrier=lt.LoadTypes.PELLETS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1 / 12 * maximal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            maximal_temperature_in_celsius=80,
            # gas value from emission_factros_and_costs_devices.csv,
            # factor pellet/gas from https://depv.de/p/Bessere-CO2-Bilanz-mit-Holzpellets-pWuQQ4VvuNQoYjUzRf778Z
            co2_footprint=0.63 * 49.47,
            # gas value from emission_factros_and_costs_devices.csv,
            # factor pellet/gas from https://www.dein-heizungsbauer.de/ratgeber/bauen-sanieren/pelletheizung-kosten/
            cost=3.33 * 7416,
            lifetime=20,  # use same value as for others
            # from https://www.dein-heizungsbauer.de/ratgeber/bauen-sanieren/pelletheizung-kosten/
            maintenance_cost_as_percentage_of_investment=0.01,
            consumption_in_kilowatt_hour=0,
        )
        return config


class GenericBoiler(Component):
    """GenericBoiler class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the GenericBoiler modulating [0..1]
    WaterInputTemperature = "WaterInputTemperature"

    # Output
    WaterOutputMassFlow = "WaterOutputMassFlow"
    WaterOutputTemperature = "WaterOutputTemperature"
    EnergyDemand = "EnergyDemand"
    FuelDemand = "FuelDemand"
    ThermalOutputPower = "ThermalOutputPower"
    ThermalOutputEnergy = "ThermalOutputEnergy"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericBoilerConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            True,
        )
        self.water_input_temperature_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )

        self.water_output_mass_flow_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputMassFlow,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterOutputMassFlow} will follow.",
        )
        self.water_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterOutputTemperature} will follow.",
        )
        self.energy_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.EnergyDemand,
            lt.LoadTypes.ANY,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.EnergyDemand} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.fuel_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.FuelDemand,
            lt.LoadTypes.ANY,
            lt.Units.LITER,
            output_description=f"here a description for {self.FuelDemand} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPower,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ThermalOutputPower} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_energy_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputEnergy,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalOutputEnergy} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        # Set important parameters
        self.build()
        self.fuel_consumption_in_liter: float = 0

        self.add_default_connections(self.get_default_connections_from_controller_generic_boiler())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

    def get_default_connections_from_controller_generic_boiler(
        self,
    ):
        """Get Controller Generic Boiler default connections."""
        component_class = GenericBoilerController
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToGenericBoiler,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get Simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleHotWaterStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.WaterInputTemperature,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Get values from config
        self.energy_carrier = self.config.energy_carrier
        self.minimal_thermal_power_in_watt = self.config.minimal_thermal_power_in_watt
        self.maximal_thermal_power_in_watt = self.config.maximal_thermal_power_in_watt
        self.min_combustion_efficiency = self.config.eff_th_min
        self.max_combustion_efficiency = self.config.eff_th_max
        self.maximal_temperature_in_celsius = self.config.maximal_temperature_in_celsius
        self.temperature_delta_in_celsius = self.config.temperature_delta_in_celsius
        # Get physical properties of water and fuel used for the combustion
        self.specific_heat_capacity_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=lt.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        )

        # Here use higher heating value for condesing boiler and lower heating value for conventional boiler
        if self.config.boiler_type == BoilerType.CONDENSING:
            self.heating_value_of_fuel_in_joule_per_m3 = PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=self.energy_carrier
            ).higher_heating_value_in_joule_per_m3
        elif self.config.boiler_type == BoilerType.CONVENTIONAL:
            self.heating_value_of_fuel_in_joule_per_m3 = PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=self.energy_carrier
            ).lower_heating_value_in_joule_per_m3
        else:
            raise ValueError(f"Boiler type {self.config.boiler_type} is not implemented.")

        # J = Wh/3600 and m3 = 1000 l
        self.heating_value_of_fuel_in_watthour_per_liter = self.heating_value_of_fuel_in_joule_per_m3 * 3.6e-6

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.config.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Generic Boiler."""
        control_signal = stsv.get_input_value(self.control_signal_channel)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")

        # Calculate combustion efficiency
        delta_efficiency = self.max_combustion_efficiency - self.min_combustion_efficiency

        if control_signal * self.maximal_thermal_power_in_watt < self.minimal_thermal_power_in_watt:
            maximum_power_used_in_watt = self.minimal_thermal_power_in_watt
            real_combustion_efficiency = self.min_combustion_efficiency
        else:
            maximum_power_used_in_watt = control_signal * self.maximal_thermal_power_in_watt
            real_combustion_efficiency = self.min_combustion_efficiency + delta_efficiency * control_signal

        # energy consumption
        fuel_energy_consumption_in_watt_hour = (
            maximum_power_used_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )
        fuel_consumption_in_liter = (
            fuel_energy_consumption_in_watt_hour / self.heating_value_of_fuel_in_watthour_per_liter
        )

        # thermal power delivered from combustion
        thermal_power_delivered_in_watt = maximum_power_used_in_watt * real_combustion_efficiency * control_signal
        thermal_energy_delivered_in_watt_hour = (
            thermal_power_delivered_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )

        # water output temperature and mass flow
        water_output_temperature_in_celsius = self.temperature_delta_in_celsius + stsv.get_input_value(
            self.water_input_temperature_channel
        )
        mass_flow_out_in_kg_per_second = thermal_power_delivered_in_watt / (
            self.specific_heat_capacity_water_in_joule_per_kilogram_per_celsius * self.temperature_delta_in_celsius
        )

        stsv.set_output_value(self.thermal_output_power_channel, thermal_power_delivered_in_watt)
        stsv.set_output_value(self.thermal_output_energy_channel, thermal_energy_delivered_in_watt_hour)
        stsv.set_output_value(self.energy_demand_channel, fuel_energy_consumption_in_watt_hour)
        stsv.set_output_value(self.fuel_demand_channel, fuel_consumption_in_liter)
        stsv.set_output_value(
            self.water_output_temperature_channel,
            water_output_temperature_in_celsius,
        )
        stsv.set_output_value(self.water_output_mass_flow_channel, mass_flow_out_in_kg_per_second)

    @staticmethod
    def get_cost_capex(config: GenericBoilerConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
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
        if config.energy_carrier == lt.LoadTypes.GAS:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.GAS_HEATER_SPACE_HEATING
        elif config.energy_carrier == lt.LoadTypes.OIL:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.OIL_HEATER_SPACE_HEATING
        elif config.energy_carrier == lt.LoadTypes.HYDROGEN:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.HYDROGEN_SPACE_HEATING
        elif config.energy_carrier == lt.LoadTypes.PELLETS:
            capex_cost_data_class.kpi_tag = KpiTagEnumClass.PELLETS_SPACE_HEATING
        else:
            capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.field_name == self.EnergyDemand:
                self.config.consumption_in_kilowatt_hour = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)
            elif output.component_name == self.component_name and output.field_name == self.FuelDemand:
                self.fuel_consumption_in_liter = round(sum(postprocessing_results.iloc[:, index]), 1)

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        if self.energy_carrier == lt.LoadTypes.GAS:
            kpi_tag = KpiTagEnumClass.GAS_HEATER_SPACE_HEATING
            co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.OIL:
            kpi_tag = KpiTagEnumClass.OIL_HEATER_SPACE_HEATING
            co2_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
            euro_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
            co2_per_simulated_period_in_kg = self.fuel_consumption_in_liter * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_liter * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.HYDROGEN:  # TODO: implement costs and co2
            kpi_tag = KpiTagEnumClass.HYDROGEN_SPACE_HEATING
            co2_per_unit = 0
            euro_per_unit = 0
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.PELLETS:  # TODO: implement costs and co2
            kpi_tag = KpiTagEnumClass.PELLETS_SPACE_HEATING
            co2_per_unit = 0
            euro_per_unit = 0
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        else:
            raise ValueError(f"Energy carrier {self.energy_carrier} not implemented for Generic boiler.")

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            consumption_in_kwh=self.config.consumption_in_kilowatt_hour,
            loadtype=self.energy_carrier,
            kpi_tag=kpi_tag,
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        my_kpi_entry = KpiEntry(
            name=f"{opex_dataclass.loadtype.value} consumption for space heating",
            unit="kWh",
            value=opex_dataclass.consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry)

        # fuel demand in liter
        my_kpi_entry_two = KpiEntry(
            name=f"{opex_dataclass.loadtype.value} fuel consumption for space heating",
            unit="l",
            value=self.fuel_consumption_in_liter,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry_two)
        return list_of_kpi_entries


@dataclass_json
@dataclass
class GenericBoilerControllerConfig(ConfigBase):
    """Boiler Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GenericBoilerController.get_full_classname()

    building_name: str
    name: str
    is_modulating: bool
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    minimal_thermal_power_in_watt: float
    maximal_thermal_power_in_watt: float
    set_temperature_difference_for_full_power: float

    @classmethod
    def get_default_modulating_generic_boiler_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default Generic Boiler Controller."""
        return GenericBoilerControllerConfig(
            building_name=building_name,
            name="ModulatingBoilerController",
            is_modulating=True,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from Generic Boiler config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )

    @classmethod
    def get_default_on_off_generic_boiler_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default Generic Boiler Controller."""
        return GenericBoilerControllerConfig(
            building_name=building_name,
            name="OnOffBoilerController",
            is_modulating=False,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from Generic Boiler config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )


class GenericBoilerController(Component):
    """Generic Boiler Controller.

    It takes data from other
    components and sends signal to the Generic_boiler for
    activation or deactivation.
    Modulating Power with respect to water temperature from storage if applied.

    Parameters
    ----------
    Components to connect to:
    (1) Generic_boiler (control_signal)

    """

    # Inputs
    WaterTemperatureInputFromHeatWaterStorage = "WaterTemperatureInputFromHeatWaterStorage"

    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    ControlSignalToGenericBoiler = "ControlSignalToGenericBoiler"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericBoilerControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build()

        # input channel
        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DailyAverageOutsideTemperature,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.control_signal_to_generic_boiler_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToGenericBoiler,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description=f"here a description for {self.ControlSignalToGenericBoiler} will follow.",
        )

        self.controller_generic_boilermode: Any
        self.previous_generic_boiler_mode: Any

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureInputFromHeatWaterStorage,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_weather(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_controller(
        self,
    ):
        """Get heat distribution controller default connections."""

        connections = []
        hds_controller_classname = HeatDistributionController.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.HeatingFlowTemperatureFromHeatDistributionSystem,
                hds_controller_classname,
                HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def build(
        self,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_generic_boilermode = "off"
        self.previous_generic_boiler_mode = self.controller_generic_boilermode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_generic_boiler_mode = self.controller_generic_boilermode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_generic_boilermode = self.previous_generic_boiler_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Generic Boiler comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_from_heat_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )

            heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(
                self.heating_flow_temperature_from_heat_distribution_system_channel
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            # turning Generic boiler off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # on/off controller comparing set flow temperature and water input temperature
            self.conditions_on_off(
                water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                summer_heating_mode=summer_heating_mode,
            )

            if self.controller_generic_boilermode == "heating":
                # get a modulated control signal between 0 and 1
                if self.config.is_modulating is True:
                    control_signal = self.modulate_power(
                        water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                        set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    )
                else:
                    control_signal = 1
            elif self.controller_generic_boilermode == "off":
                control_signal = 0
            else:
                raise ValueError("Generic Boiler Controller control_signal unknown.")

            stsv.set_output_value(self.control_signal_to_generic_boiler_channel, control_signal)

    def modulate_power(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linear between minimial_thermal_power and max_thermal_power of Generic Boiler.

        only used if generic_boilermode is "heating".
        """

        minimal_percentage = (
            self.config.minimal_thermal_power_in_watt
            / self.config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius
            - self.config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage
        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1 - (
                (
                    self.config.set_temperature_difference_for_full_power
                    - (set_heating_flow_temperature_in_celsius - water_temperature_input_in_celsius)
                )
                / self.config.set_temperature_difference_for_full_power
            )
            percentage = max(minimal_percentage, linear_fit)
            return percentage
        if (
            water_temperature_input_in_celsius <= set_heating_flow_temperature_in_celsius + 0.5
        ):  # use same hysteresis like in conditions_on_off()
            percentage = minimal_percentage
            return percentage

        # if something went wrong
        raise ValueError("Modulation of Generic Boiler needs some adjustments")

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> None:
        """Set conditions for the Generic Boiler controller mode."""

        if self.controller_generic_boilermode == "heating":
            if (
                water_temperature_input_in_celsius > (set_heating_flow_temperature_in_celsius + 0.5)
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_generic_boilermode = "off"
                return

        elif self.controller_generic_boilermode == "off":
            # Generic Boiler is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius < (set_heating_flow_temperature_in_celsius - 1.0)
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_generic_boilermode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the Generic boiler."""

        # if no heating threshold is set, the Generic boiler is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif daily_average_outside_temperature_in_celsius > set_heating_threshold_temperature_in_celsius:
            heating_mode = "off"

        # it is cold enough for heating
        elif daily_average_outside_temperature_in_celsius < set_heating_threshold_temperature_in_celsius:
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
        return heating_mode

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: GenericBoilerControllerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []