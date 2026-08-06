"""Left overs from old webtool postprocessing."""


# Prepare webtool results
if PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL in ppdt.post_processing_options:
    log.information("Make JSON file for webtool.")
    self.write_results_for_webtool_to_json_file(ppdt, building_objects_in_district_list)

# Prepare webtool operation results
if PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL in ppdt.post_processing_options:
    log.information("Make JSON file for webtool (operation).")
    self.write_operation_data_for_webtool(ppdt)

def write_results_for_webtool_to_json_file(
        self, ppdt: PostProcessingDataTransfer, building_objects_in_district_list: list
    ) -> None:
        """Collect results and write into json for webtool."""

        # Check if important options were set
        if all(
            option in ppdt.post_processing_options
            for option in [
                PostProcessingOptions.COMPUTE_KPIS,
                PostProcessingOptions.COMPUTE_CAPEX,
                PostProcessingOptions.COMPUTE_OPEX,
            ]
        ):
            # Get KPIs from ppdt
            kpi_collection_dict = ppdt.kpi_collection_dict

            # Calculate capex
            capex_calculation = _load_attribute(
                "hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation",
                "capex_calculation",
            )
            capex_compute_return = capex_calculation(
                components=ppdt.wrapped_components,
                simulation_parameters=ppdt.simulation_parameters,
                building_objects_in_district_list=building_objects_in_district_list,
            )

            # Calculate opex
            opex_calculation = _load_attribute(
                "hisim.postprocessing.cost_and_emission_computation.opex_and_capex_cost_calculation",
                "opex_calculation",
            )
            opex_compute_return = opex_calculation(
                components=ppdt.wrapped_components,
                all_outputs=ppdt.all_outputs,
                postprocessing_results=ppdt.results,
                simulation_parameters=ppdt.simulation_parameters,
                building_objects_in_district_list=building_objects_in_district_list,
            )

            # Consolidate results into structured dataclass for webtool
            webtool_dict_class = _load_attribute("hisim.postprocessing.webtool_entries", "WebtoolDict")
            webtool_results_dataclass = webtool_dict_class(  # type: ignore
                kpis=kpi_collection_dict,
                post_processing_data_transfer=ppdt,
                computed_opex=opex_compute_return,
                computed_capex=capex_compute_return,
            )

            # Save dataclass as json file in results folder
            json_file = webtool_results_dataclass.to_json(indent=4)
            with open(
                os.path.join(ppdt.simulation_parameters.result_directory, "results_for_webtool.json"),
                "w",
                encoding="utf-8",
            ) as file:
                file.write(json_file)

        else:
            raise ValueError(
                "Some PostProcessingOptions are not set. Please check if "
                f"{PostProcessingOptions.COMPUTE_KPIS}, {PostProcessingOptions.COMPUTE_CAPEX} and "
                f"{PostProcessingOptions.COMPUTE_OPEX} are set in your system setup."
            )

def write_operation_data_for_webtool(self, ppdt: PostProcessingDataTransfer) -> None:
        """Collect daily operation results and write into json for webtool."""

        output_postprocessing_rules = _load_attribute("hisim.loadtypes", "OutputPostprocessingRules")
        # Get bools that tells if the output should be displayed in webtool
        component_display_in_webtool: list[str] = []
        for output in ppdt.all_outputs:
            if output.postprocessing_flag:
                if output_postprocessing_rules.DISPLAY_IN_WEBTOOL in output.postprocessing_flag:
                    component_display_in_webtool.append(output.get_pretty_name())

        assert ppdt.results_daily is not None
        results_daily = ppdt.results_daily[component_display_in_webtool]
        data = results_daily.to_json(date_format="iso")

        # Write to file
        with open(
            os.path.join(ppdt.simulation_parameters.result_directory, "results_daily_operation_for_webtool.json"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write(data)

def get_dict_from_opex_capex_lists(self, value_list: List[str]) -> Dict[str, Any]:
        """Get dict with values for webtool from opex capex lists."""

        dict_with_cost_values = {}
        dict_with_emission_values = {}
        dict_with_lifetime_values = {}

        total_dict = {}

        name_one = value_list[0]

        for value_unit in value_list:
            if "---" not in value_unit:
                variable_name = "".join(x for x in value_unit[0] if x != ":")
                variable_value_investment = value_unit[1]
                variable_value_emissions = value_unit[2]
                variable_value_lifetime = value_unit[3]

                dict_with_cost_values.update({f"{variable_name} [{name_one[1]}] ": variable_value_investment})
                dict_with_emission_values.update({f"{variable_name} [{name_one[2]}] ": variable_value_emissions})
                dict_with_lifetime_values.update({f"{variable_name} [{name_one[3]}] ": variable_value_lifetime})

                total_dict.update(
                    {
                        "column 1": dict_with_cost_values,
                        "column 2": dict_with_emission_values,
                        "column 3": dict_with_lifetime_values,
                    }
                )

        return total_dict