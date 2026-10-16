"""CSV loader component for HiSim simulations.

This module provides the :class:`CSVLoader` component, which reads a load
profile from a CSV file and exposes it as a time-indexed output channel for
HiSim simulations. The accompanying :class:`CSVLoaderConfig` dataclass
collects the file path, column index, separator, multiplier, and other
parameters needed to read a profile and feed it into a simulation as a
single output channel.

A file with a time column serves any window of the year it covers: the loader
looks the run's ``start_date`` up among the column's labels
(:class:`hisim.window_offset.WindowOffset`) and reads the window's rows, averaging
the rows inside each timestep when the file is finer than the run. A file
without one can only serve a run that starts on 1 January, because its first row
is the only row whose date is known.
"""

import warnings
from pathlib import Path
from typing import ClassVar, List, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import numpy as np
import pandas as pd


from hisim import loadtypes as lt
from hisim import utils
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from hisim.window_offset import WindowOffset
from hisim.config import ConfigBase, ComponentID, DisplayConfig, constructor
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class CSVLoaderConfig(ConfigBase):
    """Configuration of one CSV profile replayed as a component output.

    Every field is a parameter of the file being read — its name, the column, what the numbers
    in that column mean and how the file spells them — so there is no default profile to name
    and no preset. A loader is built by naming the file::

        CSVLoaderConfig.for_csv_file(
            "CSV",
            csv_filename="wind_generated_power_1_min.csv",
            column=1,
            loadtype=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.KILOWATT,
            column_name="generated_power",
        )

    The reading itself happens in :class:`CSVLoader`, once, when the component is built; this
    class only says which numbers to read.

    **The time column.** ``time_column`` names the column that holds each row's time label, so
    the loader can find the run's window in the file. A label is an ISO 8601 date and time without
    a UTC offset, such as ``2021-07-01 00:00:00`` or ``2021-07-01T00:00``, and it is read in the
    run's standard time, the clock ``start_date`` is read in: no daylight saving, so the labels
    run on an even grid across the whole year, with no skipped or repeated hour. This is how the
    weather resolves its window too (:class:`hisim.window_offset.WindowOffset`).

    The rows may be finer than the run: then each timestep reads the mean of the rows inside it.
    Example: a minutely file in a 900 s run gives each timestep the mean of its 15 one-minute rows,
    which keeps the energy of a power column such as kW (a column of energy per row would need the
    sum instead, so such a file has to be written at the run's resolution). A file coarser than
    the run, or one whose row step does not divide the run's step evenly (a 600 s file in a 900 s
    run), is refused: no row can be split into timesteps without inventing values.

    Without a time column, the file's first row is read as 1 January 00:00 and row ``t`` at
    timestep ``t``, whatever the file's resolution, so only a run that starts on 1 January can use
    it; any other start is refused.
    """

    MAIN_CLASS = "hisim.components.csvloader.CSVLoader"

    component_id: ComponentID
    #: Name of the CSV file, relative to the HiSim inputs directory.
    csv_filename: str
    #: Zero-based index of the column holding the profile values.
    column: int
    #: Physical load type the column's numbers carry, e.g. electricity or heat.
    loadtype: lt.LoadTypes
    #: Unit the column's numbers are written in.
    unit: lt.Units
    #: Human-readable name of that column, used as the output channel's display name.
    column_name: str
    #: Column separator the file uses.
    sep: str = ","
    #: Decimal separator the file uses.
    decimal: str = "."
    #: Factor applied to every loaded value before it reaches the output.
    multiplier: float = 1.0
    #: Description text of the output channel.
    output_description: str = "Values from CSV"
    #: Zero-based index of the column holding each row's time label, an ISO 8601 date and time
    #: without a UTC offset in the run's standard time; ``None`` for a file without one, which can
    #: only serve a run that starts on 1 January.
    time_column: Optional[int] = None

    @constructor(note="one column of one CSV file under the HiSim inputs directory")
    @classmethod
    def for_csv_file(
        cls,
        name: str,
        csv_filename: str,
        column: int,
        loadtype: lt.LoadTypes,
        unit: lt.Units,
        column_name: str,
        sep: str = ",",
        decimal: str = ".",
        multiplier: float = 1.0,
        output_description: str = "Values from CSV",
        time_column: Optional[int] = None,
    ) -> "CSVLoaderConfig":
        """Builds the configuration of one profile column of one CSV file.

        A recorded profile is identified by the file it lives in and the column it occupies,
        and neither has a defensible default, so this is the only builder the class has::

            CSVLoaderConfig.for_csv_file(
                "CSV", "wind_generated_power_1_min.csv", 1,
                lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT, "generated_power",
            )

        Nothing is read here — the file is opened when :class:`CSVLoader` is built — so a
        misspelled file name fails at component construction, not at configuration.

        Args:
            name: Instance name of the loader; its ``ComponentID`` is built from it.
            csv_filename: Name of the file, relative to the HiSim inputs directory.
            column: Zero-based index of the column holding the values.
            loadtype: Physical load type the values carry.
            unit: Unit the values are written in.
            column_name: Human-readable name of the column, shown on the output channel.
            sep: Column separator the file uses; a comma by default.
            decimal: Decimal separator the file uses; a full stop by default.
            multiplier: Factor applied to every value; 1.0 leaves the profile as written.
            output_description: Description of the output channel.
            time_column: Zero-based index of the column holding each row's time label, or
                ``None`` for a file without one; see the class docstring for the label format.

        Returns:
            A fresh configuration of that column; nothing about it is shared with any other
            instance.
        """
        return cls(
            component_id=ComponentID(name=name),
            csv_filename=csv_filename,
            column=column,
            loadtype=loadtype,
            unit=unit,
            column_name=column_name,
            sep=sep,
            decimal=decimal,
            multiplier=multiplier,
            output_description=output_description,
            time_column=time_column,
        )


class CSVLoader(cp.DataSourceComponent):
    r"""Csvloader class.

    Class component loads CSV file containing some
    load profile relevant to the applied setup
    function.

    The loader declares :attr:`~hisim.component.Component.MODELS_NO_DEVICE`, so cost and KPI
    computation treat it as a data source with nothing to buy and nothing to report. Do not use
    it to stand in for a real, costed device — a generator or a metered appliance replayed
    through it would silently contribute zero cost and zero indicators to the system totals
    instead of failing loudly.


    Parameters
    ----------
    name: str
        Name of load profile from CSV file
    csv_filename: str
        Name of CSV filename containing the load profile data
    column: int
        Column number where the load profile data is stored
        inside of the CSV file
    loadtype: LoadTypes,
        Load type corresponded to the data loaded
    unit: lt.Units
        Units of data loaded
    column_name: str
        Name of column where the load profile data is stored
        inside of the CSV File
    simulation_parameters: cp.SimulationParameters
        Simulation parameters used by the setup function
    sep: str
        Separator used CSV file
    decimal: str
        Decimal indicator used in the CSV file
    multiplier: float
        Multiplication factor, in case an amplification of
        the data is required

    Attributes
    ----------
    column_values_in_loaded_unit: np.ndarray
        The loaded profile values as a float array, indexed by the
        simulation timestep. The values carry the physical unit
        declared in :attr:`CSVLoaderConfig.unit`
        (``self.csvconfig.unit``); the ``_in_loaded_unit`` suffix
        signals that a unit applies even though it is not statically
        determinable from the attribute name. Read in
        :meth:`i_simulate` as
        ``self.column_values_in_loaded_unit[timestep] * self.multiplier``.
    column_name: str
        Name of the column the profile was read from (mirrors
        ``CSVLoaderConfig.column_name``).
    multiplier: float
        Multiplication factor applied to every value in
        :attr:`column_values_in_loaded_unit` when producing the output (mirrors
        ``CSVLoaderConfig.multiplier``).

    Notes
    -----
    ``column_values_in_loaded_unit`` was previously named
    ``self.column`` (issue #758) and then ``self.column_values``. The
    bare ``column`` name was misleading because it collided with
    ``self.csvconfig.column`` (an ``int`` column index), and
    ``column_values`` carried no unit suffix despite holding physical
    quantities (issue #1924). Because the unit is runtime-determined
    via :attr:`CSVLoaderConfig.unit`, the suffix ``_in_loaded_unit``
    is used instead of a fixed ``_in_<unit>`` suffix. A repo-wide
    audit (``grep -rn "\.column\b" --include="*.py"`` excluding
    ``csvconfig.column``, ``.columns``, and ``column_values``) found
    no remaining reads of ``.column`` on :class:`CSVLoader``
    instances, so the rename is safe in-tree. Deprecated ``column``
    and ``column_values`` properties are kept as backward-compatible
    aliases for any downstream code that may still introspect
    component state.

    """

    cost_relevance = CostRelevance.FREE_OF_COST

    Output1: str = "CSVProfile"

    # A loader that replays a recorded profile is a data source, not a device: it has
    # nothing to buy, nothing to run and no indicators of its own. Declaring that is
    # what lets a setup built from it compute costs and KPIs at all;
    # see Component.MODELS_NO_DEVICE.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    def __init__(
        self,
        config: CSVLoaderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
        inputs_dir: Path | None = None,
        dataframe: pd.DataFrame | None = None,
    ) -> None:
        """Initialize the class.

        Parameters
        ----------
        config:
            Configuration of the CSV loader.
        my_simulation_parameters:
            Simulation parameters used by the setup function.
        my_display_config:
            Display configuration for the component.
        inputs_dir:
            Optional directory that holds the CSV input file. Defaults to
            ``Path(utils.HISIMPATH["inputs"])`` when ``dataframe`` is not
            supplied. Ignored when ``dataframe`` is given.
        dataframe:
            Optional pre-loaded :class:`pandas.DataFrame` used instead of
            reading the CSV file from disk. When provided, ``inputs_dir`` and
            ``self.csvconfig.csv_filename`` are ignored and no file system
            access is performed. This is the seam used by unit tests so that
            :class:`CSVLoader` can be constructed without a real CSV file.
        """
        self.csvconfig: CSVLoaderConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: CSVLoaderConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.output1_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Output1,
            self.csvconfig.loadtype,
            self.csvconfig.unit,
            output_description="CSV loader output 1",
        )
        self.output1_channel.display_name = self.csvconfig.column_name
        self.multiplier: float = self.csvconfig.multiplier

        if dataframe is not None:
            loaded_dataframe = dataframe
        else:
            if inputs_dir is None:
                inputs_dir = Path(utils.HISIMPATH["inputs"])
            loaded_dataframe = self._load_dataframe(inputs_dir)
        if self.csvconfig.column >= len(loaded_dataframe.columns):
            raise RuntimeError(
                f"Invalid column number for the csv file: {self.csvconfig.column}. Found {len(loaded_dataframe.columns)} columns."
            )
        dfcolumn = loaded_dataframe.iloc[:, self.csvconfig.column]
        self.column_name: str = self.csvconfig.column_name
        if len(dfcolumn) < self.my_simulation_parameters.timesteps:
            raise ValueError(
                f"CSV '{self.csvconfig.csv_filename}' has {len(dfcolumn)} rows, "
                f"which is fewer than the {self.my_simulation_parameters.timesteps} "
                "simulation timesteps."
            )
        all_values_in_loaded_unit = dfcolumn.to_numpy(dtype=float)
        timesteps = self.my_simulation_parameters.timesteps
        if self.csvconfig.time_column is None:
            self._refuse_a_window_after_the_first_of_january()
            window_offset = 0
            rows_per_timestep = 1
            rows_read_in_loaded_unit = all_values_in_loaded_unit
        else:
            labels = self._time_labels(loaded_dataframe)
            rows_per_timestep = self._rows_per_timestep(labels)
            window_offset = WindowOffset.resolve(
                labels=labels,
                window_start=self.my_simulation_parameters.start_date,
                timesteps=timesteps * rows_per_timestep,
                seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep // rows_per_timestep,
                source=f"the CSV file '{self.csvconfig.csv_filename}' of {self.component_name}",
            )
            rows_read_in_loaded_unit = all_values_in_loaded_unit[
                window_offset:window_offset + timesteps * rows_per_timestep
            ]
        if not np.all(np.isfinite(rows_read_in_loaded_unit)):
            bad_rows = window_offset + np.where(~np.isfinite(rows_read_in_loaded_unit))[0]
            raise ValueError(
                f"CSV '{self.csvconfig.csv_filename}' column "
                f"'{self.column_name}' contains non-finite values "
                f"(NaN/inf) at rows {bad_rows[:10].tolist()}"
            )
        if rows_per_timestep == 1:
            self.column_values_in_loaded_unit: np.ndarray = rows_read_in_loaded_unit
        else:
            self.column_values_in_loaded_unit = rows_read_in_loaded_unit.reshape(timesteps, rows_per_timestep).mean(
                axis=1
            )
        self.values: List[float] = []

    def _refuse_a_window_after_the_first_of_january(self) -> None:
        """Refuse a run that does not start on 1 January, for a file without a time column.

        Without a time column the loader knows the date of no row, so it reads the first row as
        1 January 00:00 and timestep ``t`` as row ``t``. A run from 1 July would then read January's
        values under July's labels, without any sign of it. Example: the minutely wind profile of
        ``electrolyzer_with_renewables`` without its time column serves a run from 1 January 2021,
        and a run from 1 July 2021 is refused here.

        Raises:
            ValueError: If the run's ``start_date`` is not 1 January.
        """
        start_date = self.my_simulation_parameters.start_date
        if (start_date.month, start_date.day) != (1, 1):
            raise ValueError(
                f"{self.component_name} reads the CSV file '{self.csvconfig.csv_filename}' without a time column, "
                f"so it can only serve a run that starts on 1 January, and this run starts on {start_date}. "
                "Name the column that holds each row's time label as time_column (an ISO 8601 date and time "
                "without a UTC offset, in standard time), so the loader can find the window in the file."
            )

    def _rows_per_timestep(self, labels: pd.DatetimeIndex) -> int:
        """Return how many rows of the file one timestep of the run averages.

        The row step is the distance of the first two labels. Example: a minutely file in a 900 s
        run averages 15 rows per timestep, and a minutely file in a 60 s run reads one. Whether the
        rows keep that step across the run's window is checked by :meth:`WindowOffset.resolve`.

        Args:
            labels: The file's time labels, one per row.

        Returns:
            The number of rows per timestep, at least 1.

        Raises:
            ValueError: If the file has fewer than two rows, if its row step is not a positive whole
                number of seconds, if it is coarser than the run's step, or if it does not divide the
                run's step evenly.
        """
        filename = self.csvconfig.csv_filename
        if len(labels) < 2:
            raise ValueError(
                f"The CSV file '{filename}' of {self.component_name} has {len(labels)} time label(s), so the "
                "step of its rows is not known."
            )
        row_step = labels[1] - labels[0]
        timestep = pd.Timedelta(seconds=self.my_simulation_parameters.seconds_per_timestep)
        if row_step <= pd.Timedelta(0) or row_step % pd.Timedelta(seconds=1) != pd.Timedelta(0):
            raise ValueError(
                f"The rows of the CSV file '{filename}' of {self.component_name} are {row_step} apart, which is "
                "not a positive whole number of seconds."
            )
        if row_step > timestep:
            raise ValueError(
                f"The rows of the CSV file '{filename}' of {self.component_name} are {row_step} apart, coarser "
                f"than the run's timestep of {timestep}. A row cannot be split into timesteps; write the file "
                "at the run's resolution or finer."
            )
        if timestep % row_step != pd.Timedelta(0):
            raise ValueError(
                f"The rows of the CSV file '{filename}' of {self.component_name} are {row_step} apart, which "
                f"does not divide the run's timestep of {timestep} evenly, so a timestep cannot average whole "
                "rows."
            )
        return int(timestep // row_step)

    def _time_labels(self, dataframe: pd.DataFrame) -> pd.DatetimeIndex:
        """Read the time column of the file as the labels the run's window is looked up in.

        A label is an ISO 8601 date and time without a UTC offset, read in the run's standard time.
        Example: ``2021-07-01 00:00:00`` is the label of the row a run from 1 July 2021 reads at its
        timestep 0.

        Args:
            dataframe: The file's rows, as read.

        Returns:
            One label per row, without time zone.

        Raises:
            ValueError: If the time column is not a column of the file or is the value column, if a
                label is not an ISO 8601 date and time, or if the labels carry a UTC offset.
        """
        time_column = self.csvconfig.time_column
        filename = self.csvconfig.csv_filename
        if time_column is None or not 0 <= time_column < len(dataframe.columns):
            raise ValueError(
                f"The time column {time_column} of {self.component_name} is not a column of the CSV file "
                f"'{filename}', which has {len(dataframe.columns)} columns."
            )
        if time_column == self.csvconfig.column:
            raise ValueError(
                f"{self.component_name} names column {time_column} of the CSV file '{filename}' as both its time "
                "column and its value column."
            )
        try:
            labels = pd.DatetimeIndex(pd.to_datetime(dataframe.iloc[:, time_column], format="ISO8601"))
        except (ValueError, TypeError) as error:
            raise ValueError(
                f"The time column {time_column} of the CSV file '{filename}' of {self.component_name} holds a "
                f"label that is not an ISO 8601 date and time, such as 2021-07-01 00:00:00: {error}"
            ) from error
        if labels.tz is not None:
            raise ValueError(
                f"The time labels of the CSV file '{filename}' of {self.component_name} carry the UTC offset "
                f"{labels.tz}. The loader reads labels without an offset, in the run's standard time; write "
                "them without the offset, converted to standard time."
            )
        return labels

    @property
    def column(self) -> np.ndarray:
        """Deprecated alias for :attr:`column_values_in_loaded_unit`.

        The instance attribute previously named ``self.column`` held the
        loaded profile values as a :class:`numpy.ndarray` (indexed by
        timestep), which collided with ``self.csvconfig.column`` (an
        ``int`` column index). It was renamed to ``column_values``
        for clarity (see issue #758) and later to
        :attr:`column_values_in_loaded_unit` to carry its physical
        unit (see issue #1924). A repo-wide audit found no remaining
        references to ``.column`` on :class:`CSVLoader` instances,
        but this alias is kept as a backward-compatible shim for any
        downstream code that may still introspect component state.

        Returns
        -------
        np.ndarray
            The loaded profile values (same object as
            :attr:`column_values_in_loaded_unit`).

        Warns
        -----
        DeprecationWarning
            Always, on every access. Use :attr:`column_values_in_loaded_unit` instead.
        """
        warnings.warn(
            "CSVLoader.column is deprecated; use CSVLoader.column_values_in_loaded_unit "
            "instead. The attribute was renamed because the bare name "
            "'column' was confused with the integer column index "
            "CSVLoaderConfig.column.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.column_values_in_loaded_unit

    @property
    def column_values(self) -> np.ndarray:
        """Deprecated alias for :attr:`column_values_in_loaded_unit`.

        The attribute was renamed from ``column_values`` to
        :attr:`column_values_in_loaded_unit` to signal that the loaded
        profile values carry a physical unit (the one declared in
        :attr:`CSVLoaderConfig.unit`), which is not statically
        determinable from the attribute name alone (see issue #1924).
        This alias is kept as a backward-compatible shim for any
        downstream code that may still reference ``column_values``.

        Returns
        -------
        np.ndarray
            The loaded profile values (same object as
            :attr:`column_values_in_loaded_unit`).

        Warns
        -----
        DeprecationWarning
            Always, on every access. Use
            :attr:`column_values_in_loaded_unit` instead.
        """
        warnings.warn(
            "CSVLoader.column_values is deprecated; use "
            "CSVLoader.column_values_in_loaded_unit instead. The "
            "attribute was renamed to carry its physical unit, which "
            "is runtime-determined via CSVLoaderConfig.unit.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.column_values_in_loaded_unit

    @staticmethod
    def _read_csv(config: CSVLoaderConfig, inputs_dir: Path) -> pd.DataFrame:
        """Read the CSV referenced by *config* from *inputs_dir*.

        Shared helper so the file-system read lives in exactly one place and
        can be reused by both :meth:`from_config_file` and
        :meth:`_load_dataframe` without duplicating the ``pandas.read_csv``
        arguments.
        """
        return pd.read_csv(
            inputs_dir / config.csv_filename,
            sep=config.sep,
            decimal=config.decimal,
        )

    @classmethod
    def from_config_file(
        cls,
        config: CSVLoaderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
        inputs_dir: Path | None = None,
    ) -> "CSVLoader":
        """Construct a :class:`CSVLoader` by reading its CSV file from disk.

        This is the thin I/O factory that keeps file-system access out of
        :meth:`__init__`: it reads the bytes referenced by *config* and then
        delegates to :meth:`__init__` with the already-loaded
        :class:`pandas.DataFrame`. Callers -- and in particular unit tests --
        that already hold a loaded dataframe can pass it straight to
        ``__init__`` via the ``dataframe`` argument and skip disk access
        entirely, making :class:`CSVLoader` cheaply constructible without a
        real CSV file.

        Parameters
        ----------
        config:
            Configuration of the CSV loader.
        my_simulation_parameters:
            Simulation parameters used by the setup function.
        my_display_config:
            Display configuration for the component.
        inputs_dir:
            Optional directory that holds the CSV input file. Defaults to
            ``Path(utils.HISIMPATH["inputs"])`` when not supplied.
        """
        if inputs_dir is None:
            inputs_dir = Path(utils.HISIMPATH["inputs"])
        dataframe = cls._read_csv(config, inputs_dir)
        return cls(
            config=config,
            my_simulation_parameters=my_simulation_parameters,
            my_display_config=my_display_config,
            dataframe=dataframe,
        )

    def _load_dataframe(self, inputs_dir: Path) -> pd.DataFrame:
        """Read the configured CSV file into a :class:`pandas.DataFrame`.

        Thin seam around :meth:`_read_csv` so the disk read can be replaced
        (for example in unit tests) without touching the file system. Kept for
        backward compatibility with :meth:`__init__`'s default read path.
        """
        return self._read_csv(self.csvconfig, inputs_dir)

    def i_restore_state(self) -> None:
        """No-op override of the component state-restore lifecycle hook.

        CSVLoader holds no mutable runtime state across timesteps, so
        restoring a previously saved state requires no action.
        """
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Write the profile value for *timestep* to the output channel.

        Looks up ``self.column_values_in_loaded_unit[timestep]``, multiplies it by
        ``self.multiplier``, and stores the result in *stsv* via
        ``self.output1_channel``.

        Args:
            timestep: Index of the current simulation timestep.
            stsv: Single-time-step values container to write the output into.
            force_convergence: Unused; accepted for interface compatibility
                with the component lifecycle.
        """
        stsv.set_output_value(self.output1_channel, float(self.column_values_in_loaded_unit[timestep]) * self.multiplier)

    def i_prepare_simulation(self) -> None:
        """No-op override of the pre-simulation preparation hook.

        All profile data is loaded in ``__init__``, so no additional
        preparation is needed before the simulation loop begins.
        """
        pass

    def i_save_state(self) -> None:
        """No-op override of the component state-save lifecycle hook.

        CSVLoader holds no mutable runtime state across timesteps, so
        there is nothing to snapshot.
        """
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """No-op override of the post-step consistency-check hook.

        Args:
            timestep: Index of the current simulation timestep.
            stsv: Single-time-step values container (unused).
        """
        pass

    def write_to_report(self) -> List[str]:
        """Return the loader configuration as a string dictionary for reports.

        Returns:
            The string dictionary produced by
            ``self.csvconfig.get_string_dict()``, suitable for inclusion in
            the simulation report.
        """
        return self.csvconfig.get_string_dict()
