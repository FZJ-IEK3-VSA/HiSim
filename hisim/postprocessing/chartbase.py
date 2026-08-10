"""Base classes for HiSim post-processing charts.

This module provides the shared foundation that every chart produced during
post-processing builds on.  The :class:`Chart` base class parses an output
identifier into a title, output type, and per-component folder layout, and
exposes the small set of helper methods that concrete chart implementations
reuse.  :class:`ChartFontsAndSize` collects the font and figure-size constants
that keep the visual style of all plots consistent.

Chart class
-----------

:class:`Chart` is constructed with an ``output`` identifier of the form
``"ComponentName # PropertyName"`` together with the component name, a unit,
and the results directory.  From these it derives:

* ``title`` — a human-readable title (the component and property split on the
  ``#`` delimiter and joined with a newline).
* ``output_type`` and ``component_output_folder_path`` — the per-component
  sub-folder under ``directory_path`` where the rendered figure is written.
* ``filepath`` and ``filepath2`` — the absolute output file paths, the latter
  inside the per-component folder.

Construction is free of filesystem side effects: path length is validated
through an injectable ``path_checker`` (defaulting to
:func:`hisim.result_path_provider.check_path_length`), and the output
directory is created lazily by :meth:`Chart.ensure_output_dir` rather than in
``__init__``.  This keeps chart objects cheap to build and easy to unit-test.

Inheritance structure
---------------------

Concrete chart types live in :mod:`hisim.postprocessing.charts`
(:class:`~hisim.postprocessing.charts.Carpet`,
:class:`~hisim.postprocessing.charts.Line`, and
:class:`~hisim.postprocessing.charts.BarChart`) and
:mod:`hisim.postprocessing.chart_singleday`
(:class:`~hisim.postprocessing.chart_singleday.ChartSingleDay`).  Each
subclass multiply inherits from :class:`Chart` and
:class:`ChartFontsAndSize`, calls ``super().__init__(...)`` with the
appropriate ``chart_type`` label, and then implements its own ``plot``
method.  The base class therefore defines the common state and helpers, while
the subclasses own the matplotlib rendering.

Customization hooks
-------------------

Subclasses interact with the base class through two helper methods:

* :meth:`Chart.ensure_output_dir` — creates
  ``component_output_folder_path`` (and parents) immediately before a figure
  is written, so construction never touches the disk.
* :meth:`Chart.rescale_y_axis` — applies an SI prefix (T, G, M, k) to the
  plotted values and unit string based on the maximum absolute value, keeping
  axis numbers readable.  Units already carrying a ``k`` prefix (kg, kWh,
  kg/s, kW) are normalised first; ``-`` and ``%`` are returned unchanged.

The class-level ``months_abbrev_uppercase`` and ``label_months_lowercase``
lists supply consistent month labels shared by the bar and single-day charts.
"""

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
        time_correction_factor_in_hours,
        *,
        output2=None,
        figure_format=None,
        path_checker=None,
    ):
        """Initialize the base chart.

        Parses the `output` string (which embeds a component name and a
        property separated by ``#``) to derive the chart title, output type,
        and per-component folder path.  The output directory is *not* created
        here — call :meth:`ensure_output_dir` before writing.

        Args:
            output: Full output identifier, e.g. ``"ComponentName # PropertyName"``.
            component_name: Name of the component whose output is plotted.
            output_description: Human-readable description of the output.
            chart_type: Chart type label (e.g. ``"line"``, ``"bar"``); used in
                the generated filename.
            units: Units of the plotted values.  May be an Enum with a
                ``.value`` attribute or a plain string.
            directory_path: Base results directory under which the chart file
                and per-component sub-folder are created.
            time_correction_factor_in_hours: Factor applied to convert between
                simulation time steps and real time.
            output2: Optional second output identifier; when present the
                filename is suffixed with ``_double``.
            figure_format: Enum member whose ``.value`` is the file extension
                (e.g. ``.png``).
            path_checker: Optional callable that validates path length.
                Defaults to
                :func:`hisim.result_path_provider.check_path_length`.
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
        self.time_correction_factor_in_hours = time_correction_factor_in_hours

        self.title: str = ""
        matches = re.finditer(".+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$|#)", self.output)
        matches = [m.group(0) for m in matches]  # type: ignore

        passed_hash_delimiter = False
        chart_property = ""
        chart_object = ""
        for single_match in matches:
            if passed_hash_delimiter:
                chart_property = f"{chart_property}{single_match}"
            else:
                chart_object = f"{chart_object}{single_match}"

            if single_match.find("#"):  # type: ignore
                passed_hash_delimiter = True

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
        """Rescale y_values of plots.

        Chooses an SI prefix (T, G, M, k) based on the maximum absolute
        value so that axis numbers stay readable.  Units already prefixed
        with ``k`` (kg, kWh, kg/s, kW) are first stripped of the ``k`` and
        the values multiplied by 1e3 before the new prefix is applied.
        Units ``-`` and ``%`` are left unchanged.

        Args:
            y_values: Array-like numeric values to rescale.
            units: Current unit string (may be modified in the returned
                tuple).

        Returns:
            A tuple ``(rescaled_y_values, updated_units)`` where
            ``updated_units`` carries the new SI prefix, or the original
            values and units when no rescaling was needed.
        """
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
