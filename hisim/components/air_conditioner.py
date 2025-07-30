"""Air Conditioner Component."""

from dataclasses import dataclass
from typing import Any, List, Optional
from dataclasses_json import dataclass_json

import numpy as np
import pandas as pd
from hisim import log

from hisim import component as cp
from hisim.component import (
    CapexCostDataClass,
    ConfigBase,
    DisplayConfig,
    OpexCostDataClass,
)
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
)
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)
from hisim.simulationparameters import SimulationParameters
from hisim.loadtypes import LoadTypes, Units
from hisim.components.weather import Weather
from hisim.components.building import Building
from hisim import utils
from hisim.sim_repository_singleton import (
    SingletonSimRepository,
    SingletonDictKeyEnum,
)

__authors__ = "Marwa Alfouly, Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt, Marwa Alfouly, Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class AirConditionerConfig(ConfigBase):
    """Configuration dataclass for the air conditioner component."""

    building_name: str
    name: str
    manufacturer: str
    model_name: str
    scale_factor: float
    t_out_cooling_ref: float
    t_out_heating_ref: float
    eer_ref: float
    cop_ref: float
    cooling_capacity_ref: float
    heating_capacity_ref: float
    investment_costs_in_euro: float
    lifetime_in_years: int
    co2_emissions_kg_co2_eq: float
    maintenance_costs_in_euro_per_year: float

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the main component class."""
        return AirConditioner.get_full_classname()

    def __init__(
        self,
        building_name: str,
        name: str,
        manufacturer="Panasonic",
        model_name="CS-RE18JKE/CU-RE18JKE",
        scale_factor=1.0,
    ):
        """Constructor."""

        super().__init__(name, building_name)
        self.manufacturer = "Panasonic"
        self.model_name = "CS-RE18JKE/CU-RE18JKE"
        self.scale_factor = scale_factor
        air_conditioners = utils.load_smart_appliance("Air Conditioner")
        air_conditioner = next(
            (
                ac
                for ac in air_conditioners
                if ac["Manufacturer"] == manufacturer
                and ac["Model"] == model_name
            ),
            None,
        )

        if air_conditioner is None:
            raise Exception(
                "Air conditioner model not registered in the database"
            )

        # Prepare reference data for interpolation
        self.t_out_cooling_ref = air_conditioner[
            "Outdoor temperature range - cooling"
        ]
        self.t_out_heating_ref = air_conditioner[
            "Outdoor temperature range - heating"
        ]
        self.eer_ref = air_conditioner["EER W/W"]
        self.cop_ref = air_conditioner["COP W/W"]
        self.cooling_capacity_ref = air_conditioner["Cooling capacity W"]
        self.heating_capacity_ref = air_conditioner["Heating capacity W"]
        # Based on https://www.obi.de/magazin/bauen/haustechnik/klimaanlage/klima-splitgeraet#Kosten
        ac_type = air_conditioner["Type"].replace("-", " ").lower()
        if "single split" in ac_type:
            installation_cost = 1400
        elif "duo" in ac_type:
            installation_cost = 2350
        elif "ducted" in ac_type or "triple" in ac_type:
            installation_cost = 3300
        else:
            raise ValueError(
                f"No installation cost information for type {air_conditioner['Type']}"
            )
        self.cost = air_conditioner["Price"] + installation_cost
        self.maintenance_cost_as_percentage_of_investment = 0.05
        # Lifetime estimation:
        # 10 years https://www.deutschlandfunk.de/belastung-fuer-die-atmosphaere-der-vormarsch-der-100.html,
        # 10-15 years https://klivago.de/faq-was-man-ueber-eine-klimaanlage-wissen-sollte
        # 15 years https://volted.ch/blogs/guides-fokus-und-bericht/wie-lange-halten-tragbare-
        # klimaanlagen?srsltid=AfmBOoojFnnbhbCYtAGCZLzdawl4C8zeNvRtc9GFeCICEwaWB6ZdKhSt
        self.lifetime = 12
        self.co2_emissions_kg_co2_eq = (
            165.84  # In first step same as for heat pump
        )

    @classmethod
    def get_default_air_conditioner_config(
        cls,
        building_name: str = "BUI1",
    ) -> "AirConditionerConfig":
        """Return default air-conditioner configuration."""

        return cls(
            building_name=building_name,
            name="AirConditioner",
            manufacturer="Samsung",  # Other option: "Panasonic" , Further options are avilable in the smart_devices file
            model_name="AC120HBHFKH/SA - AC120HCAFKH/SA",  # "AC120HBHFKH/SA - AC120HCAFKH/SA"     #Other option: "CS-TZ71WKEW + CU-TZ71WKE"#
        )

    @classmethod
    def get_scaled_air_conditioner_config(
        cls,
        heating_load: float,
        heating_reference_temperature: float,
        building_name: str = "BUI1",
    ) -> Any:
        """Return default air-conditioner configuration."""
        air_conditioners = utils.load_smart_appliance("Air Conditioner")
        air_conditioner_df = pd.DataFrame(air_conditioners)

        # Select air conditioner whose heating capacity (at temperature closes to heating reference temperature) is closest
        # to heating load of building
        def get_relevant_heating_capacity_for_temperature(
            temperature_range, capacity
        ):
            closest_temperature_index = min(
                range(len(temperature_range)),
                key=lambda i: abs(
                    temperature_range[i] - heating_reference_temperature
                ),
            )
            return capacity[closest_temperature_index]

        differences = air_conditioner_df.apply(
            lambda row: abs(
                get_relevant_heating_capacity_for_temperature(
                    row["Outdoor temperature range - heating"],
                    row["Heating capacity W"],
                )
                - heating_load
            ),
            axis=1,
        )

        selected_air_conditioner = air_conditioner_df.loc[differences.idxmin()]
        relevant_capacity_for_scaling = (
            get_relevant_heating_capacity_for_temperature(
                selected_air_conditioner[
                    "Outdoor temperature range - heating"
                ],
                selected_air_conditioner["Heating capacity W"],
            )
        )

        # 0.6 was determined heuristically based on manually experimenting
        # with different scaling factors
        scaling_factor = relevant_capacity_for_scaling * 0.6 / heating_load

        return cls(
            building_name=building_name,
            name="AirConditioner",
            manufacturer=selected_air_conditioner["Manufacturer"],
            model_name=selected_air_conditioner["Model"],
            scale_factor=scaling_factor,
        )


class AirConditioner(cp.Component):
    """Simulates an air conditioner that provides heating and cooling based on a modulating signal."""

    # Input and output channel names
    OperatingState = "State"
    ModulatingPowerSignal = "ModulatingPowerSignal"
    OutdoorAirTemperature = "TemperatureOutside"
    GridImport = "GridImport"
    PV2load = "PV2load"
    Battery2Load = "Battery2Load"
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ThermalEnergyDelivered = "ThermalEnergyDelivered"
    ElectricalPowerConsumption = "ElectricalPowerConsumption"
    ElectricalEnergyConsumption = "ElectricalEnergyConsumption"
    Efficiency = "EnergyEfficiencyRatio"
    CoefficientOfPerformance = "CoefficientOfPerformance"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Initialize the air conditioner component."""
        self.air_conditioner_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # Build model from the database
        self.build()

        # Define input channels
        self.t_out_channel = self.add_input(
            self.component_name,
            self.OutdoorAirTemperature,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.modulating_power_signal_channel = self.add_input(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            False,
        )
        self.optimal_electric_power_pv_channel = self.add_input(
            self.component_name,
            self.PV2load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_grid_channel = self.add_input(
            self.component_name,
            self.GridImport,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )
        self.optimal_electric_power_battery_channel = self.add_input(
            self.component_name,
            self.Battery2Load,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            False,
        )

        # Define output channels
        self.thermal_power_generation_channel = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description="Delivered thermal power",
        )
        self.thermal_energy_generation_channel = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalEnergyDelivered,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT_HOUR,
            output_description="Delivered thermal energy",
        )
        self.electrical_power_consumption_channel = self.add_output(
            self.component_name,
            self.ElectricalPowerConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description="Electrical power consumption",
        )
        self.electrical_energy_consumption_channel = self.add_output(
            self.component_name,
            self.ElectricalEnergyConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT_HOUR,
            output_description="Electrical energy consumption",
        )
        self.efficiency = self.add_output(
            self.component_name,
            self.Efficiency,
            LoadTypes.ANY,
            Units.ANY,
            output_description="Energy efficiency ratio for cooling.",
        )

        # Connect default inputs
        self.add_default_connections(
            self.get_default_connections_from_weather()
        )
        self.add_default_connections(
            self.get_default_connections_from_controller()
        )

    def get_default_connections_from_weather(self):
        """Connect to default weather component for outside temperature."""
        return [
            cp.ComponentConnection(
                self.OutdoorAirTemperature,
                Weather.get_classname(),
                Weather.TemperatureOutside,
            )
        ]

    def get_default_connections_from_controller(self):
        """Connect to default controller for power modulation signal."""
        return [
            cp.ComponentConnection(
                self.ModulatingPowerSignal,
                AirConditionerController.get_classname(),
                AirConditionerController.ModulatingPowerSignal,
            )
        ]

    @staticmethod
    def get_cost_capex(
        config: AirConditionerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Return capital expenditure (CAPEX) and CO2 footprint for the simulation duration."""
        seconds_per_year = 365 * 24 * 60 * 60
        duration_ratio = (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_per_period = (config.investment_costs_in_euro / config.lifetime) * duration_ratio
        co2_per_period = (
            config.co2_emissions_kg_co2_eq / config.lifetime
        ) * duration_ratio

        return CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.co2_emissions_kg_co2_eq,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_period,
            device_co2_footprint_for_simulated_period_in_kg=co2_per_period,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER,
        )

    def get_cost_opex(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:
        """Return operational expenditure (OPEX) including maintenance."""

        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.ElectricalEnergyConsumption
                and output.unit == Units.WATT_HOUR
            ):
                electricity_consumption_kwh = round(
                    sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                )
                break
        assert electricity_consumption_kwh is not None

        emissions_and_cost_factors = (
            EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                self.my_simulation_parameters.year
            )
        )

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=electricity_consumption_kwh
            * emissions_and_cost_factors.electricity_costs_in_euro_per_kwh,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=electricity_consumption_kwh
            * emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh,
            total_consumption_in_kwh=electricity_consumption_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER,
        )

        return opex_cost_data_class

    def build(self):
        """Initialize internal variables using values from air conditioner database."""

        # Fit polynomials to simulate continuous values based on temperature
        self.eer_coef = np.polyfit(
            self.config.t_out_cooling_ref, self.config.eer_ref, 1
        )
        self.cooling_capacity_coef = np.polyfit(
            self.config.t_out_cooling_ref, self.config.cooling_capacity_ref, 1
        )
        self.cop_coef = np.polyfit(
            self.config.t_out_heating_ref, self.config.cop_ref, 1
        )
        self.heating_capacity_coef = np.polyfit(
            self.config.t_out_heating_ref, self.config.heating_capacity_ref, 1
        )

        # Save coefficients for use by other components
        SingletonSimRepository().set_entry(
            SingletonDictKeyEnum.COEFFICIENT_OF_PERFORMANCE_HEATING,
            self.cop_coef,
        )
        SingletonSimRepository().set_entry(
            SingletonDictKeyEnum.ENERGY_EFFICIENY_RATIO_COOLING, self.eer_coef
        )

    # Interpolation functions
    def _calculate_energy_efficiency_ratio(self, t_out):
        return np.polyval(self.eer_coef, t_out)

    def _calculate_cooling_capacity(self, t_out):
        return np.polyval(self.cooling_capacity_coef, t_out)

    def _calculate_coefficient_of_performance(self, t_out):
        return np.polyval(self.cop_coef, t_out)

    def _calculate_heating_capacity(self, t_out):
        return np.polyval(self.heating_capacity_coef, t_out)

    def _calculate_electricity_consumption(
        self, thermal_energy: float, efficiency: float
    ) -> float:
        """Return electricity usage based on energy and efficiency."""
        if efficiency == 0:
            return 0
        return abs(thermal_energy / efficiency)

    def write_to_report(self):
        """Output relevant info to final simulation report."""
        return self.air_conditioner_config.get_string_dict()

    # Simulation hooks
    def i_prepare_simulation(self):
        """Prepares the simulation."""
        pass

    def i_save_state(self):
        """Saves the state."""
        pass

    def i_restore_state(self):
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        """Doublechecks."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ):
        """Simulate air conditioner behavior for one timestep."""
        if force_convergence:
            pass

        air_temperature_deg_c = stsv.get_input_value(self.t_out_channel)
        modulation_signal = stsv.get_input_value(
            self.modulating_power_signal_channel
        )

        efficiency = 0
        thermal_power_delivered_w = 0

        if modulation_signal > 0:
            # Heating mode
            efficiency = self._calculate_coefficient_of_performance(
                air_temperature_deg_c
            )
            thermal_power_delivered_w = (
                self._calculate_heating_capacity(air_temperature_deg_c)
                * self.config.scale_factor
                * modulation_signal
            )
        elif modulation_signal < 0:
            # Cooling mode
            efficiency = self._calculate_energy_efficiency_ratio(
                air_temperature_deg_c
            )
            thermal_power_delivered_w = (
                self._calculate_cooling_capacity(air_temperature_deg_c)
                * self.config.scale_factor
                * modulation_signal
            )

        electrical_power_consumption_w = (
            self._calculate_electricity_consumption(
                thermal_power_delivered_w, efficiency
            )
        )
        electrical_energy_consumption_wh = (
            electrical_power_consumption_w
            * self.my_simulation_parameters.seconds_per_timestep
            / 3.6e3
        )
        thermal_energy_delivered_wh = (
            thermal_power_delivered_w
            * self.my_simulation_parameters.seconds_per_timestep
            / 3.6e3
        )

        # Write outputs
        stsv.set_output_value(self.efficiency, efficiency)
        stsv.set_output_value(
            self.electrical_power_consumption_channel,
            electrical_power_consumption_w,
        )
        stsv.set_output_value(
            self.electrical_energy_consumption_channel,
            electrical_energy_consumption_wh,
        )
        stsv.set_output_value(
            self.thermal_power_generation_channel, thermal_power_delivered_w
        )
        stsv.set_output_value(
            self.thermal_energy_generation_channel, thermal_energy_delivered_wh
        )

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
        capex_dataclass = self.get_cost_capex(
            self.config, self.my_simulation_parameters
        )

        # Energy related KPIs
        electricity_consumption_kwh = KpiEntry(
            name="Electrical energy consumption",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(electricity_consumption_kwh)

        thermal_energy_delivered_cooling_in_kwh: float
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if (
                    output.field_name == self.ThermalEnergyDelivered
                    and output.unit == Units.WATT_HOUR
                ):
                    thermal_energy_delivered_cooling_in_kwh = round(
                        postprocessing_results.iloc[:, index][
                            postprocessing_results.iloc[:, index] < 0
                        ].sum()
                        * 1e-3,
                        1,
                    )
                    thermal_energy_delivered_heating_in_kwh = round(
                        postprocessing_results.iloc[:, index][
                            postprocessing_results.iloc[:, index] > 0
                        ].sum()
                        * 1e-3,
                        1,
                    )
                    break

        thermal_energy_delivered_cooling_entry = KpiEntry(
            name="Thermal energy delivered - cooling",
            unit="kWh",
            value=thermal_energy_delivered_cooling_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )

        list_of_kpi_entries.append(thermal_energy_delivered_cooling_entry)

        thermal_energy_delivered_heating_entry = KpiEntry(
            name="Thermal energy delivered - heating",
            unit="kWh",
            value=thermal_energy_delivered_heating_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )

        list_of_kpi_entries.append(thermal_energy_delivered_heating_entry)

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
            name="OPEX - Electricity costs",
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
            name="Total Costs (CAPEX for simulated period + OPEX energy and maintenance)",
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
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg
            + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
        return list_of_kpi_entries


@dataclass_json
@dataclass
class AirConditionerControllerConfig(ConfigBase):
    """Configuration class for the air conditioner controller."""

    building_name: str
    name: str
    heating_set_temperature_deg_c: float
    cooling_set_temperature_deg_c: float
    minimum_runtime_s: float
    minimum_idle_time_s: float
    offset: float
    temperature_difference_full_power_deg_c: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the associated controller class."""
        return AirConditionerController.get_full_classname()

    @classmethod
    def get_default_air_conditioner_controller_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Returns a default configuration object."""
        return cls(
            building_name=building_name,
            name="AirConditionerControllerConfig",
            heating_set_temperature_deg_c=20.0,
            cooling_set_temperature_deg_c=24.0,
            minimum_runtime_s=30 * 60,
            minimum_idle_time_s=15 * 60,
            offset=5.0,
            temperature_difference_full_power_deg_c=3.0,
        )


class AirConditionerControllerState:
    """Class representing the internal state of the air conditioner controller."""

    def __init__(
        self,
        mode: str,
        activation_time_step: int,
        deactivation_time_step: int,
        percentage: float,
    ) -> None:
        """Constructor."""

        self.mode = mode  # can be "heating", "cooling", or "off"
        self.activation_time_step = activation_time_step
        self.deactivation_time_step = deactivation_time_step
        self.percentage = (
            percentage  # current power modulation level (0.0 - 1.0)
        )

    def clone(self) -> "AirConditionerControllerState":
        """Returns a deep copy of the current state."""
        return AirConditionerControllerState(
            self.mode,
            self.activation_time_step,
            self.deactivation_time_step,
            self.percentage,
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation step (no logic required here)."""
        pass

    def activate_heating(self, timestep: int) -> None:
        """Switches mode to heating and stores activation time."""
        self.mode = "heating"
        self.activation_time_step = timestep

    def activate_cooling(self, timestep: int) -> None:
        """Switches mode to cooling and stores activation time."""
        self.mode = "cooling"
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Turns off the system and stores deactivation time."""
        self.mode = "off"
        self.deactivation_time_step = timestep


class AirConditionerController(cp.Component):
    """Controller component for modulating air conditioner behavior based on temperature."""

    TemperatureIndoorAir = "TemperatureIndoorAir"
    ElectricityInput = "ElectricityInput"
    OperatingState = "OperatingState"
    ModulatingPowerSignal = "ModulatingPowerSignal"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Initializes the air conditioner controller component."""

        self.config = config
        self.my_simulation_parameters = my_simulation_parameters

        self.minimum_runtime_in_timesteps = int(
            config.minimum_runtime_s
            / my_simulation_parameters.seconds_per_timestep
        )
        self.minimum_resting_time_in_timesteps = int(
            config.minimum_idle_time_s
            / my_simulation_parameters.seconds_per_timestep
        )

        component_name = self.get_component_name()
        super().__init__(
            component_name, my_simulation_parameters, config, my_display_config
        )

        # State initialization
        self.state = AirConditionerControllerState("off", 0, 0, 0.0)
        self.previous_state = self.state.clone()
        self.processed_state = self.state.clone()

        self.add_connections()

    def add_connections(self):
        """Registers the component's inputs and outputs."""
        self.indoor_air_temperature_channel = self.add_input(
            self.component_name,
            self.TemperatureIndoorAir,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.operation_modulating_signal_channel = self.add_output(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            output_description="Power modulation signal for the air conditioner",
        )

        self.add_default_connections(
            self.get_default_connections_from_building()
        )

    def get_default_connections_from_building(self):
        """Connects the component's input to the building temperature."""
        log.information(
            "Setting building default connections in AirConditionerController"
        )
        return [
            cp.ComponentConnection(
                self.TemperatureIndoorAir,
                Building.get_classname(),
                Building.TemperatureIndoorAir,
            )
        ]

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self, timestep: int, stsv: cp.SingleTimeStepValues
    ) -> None:
        """Doublechecks."""
        pass

    def write_to_report(self):
        """Writes to report."""

        return self.config.get_string_dict() + [
            "Air Conditioner Controller",
            f"Heating set temperature: {self.config.heating_set_temperature_deg_c} °C",
            f"Cooling set temperature: {self.config.cooling_set_temperature_deg_c} °C",
        ]

    @staticmethod
    def get_cost_capex(
        config: AirConditionerControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        # Returns default class, as controller itself has no opex cost
        capex_cost_data_class = (
            CapexCostDataClass.get_default_capex_cost_data_class()
        )
        return capex_cost_data_class

    def get_cost_opex(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:
        """Returns opex costs of component."""

        # Returns default class, as controller itself has no opex cost
        opex_cost_data_class = (
            OpexCostDataClass.get_default_opex_cost_data_class()
        )
        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []

    def i_simulate(
        self,
        timestep: int,
        stsv: cp.SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Main simulation step."""
        if force_convergence:
            self.state = self.processed_state.clone()
            mode = self.state.mode
        else:
            indoor_air_temperature_deg_c = stsv.get_input_value(
                self.indoor_air_temperature_channel
            )
            mode = self.determine_operating_mode(
                indoor_air_temperature_deg_c, timestep
            )
            percentage = self.modulate_power(
                indoor_air_temperature_deg_c, mode
            )

            self.state.mode = mode
            self.state.percentage = percentage
            self.processed_state = self.state.clone()

        # Encode state as signed power level (heating = +%, cooling = -%, off = 0)
        if mode == "heating":
            value = self.state.percentage
        elif mode == "cooling":
            value = -self.state.percentage
        elif mode == "off":
            value = 0.0
        else:
            raise ValueError(f"Unhandled mode case: {mode}")

        stsv.set_output_value(self.operation_modulating_signal_channel, value)

    def _check_minimum_operation_and_idle_time(self, timestep) -> Optional[str]:
        # Enforce minimum operation time
        if self.state.mode in {"heating", "cooling"}:
            if (
                self.state.activation_time_step
                + self.minimum_runtime_in_timesteps
                > timestep
            ):
                return self.state.mode

        # Enforce minimum idle time
        if self.state.mode == "off":
            if (
                self.state.deactivation_time_step
                + self.minimum_resting_time_in_timesteps
                > timestep
            ):
                return "off"

        return None

    def determine_operating_mode(
        self, current_temperature_deg_c: float, timestep: int
    ) -> str:  # pylint: disable=too-many-return-statements
        """Controller takes action to maintain defined comfort range."""
        operating_time_compliant_state = self._check_minimum_operation_and_idle_time(timestep)
        if operating_time_compliant_state is not None:
            return operating_time_compliant_state

        heating_setpoint = self.config.heating_set_temperature_deg_c
        cooling_setpoint = self.config.cooling_set_temperature_deg_c
        offset = self.config.offset

        # Stay in heating if within heating deadband
        if (
            self.state.mode == "heating"
            or self.processed_state.mode == "heating"
        ) and current_temperature_deg_c < heating_setpoint + offset:
            if self.state.mode != "heating":
                self.state.activate_heating(timestep)
            return "heating"

        # Stay in cooling if within cooling deadband
        if (
            self.state.mode == "cooling"
            or self.processed_state.mode == "cooling"
        ) and current_temperature_deg_c > cooling_setpoint - offset:
            if self.state.mode != "cooling":
                self.state.activate_cooling(timestep)
            return "cooling"

        # Switch to cooling if temperature exceeds upper cooling threshold (and previously not heating)
        if current_temperature_deg_c > cooling_setpoint and (
            self.state.mode != "heating"
            and self.processed_state.mode != "heating"
        ):
            if self.state.mode != "cooling":
                self.state.activate_cooling(timestep)
            return "cooling"

        # Switch to heating if temperature drops below lower heating threshold (and previously not cooling)
        if current_temperature_deg_c < heating_setpoint and (
            self.state.mode != "cooling"
            and self.processed_state.mode != "cooling"
        ):
            if self.state.mode != "heating":
                self.state.activate_heating(timestep)
            return "heating"

        if self.state.mode != "off":
            self.state.deactivate(timestep)
        return "off"

    def modulate_power(
        self, current_temperature_deg_c: float, operating_mode: str
    ) -> float:
        """Power modulation.

        Modulates power non-linearly (quadratic) based on the temperature difference.
        Power drops off more aggressively as the temperature nears the setpoint.
        """
        if operating_mode == "off":
            return 0.0

        if operating_mode == "heating":
            temperature_difference = max(
                (
                    self.config.heating_set_temperature_deg_c
                    + self.config.offset
                )
                - current_temperature_deg_c,
                0,
            )
        elif operating_mode == "cooling":
            temperature_difference = max(
                current_temperature_deg_c
                - (
                    self.config.cooling_set_temperature_deg_c
                    - self.config.offset
                ),
                0,
            )
        else:
            raise ValueError(f"Unknown operating mode: {operating_mode}")

        # Apply quadratic scaling
        capped_ratio = min(
            temperature_difference
            / self.config.temperature_difference_full_power_deg_c,
            1.0,
        )
        percentage = float(max(1 - (1 - capped_ratio) ** 2, 0.1))

        return percentage
