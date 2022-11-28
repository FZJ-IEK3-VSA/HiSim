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

def get_kpi_from_json(kpi_file: str) -> float:
    kpi_instance = KPIConfig.from_json(kpi_file) # type ignore
    return kpi_instance.self_consumption_rate