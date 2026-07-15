"""Tests for the :class:`CSVLoader` component.

These tests exercise the injectable dataframe seam so the component can be
constructed and simulated without reading a real CSV file from disk.
"""

# clean

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
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
    return CSVLoaderConfig(
        building_name="BUI1",
        name="CSV",
        csv_filename="fake_profile.csv",
        column=column,
        loadtype=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        column_name=column_name,
        sep=",",
        decimal=".",
        multiplier=multiplier,
        output_description="Values from CSV",
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

    np.testing.assert_array_equal(loader.column_values, np.asarray(values, dtype=float))
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

    np.testing.assert_array_equal(loader.column_values, np.asarray(values, dtype=float))
    assert loader.multiplier == 1.0
    assert loader.column_name == "Profile"


@pytest.mark.base
def test_csvloader_column_deprecated_alias_returns_column_values() -> None:
    """The deprecated ``column`` attribute aliases ``column_values``.

    Issue #758 renamed the misleading public attribute ``self.column`` (an
    np.ndarray of profile values) to ``self.column_values`` to avoid clashing
    with ``self.csvconfig.column`` (an int index). A repo-wide audit found no
    remaining in-tree reads of ``.column`` on ``CSVLoader`` instances, but a
    backward-compatible read-only property is kept so any downstream code that
    still introspects ``loader.column`` gets a ``DeprecationWarning`` and the
    same array rather than a silent ``AttributeError``.
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

    with pytest.warns(DeprecationWarning, match="column_values"):
        legacy = loader.column

    # The alias returns the same underlying array object.
    assert legacy is loader.column_values
    np.testing.assert_array_equal(legacy, np.asarray(values, dtype=float))
