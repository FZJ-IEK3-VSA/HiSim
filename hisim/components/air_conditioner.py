"""Air Conditioner Component."""

from dataclasses import dataclass
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

    #: Share of the selected unit's heating capacity that :meth:`for_building_load` sizes it
    #: to. 0.6 was determined heuristically based on manually experimenting with different
    #: scaling factors.
    CAPACITY_SHARE_FOR_BUILDING_LOAD: ClassVar[float] = 0.6

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
            "to the load, then scales it by 0.6 × capacity / load"
        )
    )
    @classmethod
    def for_building_load(
        cls,
        name: str,
        heating_load_in_watt: float,
        heating_reference_temperature_in_celsius: float,
    ) -> "AirConditionerConfig":
        """Picks the catalogue unit that fits a building's heating load and sizes it down to it.

        Every catalogue row is measured at three outdoor temperatures; the one closest to
        ``heating_reference_temperature_in_celsius`` gives the row's heating capacity at the
        building's design condition. The unit whose capacity is nearest the building's heating
        load wins, and its ``scale_factor`` becomes
        :attr:`CAPACITY_SHARE_FOR_BUILDING_LOAD` × capacity / load, so the unit is run below its
        rating instead of at it. Ties go to the unit the database lists first.

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

        return cls.for_device(
            name,
            manufacturer=air_conditioners[selected]["Manufacturer"],
            model_name=air_conditioners[selected]["Model"],
            scale_factor=capacities[selected] * cls.CAPACITY_SHARE_FOR_BUILDING_LOAD / heating_load_in_watt,
        )

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


class AirConditioner(cp.Component):
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
    """Configuration of the air conditioner's controller: a comfort band with minimum run times.

    The controller watches the building's indoor air temperature and puts the unit into
    heating below :attr:`heating_set_temperature_deg_c`, into cooling above
    :attr:`cooling_set_temperature_deg_c`, and off in the band between them. Two things
    soften that switch: ``offset`` widens the band the unit stays in once it has started, so
    it does not stop the moment the setpoint is reached, and the two minimum times keep it
    running, or keep it off, for a while whatever the air says. Within the running band the
    power is modulated quadratically, reaching full power
    ``temperature_difference_full_power_deg_c`` kelvin past the setpoint that started it.

    The named default is :meth:`preset_standard`::

        AirConditionerControllerConfig.preset_standard("AirConditionerController")

    Nothing here is derived from the building. These are the temperatures the residents ask
    for and the cycling limits of the machine, so no field is sizable and the preset takes
    nothing but the instance name.
    """

    MAIN_CLASS = "hisim.components.air_conditioner.AirConditionerController"

    component_id: ComponentID
    #: Indoor air temperature below which the unit heats, in °C.
    heating_set_temperature_deg_c: float = 20.0
    #: Indoor air temperature above which the unit cools, in °C.
    cooling_set_temperature_deg_c: float = 24.0
    #: Shortest time the unit stays in heating or cooling once it has started, in seconds.
    #: Rounded down to whole time steps, so a value below one time step imposes nothing.
    minimum_runtime_s: float = 1800.0
    #: Shortest time the unit stays off once it has stopped, in seconds, rounded down the
    #: same way. Together with the runtime it is what stops the unit chattering on and off
    #: around the setpoint.
    minimum_idle_time_s: float = 900.0
    #: Width of the hysteresis band, in kelvin: how far past the setpoint that started it the
    #: unit keeps running. Heating continues up to ``heating_set_temperature_deg_c + offset``
    #: and cooling down to ``cooling_set_temperature_deg_c - offset``.
    offset: float = 5.0
    #: Temperature difference from the far edge of that band at which the unit runs at full
    #: power, in kelvin. Below it the modulation is the square of the ratio, so the unit
    #: throttles back sharply as the air approaches the setpoint.
    temperature_difference_full_power_deg_c: float = 3.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "AirConditionerControllerConfig":
        """The one comfort band the fleet runs: heating below 20 °C, cooling above 24 °C.

        The field defaults are that band, with five kelvin of hysteresis either side, full
        power three kelvin from the edge, and the unit held for half an hour once it starts
        and a quarter of an hour once it stops.

        Args:
            name: Instance name of the controller in the simulation.

        Returns:
            The configuration, fully concrete -- the class has no sizable field.
        """
        return cls(component_id=ComponentID(name=name))


class AirConditionerControllerState:
    """Class representing the internal state of the air conditioner controller."""

    def __init__(
        self,
        mode: str,
        activation_time_step: int,
        deactivation_time_step: int,
        power_modulation_percentage: float,
    ) -> None:
        """Constructor."""

        self.mode = mode  # can be "heating", "cooling", or "off"
        self.activation_time_step = activation_time_step
        self.deactivation_time_step = deactivation_time_step
        self.power_modulation_percentage = (
            power_modulation_percentage  # current power modulation level (0.1 - 1.0)
        )

    def clone(self) -> "AirConditionerControllerState":
        """Returns a deep copy of the current state."""
        return AirConditionerControllerState(
            self.mode,
            self.activation_time_step,
            self.deactivation_time_step,
            self.power_modulation_percentage,
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

    cost_relevance = CostRelevance.FREE_OF_COST

    TemperatureIndoorAir: ClassVar[str] = "TemperatureIndoorAir"
    ElectricityInput: ClassVar[str] = "ElectricityInput"
    OperatingState: ClassVar[str] = "OperatingState"
    ModulatingPowerSignal: ClassVar[str] = "ModulatingPowerSignal"

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
        self.last_committed_state = self.state.clone()

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
        """Main simulation step."""
        if force_convergence:
            self.state = self.last_committed_state.clone()
            mode = self.state.mode
        else:
            indoor_air_temperature_deg_c = stsv.get_input_value(
                self.indoor_air_temperature_channel
            )
            mode = self.determine_operating_mode(
                indoor_air_temperature_deg_c, timestep
            )
            power_modulation_percentage = self.modulate_power(
                indoor_air_temperature_deg_c, mode
            )

            self.state.mode = mode
            self.state.power_modulation_percentage = power_modulation_percentage
            self.last_committed_state = self.state.clone()

        # Encode state as signed power level (heating = +%, cooling = -%, off = 0)
        if mode == "heating":
            signed_modulating_signal = self.state.power_modulation_percentage
        elif mode == "cooling":
            signed_modulating_signal = -self.state.power_modulation_percentage
        elif mode == "off":
            signed_modulating_signal = 0.0
        else:
            raise ValueError(f"Unhandled mode case: {mode}")

        stsv.set_output_value(
            self.operation_modulating_signal_channel, signed_modulating_signal
        )

    def _check_minimum_operation_and_idle_time(self, timestep: int) -> Optional[str]:
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
            or self.last_committed_state.mode == "heating"
        ) and current_temperature_deg_c < heating_setpoint + offset:
            if self.state.mode != "heating":
                self.state.activate_heating(timestep)
            return "heating"

        # Stay in cooling if within cooling deadband
        if (
            self.state.mode == "cooling"
            or self.last_committed_state.mode == "cooling"
        ) and current_temperature_deg_c > cooling_setpoint - offset:
            if self.state.mode != "cooling":
                self.state.activate_cooling(timestep)
            return "cooling"

        # Switch to cooling if temperature exceeds upper cooling threshold (and previously not heating)
        if current_temperature_deg_c > cooling_setpoint and (
            self.state.mode != "heating"
            and self.last_committed_state.mode != "heating"
        ):
            if self.state.mode != "cooling":
                self.state.activate_cooling(timestep)
            return "cooling"

        # Switch to heating if temperature drops below lower heating threshold (and previously not cooling)
        if current_temperature_deg_c < heating_setpoint and (
            self.state.mode != "cooling"
            and self.last_committed_state.mode != "cooling"
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
        power_modulation_percentage = float(max(1 - (1 - capped_ratio) ** 2, 0.1))

        return power_modulation_percentage
