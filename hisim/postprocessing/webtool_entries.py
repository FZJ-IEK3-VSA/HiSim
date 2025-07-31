"""Webtool results with important kpis."""

from dataclasses import dataclass, field, InitVar
from typing import Dict, List, Optional
import re
import pandas as pd
from dataclass_wizard import JSONWizard

from hisim.component import ComponentOutput
from hisim.loadtypes import OutputPostprocessingRules
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer


@dataclass
class ResultEntry(JSONWizard):
    """Class for storing one result entry for hisim webtool."""

    name: str
    unit: str
    value: float
    description: Optional[str] = None


@dataclass
class WebtoolDict(JSONWizard):
    """Class for storing results for hisim webtool."""

    kpis: Dict
    components: Dict = field(init=False)

    post_processing_data_transfer: InitVar[PostProcessingDataTransfer]
    computed_opex: InitVar[List]
    computed_capex: InitVar[List]

    def __post_init__(self, post_processing_data_transfer, computed_opex, computed_capex):
        """Build the dataclass from input data."""
        self.components = {}
        self.init_structure(post_processing_data_transfer)
        self.add_opex_capex_results(computed_opex, computed_capex)
        self.add_technical_results(post_processing_data_transfer)
        self.add_configuration(post_processing_data_transfer)

    def init_structure(self, post_processing_data_transfer):
        """Initialize results dict for webtool with component names and categories.

        Only components with DisplayConfig.display_in_webtool=True are selected.
        """

        # Get bools that tells if the components should be displayed in webtool
        for component in post_processing_data_transfer.wrapped_components:
            if component.my_component.my_display_config.display_in_webtool:
                this_name = component.my_component.component_name
                this_pretty_name = component.my_component.my_display_config.pretty_name
                this_component_class = component.my_component.get_classname()
                self.components[this_name] = {
                    "prettyName": this_pretty_name,
                    "class": this_component_class,
                    "configuration": {},
                    "operation": {},
                    "economics": {},
                }

    def add_opex_capex_results(self, computed_opex, computed_capex):
        """Add results from the results of `opex_calculation()` and `capex_calculation()` to webtool dict."""
        categories_opex = ["economics", "operation", "economics"]
        categories_capex = ["economics", "operation", "economics"]
        # Get OPEX and CAPEX
        for computed_values, categories in zip([computed_opex, computed_capex], [categories_opex, categories_capex]):
            if not all(isinstance(_, str) for _ in computed_values[0]):
                # First row is header.
                raise ValueError("Expected header in first row.")

            for computed_values_row in computed_values[1:]:
                if not computed_values_row:
                    continue

                if not isinstance(computed_values_row[0], str):
                    # Fist column is component name.
                    raise ValueError("Expected component name in first column.")

                if "total" in computed_values_row[0].lower():
                    # Skip rows with total values.
                    continue
                selected_computed_values_row = computed_values_row[-3:]  # this is adapted according to length of categories
                for idx_column, computed_values_item in enumerate(selected_computed_values_row):
                    if len(selected_computed_values_row) != len(categories):
                        raise ValueError(f"Index and value length mismatch: {len(selected_computed_values_row)} vs {len(categories)}.")
                    # Get component name
                    this_component = computed_values_row[0]
                    # Skip components that should not be displayed
                    if this_component not in self.components.keys():
                        continue
                    # Get value key and reformat unit into brackets
                    computed_values_key = computed_values[0][idx_column + 1]
                    try:
                        computed_values_name, computed_values_unit = computed_values_key.split(" in ")
                    except Exception as exc:
                        match = re.match(r"^(.*?)\s*\[(.*?)\]$", computed_values_key)
                        if match:
                            computed_values_name, computed_values_unit = match.groups()
                        else:
                            raise ValueError(f"Key {computed_values_key} cannot be reformatted.") from exc
                    # Create result entry
                    result_entry = ResultEntry(
                        name=computed_values_name, unit=computed_values_unit, description="", value=computed_values_item)

                    # Save to dict
                    self.components[this_component][categories[idx_column]].update({computed_values_name: result_entry})

    def add_technical_results(self, post_processing_data_transfer: PostProcessingDataTransfer) -> None:
        """Add technical results from PostProcessingDataTransfer to results dataclass."""

        # Get outputs
        name_component_dict: Dict[str, ComponentOutput] = {}
        component_output: ComponentOutput
        for component_output in post_processing_data_transfer.all_outputs:
            name_component_dict[component_output.get_pretty_name()] = component_output

        # Get bools that tells if the components should be displayed in webtool
        component_display_in_webtool_dict: Dict[str, bool] = {}
        for component in post_processing_data_transfer.wrapped_components:
            component_display_in_webtool_dict[
                component.my_component.component_name
            ] = component.my_component.my_display_config.display_in_webtool

        # Read data from PostProcessingDataTransfer and save to results
        data: pd.DataFrame = post_processing_data_transfer.results_cumulative
        series_name: str
        series: pd.Series
        for series_name, series in data.items():
            my_output = name_component_dict[series_name]
            # Skip components that should not be displayed
            if my_output.component_name not in self.components.keys():
                continue
            # Skip outputs without postprocessing flag OutputPostprocessingRules.DISPLAY_IN_WEBTOOL
            if my_output.postprocessing_flag:
                if OutputPostprocessingRules.DISPLAY_IN_WEBTOOL not in my_output.postprocessing_flag:
                    continue
            else:
                continue
            # Create result entry
            my_result = ResultEntry(
                name=my_output.display_name,
                unit=my_output.unit,
                description=my_output.output_description,
                value=round(series.item(), 2),
            )
            # Write to result dict
            self.components[my_output.component_name]["operation"].update({my_output.display_name: my_result.to_dict()})

    def add_configuration(self, ppdt: PostProcessingDataTransfer) -> None:
        """Add configuration for displayed components from PostProcessingDataTransfer to results dataclass."""
        for wrapped_component in ppdt.wrapped_components:
            if wrapped_component.my_component.my_display_config.display_in_webtool:
                component_content = wrapped_component.my_component.config.to_dict()
                self.components[wrapped_component.my_component.component_name]["configuration"] = component_content
