"""KPI config module."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class KPIConfigModular:

    """KPI config class."""

    #: ratio between the portion of the PV production consumed by the loads and the total PV production, given in %
    self_consumption_rate: float
    #: ratio between the portion of the PV production consumed by the loads and the total load, given in %
    autarky_rate: float
    #: amount of electricity injected to the grid during the simulation period, given in kWh
    injection: float
    #: annual cost for investment and operation in the considered technology, given in euros
    economic_investment_costs_in_euro: float
    #: annual C02 emmissions due to the construction and operation of the considered technology, given in kg
    co2_investment_costs_in_euro: float

    def get_kpi(self) -> float:
        """Weights all kpis to get one value evaluating the performance of one building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm of the building sizer.
        """
        # TODO: find normalization for KPIs and multiply by weights given from causal model
        # first approach: sum of self consumption and autarky...
        return self.self_consumption_rate + self.autarky_rate
