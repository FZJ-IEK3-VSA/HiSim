# -*- coding: utf-8 -*-
"""
Created on Thu Sep  8 12:50:25 2022

@author: Johanna
"""

from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import json
import hisim.loadtypes as lt

@dataclass_json
@dataclass()
class ModularHouseholdResults:
    terminationflag: lt.Termination = lt.Termination.SUCESSFUL #add enum in loadtypes
    investment_cost: float
    co2_cost: float
    injection: float
    autarky_rate: float
    self_consumption_rate: float