# -*- coding: utf-8 -*-
"""
Created on Thu Sep  8 09:42:48 2022

@author: Johanna
"""

from typing import List
from dataclasses import dataclass, field, asdict
from dataclasses_json import dataclass_json
import json
import hisim.loadtypes as lt
from economic_parameters import EconomicParameters

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
    capacity_for_cost: List[float] = field( default_factory=list)
    cost_per_capacity: List[float] = field( default_factory=list)
    time: List[float] = field( default_factory=list) 
    costfactor_per_time: List[float] = field( default_factory=list)
    capacity_for_co2: List[float] = field( default_factory=list)
    co2_per_capacity: List[float] = field( default_factory=list)
    
def create_componentcost_file(
        component: lt.ComponentType, capacity_unit: lt.Units, capacity_for_cost: List,
        cost_per_capacity: List, time: List, costfactor_per_time: List, capacity_for_co2: List,
        co2_per_capacity: List) -> None:

    costfile = ComponentCost(
        component=component, capacity_unit=capacity_unit, capacity_for_cost=capacity_for_cost,
        cost_per_capacity=cost_per_capacity, time=time, costfactor_per_time=costfactor_per_time,
        capacity_for_co2=capacity_for_co2, co2_per_capacity=co2_per_capacity)
    costfile_written=json.dumps(asdict(costfile))
    
    with open('ComponentCost' + component.value + '.json', 'w') as outfile:
        outfile.write(costfile_written)

def write_batterycost_file():
    create_componentcost_file(
        component=lt.ComponentType.BATTERY, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500,1000,2000,10000],
        cost_per_capacity=[1000,1800,2500,5000], time=[2022,2030,2050], costfactor_per_time=[100,90,60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000] )

def write_heatpump_cost_file():
    create_componentcost_file(
        component=lt.ComponentType.HEAT_PUMP, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500,1000,2000,10000],
        cost_per_capacity=[1000,1800,2500,5000], time=[2022,2030,2050], costfactor_per_time=[100,90,60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000] )

def write_smartdev_cost_file():
    create_componentcost_file(
        component=lt.ComponentType.HEAT_PUMP, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500,1000,2000,10000],
        cost_per_capacity=[1000,1800,2500,5000], time=[2022,2030,2050], costfactor_per_time=[100,90,60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000] )
write_heatpump_cost_file()
 

@dataclass_json
@dataclass()
class FuelCost:
    
    """Defines economic and C02 parameters of fuels. """
    fuel: lt.LoadTypes = lt.LoadTypes.GAS
    fuel_unit: lt.Units =lt.Units.WATT_HOUR
    price_unit: lt.Units = lt.Units.EURO
    co2_unit: lt.Units = lt.Units.KG
    price_per_unit_fuel: float = 1e-4
    co2_per_unit_fuel: float = 1e-4
    
def create_fuelcost_file(
        fuel: lt.LoadTypes, fuel_unit: lt.Units, price_per_unit_fuel: float,
        co2_per_unit_fuel:float) -> None:
    costfile= FuelCost(fuel=fuel, fuel_unit=fuel_unit, price_per_unit_fuel=price_per_unit_fuel,
                       co2_per_unit_fuel=co2_per_unit_fuel)
    costfile_written = json.dumps(asdict(costfile))
    
    with open("FuelCost" + fuel.value + '.json', 'w') as outfile:
        outfile.write(costfile_written)

def write_electricitycost_file():
    create_fuelcost_file(
        fuel=lt.LoadTypes.ELECTRICITY, fuel_unit=lt.Units.WATT_HOUR,
        price_per_unit_fuel=2e-4, co2_per_unit_fuel=1.5e-4)

def create_economicparameters_file(insulation_bought: bool,
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
    ev_bought: bool,
    ev_threshold: float) -> None:

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
            ev_bought=ev_bought,
            ev_threshold=ev_threshold)
    economic_parameters_file = json.dumps(asdict(economic_parameters_file))

    with open('EconomicParameters.json', 'w') as outfile:
        outfile.write(economic_parameters_file)


def write_economicparameters_file():
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
            ev_bought=True,
            ev_threshold=2e4)
#write_economicparameters_file()




