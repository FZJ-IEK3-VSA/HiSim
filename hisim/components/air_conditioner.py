"""Air Conditioner Component."""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional
from dataclasses_json import dataclass_json

import numpy as np
import pandas as pd
from hisim import log

from hisim import component as cp
from hisim.component import (
    CapexCostDataClass,
    OpexCostDataClass,
)
from hisim.config import ConfigBase, ComponentID, DisplayConfig, constructor, preset
from hisim.components.configuration import (
    EmissionFactorsAndCostsForFuelsConfig,
)
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.cost_and_emission_computation.capex_computation import prorate_to_simulated_period
from hisim.loadtypes import InandOutputType, LoadTypes, Units
from hisim.components.weather import Weather
from hisim.components.building import Building
from hisim import utils
from hisim.economics.facts import CostRelevance


def get_price_in_euro(air_conditioner: Dict[str, Any]) -> float:
    """Return the purchase price of a smart-devices air conditioner entry in euro.

    Prices in the smart-devices database are given either as a plain number or as a
    string carrying the currency, e.g. "3499.00 EUR".
    """

    if "Price" not in air_conditioner:
        raise ValueError(
            f"Air conditioner {air_conditioner['Manufacturer']}/{air_conditioner['Model']} "
            "has no price in the smart devices database, so its investment costs are unknown."
        )
    price = air_conditioner["Price"]
    if isinstance(price, (int, float)):
        return float(price)

    amount, _, currency = str(price).partition(" ")
    if currency not in ("", "EUR"):
        raise ValueError(
            f"Price of air conditioner {air_conditioner['Manufacturer']}/{air_conditioner['Model']} "
            f"is given in {currency}, but only EUR is supported."
        )
    return float(amount)


def get_installation_cost_in_euro(air_conditioner: Dict[str, Any]) -> float:
    """Return the installation costs of a smart-devices air conditioner entry in euro.

    Based on https://www.obi.de/magazin/bauen/haustechnik/klimaanlage/klima-splitgeraet#Kosten
    """

    ac_type = air_conditioner["Type"].replace("-", " ").lower()
    if "single split" in ac_type:
        return 1400
    if "duo" in ac_type:
        return 2350
    if "ducted" in ac_type or "triple" in ac_type:
        return 3300
    raise ValueError(f"No installation cost information for type {air_conditioner['Type']}")


@dataclass_json
@dataclass
class AirConditionerConfig(ConfigBase):
    """Configuration of the air conditioner: one unit out of the bundled smart-devices catalogue.

    The component models no machine of its own. It interpolates the efficiency and capacity
    curves of a real catalogue unit over the outdoor temperature and multiplies the result by
    ``scale_factor``, so this configuration is a copy of one catalogue row — three outdoor
    temperatures and the EER, COP, cooling and heating capacity measured at each — plus that
    factor and the unit's costs. The row is read when the configuration is built, never during
    the simulation, which is why a scenario file carries the twelve reference numbers rather
    than the catalogue key alone.

    The named default is :meth:`preset_samsung_ac120`, the ducted split unit the fleet uses.
    Any other row is reached by naming it, and a unit picked to match a building's heating
    load by :meth:`for_building_load`::

        AirConditionerConfig.for_device("AirConditioner", "Daikin", "FTXZ35N/RXZ35N")
        AirConditionerConfig.for_building_load("AirConditioner", 11430.0, -7.0)
    """

    MAIN_CLASS = "hisim.components.air_conditioner.AirConditioner"

    #: Key under which the smart-devices database lists the air conditioners.
    CATALOGUE_NAME: ClassVar[str] = "Air Conditioner"

    #: Share of the investment costs spent on maintenance each year.
    MAINTENANCE_COST_AS_PERCENTAGE_OF_INVESTMENT_PER_YEAR: ClassVar[float] = 0.05

    #: Service life of an air conditioner, in years. 10 years
    #: https://www.deutschlandfunk.de/belastung-fuer-die-atmosphaere-der-vormarsch-der-100.html,
    #: 10-15 years https://klivago.de/faq-was-man-ueber-eine-klimaanlage-wissen-sollte,
    #: 15 years https://volted.ch/blogs/guides-fokus-und-bericht/wie-lange-halten-tragbare-
    #: klimaanlagen?srsltid=AfmBOoojFnnbhbCYtAGCZLzdawl4C8zeNvRtc9GFeCICEwaWB6ZdKhSt
    LIFETIME_IN_YEARS: ClassVar[int] = 12

    #: Manufacturing emissions of one unit, in kg CO2 equivalent; in a first step the same
    #: figure as for a heat pump.
    CO2_EMISSIONS_IN_KG_CO2_EQ: ClassVar[float] = 165.84

    #: Share of the building's design heating load that :meth:`for_building_load` sizes the
    #: unit's heating capacity to, at the building's heating reference temperature. Chosen by
    #: measurement on the air-conditioned house (Seville, full year at 60 s): at 0.8 the unit
    #: runs at full power for 4 h a year in heating and 22 h in cooling, and the selected unit
    #: stays within its catalogue rating (scale factor 0.95); 0.6 leaves 121 h and 197 h at full
    #: power, while 1.0 and 1.2 gain little comfort and run the unit beyond its rating.
    CAPACITY_SHARE_FOR_BUILDING_LOAD: ClassVar[float] = 0.8

    component_id: ComponentID
    #: Manufacturer as the smart-devices database spells it, the first half of the catalogue key.
    manufacturer: str
    #: Model name as the database spells it, the second half of that key.
    model_name: str
    #: Multiplier on every capacity and power the unit delivers; 1.0 runs it at its rating.
    scale_factor: float
    #: Outdoor temperatures, in °C, at which the cooling figures below were measured.
    t_out_cooling_ref: List[float]
    #: Outdoor temperatures, in °C, at which the heating figures below were measured.
    t_out_heating_ref: List[float]
    #: Energy efficiency ratio in cooling, W/W, one per cooling reference temperature.
    eer_ref: List[float]
    #: Coefficient of performance in heating, W/W, one per heating reference temperature.
    cop_ref: List[float]
    #: Cooling capacity in W, one per cooling reference temperature.
    cooling_capacity_ref: List[float]
    #: Heating capacity in W, one per heating reference temperature.
    heating_capacity_ref: List[float]
    #: Purchase price plus installation costs of the unit, in euro.
    investment_costs_in_euro: float
    #: Service life of the unit, in years.
    lifetime_in_years: int
    #: Manufacturing emissions of the unit, in kg CO2 equivalent.
    co2_emissions_kg_co2_eq: float
    #: Yearly maintenance costs, in euro.
    maintenance_costs_in_euro_per_year: float

    @preset(note='smart-devices catalogue, Samsung "AC120HBHFKH/SA - AC120HCAFKH/SA"')
    @classmethod
    def preset_samsung_ac120(cls, name: str) -> "AirConditionerConfig":
        """The fleet's air conditioner, Samsung's ducted split AC120HBHFKH/SA - AC120HCAFKH/SA.

        The catalogue row is read here, so the twelve reference values, the costs, the service
        life and the CO2 figure are whatever the bundled database says about that unit today.
        The unit runs at its rating: ``scale_factor`` is 1.0, since a preset knows no building
        to size against — :meth:`for_building_load` is the builder that does.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            AirConditionerConfig: The preset configuration.
        """
        return cls.for_device(name, manufacturer="Samsung", model_name="AC120HBHFKH/SA - AC120HCAFKH/SA")

    @constructor
    @classmethod
    def for_device(
        cls,
        name: str,
        manufacturer: str,
        model_name: str,
        scale_factor: float = 1.0,
    ) -> "AirConditionerConfig":
        """Builds the configuration of one named unit of the bundled smart-devices catalogue.

        The catalogue has more units than the one the preset pins and a preset name is wire
        format forever, so the others are reached by naming them. The row supplies the twelve
        reference values and the purchase price; the investment costs add the installation
        costs the unit's type implies, and the maintenance costs are
        :attr:`MAINTENANCE_COST_AS_PERCENTAGE_OF_INVESTMENT_PER_YEAR` of the investment. Service
        life and CO2 figure are the class constants, which do not vary by unit.

        Unlike a catalogue key that the component resolves later, this pair is checked here: the
        database is read while the configuration is built, so a misspelling fails at build time.

        Args:
            name: Instance name of the air conditioner; its ``ComponentID`` is built from it.
            manufacturer: Manufacturer as the database spells it, e.g. ``"Panasonic"``.
            model_name: Model name as the database spells it, e.g.
                ``"CS-TZ71WKEW + CU-TZ71WKE"``.
            scale_factor: Multiplier on every capacity and power the unit delivers. 1.0, the
                default, runs it at its catalogue rating.

        Returns:
            A fresh configuration of that unit; nothing about it is shared with any other
            instance.

        Raises:
            ValueError: If no catalogue row has that manufacturer and model name, if the row
                carries no price or a price in another currency, or if its type implies no
                installation costs.
        """
        air_conditioners = utils.load_smart_appliance(cls.CATALOGUE_NAME)
        air_conditioner = next(
            (
                candidate
                for candidate in air_conditioners
                if candidate["Manufacturer"] == manufacturer and candidate["Model"] == model_name
            ),
            None,
        )
        if air_conditioner is None:
            known = ", ".join(f"{entry['Manufacturer']} / {entry['Model']}" for entry in air_conditioners)
            raise ValueError(
                f"Air conditioner model {manufacturer}/{model_name} not found in database. "
                f"The smart devices database holds: {known}."
            )

        investment_costs_in_euro = get_price_in_euro(air_conditioner) + get_installation_cost_in_euro(air_conditioner)

        return cls(
            component_id=ComponentID(name=name),
            manufacturer=manufacturer,
            model_name=model_name,
            scale_factor=scale_factor,
            t_out_cooling_ref=air_conditioner["Outdoor temperature range - cooling"],
            t_out_heating_ref=air_conditioner["Outdoor temperature range - heating"],
            eer_ref=air_conditioner["EER W/W"],
            cop_ref=air_conditioner["COP W/W"],
            cooling_capacity_ref=air_conditioner["Cooling capacity W"],
            heating_capacity_ref=air_conditioner["Heating capacity W"],
            investment_costs_in_euro=investment_costs_in_euro,
            lifetime_in_years=cls.LIFETIME_IN_YEARS,
            co2_emissions_kg_co2_eq=cls.CO2_EMISSIONS_IN_KG_CO2_EQ,
            maintenance_costs_in_euro_per_year=(
                cls.MAINTENANCE_COST_AS_PERCENTAGE_OF_INVESTMENT_PER_YEAR * investment_costs_in_euro
            ),
        )

    @constructor(
        note=(
            "selects the device whose heating capacity at the reference temperature is closest "
            "to the load, then scales it so that its heating capacity there is "
            "CAPACITY_SHARE_FOR_BUILDING_LOAD × load"
        )
    )
    @classmethod
    def for_building_load(
        cls,
        name: str,
        heating_load_in_watt: float,
        heating_reference_temperature_in_celsius: float,
    ) -> "AirConditionerConfig":
        """Picks the catalogue unit that fits a building's heating load and scales it to a share of that load.

        Two steps:

        1. **Selection.** Every catalogue row is measured at three outdoor temperatures; the
           one closest to ``heating_reference_temperature_in_celsius`` gives the row's heating
           capacity at the building's design condition. The unit whose capacity is nearest the
           building's heating load wins; ties go to the unit the database lists first.
        2. **Scaling.** ``scale_factor`` becomes
           :attr:`CAPACITY_SHARE_FOR_BUILDING_LOAD` × load / capacity, where the capacity is
           the straight-line fit through the row's three heating measurements at the reference
           temperature -- the same fit the component interpolates with. The unit the
           simulation runs therefore delivers exactly that share of the design load at the
           reference temperature, and a building with a larger load gets a proportionally
           larger unit. (Until 2026-10 the factor was the inverse, share × capacity / load,
           which gave a larger load a smaller unit; bead hisim-h84t.)

        The investment costs, maintenance and CO2 figure are those of the selected catalogue
        unit and do not change with ``scale_factor``.

        This is a constructor rather than a sizing law because one lookup answers twelve fields
        at once: the selected row's reference values and costs are written into the
        configuration here, and nothing downstream can tell which building they came from.

        For a building whose design heating load is 11 430 W at -7 °C::

            AirConditionerConfig.for_building_load("AirConditioner", 11430.0, -7.0)

        Args:
            name: Instance name of the air conditioner; its ``ComponentID`` is built from it.
            heating_load_in_watt: The building's design heating load, in W.
            heating_reference_temperature_in_celsius: The outdoor temperature that load is
                defined at, in °C.

        Returns:
            A fresh configuration of the selected unit, scaled to the load.
        """
        air_conditioners = utils.load_smart_appliance(cls.CATALOGUE_NAME)
        capacities = [
            cls._heating_capacity_at_temperature(
                candidate["Outdoor temperature range - heating"],
                candidate["Heating capacity W"],
                heating_reference_temperature_in_celsius,
            )
            for candidate in air_conditioners
        ]
        selected = min(range(len(air_conditioners)), key=lambda index: abs(capacities[index] - heating_load_in_watt))
        rated_heating_capacity_in_watt = cls._fitted_heating_capacity_in_watt(
            air_conditioners[selected]["Outdoor temperature range - heating"],
            air_conditioners[selected]["Heating capacity W"],
            heating_reference_temperature_in_celsius,
        )

        return cls.for_device(
            name,
            manufacturer=air_conditioners[selected]["Manufacturer"],
            model_name=air_conditioners[selected]["Model"],
            scale_factor=cls.CAPACITY_SHARE_FOR_BUILDING_LOAD * heating_load_in_watt / rated_heating_capacity_in_watt,
        )

    @staticmethod
    def _fitted_heating_capacity_in_watt(
        temperature_range_in_celsius: List[float],
        heating_capacity_in_watt: List[float],
        outdoor_temperature_in_celsius: float,
    ) -> float:
        """Returns a catalogue unit's heating capacity at an outdoor temperature, as the component computes it.

        The component (:meth:`AirConditioner.build`) fits a straight line through the row's
        three heating measurements and reads the capacity off it; this does the same, so the
        sizing and the simulation agree on what the unit delivers at a given outdoor
        temperature before ``scale_factor`` is applied.

        Args:
            temperature_range_in_celsius: The outdoor temperatures the row was measured at, in °C.
            heating_capacity_in_watt: The heating capacities, in W, in the same order.
            outdoor_temperature_in_celsius: The outdoor temperature to evaluate, in °C.

        Returns:
            The fitted heating capacity at that temperature, in W, at a scale factor of 1.
        """
        line_coefficients = np.polyfit(temperature_range_in_celsius, heating_capacity_in_watt, 1)
        return float(np.polyval(line_coefficients, outdoor_temperature_in_celsius))

    @staticmethod
    def _heating_capacity_at_temperature(
        temperature_range: List[float],
        capacity: List[float],
        temperature: float,
    ) -> float:
        """Returns the heating capacity measured closest to the given outdoor temperature.

        A catalogue row states its heating capacity at three outdoor temperatures and nothing
        in between; this picks the measurement nearest ``temperature`` rather than
        interpolating, because the selection only has to rank units against one another.

        Args:
            temperature_range: The outdoor temperatures the row was measured at, in °C.
            capacity: The heating capacities, in W, in the same order.
            temperature: The outdoor temperature to look up, in °C.

        Returns:
            The capacity, in W, at the nearest measured temperature; ties go to the first.
        """
        closest = min(range(len(temperature_range)), key=lambda index: abs(temperature_range[index] - temperature))
        return capacity[closest]


class AirConditioner(cp.PhysicsComponent):
    """Simulates an air conditioner that provides heating and cooling based on a modulating signal."""

    cost_relevance = CostRelevance.PRICED

    # Input and output channel names
    OperatingState: ClassVar[str] = "State"
    ModulatingPowerSignal: ClassVar[str] = "ModulatingPowerSignal"
    OutdoorAirTemperature: ClassVar[str] = "TemperatureOutside"
    GridImport: ClassVar[str] = "GridImport"
    PV2load: ClassVar[str] = "PV2load"
    Battery2Load: ClassVar[str] = "Battery2Load"
    ThermalPowerDelivered: ClassVar[str] = "ThermalPowerDelivered"
    ThermalEnergyDelivered: ClassVar[str] = "ThermalEnergyDelivered"
    ElectricalPowerConsumption: ClassVar[str] = "ElectricalPowerConsumption"
    ElectricalEnergyConsumption: ClassVar[str] = "ElectricalEnergyConsumption"
    Efficiency: ClassVar[str] = "EnergyEfficiencyRatio"
    CoefficientOfPerformance: ClassVar[str] = "CoefficientOfPerformance"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
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
            # Without this flag the KPI layer never counts the air conditioner: it gathers total
            # electricity consumption from the postprocessing flags, not from the load type, so an
            # untagged consumer makes grid import exceed total consumption and the KPI run refuses.
            postprocessing_flag=[InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
            output_description="Electrical power consumption",
        )
        self.electrical_energy_consumption_channel = self.add_output(
            self.component_name,
            self.ElectricalEnergyConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT_HOUR,
            output_description="Electrical energy consumption",
        )
        self.eer_channel = self.add_output(
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

    def get_default_connections_from_weather(self) -> List[cp.ComponentConnection]:
        """Connect to default weather component for outside temperature."""
        return [
            cp.ComponentConnection(
                self.OutdoorAirTemperature,
                Weather.get_classname(),
                Weather.TemperatureOutside,
            )
        ]

    def get_default_connections_from_controller(self) -> List[cp.ComponentConnection]:
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
        prorated = prorate_to_simulated_period(
            investment_in_euro=config.investment_costs_in_euro,
            co2_footprint_in_kg=config.co2_emissions_kg_co2_eq,
            maintenance_in_euro_per_year=config.maintenance_costs_in_euro_per_year,
            lifetime_in_years=config.lifetime_in_years,
            simulation_parameters=simulation_parameters,
        )

        return CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.co2_emissions_kg_co2_eq,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=prorated.investment_for_simulated_period_in_euro,
            device_co2_footprint_for_simulated_period_in_kg=prorated.co2_footprint_for_simulated_period_in_kg,
            maintenance_costs_in_euro_per_year=config.maintenance_costs_in_euro_per_year,
            maintenance_cost_per_simulated_period_in_euro=prorated.maintenance_for_simulated_period_in_euro,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER,
        )

    def get_cost_opex(
        self, all_outputs: List[cp.ComponentOutput], postprocessing_results: pd.DataFrame
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
                self.my_simulation_parameters.year, self.my_simulation_parameters.country
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

    def build(self) -> None:
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

    def write_to_report(self) -> List[str]:
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
        stsv.set_output_value(self.eer_channel, efficiency)
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
        all_outputs: List[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        assert isinstance(self.config, AirConditionerConfig)
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
    """Configuration of the air conditioner's controller: two set points, a hysteresis band and run times.

    The controller holds the indoor air at :attr:`heating_set_temperature_deg_c` while it heats
    and at :attr:`cooling_set_temperature_deg_c` while it cools. The unit switches on when the
    air leaves the comfort band between the two set points, and switches off only once the air
    is :attr:`hysteresis_in_kelvin` back inside it: heating stops above 20 + 1 = 21 °C, cooling
    below 24 - 1 = 23 °C. While it runs, the controller ramps the unit's power by the distance
    from the set point it holds, so the air settles on that set point instead of drooping
    below it; :attr:`ramp_time_to_full_power_at_one_kelvin_s` says how fast. The two minimum
    times keep the unit running, or keep it off, for a while whatever the air says, so it
    does not chatter on and off.

    The named default is :meth:`preset_standard`::

        AirConditionerControllerConfig.preset_standard("AirConditionerController")

    Nothing here is derived from the building. These are the temperatures the residents ask
    for and the cycling limits of the machine, so no field is sizable and the preset takes
    nothing but the instance name.
    """

    MAIN_CLASS = "hisim.components.air_conditioner.AirConditionerController"

    component_id: ComponentID
    #: Indoor air temperature the unit holds while heating, in °C. Below it an idle unit
    #: starts heating.
    heating_set_temperature_deg_c: float = 20.0
    #: Indoor air temperature the unit holds while cooling, in °C. Above it an idle unit
    #: starts cooling.
    cooling_set_temperature_deg_c: float = 24.0
    #: Shortest time the unit stays in heating or cooling once it has started, in seconds.
    #: Rounded down to whole time steps, so a value below one time step imposes nothing. 0
    #: means no minimum; a negative or non-finite time is refused.
    minimum_runtime_s: float = 1800.0
    #: Shortest time the unit stays off once it has stopped, in seconds, rounded down the
    #: same way. Together with the runtime it is what stops the unit chattering on and off
    #: around the set point.
    minimum_idle_time_s: float = 900.0
    #: Width of the hysteresis band, in kelvin: how far back inside the comfort band the air
    #: must be before a running unit stops. Heating stops above
    #: ``heating_set_temperature_deg_c + hysteresis_in_kelvin`` and cooling below
    #: ``cooling_set_temperature_deg_c - hysteresis_in_kelvin``.
    hysteresis_in_kelvin: float = 1.0
    #: How fast the unit's power follows the air, in seconds: the time the controller takes
    #: to ramp the power from zero to full while the air stays one kelvin on the wrong side of
    #: the set point it holds. Two kelvin ramp twice as fast; on the right side the power
    #: ramps down the same way.
    ramp_time_to_full_power_at_one_kelvin_s: float = 1800.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "AirConditionerControllerConfig":
        """The one comfort band the fleet runs: hold 20 °C while heating, 24 °C while cooling.

        The field defaults are that band, with one kelvin of hysteresis (heating stops above
        21 °C, cooling below 23 °C), half an hour from zero to full power per kelvin of
        deviation, and the unit held for half an hour once it starts and a quarter of an hour
        once it stops.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class AirConditionerControllerMode(str, Enum):
    """What the controller asks of the air conditioner: heat, cool or stay off.

    A ``str`` enum so that the values read as the plain words in logs and compare equal to
    them.
    """

    HEATING = "heating"
    COOLING = "cooling"
    OFF = "off"


class AirConditionerControllerState:
    """What the controller remembers from one time step to the next.

    The controller commits this state at the end of every time step (``i_save_state``) and
    every pass of the next step starts again from that committed copy (``i_restore_state``),
    so nothing a pass changes here can leak into the next pass of the same step.

    Attributes:
        mode: Whether the unit heats, cools or is off.
        activation_time_step: The time step the unit last started heating or cooling.
        deactivation_time_step: The time step the unit last stopped.
        power_fraction: Share of the unit's capacity it runs at, from 0 to 1; 0 when off.
        indoor_air_temperature_deg_c: The indoor air temperature the controller read at the
            end of the step, in °C, or ``None`` before the first step. The next step decides
            on it.
    """

    def __init__(
        self,
        mode: AirConditionerControllerMode,
        activation_time_step: int,
        deactivation_time_step: int,
        power_fraction: float,
        indoor_air_temperature_deg_c: Optional[float] = None,
    ) -> None:
        """Builds the state from its five values.

        Args:
            mode: Whether the unit heats, cools or is off.
            activation_time_step: The time step the unit last started.
            deactivation_time_step: The time step the unit last stopped.
            power_fraction: Share of the unit's capacity it runs at, from 0 to 1.
            indoor_air_temperature_deg_c: The indoor air temperature last read, in °C, or
                ``None`` if none has been read yet.
        """
        self.mode = mode
        self.activation_time_step = activation_time_step
        self.deactivation_time_step = deactivation_time_step
        self.power_fraction = power_fraction
        self.indoor_air_temperature_deg_c = indoor_air_temperature_deg_c

    def clone(self) -> "AirConditionerControllerState":
        """Returns an independent copy of this state.

        Returns:
            A new state with the same five values.
        """
        return AirConditionerControllerState(
            self.mode,
            self.activation_time_step,
            self.deactivation_time_step,
            self.power_fraction,
            self.indoor_air_temperature_deg_c,
        )


class AirConditionerController(cp.L1ControllerComponent):
    """Controller that holds the indoor air at a heating or a cooling set point with an air conditioner.

    Like a digital room thermostat, the controller samples the indoor air temperature once per
    time step and holds its signal for the next step: every step it decides on the temperature
    the building reported at the end of the previous step, together with what it committed
    then. Each pass of a step therefore sends the same signal, the controller needs no extra
    convergence passes, and the result does not depend on how the step's iteration starts.

    It decides in two parts:

    * **Mode, with hysteresis and minimum times.** An idle unit starts heating below the
      heating set point (20 °C) or cooling above the cooling set point (24 °C), once it has
      been off for the minimum idle time. A heating unit stops once the air is above the
      heating set point plus the hysteresis (21 °C), a cooling unit once it is below the
      cooling set point minus the hysteresis (23 °C), and neither stops before the minimum
      runtime. It also stops only from its lowest power, :attr:`MINIMUM_POWER_FRACTION`:
      while the air is back inside the band but the power is still higher, the power keeps
      ramping down instead (a soft stop). Stopping from high power would drop the air by the
      unit's whole same-step effect, up to 4 K, and the minimum idle time would then hold it
      there. The soft stop always ends: back inside the band the deviation from the held set
      point is at least the hysteresis, so each step lowers the power by at least
      ``hysteresis * dt / t_ramp`` (1/30 at 60 s with the preset), and from full power the
      minimum is reached within ``0.9 * t_ramp`` (27 minutes) of the air staying there. If
      the air falls back below the hysteresis edge first, the unit is simply regulating
      again. A hysteresis of zero would take that bound away and is refused.
    * **Power, ramped by the distance from the set point it holds.** While running, the
      power fraction changes each step by ``(T_set - T) * dt / t_ramp`` when heating and by
      ``(T - T_set) * dt / t_ramp`` when cooling, with ``T`` the sampled indoor air
      temperature, ``dt`` the step length and ``t_ramp``
      :attr:`AirConditionerControllerConfig.ramp_time_to_full_power_at_one_kelvin_s`, and
      stays between :attr:`MINIMUM_POWER_FRACTION` and 1. Below the set point the power
      rises, above it the power falls, so the air settles on the set point whatever the
      load, rather than at a load-dependent distance below it as with a power proportional
      to the deviation.

    Why the power ramps instead of following the deviation directly: the building's indoor air
    node answers the unit's power within the same time step (about 0.6 K per kW in the
    air-conditioned house, some 4.7 K at full power for a unit sized to 0.8 of its heating
    load). A power proportional to the deviation that is steep enough to hold the set point
    within half a kelvin would overshoot that answer every step and oscillate; one flat enough
    to be stable leaves the air kelvins below the set point. The ramp settles in about
    ``t_ramp / (4.7 K)``, some six minutes with the preset.

    For the same reason a step longer than :attr:`LONGEST_RAMPED_STEP_S` (7.5 minutes) ramps
    only as far as a 7.5-minute step would. One step's ramp, times that same-step answer, must
    stay below 2 K per kelvin of deviation, or every step overshoots further than the last:
    4.7 K × 450 s / 1800 s = 1.2 is stable, while the 900 s cap the controller first had gave
    2.35, and the air-conditioned house at 15-minute steps chattered (2,814 heating starts a
    year).

    The output ``ModulatingPowerSignal`` is the power fraction with a sign: positive for
    heating, negative for cooling, 0 when off.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    TemperatureIndoorAir: ClassVar[str] = "TemperatureIndoorAir"
    ElectricityInput: ClassVar[str] = "ElectricityInput"
    OperatingState: ClassVar[str] = "OperatingState"
    ModulatingPowerSignal: ClassVar[str] = "ModulatingPowerSignal"

    #: Lowest share of its capacity the unit runs at while heating or cooling. Inverter units
    #: cannot modulate down to zero; below this the unit cycles on and off instead.
    MINIMUM_POWER_FRACTION: ClassVar[float] = 0.1
    #: Longest step length, in seconds, whose full length the power ramp follows. A longer
    #: step changes the power only as much as a step of this length would, which keeps the
    #: loop stable at 15-minute and hourly steps (see the class docstring for the 2 K bound).
    LONGEST_RAMPED_STEP_S: ClassVar[float] = 450.0

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: AirConditionerControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Builds the controller, converts its minimum times to time steps and starts it off.

        The unit starts off and free to start at once; the first step only samples the air,
        so the earliest start is the second step.

        Args:
            my_simulation_parameters: The simulation's parameters; their step length turns the
                minimum times into whole time steps and scales the power ramp.
            config: The controller's configuration.
            my_display_config: How the controller shows in reports.

        Raises:
            ValueError: If the hysteresis is not positive; the soft stop needs it to end (see the
                class docstring). If the ramp time is not a finite number of seconds above 0; the
                power changes by the step length over it, so 0 divides by zero and a negative time
                lowers the power of a unit whose room is too cold. If the heating set point is not
                below the cooling set point; an idle unit would then heat a room that is warm enough. If
                the minimum runtime or idle time is negative or not finite; a negative time would silently
                mean no minimum.
        """
        if config.hysteresis_in_kelvin <= 0.0:
            raise ValueError(
                f"The air conditioner's controller needs a positive hysteresis, got {config.hysteresis_in_kelvin} K: "
                "a running unit ramps down to its minimum power before it stops, and only a positive "
                "hysteresis bounds how long that takes."
            )
        ramp_time_s = config.ramp_time_to_full_power_at_one_kelvin_s
        if not math.isfinite(ramp_time_s) or ramp_time_s <= 0.0:
            raise ValueError(
                "The air conditioner's controller needs a finite ramp_time_to_full_power_at_one_kelvin_s above "
                f"0 s, got {ramp_time_s!r} s: the power changes each step by the step length over this time, so 0 "
                "divides by zero and a negative time lowers the power while the room is on the wrong side of the "
                "set point."
            )
        if config.heating_set_temperature_deg_c >= config.cooling_set_temperature_deg_c:
            raise ValueError(
                "The air conditioner's controller needs heating_set_temperature_deg_c below "
                f"cooling_set_temperature_deg_c, got {config.heating_set_temperature_deg_c} °C and "
                f"{config.cooling_set_temperature_deg_c} °C: the comfort band lies between the two, and with the "
                "heating set point on top an idle unit heats a room that is already above the cooling set point."
            )
        for field_name, seconds in (
            ("minimum_runtime_s", config.minimum_runtime_s),
            ("minimum_idle_time_s", config.minimum_idle_time_s),
        ):
            if not math.isfinite(seconds) or seconds < 0.0:
                raise ValueError(
                    f"The air conditioner's controller needs {field_name} to be a finite time of at least 0 s, got "
                    f"{seconds!r} s: 0 means no minimum, and a negative time would silently mean no minimum as well."
                )

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
        #: Change of the power fraction per time step and kelvin of deviation.
        self.power_fraction_change_per_kelvin_and_timestep = (
            min(my_simulation_parameters.seconds_per_timestep, self.LONGEST_RAMPED_STEP_S)
            / config.ramp_time_to_full_power_at_one_kelvin_s
        )

        component_name = self.get_component_name()
        super().__init__(
            component_name, my_simulation_parameters, config, my_display_config
        )

        self.state = AirConditionerControllerState(
            AirConditionerControllerMode.OFF,
            activation_time_step=0,
            deactivation_time_step=-self.minimum_resting_time_in_timesteps,
            power_fraction=0.0,
        )
        self.previous_state = self.state.clone()

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

    def get_default_connections_from_building(self) -> List[cp.ComponentConnection]:
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
        """Commits the state the last pass of the previous step left, for every pass of this step."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Starts the pass from the committed state, discarding whatever an earlier pass of this step wrote."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self, timestep: int, stsv: cp.SingleTimeStepValues
    ) -> None:
        """Doublechecks."""
        pass

    def write_to_report(self) -> List[str]:
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
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_cost_opex(
        self, all_outputs: List[cp.ComponentOutput], postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:
        """Returns opex costs of component."""

        # Returns default class, as controller itself has no opex cost
        return OpexCostDataClass.get_default_opex_cost_data_class()

    def get_component_kpi_entries(
        self,
        all_outputs: List[cp.ComponentOutput],
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
        """Sends this step's signal, decided on the committed state, and samples the air for the next step.

        The decision reads only :attr:`previous_state`, the state committed at the end of the
        previous step, so every pass of a step sends the same signal; forced convergence
        changes nothing. The indoor air temperature this pass reads is stored in the working
        state, and the last pass's value is what the next step decides on.

        Args:
            timestep: Index of the time step being simulated.
            stsv: The step's values; the indoor air temperature is read from it and the
                signal written to it.
            force_convergence: Unused; the signal is the same in every pass anyway.
        """
        decided_state = self.decide(self.previous_state, timestep)
        decided_state.indoor_air_temperature_deg_c = stsv.get_input_value(
            self.indoor_air_temperature_channel
        )
        self.state = decided_state
        stsv.set_output_value(
            self.operation_modulating_signal_channel,
            self.get_signed_power_fraction(decided_state),
        )

    @staticmethod
    def get_signed_power_fraction(state: AirConditionerControllerState) -> float:
        """Encodes a state as the signal the air conditioner reads.

        Args:
            state: The state to encode.

        Returns:
            The power fraction, positive when heating, negative when cooling, 0 when off.
        """
        if state.mode == AirConditionerControllerMode.HEATING:
            return state.power_fraction
        if state.mode == AirConditionerControllerMode.COOLING:
            return -state.power_fraction
        return 0.0

    def decide(
        self, committed_state: AirConditionerControllerState, timestep: int
    ) -> AirConditionerControllerState:
        """Decides mode and power for this step from the state committed at the end of the previous one.

        A pure function of its arguments: it reads the sampled indoor air temperature in
        ``committed_state`` and returns a new state, leaving ``committed_state`` unchanged.
        Before the first sample (the first step) the unit stays as it is. For the rules, see
        the class docstring.

        Args:
            committed_state: The state committed at the end of the previous step.
            timestep: Index of the time step being decided.

        Returns:
            The state for this step; its sampled temperature is still the committed one, for
            the caller to overwrite with this step's reading.
        """
        decided_state = committed_state.clone()
        indoor_air_temperature_deg_c = committed_state.indoor_air_temperature_deg_c
        if indoor_air_temperature_deg_c is None:
            return decided_state

        heating_set_temperature_deg_c = self.config.heating_set_temperature_deg_c
        cooling_set_temperature_deg_c = self.config.cooling_set_temperature_deg_c
        hysteresis_in_kelvin = self.config.hysteresis_in_kelvin

        if committed_state.mode == AirConditionerControllerMode.OFF:
            if timestep - committed_state.deactivation_time_step < self.minimum_resting_time_in_timesteps:
                return decided_state
            if indoor_air_temperature_deg_c < heating_set_temperature_deg_c:
                decided_state.mode = AirConditionerControllerMode.HEATING
            elif indoor_air_temperature_deg_c > cooling_set_temperature_deg_c:
                decided_state.mode = AirConditionerControllerMode.COOLING
            else:
                return decided_state
            decided_state.activation_time_step = timestep
            decided_state.power_fraction = 0.0
        else:
            may_stop = timestep - committed_state.activation_time_step >= self.minimum_runtime_in_timesteps
            heating = committed_state.mode == AirConditionerControllerMode.HEATING
            back_inside_band = (
                indoor_air_temperature_deg_c > heating_set_temperature_deg_c + hysteresis_in_kelvin
                if heating
                else indoor_air_temperature_deg_c < cooling_set_temperature_deg_c - hysteresis_in_kelvin
            )
            at_minimum_power = committed_state.power_fraction <= self.MINIMUM_POWER_FRACTION
            if may_stop and back_inside_band and at_minimum_power:
                decided_state.mode = AirConditionerControllerMode.OFF
                decided_state.deactivation_time_step = timestep
                decided_state.power_fraction = 0.0
                return decided_state

        if decided_state.mode == AirConditionerControllerMode.HEATING:
            deviation_in_kelvin = heating_set_temperature_deg_c - indoor_air_temperature_deg_c
        else:
            deviation_in_kelvin = indoor_air_temperature_deg_c - cooling_set_temperature_deg_c
        decided_state.power_fraction = min(
            max(
                decided_state.power_fraction
                + deviation_in_kelvin * self.power_fraction_change_per_kelvin_and_timestep,
                self.MINIMUM_POWER_FRACTION,
            ),
            1.0,
        )
        return decided_state
