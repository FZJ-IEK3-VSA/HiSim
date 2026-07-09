"""Data containers for images embedded in HiSim post-processing reports.

Defines :class:`SystemChartEntry`, a lightweight dataclass pairing a rendered
system/network chart file with its caption, and :class:`ReportImageEntry`,
which bundles the metadata describing a single component output figure
(component name, output type, file path, category, description, unit) for
inclusion in the generated report.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SystemChartEntry:
    """Describe a single system/network chart to embed in a report.

    Attributes:
        path: Filesystem path to the rendered chart image file.
        caption: Human-readable caption displayed alongside the chart.
    """

    path: str
    caption: str


class ReportImageEntry:
    """Bundle metadata for a single component output figure shown in a report.

    Holds the component name, output type, rendered file path, output folder,
    optional category, description, and unit describing one figure produced
    for a component output during post-processing.
    """

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
        """Initialize the report image entry and validate required fields.

        Args:
            component_name: Name of the component whose output figure this
                entry describes. Must not be ``None``.
            output_type: Type label of the component output (used to group
                figures of the same kind together in the report).
            file_path: Filesystem path to the rendered figure image file.
            component_output_folder_path: Directory holding the component's
                output files (figures, CSVs, etc.).
            category: Optional category label under which to file the figure;
                may be ``None`` when no category applies.
            output_description: Human-readable description of the output shown
                in the figure. Must not be ``None``.
            unit: Optional unit of the plotted quantity (e.g. ``"kW"``); may
                be ``None`` when the output is unitless.

        Raises:
            ValueError: If ``component_name`` or ``output_description`` is
                ``None``.
        """
        if component_name is None:
            raise ValueError("Component name was None.")
        self.component_name = component_name
        self.output_type = output_type
        self.category = category
        if output_description is None:
            raise ValueError("Component description was none from component: " + component_name)
        self.output_description = output_description
        self.unit = unit
        self.component_output_folder_path = component_output_folder_path
        self.file_path = file_path
