""" Generates Class for the results of the modular_example preprocessing."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hisim.loadtypes as lt


@dataclass_json
@dataclass()
class ModularHouseholdResults:

    """Class for Results of ModularHousehold."""

    investment_cost: float
    co2_cost: float
    injection: float
    autarky_rate: float
    self_consumption_rate: float
    terminationflag: lt.Termination = lt.Termination.SUCCESSFUL  # add enum in loadtypes
