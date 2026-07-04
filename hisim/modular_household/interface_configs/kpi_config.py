"""KPI config module."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class KPIConfigModular:

    """Configuration container for household energy system KPIs.

    Stores key performance indicators used to evaluate the performance
    of a building/household energy configuration. These metrics are
    computed during simulation and used by optimization algorithms
    (e.g., evolutionary building sizer) to compare and rank configurations.

    Attributes:
        self_consumption_rate: Ratio of the portion of PV production
            consumed by the loads to the total PV production, given in
            percent.
        autarky_rate: Ratio of the portion of PV production consumed by
            the loads to the total load, given in percent.
        injection: Amount of electricity injected to the grid during the
            simulation period, given in kWh.
        economic_investment_costs_in_euro: Annual cost for investment
            and operation of the considered technology, given in euros.
        co2_investment_costs_in_kg: Annual CO2 emissions due to the
            construction and operation of the considered technology,
            given in kg.
    """

    #: ratio between the portion of the PV production consumed by the loads and the total PV production, given in %
    self_consumption_rate: float
    #: ratio between the portion of the PV production consumed by the loads and the total load, given in %
    autarky_rate: float
    #: amount of electricity injected to the grid during the simulation period, given in kWh
    injection: float
    #: annual cost for investment and operation in the considered technology, given in euros
    economic_investment_costs_in_euro: float
    #: annual CO2 emissions due to the construction and operation of the considered technology, given in kg
    co2_investment_costs_in_kg: float

    def get_kpi(self) -> float:
        """Weights all kpis to get one value evaluating the performance of one building configuration.

        Also referred to as "rating" or "fitness" in the evolutionary algorithm of the building sizer.

        Returns:
            The sum ``self_consumption_rate + autarky_rate`` expressed in
            **percent** (i.e. each input is a percentage in [0, 100], as
            documented on the fields, *not* a fraction in [0, 1]). The result
            therefore ranges up to 200 for the unweighted sum of two rates.
        """
        # TODO: find normalization for KPIs and multiply by weights given from causal model
        # first approach: sum of self consumption and autarky (both given in percent)
        return self.self_consumption_rate + self.autarky_rate
