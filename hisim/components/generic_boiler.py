"""Generic Boiler Module.

This heating system can use different energy carriers (natural, gas, oil, hydrogen, ...) for combustion.
The Generic boiler chooses between two modes: a) a conventional boiler (Heizwertheizung) representing older boilers and
b) a condensing boiler (Brennwertheizung) which is more modern and uses heat not only from the combustion process but also from waste gases
which is why it has higher efficiencies than the conventional boiler.
https://www.vaillant.co.uk/advice/understanding-heating-technology/boilers/what-is-a-Generic-boiler/.

The Generic boiler controller can be set as modulating controller (which is often used)
and as non-modulating on_off controller (which is used especially for pellet and wood chip heating).
"""

# clean
# Owned
import importlib
from dataclasses import dataclass
from typing import List, Any, Optional, Tuple
from enum import Enum
import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.components.configuration import (
    PhysicsConfig,
)
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
from hisim.components.dual_circuit_system import (
    DiverterValve,
    HeatingMode,
    SetTemperatureConfig,
)
from hisim.components.simple_water_storage import (
    SimpleHotWaterStorage,
    SimpleDHWStorage,
)
from hisim.components.weather import Weather
from hisim.components.heat_distribution_system import (
    HeatDistributionController,
)
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions

__authors__ = "Frank Burkrad, Maximilian Hillen, Markus Blasberg, Katharina Rieck, Kristina Dabrock"
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
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float]
    #: lifetime in years
    lifetime_in_years: Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]
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
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config

    @staticmethod
    def scale_thermal_power(
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int],
    ) -> float:
        """Scale thermal power."""

        maximal_thermal_power_in_watt_sh = heating_load_of_building_in_watt
        maximal_thermal_power_in_watt_dhw = (
            2500 * number_of_apartments_in_building if number_of_apartments_in_building is not None else 0
        )

        maximal_thermal_power_in_watt = max(maximal_thermal_power_in_watt_sh, maximal_thermal_power_in_watt_dhw)
        if maximal_thermal_power_in_watt_dhw > 0 and maximal_thermal_power_in_watt_sh > 0:
            maximal_thermal_power_in_watt *= 1.1  # add 10% when used for both SH and DHW
        return maximal_thermal_power_in_watt

    @classmethod
    def get_scaled_condensing_gas_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a scaled condensing gas boiler scaled to heating load."""
        maximal_thermal_power_in_watt = cls.scale_thermal_power(
            heating_load_of_building_in_watt, number_of_apartments_in_building
        )
        config = GenericBoilerConfig(
            building_name=building_name,
            name="CondensingGasBoiler",
            boiler_type=BoilerType.CONDENSING,
            energy_carrier=lt.LoadTypes.GAS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=0,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
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
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_conventional_oil_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional oil boiler scaled to heating load."""
        maximal_thermal_power_in_watt = cls.scale_thermal_power(
            heating_load_of_building_in_watt, number_of_apartments_in_building
        )
        config = GenericBoilerConfig(
            building_name=building_name,
            name="ConventionalOilBoiler",
            boiler_type=BoilerType.CONVENTIONAL,
            energy_carrier=lt.LoadTypes.OIL,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=0,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_conventional_pellet_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional pellet boiler scaled to heating load.

        So far we only have the lower heating value of pellets (see PhysicsConfig),
        so only conventional pellet boilers are used.
        """
        maximal_thermal_power_in_watt = cls.scale_thermal_power(
            heating_load_of_building_in_watt, number_of_apartments_in_building
        )
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
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_conventional_wood_chip_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default conventional wood chip boiler scaled to heating load.

        So far we only have the lower heating value of wood chips (see PhysicsConfig),
        so only conventional wood chip boilers are used.
        """
        maximal_thermal_power_in_watt = cls.scale_thermal_power(
            heating_load_of_building_in_watt, number_of_apartments_in_building
        )
        config = GenericBoilerConfig(
            building_name=building_name,
            name="ConventionalWoodChipBoiler",
            boiler_type=BoilerType.CONVENTIONAL,
            energy_carrier=lt.LoadTypes.WOOD_CHIPS,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=1 / 12 * maximal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_condensing_hydrogen_boiler_config(
        cls,
        heating_load_of_building_in_watt: float,
        number_of_apartments_in_building: Optional[int] = None,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a scaled condensing hydrogen boiler scaled to heating load."""
        maximal_thermal_power_in_watt = cls.scale_thermal_power(
            heating_load_of_building_in_watt, number_of_apartments_in_building
        )
        config = GenericBoilerConfig(
            building_name=building_name,
            name="CondensingHydrogenBoiler",
            boiler_type=BoilerType.CONDENSING,
            energy_carrier=lt.LoadTypes.GREEN_HYDROGEN,
            temperature_delta_in_celsius=20,
            minimal_thermal_power_in_watt=0,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            eff_th_min=0.60,
            eff_th_max=0.90,
            # capex and device emissions are calculated in get_cost_capex function by default
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
            consumption_in_kilowatt_hour=0,
        )
        return config


class GenericBoiler(Component):
    """GenericBoiler class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the GenericBoiler modulating [0..1]
    OperatingMode = "OperatingMode"
    TemperatureDelta = "TemperatureDelta"
    WaterInputTemperatureSh = "WaterInputTemperatureSh"
    WaterInputTemperatureDhw = "WaterInputTemperatureDhw"

    # Output
    WaterOutputMassFlowSh = "WaterOutputMassFlowSh"
    WaterOutputTemperatureSh = "WaterOutputTemperatureSh"
    EnergyDemandSh = "EnergyDemandSh"
    ThermalPowerGenerationSh = "ThermalOutputPowerSh"
    ThermalOutputEnergySh = "ThermalOutputEnergySh"
    WaterOutputMassFlowDhw = "WaterOutputMassFlowDhw"
    WaterOutputTemperatureDhw = "WaterOutputTemperatureDhw"
    EnergyDemandDhw = "EnergyDemandDhw"
    ThermalOutputPowerDhw = "ThermalOutputPowerDhw"
    ThermalOutputEnergyDhw = "ThermalOutputEnergyDhw"

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
        self.operating_mode_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.OperatingMode,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            True,
        )
        self.temperature_delta_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.TemperatureDelta,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.ANY,
            True,
        )

        # Space heating
        self.water_input_temperature_sh_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputTemperatureSh,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )
        self.water_output_mass_flow_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputMassFlowSh,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterOutputMassFlowSh} will follow.",
        )
        self.water_output_temperature_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputTemperatureSh,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.WaterOutputTemperatureSh} will follow.",
        )
        self.energy_demand_sh_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.EnergyDemandSh,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.EnergyDemandSh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_power_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerGenerationSh,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ThermalPowerGenerationSh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_energy_sh_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputEnergySh,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT_HOUR,
            output_description=f"here a description for {self.ThermalOutputEnergySh} will follow.",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )

        # DHW
        self.water_input_temperature_dhw_channel: ComponentInput = self.add_input(
            self.component_name,
            GenericBoiler.WaterInputTemperatureDhw,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            False,
        )
        self.water_output_mass_flow_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputMassFlowDhw,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            output_description="Mass flow rate of warm water.",
        )
        self.water_output_temperature_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.WaterOutputTemperatureDhw,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            output_description="Warm water output temperature",
        )
        self.energy_demand_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            GenericBoiler.EnergyDemandDhw,
            lt.LoadTypes.WARM_WATER,
            lt.Units.WATT_HOUR,
            output_description="Energy demand",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_power_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerDhw,
            load_type=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.WATT,
            output_description="Thermal power output",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        self.thermal_output_energy_dhw_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputEnergyDhw,
            load_type=lt.LoadTypes.WARM_WATER,
            unit=lt.Units.WATT_HOUR,
            output_description="Thermal energy output",
            postprocessing_flag=[lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
        )
        # Set important parameters
        self.build()
        self.fuel_consumption_in_liter: float = 0
        self.fuel_consumption_in_kg: float = 0

        self.add_default_connections(self.get_default_connections_from_controller_generic_boiler())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

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
        connections.append(
            ComponentConnection(
                GenericBoiler.OperatingMode,
                l1_controller_classname,
                component_class.OperatingMode,
            )
        )
        connections.append(
            ComponentConnection(
                GenericBoiler.TemperatureDelta,
                l1_controller_classname,
                component_class.TemperatureDelta,
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
                GenericBoiler.WaterInputTemperatureSh,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(
        self,
    ):
        """Get Simple dhw storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleDHWStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoiler.WaterInputTemperatureDhw,
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
        # self.temperature_delta_in_celsius = (
        #     self.config.temperature_delta_in_celsius
        # )
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

        # J = kWh/(3.6 * 1e6) and m3 = 1e3 l
        self.heating_value_of_fuel_in_kwh_per_liter = self.heating_value_of_fuel_in_joule_per_m3 / (3.6 * 1e9)
        self.fuel_density_in_kg_per_m3 = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=self.config.energy_carrier
        ).density_in_kg_per_m3

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

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the Generic Boiler."""
        control_signal = stsv.get_input_value(self.control_signal_channel)
        operating_mode = stsv.get_input_value(self.operating_mode_channel)
        temperature_delta = stsv.get_input_value(self.temperature_delta_channel)

        if not 0 <= control_signal <= 1:
            raise Exception(f"Expected a control signal between 0 and 1, not {control_signal}")

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

        # thermal power delivered from combustion
        thermal_power_delivered_in_watt = maximum_power_used_in_watt * real_combustion_efficiency
        thermal_energy_delivered_in_watt_hour = (
            thermal_power_delivered_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        )
        mass_flow_out_in_kg_per_second = (
            thermal_power_delivered_in_watt
            / (self.specific_heat_capacity_water_in_joule_per_kilogram_per_celsius * temperature_delta)
            if temperature_delta > 0
            else 0
        )

        if operating_mode == HeatingMode.SPACE_HEATING.value:
            stsv.set_output_value(
                self.thermal_output_power_sh_channel,
                thermal_power_delivered_in_watt,
            )
            stsv.set_output_value(
                self.thermal_output_energy_sh_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(
                self.energy_demand_sh_channel,
                fuel_energy_consumption_in_watt_hour,
            )
            water_output_temperature_in_celsius = temperature_delta + stsv.get_input_value(
                self.water_input_temperature_sh_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                water_output_temperature_in_celsius,
            )
            stsv.set_output_value(
                self.water_output_mass_flow_sh_channel,
                mass_flow_out_in_kg_per_second,
            )
            for channel in [
                self.thermal_output_power_dhw_channel,
                self.thermal_output_energy_dhw_channel,
                self.energy_demand_dhw_channel,
                self.water_output_mass_flow_dhw_channel,
            ]:
                stsv.set_output_value(channel, 0)
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                stsv.get_input_value(self.water_input_temperature_dhw_channel),
            )
        elif operating_mode == HeatingMode.DOMESTIC_HOT_WATER.value:
            stsv.set_output_value(
                self.thermal_output_power_dhw_channel,
                thermal_power_delivered_in_watt,
            )
            stsv.set_output_value(
                self.thermal_output_energy_dhw_channel,
                thermal_energy_delivered_in_watt_hour,
            )
            stsv.set_output_value(
                self.energy_demand_dhw_channel,
                fuel_energy_consumption_in_watt_hour,
            )

            water_output_temperature_in_celsius = temperature_delta + stsv.get_input_value(
                self.water_input_temperature_dhw_channel
            )
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                water_output_temperature_in_celsius,
            )

            stsv.set_output_value(
                self.water_output_mass_flow_dhw_channel,
                mass_flow_out_in_kg_per_second,
            )
            for channel in [
                self.thermal_output_power_sh_channel,
                self.thermal_output_energy_sh_channel,
                self.energy_demand_sh_channel,
                self.water_output_temperature_sh_channel,
                self.water_output_mass_flow_sh_channel,
            ]:
                stsv.set_output_value(channel, 0)
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                stsv.get_input_value(self.water_input_temperature_sh_channel),
            )
        elif operating_mode == HeatingMode.OFF.value:
            for channel in [
                self.thermal_output_power_sh_channel,
                self.thermal_output_energy_sh_channel,
                self.energy_demand_sh_channel,
                self.water_output_temperature_sh_channel,
                self.water_output_mass_flow_sh_channel,
            ]:
                stsv.set_output_value(channel, 0)
            stsv.set_output_value(
                self.water_output_temperature_sh_channel,
                stsv.get_input_value(self.water_input_temperature_sh_channel),
            )
            for channel in [
                self.thermal_output_power_dhw_channel,
                self.thermal_output_energy_dhw_channel,
                self.energy_demand_dhw_channel,
                self.water_output_mass_flow_dhw_channel,
            ]:
                stsv.set_output_value(channel, 0)
            stsv.set_output_value(
                self.water_output_temperature_dhw_channel,
                stsv.get_input_value(self.water_input_temperature_dhw_channel),
            )
        else:
            raise ValueError(f"Unknown operating mode {operating_mode}")

    @staticmethod
    def get_cost_capex(
        config: GenericBoilerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""

        if config.energy_carrier == lt.LoadTypes.GAS:
            kpi_tag = KpiTagEnumClass.GAS_BOILER
            component_type = lt.ComponentType.GAS_HEATER
        elif config.energy_carrier == lt.LoadTypes.OIL:
            kpi_tag = KpiTagEnumClass.OIL_BOILER
            component_type = lt.ComponentType.OIL_HEATER
        elif config.energy_carrier == lt.LoadTypes.GREEN_HYDROGEN:
            kpi_tag = KpiTagEnumClass.HYDROGEN_BOILER
            component_type = lt.ComponentType.HYDROGEN_HEATER
        elif config.energy_carrier == lt.LoadTypes.PELLETS:
            kpi_tag = KpiTagEnumClass.PELLET_BOILER
            component_type = lt.ComponentType.PELLET_HEATER
        elif config.energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            kpi_tag = KpiTagEnumClass.WOOD_CHIP_BOILER
            component_type = lt.ComponentType.WOOD_CHIP_HEATER
        else:
            raise ValueError(f"Energy carrier {config.energy_carrier} for generic_boiler not implemented yet.")

        unit = lt.Units.KILOWATT
        size_of_energy_system = config.maximal_thermal_power_in_watt * 1e-3

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )

        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        sh_consumption_in_kilowatt_hour = None
        dhw_consumption_in_kwh = None
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.EnergyDemandSh
                and output.unit == lt.Units.WATT_HOUR
            ):
                sh_consumption_in_kilowatt_hour = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)
            if (
                output.component_name == self.component_name
                and output.field_name == self.EnergyDemandDhw
                and output.unit == lt.Units.WATT_HOUR
            ):
                dhw_consumption_in_kwh = round(sum(postprocessing_results.iloc[:, index]) * 1e-3, 1)

        assert sh_consumption_in_kilowatt_hour is not None
        assert dhw_consumption_in_kwh is not None
        self.config.consumption_in_kilowatt_hour = sh_consumption_in_kilowatt_hour + dhw_consumption_in_kwh

        self.fuel_consumption_in_liter = round(
            self.config.consumption_in_kilowatt_hour / self.heating_value_of_fuel_in_kwh_per_liter,
            1,
        )
        self.fuel_consumption_in_kg = round(
            self.fuel_consumption_in_liter * 1e-3 * self.fuel_density_in_kg_per_m3,
            1,
        )
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        if self.energy_carrier == lt.LoadTypes.GAS:
            kpi_tag = KpiTagEnumClass.GAS_BOILER
            co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.OIL:
            kpi_tag = KpiTagEnumClass.OIL_BOILER
            co2_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
            euro_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
            co2_per_simulated_period_in_kg = self.fuel_consumption_in_liter * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_liter * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.GREEN_HYDROGEN:
            kpi_tag = KpiTagEnumClass.HYDROGEN_BOILER
            co2_per_unit = emissions_and_cost_factors.green_hydrogen_gas_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.green_hydrogen_gas_costs_in_euro_per_kwh
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.config.consumption_in_kilowatt_hour * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.PELLETS:
            kpi_tag = KpiTagEnumClass.PELLET_BOILER
            co2_per_unit = emissions_and_cost_factors.pellet_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.pellet_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_kg / 1000 * euro_per_unit

        elif self.energy_carrier == lt.LoadTypes.WOOD_CHIPS:
            kpi_tag = KpiTagEnumClass.WOOD_CHIP_BOILER
            co2_per_unit = emissions_and_cost_factors.wood_chip_footprint_in_kg_per_kwh
            euro_per_unit = emissions_and_cost_factors.wood_chip_costs_in_euro_per_t
            co2_per_simulated_period_in_kg = self.config.consumption_in_kilowatt_hour * co2_per_unit
            opex_energy_cost_per_simulated_period_in_euro = self.fuel_consumption_in_kg / 1000 * euro_per_unit

        else:
            raise ValueError(f"Energy carrier {self.energy_carrier} not implemented for Generic boiler.")

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=self.config.consumption_in_kilowatt_hour,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kwh,
            consumption_for_space_heating_in_kwh=sh_consumption_in_kilowatt_hour,
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
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(self.config, self.my_simulation_parameters)

        # Energy related KPIs
        sh_thermal_energy_delivered_in_kilowatt_hour = None
        dhw_thermal_energy_delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalOutputEnergySh and output.unit == lt.Units.WATT_HOUR:
                    sh_thermal_energy_delivered_in_kilowatt_hour = round(
                        sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                    )
                if output.field_name == self.ThermalOutputEnergyDhw and output.unit == lt.Units.WATT_HOUR:
                    dhw_thermal_energy_delivered_in_kilowatt_hour = round(
                        sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                    )

        assert sh_thermal_energy_delivered_in_kilowatt_hour is not None
        assert dhw_thermal_energy_delivered_in_kilowatt_hour is not None
        total_thermal_energy_delivered_in_kilowatt_hour = (
            sh_thermal_energy_delivered_in_kilowatt_hour + dhw_thermal_energy_delivered_in_kilowatt_hour
        )
        thermal_energy_delivered_entry = KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=total_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(thermal_energy_delivered_entry)

        sh_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for space heating",
            unit="kWh",
            value=sh_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(sh_thermal_energy_delivered_entry)
        dhw_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for domestic hot water",
            unit="kWh",
            value=dhw_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_thermal_energy_delivered_entry)

        energy_consumption = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (energy)",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        sh_energy_consumption = KpiEntry(
            name=f"Energy {self.energy_carrier.value} consumption for space heating",
            unit="kWh",
            value=opex_dataclass.consumption_for_space_heating_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(sh_energy_consumption)

        dhw_energy_consumption = KpiEntry(
            name=f"Energy {self.energy_carrier.value} consumption for doemstic hot water",
            unit="kWh",
            value=opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_energy_consumption)

        fuel_consumption_l = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (volume)",
            unit="l",
            value=self.fuel_consumption_in_liter,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(fuel_consumption_l)

        fuel_consumption_kg = KpiEntry(
            name=f"Total {self.energy_carrier.value} consumption (mass)",
            unit="kg",
            value=self.fuel_consumption_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(fuel_consumption_kg)

        # Economic and environmental KPIs
        capex = KpiEntry(
            name="CAPEX - Investment cost",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(capex)

        co2_footprint_capex = KpiEntry(
            name="CAPEX - CO2 Footprint",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_capex)

        opex = KpiEntry(
            name="OPEX - Fuel costs",
            unit="EUR",
            value=opex_dataclass.opex_energy_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex)

        maintenance_costs = KpiEntry(
            name="OPEX - Maintenance costs",
            unit="EUR",
            value=opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(maintenance_costs)

        co2_footprint = KpiEntry(
            name="OPEX - CO2 Footprint",
            unit="kg",
            value=opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint)

        total_costs = KpiEntry(
            name="Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_for_simulated_period_in_euro
            + opex_dataclass.opex_energy_cost_in_euro
            + opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_costs)

        total_co2_footprint = KpiEntry(
            name="Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
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
    minimum_runtime_in_seconds: float
    minimum_resting_time_in_seconds: float
    dhw_hysteresis_offset: float
    with_domestic_hot_water_preparation: bool
    secondary_mode: Optional[bool]  # If used as secondary heat generator for DHW in hybrid mode

    @classmethod
    def get_default_modulating_generic_boiler_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        building_name: str = "BUI1",
        secondary_mode: bool = False,
        with_domestic_hot_water_preparation: bool = False,
    ) -> Any:
        """Gets a default Generic Boiler Controller, for example for gas and oil boilers."""
        return GenericBoilerControllerConfig(
            building_name=building_name,
            name="ModulatingBoilerController",
            is_modulating=True,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from Generic Boiler config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
            minimum_runtime_in_seconds=1800,
            minimum_resting_time_in_seconds=1800,
            dhw_hysteresis_offset=10,
            secondary_mode=secondary_mode,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )

    @classmethod
    def get_default_on_off_generic_boiler_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        with_domestic_hot_water_preparation: bool = False,
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
            minimum_resting_time_in_seconds=0,
            minimum_runtime_in_seconds=0,
            dhw_hysteresis_offset=10,
            secondary_mode=False,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )

    @classmethod
    def get_default_pellet_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        with_domestic_hot_water_preparation: bool = False,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default controller for pellet boiler."""
        return GenericBoilerControllerConfig(
            building_name=building_name,
            name="PelletBoilerController",
            is_modulating=False,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from Generic Boiler config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
            minimum_resting_time_in_seconds=15 * 60,
            minimum_runtime_in_seconds=30 * 60,
            dhw_hysteresis_offset=10,
            secondary_mode=False,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )

    @classmethod
    def get_default_wood_chip_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float,
        with_domestic_hot_water_preparation: bool = False,
        building_name: str = "BUI1",
    ) -> Any:
        """Gets a default controller for wood chip boiler."""
        return GenericBoilerControllerConfig(
            building_name=building_name,
            name="WoodChipBoilerController",
            is_modulating=False,
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from Generic Boiler config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
            minimum_resting_time_in_seconds=30 * 60,
            minimum_runtime_in_seconds=60 * 60,
            dhw_hysteresis_offset=10,  # overheating of buffer storage to reduce number of startups
            secondary_mode=False,
            with_domestic_hot_water_preparation=with_domestic_hot_water_preparation,
        )


class GenericBoilerControllerState:
    """Data class that saves the state of the controller."""

    def __init__(
        self,
        on_off: int,
        activation_time_step: int,
        deactivation_time_step: int,
        percentage: float,
    ) -> None:
        """Initializes the heat pump controller state."""
        self.on_off: int = on_off
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step
        self.percentage: float = percentage

    def clone(self) -> "GenericBoilerControllerState":
        """Copies the current instance."""
        return GenericBoilerControllerState(
            on_off=self.on_off,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
            percentage=self.percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate(self, timestep: int) -> None:
        """Activates the heat pump and remembers the time step."""
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the heat pump and remembers the time step."""
        self.on_off = 0
        self.deactivation_time_step = timestep


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
    WaterTemperatureInputFromWaterStorage = "WaterTemperatureInputFromWaterStorage"
    WaterTemperatureInputFromDHWStorage = "WaterTemperatureInputFromDHWStorage"

    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    ControlSignalToGenericBoiler = "ControlSignalToGenericBoiler"
    OperatingMode = "OperatingMode"
    TemperatureDelta = "TemperatureDelta"

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

        # warm water should always have at least 55°C, should be 60°C when leaving heat generator, see source below
        # https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser#undefined
        self.warm_water_temperature_aim_in_celsius: float = 60.0

        self.minimum_runtime_in_timesteps = int(
            self.config.minimum_runtime_in_seconds / self.my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            self.config.minimum_resting_time_in_seconds / self.my_simulation_parameters.seconds_per_timestep
        )

        self.state: GenericBoilerControllerState = GenericBoilerControllerState(0, 0, 0, 0)

        self.build()

        # input channel
        self.water_temperature_space_heating_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromWaterStorage,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        if self.config.with_domestic_hot_water_preparation:
            self.water_temperature_dhw_input_channel: ComponentInput = self.add_input(
                self.component_name,
                self.WaterTemperatureInputFromDHWStorage,
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
            output_description="Control signal for modulation of boiler.",
        )
        self.operating_mode_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.OperatingMode,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Operating mode.",
        )
        self.temperature_delta_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.TemperatureDelta,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.ANY,
            output_description="Temperature difference between actual and set water temperature.",
        )

        self.controller_mode: HeatingMode
        self.previous_controller_mode: HeatingMode

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_dhw_storage())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureInputFromWaterStorage,
                storage_classname,
                SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_dhw_storage(
        self,
    ) -> List[ComponentConnection]:
        """Get default connections from DHW storage."""
        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                GenericBoilerController.WaterTemperatureInputFromDHWStorage,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
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
        self.controller_mode = HeatingMode.OFF
        self.previous_controller_mode = self.controller_mode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_controller_mode = self.controller_mode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_mode = self.previous_controller_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.config.get_string_dict()

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the Generic Boiler comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs
            water_temperature_input_from_space_heating_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_space_heating_input_channel
            )

            water_temperature_input_from_dhw_water_storage_in_celsius = None
            if self.config.with_domestic_hot_water_preparation:
                water_temperature_input_from_dhw_water_storage_in_celsius = stsv.get_input_value(
                    self.water_temperature_dhw_input_channel
                )

            heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(
                self.heating_flow_temperature_from_heat_distribution_system_channel
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            control_signal, temperature_delta = self.determine_operating_mode(
                daily_avg_outside_temperature_in_celsius,
                water_temperature_input_from_space_heating_water_storage_in_celsius,
                water_temperature_input_from_dhw_water_storage_in_celsius,
                heating_flow_temperature_from_heat_distribution_system,
                timestep,
            )

            stsv.set_output_value(self.control_signal_to_generic_boiler_channel, control_signal)
            stsv.set_output_value(self.operating_mode_channel, self.controller_mode.value)
            stsv.set_output_value(self.temperature_delta_channel, temperature_delta)

    def determine_operating_mode(
        self,
        daily_avg_outside_temperature_in_celsius: float,
        water_temperature_input_from_space_heating_water_storage_in_celsius: float,
        water_temperature_input_from_dhw_water_storage_in_celsius: Optional[float],
        heating_flow_temperature_from_heat_distribution_system: float,
        timestep,
    ) -> Tuple[float, float]:
        """Determine which operating mode to use in dual-circuit system."""

        previous_controller_mode = self.controller_mode
        self.controller_mode = DiverterValve.determine_operating_mode(
            with_domestic_hot_water_preparation=self.config.with_domestic_hot_water_preparation,
            current_controller_mode=previous_controller_mode,
            daily_average_outside_temperature=daily_avg_outside_temperature_in_celsius,
            water_temperature_input_sh_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
            water_temperature_input_dhw_in_celsius=water_temperature_input_from_dhw_water_storage_in_celsius,
            set_temperatures=SetTemperatureConfig(
                set_temperature_space_heating=heating_flow_temperature_from_heat_distribution_system,
                set_temperature_dhw=self.warm_water_temperature_aim_in_celsius,
                hysteresis_dhw_offset=self.config.dhw_hysteresis_offset,
                outside_temperature_threshold=self.config.set_heating_threshold_outside_temperature_in_celsius,
            ),
        )

        # Enforce minimum run and idle times (if necessary overwrites previously set mode)
        self.enforce_minimum_run_and_idle_times(previous_controller_mode, timestep)

        if self.controller_mode == HeatingMode.SPACE_HEATING:
            # get a modulated control signal between 0 and 1
            if self.config.is_modulating is True:
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_space_heating_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            else:
                control_signal = 1
            temperature_delta = (
                heating_flow_temperature_from_heat_distribution_system
                - water_temperature_input_from_space_heating_water_storage_in_celsius
            )
        elif self.controller_mode == HeatingMode.DOMESTIC_HOT_WATER:
            assert self.config.with_domestic_hot_water_preparation is not None
            assert water_temperature_input_from_dhw_water_storage_in_celsius is not None
            if self.config.is_modulating is True:
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_dhw_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius
                    + self.config.dhw_hysteresis_offset,
                )
            else:
                control_signal = 1
            temperature_delta = max(
                (self.warm_water_temperature_aim_in_celsius + self.config.dhw_hysteresis_offset)
                - water_temperature_input_from_dhw_water_storage_in_celsius,
                0,
            )
        elif self.controller_mode == HeatingMode.OFF:
            control_signal = 0
            temperature_delta = 0
        else:
            raise ValueError("Controller mode unknown.")

        return control_signal, temperature_delta

    def enforce_minimum_run_and_idle_times(self, previous_controller_mode: HeatingMode, timestep: int) -> None:
        """Enforces minimum run and idle times."""
        if (
            previous_controller_mode != HeatingMode.OFF
            and self.state.activation_time_step + self.minimum_runtime_in_timesteps > timestep
            and self.controller_mode == HeatingMode.OFF
        ):
            # mandatory on, minimum runtime not reached
            self.controller_mode = previous_controller_mode
        if (
            previous_controller_mode == HeatingMode.OFF
            and self.state.deactivation_time_step + self.minimum_resting_time_in_timesteps > timestep
            and self.controller_mode != HeatingMode.OFF
        ):
            self.controller_mode = HeatingMode.OFF
            # mandatory off, minimum resting time not reached

    def modulate_power(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linearly between minimial_thermal_power and max_thermal_power of Generic Boiler.

        Use only when boiler is in operating mode, as this function will not go below the
        percentage required to fullfill the minimum thermal power requirement.
        """

        minimal_percentage = float(
            self.config.minimal_thermal_power_in_watt / self.config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius - self.config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage
        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1.0 - (
                (
                    self.config.set_temperature_difference_for_full_power
                    - (set_heating_flow_temperature_in_celsius - water_temperature_input_in_celsius)
                )
                / self.config.set_temperature_difference_for_full_power
            )
            percentage = float(max(minimal_percentage, linear_fit))
            return percentage  # type: ignore
        if water_temperature_input_in_celsius <= set_heating_flow_temperature_in_celsius:
            percentage = minimal_percentage
            return percentage  # type: ignore

        return minimal_percentage

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
        config: GenericBoilerControllerConfig,
        simulation_parameters: SimulationParameters,
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
