"""Tests for the identity facts (``roadmap/pylpg_flakiness.md`` F7).

PV, building and car results are computed from an upstream component -- the weather or the occupancy --
but their cache keys used to hash only their own configuration, so two runs with different weathers
could share one entry. Now the upstream config contributes a readable identity string as a sizing fact,
the downstream config declares a field sized from it, and the key (a hash of the whole config) includes
it. These tests pin the identity strings, the key sensitivity, the engine binding on the file path, and
the refusal to build a component whose identity was never set.
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
    """Computes cache key material the way the components do."""

    PARAMETERS = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)

    @classmethod
    def of(cls, config: Any) -> str:
        """Return the key material of a config under the shared simulation parameters.

        Args:
            config: a configuration with all sized fields resolved.

        Returns:
            str: the key material.
        """
        return utils.build_cache_key_string(config, cls.PARAMETERS)

    @staticmethod
    def sized_from(config: Any, **facts: Any) -> Any:
        """Resolve a config against the given facts only.

        Args:
            config: the configuration.
            **facts: the sizing facts to provide.

        Returns:
            The resolved copy.
        """
        return config.resolve(SizingContext().with_facts(**facts))


@pytest.mark.base
def test_the_weather_identity_names_the_station_the_data_set_and_the_file_and_not_the_machine() -> None:
    """The weather identity contains station, data set and file stem, and no directory.

    Two configs reading the same station from different checkouts must give the same identity, otherwise
    a cache could never be shared between machines.
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
    """The occupancy identity changes with the seed, not only with the household name."""
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    with_other_routes = dataclasses.replace(baseline, guid="another-seed")

    households = baseline.household if isinstance(baseline.household, list) else [baseline.household]
    assert str(households[0].Name) in baseline.identity()
    assert str(baseline.energy_intensity.value) in baseline.identity()
    assert baseline.identity() != with_other_routes.identity()


@pytest.mark.base
def test_a_pv_key_moves_when_the_weather_does_and_a_building_key_too() -> None:
    """Changing the weather changes the PV key and the building key.

    Before F7 the PV key was identical for Aachen and Seville.
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
    """Changing the occupancy changes the car key."""
    baseline = UtspLpgConnectorConfig.get_default_utsp_connector_config()
    other = dataclasses.replace(baseline, guid="another-seed")
    car = CarConfig.for_household(name="Car", household_name="CHR01", car_name="Small_Car")

    assert Keys.of(Keys.sized_from(car, occupancy_identity=baseline.identity())) != Keys.of(
        Keys.sized_from(car, occupancy_identity=other.identity())
    )


@pytest.mark.base
def test_the_engine_binds_the_weather_identity_with_nothing_declared_in_the_file() -> None:
    """On the file path the fact binds without a ``sizing_sources`` line: one weather, one provider.

    Uses the repository's own example file, so this exercises the real loader and engine.
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
    """A building whose ``weather_identity`` is still ``AUTO`` is refused at construction.

    This is what makes the fix hold: no run can write a cache entry under an incomplete key.
    """
    from hisim.components.building.building import Building  # pylint: disable=import-outside-toplevel

    config = BuildingConfig.preset_standard("Building")
    assert config.weather_identity is AUTO

    with pytest.raises(ConfigSizingError, match="weather_identity"):
        Building(config=config, my_simulation_parameters=Keys.PARAMETERS)
