"""Unit tests for the pure in-memory CRUD methods of :class:`SimRepository`.

``SimRepository`` is a small, fully deterministic key/value store used to exchange
data across components during a simulation. Every method only mutates or reads the
two internal dicts (``entries`` and ``dynamic_entries``); none of them touch the
filesystem, the network, or any global state. These tests therefore exercise the
CRUD contract directly and hermetically, with no simulation setup required.

The dynamic-entry dict is pre-seeded in ``__init__`` with an empty sub-dict for
every member of :class:`lt.ComponentType`, so any real enum member (here
``ComponentType.PV``) is a valid key without extra setup.

The last test in the file is the exception: a small Weather/occupancy/Building household, run
end to end, that shows the same repository doing its real job -- carrying the Weather's full-year
series from the component that computes them to the components that read them.
"""

# clean

from pathlib import Path
from typing import Any, Optional

import pytest
from pytest import MarkDecorator

import hisim.simulator as sim
from hisim import loadtypes as lt
from hisim import utils
from hisim.components import building
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.sim_repository import SimRepository
from hisim.simulator import SimulationParameters

pytestmark: MarkDecorator = pytest.mark.base

# A representative ComponentType member. ``__init__`` pre-populates an empty
# sub-dict for every ComponentType member, so PV needs no extra setup.
_CT: lt.ComponentType = lt.ComponentType.PV

# A module path the end-to-end test hands the Simulator so it can name its result directory;
# no such file is imported, only its directory and stem are used.
PATH: str = "../system_setups/household_for_test_sim_repository.py"


# --------------------------------------------------------------------------- #
# Plain entries: set_entry / get_entry / entry_exists / delete_entry
# --------------------------------------------------------------------------- #
def test_set_then_get_entry_roundtrips_value() -> None:
    """``set_entry`` stores a value that ``get_entry`` returns unchanged."""
    repo = SimRepository()
    repo.set_entry("foo", 42)
    assert repo.get_entry("foo") == 42


def test_entry_exists_reflects_set_and_delete() -> None:
    """``entry_exists`` is False on a fresh repo, True after set, False after delete."""
    repo = SimRepository()
    assert repo.entry_exists("foo") is False
    repo.set_entry("foo", 1)
    assert repo.entry_exists("foo") is True
    repo.delete_entry("foo")
    assert repo.entry_exists("foo") is False


def test_get_entry_missing_key_raises_keyerror() -> None:
    """``get_entry`` on an absent key raises ``KeyError`` (matches ``dict[key]``)."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.get_entry("missing")


def test_delete_entry_missing_key_raises_keyerror() -> None:
    """``delete_entry`` uses ``dict.pop`` without a default, so a missing key raises."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.delete_entry("missing")


def test_stored_none_is_distinct_from_absent() -> None:
    """Storing ``None`` is a present entry, distinguishable from a missing key."""
    repo = SimRepository()
    repo.set_entry("k", None)
    assert repo.entry_exists("k") is True
    assert repo.get_entry("k") is None


def test_set_entry_overwrites_existing_value() -> None:
    """A second ``set_entry`` for the same key replaces the previous value."""
    repo = SimRepository()
    repo.set_entry("k", 1)
    repo.set_entry("k", 2)
    assert repo.get_entry("k") == 2


# --------------------------------------------------------------------------- #
# Dynamic entries: set_dynamic_entry / get_dynamic_entry /
#                  get_dynamic_source_weights / delete_dynamic_entry
# --------------------------------------------------------------------------- #
def test_set_then_get_dynamic_entry_roundtrips_value() -> None:
    """``set_dynamic_entry`` stores a value that ``get_dynamic_entry`` returns."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 10, "v")
    assert repo.get_dynamic_entry(_CT, 10) == "v"


def test_get_dynamic_entry_unset_weight_returns_none() -> None:
    """An unset ``(component_type, weight)`` pair resolves to ``None``."""
    repo = SimRepository()
    assert repo.get_dynamic_entry(_CT, 999) is None


def test_get_dynamic_entry_unknown_component_type_returns_none() -> None:
    """A component type that is not a pre-seeded key resolves to ``None``.

    ``get_dynamic_entry`` uses ``dict.get(component_type, None)`` and therefore
    returns ``None`` (rather than raising) for an unknown component type.
    """
    repo = SimRepository()
    # ``get_dynamic_entry`` is typed to take a ComponentType; pass a value that is
    # not a pre-seeded key to exercise the ``.get(...) is None`` branch.
    not_a_component_type: Any = "not-a-component-type"
    assert repo.get_dynamic_entry(not_a_component_type, 1) is None


def test_get_dynamic_source_weights_empty_on_fresh_repo() -> None:
    """A freshly constructed repo has no weights for any component type."""
    repo = SimRepository()
    assert not repo.get_dynamic_source_weights(_CT)


def test_get_dynamic_source_weights_preserves_insertion_order() -> None:
    """Weights are returned in insertion order (Python dict ordering)."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 1, "a")
    repo.set_dynamic_entry(_CT, 2, "b")
    assert repo.get_dynamic_source_weights(_CT) == [1, 2]


def test_delete_dynamic_entry_removes_the_entry() -> None:
    """``delete_dynamic_entry`` removes the entry; it is no longer retrievable."""
    repo = SimRepository()
    repo.set_dynamic_entry(_CT, 1, "a")
    repo.set_dynamic_entry(_CT, 2, "b")

    # The current implementation discards the ``dict.pop`` return value, so the
    # method returns ``None`` rather than the stored value. Pin that contract so a
    # future change to the return value is a deliberate, reviewed decision.
    assert repo.delete_dynamic_entry(_CT, 1) is None  # type: ignore[func-returns-value]

    assert repo.get_dynamic_entry(_CT, 1) is None
    assert repo.get_dynamic_source_weights(_CT) == [2]


def test_delete_dynamic_entry_missing_weight_raises_keyerror() -> None:
    """``delete_dynamic_entry`` uses ``dict.pop`` without a default."""
    repo = SimRepository()
    with pytest.raises(KeyError):
        repo.delete_dynamic_entry(_CT, 999)


def test_dynamic_entries_are_independent_per_component_type() -> None:
    """Entries stored under one component type do not leak into another."""
    repo = SimRepository()
    other: lt.ComponentType = lt.ComponentType.BATTERY
    repo.set_dynamic_entry(_CT, 1, "pv-value")
    repo.set_dynamic_entry(other, 1, "battery-value")
    assert repo.get_dynamic_entry(_CT, 1) == "pv-value"
    assert repo.get_dynamic_entry(other, 1) == "battery-value"
    assert repo.get_dynamic_source_weights(_CT) == [1]
    assert repo.get_dynamic_source_weights(other) == [1]


# --------------------------------------------------------------------------- #
# clear()
# --------------------------------------------------------------------------- #
def test_clear_deletes_both_internal_dicts() -> None:
    """``clear`` deletes the ``entries`` and ``dynamic_entries`` attributes."""
    repo = SimRepository()
    repo.set_entry("a", 1)
    repo.set_dynamic_entry(_CT, 1, "x")
    repo.clear()
    assert hasattr(repo, "entries") is False
    assert hasattr(repo, "dynamic_entries") is False


def test_clear_on_fresh_empty_repo_does_not_raise() -> None:
    """``clear`` on a freshly constructed, empty repo is a no-op (no error)."""
    repo = SimRepository()
    repo.clear()
    assert hasattr(repo, "entries") is False
    assert hasattr(repo, "dynamic_entries") is False


# --------------------------------------------------------------------------- #
# The repository in a real run
# --------------------------------------------------------------------------- #
@utils.measure_execution_time
def test_household_run_publishes_the_weather_series_into_the_run_repository(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Check that a real run exchanges its whole-year series through the run's own repository.

    The CRUD tests above drive :class:`SimRepository` directly; this one drives it the way a
    simulation does. A Weather/occupancy/Building household publishes the Weather's full-year
    series into the repository the ``Simulator`` owns, under the key names the ``Weather`` class
    exposes, and that is where the PV system reads them. Nothing here is process-global: the
    repository belongs to this ``Simulator`` and is cleared when its run ends.
    """

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    seconds_per_timestep = 60 * 60

    # =========================================================================================================================================================
    # Build Components

    # Build Simulation Parameters
    if my_simulation_parameters is None:
        my_simulation_parameters = SimulationParameters.one_day_only(
            year=year, seconds_per_timestep=seconds_per_timestep
        )

    # this part is copied from hisim_main
    path_to_be_added = str(Path(PATH).resolve().parent)
    # Build Simulator

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_for_test_sim_repository",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # Build Weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Building
    my_building_config = building.BuildingConfig.preset_standard("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    my_building = building.Building(
        config=my_building_config, my_simulation_parameters=my_simulation_parameters
    )
    # Build Occupancy
    my_occupancy_config = (
        loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.get_default_utsp_connector_config()
    )
    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )

    # =========================================================================================================================================================
    # Connect Components

    # Building
    my_building.connect_input(
        my_building.Altitude, my_weather.component_name, my_weather.Altitude
    )
    my_building.connect_input(
        my_building.Azimuth, my_weather.component_name, my_weather.Azimuth
    )
    my_building.connect_input(
        my_building.DirectNormalIrradiance,
        my_weather.component_name,
        my_weather.DirectNormalIrradiance,
    )
    my_building.connect_input(
        my_building.DiffuseHorizontalIrradiance,
        my_weather.component_name,
        my_weather.DiffuseHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.GlobalHorizontalIrradiance,
        my_weather.component_name,
        my_weather.GlobalHorizontalIrradiance,
    )
    my_building.connect_input(
        my_building.DirectNormalIrradianceExtra,
        my_weather.component_name,
        my_weather.DirectNormalIrradianceExtra,
    )
    my_building.connect_input(
        my_building.ApparentZenith, my_weather.component_name, my_weather.ApparentZenith
    )
    my_building.connect_input(
        my_building.TemperatureOutside,
        my_weather.component_name,
        my_weather.TemperatureOutside,
    )
    my_building.connect_input(
        my_building.HeatingByResidents,
        my_occupancy.component_name,
        my_occupancy.HeatingByResidents,
    )

    my_building.connect_input(
        my_building.HeatingByDevices,
        my_occupancy.component_name,
        my_occupancy.HeatingByDevices,
    )

    # =========================================================================================================================================================
    # Add Components to Simulator and run all timesteps

    my_sim.add_component(my_weather)
    my_sim.add_component(my_occupancy)
    my_sim.add_component(my_building)

    # Prepare the components explicitly first, so the per-simulation repository can be inspected:
    # ``run_all_timesteps`` prepares them again and then clears the repository at the end of the
    # run, which drops the very entries under test.
    my_sim.prepare_calculation()
    published = dict(my_sim.simulation_repository.entries)

    my_sim.run_all_timesteps()

    # The Weather publishes its eight full-year series into the repository the Simulator owns,
    # which is where the PV system reads them. Two are asserted by name: indexing the key proves
    # it is there, and a non-empty series proves the Weather genuinely pushed its computed values
    # through rather than registering an empty entry. The count is the eight series plus the
    # weather location the report region is read from.
    assert len(published[weather.Weather.YEARLY_TEMPERATURE_OUTSIDE]) > 0
    assert len(published[weather.Weather.YEARLY_AZIMUTH]) > 0
    assert len(published) >= 9
