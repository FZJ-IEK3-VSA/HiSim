"""Tests for the days the LoadProfileGenerator is asked to simulate for a run, and what that request gives.

The generator always simulates the run's whole calendar year, and the run's window is cut from that year (owner
decision of 2026-10-19): the household it simulates depends on how many days it simulates, so a week asked for on
its own differs from the same week of the year. The days are named in ISO 8601. The occupancy used to name them
day-first (``01-01-2021`` to ``08-01-2021`` for a week), which the generator reads month-first: a week was simulated
from 1 January to 1 August.
"""

import datetime
import pathlib

import pytest
from utspclient.helpers.lpgdata import Households

from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    UtspLpgConnector,
    UtspLpgConnectorConfig,
)
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
    ("start", "days", "requested"),
    [
        (datetime.datetime(2021, 1, 1), 1, ("2021-01-01", "2022-01-01")),
        (datetime.datetime(2021, 1, 1), 2, ("2021-01-01", "2022-01-01")),
        (datetime.datetime(2021, 1, 1), 7, ("2021-01-01", "2022-01-01")),
        (datetime.datetime(2021, 1, 1), 365, ("2021-01-01", "2022-01-01")),
        (datetime.datetime(2021, 7, 1), 7, ("2021-01-01", "2022-01-01")),
        (datetime.datetime(2021, 12, 25), 7, ("2021-01-01", "2022-01-01")),
    ],
)
def test_a_request_names_the_whole_year_in_iso_8601(
    start: datetime.datetime, days: int, requested: tuple, tmp_path: pathlib.Path
) -> None:
    """Catches a request of the window's own days, and a day-first date (8 January read as 1 August).

    A day, a week, a July week, the last week and the whole of 2021 all ask for 1 January 2021 to 1 January 2022,
    the end day the local full-year request always had (the UTSP one ended on 31 December).
    """
    parameters = parameters_of(start, days, tmp_path)

    assert UtspLpgConnector.lpg_request_days(parameters) == requested


@pytest.mark.base
def test_every_window_of_one_year_names_the_same_request_in_the_cache_key(tmp_path: pathlib.Path) -> None:
    """Catches a cache key that names a window's own request, so equal profiles would sit under other requests.

    A week, a July week and the whole of 2021 name the same request, in both modes the LoadProfileGenerator
    computes; 2022 names another, and a shipped profile names none.
    """
    week = parameters_of(datetime.datetime(2021, 1, 1), 7, tmp_path)
    july_week = parameters_of(datetime.datetime(2021, 7, 1), 7, tmp_path)
    year = parameters_of(datetime.datetime(2021, 1, 1), 365, tmp_path)
    next_year = parameters_of(datetime.datetime(2022, 1, 1), 7, tmp_path)

    for mode in (LpgDataAcquisitionMode.USE_LOCAL_LPG, LpgDataAcquisitionMode.USE_UTSP):
        material = UtspLpgConnector.request_key_material(mode, week)
        assert material == ":lpg_request=2021-01-01..2022-01-01"
        assert UtspLpgConnector.request_key_material(mode, july_week) == material
        assert UtspLpgConnector.request_key_material(mode, year) == material
        assert UtspLpgConnector.request_key_material(mode, next_year) == ":lpg_request=2022-01-01..2023-01-01"
    assert UtspLpgConnector.request_key_material(LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE, week) == ""


class LocalOccupancy:
    """Build occupancies of CHR01 whose profiles the local LoadProfileGenerator computes."""

    #: The profiles every occupancy publishes, one value per minute of the run at 60 s.
    PROFILES = (
        "electricity_consumption",
        "heating_by_devices",
        "water_consumption",
        "heating_by_residents",
        "number_of_residents",
    )

    @classmethod
    def build(cls, start: datetime.datetime, days: int, directory: pathlib.Path) -> UtspLpgConnector:
        """Return an occupancy of CHR01 for a run of ``days`` days from ``start``, computed by the local LPG.

        Each call runs the local LoadProfileGenerator for the whole of the run's year, about two minutes.

        Args:
            start: the run's start.
            days: the run's length in days.
            directory: where the run writes and caches.

        Returns:
            UtspLpgConnector: the occupancy, its profiles read.
        """
        config = UtspLpgConnectorConfig.for_household(
            "Occupancy",
            Households.CHR01_Couple_both_at_Work,
            data_acquisition_mode=LpgDataAcquisitionMode.USE_LOCAL_LPG,
        )
        occupancy: UtspLpgConnector = UtspLpgConnector(
            my_simulation_parameters=parameters_of(start, days, directory), config=config
        )
        return occupancy


@pytest.fixture(name="occupancy_of_2021", scope="module")
def fixture_occupancy_of_2021(tmp_path_factory: pytest.TempPathFactory) -> UtspLpgConnector:
    """Return the full-year 2021 occupancy of CHR01 from the local LPG, computed once for the module's tests.

    Args:
        tmp_path_factory: pytest's factory of temporary directories.

    Returns:
        UtspLpgConnector: the full-year occupancy.
    """
    return LocalOccupancy.build(datetime.datetime(2021, 1, 1), 365, tmp_path_factory.mktemp("year"))


@pytest.mark.utsp
def test_a_week_reads_the_first_week_of_the_year_the_local_generator_simulates(
    occupancy_of_2021: UtspLpgConnector, tmp_path: pathlib.Path
) -> None:
    """Catches a short run that gets another household than the same days of a full-year run.

    A one-week run and a full-year run of CHR01 in 2021 each run the local LoadProfileGenerator for the whole
    of 2021 (two runs of about two minutes each), and the week's five profiles equal the first 10 080 minutes of
    the year's. A request of the week alone gave another household: up to 12 kW apart in a minute. The full-year
    run's 525 600 minutes also show that the request reaches the end of the year.
    """
    week = LocalOccupancy.build(datetime.datetime(2021, 1, 1), 7, tmp_path)
    minutes_of_the_week = 7 * 1440

    assert len(occupancy_of_2021.electricity_consumption) == 365 * 1440
    for profile in LocalOccupancy.PROFILES:
        week_profile = getattr(week, profile)
        assert len(week_profile) == minutes_of_the_week, profile
        assert week_profile == getattr(occupancy_of_2021, profile)[:minutes_of_the_week], profile


@pytest.mark.utsp
def test_a_july_week_reads_the_same_days_of_the_year_the_local_generator_simulates(
    occupancy_of_2021: UtspLpgConnector, tmp_path: pathlib.Path
) -> None:
    """Catches a mid-year run that reads other minutes of the year than the full-year run calls its days.

    A week from 1 July 2021 asks the local LoadProfileGenerator for the whole of 2021 and cuts its window from
    that year, read from the profile's stated start, 1 January. Its five profiles equal the full-year run's
    minutes from 1 July 00:00 in standard time, minute 260 640 of the year (181 days of 1440 minutes): the full-
    year run labels its minutes in standard time, so both cut the same slice of one profile.
    """
    july_week = LocalOccupancy.build(datetime.datetime(2021, 7, 1), 7, tmp_path)
    first_minute = 181 * 1440
    minutes_of_the_week = 7 * 1440

    for profile in LocalOccupancy.PROFILES:
        july_profile = getattr(july_week, profile)
        assert len(july_profile) == minutes_of_the_week, profile
        year_profile = getattr(occupancy_of_2021, profile)
        assert july_profile == year_profile[first_minute: first_minute + minutes_of_the_week], profile
