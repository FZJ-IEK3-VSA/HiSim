"""The occupants open the windows only when the room is clearly above the cooling set temperature.

A distribution system that cools the room in the same step leaves it exactly at the cooling set temperature. Without
a margin, float noise between the simulator's passes decided whether the windows opened, and the step had no
consistent state: the room flipped between the set temperature and the aired temperature until the simulator gave
up after 100 passes (measured with the space-heating buffer as a start-of-step node, the gas twin in June and August).
"""

import pytest

from hisim.components.building.building import Building


@pytest.mark.base
@pytest.mark.parametrize(
    ("indoor_in_celsius", "outside_in_celsius", "expected"),
    [
        (25.5, 18.0, True),
        (25.0, 18.0, False),
        (25.0 + 1e-12, 18.0, False),
        (25.0 + Building.WINDOW_OPENING_MARGIN_IN_KELVIN / 2.0, 18.0, False),
        (25.0 + 2.0 * Building.WINDOW_OPENING_MARGIN_IN_KELVIN, 18.0, True),
        (25.5, 26.0, False),
        (24.0, 18.0, False),
    ],
)
def test_the_windows_open_only_clearly_above_the_cooling_set_temperature(
    indoor_in_celsius: float, outside_in_celsius: float, expected: bool
) -> None:
    """With a 22 °C initial and a 25 °C cooling set temperature: aired above 25.01 °C with a cooler outside only."""
    assert (
        Building.occupants_open_the_windows(
            initial_temperature_in_celsius=22.0,
            set_cooling_temperature_in_celsius=25.0,
            indoor_air_temperature_in_celsius=indoor_in_celsius,
            outside_temperature_in_celsius=outside_in_celsius,
        )
        is expected
    )


@pytest.mark.base
def test_a_cooling_set_temperature_below_the_initial_temperature_never_opens_the_windows() -> None:
    """An aired room falls back to the initial temperature, so a cooling set temperature below it never airs."""
    assert not Building.occupants_open_the_windows(
        initial_temperature_in_celsius=22.0,
        set_cooling_temperature_in_celsius=21.0,
        indoor_air_temperature_in_celsius=30.0,
        outside_temperature_in_celsius=10.0,
    )
