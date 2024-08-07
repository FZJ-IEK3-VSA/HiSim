# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate.

KPis for PV-battery systems in houses:
https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
"""


from typing import List, Dict
from dataclasses import dataclass
from dataclass_wizard import JSONWizard
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.kpi_computation.kpi_preparation import KpiPreparation


@dataclass
class KpiGenerator(JSONWizard, KpiPreparation):
    """Class for generating and calculating key performance indicators."""

    post_processing_data_transfer: PostProcessingDataTransfer
    building_objects_in_district_list: list

    def __post_init__(self):
        """Build the dataclass from input data."""
        super().__init__(post_processing_data_transfer=self.post_processing_data_transfer,building_objects_in_district_list=self.building_objects_in_district_list)

        for building_objects_in_district in self.building_objects_in_district_list:
            self.create_kpi_collection(building_objects_in_district)

        self.kpi_collection_dict_sorted = self.sort_kpi_collection_according_to_kpi_tags(
            kpi_collection_dict_unsorted=self.kpi_collection_dict_unsorted
        )
        self.return_table_for_report()

    def create_kpi_collection(self, building_objects_in_district):
        """Create kpi collection and write back into post processing data transfer."""
        # get filtered result dataframe
        self.filtered_result_dataframe = self.filter_results_according_to_postprocessing_flags(
            all_outputs=self.all_outputs,
            results=self.results,
            building_objects_in_district=building_objects_in_district,
        )

        # get consumption and production and battery kpis
        (
            total_electricity_consumption_in_kilowatt_hour,
            total_electricity_production_in_kilowatt_hour,
            pv_production_in_kilowatt_hour,
        ) = self.compute_electricity_consumption_and_production_and_battery_kpis(
            result_dataframe=self.filtered_result_dataframe, building_objects_in_district=building_objects_in_district
        )

        # get ratio between total production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=total_electricity_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between total production and total consumption",
            building_objects_in_district=building_objects_in_district,
        )
        # get ratio between pv production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=pv_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between PV production and total consumption",
            building_objects_in_district=building_objects_in_district,
        )

        # get self-consumption, autarkie, injection
        (
            grid_injection_in_kilowatt_hour,
            self_consumption_in_kilowatt_hour,
            self.filtered_result_dataframe,
        ) = self.compute_self_consumption_injection_autarky(
            result_dataframe=self.filtered_result_dataframe,
            electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            building_objects_in_district=building_objects_in_district,
        )
        # get electricity to and from grid
        (
            total_electricity_from_grid_in_kwh,
            total_electricity_to_grid_in_kwh,
        ) = self.get_electricity_to_and_from_grid_from_electricty_meter(building_objects_in_district)
        # get relative electricity demand
        relative_electricity_demand_from_grid_in_percent = self.compute_relative_electricity_demand(
            total_electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
            building_objects_in_district=building_objects_in_district,
        )
        # get self-consumption rate according to solar htw berlin
        self.compute_self_consumption_rate_according_to_solar_htw_berlin(
            total_electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_to_grid_in_kilowatt_hour=total_electricity_to_grid_in_kwh,
            building_objects_in_district=building_objects_in_district,
        )
        # get autarky rate according to solar htw berlin
        self.compute_autarky_according_to_solar_htw_berlin(
            relative_electricty_demand_in_percent=relative_electricity_demand_from_grid_in_percent,
            building_objects_in_district=building_objects_in_district,
        )

        # get energy prices and co2 emissions
        self.compute_energy_prices_and_co2_emission(
            result_dataframe=self.filtered_result_dataframe,
            injection=self.filtered_result_dataframe["grid_injection_in_watt"],
            self_consumption=self.filtered_result_dataframe["self_consumption_in_watt"],
            electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            grid_injection_in_kilowatt_hour=grid_injection_in_kilowatt_hour,
            self_consumption_in_kilowatt_hour=self_consumption_in_kilowatt_hour,
            building_objects_in_district=building_objects_in_district,
        )
        # get capex and opex costs
        self.read_opex_and_capex_costs_from_results(building_object=building_objects_in_district)

    def return_table_for_report(self):
        """Return a table with all kpis for the report."""
        table: List = []
        table.append(["Object", "KPI", "Value", "Unit"])
        for building_object in self.building_objects_in_district_list:
            for kpi_tag, kpi_entries in self.kpi_collection_dict_sorted[building_object].items():
                table.append([f"{building_object}", f"{kpi_tag}", "", ""])
                table.append(["--------", "--------------------", "", ""])
                for kpi_name, kpi_entry in kpi_entries.items():
                    value = kpi_entry["value"]
                    if value is not None:
                        value = round(value, 2)
                    unit = kpi_entry["unit"]
                    table.append([f"{building_object}", f"{kpi_name}: ", f"{value}", f"{unit}"])
                table.append(["\n", "\n", "\n", "\n"])

        #  Insert line break for report
        wrapped_table_data = []
        for row in table:
            wrapped_row = []
            for cell in row:
                wrapped_cell = "\n".join([cell[i : i + 80] for i in range(0, len(cell), 80)])
                wrapped_row.append(wrapped_cell)
            wrapped_table_data.append(wrapped_row)

        return wrapped_table_data

    def sort_kpi_collection_according_to_kpi_tags(self, kpi_collection_dict_unsorted: Dict) -> Dict:
        """Sort KPI collection dict according to KPI tags."""

        # kpi_collection_dict_sorted: Dict[str, Dict] = {}

        kpi_collection_dict_sorted: Dict[str, Dict] = {building_objects: {} for building_objects in self.building_objects_in_district_list}

        for building_object in self.building_objects_in_district_list:
            # get all tags and use as keys for sorted kpi dict
            for entry in kpi_collection_dict_unsorted[building_object].values():
                kpi_collection_dict_sorted[building_object].update({entry["tag"]: {}})

            # now sort kpi dict entries according to tags
            for kpi_name, entry in kpi_collection_dict_unsorted[building_object].items():
                for tag, tag_dict in kpi_collection_dict_sorted[building_object].items():
                    if entry["tag"] in tag:
                        tag_dict.update({kpi_name: entry})

        return kpi_collection_dict_sorted
