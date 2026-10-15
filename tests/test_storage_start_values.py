"""Every storage starts a run at its one stated start value, about half charged.

A run that starts on an empty or a full storage carries a start transient of hours to days in every stateful column,
and its results depend on where the run starts. So each storage states its start value once, as a class constant
with its unit, and builds its first state from it; the state classes carry no default of their own that could
contradict it. These tests pin each constant and check that a freshly built component starts from it:

* the house battery and the car battery start at a state of charge of 0.5;
* the hydrogen tank starts half way between its floor and its capacity, 250 kg for the 500 kg preset;
* the domestic-hot-water tank starts at 50 °C, half its usable charge: half way between the 40 °C tap temperature,
  below which its water is no longer usable, and the 60 °C warm-water aim;
* the example storage starts at half its capacity, 25 kWh for the 50 kWh preset;
* the space-heating buffer keeps its 35 °C by the owner's decision, now stated once: its half-charged value depends
  on the emitter it feeds, which the buffer does not know.
"""

import pytest

from hisim.components import advanced_battery_bslib
from hisim.components import advanced_ev_battery_bslib
from hisim.components import example_storage
from hisim.components import generic_electrolyzer_and_h2_storage
from hisim.components import more_advanced_heat_pump_hplib
from hisim.components import simple_water_storage
from hisim.components.configuration import HouseholdWarmWaterDemandConfig
from hisim.config import ComponentID, SizingContext
from hisim.simulationparameters import SimulationParameters


class StartValues:
    """The simulation parameters and the arithmetic every start-value test shares."""

    #: A PV peak power to size the house battery against, in W; any positive value builds a battery.
    PV_PEAK_POWER_IN_WATT = 10_000.0

    @staticmethod
    def parameters() -> SimulationParameters:
        """Return one day at a 60 s resolution, enough to build every storage."""
        return SimulationParameters.one_day_only(2021, 60)


@pytest.mark.base
def test_the_house_battery_starts_half_charged() -> None:
    """The house battery starts at a state of charge of 0.5, not recharge-blocked, and its state has no default."""
    assert advanced_battery_bslib.Battery.INITIAL_STATE_OF_CHARGE == 0.5
    config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
        SizingContext(pv_peak_power_in_watt=(StartValues.PV_PEAK_POWER_IN_WATT,))
    )
    battery = advanced_battery_bslib.Battery(my_simulation_parameters=StartValues.parameters(), config=config)
    assert battery.state.state_of_charge == 0.5
    assert battery.state.recharging_is_blocked is False
    assert battery.previous_state == battery.state
    with pytest.raises(TypeError):
        advanced_battery_bslib.BatteryState()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter


@pytest.mark.base
def test_the_car_battery_starts_half_charged() -> None:
    """The car battery starts at a state of charge of 0.5, and its state class has no default to contradict it."""
    assert advanced_ev_battery_bslib.CarBattery.INITIAL_STATE_OF_CHARGE == 0.5
    config = advanced_ev_battery_bslib.CarBatteryConfig.preset_standard("CarBattery_1")
    car_battery = advanced_ev_battery_bslib.CarBattery(my_simulation_parameters=StartValues.parameters(), config=config)
    assert car_battery.state.soc == 0.5
    with pytest.raises(TypeError):
        advanced_ev_battery_bslib.EVBatteryState()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter


@pytest.mark.base
def test_the_hydrogen_tank_starts_half_full() -> None:
    """The 500 kg preset tank starts with 250 kg; a tank with a floor starts half way from the floor to the top."""
    storage_class = generic_electrolyzer_and_h2_storage.HydrogenStorage
    assert storage_class.INITIAL_FILL_FRACTION == 0.5
    config_class = generic_electrolyzer_and_h2_storage.ElectrolyzerWithHydrogenStorageConfig
    config = config_class.preset_standard("HydrogenStorage")
    assert not hasattr(config, "starting_fill")
    storage = storage_class(my_simulation_parameters=StartValues.parameters(), config=config)
    assert storage.hydrogenstorage.fill == 250.0
    assert storage_class.initial_fill_in_kg(min_capacity_in_kg=100.0, max_capacity_in_kg=500.0) == 300.0


@pytest.mark.base
def test_the_hot_water_tank_starts_half_way_between_the_tap_temperature_and_the_warm_water_aim() -> None:
    """The hot-water tank starts at 50 °C: half way from the 40 °C tap temperature to the 60 °C warm-water aim."""
    tank_class = simple_water_storage.SimpleDHWStorage
    tap_temperature_in_celsius = (
        HouseholdWarmWaterDemandConfig.ww_temperature_demand - HouseholdWarmWaterDemandConfig.temperature_difference_hot
    )
    # The warm-water aim, read where it is configured: the heat pump's hot-water controller.
    warm_water_aim_in_celsius = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.t_max_dhw_storage_in_celsius
    )
    assert tap_temperature_in_celsius == 40
    assert warm_water_aim_in_celsius == 60.0
    assert tank_class.INITIAL_TEMPERATURE_IN_CELSIUS == 50.0
    assert tank_class.INITIAL_TEMPERATURE_IN_CELSIUS == pytest.approx(
        (tap_temperature_in_celsius + warm_water_aim_in_celsius) / 2, abs=1e-12
    )
    config = simple_water_storage.SimpleDHWStorageConfig(
        component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=250.0
    )
    tank = tank_class(my_simulation_parameters=StartValues.parameters(), config=config)
    assert tank.state.temperature_at_start_of_step_in_celsius == 50.0


@pytest.mark.base
def test_the_example_storage_starts_half_full() -> None:
    """The 50 kWh example storage starts with 25 kWh."""
    assert example_storage.SimpleStorage.INITIAL_FILL_FRACTION == 0.5
    config = example_storage.SimpleStorageConfig.preset_thermal("SimpleThermalStorage")
    storage = example_storage.SimpleStorage(my_simulation_parameters=StartValues.parameters(), config=config)
    assert storage.state.fill_in_kwh == 25.0


@pytest.mark.base
def test_the_space_heating_buffer_states_its_start_temperature_once() -> None:
    """The buffer starts at its one stated 35 °C (owner, 2026-10-15), and its state class has no default."""
    buffer_class = simple_water_storage.SimpleHotWaterStorage
    assert buffer_class.INITIAL_TEMPERATURE_IN_CELSIUS == 35.0
    config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer("SimpleHotWaterStorage").resolve(
        SizingContext(maximal_thermal_power_in_watt=10_000.0)
    )
    buffer = buffer_class(my_simulation_parameters=StartValues.parameters(), config=config)
    assert buffer.state.mean_water_temperature_in_celsius == 35.0
    assert buffer.mean_water_temperature_in_water_storage_in_celsius == 35.0
    state_class = simple_water_storage.SimpleWaterStorageState
    with pytest.raises(TypeError):
        state_class()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter
