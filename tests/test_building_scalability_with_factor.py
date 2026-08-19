"""Test for scalability in building module.

The aim is to make the building module scalable via a factor which is the absolute conditioned floor area (or total base area)
divided by the conditioned floor area given by TABULA.
The window areas are scaled via the ratio of window area to wall area.
"""
# clean
from typing import Any
import numpy as np
import pytest

from hisim import component
from hisim.components import building
from hisim.simulationparameters import SimulationParameters
from hisim import utils


@pytest.mark.buildingtest
@utils.measure_execution_time
def test_building_scalability() -> None:
    """Verify building envelope surfaces scale with conditioned floor area."""

    base_area = 121.2
    seconds_per_timestep = 60
    simulation_parameters = SimulationParameters.full_year(
        year=2021,
        seconds_per_timestep=seconds_per_timestep,
    )

    def create_building(conditioned_floor_area: float) -> Any:
        config = building.BuildingConfig.get_default_german_single_family_home()
        config.absolute_conditioned_floor_area_in_m2 = conditioned_floor_area

        repo = component.SimRepository()

        residence = building.Building(
            config=config,
            my_simulation_parameters=simulation_parameters,
        )
        residence.set_sim_repo(repo)
        residence.i_prepare_simulation()

        return residence

    baseline = create_building(base_area)
    baseline_info = baseline.my_building_information

    opaque_baseline = [
        baseline_info.facade_area_in_m2,
        baseline_info.roof_area_in_m2,
        baseline_info.floor_area_in_m2,
    ]

    window_door_baseline = [
        baseline_info.window_area_in_m2,
        baseline_info.door_area_in_m2,
    ]

    window_areas_baseline = baseline_info.scaled_window_areas_in_m2

    for factor in [1, 5, 12]:
        residence = create_building(base_area * factor)
        info = residence.my_building_information

        np.testing.assert_allclose(
            [area * factor for area in opaque_baseline],
            [
                info.facade_area_in_m2,
                info.roof_area_in_m2,
                info.floor_area_in_m2,
            ],
            rtol=0.01,
        )

        np.testing.assert_allclose(
            [area * factor for area in window_door_baseline],
            [
                info.window_area_in_m2,
                info.door_area_in_m2,
            ],
            rtol=0.01,
        )

        window_scaling_factor = info.window_scaling_factor

        np.testing.assert_allclose(
            info.scaled_window_areas_in_m2,
            [
                area * window_scaling_factor
                for area in window_areas_baseline
            ],
            rtol=0.01,
        )
