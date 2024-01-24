"""Webtool results with important kpis."""

from typing import Dict, Any, List
from dataclasses import dataclass, field
from dataclass_wizard import JSONWizard

from hisim.component_wrapper import ComponentWrapper


@dataclass
class WebtoolComponent(JSONWizard):
    name: str


@dataclass
class WebtoolEntries(JSONWizard):

    """Class for storing important kpis for hisim webtool."""

    components: List[WebtoolComponent]
    kpis: Dict[str, Any] = field(default_factory=dict)


def get_components_for_webtool(components: List[ComponentWrapper], computed_opex: List, computed_capex: List):
    this_components_dict = {}

    # Get component names and initialize dictionary.
    for component in components:
        this_name = component.my_component.my_display_config.pretty_name or component.my_component.component_name
        this_component_class = component.my_component.get_classname()
        this_components_dict[component.my_component.component_name] = {
            "Name": this_name,
            "Class": this_component_class,
            "Sizing": {},
            "Energy": {},
            "Emissions": {},
            "Economics": {},
        }

    categories_opex = ["Economics", "Emissions", "Energy"]
    categories_capex = ["Economics", "Emissions", "Economics"]

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
                this_components_dict[this_component][categories[idx_column]].update(
                    {computed_values_key: computed_values_item}
                )

    return list(this_components_dict.values())
