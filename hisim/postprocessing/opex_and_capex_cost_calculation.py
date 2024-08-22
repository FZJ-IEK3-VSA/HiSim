"""Calculate Opex and Capex for each component."""

import os
from typing import List

import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper
from hisim.component import OpexCostDataClass
from hisim.components.advanced_battery_bslib import Battery
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.gas_meter import GasMeter
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib
from hisim.components.generic_heat_pump_modular import ModularHeatPump


def opex_calculation(
    components: List[ComponentWrapper],
    all_outputs: List,
    postprocessing_results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
    building_objects_in_district_list: list,
) -> List:
    """Loops over all components and calls opex cost calculation."""
    total_operational_co2_footprint = 0.0
    total_opex_energy_cost = 0.0
    total_opex_maintenance_cost = 0.0
    total_consumption_in_kwh = 0.0
    total_operational_co2_footprint_without_hp = 0.0
    total_opex_energy_cost_without_hp = 0.0
    total_opex_maintenance_cost_without_hp = 0.0
    total_consumption_in_kwh_without_hp = 0.0

    headline: List[object] = [
        "Component",
        "Maintenance Costs in EUR",
        "Fuel CO2 Emissions in kg",
        "Consumption in kWh",
    ]
    opex_table_as_list_of_list = []

    for building_object in building_objects_in_district_list:
        total_energy_cost_building_object = 0.0
        total_maintenance_cost_building_object = 0.0
        total_operational_co2_footprint_building_object = 0.0
        total_consumption_in_kwh_building_object = 0.0
        total_energy_cost_building_object_without_hp = 0.0
        total_maintenance_cost_building_object_without_hp = 0.0
        total_operational_co2_footprint_building_object_without_hp = 0.0
        total_consumption_in_kwh_building_object_without_hp = 0.0

        for component in components:
            component_unwrapped = component.my_component
            if building_object in str(component_unwrapped.component_name):
                # cost and co2_footprint are calculated per simulated period
                component_class_name = component_unwrapped.get_classname()
                # get opex data for all components except Electricity and Gas Meter
                if component_class_name != ElectricityMeter.get_classname() and component_class_name != GasMeter.get_classname():
                    opex_cost_data_class: OpexCostDataClass = component_unwrapped.get_cost_opex(
                        all_outputs=all_outputs,
                        postprocessing_results=postprocessing_results,
                    )
                    cost_energy = opex_cost_data_class.opex_energy_cost_in_euro
                    cost_maintenance = opex_cost_data_class.opex_maintenance_cost_in_euro
                    co2_footprint = opex_cost_data_class.co2_footprint_in_kg
                    consumption = opex_cost_data_class.consumption_in_kwh
                    total_energy_cost_building_object += cost_energy
                    total_maintenance_cost_building_object += cost_maintenance
                    total_operational_co2_footprint_building_object += co2_footprint
                    total_consumption_in_kwh_building_object += consumption

                    if isinstance(component_unwrapped, (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib)):
                        pass
                    else:
                        total_energy_cost_building_object_without_hp += cost_energy
                        total_maintenance_cost_building_object_without_hp += cost_maintenance
                        total_operational_co2_footprint_building_object_without_hp += co2_footprint
                        total_consumption_in_kwh_building_object_without_hp += consumption

                    opex_table_as_list_of_list.append(
                        [
                            component_unwrapped.component_name,
                            round(cost_maintenance, 2),
                            round(co2_footprint, 2),
                            round(consumption, 2),
                        ]
                    )

        opex_table_as_list_of_list.append(
            [
                f"{building_object}_Total",
                round(total_maintenance_cost_building_object, 2),
                round(total_operational_co2_footprint_building_object, 2),
                round(total_consumption_in_kwh_building_object, 2),
            ]
        )

        opex_table_as_list_of_list.append(
            [
                f"{building_object}_Total_without_heatpump",
                round(total_maintenance_cost_building_object_without_hp, 2),
                round(total_operational_co2_footprint_building_object_without_hp, 2),
                round(total_consumption_in_kwh_building_object_without_hp, 2),
            ]
        )

        total_opex_energy_cost += total_energy_cost_building_object
        total_opex_maintenance_cost += total_maintenance_cost_building_object
        total_operational_co2_footprint += total_operational_co2_footprint_building_object
        total_consumption_in_kwh += total_consumption_in_kwh_building_object

        total_opex_energy_cost_without_hp += total_energy_cost_building_object_without_hp
        total_opex_maintenance_cost_without_hp += total_maintenance_cost_building_object_without_hp
        total_operational_co2_footprint_without_hp += total_operational_co2_footprint_building_object_without_hp
        total_consumption_in_kwh_without_hp += total_consumption_in_kwh_building_object_without_hp

    opex_table_as_list_of_list.append(
        [
            "Total",
            round(total_opex_maintenance_cost, 2),
            round(total_operational_co2_footprint, 2),
            round(total_consumption_in_kwh, 2),
        ]
    )
    opex_table_as_list_of_list.append(
        [
            "Total_without_heatpump",
            round(total_opex_maintenance_cost_without_hp, 2),
            round(total_operational_co2_footprint_without_hp, 2),
            round(total_consumption_in_kwh_without_hp, 2),
        ]
    )
    pathname = os.path.join(simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    opex_df = pd.DataFrame(opex_table_as_list_of_list, columns=headline)
    opex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    opex_table_as_list_of_list.insert(0, headline)

    return opex_table_as_list_of_list


def capex_calculation(
    components: List[ComponentWrapper],
    simulation_parameters: SimulationParameters,
    building_objects_in_district_list: list,
) -> List:
    """Loops over all components and calls capex cost calculation."""
    seconds_per_year = 365 * 24 * 60 * 60
    total_investment_cost = 0.0
    total_device_co2_footprint = 0.0
    total_investment_cost_per_simulated_period = 0.0
    total_device_co2_footprint_per_simulated_period = 0.0
    total_investment_cost_without_hp = 0.0
    total_device_co2_footprint_without_hp = 0.0
    total_investment_cost_per_simulated_period_without_hp = 0.0
    total_device_co2_footprint_per_simulated_period_without_hp = 0.0

    headline: List[object] = [
        "Component",
        "Investment in EUR",
        "Device CO2-footprint in kg",
        "Lifetime in years",
    ]
    capex_table_as_list_of_list = []

    for building_object in building_objects_in_district_list:
        total_investment_cost_building_object = 0.0
        total_device_co2_footprint_building_object = 0.0
        total_investment_cost_per_simulated_period_building_object = 0.0
        total_device_co2_footprint_per_simulated_period_building_object = 0.0
        total_investment_cost_building_object_without_hp = 0.0
        total_device_co2_footprint_building_object_without_hp = 0.0
        total_investment_cost_per_simulated_period_building_object_without_hp = 0.0
        total_device_co2_footprint_per_simulated_period_building_object_without_hp = 0.0

        for component in components:
            component_unwrapped = component.my_component
            if building_object in str(component_unwrapped.component_name):
                capex, co2_footprint, lifetime = component_unwrapped.get_cost_capex(
                    config=component_unwrapped.config,
                )
                if lifetime > 0:
                    # lifetime is per default set to 1.0 in class cp.Component to avoid devide by zero error

                    # battery costs and emissions are calculated per used cycles not per simulation period  # better aproximation of aging
                    if isinstance(component_unwrapped, Battery) and hasattr(
                        component_unwrapped, "get_battery_aging_information"
                    ):
                        (
                            virtual_number_of_full_charge_cycles,
                            lifetime_in_cycles,
                        ) = component_unwrapped.get_battery_aging_information()
                        if lifetime_in_cycles > 0:
                            capex_per_simulated_period_building_object = (capex / lifetime_in_cycles) * (
                                virtual_number_of_full_charge_cycles
                            )
                            device_co2_footprint_per_simulated_period_building_object = (
                                co2_footprint / lifetime_in_cycles
                            ) * (virtual_number_of_full_charge_cycles)
                        else:
                            log.warning(
                                f"capex calculation not valid. Check lifetime_in_cycles in Configuration of {component.my_component}"
                            )
                    else:
                        capex_per_simulated_period_building_object = (capex / lifetime) * (
                            simulation_parameters.duration.total_seconds() / seconds_per_year
                        )
                        device_co2_footprint_per_simulated_period_building_object = (co2_footprint / lifetime) * (
                            simulation_parameters.duration.total_seconds() / seconds_per_year
                        )
                    total_investment_cost_building_object += capex
                    total_device_co2_footprint_building_object += co2_footprint
                    total_investment_cost_per_simulated_period_building_object += (
                        capex_per_simulated_period_building_object
                    )
                    total_device_co2_footprint_per_simulated_period_building_object += (
                        device_co2_footprint_per_simulated_period_building_object
                    )

                    if isinstance(component_unwrapped, (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib)):
                        pass
                    else:
                        total_investment_cost_building_object_without_hp += capex
                        total_device_co2_footprint_building_object_without_hp += co2_footprint
                        total_investment_cost_per_simulated_period_building_object_without_hp += capex_per_simulated_period_building_object
                        total_device_co2_footprint_per_simulated_period_building_object_without_hp += device_co2_footprint_per_simulated_period_building_object

                    capex_table_as_list_of_list.append(
                        [
                            component_unwrapped.component_name,
                            round(capex_per_simulated_period_building_object, 2),
                            round(device_co2_footprint_per_simulated_period_building_object, 2),
                            lifetime,
                        ]
                    )

                else:
                    log.warning(
                        f"capex calculation not valid. Check lifetime in Configuration of {component.my_component}"
                    )

        capex_table_as_list_of_list.append(
            [
                f"{building_object}_Total_per_simulated_period_without_heatpump",
                round(total_investment_cost_per_simulated_period_building_object_without_hp, 2),
                round(total_device_co2_footprint_per_simulated_period_building_object_without_hp, 2),
                "---",
            ]
        )
        capex_table_as_list_of_list.append(
            [
                f"{building_object}_Total_per_simulated_period",
                round(total_investment_cost_per_simulated_period_building_object, 2),
                round(total_device_co2_footprint_per_simulated_period_building_object, 2),
                "---",
            ]
        )
        capex_table_as_list_of_list.append(
            [
                f"{building_object}_Total_without_heatpump",
                round(total_investment_cost_building_object_without_hp, 2),
                round(total_device_co2_footprint_building_object_without_hp, 2),
                "---",
            ]
        )
        capex_table_as_list_of_list.append(
            [
                f"{building_object}_Total",
                round(total_investment_cost_building_object, 2),
                round(total_device_co2_footprint_building_object, 2),
                "---",
            ]
        )

        total_investment_cost += total_investment_cost_building_object
        total_device_co2_footprint += total_device_co2_footprint_building_object
        total_investment_cost_per_simulated_period += total_investment_cost_per_simulated_period_building_object
        total_device_co2_footprint_per_simulated_period += (
            total_device_co2_footprint_per_simulated_period_building_object
        )

        total_investment_cost_without_hp += total_investment_cost_building_object_without_hp
        total_device_co2_footprint_without_hp += total_device_co2_footprint_building_object_without_hp
        total_investment_cost_per_simulated_period_without_hp += (
            total_investment_cost_per_simulated_period_building_object_without_hp)
        total_device_co2_footprint_per_simulated_period_without_hp += (
            total_device_co2_footprint_per_simulated_period_building_object_without_hp
        )

    capex_table_as_list_of_list.append(
        [
            "Total_per_simulated_period",
            round(total_investment_cost_per_simulated_period, 2),
            round(total_device_co2_footprint_per_simulated_period, 2),
            "---",
        ]
    )
    capex_table_as_list_of_list.append(
        [
            "Total_per_simulated_period_without_heatpump",
            round(total_investment_cost_per_simulated_period_without_hp, 2),
            round(total_device_co2_footprint_per_simulated_period_without_hp, 2),
            "---",
        ]
    )
    capex_table_as_list_of_list.append(
        [
            "Total",
            round(total_investment_cost, 2),
            round(total_device_co2_footprint, 2),
            "---",
        ]
    )
    capex_table_as_list_of_list.append(
        [
            "Total_without_heatpump",
            round(total_investment_cost_without_hp, 2),
            round(total_device_co2_footprint_without_hp, 2),
            "---",
        ]
    )

    pathname = os.path.join(simulation_parameters.result_directory, "investment_cost_co2_footprint.csv")
    capex_df = pd.DataFrame(capex_table_as_list_of_list, columns=headline)
    capex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    capex_table_as_list_of_list.insert(0, headline)

    return capex_table_as_list_of_list
