# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and self-sufficiency rate.

KPis for PV-battery systems in houses:
https://solar.htw-berlin.de/wp-content/uploads/WENIGER-2017-Vergleich-verschiedener-Kennzahlen-zur-Bewertung-von-PV-Batteriesystemen.pdf.
"""


from typing import List, Dict
from dataclasses import dataclass
from dataclass_wizard import JSONWizard
from hisim.loadtypes import DistrictNames
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessing.kpi_computation.kpi_preparation import KpiPreparation, KpiTagEnumClass


@dataclass
class KpiGenerator(JSONWizard, KpiPreparation):
    """Class for generating and calculating key performance indicators."""

    post_processing_data_transfer: PostProcessingDataTransfer
    building_objects_in_district_list: list

    def __post_init__(self):
        """Build the dataclass from input data."""
        super().__init__(
            post_processing_data_transfer=self.post_processing_data_transfer,
            building_objects_in_district_list=self.building_objects_in_district_list,
        )

        for building_objects_in_district in self.building_objects_in_district_list:
            self.create_kpi_collection(building_objects_in_district)

        for building_objects_in_district in self.building_objects_in_district_list:
            if any(word in building_objects_in_district for word in DistrictNames):
                self.create_overall_district_kpi(district_name=building_objects_in_district)

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
            windturbine_production_in_kilowatt_hour,
            building_production_in_kilowatt_hour,
        ) = self.compute_electricity_consumption_and_production_and_battery_kpis(
            result_dataframe=self.filtered_result_dataframe,
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )

        # get ratio between total production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=total_electricity_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between total production and total consumption",
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )
        # get ratio between pv production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=pv_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between PV production and total consumption",
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )
        # get ratio between wka production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=windturbine_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between Windturbine production and total consumption",
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )
        # get ratio between building production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=building_production_in_kilowatt_hour,
            numerator_value=total_electricity_consumption_in_kilowatt_hour,
            kpi_name="Ratio between Building production and total consumption",
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )

        # get self-consumption, autarkie, injection
        self.filtered_result_dataframe = self.compute_self_consumption_injection_self_sufficiency(
            result_dataframe=self.filtered_result_dataframe,
            electricity_consumption_in_kilowatt_hour=total_electricity_consumption_in_kilowatt_hour,
            electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
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
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )

        # get self-consumption rate according to solar htw berlin
        self.compute_self_consumption_rate_according_to_solar_htw_berlin(
            total_electricity_production_in_kilowatt_hour=total_electricity_production_in_kilowatt_hour,
            electricity_to_grid_in_kilowatt_hour=total_electricity_to_grid_in_kwh,
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )
        # get self-sufficiency rate according to solar htw berlin
        self.compute_self_sufficiency_according_to_solar_htw_berlin(
            relative_electricty_demand_in_percent=relative_electricity_demand_from_grid_in_percent,
            building_objects_in_district=building_objects_in_district,
            kpi_tag=(
                KpiTagEnumClass.GENERAL
                if not any(word in building_objects_in_district for word in DistrictNames)
                else KpiTagEnumClass.ELECTRICITY_GRID
            ),
        )

        # get capex and opex costs
        self.read_opex_and_capex_costs_from_results(building_object=building_objects_in_district)

    def create_overall_district_kpi(self, district_name):
        """Creation of overall district kpis."""

        (
            overall_production_district_in_kilowatt_hour,
            overall_consumption_district_in_kilowatt_hour,
            overall_self_consumption_district_in_kilowatt_hour,
        ) = self.create_overall_district_kpi_collection(district_name=district_name)

        self.create_overall_district_costs_collection(district_name=district_name)
        self.create_overall_district_emissions_collection(district_name=district_name)
        self.create_overall_district_contracting_collection(district_name=district_name, all_outputs=self.all_outputs)

        # get ratio between total production and total consumption
        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=overall_production_district_in_kilowatt_hour,
            numerator_value=overall_consumption_district_in_kilowatt_hour,
            kpi_name="Overall ratio between total production and total consumption in district",
            building_objects_in_district=district_name,
            kpi_tag=KpiTagEnumClass.GENERAL,
        )

        # get electricity to and from grid
        (
            total_electricity_from_grid_in_kwh,
            total_electricity_to_grid_in_kwh,
        ) = self.get_electricity_to_and_from_grid_from_electricty_meter(district_name)
        # get relative electricity demand
        relative_electricity_demand_from_grid_in_percent = self.compute_relative_electricity_demand(
            total_electricity_consumption_in_kilowatt_hour=overall_consumption_district_in_kilowatt_hour,
            electricity_from_grid_in_kilowatt_hour=total_electricity_from_grid_in_kwh,
            building_objects_in_district=district_name,
            kpi_tag=KpiTagEnumClass.GENERAL,
            name="Overall relative electricity demand from grid in district",
        )

        self.compute_ratio_between_two_values_and_set_as_kpi(
            denominator_value=overall_self_consumption_district_in_kilowatt_hour,
            numerator_value=overall_consumption_district_in_kilowatt_hour,
            kpi_name="Overall self-sufficiency rate of electricity in district",
            building_objects_in_district=district_name,
            kpi_tag=KpiTagEnumClass.GENERAL,
        )

        self.compute_self_consumption_rate_according_to_solar_htw_berlin(
            total_electricity_production_in_kilowatt_hour=overall_production_district_in_kilowatt_hour,
            electricity_to_grid_in_kilowatt_hour=total_electricity_to_grid_in_kwh,
            building_objects_in_district=district_name,
            kpi_tag=KpiTagEnumClass.GENERAL,
            name="Overall self-consumption rate according to solar htw berlin in district",
        )

        self.compute_self_sufficiency_according_to_solar_htw_berlin(
            relative_electricty_demand_in_percent=relative_electricity_demand_from_grid_in_percent,
            building_objects_in_district=district_name,
            kpi_tag=KpiTagEnumClass.GENERAL,
            name="Overall self-sufficiency according to solar htw berlin in district",
        )

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

        kpi_collection_dict_sorted: Dict[str, Dict] = {
            building_objects: {} for building_objects in self.building_objects_in_district_list
        }

        for building_object in self.building_objects_in_district_list:
            # get all tags and use as keys for sorted kpi dict
            for entry in kpi_collection_dict_unsorted[building_object].values():
                kpi_collection_dict_sorted[building_object].update({entry["tag"]: {}})

            # now sort kpi dict entries according to tags
            for kpi_name, entry in kpi_collection_dict_unsorted[building_object].items():
                for tag, tag_dict in kpi_collection_dict_sorted[building_object].items():
                    if entry["tag"] is tag:
                        tag_dict.update({kpi_name: entry})

        return kpi_collection_dict_sorted
