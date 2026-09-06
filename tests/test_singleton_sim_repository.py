"""Test for singleton sim repository."""

# clean

# This test deliberately clears the singleton metaclass's private instance
# cache to verify thread-safe first access.
# pylint: disable=protected-access

from typing import Optional
from pathlib import Path
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.components import loadprofilegenerator_utsp_connector
from hisim.components import weather
from hisim.components import building
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from hisim import log
from hisim import utils


# PATH and FUNC needed to build simulator, PATH is fake
PATH: str = "../system_setups/household_for_test_sim_repository.py"


@utils.measure_execution_time
@pytest.mark.base
def test_house(
    my_simulation_parameters: Optional[SimulationParameters] = None,
) -> None:  # noqa: too-many-statements
    """Check that a normal simulation works with the singleton sim repository implementation.

    The singleton identity property is verified separately in
    ``test_singleton_returns_same_instance``.
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

    my_sim.run_all_timesteps()

    log.information(f"singleton sim repo {SingletonSimRepository().my_dict}")

    # The components exchange data through the singleton sim repository while
    # the simulation is built and run. Assert that the repository was actually
    # populated during the run instead of only checking that "nothing raised" --
    # this pins down the behaviour the docstring promises (a normal simulation
    # works *with* the singleton sim repository).
    repo = SingletonSimRepository()
    assert repo.my_dict is not None
    assert len(repo.my_dict) > 0
    # Weather registers its location in the singleton during construction.
    assert SingletonDictKeyEnum.LOCATION in repo.my_dict
    assert repo.my_dict[SingletonDictKeyEnum.LOCATION] == my_weather_config.location
    # Building registers its 5R1C thermal parameters in the singleton during build().
    assert SingletonDictKeyEnum.THERMALCAPACITYENVELOPE in repo.my_dict
    assert SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING in repo.my_dict
    assert SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION in repo.my_dict
    # The thermal parameters must carry real, positive computed values -- not just
    # be present -- to confirm the building genuinely pushed its 5R1C results
    # through the singleton sim repository during the run.
    assert repo.my_dict[SingletonDictKeyEnum.THERMALCAPACITYENVELOPE] > 0
    assert repo.my_dict[SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING] > 0
    assert repo.my_dict[SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION] > 0
    assert len(repo.my_dict) >= 7


@pytest.mark.base
def test_singleton_returns_same_instance() -> None:
    """Verify the singleton identity property of ``SingletonSimRepository``.

    Two constructions of ``SingletonSimRepository`` must return the very same
    instance, while a plain (non-singleton) class must yield distinct instances.
    This check is independent of any simulation and therefore isolated from the
    component-wiring concerns covered by ``test_house``.
    """

    # https://medium.com/analytics-vidhya/how-to-create-a-thread-safe-singleton-class-in-python-822e1170a7f6
    first_singleton_sim_repository = SingletonSimRepository()
    second_singleton_sim_repository = SingletonSimRepository()

    assert first_singleton_sim_repository is second_singleton_sim_repository

    # Sanity check - a non-singleton class should create two separate instances

    class NonSingleton:

        """Just a class to show the difference between a singleton and a non-singleton."""

        pass

    assert NonSingleton() is not NonSingleton()


@pytest.mark.base
def test_singleton_concurrent_first_access_is_thread_safe() -> None:
    """Verify double-checked locking: concurrent first accesses yield one instance.

    ``SingletonMeta.__call__`` uses double-checked locking so that the lock is
    only acquired on the creation path. This test stresses that path by clearing
    the instance cache and instantiating from many threads at once; the result
    must still be a single shared instance (no duplicate construction).
    """
    import threading

    from hisim.sim_repository_singleton import SingletonMeta

    # Reset the metaclass instance cache so the very first call from each
    # thread races through the outer (lock-free) check.
    SingletonMeta._instances.clear()

    results: list = []
    num_threads = 32

    def worker() -> None:
        results.append(SingletonSimRepository())

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == num_threads
    first = results[0]
    assert all(r is first for r in results), "concurrent first access created distinct instances"

    # A subsequent call must still return the same instance (fast path).
    assert SingletonSimRepository() is first
