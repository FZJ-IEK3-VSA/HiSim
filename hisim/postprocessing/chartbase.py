"""Contains the base class for the charts."""

# clean
from pathlib import Path
import re
from typing import Any, Tuple
from dataclasses import dataclass
import numpy as np
from hisim import result_path_provider


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
        "DEC",
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
        path_checker=None,
    ):
        """Initializes the base class.

        ``path_checker`` is an optional callable used to validate the length of
        the generated file paths. It defaults to
        :func:`hisim.result_path_provider.check_path_length`; tests may inject a
        no-op to avoid the hidden ``result_path_provider`` singleton. The
        per-component output directory is *not* created here -- call
        :meth:`ensure_output_dir` (done by the ``plot`` methods) before writing.
        """
        self.output = output
        self.component_name = component_name
        self.output_description = output_description
        self.type = chart_type
        self.figure_format = figure_format

        if hasattr(units, "value"):
            self.units = units.value
            # self.ylabel = units.value
        else:
            self.units = units
            # self.ylabel = units
        self.time_correction_factor = time_correction_factor

        self.title: str = ""
        matches = re.finditer(".+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$|#)", self.output)
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
        self.title = self.title.strip()
        self.directory_path = directory_path
        self.output_type = self.output.split(" # ", 2)[1]
        self.component_output_folder_path = str(Path(self.directory_path) / self.component_name / self.output_type)
        self.object_name = " "
        self.property = chart_property
        if output2 is not None:
            self.output2 = output2
            self.filename = f"{self.type.lower()}_double{self.figure_format.value}"
        else:
            self.filename = f"{self.type.lower()}{self.figure_format.value}"
        self.filepath = str(Path(self.directory_path) / self.filename)
        self.filepath2 = str(Path(self.component_output_folder_path) / self.filename)
        # Resolve the path-length checker: tests may inject a no-op to avoid the
        # hidden ``result_path_provider`` singleton; production keeps the default.
        if path_checker is None:
            path_checker = result_path_provider.check_path_length
        path_checker(path=self.filepath)
        path_checker(path=self.filepath2)

    def ensure_output_dir(self) -> None:
        """Create the per-component output directory if it does not exist yet.

        This used to be done unconditionally in ``__init__``, which made every
        ``Chart`` (and subclass) construction touch the filesystem and depend on
        the global ``result_path_provider``. It is now invoked lazily by the
        ``plot`` methods, right before they write into ``self.filepath2``, so
        that constructing a chart is free of side effects and easy to test.
        """
        Path(self.component_output_folder_path).mkdir(parents=True, exist_ok=True)

    def rescale_y_axis(self, y_values: Any, units: Any) -> Tuple[Any, Any]:
        """Rescale y_values of plots."""
        max_scale = np.max(np.abs(y_values))  # type: ignore

        if units not in ["-", "%"]:
            scale = ""

            # if k already in unit, remove k first and then scale
            if units in ["kg", "kWh", "kg/s", "kW"]:
                y_values = y_values * 1e3
                units = units.strip("k")

            if max_scale >= 1e12:
                y_values = y_values * 1e-12
                scale = "T"
            elif 1e9 <= max_scale < 1e12:
                y_values = y_values * 1e-9
                scale = "G"
            elif 1e6 <= max_scale < 1e9:
                y_values = y_values * 1e-6
                scale = "M"
            elif 1e3 <= max_scale < 1e6:
                y_values = y_values * 1e-3
                scale = "k"

            units = f"{scale}{units}"

        return y_values, units


@dataclass
class ChartFontsAndSize:
    """Give the font sizes and figure sizes of the figures."""

    figsize = (6, 4)
    dpi = 600
    fontsize_title = 14
    fontsize_label = 12
    fontsize_legend = 12
    fontsize_ticks = 10
