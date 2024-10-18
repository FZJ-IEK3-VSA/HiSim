"""district heating Module."""

# clean
# Owned
# import importlib
from dataclasses import dataclass
from typing import List, Any, Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.loadtypes import LoadTypes, Units
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
from hisim.components.heat_distribution_system import HeatDistributionController, HeatDistribution
from hisim.components.weather import Weather
# from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry  # , KpiTagEnumClass

__authors__ = "Katharina Rieck"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Katharina Rieck"
__email__ = "k.rieck@fz-juelich.de"
__status__ = ""


@dataclass_json
@dataclass
class GenericDistrictHeatingConfig(ConfigBase):
    """Configuration of the District Heating class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return DistrictHeating.get_full_classname()

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
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: consumption of the car in kWh or l
    consumption_in_kilowatt_hour: float

    @classmethod
    def get_default_district_heating_config(cls, building_name: str = "BUI1",) -> Any:
        """Get a default building_name."""
        maximal_power_in_watt: float = 12_000  # W
        config = GenericDistrictHeatingConfig(
            building_name=building_name,
            name="GenericDistrictHeating",
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
            co2_footprint=maximal_power_in_watt * 1e-3 * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=1,  # value from emission_factros_and_costs_devices.csv
            lifetime=1,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config

    @classmethod
    def get_scaled_district_heating_config(
        cls, heating_load_of_building_in_watt: float, building_name: str = "BUI1",
    ) -> Any:
        """Get a default building_name."""
        maximal_power_in_watt: float = heating_load_of_building_in_watt  # W
        config = GenericDistrictHeatingConfig(
            building_name=building_name,
            name="GenericDistrictHeating",
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
            co2_footprint=maximal_power_in_watt * 1e-3 * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=1,  # value from emission_factros_and_costs_devices.csv
            lifetime=1,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config


class DistrictHeating(Component):
    """District Heating class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the District heating modulating [0..1]
    WaterInputTemperature = "WaterInputTemperature"

    # Output
    WaterMassflowOutput = "WaterMassflowOutput"
    WaterOutputTemperature = "WaterOutputTemperature"
    ThermalOutputPower = "ThermalOutputPower"

    # @utils.graph_call_path_factory(max_depth=2, memory_flag=True, file_name="call_path")
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericDistrictHeatingConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.district_heating_config = config
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
            self.component_name, DistrictHeating.ControlSignal, LoadTypes.ANY, Units.PERCENT, True,
        )
        self.water_input_tempertaure_channel: ComponentInput = self.add_input(
            self.component_name, DistrictHeating.WaterInputTemperature, LoadTypes.WATER, Units.CELSIUS, True,
        )

        self.water_mass_flow_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeating.WaterMassflowOutput,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterMassflowOutput} will follow.",
        )
        self.water_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeating.WaterOutputTemperature,
            LoadTypes.WATER,
            Units.CELSIUS,
            output_description=f"here a description for {self.WaterOutputTemperature} will follow.",
        )
        self.thermal_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPower,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=f"here a description for {self.ThermalOutputPower} will follow.",
        )

        self.minimal_thermal_power_in_watt = self.district_heating_config.minimal_thermal_power_in_watt
        self.maximal_thermal_power_in_watt = self.district_heating_config.maximal_power_in_watt
        self.eff_th_min = self.district_heating_config.eff_th_min
        self.eff_th_max = self.district_heating_config.eff_th_max
        self.maximal_temperature_in_celsius = self.district_heating_config.maximal_temperature_in_celsius
        self.temperature_delta_in_celsius = self.district_heating_config.temperature_delta_in_celsius

        self.add_default_connections(self.get_default_connections_from_district_heating_controller())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_system())

    def get_default_connections_from_district_heating_controller(self,):
        """Get Controller District Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = DistrictHeatingController
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeating.ControlSignal, l1_controller_classname, component_class.ControlSignalToDistrictHeating,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_system(self,):
        """Get heat distribution system default connections."""

        component_class = HeatDistribution
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeating.WaterInputTemperature, hws_classname, component_class.WaterTemperatureOutput,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.district_heating_config.get_string_dict()

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
        """Simulate the district heating."""
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

        thermal_power_in_watt = maximum_power * eff_th_real * control_signal
        c_w = 4182
        mass_flow_out_temperature_in_celsius = self.temperature_delta_in_celsius + stsv.get_input_value(
            self.water_input_tempertaure_channel
        )
        mass_flow_out_in_kg_per_s = thermal_power_in_watt / (c_w * self.temperature_delta_in_celsius)

        stsv.set_output_value(self.thermal_output_power_channel, thermal_power_in_watt)  # efficiency
        stsv.set_output_value(
            self.water_output_temperature_channel, mass_flow_out_temperature_in_celsius,
        )  # efficiency
        stsv.set_output_value(self.water_mass_flow_output_channel, mass_flow_out_in_kg_per_s)  # efficiency

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: GenericDistrictHeatingConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass_json
@dataclass
class GenericDistrictHeatingControllerConfig(ConfigBase):
    """District Heating Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return DistrictHeatingController.get_full_classname()

    building_name: str
    name: str
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    minimal_thermal_power_in_watt: float  # [W]
    maximal_thermal_power_in_watt: float  # [W]
    set_temperature_difference_for_full_power: float

    @classmethod
    def get_default_district_heating_controller_config(
        cls, building_name: str = "BUI1",
    ) -> "GenericDistrictHeatingControllerConfig":
        """Gets a default Generic District Heating Controller."""
        return GenericDistrictHeatingControllerConfig(
            building_name=building_name,
            name="GenericDistrictHeatingController",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            minimal_thermal_power_in_watt=1000,  # [W]
            maximal_thermal_power_in_watt=6200,  # [W]
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )

    @classmethod
    def get_scaled_district_heating_controller_config(
        cls, heating_load_of_building_in_watt: float, building_name: str = "BUI1",
    ) -> "GenericDistrictHeatingControllerConfig":
        """Gets a default Generic Heat Pump Controller."""
        maximal_thermal_power_in_watt = heating_load_of_building_in_watt
        return GenericDistrictHeatingControllerConfig(
            building_name=building_name,
            name="GenericGasHeaterController",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
            minimal_thermal_power_in_watt=1_000,  # [W]
            maximal_thermal_power_in_watt=maximal_thermal_power_in_watt,  # [W]
            set_temperature_difference_for_full_power=5.0,  # [K] # 5.0 leads to acceptable results
        )


class DistrictHeatingController(Component):
    """District Heating Controller.

    It takes data from other
    components and sends signal to the generic_gas_heater for
    activation or deactivation.
    Modulating Power with respect to water temperature from heat distribution system.

    Parameters
    ----------
    Components to connect to:
    (1) generic_district_heating

    """

    # Inputs
    WaterTemperatureInputFromHeatDistributionSystem = "WaterTemperatureInputFromHeatDistributionSystem"
    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    ControlSignalToDistrictHeating = "ControlSignalToDistrictHeating"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericDistrictHeatingControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.district_heating_controller_config = config
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
            self.WaterTemperatureInputFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name, self.DailyAverageOutsideTemperature, LoadTypes.TEMPERATURE, Units.CELSIUS, True,
        )

        self.control_signal_to_district_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToDistrictHeating,
            LoadTypes.ANY,
            Units.PERCENT,
            output_description=f"here a description for {self.ControlSignalToDistrictHeating} will follow.",
        )

        self.controller_mode: Any
        self.previous_controller_mode: Any

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_heat_distribution())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_controller())

    def get_default_connections_from_heat_distribution(self,):
        """Get heat ditribution default connections."""

        connections = []
        source_classname = HeatDistribution.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.WaterTemperatureInputFromHeatDistributionSystem,
                source_classname,
                HeatDistribution.WaterTemperatureOutput,
            )
        )
        return connections

    def get_default_connections_from_weather(self,):
        """Get simple_water_storage default connections."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.DailyAverageOutsideTemperature,
                weather_classname,
                Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_heat_distribution_controller(self,):
        """Get heat distribution controller default connections."""

        connections = []
        hds_controller_classname = HeatDistributionController.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingController.HeatingFlowTemperatureFromHeatDistributionSystem,
                hds_controller_classname,
                HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def build(self,) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_mode = "off"
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

    def write_to_report(self,) -> List[str]:
        """Write important variables to report."""
        return self.district_heating_controller_config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the district heating comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_from_heat_distibution_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )

            heating_flow_temperature_from_heat_distribution_system = stsv.get_input_value(
                self.heating_flow_temperature_from_heat_distribution_system_channel
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            # turning district heating off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.district_heating_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # on/off controller
            self.conditions_on_off(
                water_temperature_input_in_celsius=water_temperature_input_from_heat_distibution_in_celsius,
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                summer_heating_mode=summer_heating_mode,
            )

            if self.controller_mode == "heating":
                control_signal = self.modulate_power(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_distibution_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                )
            elif self.controller_mode == "off":
                control_signal = 0
            else:
                raise ValueError("District Heating Controller control_signal unknown.")

            stsv.set_output_value(self.control_signal_to_district_heating_channel, control_signal)

    def modulate_power(
        self, water_temperature_input_in_celsius: float, set_heating_flow_temperature_in_celsius: float,
    ) -> float:
        """Modulate linear between minimial_thermal_power and max_thermal_power of District Heating."""

        minimal_percentage = (
            self.district_heating_controller_config.minimal_thermal_power_in_watt
            / self.district_heating_controller_config.maximal_thermal_power_in_watt
        )
        if (
            water_temperature_input_in_celsius
            < set_heating_flow_temperature_in_celsius
            - self.district_heating_controller_config.set_temperature_difference_for_full_power
        ):
            percentage = 1.0
            return percentage

        if water_temperature_input_in_celsius < set_heating_flow_temperature_in_celsius:
            linear_fit = 1 - (
                (
                    self.district_heating_controller_config.set_temperature_difference_for_full_power
                    - (set_heating_flow_temperature_in_celsius - water_temperature_input_in_celsius)
                )
                / self.district_heating_controller_config.set_temperature_difference_for_full_power
            )
            percentage = max(minimal_percentage, linear_fit)
            return percentage
        if (
            water_temperature_input_in_celsius <= set_heating_flow_temperature_in_celsius + 0.5
        ):  # use same hysteresis like in conditions_on_off()
            percentage = minimal_percentage
            return percentage

        # if something went wrong
        raise ValueError("modulation of district heating needs some adjustments")

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
    ) -> None:
        """Set conditions for the district heating controller mode."""

        if self.controller_mode == "heating":
            if (
                water_temperature_input_in_celsius > (set_heating_flow_temperature_in_celsius + 0.5)
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_mode = "off"
                return

        elif self.controller_mode == "off":
            # district heating is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius < (set_heating_flow_temperature_in_celsius - 1.0)
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_mode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the district heating."""

        # if no heating threshold is set, the gas_heater is always on
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

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: GenericDistrictHeatingControllerConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
