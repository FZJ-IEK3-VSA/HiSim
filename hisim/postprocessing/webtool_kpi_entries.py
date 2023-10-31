"""Webtool results with important kpis."""

from dataclasses import dataclass, field
from typing import Any, Dict

from dataclass_wizard import JSONWizard


@dataclass
class WebtoolKpiEntries(JSONWizard):

    """Class for storing important kpis for hisim webtool."""

    kpi_dict: Dict[str, Any] = field(default_factory=dict)

    opex_dict: Dict[str, Any] = field(default_factory=dict)

    capex_dict: Dict[str, Any] = field(default_factory=dict)

    capacity_dict: Dict[str, Any] = field(default_factory=dict)
