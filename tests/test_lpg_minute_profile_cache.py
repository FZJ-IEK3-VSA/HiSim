"""Tests for the occupancy's cache of the LoadProfileGenerator's minute profile (hisim-caching-hi1.10).

The occupancy caches what the LoadProfileGenerator computed for a household -- its whole requested year, one value per
wall-clock minute -- and cuts every run's window and timestep from it when it reads it. The generator therefore runs
once per household configuration and year, not once per window and resolution: each run used to cache its own
windowed and averaged series under a key naming the run's start, end and timestep, and every new window ran the
generator for the whole year again. These tests pin that the generator runs once, that a profile served from the cache
gives the same series as one just computed, what the key names and what it does not, and that the entry's metadata
names the configuration that produced it.

The local generator is replaced by :class:`FakeGenerator`, which serves the shipped CHR01 profile as its result files.
"""

import dataclasses
import datetime
import json
import math
import os
import pathlib
from typing import List, Optional

import pytest
from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim import utils
from hisim.caching import CacheEntryMetadata
from hisim.components.loadprofilegenerator_utsp_connector import (
    LpgDataAcquisitionMode,
    LpgMinuteProfile,
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


class FakeGenerator:
    """Stands in for the local LoadProfileGenerator: serves the shipped CHR01 files as its results and counts its runs.

    The shipped files carry the names the generator writes (``Results/SumProfiles.HH1.Electricity.csv``,
    ``Results/BodilyActivityLevel.High.HH1.json``, ...), so the occupancy reads them exactly as a local calculation's
    results. They hold no flexibility and no car files, as a household without a car.
    """

    #: The shipped profile's files.
    SHIPPED = utils.HISIMPATH["occupancy"]["CHR01 Couple both at Work"]

    def __init__(self, directory: pathlib.Path) -> None:
        """Lay out a result folder of links to the shipped files below ``directory``.

        Args:
            directory: a scratch directory of the test.
        """
        self.runs = 0
        self.result_folder = directory / "generator"
        results = self.result_folder / "Results"
        results.mkdir(parents=True)
        for shipped_file in [
            *self.SHIPPED["number_of_residents"],
            self.SHIPPED["electricity_consumption"],
            self.SHIPPED["water_consumption"],
            self.SHIPPED["heating_by_devices"],
        ]:
            (results / os.path.basename(shipped_file)).symlink_to(shipped_file)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make every occupancy run this fake instead of the local LoadProfileGenerator.

        Args:
            monkeypatch: pytest's monkeypatch fixture.
        """

        def execute(
            connector: UtspLpgConnector, calculation_index: int, household: JsonReference, random_seed: Optional[int] = None
        ) -> str:
            """Count one run of the generator and return the result folder it would have written."""
            del connector, calculation_index, household, random_seed
            self.runs += 1
            return str(self.result_folder)

        monkeypatch.setattr(UtspLpgConnector, "execute_local_lpg_single_household", execute)


class Runs:
    """Build the parameters and the occupancies of the runs the tests compare."""

    #: The first minute of the shipped profile's year.
    NEW_YEAR = datetime.datetime(2021, 1, 1)

    @classmethod
    def parameters(
        cls,
        directory: pathlib.Path,
        days: int,
        seconds_per_timestep: int,
        start: datetime.datetime = NEW_YEAR,
        part_load_above_seconds: Optional[float] = None,
    ) -> SimulationParameters:
        """Return the parameters of a run of ``days`` days from ``start``, caching in ``directory / "cache"``.

        Args:
            directory: where the run writes and caches.
            days: the run's length in days.
            seconds_per_timestep: the run's timestep, in s.
            start: the run's start.
            part_load_above_seconds: the run's part-load threshold, in s, or ``None`` for the default.

        Returns:
            SimulationParameters: the parameters.
        """
        return SimulationParameters(
            start_date=start,
            end_date=start + datetime.timedelta(days=days),
            seconds_per_timestep=seconds_per_timestep,
            result_directory=str(directory / "results"),
            post_processing_options=[],
            cache_dir_path=str(directory / "cache"),
            part_load_above_seconds=part_load_above_seconds,
        )

    @classmethod
    def config(
        cls,
        mode: LpgDataAcquisitionMode = LpgDataAcquisitionMode.USE_LOCAL_LPG,
        household: Optional[object] = None,
    ) -> UtspLpgConnectorConfig:
        """Return an occupancy of CHR01, or of ``household``, in ``mode``.

        Args:
            mode: where the profile comes from.
            household: a household reference or a list of them; CHR01 when omitted.

        Returns:
            UtspLpgConnectorConfig: the configuration.
        """
        return UtspLpgConnectorConfig.for_household(
            "Occupancy",
            Households.CHR01_Couple_both_at_Work if household is None else household,  # type: ignore[arg-type]  # a reference or a list
            data_acquisition_mode=mode,
        )

    @classmethod
    def occupancy(cls, parameters: SimulationParameters, config: Optional[UtspLpgConnectorConfig] = None) -> UtspLpgConnector:
        """Build an occupancy, which reads its series while it is built.

        Args:
            parameters: the run's parameters.
            config: the occupancy's configuration; CHR01 from the local generator when omitted.

        Returns:
            UtspLpgConnector: the occupancy.
        """
        occupancy: UtspLpgConnector = UtspLpgConnector(
            my_simulation_parameters=parameters, config=cls.config() if config is None else config
        )
        return occupancy

    @staticmethod
    def series(occupancy: UtspLpgConnector) -> List[list]:
        """Return the five series an occupancy publishes.

        Args:
            occupancy: the occupancy.

        Returns:
            List[list]: electricity, device heat, warm water, body heat and residents, one value per timestep.
        """
        return [
            occupancy.electricity_consumption,
            occupancy.heating_by_devices,
            occupancy.water_consumption,
            occupancy.heating_by_residents,
            occupancy.number_of_residents,
        ]


def assert_bit_identical(actual: List[list], expected: List[list]) -> None:
    """Assert two sets of series are equal value by value, in their types and in the sign of their zeros.

    Args:
        actual: the series under test.
        expected: the reference series.
    """
    assert len(actual) == len(expected)
    for actual_series, expected_series in zip(actual, expected):
        assert actual_series == expected_series
        assert [type(value) for value in actual_series] == [type(value) for value in expected_series]
        assert [math.copysign(1.0, value) for value in actual_series] == [
            math.copysign(1.0, value) for value in expected_series
        ]


@pytest.mark.base
def test_one_generator_run_serves_a_week_a_year_and_a_900_s_year(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches the generator running again for another window or timestep of a year it already computed.

    A 60 s week, a 60 s year and a 900 s year of CHR01 start the generator once: the first computes and caches the
    year, the other two read it. The year at 900 s read from the cache equals the shipped profile read directly,
    which is never cached, so the cached minutes are the files' minutes.
    """
    generator = FakeGenerator(tmp_path)
    generator.install(monkeypatch)

    Runs.occupancy(Runs.parameters(tmp_path, days=7, seconds_per_timestep=60))
    assert generator.runs == 1
    year_at_60_s = Runs.occupancy(Runs.parameters(tmp_path, days=365, seconds_per_timestep=60))
    year_at_900_s = Runs.occupancy(Runs.parameters(tmp_path, days=365, seconds_per_timestep=900))
    assert generator.runs == 1

    assert len(year_at_60_s.electricity_consumption) == 365 * 1440
    assert len(year_at_900_s.electricity_consumption) == 365 * 96
    shipped_year_at_900_s = Runs.occupancy(
        Runs.parameters(tmp_path / "shipped", days=365, seconds_per_timestep=900),
        Runs.config(LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE),
    )
    assert_bit_identical(Runs.series(year_at_900_s), Runs.series(shipped_year_at_900_s))


@pytest.mark.base
def test_a_profile_from_the_cache_gives_the_series_of_the_cold_run(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a cache that changes what it stored: a warm run of a window that differs from the cold one.

    A July week at 900 s is computed cold, then built again from the cache; the five series, the car report and the
    flexibility events are the same, value by value and type by type.
    """
    generator = FakeGenerator(tmp_path)
    generator.install(monkeypatch)
    parameters = Runs.parameters(tmp_path, days=7, seconds_per_timestep=900, start=datetime.datetime(2021, 7, 1))

    cold = Runs.occupancy(parameters)
    warm = Runs.occupancy(parameters)

    assert generator.runs == 1
    assert_bit_identical(Runs.series(warm), Runs.series(cold))
    assert warm.car_data_dict == cold.car_data_dict == {"car_states": [{}], "car_locations": [{}], "driving_distances": [{}]}
    assert warm.flexibility_data_dict == cold.flexibility_data_dict == {"flexibility": [{}]}


@pytest.mark.base
def test_the_key_names_nothing_of_the_runs_window_timestep_or_threshold(tmp_path: pathlib.Path) -> None:
    """Catches a key that names the run's window, timestep or part-load threshold, so each would run the generator."""
    config = Runs.config()
    week = Runs.parameters(tmp_path, days=7, seconds_per_timestep=60)
    others = [
        Runs.parameters(tmp_path, days=365, seconds_per_timestep=60),
        Runs.parameters(tmp_path, days=365, seconds_per_timestep=900),
        Runs.parameters(tmp_path, days=7, seconds_per_timestep=900, start=datetime.datetime(2021, 7, 1)),
        Runs.parameters(tmp_path, days=7, seconds_per_timestep=900, part_load_above_seconds=1200),
    ]

    material = UtspLpgConnector.minute_profile_key_material(config, week)

    assert material.startswith(config.cache_key_view().to_json())
    assert material.endswith(":lpg_minute_profiles:lpg_request=2021-01-01..2022-01-01")
    for parameters in others:
        assert UtspLpgConnector.minute_profile_key_material(config, parameters) == material


@pytest.mark.base
def test_the_key_names_the_household_the_mode_the_guid_and_the_year(tmp_path: pathlib.Path) -> None:
    """Catches one household's or one year's profile served for another."""
    week = Runs.parameters(tmp_path, days=7, seconds_per_timestep=60)
    next_year = Runs.parameters(tmp_path, days=7, seconds_per_timestep=60, start=datetime.datetime(2022, 1, 1))
    config = Runs.config()
    material = UtspLpgConnector.minute_profile_key_material(config, week)

    assert UtspLpgConnector.minute_profile_key_material(config, next_year) != material
    for other_config in (
        Runs.config(household=Households.CHR02_Couple_30_64_age_with_work),
        Runs.config(LpgDataAcquisitionMode.USE_UTSP),
        dataclasses.replace(config, guid="2"),
        dataclasses.replace(config, profile_with_washing_machine_and_dishwasher=False),
    ):
        assert UtspLpgConnector.minute_profile_key_material(other_config, week) != material


@pytest.mark.base
def test_each_entry_records_the_configuration_that_produced_it(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches an entry whose metadata names the configuration asked for instead of the one computed (Fault D, F5).

    Two households given as a list are requested and cached one by one, each under a guid of its own. Each entry's
    ``.meta`` file holds the key material of the household's own configuration and hashes to the entry's name. An
    entry whose metadata no longer agrees is discarded and computed again.
    """
    generator = FakeGenerator(tmp_path)
    generator.install(monkeypatch)
    parameters = Runs.parameters(tmp_path, days=2, seconds_per_timestep=60)
    config = Runs.config(household=[Households.CHR01_Couple_both_at_Work, Households.CHR01_Couple_both_at_Work])

    pair = Runs.occupancy(parameters, config)
    entries, household_configs = pair.minute_profile_cache_entries_of_households(cache_dir_path=None)

    assert generator.runs == 2
    assert [household_config.guid for household_config in household_configs] == ["2", "1"]
    for entry, household_config in zip(entries, household_configs):
        assert entry.exists
        assert CacheEntryMetadata.describes(entry.path)
        recorded = pathlib.Path(CacheEntryMetadata.metadata_filepath(entry.path)).read_text(encoding="utf-8")
        assert recorded == UtspLpgConnector.minute_profile_key_material(household_config, parameters)

    pathlib.Path(CacheEntryMetadata.metadata_filepath(entries[0].path)).write_text("another key", encoding="utf-8")
    Runs.occupancy(parameters, config)
    assert generator.runs == 3


@pytest.mark.base
def test_a_profile_written_to_the_cache_reads_back_bit_for_bit(tmp_path: pathlib.Path) -> None:
    """Catches a cache format that changes a value it stores: a last bit, a sign of zero, an int, a nested car value."""
    profile = LpgMinuteProfile(
        start_time="2021-01-01T00:00:00",
        persons_at_high_activity=[2.0, 1.0, 0.0],
        persons_at_low_activity=[0.0, 1.0, 2.0],
        electricity_per_minute_in_kwh=[0.1 + 0.2, 1e-17, 0.0014992736178730055],
        warm_water_per_minute_in_liter=[0, 3, -0.0],
        inner_device_heat_gains_per_minute_in_kwh=[1 / 3, 2 / 3, 5e-324],
        flexibility={},
        car_states={"Values": ["Parked", "Driving"], "StartTime": "2021-01-01T00:00:00"},
        car_locations={"Values": [1.5, 2.0], "HouseKey": {"HouseholdName": "CHR01"}},
        driving_distances={"Values": [0.0, 12.25]},
    )
    entry = utils.get_cache_entry("Occupancy", "a key", Runs.parameters(tmp_path, 1, 60), str(tmp_path / "cache"))

    UtspLpgConnector.write_minute_profiles(entry, [profile, profile])
    read_back = UtspLpgConnector.read_cached_minute_profiles(entry.path)

    assert read_back == [profile, profile]
    assert [type(value) for value in read_back[0].warm_water_per_minute_in_liter] == [int, int, float]
    assert math.copysign(1.0, read_back[0].warm_water_per_minute_in_liter[2]) == -1.0
    assert CacheEntryMetadata.describes(entry.path)


@pytest.mark.base
def test_an_entry_of_another_layout_is_refused(tmp_path: pathlib.Path) -> None:
    """Catches an entry with other fields being read as a profile with some series missing."""
    entry_path = tmp_path / "entry.cache"
    entry_path.write_text(json.dumps([{"start_time": "2021-01-01T00:00:00", "data": "a,b"}]), encoding="utf-8")

    with pytest.raises(TypeError):
        UtspLpgConnector.read_cached_minute_profiles(str(entry_path))


@pytest.mark.base
def test_the_minute_values_are_residents_body_heat_and_powers_in_watt() -> None:
    """Catches a wrong body heat per person, a lost factor of 1000 * 60, or series of different lengths.

    Two persons at a high and one at a low activity level give 3 residents and 2 * 150 + 100 = 400 W; 0.001 kWh in
    a minute is 60 W. The rows end with the shortest series, here the warm water's two minutes.
    """
    profile = LpgMinuteProfile(
        start_time="2021-01-01T00:00:00",
        persons_at_high_activity=[2.0, 0.0, 1.0],
        persons_at_low_activity=[1.0, 2.0, 0.0],
        electricity_per_minute_in_kwh=[0.001, 0.0, 0.002],
        warm_water_per_minute_in_liter=[0.5, 0.0],
        inner_device_heat_gains_per_minute_in_kwh=[0.0005, 0.0, 0.0],
        flexibility={},
        car_states={},
        car_locations={},
        driving_distances={},
    )

    minute_values = UtspLpgConnector.minute_values_of(profile)

    assert minute_values["number_of_residents"].tolist() == [3.0, 2.0]
    assert minute_values["heating_by_residents"].tolist() == [400.0, 200.0]
    assert minute_values["electricity_consumption"].tolist() == pytest.approx([60.0, 0.0])
    assert minute_values["water_consumption"].tolist() == [0.5, 0.0]
    assert minute_values["inner_device_heat_gains"].tolist() == pytest.approx([30.0, 0.0])


@pytest.mark.base
def test_a_households_files_have_one_start() -> None:
    """Catches activity files of different starts read as one household, one of them shifted against the other."""
    occupancy = UtspLpgConnector.__new__(UtspLpgConnector)
    occupancy.component_name = "Occupancy"

    assert occupancy.common_start_time([{"StartTime": "2021-01-01T00:00:00"}] * 2) == "2021-01-01T00:00:00"
    with pytest.raises(ValueError, match="state different starts"):
        occupancy.common_start_time([{"StartTime": "2021-01-01T00:00:00"}, {"StartTime": "2021-01-02T00:00:00"}])


@pytest.mark.base
def test_the_request_asks_for_the_generators_one_minute_files_at_any_timestep() -> None:
    """Catches a request that follows the run's timestep and has the generator write files nobody reads.

    The occupancy reads only the one-minute files and averages them itself, so the request of a 900 s run is the
    one of a 60 s run: an external resolution of one minute.
    """
    occupancy = UtspLpgConnector.__new__(UtspLpgConnector)
    occupancy.utsp_config = Runs.config(LpgDataAcquisitionMode.USE_UTSP)

    request = occupancy.prepare_lpg_simulation_config_for_utsp_request("2021-01-01", "2022-01-01", Households.CHR01_Couple_both_at_Work)

    assert request.CalcSpec is not None
    assert request.CalcSpec.ExternalTimeResolution == "0:01:00" == UtspLpgConnector.LPG_EXTERNAL_TIME_RESOLUTION
