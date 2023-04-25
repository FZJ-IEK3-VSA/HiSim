from hisim.components import (generic_hot_water_storage_modular,
                              generic_pv_system,
                              generic_smart_device,
                              generic_heat_source,
                              advanced_battery_bslib,
                              generic_car
)

from hisim.utils import HISIMPATH
import pandas as pd
from hisim.loadtypes import LoadTypes
from typing import List, Tuple
from hisim.component_wrapper import ComponentWrapper

def read_in_component_costs() -> pd.DataFrame:
    """Reads data for cost and co2 emissions of component installation/investment from csv."""
    price_frame = pd.read_csv(HISIMPATH["component_costs"], sep=";", usecols=[0, 8, 9])
    price_frame.index = price_frame["Product/service"]  # type: ignore
    price_frame.drop(columns=["Product/service"], inplace=True)
    return price_frame

def compute_investment_cost(
        components: List[ComponentWrapper],
) -> Tuple[float, float]:
    
    # initialize values
    investment_cost = 0.0
    co2_emissions = 0.0
    price_frame = read_in_component_costs()

    for component in components:
        if isinstance(component.my_component, generic_smart_device.SmartDevice):
            column = price_frame.iloc[price_frame.index == "Washing machine (or domestic appliances in general)"]
            component_capacity = 1
        elif isinstance(component.my_component, generic_pv_system.PVSystem):
            column = price_frame.iloc[price_frame.index == "Photovoltaic panel"]
            component_capacity = component.my_component.power * 1e-3
        elif isinstance(component.my_component, generic_heat_source.HeatSource):
            if component.my_component.config.fuel == LoadTypes.DISTRICTHEATING:
                column = price_frame.iloc[price_frame.index == "Biomass district heating system"]
            elif component.my_component.config.fuel == LoadTypes.GAS:
                column = price_frame.iloc[price_frame.index == "Gas boiler"]
            elif component.my_component.config.fuel == LoadTypes.OIL:
                column = price_frame.iloc[price_frame.index == "Oil boiler"]
            elif component.my_component.config.fuel == LoadTypes.ELECTRICITY:
                column = price_frame.iloc[price_frame.index == "Electric heating"]
            component_capacity = component.my_component.config.power_th * 1e-3
        elif isinstance(component.my_component, generic_hot_water_storage_modular.HotWaterStorage):
            column = price_frame.iloc[price_frame.index == "Hot Water tank"]
            component_capacity = component.my_component.volume
        elif isinstance(component.my_component, advanced_battery_bslib.Battery):
            column = price_frame.iloc[price_frame.index == "Lithium iron phosphate battery"]
            component_capacity = component.my_component.e_bat_custom
        elif isinstance(component.my_component, generic_car.Car):
            if component.my_component.fuel == LoadTypes.ELECTRICITY:
                column = price_frame.iloc[price_frame.index == "Electric vehicle"]
            elif component.my_component.fuel == LoadTypes.DIESEL:
                column = price_frame.iloc[price_frame.index == "Diesel vehicle"]
            component_capacity = 1
        
        else:
            continue
        co2_emissions = co2_emissions + float(column["annual Footprint"]) * component_capacity
        investment_cost = investment_cost + float(column["annual cost"]) * component_capacity

    return investment_cost, co2_emissions