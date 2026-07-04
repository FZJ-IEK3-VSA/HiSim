"""Postprocessing: computes investment cost and CO2 footprint of technical equipment.

Functions from this file are called in Postprocessing option compute_kpis.
"""

# clean
from functools import lru_cache
from typing import List, NamedTuple, Optional
import pandas as pd
from hisim.components import (
    generic_hot_water_storage_modular,
    generic_pv_system,
    generic_smart_device,
    generic_heat_source,
    advanced_battery_bslib,
    generic_car,
    generic_chp,
    generic_hydrogen_storage,
    generic_electrolyzer,
)
from hisim.utils import HISIMPATH
from hisim.loadtypes import LoadTypes
from hisim.component_wrapper import ComponentWrapper


class InvestmentCostResult(NamedTuple):
    """Result of :func:`compute_investment_cost`.

    A :class:`NamedTuple` subclass of :class:`tuple`, so existing positional
    unpacking (``cost, co2 = compute_investment_cost(...)``) keeps working
    while also exposing the values by name for readable call sites.
    """

    investment_cost: float
    co2_emissions: float


@lru_cache(maxsize=1)
def read_in_component_costs() -> pd.DataFrame:
    """Reads data for cost and co2 emissions of component installation/investment from csv.

    :return: DataFrame with price and co2 footprint information of all relevant components.
    :rtype: pd.DataFrame
    """
    price_frame = pd.read_csv(HISIMPATH["component_costs"], sep=";", usecols=[0, 8, 9])
    price_frame.index = price_frame["Product/service"]  # type: ignore
    price_frame.drop(columns=["Product/service"], inplace=True)
    return price_frame


def compute_investment_cost(
    components: List[ComponentWrapper],
    price_frame: Optional[pd.DataFrame] = None,
) -> InvestmentCostResult:
    """Iterates over all components and computes annual investment cost and annual C02 footprint respectively.

    :param components: List of all configured components in the HiSIM system setup.
    :type components: List[ComponentWrapper]
    :param price_frame: optional DataFrame with price and co2 footprint information.
        When None (the default for production callers) the data is read from
        the component_costs CSV via read_in_component_costs(). Tests can
        pass a small synthetic DataFrame to exercise individual branches without
        relying on a real CSV file on disk.
    :type price_frame: Optional[pd.DataFrame]
    :return: annual investment cost for considered equipment and annual C02 footprint.
    :rtype: InvestmentCostResult
    """
    # initialize values
    investment_cost = 0.0
    co2_emissions = 0.0
    price_frame = price_frame if price_frame is not None else read_in_component_costs()

    for component in components:
        if isinstance(component.my_component, generic_smart_device.SmartDevice):
            component_price_row = price_frame.iloc[price_frame.index == "Washing machine (or domestic appliances in general)"]
            component_capacity = 1.0
        elif isinstance(component.my_component, generic_pv_system.PVSystem):
            component_price_row = price_frame.iloc[price_frame.index == "Photovoltaic panel"]
            component_capacity = component.my_component.pvconfig.power_in_watt * 1e-3
        elif isinstance(component.my_component, generic_heat_source.HeatSource):
            if component.my_component.config.fuel == LoadTypes.DISTRICTHEATING:
                component_price_row = price_frame.iloc[price_frame.index == "Biomass district heating system"]
            elif component.my_component.config.fuel == LoadTypes.GAS:
                component_price_row = price_frame.iloc[price_frame.index == "Gas boiler"]
            elif component.my_component.config.fuel == LoadTypes.OIL:
                component_price_row = price_frame.iloc[price_frame.index == "Oil boiler"]
            elif component.my_component.config.fuel == LoadTypes.ELECTRICITY:
                component_price_row = price_frame.iloc[price_frame.index == "Electric heating"]
            component_capacity = component.my_component.config.power_th * 1e-3
        elif isinstance(component.my_component, generic_hot_water_storage_modular.HotWaterStorage):
            component_price_row = price_frame.iloc[price_frame.index == "Hot Water tank"]
            component_capacity = component.my_component.volume
        elif isinstance(component.my_component, advanced_battery_bslib.Battery):
            component_price_row = price_frame.iloc[price_frame.index == "Lithium iron phosphate battery"]
            component_capacity = component.my_component.custom_battery_capacity_generic_in_kilowatt_hour
        elif isinstance(component.my_component, generic_car.Car):
            if component.my_component.config.fuel == LoadTypes.ELECTRICITY:
                component_price_row = price_frame.iloc[price_frame.index == "Electric vehicle"]
            elif component.my_component.config.fuel == LoadTypes.DIESEL:
                component_price_row = price_frame.iloc[price_frame.index == "Diesel vehicle"]
            component_capacity = 1.0
        elif isinstance(component.my_component, generic_chp.SimpleCHP):
            if component.my_component.config.use == LoadTypes.GAS:
                component_price_row = price_frame.iloc[price_frame.index == "Gas powered Combined Heat and Power"]
            elif component.my_component.config.use == LoadTypes.GREEN_HYDROGEN:
                component_price_row = price_frame.iloc[price_frame.index == "Hydrogen fuelcell"]
            component_capacity = component.my_component.config.p_fuel * 1e-3
        elif isinstance(component.my_component, generic_hydrogen_storage.GenericHydrogenStorage):
            component_price_row = price_frame.iloc[price_frame.index == "Hydrogen Storage"]
            component_capacity = component.my_component.config.max_capacity_in_kg
        elif isinstance(component.my_component, generic_electrolyzer.GenericElectrolyzer):
            component_price_row = price_frame.iloc[price_frame.index == "Electrolyzer"]
            component_capacity = component.my_component.config.max_power * 1e-3
        else:
            continue
        co2_emissions = co2_emissions + float(component_price_row["annual Footprint"].iloc[0]) * component_capacity
        investment_cost = investment_cost + float(component_price_row["annual cost"].iloc[0]) * component_capacity

    return InvestmentCostResult(investment_cost=investment_cost, co2_emissions=co2_emissions)
