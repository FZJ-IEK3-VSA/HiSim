"""The building publishes its indoor air temperature at the start of the step, the value the previous step ended at.

A component that decides on the room temperature inside the step (the heat distribution system's clamp, which keeps
its return from falling below the room) must read a value the step's iteration does not move; otherwise the room
temperature and the heat delivered into it can chase each other from pass to pass. The building's
``TemperatureIndoorAirAtStartOfStepInCelsius`` is that value: the indoor air temperature the previous step converged
to, and the configured initial temperature on the first step.

The vectors come from the building's committed one-day golden, which ``test_building_one_day_snapshot.py`` checks
against the component on every run.
"""

import pytest

from hisim.components.building.building import Building
from tests import building_golden_support as golden_support


@pytest.mark.base
@pytest.mark.parametrize(
    ("variant", "initial_temperature_in_celsius"), [("outputs_default_config", 22.0), ("outputs_scaled_config", 19.0)]
)
def test_the_start_of_step_indoor_temperature_is_the_previous_steps_indoor_temperature(
    variant: str, initial_temperature_in_celsius: float
) -> None:
    """Every step's start-of-step value equals the previous step's indoor air temperature; the first is the initial one.

    The default variant starts at the configuration's 22 °C, the scaled variant at its 19 °C.

    Catches a value read after this step's update (it would equal the same step's indoor temperature) or one that is
    not saved with the state (it would stay at the initial value).
    """
    outputs = golden_support.load_golden("building_one_day.json")[variant]
    at_start = outputs[Building.TemperatureIndoorAirAtStartOfStepInCelsius]
    indoor = outputs[Building.TemperatureIndoorAir]
    assert at_start[0] == initial_temperature_in_celsius
    assert at_start[1:] == indoor[:-1]
    assert at_start[1:] != indoor[1:]
