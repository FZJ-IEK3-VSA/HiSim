"""Report Image Entry Module."""

from typing import Optional


class ReportImageEntry:

    """Class for report images."""

    def __init__(
        self,
        component_name: str,
        output_type: str,
        file_path: str,
        component_output_folder_path: str,
        category: Optional[str],
        output_description: Optional[str],
        unit: Optional[str],
    ) -> None:
        """Initialize the report image entry."""
        self.component_name = component_name
        self.output_type = output_type
        self.category = category
        self.output_description = output_description
        self.unit = unit
        self.component_output_folder_path = component_output_folder_path
        self.file_path = file_path
