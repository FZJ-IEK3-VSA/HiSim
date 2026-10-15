"""Tests for the days a local LoadProfileGenerator run is asked to simulate.

The occupancy used to name the days day-first (``01-01-2021`` to ``08-01-2021`` for a week), which the
LoadProfileGenerator reads month-first: a week was simulated from 1 January to 1 August, and the profile of
the first week differs from the one a request of that week gives, because the generator's household
behaviour depends on how many days it simulates. ISO 8601 is read as written.
"""

import datetime
import json
import pathlib

import pytest
from utspclient.helpers.lpgdata import Households

from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    UtspLpgConnector,
    UtspLpgConnectorConfig,
)
from hisim.components.pylpg_workspace import PylpgWorkspace
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__status__ = "development"


def parameters_of(start: datetime.datetime, days: int, directory: pathlib.Path) -> SimulationParameters:
    """Return the parameters of a run of ``days`` days from ``start`` at 60 s, writing below ``directory``.

    Args:
        start: the run's start.
        days: the run's length in days.
        directory: where the run writes and caches.

    Returns:
        SimulationParameters: the parameters.
    """
    return SimulationParameters(
        start_date=start,
        end_date=start + datetime.timedelta(days=days),
        seconds_per_timestep=60,
        result_directory=str(directory / "results"),
        post_processing_options=[],
        cache_dir_path=str(directory / "cache"),
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("days", "requested"),
    [(7, ("2021-01-01", "2021-01-08")), (365, ("2021-01-01", "2022-01-01")), (2, ("2021-01-01", "2021-01-03"))],
)
def test_a_local_request_names_its_days_in_iso_8601(days: int, requested: tuple, tmp_path: pathlib.Path) -> None:
    """Catches a day-first date, which the LoadProfileGenerator reads month-first (8 January as 1 August)."""
    parameters = parameters_of(datetime.datetime(2021, 1, 1), days, tmp_path)

    assert UtspLpgConnector.local_lpg_request_days(parameters) == requested


@pytest.mark.utsp
def test_the_local_generator_simulates_the_requested_days(tmp_path: pathlib.Path) -> None:
    """Catches a request the local LoadProfileGenerator reads as other days than the ones meant.

    A run of two days from 1 January asks for 1 to 3 January, the end day included, and gets three days of
    minutes from 1 January 00:00; the day-first request got 32 days, to 1 February.
    """
    parameters = parameters_of(datetime.datetime(2021, 1, 1), 2, tmp_path)
    config = UtspLpgConnectorConfig.for_household(
        "Occupancy", Households.CHR01_Couple_both_at_Work, data_acquisition_mode=LpgDataAcquisitionMode.USE_LOCAL_LPG
    )
    occupancy = UtspLpgConnector(my_simulation_parameters=parameters, config=config)
    try:
        files = occupancy.get_profiles_from_local_lpg(config.household)
        high_activity_file = files[4]
        with open(high_activity_file, encoding="utf-8") as file:
            high_activity = json.load(file)
    finally:
        PylpgWorkspace.release(occupancy.claimed_pylpg_calculation_indices, occupancy.pylpg_work_root())

    assert high_activity["StartTime"] == "2021-01-01T00:00:00"
    assert len(high_activity["Values"]) == 3 * 1440
