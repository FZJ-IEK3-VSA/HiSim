"""Webtool results with important kpis."""

from typing import Dict, Any, List
from dataclasses import dataclass, field
from dataclass_wizard import JSONWizard

from hisim.component_wrapper import ComponentWrapper


@dataclass
class WebtoolDict:

    """Class for storing results for hisim webtool."""

    def __init__(
        self,
        components: List[ComponentWrapper],
        kpis: Dict,
        computed_opex: List,
        computed_capex: List,
    ):
        self.components = {}
        self.kpis = kpis  # KPIs are used for result validation. Do not change this.

        #  Add results one by one.
        self.init_structure(components)
        self.add_opex_capex(computed_opex, computed_capex)
        self.add_sizing(components)

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
                "energy": {},
                "emissions": {},
                "economics": {},
            }

    def add_opex_capex(self, computed_opex, computed_capex):
        """Add results from the results of `opex_calculation()` and `capex_calculation()` to webtool dict."""
        categories_opex = ["economics", "emissions", "energy"]
        categories_capex = ["economics", "emissions", "economics"]
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

    def add_sizing(self, components):
        print("HI.")
