"""Tests for sizing the air conditioner to a building's heating load.

:meth:`AirConditionerConfig.for_building_load` selects a catalogue unit and sets its
``scale_factor`` so that the simulated unit delivers
:attr:`AirConditionerConfig.CAPACITY_SHARE_FOR_BUILDING_LOAD` × the design heating load at the
heating reference temperature. Until 2026-10 the factor was inverted (share × capacity / load),
so a larger load gave a smaller unit (bead hisim-h84t); these tests keep that from coming back.
"""

from typing import List

import pytest

from hisim import simulator as sim
from hisim.components.air_conditioner import AirConditioner, AirConditionerConfig


def simulated_heating_capacity_in_watt(config: AirConditionerConfig, outdoor_temperature_in_celsius: float) -> float:
    """Builds the air conditioner from a configuration and returns its full-power heating capacity.

    The capacity is the one the component multiplies the controller's signal with: its own fit
    of the catalogue curve at the outdoor temperature, times ``scale_factor``.

    Args:
        config: The air conditioner's configuration.
        outdoor_temperature_in_celsius: The outdoor temperature, in °C.

    Returns:
        The heating capacity at full power, in W.
    """
    simulation_parameters = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    air_conditioner = AirConditioner(simulation_parameters, config)
    # The component's own curve is the point: the sizing must agree with what it simulates.
    fitted_capacity_in_watt = air_conditioner._calculate_heating_capacity(  # pylint: disable=protected-access
        outdoor_temperature_in_celsius
    )
    return float(fitted_capacity_in_watt) * config.scale_factor


@pytest.mark.base
@pytest.mark.parametrize("heating_load_in_watt", [4000.0, 6000.0, 9779.9, 12000.0, 16000.0])
@pytest.mark.parametrize("heating_reference_temperature_in_celsius", [-7.0, 5.7])
def test_simulated_heating_capacity_is_the_share_of_the_load(
    heating_load_in_watt: float, heating_reference_temperature_in_celsius: float
) -> None:
    """At the reference temperature the simulated unit heats with exactly share × load, whichever unit is selected."""
    config = AirConditionerConfig.for_building_load(
        "AirConditioner", heating_load_in_watt, heating_reference_temperature_in_celsius
    )

    capacity_in_watt = simulated_heating_capacity_in_watt(config, heating_reference_temperature_in_celsius)

    assert capacity_in_watt == pytest.approx(
        AirConditionerConfig.CAPACITY_SHARE_FOR_BUILDING_LOAD * heating_load_in_watt
    )


@pytest.mark.base
def test_a_larger_heating_load_gets_a_larger_unit() -> None:
    """The simulated capacity grows with the load, also between loads that select the same catalogue unit.

    The inverted law gave the Panasonic unit selected for 9,000 to 10,500 W a capacity that fell
    as the load rose; here it rises in proportion.
    """
    heating_loads_in_watt = [9000.0, 9500.0, 10000.0, 10500.0]
    configs = [AirConditionerConfig.for_building_load("AirConditioner", load, 5.7) for load in heating_loads_in_watt]

    capacities_in_watt: List[float] = [simulated_heating_capacity_in_watt(config, 5.7) for config in configs]

    assert len({config.model_name for config in configs}) == 1
    assert capacities_in_watt == sorted(capacities_in_watt)
    assert capacities_in_watt[-1] / capacities_in_watt[0] == pytest.approx(10500.0 / 9000.0)
