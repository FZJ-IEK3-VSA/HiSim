"""Tests for the identity facts: how a cached result's key comes to describe the upstream it depends on.

``roadmap/pylpg_flakiness.md`` F7 is the finding that a PV system's, a building's and a car's cache
keys described their *owner* and not their *inputs*: the PV key hashed the PV config, which knows
nothing about the weather it was run with, so two runs against two weathers shared one entry. The fix
is not to widen the key by hand but to make the configuration complete -- the upstream config
contributes its readable identity as a sizing fact, the downstream config declares a field sized from
it, and the key, which hashes the whole config, is complete by construction.

These tests pin the four things that make that work: the identity strings are readable and say what
decides the data; the sizing engine binds them with nothing declared in the file; every key moves when
its upstream changes; and a downstream component cannot be built without the fact, because the whole
point is that no run can ever produce an entry under an incomplete key again.

Each test states the failure mode it catches.
"""

# clean

import dataclasses
import pathlib
from typing import Any

import pytest

from hisim import utils
from hisim.components.building.config import BuildingConfig
from hisim.components.generic_car import CarConfig
from hisim.components.generic_pv_system import PVSystemConfig
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnectorConfig
from hisim.components.weather import LocationEnum, WeatherConfig
from hisim.config import AUTO, ConfigSizingError, SizingContext
from hisim.energy_system.configure import configure_energy_system
from hisim.energy_system.loader import load_energy_system
from hisim.simulationparameters import SimulationParameters

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class Keys:
    """Computes cache keys the way the components do, so the tests assert on the real material."""

    PARAMETERS = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)

    @classmethod
    def of(cls, config: Any) -> str:
        """The legacy key material of a config: its JSON plus the simulation key, as every writer hashes it.

        Args:
            config: A configuration with every sized field resolved.

        Returns:
            str: the key material.
        """
        return utils.build_cache_key_string(config, cls.PARAMETERS)

    @staticmethod
    def sized_from(config: Any, **facts: Any) -> Any:
        """Resolves a config against just the given facts.

        Args:
            config: The configuration to resolve.
            **facts: The facts the surrounding system would provide.

        Returns:
            The resolved copy.
        """
        return config.resolve(SizingContext().with_facts(**facts))


@pytest.mark.base
def test_the_weather_identity_names_the_station_the_data_set_and_the_file_and_not_the_machine() -> None:
    """The identity is readable and decided by the data, not by where the repository is checked out.

    Two weather configs that read the same station from two different directories are the same
    weather, and they must produce the same downstream keys or a cache could never be shared between
    two machines.

    Catches: an absolute path leaking into the identity, or the identity dropping one of the three
    things that decide what the weather component reads.
    """
    aachen = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN)
    elsewhere = dataclasses.replace(aachen, source_path=str(pathlib.Path("/another/checkout") / pathlib.Path(aachen.source_path).name))

    identity = aachen.identity()

    assert identity.startswith("Aachen/")
    assert aachen.data_source.value in identity
    assert pathlib.Path(aachen.source_path).name in identity
    assert "/another/checkout" not in elsewhere.identity()
    assert elsewhere.identity() == identity


@pytest.mark.base
def test_the_occupancy_identity_says_more_than_the_household_name() -> None:
    """The car's key used to carry the household name alone; the identity carries what decides the profile.

    Catches: two occupancies with the same household but different travel routes producing the same
    identity, which is the collision the car's key used to have.
    """
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    with_other_routes = dataclasses.replace(baseline, guid="another-seed")

    households = baseline.household if isinstance(baseline.household, list) else [baseline.household]
    assert str(households[0].Name) in baseline.identity()
    assert str(baseline.energy_intensity.value) in baseline.identity()
    assert baseline.identity() != with_other_routes.identity()


@pytest.mark.base
def test_a_pv_key_moves_when_the_weather_does_and_a_building_key_too() -> None:
    """L1 of the cache-testing spec: perturb the upstream, the downstream key changes.

    This is the whole of F7 for these two components. Before, the PV key was identical for Aachen and
    Seville; now the weather's identity is part of the PV configuration and therefore of the key.

    Catches: the sized field falling out of the key -- a ``to_json`` that skips it, say.
    """
    aachen = WeatherConfig.get_default(location_entry=LocationEnum.AACHEN).identity()
    seville = WeatherConfig.get_default(location_entry=LocationEnum.SEVILLE).identity()

    pv = PVSystemConfig.get_default_pv_system()
    building = BuildingConfig.preset_standard("Building")

    assert Keys.of(Keys.sized_from(pv, weather_identity=aachen)) != Keys.of(Keys.sized_from(pv, weather_identity=seville))
    assert Keys.of(Keys.sized_from(building, weather_identity=aachen)) != Keys.of(
        Keys.sized_from(building, weather_identity=seville)
    )


@pytest.mark.base
def test_a_car_key_moves_when_the_occupancy_does() -> None:
    """The same for the car and its occupancy: the household name is no longer all the key knows.

    Catches: two cars of the same household name under different occupancies sharing a cache entry.
    """
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    other = dataclasses.replace(baseline, guid="another-seed")
    car = CarConfig.for_household(name="Car", household_name="CHR01", car_name="Small_Car")

    assert Keys.of(Keys.sized_from(car, occupancy_identity=baseline.identity())) != Keys.of(
        Keys.sized_from(car, occupancy_identity=other.identity())
    )


@pytest.mark.base
def test_the_engine_binds_the_weather_identity_with_nothing_declared_in_the_file() -> None:
    """On the file path the fact needs no ``sizing_sources`` line: one weather, one provider, it binds.

    Uses the repository's own hand-written example, so this is the real loader, the real engine and a
    real file rather than a fixture shaped to pass.

    Catches: the contribution not being registered, or the field not being sized on the declarative
    path -- either of which would leave every recorded energy system with an incomplete key.
    """
    repository_root = pathlib.Path(__file__).resolve().parents[1]
    model = load_energy_system(repository_root / "energy_systems" / "gas_boiler_household.energy_system.yaml")

    configured = configure_energy_system(model)

    weather_names = [name for name in model.components if isinstance(configured.config_of(name), WeatherConfig)]
    assert len(weather_names) == 1
    expected = configured.config_of(weather_names[0]).identity()
    building_names = [name for name in model.components if isinstance(configured.config_of(name), BuildingConfig)]
    assert building_names, "the example has a building"
    for name in building_names:
        assert configured.config_of(name).weather_identity == expected
        assert configured.config_of(name).weather_identity is not AUTO


@pytest.mark.base
def test_a_building_cannot_be_built_without_knowing_its_weather() -> None:
    """The construction-time check is what makes the fix hold: no run can produce an entry under an incomplete key.

    Catches: the sized field being given a concrete default that lets an unsized building through,
    which would quietly restore the old incomplete key for every setup that forgot the assignment.
    """
    from hisim.components.building.building import Building  # pylint: disable=import-outside-toplevel

    config = BuildingConfig.preset_standard("Building")
    assert config.weather_identity is AUTO

    with pytest.raises(ConfigSizingError, match="weather_identity"):
        Building(config=config, my_simulation_parameters=Keys.PARAMETERS)
