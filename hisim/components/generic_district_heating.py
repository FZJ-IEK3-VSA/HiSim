"""District Heating Module."""

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
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.configuration import PhysicsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass

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
class DistrictHeatingForSHConfig(ConfigBase):
    """Configuration of the District Heating class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return DistrictHeatingForSH.get_full_classname()

    building_name: str
    name: str
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
        """Get a default district heating."""
        config = DistrictHeatingForSHConfig(
            building_name=building_name,
            name="DistrictHeatingForSH",
            co2_footprint=0,
            cost=0,  # value from emission_factros_and_costs_devices.csv
            lifetime=1,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config


class DistrictHeatingForSH(Component):
    """District Heating class."""

    # Input
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"  # how much water temperature needs to be increased
    WaterInputTemperature = "WaterInputTemperature"
    WaterInputMassFlowRateFromHeatDistributionSystem = "WaterInputMassFlowRateFromHeatDistributionSystem"

    # Output
    WaterOutputTemperature = "WaterOutputTemperature"
    ThermalOutputPower = "ThermalOutputPower"
    WaterOutputMassFlowRate = "WaterOutputMassFlowRate"

    # @utils.graph_call_path_factory(max_depth=2, memory_flag=True, file_name="call_path")
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingForSHConfig,
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
        # Inputs
        self.delta_temperature_channel: ComponentInput = self.add_input(
            self.component_name, DistrictHeatingForSH.DeltaTemperatureNeeded, LoadTypes.TEMPERATURE, Units.CELSIUS, True,
        )
        self.water_input_temperature_channel: ComponentInput = self.add_input(
            self.component_name, DistrictHeatingForSH.WaterInputTemperature, LoadTypes.WATER, Units.CELSIUS, True,
        )
        self.water_input_mass_flow_rate_channel: ComponentInput = self.add_input(
            self.component_name,
            DistrictHeatingForSH.WaterInputMassFlowRateFromHeatDistributionSystem,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            True,
        )

        # Outputs
        self.water_mass_flow_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeatingForSH.WaterOutputMassFlowRate,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterOutputMassFlowRate} will follow.",
        )
        self.water_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            DistrictHeatingForSH.WaterOutputTemperature,
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

        self.add_default_connections(self.get_default_connections_from_district_heating_controller())
        self.add_default_connections(self.get_default_connections_from_heat_distribution_system())

    def get_default_connections_from_district_heating_controller(self,):
        """Get Controller District Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = DistrictHeatingControllerForSH
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingForSH.DeltaTemperatureNeeded, l1_controller_classname, component_class.DeltaTemperatureNeeded,
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
                DistrictHeatingForSH.WaterInputTemperature, hws_classname, component_class.WaterTemperatureOutput,
            )
        )
        connections.append(
            ComponentConnection(
                DistrictHeatingForSH.WaterInputMassFlowRateFromHeatDistributionSystem,
                hws_classname,
                component_class.WaterMassFlowHDS,
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
        # get inputs
        delta_temperature_needed_in_celsius = stsv.get_input_value(self.delta_temperature_channel)
        water_input_mass_flow_rate_in_kg_per_s = stsv.get_input_value(self.water_input_mass_flow_rate_channel)
        water_input_temperature_in_celsius = stsv.get_input_value(self.water_input_temperature_channel)
        # check values
        if delta_temperature_needed_in_celsius < 0:
            raise ValueError(
                f"Delta temperature is {delta_temperature_needed_in_celsius} °C"
                "but it should not be negative because district heating cannot provide cooling. "
                "Please check your district heating controller."
            )

        # calculate output temperature
        water_output_temperature_in_celsius = water_input_temperature_in_celsius + delta_temperature_needed_in_celsius

        # calculate thermal power delivered Q = m * cw * dT
        thermal_power_delivered_in_watt = (
            water_input_mass_flow_rate_in_kg_per_s
            * PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * delta_temperature_needed_in_celsius
        )
        stsv.set_output_value(self.thermal_output_power_channel, thermal_power_delivered_in_watt)
        stsv.set_output_value(self.water_output_temperature_channel, water_output_temperature_in_celsius)
        # use as water flow the same mass flow as heat distribution system
        stsv.set_output_value(self.water_mass_flow_output_channel, water_input_mass_flow_rate_in_kg_per_s)

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.HEATING
                and output.field_name == self.ThermalOutputPower
                and output.unit == Units.WATT
            ):
                self.config.consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )

        # TODO: most opex values need to be implemented still
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=0,
            consumption_in_kwh=self.config.consumption_in_kwh,
            loadtype=LoadTypes.DISTRICTHEATING,
            kpi_tag=KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING,
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: DistrictHeatingForSHConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        # TODO: capex values need to be implemented still
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
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
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalOutputPower and output.load_type == LoadTypes.HEATING and output.unit == Units.WATT:
                    # take only output values for heating
                    thermal_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    thermal_output_energy_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=thermal_output_power_values_in_watt,
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    )
                    thermal_output_energy_entry = KpiEntry(
                        name="Thermal output energy for space heating",
                        unit="kWh",
                        value=thermal_output_energy_in_kilowatt_hour,
                        tag=KpiTagEnumClass.DISTRICT_HEATING_SPACE_HEATING,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(thermal_output_energy_entry)

        return list_of_kpi_entries


@dataclass_json
@dataclass
class DistrictHeatingControllerForSHConfig(ConfigBase):
    """District Heating Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return DistrictHeatingControllerForSH.get_full_classname()

    building_name: str
    name: str
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]

    @classmethod
    def get_default_district_heating_controller_config(
        cls, building_name: str = "BUI1",
    ) -> Any:
        """Gets a default district heating controller."""
        return DistrictHeatingControllerForSHConfig(
            building_name=building_name,
            name="DistrictHeatingControllerForSH",
            set_heating_threshold_outside_temperature_in_celsius=16.0,
        )


class DistrictHeatingControllerForSH(Component):
    """District Heating Controller."""

    # Inputs
    WaterTemperatureInputFromHeatDistributionSystem = "WaterTemperatureInputFromHeatDistributionSystem"
    # set heating  flow temperature
    HeatingFlowTemperatureFromHeatDistributionSystem = "HeatingFlowTemperatureFromHeatDistributionSystem"

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    # Outputs
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingControllerForSHConfig,
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

        self.delta_temperature_to_district_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.DeltaTemperatureNeeded,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=f"here a description for {self.DeltaTemperatureNeeded} will follow.",
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
                DistrictHeatingControllerForSH.WaterTemperatureInputFromHeatDistributionSystem,
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
                DistrictHeatingControllerForSH.DailyAverageOutsideTemperature,
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
                DistrictHeatingControllerForSH.HeatingFlowTemperatureFromHeatDistributionSystem,
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

            heating_flow_temperature_from_heat_distribution_in_celsius = stsv.get_input_value(
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
                set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_in_celsius,
                summer_heating_mode=summer_heating_mode,
            )

            if self.controller_mode == "heating":
                # delta temperature should not be negative because district heating cannot provide cooling
                delta_temperature_in_celsius = max(
                    heating_flow_temperature_from_heat_distribution_in_celsius
                    - water_temperature_input_from_heat_distibution_in_celsius,
                    0,
                )
            elif self.controller_mode == "off":
                delta_temperature_in_celsius = 0
            else:
                raise ValueError("District Heating Controller control_signal unknown.")

            stsv.set_output_value(self.delta_temperature_to_district_heating_channel, delta_temperature_in_celsius)

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
        config: DistrictHeatingControllerForSHConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass_json
@dataclass
class DistrictHeatingForDHWConfig(ConfigBase):
    """Configuration of the District Heating class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return DistrictHeatingForDHW.get_full_classname()

    building_name: str
    name: str
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
    def get_default_district_dhw_heating_config(cls, building_name: str = "BUI1",) -> Any:
        """Get a default district heating for dhw."""
        config = DistrictHeatingForDHWConfig(
            building_name=building_name,
            name="DistrictHeatingForDHW",
            co2_footprint=0,
            cost=0,  # value from emission_factros_and_costs_devices.csv
            lifetime=1,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0,  # source: VDI2067-1
            consumption_in_kilowatt_hour=0,
        )
        return config


class DistrictHeatingForDHW(Component):
    """District Heating for DHW class."""

    # Input
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"  # how much water temperature needs to be increased
    WaterInputTemperature = "WaterInputTemperature"
    WaterInputMassFlowRateFromWarmWaterStorage = "WaterInputMassFlowRateFromWarmWaterStorage"
    # Output
    WaterOutputTemperature = "WaterOutputTemperature"
    ThermalOutputPower = "ThermalOutputPower"
    WaterOutputMassFlowRate = "WaterOutputMassFlowRate"

    # @utils.graph_call_path_factory(max_depth=2, memory_flag=True, file_name="call_path")
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingForDHWConfig,
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

        # Inputs
        self.delta_temperature_channel: ComponentInput = self.add_input(
            self.component_name, self.DeltaTemperatureNeeded, LoadTypes.TEMPERATURE, Units.CELSIUS, True,
        )
        self.water_input_temperature_channel: ComponentInput = self.add_input(
            self.component_name, self.WaterInputTemperature, LoadTypes.WATER, Units.CELSIUS, True,
        )
        self.water_input_mass_flow_rate_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterInputMassFlowRateFromWarmWaterStorage,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            True,
        )
        # Outputs
        self.water_mass_flow_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterOutputMassFlowRate,
            LoadTypes.WATER,
            Units.KG_PER_SEC,
            output_description=f"here a description for {self.WaterOutputMassFlowRate} will follow.",
        )
        self.water_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.WaterOutputTemperature,
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

        self.add_default_connections(self.get_default_connections_from_dhw_district_heating_controller())
        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_dhw_district_heating_controller(self,):
        """Get Controller District Heating default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_class = DistrictHeatingControllerForDHW
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingForDHW.DeltaTemperatureNeeded,
                l1_controller_classname,
                component_class.DeltaTemperatureNeeded,
            )
        )
        return connections

    def get_default_connections_from_simple_dhw_storage(self,):
        """Get simple dhw storage default connections."""

        component_class = SimpleDHWStorage
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingForDHW.WaterInputTemperature,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        connections.append(
            ComponentConnection(
                DistrictHeatingForDHW.WaterInputMassFlowRateFromWarmWaterStorage,
                hws_classname,
                component_class.WaterMassFlowRateOfDHW,
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
        # get inputs
        delta_temperature_needed_in_celsius = stsv.get_input_value(self.delta_temperature_channel)
        water_input_mass_flow_rate_in_kg_per_s = stsv.get_input_value(self.water_input_mass_flow_rate_channel)
        water_input_temperature_in_celsius = stsv.get_input_value(self.water_input_temperature_channel)
        # check values
        if delta_temperature_needed_in_celsius < 0:
            raise ValueError(
                f"Delta temperature is {delta_temperature_needed_in_celsius} °C"
                "but it should not be negative because district heating cannot provide cooling. "
                "Please check your district heating controller."
            )

        # calculate output temperature
        water_output_temperature_in_celsius = water_input_temperature_in_celsius + delta_temperature_needed_in_celsius

        # calculate thermal power delivered Q = m * cw * dT
        thermal_power_delivered_in_watt = (
            water_input_mass_flow_rate_in_kg_per_s
            * PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * delta_temperature_needed_in_celsius
        )
        stsv.set_output_value(self.thermal_output_power_channel, thermal_power_delivered_in_watt)
        stsv.set_output_value(self.water_output_temperature_channel, water_output_temperature_in_celsius)
        # use as water flow the same mass flow as heat distribution system
        stsv.set_output_value(self.water_mass_flow_output_channel, water_input_mass_flow_rate_in_kg_per_s)

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.load_type == LoadTypes.HEATING
                and output.field_name == self.ThermalOutputPower
                and output.unit == Units.WATT
            ):
                self.config.consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )

        # TODO: most opex values need to be implemented still
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=0,
            consumption_in_kwh=self.config.consumption_in_kwh,
            loadtype=LoadTypes.DISTRICTHEATING,
            kpi_tag=KpiTagEnumClass.DISTRICT_HEATING_DOMESTIC_HOT_WATER,
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: DistrictHeatingForDHWConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        # TODO: capex values need to be implemented still
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(all_outputs=all_outputs, postprocessing_results=postprocessing_results)
        my_kpi_entry = KpiEntry(
            name=f"{opex_dataclass.loadtype.value} consumption for DHW",
            unit="kWh",
            value=opex_dataclass.consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry)
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ThermalOutputPower and output.load_type == LoadTypes.HEATING and output.unit == Units.WATT:
                    # take only output values for heating
                    thermal_output_power_values_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    # get energy from power
                    thermal_output_energy_in_kilowatt_hour = KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=thermal_output_power_values_in_watt,
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    )
                    thermal_output_energy_entry = KpiEntry(
                        name="Thermal output energy for DHW",
                        unit="kWh",
                        value=thermal_output_energy_in_kilowatt_hour,
                        tag=KpiTagEnumClass.DISTRICT_HEATING_DOMESTIC_HOT_WATER,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(thermal_output_energy_entry)

        return list_of_kpi_entries


@dataclass_json
@dataclass
class DistrictHeatingControllerForDHWConfig(ConfigBase):
    """District Heating Controller for DHW Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return DistrictHeatingControllerForDHW.get_full_classname()

    building_name: str
    name: str

    @classmethod
    def get_default_district_heating_dhw_controller_config(
        cls, building_name: str = "BUI1",
    ) -> Any:
        """Gets a default district heating controller."""
        return DistrictHeatingControllerForDHWConfig(
            building_name=building_name, name="DistrictHeatingControllerForDHW",
        )


class DistrictHeatingControllerForDHW(Component):
    """District Heating Controller for DHW."""

    # Inputs
    WaterTemperatureInputFromWarmWaterStorage = "WaterTemperatureInputFromWarmWaterStorage"

    # Outputs
    DeltaTemperatureNeeded = "DeltaTemperatureNeeded"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: DistrictHeatingControllerForDHWConfig,
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
            self.WaterTemperatureInputFromWarmWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        # output channel
        self.delta_temperature_to_district_heating_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.DeltaTemperatureNeeded,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            output_description=f"here a description for {self.DeltaTemperatureNeeded} will follow.",
        )

        self.controller_mode: Any
        self.previous_controller_mode: Any

        self.add_default_connections(self.get_default_connections_from_simple_dhw_storage())

    def get_default_connections_from_simple_dhw_storage(self,):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                DistrictHeatingControllerForDHW.WaterTemperatureInputFromWarmWaterStorage,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
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
        # warm water should aim for 55°C, should be 60°C when leaving heat generator, see source below
        # https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser#undefined
        self.warm_water_temperature_aim_in_celsius: float = 60.0

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
            water_temperature_input_from_warm_water_storage_in_celsius = stsv.get_input_value(
                self.water_temperature_input_channel
            )

            # on/off controller
            self.conditions_on_off(
                water_temperature_input_in_celsius=water_temperature_input_from_warm_water_storage_in_celsius,
                set_heating_flow_temperature_in_celsius=self.warm_water_temperature_aim_in_celsius,
            )

            if self.controller_mode == "heating":
                # delta temperature should not be negative because district heating cannot provide cooling
                delta_temperature_in_celsius = max(
                    self.warm_water_temperature_aim_in_celsius
                    - water_temperature_input_from_warm_water_storage_in_celsius,
                    0,
                )
            elif self.controller_mode == "off":
                delta_temperature_in_celsius = 0
            else:
                raise ValueError("District Heating Controller control_signal unknown.")

            stsv.set_output_value(self.delta_temperature_to_district_heating_channel, delta_temperature_in_celsius)

    def conditions_on_off(
        self, water_temperature_input_in_celsius: float, set_heating_flow_temperature_in_celsius: float,
    ) -> None:
        """Set conditions for the district heating controller mode."""

        if self.controller_mode == "heating":
            if water_temperature_input_in_celsius > (set_heating_flow_temperature_in_celsius):
                self.controller_mode = "off"
                return

        elif self.controller_mode == "off":
            # district heating is only turned on if the water temperature is below the flow temperature
            if water_temperature_input_in_celsius < (set_heating_flow_temperature_in_celsius - 10.0):
                self.controller_mode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def get_cost_opex(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: DistrictHeatingControllerForDHWConfig, simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame,) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
