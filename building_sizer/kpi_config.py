from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import Any

@dataclass_json
@dataclass
class KPIConfig:
    self_consumption_rate: float
    autarky_rate: float
    injection: float
    economic_cost: float
    co2_cost: float  

def get_kpi_from_json(kpi_file: str) -> Any:
    kpi_instance = KPIConfig.from_json(kpi_file)  # type: ignore
    # TODO: find normalization for KPIs and multiply by weights given from causal model
    # first approach: sum of self consumption and autarky...
    return kpi_instance.self_consumption_rate + kpi_instance.autarky_rate