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