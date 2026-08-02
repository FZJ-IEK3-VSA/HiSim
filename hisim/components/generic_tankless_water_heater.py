"""Tankless Electric Water Heater Module (Durchlauferhitzer).

Models an instantaneous electric water heater: a resistive device that heats the
domestic hot water draw *while it flows*, without any storage vessel. It is the
counterpart to the storage-based electric DHW path
(:mod:`hisim.components.generic_electric_heating` charging a
:class:`~hisim.components.simple_water_storage.SimpleDHWStorage`) and exists so that
domestic hot water can be prepared electrically while space heating is served by a
completely independent generator (e.g. an oil ``GenericBoiler``). The component has
no controller, no storage and no connection to the building: it reacts to the water
draw alone, which is exactly how a Durchlauferhitzer behaves.

Model
-----
The warm-water draw arrives as a volume per timestep (litres, from the LPG/UTSP
occupancy profile), defined at :attr:`TanklessWaterHeaterConfig.set_water_temperature_in_celsius`
and drawn from cold water at :attr:`TanklessWaterHeaterConfig.cold_water_temperature_in_celsius`::

    m_dot     = volume * rho / seconds_per_timestep                [kg/s]
    Q_demand  = m_dot * c_p * (T_set - T_cold)                     [W]
    P_el      = min(Q_demand / efficiency, maximum_electric_power) [W]
    Q_deliver = P_el * efficiency                                  [W]
    T_out     = T_cold + Q_deliver / (m_dot * c_p)                 [°C]

When the rated power cannot cover the draw, the outlet temperature drops below the
set temperature and the shortfall is reported on ``UnmetThermalDemandPower`` rather
than raising — an undersized Durchlauferhitzer delivers lukewarm water, it does not
fail. Note that the power cap acts on the *timestep average* flow: with coarse
timesteps a short high-flow draw is smeared out and the cap bites less often than it
would in reality, so sizing studies should use a fine resolution (60 s or less).

The heat capacity flow uses the same water properties and the same 40 °C density as
:class:`~hisim.components.simple_water_storage.SimpleDHWStorage`, so a scenario that
swaps the storage path for this one keeps a consistent energy balance.
"""

# clean
# Owned
from dataclasses import dataclass
import importlib
from typing import List, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim.component import (
    CapexCostDataClass,
    Component,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    DisplayConfig,
    OpexCostDataClass,
    SingleTimeStepValues,
)
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
    HouseholdWarmWaterDemandConfig,
    PhysicsConfig,
)
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes, OutputPostprocessingRules, Units
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters

__authors__ = "Valentin Janser"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""

#: Density of water at 40 °C, the temperature the LPG warm-water volumes refer to.
#: https://www.internetchemie.info/chemie-lexikon/daten/w/wasser-dichtetabelle.php
DENSITY_OF_WATER_AT_40_DEGREE_CELSIUS_IN_KG_PER_LITER = 0.992

#: Rated electric power of a typical single-household three-phase Durchlauferhitzer.
#: Common German sizes are 18 / 21 / 24 kW; 21 kW is the usual choice for a shower
#: plus a wash basin.
DEFAULT_RATED_ELECTRIC_POWER_IN_WATT = 21_000.0


@dataclass_json
@dataclass
class TanklessWaterHeaterConfig(ConfigBase):
    """Configuration of the TanklessWaterHeater class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return TanklessWaterHeater.get_full_classname()

    building_name: str
    name: str
    #: Maximum electric power the device can draw
    maximum_electric_power_in_watt: float
    #: Efficiency of the electric to thermal power conversion
    efficiency: float
    #: Temperature the drawn water is heated to
    set_water_temperature_in_celsius: float
    #: Temperature of the cold water entering the device
    cold_water_temperature_in_celsius: float
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float]
    #: lifetime in years
    lifetime_in_years: Optional[float]
    #: maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float]
    #: subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]

    @classmethod
    def get_default_tankless_water_heater_config(
        cls,
        building_name: str = "BUI1",
    ) -> "TanklessWaterHeaterConfig":
        """Get a default tankless water heater for a single household."""
        return TanklessWaterHeaterConfig(
            building_name=building_name,
            name="TanklessWaterHeater",
            maximum_electric_power_in_watt=DEFAULT_RATED_ELECTRIC_POWER_IN_WATT,
            efficiency=0.99,
            # The LPG warm-water volumes are defined at this temperature, see
            # SimpleDHWStorage.build(), so heating to it reproduces the drawn energy.
            set_water_temperature_in_celsius=float(
                HouseholdWarmWaterDemandConfig.ww_temperature_demand
                - HouseholdWarmWaterDemandConfig.temperature_difference_hot
            ),
            cold_water_temperature_in_celsius=float(HouseholdWarmWaterDemandConfig.freshwater_temperature),
            device_co2_footprint_in_kg=None,
            investment_costs_in_euro=None,
            lifetime_in_years=None,
            maintenance_costs_in_euro_per_year=None,
            subsidy_as_percentage_of_investment_costs=None,
        )

    @classmethod
    def get_scaled_tankless_water_heater_config(
        cls,
        number_of_apartments: float,
        building_name: str = "BUI1",
    ) -> "TanklessWaterHeaterConfig":
        """Get a tankless water heater scaled to the number of apartments.

        Each apartment is assumed to have its own device, which is how multi-family
        buildings with decentral DHW are actually equipped.
        """
        if number_of_apartments <= 0:
            raise ValueError(
                f"number_of_apartments is {number_of_apartments} but must be positive "
                "in order to scale the tankless water heater."
            )
        config = cls.get_default_tankless_water_heater_config(building_name=building_name)
        config.maximum_electric_power_in_watt = DEFAULT_RATED_ELECTRIC_POWER_IN_WATT * number_of_apartments
        return config


class TanklessWaterHeater(Component):
    """Tankless electric water heater (Durchlauferhitzer).

    Heats the domestic hot water draw on demand, without storage. See the module
    docstring for the model equations and its limitations.
    """

    # Inputs
    WaterConsumption = "WaterConsumption"
    ColdWaterTemperature = "ColdWaterTemperature"

    # Outputs
    ThermalOutputDhwPower = "ThermalOutputDhwPower"
    ThermalOutputDhwEnergy = "ThermalOutputDhwEnergy"
    ElectricOutputDhwPower = "ElectricOutputDhwPower"
    ElectricOutputDhwEnergy = "ElectricOutputDhwEnergy"
    WaterOutputDhwTemperature = "WaterOutputDhwTemperature"
    WaterOutputDhwMassFlowRate = "WaterOutputDhwMassFlowRate"
    UnmetThermalDemandPower = "UnmetThermalDemandPower"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: TanklessWaterHeaterConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the necessary attributes."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config: TanklessWaterHeaterConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        if config.maximum_electric_power_in_watt <= 0:
            raise ValueError(
                f"The maximum electric power of {self.component_name} is "
                f"{config.maximum_electric_power_in_watt} W but must be positive."
            )
        if not 0 < config.efficiency <= 1:
            raise ValueError(
                f"The efficiency of {self.component_name} is {config.efficiency} "
                "but must be in the interval (0, 1]."
            )
        if config.set_water_temperature_in_celsius <= config.cold_water_temperature_in_celsius:
            raise ValueError(
                f"The set water temperature of {self.component_name} is "
                f"{config.set_water_temperature_in_celsius} °C which is not above the cold water "
                f"temperature of {config.cold_water_temperature_in_celsius} °C. "
                "A tankless water heater cannot provide cooling."
            )

        self.specific_heat_capacity_of_water_in_joule_per_kg_per_kelvin = (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        )

        # Inputs
        self.water_consumption_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.WaterConsumption,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.LITER,
            mandatory=True,
        )
        # Optional: lets a scenario feed a seasonally varying mains temperature. When it
        # is left unconnected the constant from the config is used instead.
        self.cold_water_temperature_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ColdWaterTemperature,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=False,
        )

        # Outputs
        self.thermal_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputDhwPower,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.WATT,
            output_description="Thermal power delivered to the domestic hot water draw.",
        )
        self.thermal_output_energy_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputDhwEnergy,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.WATT_HOUR,
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            output_description="Thermal energy delivered to the domestic hot water draw.",
        )
        self.electric_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputDhwPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            output_description="Electric power drawn for domestic hot water preparation.",
        )
        self.electric_output_energy_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricOutputDhwEnergy,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT_HOUR,
            postprocessing_flag=[OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
            output_description="Electric energy drawn for domestic hot water preparation.",
        )
        self.water_output_temperature_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterOutputDhwTemperature,
            load_type=LoadTypes.WATER,
            unit=Units.CELSIUS,
            output_description=(
                "Temperature of the delivered domestic hot water. Equals the set temperature "
                "unless the rated power is insufficient."
            ),
        )
        self.water_output_mass_flow_rate_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterOutputDhwMassFlowRate,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow rate of the domestic hot water draw.",
        )
        self.unmet_thermal_demand_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.UnmetThermalDemandPower,
            load_type=LoadTypes.WARM_WATER,
            unit=Units.WATT,
            output_description=(
                "Thermal power the draw would have needed beyond the rated electric power. "
                "Non-zero means the device is undersized for that timestep."
            ),
        )

        self.add_default_connections(self.get_default_connections_from_utsp())

    def get_default_connections_from_utsp(self) -> List[ComponentConnection]:
        """Get the water consumption default connection from the LPG/UTSP connector."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module = importlib.import_module(name="hisim.components.loadprofilegenerator_utsp_connector")
        component_class = getattr(component_module, "UtspLpgConnector")
        return [
            ComponentConnection(
                TanklessWaterHeater.WaterConsumption,
                component_class.get_classname(),
                component_class.WaterConsumption,
            )
        ]

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.config.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state. The component is stateless."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state. The component is stateless."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the tankless water heater.

        The outputs are a pure function of the water draw, so the result is recomputed
        on every iteration and converges immediately.
        """
        water_consumption_in_liter = stsv.get_input_value(self.water_consumption_channel)
        cold_water_temperature_in_celsius = self._get_cold_water_temperature_in_celsius(stsv)

        (
            thermal_power_delivered_in_watt,
            electric_power_in_watt,
            water_output_temperature_in_celsius,
            water_mass_flow_rate_in_kg_per_s,
            unmet_thermal_power_in_watt,
        ) = self._calculate_outputs(
            water_consumption_in_liter=water_consumption_in_liter,
            cold_water_temperature_in_celsius=cold_water_temperature_in_celsius,
        )

        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        stsv.set_output_value(self.thermal_output_power_channel, thermal_power_delivered_in_watt)
        stsv.set_output_value(
            self.thermal_output_energy_channel,
            thermal_power_delivered_in_watt * seconds_per_timestep / 3.6e3,
        )
        stsv.set_output_value(self.electric_output_power_channel, electric_power_in_watt)
        stsv.set_output_value(
            self.electric_output_energy_channel,
            electric_power_in_watt * seconds_per_timestep / 3.6e3,
        )
        stsv.set_output_value(self.water_output_temperature_channel, water_output_temperature_in_celsius)
        stsv.set_output_value(self.water_output_mass_flow_rate_channel, water_mass_flow_rate_in_kg_per_s)
        stsv.set_output_value(self.unmet_thermal_demand_power_channel, unmet_thermal_power_in_watt)

    def _get_cold_water_temperature_in_celsius(self, stsv: SingleTimeStepValues) -> float:
        """Read the cold water temperature, falling back to the configured constant."""
        if self.cold_water_temperature_channel.source_output is None:
            return self.config.cold_water_temperature_in_celsius
        return stsv.get_input_value(self.cold_water_temperature_channel)

    def _calculate_outputs(
        self,
        water_consumption_in_liter: float,
        cold_water_temperature_in_celsius: float,
    ) -> Tuple[float, float, float, float, float]:
        """Calculate the thermal and electric outputs for one timestep.

        Returns the delivered thermal power, the electric power drawn, the outlet
        temperature, the mass flow rate and the thermal power that could not be
        delivered because of the rated power limit.
        """
        if water_consumption_in_liter <= 0:
            # No draw: the device is idle. A Durchlauferhitzer has no standby loss,
            # so the outlet simply sits at the cold water temperature.
            return 0.0, 0.0, cold_water_temperature_in_celsius, 0.0, 0.0

        water_mass_flow_rate_in_kg_per_s = (
            water_consumption_in_liter
            * DENSITY_OF_WATER_AT_40_DEGREE_CELSIUS_IN_KG_PER_LITER
            / self.my_simulation_parameters.seconds_per_timestep
        )
        heat_capacity_flow_in_watt_per_kelvin = (
            water_mass_flow_rate_in_kg_per_s * self.specific_heat_capacity_of_water_in_joule_per_kg_per_kelvin
        )
        delta_temperature_needed_in_kelvin = (
            self.config.set_water_temperature_in_celsius - cold_water_temperature_in_celsius
        )
        if delta_temperature_needed_in_kelvin <= 0:
            # The mains water is already at or above the set temperature, so the device
            # stays off and passes the water through unchanged.
            return 0.0, 0.0, cold_water_temperature_in_celsius, water_mass_flow_rate_in_kg_per_s, 0.0

        thermal_power_needed_in_watt = heat_capacity_flow_in_watt_per_kelvin * delta_temperature_needed_in_kelvin
        electric_power_in_watt = min(
            thermal_power_needed_in_watt / self.config.efficiency,
            self.config.maximum_electric_power_in_watt,
        )
        thermal_power_delivered_in_watt = electric_power_in_watt * self.config.efficiency
        unmet_thermal_power_in_watt = thermal_power_needed_in_watt - thermal_power_delivered_in_watt

        water_output_temperature_in_celsius = (
            cold_water_temperature_in_celsius
            + thermal_power_delivered_in_watt / heat_capacity_flow_in_watt_per_kelvin
        )

        return (
            thermal_power_delivered_in_watt,
            electric_power_in_watt,
            water_output_temperature_in_celsius,
            water_mass_flow_rate_in_kg_per_s,
            unmet_thermal_power_in_watt,
        )

    def _sum_output_in_kwh(self, all_outputs: List, postprocessing_results: pd.DataFrame, field_name: str) -> float:
        """Sum a Watt output over the simulated period and convert it to kWh."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == field_name
                and output.unit == Units.WATT
            ):
                return round(
                    float(sum(postprocessing_results.iloc[:, index]))
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
        raise ValueError(f"Could not find {field_name} output for component {self.component_name}")

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and maintenance."""
        dhw_consumption_in_kwh = self._sum_output_in_kwh(
            all_outputs, postprocessing_results, self.ElectricOutputDhwPower
        )

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year, self.my_simulation_parameters.country
        )
        co2_per_unit = emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.electricity_costs_in_euro_per_kwh

        return OpexCostDataClass(
            opex_energy_cost_in_euro=dhw_consumption_in_kwh * euro_per_unit,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=dhw_consumption_in_kwh * co2_per_unit,
            total_consumption_in_kwh=dhw_consumption_in_kwh,
            # A tankless water heater never serves space heating.
            consumption_for_space_heating_in_kwh=0.0,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.TANKLESS_WATER_HEATER,
        )

    @staticmethod
    def get_cost_capex(
        config: TanklessWaterHeaterConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Return investment cost, CO2 emissions and lifetime."""
        # The ELECTRIC_HEATER device factors explicitly include a Durchlauferhitzer,
        # see EmissionFactorsAndCostsForDevicesConfig in components/configuration.py.
        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=ComponentType.ELECTRIC_HEATER,
            unit=Units.KILOWATT,
            size_of_energy_system=config.maximum_electric_power_in_watt * 1e-3,
            config=config,
            kpi_tag=KpiTagEnumClass.TANKLESS_WATER_HEATER,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculate KPIs for the component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(self.config, self.my_simulation_parameters)
        kpi_tag = opex_dataclass.kpi_tag

        def add(name: str, unit: str, value) -> None:
            list_of_kpi_entries.append(
                KpiEntry(name=name, unit=unit, value=value, tag=kpi_tag, description=self.component_name)
            )

        # Energy related KPIs
        add("Total energy consumption", "kWh", opex_dataclass.total_consumption_in_kwh)
        add(
            "Energy consumption for domestic hot water",
            "kWh",
            opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
        )
        add(
            "Thermal energy delivered for domestic hot water",
            "kWh",
            self._sum_output_in_kwh(all_outputs, postprocessing_results, self.ThermalOutputDhwPower),
        )

        # Sizing related KPIs: the peak draw drives the required house connection, and
        # unmet demand shows whether the rated power is sufficient.
        add(
            "Maximum electric power",
            "kW",
            self._maximum_electric_power_in_kilowatt(all_outputs, postprocessing_results),
        )
        add(
            "Unmet thermal energy for domestic hot water",
            "kWh",
            self._sum_output_in_kwh(all_outputs, postprocessing_results, self.UnmetThermalDemandPower),
        )
        add(
            "Share of timesteps with unmet domestic hot water demand",
            "%",
            self._share_of_timesteps_with_unmet_demand_in_percent(all_outputs, postprocessing_results),
        )

        # Economic and environmental KPIs
        add("CAPEX - Investment cost", "EUR", capex_dataclass.capex_investment_cost_in_euro)
        add("CAPEX - CO2 Footprint", "kg", capex_dataclass.device_co2_footprint_in_kg)
        add("OPEX - Energy costs", "EUR", opex_dataclass.opex_energy_cost_in_euro)
        add("OPEX - Maintenance costs", "EUR", opex_dataclass.opex_maintenance_cost_in_euro)
        add("OPEX - CO2 Footprint", "kg", opex_dataclass.co2_footprint_in_kg)
        add(
            "Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            "EUR",
            capex_dataclass.capex_investment_cost_for_simulated_period_in_euro
            + opex_dataclass.opex_energy_cost_in_euro
            + opex_dataclass.opex_maintenance_cost_in_euro,
        )
        add(
            "Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            "kg",
            capex_dataclass.device_co2_footprint_for_simulated_period_in_kg + opex_dataclass.co2_footprint_in_kg,
        )
        return list_of_kpi_entries

    def _column_for_output(self, all_outputs: List, postprocessing_results: pd.DataFrame, field_name: str):
        """Return the results column belonging to a Watt output of this component."""
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == field_name
                and output.unit == Units.WATT
            ):
                return postprocessing_results.iloc[:, index]
        raise ValueError(f"Could not find {field_name} output for component {self.component_name}")

    def _maximum_electric_power_in_kilowatt(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> float:
        """Return the peak electric power drawn over the simulated period."""
        column = self._column_for_output(all_outputs, postprocessing_results, self.ElectricOutputDhwPower)
        return round(float(max(column)) * 1e-3, 2)

    def _share_of_timesteps_with_unmet_demand_in_percent(
        self, all_outputs: List, postprocessing_results: pd.DataFrame
    ) -> float:
        """Return the share of timesteps in which the rated power was insufficient."""
        column = self._column_for_output(all_outputs, postprocessing_results, self.UnmetThermalDemandPower)
        if len(column) == 0:
            return 0.0
        return round(float(sum(value > 0 for value in column)) / len(column) * 100, 2)
