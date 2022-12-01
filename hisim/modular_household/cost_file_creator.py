""" Is used to create all necessary json files. """
from typing import List
from dataclasses import dataclass, field, asdict
import json
from dataclasses_json import dataclass_json
from economic_parameters import EconomicParameters
import hisim.loadtypes as lt


@dataclass_json
@dataclass()
class ComponentCost:

    """ Defines the investment parameters of components. """

    component: lt.ComponentType = lt.ComponentType.BATTERY
    capacity_unit: lt.Units = lt.Units.WATT_HOUR
    cost_unit: lt.Units = lt.Units.EURO
    co2_unit: lt.Units = lt.Units.KG
    time_unit: lt.Units = lt.Units.YEARS
    costfactor_unit: lt.Units = lt.Units.PERCENT
    capacity_for_cost: List[float] = field(default_factory=list)
    cost_per_capacity: List[float] = field(default_factory=list)
    time: List[float] = field(default_factory=list)
    costfactor_per_time: List[float] = field(default_factory=list)
    capacity_for_co2: List[float] = field(default_factory=list)
    co2_per_capacity: List[float] = field(default_factory=list)


def create_componentcost_file(
        component: lt.ComponentType, capacity_unit: lt.Units, capacity_for_cost: List,
        cost_per_capacity: List, time: List, costfactor_per_time: List, capacity_for_co2: List,
        co2_per_capacity: List) -> None:
    """Component Cost file is created."""

    costfile = ComponentCost(
        component=component, capacity_unit=capacity_unit, capacity_for_cost=capacity_for_cost,
        cost_per_capacity=cost_per_capacity, time=time, costfactor_per_time=costfactor_per_time,
        capacity_for_co2=capacity_for_co2, co2_per_capacity=co2_per_capacity)
    costfile_written = json.dumps(asdict(costfile))

    with open('ComponentCost' + component.value + '.json', 'w', encoding="utf-8") as outfile:
        outfile.write(costfile_written)


def write_batterycost_file():
    """Battery Cost is written to json file."""
    create_componentcost_file(
        component=lt.ComponentType.BATTERY, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500, 1000, 2000, 10000],
        cost_per_capacity=[1000, 1800, 2500, 5000], time=[2022, 2030, 2050], costfactor_per_time=[100, 90, 60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000])


def write_heatpump_cost_file():
    """Heatpump Cost is written to json file."""
    create_componentcost_file(
        component=lt.ComponentType.HEAT_PUMP, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500, 1000, 2000, 10000],
        cost_per_capacity=[1000, 1800, 2500, 5000], time=[2022, 2030, 2050], costfactor_per_time=[100, 90, 60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000])


def write_chp_cost_file():
    """Chp Cost is written to json file."""
    create_componentcost_file(
        component=lt.ComponentType.PREDICTIVE_CONTROLLER, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500, 1000, 2000, 10000],
        cost_per_capacity=[1000, 1800, 2500, 5000], time=[2022, 2030, 2050], costfactor_per_time=[100, 90, 60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000])


def create_economicparameters_file(
        insulation_bought: bool,
        insulation_threshold: float,
        pv_bought: bool,
        pv_threshold: float,
        smart_devices_bought: bool,
        smart_devices_threshold: float,
        heatpump_bought: bool,
        heatpump_threshold: float,
        buffer_bought: bool,
        buffer_threshold: float,
        battery_bought: bool,
        battery_threshold: float,
        h2system_bought: bool,
        h2system_threshold: float,
        chp_bought: bool,
        chp_threshold: float,
        electrolyzer_bought: bool,
        electrolyzer_threshold: float,
        surpluscontroller_bought: bool,
        surpluscontroller_threshold: float,
        ev_bought: bool,
        ev_threshold: float) -> None:
    """Economic Parameters are written to json file."""

    economic_parameters_file = EconomicParameters(
        insulation_bought=insulation_bought,
        insulation_threshold=insulation_threshold,
        pv_bought=pv_bought,
        pv_threshold=pv_threshold,
        smart_devices_bought=smart_devices_bought,
        smart_devices_threshold=smart_devices_threshold,
        heatpump_bought=heatpump_bought,
        heatpump_threshold=heatpump_threshold,
        buffer_bought=buffer_bought,
        buffer_threshold=buffer_threshold,
        battery_bought=battery_bought,
        battery_threshold=battery_threshold,
        h2system_bought=h2system_bought,
        h2system_threshold=h2system_threshold,
        chp_bought=chp_bought,
        chp_threshold=chp_threshold,
        electrolyzer_bought=electrolyzer_bought,
        electrolyzer_threshold=electrolyzer_threshold,
        surpluscontroller_bought=surpluscontroller_bought,
        surpluscontroller_threshold=surpluscontroller_threshold,
        ev_bought=ev_bought,
        ev_threshold=ev_threshold)
    economic_parameters_file = json.dumps(asdict(economic_parameters_file))

    with open('EconomicParameters.json', 'w', encoding="utf-8") as outfile:
        outfile.write(economic_parameters_file)


def write_economicparameters_file():
    """Economic Parameters are written to a json file."""
    create_economicparameters_file(
        insulation_bought=True,
        insulation_threshold=1e3,
        pv_bought=True,
        pv_threshold=1e3,
        smart_devices_bought=True,
        smart_devices_threshold=5e2,
        heatpump_bought=False,
        heatpump_threshold=1e4,
        buffer_bought=True,
        buffer_threshold=1e2,
        battery_bought=True,
        battery_threshold=1e3,
        h2system_bought=False,
        h2system_threshold=5e3,
        chp_bought=False,
        chp_threshold=5e3,
        electrolyzer_bought=False,
        electrolyzer_threshold=5e3,
        surpluscontroller_bought=False,
        surpluscontroller_threshold=5e3,
        ev_bought=True,
        ev_threshold=2e4)
