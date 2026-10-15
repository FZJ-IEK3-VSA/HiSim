"""Base classes for HiSim post-processing charts.

This module provides the shared foundation that every chart produced during
post-processing builds on.  The :class:`Chart` base class parses an output
identifier into a title, output type, and per-component folder layout, and
exposes the small set of helper methods that concrete chart implementations
reuse.  :class:`ChartFontsAndSize` collects the font and figure-size constants
that keep the visual style of all plots consistent, and
:class:`SavedChartResolution` holds the one resolution every chart is saved at.

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

Subclasses interact with the base class through these helper methods:

* :meth:`Chart.ensure_output_dir` — creates
  ``component_output_folder_path`` (and parents) immediately before a figure
  is written, so construction never touches the disk.
* :meth:`Chart.rescale_y_axis` — returns the values in the unit the chart
  shows them in, following :class:`AxisUnit`: the largest SI prefix the values
  reach for a unit that takes one, never below the declared prefix, and the
  declared unit for every other one.
* :meth:`Chart.axis_label` — the label of the value axis, the quantity and the
  shown unit, such as ``"Power [kW]"``.
* :meth:`Chart.wrap_title` — breaks a title line that would run off the figure.

The class-level ``months_abbrev_uppercase`` and ``label_months_lowercase``
lists supply consistent month labels shared by the bar and single-day charts.
"""

from pathlib import Path
import re
from typing import Any, ClassVar, Dict, List, Tuple
from dataclasses import dataclass
import numpy as np
from hisim import result_path_provider


class AxisUnit:
    """How a chart shows the values of one output: in which unit, under which axis label.

    An output declares its unit as a string of :class:`hisim.loadtypes.Units` (``"W"``, ``"kWh"``,
    ``"°C"``, ``"W per square meter"``, ...). A chart shows its values in that unit, with two
    changes. First, a unit that takes an SI prefix gets the largest prefix the values reach, but
    never one smaller than the declared prefix: 20 914 W are shown as 20.9 kW, 0.1 kg/s stay
    0.1 kg/s instead of becoming 100 g/s, and 2 000 kWh become 2 MWh rather than "kkWh". A unit
    that does not take a prefix -- a temperature, a fraction, a count of timesteps -- is shown as
    declared, so no axis reads "k°C" or "ktimesteps". Second, durations in seconds that reach
    :attr:`SECONDS_SHOWN_IN_HOURS` are shown in hours, because "2.3 Ms" is correct SI and still
    unreadable. The axis label names the quantity and the unit, for example ``"Power [kW]"``.
    """

    #: Declared units that take an SI prefix: the unit shown without prefix, and the power of ten of
    #: the declared prefix (3 for "kWh", which is 10^3 Wh).
    PREFIXABLE_UNITS: ClassVar[Dict[str, Tuple[str, int]]] = {
        "W": ("W", 0),
        "kW": ("W", 3),
        "Wh": ("Wh", 0),
        "kWh": ("Wh", 3),
        "J": ("J", 0),
        "kJ": ("J", 3),
        "Pa": ("Pa", 0),
        "W per square meter": ("W/m²", 0),
        "Wh per square meter": ("Wh/m²", 0),
        "W per meter": ("W/m", 0),
        "m": ("m", 0),
        "L": ("L", 0),
    }

    #: The SI prefixes a value may take, largest first, each with its power of ten.
    PREFIXES: ClassVar[List[Tuple[int, str]]] = [(12, "T"), (9, "G"), (6, "M"), (3, "k"), (0, "")]

    #: From this many seconds on, a duration is shown in hours (about three hours).
    SECONDS_SHOWN_IN_HOURS: ClassVar[float] = 1e4

    #: Declared units that are shown in a shorter symbol and are otherwise left as they are.
    DISPLAY_SYMBOLS: ClassVar[Dict[str, str]] = {"Degrees": "°", "m2": "m²", "m3": "m³"}

    #: The quantity each declared unit measures, which names the axis. A unit not listed here is
    #: labelled "Value".
    QUANTITIES: ClassVar[Dict[str, str]] = {
        "W": "Power",
        "kW": "Power",
        "Wh": "Energy",
        "kWh": "Energy",
        "J": "Energy",
        "kJ": "Energy",
        "kWh per timestep": "Energy",
        "W per square meter": "Power per area",
        "Wh per square meter": "Energy per area",
        "W per meter": "Power per length",
        "°C": "Temperature",
        "K": "Temperature difference",
        "Pa": "Pressure",
        "bar": "Pressure",
        "kg": "Mass",
        "kg/s": "Mass flow",
        "L": "Volume",
        "m3": "Volume",
        "Liter per timestep": "Volume per timestep",
        "Cubic meters per second": "Volume flow",
        "m": "Length",
        "m2": "Area",
        "m/s": "Speed",
        "s": "Duration",
        "h": "Duration",
        "years": "Duration",
        "timesteps": "Timesteps",
        "Degrees": "Angle",
        "%": "Share",
        "fraction": "Fraction",
        "binary": "State",
        "Euro": "Cost",
        "Euros per kWh": "Price",
    }

    @classmethod
    def scale(cls, values: Any, declared_unit: str) -> Tuple[Any, str]:
        """Return the values in the unit a chart shows them in, and that unit.

        Example: ``scale(np.array([0.0, 20914.0]), "W")`` returns ``([0.0, 20.914], "kW")``;
        ``scale(np.array([0.1]), "kg/s")`` returns ``([0.1], "kg/s")``; ``scale(np.array([2.3e6]), "s")``
        returns ``([638.9], "h")``.

        Args:
            values: The values in the declared unit; a numpy array or a pandas Series.
            declared_unit: The unit the output declares, as the string of its ``Units`` member.

        Returns:
            The values multiplied into the shown unit, and the shown unit.
        """
        magnitude = cls.largest_magnitude(values)
        if declared_unit in cls.PREFIXABLE_UNITS:
            base_unit, declared_power = cls.PREFIXABLE_UNITS[declared_unit]
            magnitude_in_base_unit = magnitude * 10.0**declared_power
            for power, prefix in cls.PREFIXES:
                if power <= declared_power or magnitude_in_base_unit >= 10.0**power:
                    return values * 10.0 ** (declared_power - power), f"{prefix}{base_unit}"
        if declared_unit == "s" and magnitude >= cls.SECONDS_SHOWN_IN_HOURS:
            return values / 3600.0, "h"
        return values, cls.DISPLAY_SYMBOLS.get(declared_unit, declared_unit)

    @classmethod
    def label(cls, declared_unit: str, shown_unit: str) -> str:
        """Return the axis label of an output: the quantity its unit measures and the shown unit.

        Example: ``label("W", "kW")`` is ``"Power [kW]"``, ``label("-", "-")`` is ``"Value [-]"``.

        Args:
            declared_unit: The unit the output declares, which decides the quantity.
            shown_unit: The unit the chart shows the values in, as :meth:`scale` returned it.

        Returns:
            The label, in the form ``"Quantity [unit]"``.
        """
        return f"{cls.QUANTITIES.get(declared_unit, 'Value')} [{shown_unit}]"

    @staticmethod
    def largest_magnitude(values: Any) -> float:
        """Return the largest absolute finite value, or 0 when there is none.

        Args:
            values: A numpy array or a pandas Series of numbers.

        Returns:
            The largest absolute value that is not NaN or infinite; 0.0 for an empty or all-NaN input.
        """
        absolute = np.abs(np.asarray(values, dtype=float))
        finite = absolute[np.isfinite(absolute)]
        return float(finite.max()) if finite.size else 0.0


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

    #: A title line longer than this many characters is broken; at the 14 pt title font of a 6 inch
    #: wide figure a longer line reaches the edge of the figure.
    MAXIMUM_TITLE_CHARACTERS: ClassVar[int] = 45

    #: Where a title line may be broken: after a space or an underscore, and at the word boundaries
    #: of camel case ("HeatPump" between "Heat" and "Pump", "HPLib" between "HP" and "Lib").
    TITLE_BREAK_PATTERN: ClassVar[str] = r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[_ ])"

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
        else:
            self.units = units
        # The unit the output declares; ``self.units`` becomes the unit a chart shows once it has plotted.
        self.declared_units: str = self.units
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
        """Return the values in the unit the chart shows them in, and that unit.

        Delegates to :meth:`AxisUnit.scale`: a unit that takes an SI prefix gets the largest prefix
        the values reach but never one below its declared prefix (20 914 W become 20.9 kW, 0.1 kg/s
        stay kg/s), a duration in seconds of three hours or more is shown in hours, and any other
        unit is left as declared. Example: ``rescale_y_axis(np.array([1500.0]), "W")`` returns
        ``([1.5], "kW")``.

        Args:
            y_values: Array-like numeric values in ``units``.
            units: The declared unit of the values, as the string of its ``Units`` member.

        Returns:
            A tuple ``(rescaled_y_values, shown_units)``.
        """
        return AxisUnit.scale(y_values, units)

    def axis_label(self, shown_units: str) -> str:
        """Return the label of the value axis: the quantity of the output and the shown unit.

        Example: an output declared in W and shown in kW is labelled ``"Power [kW]"``.

        Args:
            shown_units: The unit the chart shows the values in, as :meth:`rescale_y_axis` returned it.

        Returns:
            The label, in the form ``"Quantity [unit]"``.
        """
        return AxisUnit.label(self.declared_units, shown_units)

    @classmethod
    def wrap_title(cls, title: str, suffix: str = "") -> str:
        """Break every line of a title that is longer than :attr:`MAXIMUM_TITLE_CHARACTERS`.

        Output names are written in camel case without spaces, so a long one such as
        ``WaterTemperatureAtEndOfStepWithoutSecondaryHeatGeneratorInCelsius`` has no space to break
        at and runs off both edges of the figure. A line is broken at spaces, underscores and the
        word boundaries of camel case, and its words are kept as they are written, so the name can
        still be searched for. Example: with a limit of 20 characters,
        ``"ThermalPowerFromSecondaryHeatGenerator"`` becomes
        ``"ThermalPowerFrom\nSecondaryHeatGenerator"``.

        A ``suffix`` such as the date of a single-day chart is kept whole: it follows the last line
        when it fits there and gets a line of its own when it does not, so "January 1st" is never
        split between two lines.

        Args:
            title: The title, whose lines are separated by newlines.
            suffix: Text appended to the title as one unbroken piece; empty for none.

        Returns:
            The title with every line no longer than the limit, unless a single word is longer.
        """
        wrapped: List[str] = []
        for line in title.split("\n"):
            pieces = [piece for piece in re.split(cls.TITLE_BREAK_PATTERN, line) if piece]
            current = ""
            for piece in pieces:
                if current and len(current) + len(piece) > cls.MAXIMUM_TITLE_CHARACTERS:
                    wrapped.append(current.rstrip())
                    current = piece.lstrip()
                else:
                    current += piece
            wrapped.append(current.rstrip())
        if suffix:
            if len(wrapped[-1]) + 1 + len(suffix) > cls.MAXIMUM_TITLE_CHARACTERS:
                wrapped.append(suffix)
            else:
                wrapped[-1] = f"{wrapped[-1]} {suffix}"
        return "\n".join(wrapped)


class SavedChartResolution:
    """The one resolution, in dots per inch, at which HiSim saves every matplotlib chart.

    The output charts (line, carpet, single day, monthly bar; through :class:`ChartFontsAndSize`)
    and the lifecycle cost charts (``hisim.economics.report_plots``) are drawn at this resolution.
    A chart's size in inches and its font sizes in points do not depend on it, so the PDF report,
    which places a figure at its size in inches, shows it at the same size whatever the value;
    only the number of pixels changes. Example: a 6 x 4 inch line chart is 1800 x 1200 pixels at
    300 dpi. 300 dpi is the usual print resolution; at 600 dpi a full-year run wrote 143 MB of
    time-series images and a 201 MB report for no visible gain.
    """

    #: Dots per inch of every saved chart.
    DPI: int = 300


@dataclass
class ChartFontsAndSize:
    """Give the font sizes and figure sizes of the figures."""

    figsize = (6, 4)
    dpi = SavedChartResolution.DPI
    fontsize_title = 14
    fontsize_label = 12
    fontsize_legend = 12
    fontsize_ticks = 10
