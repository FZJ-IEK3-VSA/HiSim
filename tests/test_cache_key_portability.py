"""Tests for ``ConfigBase.cache_key_view``: what a configuration hashes into its cache key and what it leaves out.

Two caches used to hash machine-specific paths -- the LoadProfileGenerator connector its
``result_dir_path``, the weather its absolute ``source_path`` -- so their entries could never be shared
between machines. These tests pin the two overrides and the base rule: fields that decide the result
move the key, fields that only place the run do not, and a key built from the repository's own data
files contains no absolute path.
"""

# clean

import dataclasses
import os
import pathlib
import re
from typing import Any

import pytest
from utspclient.helpers.lpgdata import EnergyIntensityType

from hisim import utils
from hisim.components.generic_pv_system import PVSystemConfig
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnectorConfig
from hisim.components.weather import LocationEnum, WeatherConfig
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class Keys:
    """Derives key material the way the components do, and looks inside it."""

    PARAMETERS = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)

    #: A JSON string value that starts at a filesystem root, POSIX (``/``) or Windows (``C:\\``): the shape
    #: of a machine-specific path.
    ABSOLUTE_PATH_VALUE = re.compile(r'"[a-z_]+": "(?:/|[A-Za-z]:\\\\)[^"]*"')

    @classmethod
    def of(cls, config: Any) -> str:
        """The key material of a configuration under the shared simulation parameters.

        Args:
            config: The configuration.

        Returns:
            str: what is hashed into the entry's name and recorded beside it.
        """
        return utils.build_cache_key_string(config, cls.PARAMETERS)

    @classmethod
    def absolute_paths_in(cls, material: str) -> list:
        """Every absolute path spelled into key material.

        Args:
            material: The key material.

        Returns:
            list: the offending ``"field": "/..."`` fragments, empty for a portable key.
        """
        return cls.ABSOLUTE_PATH_VALUE.findall(material)


@pytest.mark.base
def test_the_base_view_clears_the_building_and_nothing_else() -> None:
    """The rule every config had before this change is the rule every config keeps.

    Catches: the hook regressing the one normalisation the key always had, which would file the same
    PV series under a different name per house again.
    """
    in_house_a = PVSystemConfig.get_default_pv_system()
    in_house_a.component_id = dataclasses.replace(in_house_a.component_id, building="BUI1")
    in_house_b = PVSystemConfig.get_default_pv_system()
    in_house_b.component_id = dataclasses.replace(in_house_b.component_id, building="BUI2")

    assert Keys.of(in_house_a) == Keys.of(in_house_b)
    assert in_house_a.component_id.building == "BUI1", "the view must be a copy; the live config keeps its building"
    assert Keys.of(in_house_a) != Keys.of(dataclasses.replace(in_house_a, power_in_watt=1.0))


@pytest.mark.base
def test_a_config_that_is_not_a_config_base_is_still_hashed() -> None:
    """Tests hand ``get_cache_file`` minimal stand-ins with only ``to_json``; they keep working.

    Catches: the key function assuming every parameter class has the hook.
    """

    @dataclasses.dataclass
    class StandIn:
        """The smallest thing the key function accepts."""

        name: str = "demo"

        def to_json(self) -> str:
            """The stand-in's JSON."""
            return '{"name": "demo"}'

    assert Keys.of(StandIn()).startswith('{"name": "demo"}')


@pytest.mark.base
def test_where_the_occupancy_result_is_written_does_not_change_its_key() -> None:
    """The three run-placement fields are not key material.

    Catches: the defect this change exists for -- an absolute ``result_dir_path`` in the key, so that
    no LoadProfileGenerator entry computed on one machine is ever found on another.
    """
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    elsewhere = dataclasses.replace(
        baseline,
        result_dir_path="/home/runner/work/HiSim/HiSim/hisim/results",
        cache_dir_path="/tmp/somewhere",
        calculation_index_for_local_lpg=4242,
    )

    assert Keys.of(baseline) == Keys.of(elsewhere)
    assert not Keys.absolute_paths_in(Keys.of(baseline)), Keys.absolute_paths_in(Keys.of(baseline))


@pytest.mark.base
def test_what_decides_the_occupancy_profile_still_moves_its_key() -> None:
    """Removing fields from the key must not remove the ones that matter.

    Catches: an over-eager view that clears a field the profile depends on, which would hand one
    household's profile to another.
    """
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()

    assert Keys.of(baseline) != Keys.of(dataclasses.replace(baseline, guid="another-seed"))
    assert Keys.of(baseline) != Keys.of(dataclasses.replace(baseline, profile_with_washing_machine_and_dishwasher=False))
    assert baseline.energy_intensity is not EnergyIntensityType.EnergyIntensive, "pick another member below"
    other_intensity = EnergyIntensityType.EnergyIntensive
    assert Keys.of(baseline) != Keys.of(dataclasses.replace(baseline, energy_intensity=other_intensity))


@pytest.mark.base
def test_the_same_weather_file_under_two_checkouts_has_one_key(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A catalogue file is spelled relative to the inputs directory, so the checkout's location drops out.

    Catches: the weather key naming the machine, which made the entry every other component depends on
    private to whoever computed it.
    """
    here = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN)
    relative = os.path.relpath(here.source_path, utils.get_input_directory())
    other_checkout = tmp_path / "other" / "hisim" / "inputs"
    there = dataclasses.replace(here, source_path=str(other_checkout / relative))

    key_here = Keys.of(here)
    monkeypatch.setattr(utils, "get_input_directory", lambda: str(other_checkout))
    key_there = Keys.of(there)

    assert key_here == key_there
    assert not Keys.absolute_paths_in(key_here), Keys.absolute_paths_in(key_here)
    assert relative.replace(os.sep, "/") in key_here, "the relative spelling must be what the key carries"


@pytest.mark.base
def test_a_different_weather_file_has_a_different_key() -> None:
    """Making the path portable must not make two files look alike.

    Catches: a view that drops the path altogether, so a custom weather file collides with the
    catalogue entry of the same station; and one that keeps only the file name, so two custom files
    that share a name in different directories collide with each other.
    """
    aachen = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN)
    seville = WeatherConfig.get_default(location_entry=LocationEnum.SEVILLE)
    custom = dataclasses.replace(aachen, source_path="/data/measured/my_station_2021")
    same_name_elsewhere = dataclasses.replace(aachen, source_path="/data/other_lab/my_station_2021")

    assert Keys.of(aachen) != Keys.of(seville)
    assert Keys.of(aachen) != Keys.of(custom)
    assert Keys.of(custom) != Keys.of(same_name_elsewhere)
    assert '"source_path": "/data/measured/my_station_2021"' in Keys.of(custom), (
        "a file outside the inputs directory keeps its absolute path"
    )


@pytest.mark.base
def test_the_base_rule_survives_an_override() -> None:
    """The building is cleared for a config with its own hook, not only for one without.

    Catches: a hook design that lets a subclass bypass the base rule, so the weather or occupancy key
    becomes house-specific again.
    """
    weather = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN)
    occupancy = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    for in_house_a in (weather, occupancy):
        in_house_b = dataclasses.replace(
            in_house_a, component_id=dataclasses.replace(in_house_a.component_id, building="BUI2")
        )
        in_house_a.component_id = dataclasses.replace(in_house_a.component_id, building="BUI1")

        assert Keys.of(in_house_a) == Keys.of(in_house_b), type(in_house_a).__name__
        assert '"building": null' in Keys.of(in_house_a), type(in_house_a).__name__


@pytest.mark.base
def test_the_view_never_touches_the_live_configuration() -> None:
    """The component reads its real paths after the key is built; the view must be a copy.

    Catches: an override that assigns into ``self``, which would send the weather reader to a
    relative path it cannot open.
    """
    weather = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN)
    occupancy = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    source_before, result_dir_before = weather.source_path, occupancy.result_dir_path

    Keys.of(weather)
    Keys.of(occupancy)

    assert weather.source_path == source_before
    assert occupancy.result_dir_path == result_dir_before
    assert isinstance(weather.component_id, ComponentID)
