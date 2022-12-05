from dataclasses import dataclass
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class KPIConfig:
    self_consumption_rate: float
    autarky_rate: float
    injection: float
    economic_cost: float
    co2_cost: float

    def get_kpi(self) -> float:
        # TODO: find normalization for KPIs and multiply by weights given from causal model
        # first approach: sum of self consumption and autarky...
        return self.self_consumption_rate + self.autarky_rate
