"""Oil Heater Module."""

# clean
# Owned
import importlib
from dataclasses import dataclass
from typing import List, Any, Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentConnection,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass
)
from hisim.components.simple_water_storage import SimpleHotWaterStorage
from hisim.components.weather import Weather
from hisim.components.heat_distribution_system import HeatDistributionController
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

__authors__ = "Frank Burkrad, Maximilian Hillen, Markus Blasberg"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


@dataclass_json
@dataclass
class GenericOilHeaterConfig(ConfigBase):
    """Configuration of the OilHeater class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return OilHeater.get_full_classname()

    building_name: str
    name: str
    is_modulating: bool
    minimal_thermal_power_in_watt: float  # [W]
    maximal_thermal_power_in_watt: float  # [W]
    eff_th_min: float  # [-]
    eff_th_max: float  # [-]
    delta_temperature_in_celsius: float  # [°C]
    maximal_mass_flow_in_kilogram_per_second: float  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
    maximal_temperature_in_celsius: float  # [°C]
    temperature_delta_in_celsius: float  # [°C]
    maximal_power_in_watt: float  # [W]
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: float
    #: cost for investment in Euro
    investment_costs_in_euro: float
    #: lifetime in years
    lifetime_in_years: float
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: float
    #: consumption of the car in kWh or l
    consumption_in_kilowatt_hour: float

    @classmethod
    def get_default_oilheater_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Get a default building_name."""
        maximal_power_in_watt: float = 12_000  # W
        config = GenericOilHeaterConfig(
            building_name=building_name,
            name="GenericOilHeater",
            temperature_delta_in_celsius=10,
            maximal_power_in_watt=maximal_power_in_watt,
            is_modulating=True,
            minimal_thermal_power_in_watt=1_000,  # [W]
            maximal_thermal_power_in_watt=maximal_power_in_watt,  # [W]
            eff_th_min=0.60,  # [-]
            eff_th_max=0.90,  # [-]
            delta_temperature_in_celsius=25,
            maximal_mass_flow_in_kilogram_per_second=maximal_power_in_watt
            / (4180 * 25),  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
            maximal_temperature_in_celsius=80,  # [°C])
            device_co2_footprint_in_kg=maximal_power_in_watt * 1e-3 * 19.4,  # value from emission_factros_and_costs_devices.csv
            investment_costs_in_euro=5562,  # value from emission_factros_and_costs_devices.csv
            lifetime_in_years=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_costs_in_euro_per_year=0.03 * 5562,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_oilheater_config(
        cls,
        heating_load_of_building_in_watt: float,
        building_name: str = "BUI1",
    ) -> "GenericOilHeaterConfig":
        """Get a default building_name."""
        maximal_power_in_watt: float = heating_load_of_building_in_watt  # W
        config = GenericOilHeaterConfig(
            building_name=building_name,
            name="GenericOilHeater",
            temperature_delta_in_celsius=10,
            maximal_power_in_watt=maximal_power_in_watt,
            is_modulating=True,
            minimal_thermal_power_in_watt=1_000,  # [W]
            maximal_thermal_power_in_watt=maximal_power_in_watt,  # [W]
            eff_th_min=0.60,  # [-]
            eff_th_max=0.90,  # [-]
            delta_temperature_in_celsius=25,
            maximal_mass_flow_in_kilogram_per_second=maximal_power_in_watt
            / (4180 * 25),  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
            maximal_temperature_in_celsius=80,  # [°C])
            device_co2_footprint_in_kg=maximal_power_in_watt * 1e-3 * 19.4,  # value from emission_factros_and_costs_devices.csv
            investment_costs_in_euro=5562,  # value from emission_factros_and_costs_devices.csv
            lifetime_in_years=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_costs_in_euro_per_year=0.03 * 5562,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config


class OilHeater(Component):
    """OilHeater class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the OilHeater modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"

    # Output
    MassflowOutput = "Hot Water Energy Output"
    MassflowOutputTemperature = "MassflowOutputTemperature"
    OilDemand = "OilDemand"
    ThermalOutputPower = "ThermalOutputPower"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericOilHeaterConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.oilheater_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            OilHeater.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            True,
        )
        self.mass_flow_input_tempertaure_channel: ComponentInput = self.add_input(
            self.component_name,
            OilHeater.MassflowInputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )

        self.mass_flow_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            OilHeater.MassflowOutput,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.MassflowOutput} will follow.",
        )
        self.mass_flow_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            OilHeater.MassflowOutputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.MassflowOutputTemperature} will follow.",
        )
        self.oil_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            OilHeater.OilDemand,
            lt.LoadTypes.OIL,
            lt.Units.LITER,
            output_description=f"here a description for {self.OilDemand} will follow.",
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
        )
        self.thermal_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPower,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ThermalOutputPower} will follow.",
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
        )

        self.minimal_thermal_power_in_watt = self.oilheater_config.minimal_thermal_power_in_watt
        self.maximal_thermal_power_in_watt = self.oilheater_config.maximal_power_in_watt
        self.eff_th_min = self.oilheater_config.eff_th_min
        self.eff_th_max = self.oilheater_config.eff_th_max
        self.maximal_temperature_in_celsius = self.oilheater_config.maximal_temperature_in_celsius
        self.temperature_delta_in_celsius = self.oilheater_config.temperature_delta_in_celsius

        self.add_default_connections(self.get_default_connections_from_controller_l1_generic_oil_heater())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

    def get_default_connections_from_controller_l1_generic_oil_heater(
        self,
    ):
        """Get Controller L1 Oil Heater default connections."""
        component_class = GenericOilHeaterControllerL1
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                OilHeater.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToOilHeater,
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
                OilHeater.MassflowInputTemperature,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.oilheater_config.get_string_dict()

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
        """Simulate the oil heater."""
        control_signal = stsv.get_input_value(self.control_signal_channel)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")

        # Calculate Eff
        d_eff_th = self.eff_th_max - self.eff_th_min

        if control_signal * self.maximal_thermal_power_in_watt < self.minimal_thermal_power_in_watt:
            maximum_power = self.minimal_thermal_power_in_watt
            eff_th_real = self.eff_th_min
        else:
            maximum_power = control_signal * self.maximal_thermal_power_in_watt
            eff_th_real = self.eff_th_min + d_eff_th * control_signal

        oil_power_in_watt = maximum_power * eff_th_real * control_signal
        c_w = 4182
        mass_flow_out_temperature_in_celsius = self.temperature_delta_in_celsius + stsv.get_input_value(
            self.mass_flow_input_tempertaure_channel
        )
        mass_flow_out_in_kg_per_s = oil_power_in_watt / (c_w * self.temperature_delta_in_celsius)
        oil_demand_in_watt_hour = oil_power_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e3
        # conver Wh to liters
        # https://www.energieheld.de/heizung/ratgeber/durchschnittliche-heizkosten#:~:text=Zur%20Veranschaulichung%3A%20Heiz%C3%B6l%20wird%20meistens,etwa%209%2C8%20Kilowattstunden%20Heizenergie.
        oil_demand_in_liters = oil_demand_in_watt_hour / 10000

        stsv.set_output_value(self.thermal_output_power_channel, oil_power_in_watt)  # efficiency
        stsv.set_output_value(
            self.mass_flow_output_temperature_channel,
            mass_flow_out_temperature_in_celsius,
        )  # efficiency
        stsv.set_output_value(self.mass_flow_output_channel, mass_flow_out_in_kg_per_s)  # efficiency
        stsv.set_output_value(self.oil_demand_channel, oil_demand_in_liters)  # oil consumption

    @staticmethod
    def get_cost_capex(config: GenericOilHeaterConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.investment_costs_in_euro / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.OIL_BOILER
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name and output.field_name == self.OilDemand:
                total_oil_demand_in_liters = round(sum(postprocessing_results.iloc[:, index]), 1)
                # https://www.energieheld.de/heizung/ratgeber/durchschnittliche-heizkosten#:~:text=Zur%20Veranschaulichung%3A%20Heiz%C3%B6l%20wird%20meistens,etwa%209%2C8%20Kilowattstunden%20Heizenergie.
                # 1l oil = 10 kWh
                self.config.consumption_in_kilowatt_hour = total_oil_demand_in_liters * 10

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.oil_footprint_in_kg_per_l
        co2_per_simulated_period_in_kg = total_oil_demand_in_liters * co2_per_unit

        euro_per_unit = emissions_and_cost_factors.oil_costs_in_euro_per_l
        opex_energy_cost_per_simulated_period_in_euro = total_oil_demand_in_liters * euro_per_unit

        # energy costs and co2 and everything will be considered in oil meter
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=opex_energy_cost_per_simulated_period_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=self.config.consumption_in_kilowatt_hour,
            loadtype=lt.LoadTypes.OIL,
            kpi_tag=KpiTagEnumClass.OIL_BOILER
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        total_oil_consumption_in_kilowatt_hour: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.OilDemand
                and output.load_type == lt.LoadTypes.OIL
            ):
                total_oil_demand_in_liters = round(sum(postprocessing_results.iloc[:, index]), 1)
                # https://www.energieheld.de/heizung/ratgeber/durchschnittliche-heizkosten#:~:text=Zur%20Veranschaulichung%3A%20Heiz%C3%B6l%20wird%20meistens,etwa%209%2C8%20Kilowattstunden%20Heizenergie.
                # 1l oil = 10 kWh
                total_oil_consumption_in_kilowatt_hour = total_oil_demand_in_liters * 10
                break
        my_kpi_entry = KpiEntry(
            name="Oil consumption for space heating",
            unit="kWh",
            value=total_oil_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.OIL_BOILER,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry)
        return list_of_kpi_entries


@dataclass_json
@dataclass
class GenericOilHeaterControllerL1Config(ConfigBase):
    """Oil-heater Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GenericOilHeaterControllerL1.get_full_classname()

    building_name: str
    name: str
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    minimal_thermal_power_in_watt: float  # [W]
    maximal_thermal_power_in_watt: float  # [W]
    set_temperature_difference_for_full_power: float

    @classmethod
    def get_default_generic_oil_heater_controller_config(
        cls,
        maximal_thermal_power_in_watt: float,
        minimal_thermal_power_in_watt: float = 1000,
        building_name: str = "BUI1",
    ) -> "GenericOilHeaterControllerL1Config":
        """Gets a default Generic Oil Heater Controller."""
        return GenericOilHeaterControllerL1Config(
            building_name=building_name,
            name="GenericOilHeaterController",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            # get min and max thermal power from oil heater config
            minimal_thermal_power_in_watt=minimal_thermal_power_in_watt,
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )


class GenericOilHeaterControllerL1(Component):
    """Oil Heater Controller.

    It takes data from other
    components and sends signal to the generic_oil_heater for
    activation or deactivation.
    Modulating Power with respect to water temperature from storage.

    Parameters
    ----------
    Components to connect to:
    (1) generic_oil_heater (control_signal)

    """

    # Inputs
    WaterTemperatureInputFromHeatWaterStorage = "WaterTemperatureInputFromHeatWaterStorage"

    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    ControlSignalToOilHeater = "ControlSignalToOilHeater"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericOilHeaterControllerL1Config,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.oil_heater_controller_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
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

        self.control_signal_to_oilheater_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToOilHeater,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description=f"here a description for {self.ControlSignalToOilHeater} will follow.",
        )

        self.controller_oilheatermode: Any
        self.previous_oilheater_mode: Any

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
                GenericOilHeaterControllerL1.WaterTemperatureInputFromHeatWaterStorage,
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
                GenericOilHeaterControllerL1.DailyAverageOutsideTemperature,
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
                GenericOilHeaterControllerL1.HeatingFlowTemperatureFromHeatDistributionSystem,
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
        self.controller_oilheatermode = "off"
        self.previous_oilheater_mode = self.controller_oilheatermode

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_oilheater_mode = self.controller_oilheatermode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_oilheatermode = self.previous_oilheater_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(
        self,
    ) -> List[str]:
        """Write important variables to report."""
        return self.oil_heater_controller_config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the Oil Heater comtroller."""

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

            # turning oil_heater off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.oil_heater_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # on/off controller
            self.conditions_on_off(
                water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                summer_heating_mode=summer_heating_mode,
            )

            if self.controller_oilheatermode == "heating":
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            elif self.controller_oilheatermode == "off":
                control_signal = 0
            else:
                raise ValueError("Oil Heater Controller control_signal unknown.")

            stsv.set_output_value(self.control_signal_to_oilheater_channel, control_signal)

    def modulate_power(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linear between minimial_thermal_power and max_thermal_power of Oil Heater.

        only used if oilheatermode is "heating".
        """

        minimal_percentage = (
            self.oil_heater_controller_config.minimal_thermal_power_in_watt
            / self.oil_heater_controller_config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius
            - self.oil_heater_controller_config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage
        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1 - (
                (
                    self.oil_heater_controller_config.set_temperature_difference_for_full_power
                    - (set_heating_flow_temperature_in_celsius - water_temperature_input_in_celsius)
                )
                / self.oil_heater_controller_config.set_temperature_difference_for_full_power
            )
            percentage = max(minimal_percentage, linear_fit)
            return percentage
        if (
            water_temperature_input_in_celsius <= set_heating_flow_temperature_in_celsius + 0.5
        ):  # use same hysteresis like in conditions_on_off()
            percentage = minimal_percentage
            return percentage

        # if something went wrong
        raise ValueError("modulation of Oil Heater needs some adjustments")

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> None:
        """Set conditions for the oil heater controller mode."""

        if self.controller_oilheatermode == "heating":
            if (
                water_temperature_input_in_celsius > (set_heating_flow_temperature_in_celsius + 0.5)
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_oilheatermode = "off"
                return

        elif self.controller_oilheatermode == "off":
            # oil heater is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius < (set_heating_flow_temperature_in_celsius - 1.0)
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_oilheatermode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the oil_heater."""

        # if no heating threshold is set, the oil_heater is always on
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
    def get_cost_capex(config: GenericOilHeaterControllerL1Config, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
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
