"""KPI config module."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json
import enum


@enum.unique
class KpiForOptimization(str, enum.Enum):

    """Choose KPI that will be optimized with building sizer."""

    TOTAL_COSTS = "Total Costs [€]"
    TOTAL_CO2_EMISSION = "Total CO2 Emission [kg]"
    SELFSUFFICIENCY = "Self-sufficiency rate [%]"


@dataclass_json
@dataclass
class KPIConfig:

    """KPI config class."""

    #: ratio between the load covered onsite and the total load, given in %
    self_sufficiency_rate_in_percent: float
    #: annual cost for investment and operation in the considered technology, given in euros
    total_costs_in_euro: float
    #: annual C02 emmissions due to the construction and operation of the considered technology, given in kg
    total_co2_emissions_in_kg: float

    def get_kpi(self, chosen_kpi: KpiForOptimization) -> float:
        """Weights all kpis to get one value evaluating the performance of one building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm of the building sizer.
        """
        self.chosen_kpi = chosen_kpi
        if self.chosen_kpi == KpiForOptimization.SELFSUFFICIENCY:
            return self.self_sufficiency_rate_in_percent
        elif self.chosen_kpi == KpiForOptimization.TOTAL_COSTS:
            return self.total_costs_in_euro
        elif self.chosen_kpi == KpiForOptimization.TOTAL_CO2_EMISSION:
            return self.total_co2_emissions_in_kg

