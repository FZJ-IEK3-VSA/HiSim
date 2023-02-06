from typing import Optional
from dataclasses import dataclass
from hisim import log

# @dataclass
class ReportImageEntry:

    """Class for report images."""

    def __init__(self, component_name: str, output_type: str, category: Optional[str], description: Optional[str], unit: Optional[str], path: Optional[str]) -> None:
        """Initialize the report image entry."""
        self.component_name = component_name
        self.output_type = output_type
        self.category = category
        self.description = description
        self.unit = unit
        self.path = path
