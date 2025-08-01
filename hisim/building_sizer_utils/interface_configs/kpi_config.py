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
    ANNUALIZED_TOTAL_COSTS = "Annualized Total Costs [€]"
    ANNUALIZED_ENERGY_GRID_COSTS = "Annualized Energy Grid Costs [€]"
    ANNUALIZED_MAINTENANCE_COSTS = "Annualized Maintenance Costs [€]"
    ANNUALIZED_INVESTMENT_COSTS = "Annualized Investment Costs [€]"
    ANNUALIZED_NET_INVESTMENT_COSTS = "Annualized Net Investment Costs [€]"
    # CO2
    # ------------------------------------------------------------
    ANNUALIZED_TOTAL_CO2_EMISSION = "Annualized Total CO2 Emissions [kg]"
    # Other
    # ------------------------------------------------------------
    SELFSUFFICIENCY_ELECTRICITY = "Self-sufficiency rate [%]"

    # Total values (upfront, without lifetime consideration)
    # Costs
    # ------------------------------------------------------------
    INVESTMENT_COSTS = "Investment Costs [€]"
    INVESTMENT_COSTS_MINUS_SUBSIDIES = "Net Investment Costs [€]"
    TOTAL_COSTS = "Total Costs [€]"
    ENERGY_GRID_COSTS = "Energy Grid Costs [€]"
    MAINTENANCE_COSTS = "Maintenance Costs [€]"
    # CO2
    # ------------------------------------------------------------
    TOTAL_CO2_EMISSION = "Total CO2 Emission [kg]"


# pylint: disable=too-many-return-statements
@dataclass_json
@dataclass
class KPIConfig:
    """KPI config class."""

    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_electricity_in_percent: float
    #: annual cost for investment and operation in the considered technology, given in euros
    annualized_total_costs_in_euro: float
    #: annual cost for energy from grid (electricty, gas, heat) given in euros
    annualized_energy_grid_costs_in_euro: float
    #: annual cost for energy from grid (electricty) given in euros
    annualized_electricity_grid_costs_in_euro: float
    #: annual cost for energy from grid (gas) given in euros
    annualized_gas_grid_costs_in_euro: float
    #: annual cost for energy from grid or onsite consumption (heat) given in euros
    annualized_heat_costs_in_euro: float
    #: annual cost for maintenance given in euros
    annualized_maintenance_costs_in_euro: float
    #: annual cost for investment given in euros
    annualized_investment_costs_in_euro: float
    #: annual net cost for investment given in euros (subsidies substracted)
    annualized_net_investment_costs_in_euro: float
    #: annual C02 emmissions due to the construction and operation of the considered technology, given in kg
    annualized_total_co2_emissions_in_kg: float

    def get_kpi_for_rating(self, chosen_kpi: KPIForRatingInOptimization) -> float:
        """Weights all kpis to get one value evaluating the performance of one building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm of the building sizer.
        """

        if chosen_kpi == KPIForRatingInOptimization.SELFSUFFICIENCY_ELECTRICITY:
            return self.self_sufficiency_rate_electricity_in_percent
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_TOTAL_COSTS:
            return self.annualized_total_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_ENERGY_GRID_COSTS:
            return self.annualized_energy_grid_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_MAINTENANCE_COSTS:
            return self.annualized_maintenance_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_INVESTMENT_COSTS:
            return self.annualized_investment_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_NET_INVESTMENT_COSTS:
            return self.annualized_net_investment_costs_in_euro
        if chosen_kpi == KPIForRatingInOptimization.ANNUALIZED_TOTAL_CO2_EMISSION:
            return self.annualized_total_co2_emissions_in_kg
        raise ValueError(f"Chosen KPI {self.chosen_kpi} not recognized.")
