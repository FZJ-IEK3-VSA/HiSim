import numpy as np
import pandas as pd
import json
from dataclasses import dataclass
from dataclasses import asdict
@dataclass
class SimulationParameters:
    year: int
    seconds_per_timestep: int
    method:str

@dataclass
class TechnologicalKPIS:
    autarky: float
    self_consumption: float
    utilisation_hours: float

@dataclass
class ElectricityFlowsTotal:
    electricity_from_grid: float
    electricity_into_grid: float
    maximimum_power_of_grid: float
    electricity_lost_curtailment: float

@dataclass
class ElectricityFlowsComponents:
    electricity_from_grid_into_battery: float
    electricity_from_grid_into_heat_pump: float
    electricity_from_grid_into_house: float
    electricity_into_grid_from_PV: float
    electricity_into_grid_from_CHP: float

@dataclass
class GasFlowsTotal:
    total_gas_flow_from_grid: float
    total_gas_from_grid_into_CHP: float
    total_gas_from_grid_into_gas_boiler: float

@dataclass
class SimulationResults:
    technological_KPIS: TechnologicalKPIS
    electricity_flows_total: ElectricityFlowsTotal
    electricity_flows_components:ElectricityFlowsComponents
    gas_flows_total: GasFlowsTotal

def dataclass_to_json(dataclass_to_change):
    json_object = json.dumps(asdict(dataclass_to_change), indent=4)
    with open('data.json', 'w') as f:
        json.dump(json_object, f)
def json_to_dataclass(json_to_change):
    pass

###some data is added just as an example
technological_KPIS_with_battery=TechnologicalKPIS(autarky=0.5,
                                                  self_consumption=0.3,
                                                  utilisation_hours=100)
electricity_flows_total_with_battery=ElectricityFlowsTotal(electricity_from_grid=3000,
                                                           electricity_into_grid=2000,
                                                           maximimum_power_of_grid=100,
                                                           electricity_lost_curtailment=0)
electricity_flows_components_with_battery=ElectricityFlowsComponents(electricity_into_grid_from_CHP=1000,
                                                        electricity_into_grid_from_PV=1000,
                                                        electricity_from_grid_into_heat_pump=1000,
                                                        electricity_from_grid_into_house=1000,
                                                        electricity_from_grid_into_battery=1000)
gas_flows_total_with_battery=GasFlowsTotal(total_gas_flow_from_grid=1000,
                              total_gas_from_grid_into_CHP=1000,
                              total_gas_from_grid_into_gas_boiler=1000)
simulation_results_with_battery=SimulationResults(technological_KPIS=technological_KPIS_with_battery,
                                               electricity_flows_total=electricity_flows_total_with_battery,
                                               electricity_flows_components=electricity_flows_components_with_battery,
                                               gas_flows_total=gas_flows_total_with_battery)
dataclass_to_json(simulation_results_with_battery)


