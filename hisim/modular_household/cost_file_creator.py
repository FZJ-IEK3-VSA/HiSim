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
        co2_per_capacity: List):

    costfile = ComponentCost(
        component=component, capacity_unit=capacity_unit, capacity_for_cost=capacity_for_cost,
        cost_per_capacity=cost_per_capacity, time=time, costfactor_per_time=costfactor_per_time,
        capacity_for_co2=capacity_for_co2, co2_per_capacity=co2_per_capacity)
    costfile = json.dumps(asdict(costfile))
    
    with open('ComponentCost' + component.value + '.json', 'w') as outfile:
        outfile.write(hey)

def write_batterycost_file():
    create_componentcost_file(
        component=lt.ComponentType.BATTERY, capacity_unit=lt.Units.WATT_HOUR, capacity_for_cost=[500,1000,2000,10000],
        cost_per_capacity=[1000,1800,2500,5000], time=[2022,2030,2050], costfactor_per_time=[100,90,60],
        capacity_for_co2=[1000, 10000], co2_per_capacity=[200, 2000] )
   
 
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
        co2_per_unit_fuel):
    costfile= FuelCost(fuel=fuel, fuel_unit=fuel_unit, price_per_unit_fuel=price_per_unit_fuel,
                       co2_per_unit_fuel=co2_per_unit_fuel)
    costfile = json.dumps(asdict(costfile))
    
    with open('FuelCost' + fuel.value + '.json', 'w') as outfile:
        outfile.write(costfile)

def write_electricitycost_file():
    create_fuelcost_file(
        fuel=lt.LoadTypes.ELECTRICITY, fuel_unit=lt.Units.WATT_HOUR,
        price_per_unit_fuel=2e-4, co2_per_unit_fuel=1.5e-4)
    