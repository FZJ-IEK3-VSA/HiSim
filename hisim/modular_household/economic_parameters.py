# -*- coding: utf-8 -*-
""" EconomicParameters Class will be written to a json file."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass()
class EconomicParameters:

    """EconomicParameter Class is created."""

    insulation_bought: bool = True
    insulation_threshold: float = 1e3
    pv_bought: bool = True
    pv_threshold: float = 1e3  # in Watt
    smart_devices_bought: bool = True
    smart_devices_threshold: float = 5e2
    heatpump_bought: bool = False
    heatpump_threshold: float = 1e4
    buffer_bought: bool = True
    buffer_threshold: float = 1e2
    battery_bought: bool = True
    battery_threshold: float = 1e3  # in Wh
    h2system_bought: bool = False
    h2system_threshold: float = 5e3
    chp_bought: bool = False
    chp_threshold: float = 5e3
    electrolyzer_bought: bool = False
    electrolyzer_threshold: float = 5e3
    surpluscontroller_bought: bool = False
    surpluscontroller_threshold: float = 5e3
    ev_bought: bool = True
    ev_threshold: float = 2e4

# def create_economicparameters_file(
#         component: lt.ComponentType, capacity_unit: lt.Units, capacity_for_cost: List,
#         cost_per_capacity: List, time: List, costfactor_per_time: List, capacity_for_co2: List,
#         co2_per_capacity: List):

#     costfile = ComponentCost(
#         component=component, capacity_unit=capacity_unit, capacity_for_cost=capacity_for_cost,
#         cost_per_capacity=cost_per_capacity, time=time, costfactor_per_time=costfactor_per_time,
#         capacity_for_co2=capacity_for_co2, co2_per_capacity=co2_per_capacity)
#     costfile = json.dumps(asdict(costfile))

#     with open('ComponentCost' + component.value + '.json', 'w') as outfile:
#         outfile.write(hey)

# def write_batterycost_file():
#     create_componentcost_file(
#         component=lt.ComponentType.BATTERY, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500,1000,2000,10000],
#         cost_per_capacity=[1000,1800,2500,5000], time=[2022,2030,2050], costfactor_per_time=[100,90,60],
#         capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000] )
