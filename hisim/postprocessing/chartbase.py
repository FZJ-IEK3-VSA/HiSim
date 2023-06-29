""" Contains the base class for the charts. """
# clean
import os
import re
from dataclasses import dataclass


class Chart:  # noqa: too-few-public-methods

    """Parent class for plots to be exported."""

    months_abbrev_uppercase = [
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEZ",
    ]
    label_months_lowercase = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    def __init__(
        self,
        output,
        component_name,
        output_description,
        chart_type,
        units,
        directory_path,
        time_correction_factor,
        output2=None,
        figure_format=None,
    ):
        """Initializes the base class."""
        self.output = output
        self.component_name = component_name
        self.output_description = output_description
        self.type = chart_type
        self.figure_format = figure_format

        if hasattr(units, "value"):
            self.units = units.value
            self.ylabel = units.value
        else:
            self.units = units
            self.ylabel = units
        self.time_correction_factor = time_correction_factor

        self.title: str = ""
        matches = re.finditer(
            ".+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$|#)", self.output
        )
        matches = [m.group(0) for m in matches]  # type: ignore

        pass_sign = False
        chart_property = ""
        chart_object = ""
        for single_match in matches:
            if pass_sign:
                chart_property = f"{chart_property}{single_match}"
            else:
                chart_object = f"{chart_object}{single_match}"

            if single_match.find("#"):  # type: ignore
                pass_sign = True

            if len(self.title) == 0:
                self.title = str(single_match)

            else:
                self.title = f"{self.title}{single_match}"

        self.title = self.title.replace("# ", "\n")
        self.title.strip()
        self.directory_path = directory_path
        self.output_type = self.output.split(" # ", 2)[1]
        self.component_output_folder_path = os.path.join(
            self.directory_path, self.component_name, self.output_type
        )
        os.makedirs(self.component_output_folder_path, exist_ok=True)
        self.object_name = " "
        self.property = chart_property
        if output2 is not None:
            self.output2 = output2
            self.filename = f"{self.type.lower()}_{self.component_name}_{self.output_type}_double{self.figure_format}"
        else:
            self.filename = f"{self.type.lower()}_{self.component_name}_{self.output_type}{self.figure_format}"
        self.filepath = os.path.join(self.directory_path, self.filename)
        self.filepath2 = os.path.join(self.component_output_folder_path, self.filename)


@dataclass
class ChartFontsAndSize:

    """Give the font sizes and figure sizes of the figures."""

    figsize = (6, 4)
    dpi = 600
    fontsize_title = 14
    fontsize_label = 12
    fontsize_legend = 12
    fontsize_ticks = 10
