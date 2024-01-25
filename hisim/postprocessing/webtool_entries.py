"""Webtool results with important kpis."""

from dataclasses import dataclass, field, InitVar
from typing import Dict, List

import pandas as pd
from dataclass_wizard import JSONWizard

from hisim.component_wrapper import ComponentWrapper
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer


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
        self.init_structure(post_processing_data_transfer.wrapped_components)
        self.add_opex_capex_results(computed_opex, computed_capex)
        self.add_technical_results(post_processing_data_transfer)

    def init_structure(self, components):
        """Initialize results dict for webtool with component names and categories."""
        for component in components:
            this_name = component.my_component.component_name
            this_pretty_name = component.my_component.my_display_config.pretty_name
            this_component_class = component.my_component.get_classname()
            self.components[this_name] = {
                "prettyName": this_pretty_name,
                "class": this_component_class,
                "sizing": {},
                "technical": {},
                "economical": {},
            }

    def add_opex_capex_results(self, computed_opex, computed_capex):
        """Add results from the results of `opex_calculation()` and `capex_calculation()` to webtool dict."""
        categories_opex = ["economical", "technical", "economical"]
        categories_capex = ["economical", "technical", "economical"]
        # Get OPEX and CAPEX
        for computed_values, categories in zip([computed_opex, computed_capex], [categories_opex, categories_capex]):
            if not all(isinstance(_, str) for _ in computed_values[0]):
                # First row is header.
                raise ValueError("Expected header in first row.")
            for computed_values_row in computed_values[1:]:
                if not isinstance(computed_values_row[0], str):
                    # Fist column is component name.
                    raise ValueError("Expected component name in first column.")
                if "total" in computed_values_row[0].lower():
                    # Skip rows with total values.
                    continue
                for idx_column, computed_values_item in enumerate(computed_values_row[1:]):
                    # Get component name
                    this_component = computed_values_row[0]
                    # Get value key and reformat unit into brackets
                    computed_values_key = computed_values[0][idx_column + 1]
                    computed_values_key = computed_values_key.replace(" in ", " [")
                    computed_values_key = computed_values_key + "]"
                    # Save to dict
                    self.components[this_component][categories[idx_column]].update(
                        {computed_values_key: computed_values_item}
                    )

    def add_technical_results(self, post_processing_data_transfer):
        df: pd.DataFrame = post_processing_data_transfer.results_cumulative
        for series_name, series in df.items():
            component, attribute = series_name.split(" - ", 1)
            value = series.item()
            self.components[component]["technical"].update({attribute: round(value, 2)})
