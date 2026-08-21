"""Test for the night setback controller."""

# clean
import pytest

from hisim import component as cp
from hisim.components import night_setback_controller
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID
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


@pytest.mark.base
def test_night_setback_controller_all_window_modes() -> None:
    """The hoisted invariant branches must match the original recomputation.

    The branches ("none", "within", "wrap") are compared against the original
    per-timestep recomputation for every second of the day.
    """

    simulation_parameters = SimulationParameters.full_year(year=2026, seconds_per_timestep=3600)

    def _modifier(config: "night_setback_controller.NightSetbackConfig", timestep: int) -> float:
        controller = night_setback_controller.NightSetbackController(
            my_simulation_parameters=simulation_parameters,
            config=config,
        )
        fft.add_global_index_of_components([controller])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([controller]))
        controller.i_simulate(timestep=timestep, stsv=stsv, force_convergence=False)
        return stsv.values[controller.building_temperature_modifier_channel.global_index]  # type: ignore[no-any-return]

    delta = -4.0

    # "wrap": window crosses midnight (22:00 -> 06:00), the default config.
    wrap = night_setback_controller.NightSetbackConfig(
        component_id=ComponentID(name="NightSetbackController"),
        setback_delta_in_kelvin=delta,
        night_start_hour=22,
        night_end_hour=6,
    )
    for hour in range(24):
        expected = delta if (hour >= 22 or hour < 6) else 0.0
        assert _modifier(wrap, hour) == expected, f"wrap hour {hour}"

    # "within": window lies inside a single day (06:00 -> 22:00).
    within = night_setback_controller.NightSetbackConfig(
        component_id=ComponentID(name="NightSetbackController"),
        setback_delta_in_kelvin=delta,
        night_start_hour=6,
        night_end_hour=22,
    )
    for hour in range(24):
        expected = delta if (6 <= hour < 22) else 0.0
        assert _modifier(within, hour) == expected, f"within hour {hour}"

    # "none": start == end means no night window at all.
    none = night_setback_controller.NightSetbackConfig(
        component_id=ComponentID(name="NightSetbackController"),
        setback_delta_in_kelvin=delta,
        night_start_hour=12,
        night_end_hour=12,
    )
    for hour in range(24):
        assert _modifier(none, hour) == 0.0, f"none hour {hour}"
