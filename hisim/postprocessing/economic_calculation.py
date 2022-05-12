from dataclasses import dataclass
import hisim.loadtypes as lt
@dataclass
class  ElectricityPrices:
    def electricity_price_from_grid_household(self, pv_size):
        electricity_price_from_grid_household = 0.3205
        return electricity_price_from_grid_household

    def electricity_price_from_grid_industry(self, utilisation_hours, price_for_atypical_usage, provider_price ):
        if utilisation_hours>=2500:
            if provider_price== "average":
                leistungspreis= 44.4 #Eurp/kW
                arbeitspreis= 0.0154 #Eurp/kWh
            elif provider_price== "high":
                leistungspreis= 169.9 #Eurp/kW
                arbeitspreis= 0.083 #Eurp/kWh
            elif provider_price == "low":
                leistungspreis= 73.6 #Eurp/kW
                arbeitspreis= 0.021 #Eurp/kWh

        elif utilisation_hours<2500:
            if provider_price== "average":
                leistungspreis=17.93
                arbeitspreis=0.026
            elif provider_price== "high":
                leistungspreis= 24.2 #Eurp/kW
                arbeitspreis= 0.0663#Eurp/kWh
            elif provider_price == "low":
                leistungspreis= 9.26 #Eurp/kW
                arbeitspreis= 0.0467#Eurp/kWh

        elif price_for_atypical_usage==True:
            if provider_price == "average":
                leistungspreis= 7.4 #Eurp/kW
                arbeitspreis= 0.0154
            elif provider_price== "high":
                leistungspreis= 28.19#Eurp/kW
                arbeitspreis= 0.083
            elif provider_price == "high":
                leistungspreis= 12.28#Eurp/kW
                arbeitspreis= 0.021

        return leistungspreis, arbeitspreis

    def electricity_price_relative_to_pv_size(self, pv_size):
        if pv_size < 10:
            electricity_prize_into_grid = 0.0769
        elif pv_size < 40 and pv_size > 10:
            electricity_prize_into_grid = 0.0747
        elif pv_size > 40 and pv_size < 100 :
            electricity_prize_into_grid = 0.0587
        elif pv_size > 100:
            electricity_prize_into_grid = 0.0510
        return electricity_prize_into_grid

@dataclass
class GenericBattery:
    lifespan:float
    capacity:float

    def capex_refered_to_capacity(self,capacity:float):
        return (1374.6 * capacity ** (-0.203)) * capacity
    def annual_opex_refered_to_capacity(self,annual_capex):
        return 0.01*annual_capex

    annual_capex=capex_refered_to_capacity(capacit=capacity)
    annual_opex=annual_opex_refered_to_capacity(annual_capex=annual_capex)
class ComponentData:
    def __init__(self,
                 component_name:str,
                 component_type:lt.ComponentType,
                 capex:None,
                 annual_opex:None,
                 lifespan:None,
                 ):
        self.component_name=component_name
        self.component_type=component_type
        self.capex=capex
        self.annual_opex=annual_opex
        self.lifespan=lifespan
    GenericBattery.annual_capex
