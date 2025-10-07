"""KPI config module."""

from dataclasses import dataclass
import enum
from dataclasses_json import dataclass_json


@enum.unique
class KPIForRatingInOptimization(str, enum.Enum):
    """Choose KPI that will be optimized with building sizer."""

    # Annualized KPis (values are divided by component lifetime)
    # Costs
    # ------------------------------------------------------------
    TOTAL_UPFRONT_NET_INVESTMENT_COSTS = "Total Upfront Net Investment Costs [€]"
    ANNUALIZED_TOTAL_COSTS = "Annualized Total Costs [€/m2]"
    ANNUALIZED_ENERGY_COSTS = "Annualized Energy Costs [€/m2]"
    ANNUALIZED_MAINTENANCE_COSTS = "Annualized Maintenance Costs [€/m2]"
    ANNUALIZED_INVESTMENT_COSTS = "Annualized Investment Costs [€/m2]"
    ANNUALIZED_NET_INVESTMENT_COSTS = "Annualized Net Investment Costs [€/m2]"
    # CO2
    # ------------------------------------------------------------
    ANNUALIZED_TOTAL_CO2_EMISSION = "Annualized Total CO2 Emissions [kg/m2]"
    ANNUALIZED_ENERGY_CO2_EMISSION = "Annualized Energy CO2 Emissions [kg/m2]"
    # Other
    # ------------------------------------------------------------
    SELFSUFFICIENCY_ELECTRICITY = "Self-Sufficiency Rate For Electricity [%]"
    SELFSUFFICIENCY_ALL_ENERGY = "Self-Sufficiency Rate All Energy [%]"
    ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION = "Annualized Energy Consumption [kWh/m2]"
    ANNUALIZED_ELECTRICITY_TO_GRID = "Annualized Electricity To Grid [kWh/m2]"
    ANNUALIZED_ELECTRICITY_FROM_GRID = "Annualized Electricity From Grid [kWh/m2]"
    MIN_BUILDING_INDOOR_TEMP = "Minimum Indoor Temperature [°C]"
    MAX_BUILDING_INDOOR_TEMP = "Maximum Indoor Temperature [°C]"
    DEV_FROM_MIN_BUILDING_INDOOR_TEMP = "Devation From Minimum Indoor Temperature [°C*h]"
    DEV_FROM_MAX_BUILDING_INDOOR_TEMP = "Devation From Maximum Indoor Temperature [°C*h]"


# pylint: disable=too-many-return-statements
@dataclass_json
@dataclass
class KPIConfig:
    """KPI config class."""

    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_electricity_in_percent: float
    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_all_energy_in_percent: float
    #: total upfront net investment costs, given in euro
    total_upfront_net_investment_costs_in_euro: float
    #: annual cost for investment and operation in the considered technology, given in euros per m2
    annualized_total_costs_in_euro_per_m2: float
    #: annual cost for energy from grid or from onsite consumption (electricty, gas, heat) given in euros per m2
    annualized_energy_costs_in_euro_per_m2: float
    #: annual cost for energy from grid (electricty) given in euros per m2
    annualized_electricity_costs_in_euro_per_m2: float
    #: annual cost for energy from grid (gas) given in euros per m2
    annualized_gas_costs_in_euro_per_m2: float
    #: annual cost for energy from grid or onsite consumption (heat) given in euros per m2
    annualized_heat_costs_in_euro_per_m2: float
    #: annual cost for maintenance given in euros per m2
    annualized_maintenance_costs_in_euro_per_m2: float
    #: annual cost for investment given in euros per m2
    annualized_investment_costs_in_euro_per_m2: float
    #: annual net cost for investment given in euros (subsidies substracted) per m2
    annualized_net_investment_costs_in_euro_per_m2: float
    #: annual C02 emmissions due to the construction and operation of the considered technology, given in kg per m2
    annualized_total_co2_emissions_in_kg_per_m2: float
    #: annual C02 emmissions due to the construction of the considered technology, given in kg per m2
    annualized_co2_emissions_for_devices_in_kg_per_m2: float
    #: annual C02 emmissions due to operation of the considered technology, given in kg per m2
    annualized_energy_co2_emissions_in_kg_per_m2: float
    annualized_electricity_co2_emissions_in_kg_per_m2: float
    annualized_gas_co2_emissions_in_kg_per_m2: float
    annualized_heat_co2_emissions_in_kg_per_m2: float
    #: annual energy consumption, given in kwh per m2
    annualized_purchased_energy_consumption_in_kwh_per_m2: float
    #: annual electricity to grid per m2
    annualized_electricity_to_grid_in_kwh_per_m2: float
    #: annual electricity to grid per m2
    annualized_electricity_from_grid_in_kwh_per_m2: float

    #: KPis for thermal comfort
    minimum_indoor_temperature_in_celsius: float
    maximum_indoor_temperature_in_celsius: float
    deviation_from_min_indoor_temperature_in_celsius_hour: float
    deviation_from_max_indoor_temperature_in_celsius_hour: float

    def get_kpi_for_rating(self, chosen_kpi: KPIForRatingInOptimization) -> float:
        """Weights all kpis to get one value evaluating the performance of one building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm of the building sizer.
        """

        if chosen_kpi == KPIForRatingInOptimization.SELFSUFFICIENCY_ELECTRICITY:
            return self.self_sufficiency_rate_electricity_in_percent
        if chosen_kpi == KPIForRatingInOptimization.SELFSUFFICIENCY_ALL_ENERGY:
            return self.self_sufficiency_rate_all_energy_in_percent
        if chosen_kpi == KPIForRatingInOptimization.TOTAL_UPFRONT_NET_INVESTMENT_COSTS:
            return self.total_upfront_net_investment_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_TOTAL_COSTS:
            return self.annualized_total_costs_in_euro_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_ENERGY_COSTS:
            return self.annualized_energy_costs_in_euro_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_MAINTENANCE_COSTS:
            return self.annualized_maintenance_costs_in_euro_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_INVESTMENT_COSTS:
            return self.annualized_investment_costs_in_euro_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_NET_INVESTMENT_COSTS:
            return self.annualized_net_investment_costs_in_euro_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_TOTAL_CO2_EMISSION:
            return self.annualized_total_co2_emissions_in_kg_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_ENERGY_CO2_EMISSION:
            return self.annualized_energy_co2_emissions_in_kg_per_m2
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_PURCHASED_ENERGY_CONSUMPTION:
            return self.annualized_purchased_energy_consumption_in_kwh_per_m2

        raise ValueError(f"Chosen KPI {chosen_kpi} not recognized.")
