"""Test for the night setback controller."""

# clean
import pytest

from hisim import component as cp
from hisim.components import night_setback_controller
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_night_setback_controller_outputs_expected_modifier() -> None:
    """The controller should emit the setback value at night and zero by day."""

    simulation_parameters = SimulationParameters.full_year(year=2026, seconds_per_timestep=3600)
    config = night_setback_controller.NightSetbackConfig.get_default_config()
    controller = night_setback_controller.NightSetbackController(
        my_simulation_parameters=simulation_parameters,
        config=config,
    )

    fft.add_global_index_of_components([controller])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([controller]))

    controller.i_simulate(timestep=21, stsv=stsv, force_convergence=False)
    assert stsv.values[controller.building_temperature_modifier_channel.global_index] == 0.0

    controller.i_simulate(timestep=22, stsv=stsv, force_convergence=False)
    assert stsv.values[controller.building_temperature_modifier_channel.global_index] == -4.0

    controller.i_simulate(timestep=5, stsv=stsv, force_convergence=False)
    assert stsv.values[controller.building_temperature_modifier_channel.global_index] == -4.0

    controller.i_simulate(timestep=6, stsv=stsv, force_convergence=False)
    assert stsv.values[controller.building_temperature_modifier_channel.global_index] == 0.0
