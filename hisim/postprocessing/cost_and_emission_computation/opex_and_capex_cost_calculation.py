"""Calculate Opex and Capex for each component."""

import os
from typing import List

import pandas as pd
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.gas_meter import GasMeter
from hisim.components.heating_meter import HeatingMeter
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib
from hisim.components.generic_heat_pump_modular import ModularHeatPump
from hisim.components.simple_heat_source import SimpleHeatSource


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
            if (
                building_object in str(component_unwrapped.component_name)
                or not simulation_parameters.multiple_buildings
            ):
                # cost and co2_footprint are calculated per simulated period
                component_class_name = component_unwrapped.get_classname()
                # get opex data for all components except Electricity and Gas Meter
                if (
                    component_class_name != ElectricityMeter.get_classname()
                    and component_class_name != GasMeter.get_classname()
                    and component_class_name != HeatingMeter.get_classname()
                ):
                    opex_cost_data_class: OpexCostDataClass = component_unwrapped.get_cost_opex(
                        all_outputs=all_outputs,
                        postprocessing_results=postprocessing_results,
                    )
                    cost_energy = opex_cost_data_class.opex_energy_cost_in_euro
                    cost_maintenance = opex_cost_data_class.opex_maintenance_cost_in_euro
                    co2_footprint = opex_cost_data_class.co2_footprint_in_kg
                    consumption = opex_cost_data_class.total_consumption_in_kwh
                    total_energy_cost_building_object += cost_energy
                    total_maintenance_cost_building_object += cost_maintenance
                    total_operational_co2_footprint_building_object += co2_footprint
                    total_consumption_in_kwh_building_object += consumption

                    if isinstance(
                        component_unwrapped,
                        (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib, SimpleHeatSource),
                    ):
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
        if simulation_parameters.multiple_buildings:
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

            opex_table_as_list_of_list.append(
                [
                    f"{building_object}_Total_only_heatpump",
                    round(
                        total_maintenance_cost_building_object - total_maintenance_cost_building_object_without_hp, 2
                    ),
                    round(
                        total_operational_co2_footprint_building_object
                        - total_operational_co2_footprint_building_object_without_hp,
                        2,
                    ),
                    round(
                        total_consumption_in_kwh_building_object - total_consumption_in_kwh_building_object_without_hp,
                        2,
                    ),
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
    opex_table_as_list_of_list.append(
        [
            "Total_only_heatpump",
            round(total_opex_maintenance_cost - total_opex_maintenance_cost_without_hp, 2),
            round(total_operational_co2_footprint - total_operational_co2_footprint_without_hp, 2),
            round(total_consumption_in_kwh - total_consumption_in_kwh_without_hp, 2),
        ]
    )

    pathname = os.path.join(simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    opex_df = pd.DataFrame(opex_table_as_list_of_list, columns=headline)
    opex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    opex_table_as_list_of_list.insert(0, headline)

    return opex_table_as_list_of_list


def prepare_row_for_writing_to_table(row_name: str, dict_with_values: dict):
    """Write row to table."""
    value_list = list(dict_with_values.values())
    return [row_name] + value_list


def capex_calculation(
    components: List[ComponentWrapper],
    simulation_parameters: SimulationParameters,
    building_objects_in_district_list: list,
) -> List:
    """Loops over all components and returns capex summary table."""

    headline = [
        "Component",
        "Investment [EUR]",
        "Device CO2-footprint [kg]",
        "Subsidy as percentage of investment [-]",
        "Rest-Investment [EUR]",
        "Lifetime [Years]",
        "Investment for simulated period [EUR]",
        "Rest-Investment for simulated period [EUR]",
        "Device CO2-footprint for simulated period [kg]",
    ]

    capex_rows = []
    # check if heatpumps were installed in buildings
    heat_pump_involved: bool = any(
        isinstance(comp.my_component, (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib, SimpleHeatSource))
        for comp in components
    )

    total_summary = {
        "all_components": {
            "investment": 0,
            "co2": 0,
            "subsidy": "---",
            "rest_investment": 0,
            "lifetime": "---",
            "investment_period": 0,
            "rest_investment_period": 0,
            "co2_period": 0,
        },
        "without_hp": {
            "investment": 0,
            "co2": 0,
            "subsidy": "---",
            "rest_investment": 0,
            "lifetime": "---",
            "investment_period": 0,
            "rest_investment_period": 0,
            "co2_period": 0,
        },
    }

    for building_object in building_objects_in_district_list:
        totals_per_building = {
            "all_components": {
                "investment": 0,
                "co2": 0,
                "subsidy": "---",
                "rest_investment": 0,
                "lifetime": "---",
                "investment_period": 0,
                "rest_investment_period": 0,
                "co2_period": 0,
            },
            "without_hp": {
                "investment": 0,
                "co2": 0,
                "subsidy": "---",
                "rest_investment": 0,
                "lifetime": "---",
                "investment_period": 0,
                "rest_investment_period": 0,
                "co2_period": 0,
            },
        }

        for component in components:
            component_unwrapped = component.my_component

            if (
                building_object in str(component_unwrapped.component_name)
                or not simulation_parameters.multiple_buildings
            ):
                capex: CapexCostDataClass = component_unwrapped.get_cost_capex(
                    config=component_unwrapped.config, simulation_parameters=simulation_parameters
                )

                if capex.lifetime_in_years <= 0:
                    log.warning(f"Invalid lifetime in {component_unwrapped.component_name}, skipping entry.")
                    continue

                investment = round(capex.capex_investment_cost_in_euro, 2)
                co2 = round(capex.device_co2_footprint_in_kg, 2)
                subsidy_pct = round(capex.subsidy_as_percentage_of_investment_costs, 2)
                rest_investment = round(investment * (1 - subsidy_pct), 2)
                lifetime = round(capex.lifetime_in_years, 2)
                investment_period = round(capex.capex_investment_cost_for_simulated_period_in_euro, 2)
                rest_investment_period = round(investment_period * (1 - subsidy_pct), 2)
                co2_period = round(capex.device_co2_footprint_for_simulated_period_in_kg, 2)

                # Add to total and subtotal
                for group in ["all_components", "without_hp"]:
                    if group == "without_hp" and isinstance(
                        component_unwrapped,
                        (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib, SimpleHeatSource),
                    ):
                        continue
                    totals_per_building[group]["investment"] += investment
                    totals_per_building[group]["co2"] += co2
                    totals_per_building[group]["rest_investment"] += rest_investment
                    totals_per_building[group]["investment_period"] += investment_period
                    totals_per_building[group]["rest_investment_period"] += rest_investment_period
                    totals_per_building[group]["co2_period"] += co2_period

                capex_rows.append(
                    [
                        component_unwrapped.component_name,
                        investment,
                        co2,
                        subsidy_pct,
                        rest_investment,
                        lifetime,
                        investment_period,
                        rest_investment_period,
                        co2_period,
                    ]
                )

        if simulation_parameters.multiple_buildings:
            # Insert subtotal rows per building
            # Add extra rows if heatpumps were involved
            if heat_pump_involved:
                only_heatpump_dict: dict = {
                    k: (
                        round(totals_per_building["all_components"][k] - totals_per_building["without_hp"].get(k, 0), 2)
                        if isinstance(totals_per_building["all_components"][k], (int, float))
                        else "---"
                    )
                    for k in totals_per_building["all_components"]
                }
                capex_rows.extend(
                    [
                        prepare_row_for_writing_to_table(
                            row_name=f"{building_object}_Total_without_heatpump",
                            dict_with_values=totals_per_building["without_hp"],
                        ),
                        prepare_row_for_writing_to_table(
                            row_name=f"{building_object}_Total_only_heatpump", dict_with_values=only_heatpump_dict
                        ),
                    ]
                )
            # Total values per building
            capex_rows.extend(
                [
                    prepare_row_for_writing_to_table(
                        row_name=f"{building_object}_Total", dict_with_values=totals_per_building["all_components"]
                    )
                ]
            )

        # Summarize total values
        for group in total_summary:  # pylint: disable=consider-using-dict-items
            for key in total_summary[group]:
                value = totals_per_building[group][key]
                if isinstance(value, float):
                    value = round(value, 2)
                elif isinstance(value, str):
                    continue
                total_summary[group][key] += value

    # Final total rows
    # Add extra rows if heatpumps were involved
    if heat_pump_involved:
        only_heatpump_dict: dict = {
            k: (
                round(total_summary["all_components"][k] - total_summary["without_hp"].get(k, 0), 2)
                if isinstance(total_summary["all_components"][k], (int, float))
                else "---"
            )
            for k in total_summary["all_components"]
        }

        capex_rows.extend(
            [
                ["---", "---", "---", "---", "---", "---", "---", "---", "---"],
                prepare_row_for_writing_to_table(
                    row_name="Total_without_heatpump", dict_with_values=total_summary["without_hp"]
                ),
                prepare_row_for_writing_to_table(row_name="Total_only_heatpump", dict_with_values=only_heatpump_dict),
            ]
        )
    # Total values
    capex_rows.extend(
        [
            ["---", "---", "---", "---", "---", "---", "---", "---", "---"],
            prepare_row_for_writing_to_table(row_name="Total", dict_with_values=total_summary["all_components"]),
        ]
    )

    # Export to CSV
    pathname = os.path.join(simulation_parameters.result_directory, "investment_cost_co2_footprint.csv")
    capex_df = pd.DataFrame(capex_rows, columns=headline)
    capex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    return [headline] + capex_rows
