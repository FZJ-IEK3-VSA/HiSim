"""Calculate Opex and Capex for each component."""

import os
from typing import List, Dict, Union
from dataclasses import asdict
import pandas as pd

from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.component_wrapper import ComponentWrapper
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.gas_meter import GasMeter
from hisim.components.heating_meter import HeatingMeter
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib
from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib
from hisim.components.generic_heat_pump_modular import ModularHeatPump
from hisim.components.simple_heat_source import SimpleHeatSource


def prepare_row_for_writing_to_table(row_name: str, dict_with_values: dict):
    """Write row to table."""
    value_list = list(dict_with_values.values())
    return [row_name] + value_list


def opex_calculation(
    components: List[ComponentWrapper],
    all_outputs: List,
    postprocessing_results: pd.DataFrame,
    simulation_parameters: SimulationParameters,
    building_objects_in_district_list: list,
) -> List:
    """Loops over all components and calls opex cost calculation."""
    headline = [
        "Component",
        "Total energy consumption [kWh]",
        "CO2-emissions of energy consumption [kg]",
        "Costs of energy consumption [EUR]",
        "Maintenance costs per year [EUR]",
    ]

    opex_rows = []

    total_summary: Dict[Dict[float]] = {
        "all_components": {
            "consumption": 0.0,
            "co2_emissions": 0.0,
            "energy_cost": 0.0,
            "maintenance": 0.0,
        },
        "without_hp": {
            "consumption": 0.0,
            "co2_emissions": 0.0,
            "energy_cost": 0.0,
            "maintenance": 0.0,
        },
    }

    for building_object in building_objects_in_district_list:
        totals_per_building: Dict[Dict[float]] = {
            "all_components": {
                "consumption": 0.0,
                "co2_emissions": 0.0,
                "energy_cost": 0.0,
                "maintenance": 0.0,
            },
            "without_hp": {
                "consumption": 0.0,
                "co2_emissions": 0.0,
                "energy_cost": 0.0,
                "maintenance": 0.0,
            },
        }

        meter_rows: List = []
        for component in components:
            component_unwrapped = component.my_component

            if (
                building_object in str(component_unwrapped.component_name)
                or not simulation_parameters.multiple_buildings
            ):
                opex: OpexCostDataClass = component_unwrapped.get_cost_opex(
                    all_outputs=all_outputs,
                    postprocessing_results=postprocessing_results,
                )
                # filter out none type values
                if any(v is None for v in asdict(opex).values()):
                    log.debug(
                        f"Component {component_unwrapped.component_name} has None opex value and will therefore be skipped."
                    )
                    log.debug(str(opex))
                    continue

                energy_consumption = round(opex.total_consumption_in_kwh, 2)
                co2 = round(opex.co2_footprint_in_kg, 2)
                energy_costs = round(opex.opex_energy_cost_in_euro, 2)
                maintenance = round(opex.opex_maintenance_cost_in_euro, 2)

                # Add to total and subtotal
                for group in ["all_components", "without_hp"]:
                    is_heat_pump = isinstance(
                        component_unwrapped,
                        (HeatPumpHplib, ModularHeatPump, MoreAdvancedHeatPumpHPLib, SimpleHeatSource),
                    )
                    is_meter = isinstance(
                        component_unwrapped,
                        (ElectricityMeter, GasMeter, HeatingMeter, L2GenericEnergyManagementSystem),
                    )

                    # Skip heat pumps for "without_hp"
                    if group == "without_hp" and is_heat_pump:
                        continue

                    # Skip meter values because these have the other consumptions already integrated
                    if is_meter:
                        continue

                    # Add values to the appropriate group
                    totals_per_building[group]["consumption"] += energy_consumption
                    totals_per_building[group]["co2_emissions"] += co2
                    totals_per_building[group]["energy_cost"] += energy_costs
                    totals_per_building[group]["maintenance"] += maintenance

                # Write component opex values to table
                component_row = [component_unwrapped.component_name, energy_consumption, co2, energy_costs, maintenance]

                if not is_meter:
                    opex_rows.append(component_row)
                else:
                    meter_rows.append(component_row)

        if simulation_parameters.multiple_buildings:
            # Insert subtotal rows per building

            only_heatpump_dict: dict = {
                k: (
                    round(totals_per_building["all_components"][k] - totals_per_building["without_hp"].get(k, 0), 2)
                    if isinstance(totals_per_building["all_components"][k], (int, float))
                    else None
                )
                for k in totals_per_building["all_components"]
            }
            opex_rows.extend(
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
            opex_rows.extend(
                [
                    [] * len(headline),
                    prepare_row_for_writing_to_table(
                        row_name=f"{building_object}_Total", dict_with_values=totals_per_building["all_components"]
                    ),
                ]
            )
            # Add meter values at the end
            for meter_row in meter_rows:
                opex_rows.extend([meter_row])

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

    only_heatpump_dict = {
        k: (
            round(total_summary["all_components"][k] - total_summary["without_hp"].get(k, 0), 2)
            if isinstance(total_summary["all_components"][k], (int, float))
            else None
        )
        for k in total_summary["all_components"]
    }

    opex_rows.extend(
        [
            [] * len(headline),
            prepare_row_for_writing_to_table(
                row_name="Total_without_heatpump", dict_with_values=total_summary["without_hp"]
            ),
            prepare_row_for_writing_to_table(row_name="Total_only_heatpump", dict_with_values=only_heatpump_dict),
        ]
    )
    # Total values
    opex_rows.extend(
        [
            [] * len(headline),
            prepare_row_for_writing_to_table(row_name="Total", dict_with_values=total_summary["all_components"]),
        ]
    )

    # Add meter values at the end
    opex_rows.extend([[] * len(headline)])
    for meter_row in meter_rows:
        opex_rows.extend([meter_row])

    # Export to CSV
    pathname = os.path.join(simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")
    opex_df = pd.DataFrame(opex_rows, columns=headline)
    opex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    return [headline] + opex_rows


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

    total_summary: Dict[Dict[Union[float, str]]] = {
        "all_components": {
            "investment": 0.0,
            "co2": 0.0,
            "subsidy": None,
            "rest_investment": 0.0,
            "lifetime": None,
            "investment_period": 0.0,
            "rest_investment_period": 0.0,
            "co2_period": 0.0,
        },
        "without_hp": {
            "investment": 0.0,
            "co2": 0.0,
            "subsidy": None,
            "rest_investment": 0.0,
            "lifetime": None,
            "investment_period": 0.0,
            "rest_investment_period": 0.0,
            "co2_period": 0.0,
        },
    }

    for building_object in building_objects_in_district_list:
        totals_per_building: Dict[Dict[Union[float, str]]] = {
            "all_components": {
                "investment": 0.0,
                "co2": 0.0,
                "subsidy": None,
                "rest_investment": 0.0,
                "lifetime": None,
                "investment_period": 0.0,
                "rest_investment_period": 0.0,
                "co2_period": 0.0,
            },
            "without_hp": {
                "investment": 0.0,
                "co2": 0.0,
                "subsidy": None,
                "rest_investment": 0.0,
                "lifetime": None,
                "investment_period": 0.0,
                "rest_investment_period": 0.0,
                "co2_period": 0.0,
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
                # filter out none type values
                if any(v is None for v in asdict(capex).values()):
                    log.debug(
                        f"Component {component_unwrapped.component_name} has None capex value and will therefore be skipped."
                    )
                    log.debug(str(capex))
                    continue

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

            only_heatpump_dict = {
                k: (
                    round(totals_per_building["all_components"][k] - totals_per_building["without_hp"].get(k, 0), 2)
                    if isinstance(totals_per_building["all_components"][k], (int, float))
                    else None
                )
                for k in totals_per_building["all_components"]
            }
            capex_rows.extend(
                [
                    [] * len(headline),
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
                    [] * len(headline),
                    prepare_row_for_writing_to_table(
                        row_name=f"{building_object}_Total", dict_with_values=totals_per_building["all_components"]
                    ),
                ]
            )

        # Summarize total values
        for group in total_summary:  # pylint: disable=consider-using-dict-items
            for key in total_summary[group]:
                value = totals_per_building[group][key]
                if isinstance(value, float):
                    value = round(value, 2)
                else:
                    continue
                total_summary[group][key] += value

    # Final total rows
    only_heatpump_dict = {
        k: (
            round(total_summary["all_components"][k] - total_summary["without_hp"].get(k, 0), 2)
            if isinstance(total_summary["all_components"][k], (int, float))
            else None
        )
        for k in total_summary["all_components"]
    }

    capex_rows.extend(
        [
            [] * len(headline),
            prepare_row_for_writing_to_table(
                row_name="Total_without_heatpump", dict_with_values=total_summary["without_hp"]
            ),
            prepare_row_for_writing_to_table(row_name="Total_only_heatpump", dict_with_values=only_heatpump_dict),
        ]
    )
    # Total values
    capex_rows.extend(
        [
            [] * len(headline),
            prepare_row_for_writing_to_table(row_name="Total", dict_with_values=total_summary["all_components"]),
        ]
    )

    # Export to CSV
    pathname = os.path.join(simulation_parameters.result_directory, "investment_cost_co2_footprint.csv")
    capex_df = pd.DataFrame(capex_rows, columns=headline)
    capex_df.to_csv(pathname, index=False, header=True, encoding="utf8", sep=";")

    return [headline] + capex_rows
