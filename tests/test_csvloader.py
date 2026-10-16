"""Tests for the :class:`CSVLoader` component.

These tests exercise the injectable dataframe seam so the component can be
constructed and simulated without reading a real CSV file from disk.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components.csvloader import CSVLoader, CSVLoaderConfig
from hisim.simulationparameters import SimulationParameters

# Conversion factors used to derive the seconds-per-timestep of a
# full-year simulation from the requested number of timesteps:
# days/year -> hours/day -> seconds/hour -> seconds/timestep.
SECONDS_PER_HOUR: int = 3600
HOURS_PER_DAY: int = 24
DAYS_PER_YEAR: int = 365


def _make_config(
    column: int = 0,
    multiplier: float = 1.0,
    column_name: str = "Profile",
) -> CSVLoaderConfig:
    """Build a minimal :class:`CSVLoaderConfig` for the tests."""
    return CSVLoaderConfig.for_csv_file(
        "CSV",
        csv_filename="fake_profile.csv",
        column=column,
        loadtype=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        column_name=column_name,
        multiplier=multiplier,
    )


def _make_simulation_parameters(timesteps: int) -> SimulationParameters:
    """Build :class:`SimulationParameters` for the requested number of timesteps."""
    return SimulationParameters.full_year(
        year=2021, seconds_per_timestep=DAYS_PER_YEAR * HOURS_PER_DAY * SECONDS_PER_HOUR // timesteps
    )


@pytest.mark.base
def test_csvloader_construction_with_dataframe_seam() -> None:
    """Constructing with an in-memory dataframe skips disk I/O entirely."""
    values = [float(i) for i in range(24)]
    dataframe = pd.DataFrame({"Profile": values})
    sim_params = _make_simulation_parameters(timesteps=24)
    config = _make_config(column=0, multiplier=2.0)

    loader = CSVLoader(
        config=config,
        my_simulation_parameters=sim_params,
        dataframe=dataframe,
    )

    np.testing.assert_array_equal(loader.column_values_in_loaded_unit, np.asarray(values, dtype=float))
    assert loader.multiplier == 2.0
    assert loader.column_name == "Profile"
    # The csv_filename is not consulted when a dataframe is supplied.
    assert loader.csvconfig.csv_filename == "fake_profile.csv"


@pytest.mark.base
def test_csvloader_i_simulate_applies_multiplier() -> None:
    """``i_simulate`` reads the column value and applies the configured multiplier."""
    values = [10.0, 20.0, 30.0, 40.0]
    dataframe = pd.DataFrame({"Profile": values})
    sim_params = _make_simulation_parameters(timesteps=len(values))
    config = _make_config(column=0, multiplier=1.5)

    loader = CSVLoader(
        config=config,
        my_simulation_parameters=sim_params,
        dataframe=dataframe,
    )

    stsv = cp.SingleTimeStepValues(1)
    loader.output1_channel.global_index = 0

    for index, expected in enumerate([15.0, 30.0, 45.0, 60.0]):
        loader.i_simulate(index, stsv, force_convergence=False)
        assert stsv.values[0] == pytest.approx(expected)


@pytest.mark.base
def test_csvloader_invalid_column_raises() -> None:
    """Column index validation still triggers when a dataframe is supplied."""
    dataframe = pd.DataFrame({"Profile": [1.0, 2.0, 3.0]})
    sim_params = _make_simulation_parameters(timesteps=3)
    config = _make_config(column=1)  # only one column exists

    with pytest.raises(RuntimeError, match="Invalid column number"):
        CSVLoader(
            config=config,
            my_simulation_parameters=sim_params,
            dataframe=dataframe,
        )


@pytest.mark.base
def test_csvloader_too_few_rows_raises() -> None:
    """Length validation still triggers when a dataframe is too short."""
    dataframe = pd.DataFrame({"Profile": [1.0, 2.0]})
    sim_params = _make_simulation_parameters(timesteps=24)
    config = _make_config(column=0)

    with pytest.raises(ValueError, match="fewer than the .* simulation timesteps"):
        CSVLoader(
            config=config,
            my_simulation_parameters=sim_params,
            dataframe=dataframe,
        )


@pytest.mark.base
def test_csvloader_from_config_file_reads_disk(tmp_path: Path) -> None:
    """``from_config_file`` reads the CSV from disk and wires up the loader.

    The factory performs the file-system read (here against a temporary
    directory, not the real inputs path) and then delegates to ``__init__``
    with the loaded dataframe, so the resulting component behaves exactly like
    one built through the default read path.
    """
    values = [1.0, 2.0, 3.0, 4.0]
    pd.DataFrame({"Profile": values}).to_csv(
        tmp_path / "fake_profile.csv", index=False, sep=",", decimal=".",
    )
    sim_params = _make_simulation_parameters(timesteps=len(values))
    config = _make_config(column=0, multiplier=1.0)

    loader = CSVLoader.from_config_file(
        config=config,
        my_simulation_parameters=sim_params,
        inputs_dir=tmp_path,
    )

    np.testing.assert_array_equal(loader.column_values_in_loaded_unit, np.asarray(values, dtype=float))
    assert loader.multiplier == 1.0
    assert loader.column_name == "Profile"


@pytest.mark.base
def test_csvloader_column_deprecated_alias_returns_loaded_values() -> None:
    """The deprecated ``column`` attribute aliases ``column_values_in_loaded_unit``.

    Issue #758 renamed the misleading public attribute ``self.column`` (an
    np.ndarray of profile values) to ``self.column_values`` to avoid clashing
    with ``self.csvconfig.column`` (an int index). Issue #1924 later renamed
    ``self.column_values`` to ``self.column_values_in_loaded_unit`` to carry
    its physical unit. A repo-wide audit found no remaining in-tree reads of
    ``.column`` on ``CSVLoader`` instances, but a backward-compatible
    read-only property is kept so any downstream code that still introspects
    ``loader.column`` gets a ``DeprecationWarning`` and the same array rather
    than a silent ``AttributeError``.
    """
    values = [float(i) for i in range(8)]
    dataframe = pd.DataFrame({"Profile": values})
    sim_params = _make_simulation_parameters(timesteps=len(values))
    config = _make_config(column=0, multiplier=1.0)

    loader = CSVLoader(
        config=config,
        my_simulation_parameters=sim_params,
        dataframe=dataframe,
    )

    with pytest.warns(DeprecationWarning, match=r"CSVLoader\.column is deprecated"):
        legacy = loader.column

    # The alias returns the same underlying array object.
    assert legacy is loader.column_values_in_loaded_unit
    np.testing.assert_array_equal(legacy, np.asarray(values, dtype=float))


@pytest.mark.base
def test_csvloader_column_values_deprecated_alias_returns_loaded_values() -> None:
    """The deprecated ``column_values`` attribute aliases ``column_values_in_loaded_unit``.

    Issue #1924 renamed ``self.column_values`` to
    ``self.column_values_in_loaded_unit`` to signal that the loaded profile
    values carry a physical unit (the one declared in
    :attr:`CSVLoaderConfig.unit`), which is not statically determinable. A
    backward-compatible read-only property is kept so any downstream code that
    still references ``loader.column_values`` gets a ``DeprecationWarning``
    and the same array rather than a silent ``AttributeError``.
    """
    values = [float(i) for i in range(8)]
    dataframe = pd.DataFrame({"Profile": values})
    sim_params = _make_simulation_parameters(timesteps=len(values))
    config = _make_config(column=0, multiplier=1.0)

    loader = CSVLoader(
        config=config,
        my_simulation_parameters=sim_params,
        dataframe=dataframe,
    )

    with pytest.warns(DeprecationWarning, match=r"CSVLoader\.column_values is deprecated"):
        legacy = loader.column_values

    # The alias returns the same underlying array object.
    assert legacy is loader.column_values_in_loaded_unit
    np.testing.assert_array_equal(legacy, np.asarray(values, dtype=float))


@pytest.mark.base
@pytest.mark.parametrize(
    "raw_value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="pos_inf"),
        pytest.param(float("-inf"), id="neg_inf"),
    ],
)
def test_csvloader_non_finite_values_raise(raw_value: float) -> None:
    """Non-finite CSV values (NaN/inf) are rejected at construction.

    Empty cells or literal ``nan``/``inf`` tokens in a CSV are parsed by
    pandas as non-finite floats and previously propagated silently through
    ``i_simulate`` into downstream components, surfacing as NaN in KPIs
    thousands of timesteps later with no traceable cause (issue #1059). The
    loader now fails fast at construction with a diagnostic that names the
    file, column, and offending rows.

    The parametrized non-finite inputs cover the three pandas-parsable
    non-finite float values; append a parameter to extend the guard's
    coverage instead of copy-pasting a whole test function.
    """
    dataframe = pd.DataFrame({"Profile": [1.0, raw_value, 3.0]})
    sim_params = _make_simulation_parameters(timesteps=len(dataframe))
    config = _make_config(column=0)

    with pytest.raises(ValueError, match="contains non-finite values"):
        CSVLoader(
            config=config,
            my_simulation_parameters=sim_params,
            dataframe=dataframe,
        )


@pytest.mark.base
def test_csvloader_non_finite_error_message_names_file_column_and_rows() -> None:
    """The diagnostic names the CSV file, the column, and the offending rows."""
    # Rows 2 and 5 are NaN; the message should report both (capped at 10).
    values = [1.0, 2.0, float("nan"), 4.0, 5.0, float("nan")]
    dataframe = pd.DataFrame({"Profile": values})
    sim_params = _make_simulation_parameters(timesteps=len(values))
    config = _make_config(column=0, column_name="Profile")

    with pytest.raises(ValueError) as excinfo:
        CSVLoader(
            config=config,
            my_simulation_parameters=sim_params,
            dataframe=dataframe,
        )

    message = str(excinfo.value)
    assert "fake_profile.csv" in message
    assert "'Profile'" in message
    assert "non-finite" in message
    assert "[2, 5]" in message


class TestTimeColumn:
    """The loader finds the run's window in a file with a time column, and refuses a mid-year run without one.

    The fixture is a minutely profile of 2021 whose value is its own row number, so the value a timestep reads
    says which row it read. In the run's standard time 1 July 2021 00:00 is row 260 640 (181 days of minutes),
    the row the weather resolves too (``tests/test_window_offset.py``).
    """

    #: The year of the fixture profile and of every run here.
    YEAR = 2021
    #: The resolution of the fixture profile and of the runs, in seconds.
    SECONDS_PER_TIMESTEP = 60
    #: The rows of a minutely profile of 2021.
    ROWS_OF_A_MINUTELY_YEAR = 365 * 24 * 60
    #: The row of the fixture labelled 1 July 2021 00:00.
    FIRST_OF_JULY_ROW = 181 * 24 * 60
    #: The minutely wind profile the setup ``electrolyzer_with_renewables`` reads, with its time in column 0.
    WIND_PROFILE = "wind_generated_power_1_min.csv"

    @classmethod
    def minutely_year(cls, label_format: str = "%Y-%m-%d %H:%M:%S") -> pd.DataFrame:
        """Build the fixture: a time column and a value column holding each row's own number.

        Args:
            label_format: How the time labels are written, as a ``strftime`` format.

        Returns:
            The profile, with the time labels in column 0 and the values in column 1.
        """
        labels = pd.date_range(f"{cls.YEAR}-01-01", periods=cls.ROWS_OF_A_MINUTELY_YEAR, freq="min")
        return pd.DataFrame(
            {"time": labels.strftime(label_format), "value": np.arange(cls.ROWS_OF_A_MINUTELY_YEAR, dtype=float)}
        )

    @classmethod
    def year_at(cls, row_step_in_seconds: int) -> pd.DataFrame:
        """Build a profile of the fixture's year at another row step, each value its own row number.

        Args:
            row_step_in_seconds: The distance of two rows, in seconds.

        Returns:
            The profile, with the time labels in column 0 and the values in column 1.
        """
        labels = pd.date_range(f"{cls.YEAR}-01-01", f"{cls.YEAR}-12-31 23:59", freq=f"{row_step_in_seconds}s")
        return pd.DataFrame(
            {"time": labels.strftime("%Y-%m-%d %H:%M:%S"), "value": np.arange(len(labels), dtype=float)}
        )

    @staticmethod
    def config(time_column: int | None) -> CSVLoaderConfig:
        """Build the configuration of the fixture's value column, with or without its time column.

        Args:
            time_column: The index of the time column, or ``None`` for a file read without one.

        Returns:
            The configuration.
        """
        return CSVLoaderConfig.for_csv_file(
            "CSV",
            csv_filename="fake_profile.csv",
            column=1,
            loadtype=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            column_name="value",
            time_column=time_column,
        )

    @staticmethod
    def outputs(loader: CSVLoader) -> list[float]:
        """Step the loader through every timestep of its run and collect its output.

        Args:
            loader: The loader to step.

        Returns:
            The output of each timestep.
        """
        stsv = cp.SingleTimeStepValues(1)
        loader.output1_channel.global_index = 0
        values = []
        for timestep in range(loader.my_simulation_parameters.timesteps):
            loader.i_simulate(timestep, stsv, force_convergence=False)
            values.append(stsv.values[0])
        return values

    @pytest.mark.base
    def test_a_july_run_with_a_time_column_reads_the_july_rows(self) -> None:
        """Catches a July run reading January: timestep 0 must read the row labelled 1 July 00:00."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)
        loader = CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                           dataframe=self.minutely_year())

        expected = np.arange(self.FIRST_OF_JULY_ROW, self.FIRST_OF_JULY_ROW + parameters.timesteps, dtype=float)
        np.testing.assert_array_equal(loader.column_values_in_loaded_unit, expected)
        assert self.outputs(loader) == expected.tolist()

    @pytest.mark.base
    def test_the_time_labels_may_be_written_with_a_t(self) -> None:
        """Catches the loader accepting only one spelling of ISO 8601: ``2021-07-01T00:00`` is a label too."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)
        loader = CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                           dataframe=self.minutely_year("%Y-%m-%dT%H:%M"))

        assert loader.column_values_in_loaded_unit[0] == self.FIRST_OF_JULY_ROW

    @pytest.mark.base
    def test_a_july_run_without_a_time_column_is_refused(self) -> None:
        """Catches a file without a time column serving a July run with its January rows."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)

        with pytest.raises(ValueError, match="without a time column.*only serve a run that starts on 1 January"):
            CSVLoader(config=self.config(time_column=None), my_simulation_parameters=parameters,
                      dataframe=self.minutely_year())

    @pytest.mark.base
    def test_a_january_run_without_a_time_column_reads_the_file_as_before(self) -> None:
        """Catches the time column changing a January run that has none: it reads row ``t`` at timestep ``t``.

        The loader keeps the whole column, byte for byte, as it did before the time column existed, and the
        outputs are the first rows. A run with the time column reads the same values over its window.
        """
        parameters = SimulationParameters.one_week_only(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)
        dataframe = self.minutely_year()
        without = CSVLoader(config=self.config(time_column=None), my_simulation_parameters=parameters,
                            dataframe=dataframe)
        with_time = CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                              dataframe=dataframe)

        assert without.column_values_in_loaded_unit.tobytes() == dataframe["value"].to_numpy(dtype=float).tobytes()
        outputs_without = self.outputs(without)
        assert outputs_without == list(range(parameters.timesteps))
        assert outputs_without == self.outputs(with_time)

    @pytest.mark.base
    def test_a_finer_file_is_averaged_over_each_timestep_and_keeps_its_energy(self) -> None:
        """Catches a minutely file in a 15-minute run reading consecutive minutes as quarter hours.

        Each 900 s timestep must read the mean of its 15 one-minute rows: from 1 July, timestep ``t`` averages
        rows ``260 640 + 15 t`` to ``260 640 + 15 t + 14``, whose mean is ``260 640 + 15 t + 7``. The energy of the
        window, each value times its step, is the same at both resolutions.
        """
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=900)
        dataframe = self.minutely_year()
        loader = CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                           dataframe=dataframe)

        rows_per_timestep = 15
        expected = self.FIRST_OF_JULY_ROW + rows_per_timestep * np.arange(parameters.timesteps) + 7.0
        np.testing.assert_array_equal(loader.column_values_in_loaded_unit, expected)
        window_rows = dataframe["value"].to_numpy()[
            self.FIRST_OF_JULY_ROW:self.FIRST_OF_JULY_ROW + parameters.timesteps * rows_per_timestep
        ]
        energy_of_the_rows = float(window_rows.sum() * self.SECONDS_PER_TIMESTEP)
        assert float(loader.column_values_in_loaded_unit.sum() * 900) == pytest.approx(energy_of_the_rows, rel=1e-12)

    @pytest.mark.base
    def test_a_file_coarser_than_the_run_is_refused(self) -> None:
        """Catches a quarter-hourly file serving a 60 s run, which would have to invent the minutes between rows."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)

        with pytest.raises(ValueError, match="coarser than the run's timestep"):
            CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                      dataframe=self.year_at(row_step_in_seconds=900))

    @pytest.mark.base
    def test_a_row_step_that_does_not_divide_the_timestep_is_refused(self) -> None:
        """Catches a 600 s file serving a 900 s run, whose timesteps would each hold one and a half rows."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=900)

        with pytest.raises(ValueError, match="does not divide the run's timestep"):
            CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                      dataframe=self.year_at(row_step_in_seconds=600))

    @pytest.mark.base
    def test_a_window_the_file_does_not_cover_is_refused(self) -> None:
        """Catches a file of the first half year serving a July run by reading past its end."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)

        with pytest.raises(ValueError, match="has no row labelled 2021-07-01 00:00:00"):
            CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters,
                      dataframe=self.minutely_year().iloc[: self.FIRST_OF_JULY_ROW])

    @pytest.mark.base
    @pytest.mark.parametrize(
        "label, message",
        [
            pytest.param("2021-07-01 00:00:00+02:00", "carry the UTC offset", id="utc_offset"),
            pytest.param("first of July", "not an ISO 8601 date and time", id="not_a_date"),
        ],
    )
    def test_a_label_the_loader_cannot_read_in_standard_time_is_refused(self, label: str, message: str) -> None:
        """Catches a label with a UTC offset, or one that is no date, being read in some clock of its own.

        Args:
            label: The label every row of the time column carries.
            message: The part of the refusal that names the reason.
        """
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)
        dataframe = self.minutely_year()
        dataframe["time"] = label

        with pytest.raises(ValueError, match=message):
            CSVLoader(config=self.config(time_column=0), my_simulation_parameters=parameters, dataframe=dataframe)

    @pytest.mark.base
    def test_a_time_column_that_is_the_value_column_is_refused(self) -> None:
        """Catches one column named as both the time and the values."""
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)

        with pytest.raises(ValueError, match="both its time column and its value column"):
            CSVLoader(config=self.config(time_column=1), my_simulation_parameters=parameters,
                      dataframe=self.minutely_year())

    @pytest.mark.base
    def test_the_shipped_wind_profile_serves_a_july_run_from_its_time_column(self) -> None:
        """Catches the shipped wind profile's time labels not being in the format the loader reads.

        Its timestep 0 must be the value the file writes on the row labelled 2021-07-01 00:00:00.
        """
        parameters = SimulationParameters.one_week_july(year=self.YEAR, seconds_per_timestep=self.SECONDS_PER_TIMESTEP)
        config = CSVLoaderConfig.for_csv_file(
            "CSV",
            csv_filename=self.WIND_PROFILE,
            column=1,
            loadtype=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.KILOWATT,
            column_name="generated_power",
            time_column=0,
        )
        loader = CSVLoader.from_config_file(config=config, my_simulation_parameters=parameters)
        profile = pd.read_csv(Path(utils.HISIMPATH["inputs"]) / self.WIND_PROFILE)
        july_rows = profile.index[profile["time"] == "2021-07-01 00:00:00"]

        assert list(july_rows) == [self.FIRST_OF_JULY_ROW]
        assert loader.column_values_in_loaded_unit[0] == profile["generated_power"].iloc[self.FIRST_OF_JULY_ROW]
        assert len(loader.column_values_in_loaded_unit) == parameters.timesteps
