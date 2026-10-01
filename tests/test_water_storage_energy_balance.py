"""The water storages conserve the energy their heat generators book (hisim-4g9.16).

Owner decision of 2026-09-27, option B. The vessel's temperature follows the booked energy,
``T_new = T0 + (Q_gen - Q_draw) / (M c)`` before its standby loss; a delivery is accepted up to what keeps
the vessel at or below the supply temperature, ``M c (T_supply - T0) + Q_draw``, and nothing is accepted from
a flow at or below the vessel's temperature; the accepted heat is fed back to the generator, which books and
buys carrier for it alone. The hot-water draw passes a thermostatic mixing valve, and a draw cannot take the
vessel below the temperature of the water that refills it.

The unit tests pin each rule with hand-computed numbers. The regression test runs a winter week of two
recorded households, a gas boiler and a heat pump, each with a hot-water tank and a space-heating buffer,
at 900 and 3600 s, and checks the per-step balance of both vessels from the result table alone:
``Q_booked - Q_draw - Q_loss - M c (T0(t+1) - T0(t)) = 0`` to 1e-9 kWh, with ``Q_booked`` read from the
generator's own outputs and the space-heating draw from the distribution system's.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import simple_water_storage
from hisim.components.accepted_heat import AcceptedHeat
from hisim.components.configuration import PhysicsConfig
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

#: Specific heat capacity of water HiSim uses, J/(kg K).
C_WATER = PhysicsConfig.get_properties_for_energy_carrier(
    energy_carrier=lt.LoadTypes.WATER
).specific_heat_capacity_in_joule_per_kg_per_kelvin

Storage = simple_water_storage.SimpleWaterStorage


def wh(mass_in_kg: float, kelvin: float) -> float:
    """``m c dT`` in Wh."""
    return mass_in_kg * C_WATER * kelvin / 3600


# --- the delivery cap -------------------------------------------------------------------------------------------


@pytest.mark.base
def test_a_delivery_the_vessel_has_room_for_is_accepted_whole() -> None:
    """200 kg at 50 degC, 100 kg arriving at 55 degC with a 50 degC return: 5 K of 100 kg fits under 55 degC."""
    capacity = wh(200, 1)
    offered = wh(100, 5)

    accepted = Storage.heat_accepted_from_generator_in_watt_hour(offered, 55.0, 50.0, capacity, 0.0)

    assert accepted == pytest.approx(offered)
    assert Storage.temperature_after_heat_exchange_in_celsius(50.0, accepted, 0.0, capacity) == pytest.approx(52.5)


@pytest.mark.base
def test_a_delivery_is_capped_where_the_vessel_would_pass_the_supply_temperature() -> None:
    """A 250 kg tank at 50 degC fed 1000 kg at 60 degC in one step takes 250 kg * 10 K, not 1000 kg * 10 K."""
    capacity = wh(250, 1)

    accepted = Storage.heat_accepted_from_generator_in_watt_hour(wh(1000, 10), 60.0, 50.0, capacity, 0.0)

    assert accepted == pytest.approx(wh(250, 10))  # 2902.78 Wh of the 11611.1 Wh offered
    assert Storage.temperature_after_heat_exchange_in_celsius(50.0, accepted, 0.0, capacity) == pytest.approx(60.0)


@pytest.mark.base
def test_the_draw_of_the_same_step_widens_the_room() -> None:
    """With 1 kWh drawn in the same step the vessel takes 1 kWh more and still ends at the supply temperature."""
    capacity = wh(250, 1)

    accepted = Storage.heat_accepted_from_generator_in_watt_hour(wh(1000, 10), 60.0, 50.0, capacity, 1000.0)

    assert accepted == pytest.approx(wh(250, 10) + 1000.0)
    assert Storage.temperature_after_heat_exchange_in_celsius(50.0, accepted, 1000.0, capacity) == pytest.approx(60.0)


@pytest.mark.base
@pytest.mark.parametrize("supply_temperature", [50.0, 45.0])
def test_a_flow_at_or_below_the_vessel_temperature_is_accepted_as_nothing(supply_temperature: float) -> None:
    """No negative delivery: a collector running cold or a cooling machine is booked nothing."""
    offered = wh(100, supply_temperature - 40.0)  # its own return may be colder than the vessel

    assert Storage.heat_accepted_from_generator_in_watt_hour(offered, supply_temperature, 50.0, wh(250, 1), 0.0) == 0.0
    assert Storage.heat_accepted_from_generator_in_watt_hour(-500.0, 60.0, 50.0, wh(250, 1), 0.0) == 0.0


@pytest.mark.base
def test_the_secondary_slot_gets_the_room_the_primary_left() -> None:
    """Primary takes 2 of the 2.5 K room below 55 degC in a 1 kWh/K vessel; the secondary gets the last 0.5 kWh."""
    capacity = 1000.0  # Wh/K
    primary = Storage.heat_accepted_from_generator_in_watt_hour(2000.0, 55.0, 52.5, capacity, 0.0)
    secondary = Storage.heat_accepted_from_generator_in_watt_hour(4000.0, 55.0, 52.5, capacity, 0.0, primary)

    assert (primary, secondary) == (pytest.approx(2000.0), pytest.approx(500.0))


# --- the draw floor and the mixing valve -----------------------------------------------------------------------


@pytest.mark.base
def test_a_draw_cannot_take_the_vessel_below_its_refill_temperature() -> None:
    """A 1 kWh/K buffer at 30 degC with a 25 degC return gives 5 kWh plus what was delivered, not the 8 kWh asked."""
    assert Storage.heat_granted_to_draw_in_watt_hour(8000.0, 1000.0, 30.0, 25.0, 1000.0) == pytest.approx(6000.0)
    assert Storage.heat_granted_to_draw_in_watt_hour(3000.0, 0.0, 30.0, 25.0, 1000.0) == pytest.approx(3000.0)
    assert Storage.heat_granted_to_draw_in_watt_hour(3000.0, 0.0, 20.0, 25.0, 1000.0) == 0.0
    assert Storage.heat_granted_to_draw_in_watt_hour(-300.0, 0.0, 30.0, 25.0, 1000.0) == -300.0


@pytest.mark.base
def test_the_mixing_valve_takes_less_hot_water_from_a_hot_tank() -> None:
    """60 l wanted at 40 degC from a 60 degC tank and 10 degC mains: 36 l of tank water, 60 l * 30 K of heat."""
    hot_water, requested, demand = simple_water_storage.SimpleDHWStorage.hot_water_draw(60.0, 60.0, 40.0, 10.0)

    assert hot_water == pytest.approx(36.0)  # 60 * 30 / 50
    assert requested == pytest.approx(wh(60, 30)) == demand  # 2090 Wh


@pytest.mark.base
def test_a_tank_below_the_tap_temperature_passes_its_water_unmixed_and_falls_short() -> None:
    """60 l from a 30 degC tank: all 60 l, 60 l * 20 K of heat, a third of the 60 l * 30 K demand unmet."""
    hot_water, requested, demand = simple_water_storage.SimpleDHWStorage.hot_water_draw(60.0, 30.0, 40.0, 10.0)

    assert hot_water == pytest.approx(60.0)
    assert requested == pytest.approx(wh(60, 20))
    assert demand - requested == pytest.approx(wh(60, 10))
    assert simple_water_storage.SimpleDHWStorage.hot_water_draw(60.0, 8.0, 40.0, 10.0)[1] == 0.0


# --- the generator side ----------------------------------------------------------------------------------------


def fake_output(name: str, load_type: lt.LoadTypes, unit: lt.Units) -> cp.ComponentOutput:
    """A stand-in output a component input can be connected to."""
    return cp.ComponentOutput(name, name, load_type, unit, component_id=ComponentID(name))


@pytest.mark.base
def test_a_generator_books_what_the_storage_accepted_and_scales_its_carrier() -> None:
    """Connected, 3 kW accepted of 5 kW offered books 3 kW and 60 % of the fuel; unconnected, its own 5 kW."""
    channel = cp.ComponentInput(
        "Boiler", "ThermalPowerAcceptedByStorageDhw", lt.LoadTypes.HEATING, lt.Units.WATT, False
    )
    stsv = cp.SingleTimeStepValues(1)

    assert AcceptedHeat.booked(5000.0, channel, stsv) == (5000.0, 1.0)

    accepted = fake_output("Tank", lt.LoadTypes.HEATING, lt.Units.WATT)
    accepted.global_index = 0
    channel.source_output = accepted
    stsv.values[0] = 3000.0
    assert AcceptedHeat.booked(5000.0, channel, stsv) == (3000.0, pytest.approx(0.6))
    assert AcceptedHeat.booked(0.0, channel, stsv) == (0.0, 0.0)


@pytest.mark.base
def test_the_hot_water_tank_steps_on_booked_energy() -> None:
    """One 900 s step of a 250 l tank at 50 degC: a 60 degC flow of 0.5 kg/s is capped, 60 l are drawn.

    M = 248 kg. The flow offers 450 kg * 10 K; the room is 248 kg * 10 K plus the draw of 60 l * 0.992 kg/l
    * 30 K, so the tank takes exactly that and reaches 60 degC before its standby loss.
    """
    parameters = SimulationParameters.one_day_only(2021, 900)
    config = simple_water_storage.SimpleDHWStorageConfig(
        component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=250.0
    )
    storage = simple_water_storage.SimpleDHWStorage(my_simulation_parameters=parameters, config=config)
    inputs = [
        (storage.water_consumption_channel, lt.LoadTypes.WARM_WATER, lt.Units.LITER, 60.0),
        (storage.water_temperature_heat_generator_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, 60.0),
        (storage.water_mass_flow_rate_heat_generator_input_channel, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, 0.5),
    ]
    fakes: List[cp.ComponentOutput] = []
    for number, (channel, load_type, unit, _) in enumerate(inputs):
        fake = fake_output(f"Fake{number}", load_type, unit)
        channel.source_output = fake
        fakes.append(fake)
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, storage]))
    fft.add_global_index_of_components([*fakes, storage])
    for fake, (_, _, _, value) in zip(fakes, inputs):
        stsv.values[fake.global_index] = value
    storage.state.mean_water_temperature_in_celsius = 50.0

    storage.i_simulate(0, stsv, False)

    drawn = wh(60 * 0.992, 30)
    assert stsv.values[storage.thermal_energy_dhw_channel.global_index] == pytest.approx(-drawn)
    assert stsv.values[storage.thermal_energy_from_heat_generator_channel.global_index] == pytest.approx(
        wh(248, 10) + drawn
    )
    assert stsv.values[storage.thermal_power_from_heat_generator_channel.global_index] == pytest.approx(
        (wh(248, 10) + drawn) * 4
    )
    assert storage.mean_water_temperature_in_water_storage_in_celsius == pytest.approx(60.0)
    assert stsv.values[storage.thermal_energy_unmet_dhw_channel.global_index] == 0.0


# --- the regression: a winter week of two households ------------------------------------------------------------


class WinterWeek:
    """Runs one recorded household for the second January week of 2021 and reads its balance."""

    ROOT = Path(__file__).resolve().parents[1]

    #: The recorded twin, and the output names of its generator: the heat it books per path in Wh.
    HOUSEHOLDS: Dict[str, Tuple[str, str, str]] = {
        "household_gas_building_sizer": ("CondensingGasBoiler", "ThermalOutputEnergyDhw", "ThermalOutputEnergySh"),
        "household_heatpump_building_sizer": ("MoreAdvancedHeatPumpHPLib", "ThermalEnergyDHW", "ThermalEnergySH"),
    }

    @classmethod
    def run(cls, stem: str, seconds_per_timestep: int, directory: Path) -> Any:
        """Run the household offline (predefined load profile) and return the built system."""
        from hisim.energy_system.executor import run_energy_system

        twin = (cls.ROOT / "energy_systems" / f"{stem}.energy_system.yaml").read_text(encoding="utf-8")
        assert "USE_LOCAL_LPG" in twin
        energy_system = directory / f"{stem}.energy_system.yaml"
        energy_system.write_text(twin.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        parameters = directory / "winter_week.simulation.yaml"
        parameters.write_text(
            "start_date: '2021-01-10T00:00:00'\n"
            "end_date: '2021-01-17T00:00:00'\n"
            f"seconds_per_timestep: {seconds_per_timestep}\n"
            "country: DE\n"
            "logging_level: 3\n"
            "post_processing_options: []\n",
            encoding="utf-8",
        )
        return run_energy_system(energy_system, parameters, result_directory=str(directory / "results"))

    @staticmethod
    def column(built: Any, component: str, field: str) -> np.ndarray:
        """One output of the result table as an array."""
        outputs = built.simulator.all_outputs
        (index,) = [i for i, o in enumerate(outputs) if o.component_name == component and o.field_name == field]
        return np.asarray(built.simulator.results_data_frame.iloc[:, index], dtype=float)


@pytest.mark.system_setups
@pytest.mark.parametrize("seconds_per_timestep", [900, 3600])
@pytest.mark.parametrize("stem", sorted(WinterWeek.HOUSEHOLDS))
def test_both_storages_close_their_energy_balance_on_every_step(
    stem: str, seconds_per_timestep: int, tmp_path: Path
) -> None:
    """Q_booked - Q_draw - Q_loss - dU is zero on every step of a winter week, for both vessels."""
    built = WinterWeek.run(stem, seconds_per_timestep, tmp_path)
    generator, booked_dhw, booked_sh = WinterWeek.HOUSEHOLDS[stem]
    components = dict(built.wired.components)
    hours = seconds_per_timestep / 3600

    def col(component: str, field: str) -> np.ndarray:
        return WinterWeek.column(built, component, field)

    for storage_name, booked_field in (("DHWStorage", booked_dhw), ("SimpleHotWaterStorage", booked_sh)):
        heat_capacity_in_watt_hour_per_kelvin = wh(components[storage_name].water_mass_in_storage_in_kg, 1)
        booked = col(generator, booked_field)
        accepted = col(storage_name, "ThermalEnergyFromHeatGenerator") + col(
            storage_name, "ThermalEnergyFromSecondaryHeatGenerator"
        )
        if storage_name == "DHWStorage":
            drawn = -col(storage_name, "ThermalEnergyConsumptionDHW")
            assert (drawn >= 0).all()
        else:
            drawn = col(storage_name, "ThermalPowerConsumptionHeatDistribution") * hours
            # the distribution system delivers to the building exactly what the buffer gave
            np.testing.assert_array_equal(col("HeatDistributionSystem", "ThermalPowerDelivered") * hours, drawn)
        # the start-of-step temperature of step t + 1 is the end of step t; the standby loss of step t is
        # published at step t + 1
        start_temperature = col(storage_name, "WaterMeanTemperatureInStorage")
        loss = col(storage_name, "StandbyHeatLoss")[1:] * hours
        residual_in_watt_hour = (
            booked[:-1]
            - drawn[:-1]
            - loss
            - heat_capacity_in_watt_hour_per_kelvin * np.diff(start_temperature)
        )

        np.testing.assert_allclose(booked, accepted, rtol=0, atol=1e-9)
        assert np.abs(residual_in_watt_hour).max() <= 1e-6, (storage_name, np.abs(residual_in_watt_hour).max())
        assert booked.sum() > 0, storage_name
        assert ((start_temperature >= 0) & (start_temperature <= 90)).all(), storage_name


# --- the feed-forward of the generator controllers (hisim-6ehm) -----------------------------------------------


@pytest.mark.base
def test_the_feed_forward_covers_the_draw_and_the_way_to_the_set_temperature() -> None:
    """2 kW drawn, 200 kg 5 K below set, 900 s: 2000 W + 200 * c * 5 / 900 W; nothing when far above set."""
    from hisim.components.generic_boiler import GenericBoilerController

    power = GenericBoilerController.feed_forward_thermal_power_in_watt(2000.0, 200.0, 30.0, 35.0, 900)

    assert power == pytest.approx(2000.0 + 200 * C_WATER * 5 / 900)  # 6644.4 W
    assert GenericBoilerController.feed_forward_thermal_power_in_watt(500.0, 200.0, 45.0, 35.0, 900) == 0.0


def small_boiler(seconds_per_timestep: int = 900) -> Any:
    """A condensing gas boiler with a 1 to 10 kW burner band, efficiency 0.6 to 0.9 across it."""
    from hisim.components import generic_boiler

    config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler")
    config.minimal_thermal_power_in_watt = 1000.0
    config.maximal_thermal_power_in_watt = 10000.0
    config.eff_th_min, config.eff_th_max = 0.6, 0.9
    return generic_boiler.GenericBoiler(SimulationParameters.one_day_only(2021, seconds_per_timestep), config)


@pytest.mark.base
def test_the_boiler_burns_what_yields_the_thermal_setpoint() -> None:
    """5 kW burner: efficiency 0.6 + 4 kW * 0.3 / 9 kW = 0.7333, 3666.7 W of heat, and back; clipped to the band."""
    boiler = small_boiler()

    fuel, efficiency = boiler.fuel_power_for_thermal_power(5000.0 * (0.6 + 4000.0 * 0.3 / 9000.0))

    assert (fuel, efficiency) == (pytest.approx(5000.0), pytest.approx(0.6 + 4000.0 * 0.3 / 9000.0))
    assert boiler.fuel_power_for_thermal_power(100.0) == (1000.0, 0.6)
    assert boiler.fuel_power_for_thermal_power(50000.0) == (pytest.approx(10000.0), pytest.approx(0.9))


@pytest.mark.base
def test_a_hot_water_charge_yields_a_step_to_a_buffer_about_to_run_dry_and_resumes() -> None:
    """Buffer forecast 20 degC under a 32 degC flow: the step goes to space heating; the charge resumes after."""
    from hisim.components import generic_boiler
    from hisim.components.dual_circuit_system import HeatingMode

    config = generic_boiler.GenericBoilerControllerConfig.preset_modulating("Controller")
    config.minimal_thermal_power_in_watt, config.maximal_thermal_power_in_watt = 1000.0, 10000.0
    config.with_domestic_hot_water_preparation = True
    controller = generic_boiler.GenericBoilerController(SimulationParameters.one_day_only(2021, 900), config)
    controller.controller_mode = HeatingMode.DOMESTIC_HOT_WATER

    controller.interleave_space_heating_into_dhw_charge(20.0, 52.0, 32.0, 0.0)
    assert (controller.controller_mode, controller.dhw_charge_pending) == (HeatingMode.SPACE_HEATING, True)

    controller.interleave_space_heating_into_dhw_charge(31.0, 51.0, 32.0, 0.0)
    assert controller.controller_mode == HeatingMode.DOMESTIC_HOT_WATER

    controller.controller_mode = HeatingMode.DOMESTIC_HOT_WATER
    controller.interleave_space_heating_into_dhw_charge(20.0, 44.0, 32.0, 0.0)  # tank below 45 degC keeps priority
    assert controller.controller_mode == HeatingMode.DOMESTIC_HOT_WATER


@pytest.mark.base
def test_the_storage_forecast_is_the_temperature_after_the_draw() -> None:
    """1 kW for 900 s out of 100 kg at 40 degC: 40 - 900 kJ / (100 kg * c)."""
    from hisim.components.accepted_heat import StorageForecast

    forecast = cp.ComponentInput("C", "ThermalPowerDrawForecast", lt.LoadTypes.HEATING, lt.Units.WATT, False)
    mass = cp.ComponentInput("C", "WaterMassInStorage", lt.LoadTypes.WARM_WATER, lt.Units.KG, False)
    stsv = cp.SingleTimeStepValues(2)
    assert StorageForecast.temperature_after_draw_in_celsius(40.0, forecast, mass, stsv, 900) is None
    for index, (channel, value) in enumerate(((forecast, 1000.0), (mass, 100.0))):
        source = fake_output(f"S{index}", channel.loadtype, channel.unit)
        source.global_index = index
        channel.source_output = source
        stsv.values[index] = value

    assert StorageForecast.temperature_after_draw_in_celsius(40.0, forecast, mass, stsv, 900) == pytest.approx(
        40.0 - 1000.0 * 900 / (100.0 * C_WATER)
    )


# --- the review round of #864: clamps, guards and the rules shared by both controllers -------------------------


def connected_channel(value: float, stsv: cp.SingleTimeStepValues, index: int, name: str = "Accepted") -> Any:
    """An accepted-heat input wired to a fake output holding ``value`` at ``index``."""
    channel = cp.ComponentInput("Generator", name, lt.LoadTypes.HEATING, lt.Units.WATT, False)
    source = fake_output(f"{name}Source{index}", lt.LoadTypes.HEATING, lt.Units.WATT)
    source.global_index = index
    channel.source_output = source
    stsv.values[index] = value
    return channel


@pytest.mark.base
@pytest.mark.parametrize(
    "own, accepted, expected",
    [
        (5000.0, -100.0, (0.0, 0.0)),  # never negative
        (0.0, 3000.0, (0.0, 0.0)),  # no heat without fuel, at any iteration
        (-500.0, 0.0, (0.0, 0.0)),  # a cooling or idle machine books nothing through a storage
        (5000.0, 5020.0, (5020.0, 1.004)),  # what the vessel holds, when the flow carried a little more
    ],
)
def test_the_booked_power_is_never_negative_and_needs_fuel(
    own: float, accepted: float, expected: Tuple[float, float]
) -> None:
    """Connected, the booked power is at least 0 and the share too; own <= 0 books (0, 0).

    It is not capped at ``own``: the storage keeps what the flow carried, and the generator books exactly
    that, so the vessel's balance closes (see :meth:`AcceptedHeat.booked`).
    """
    stsv = cp.SingleTimeStepValues(1)

    booked = AcceptedHeat.booked(own, connected_channel(accepted, stsv, 0), stsv)

    assert booked[0] == pytest.approx(expected[0])
    assert booked[1] == pytest.approx(expected[1])
    assert booked[1] >= 0.0


@pytest.mark.base
@pytest.mark.parametrize("tank_temperature", [10.0, 8.0])
def test_a_tank_not_warmer_than_the_mains_gives_no_hot_water(tank_temperature: float) -> None:
    """A tank at or below the 10 degC mains: no hot water leaves it, and the whole 60 l * 30 K demand is unmet."""
    hot_water, requested, demand = simple_water_storage.SimpleDHWStorage.hot_water_draw(
        60.0, tank_temperature, 40.0, 10.0
    )

    assert (hot_water, requested) == (0.0, 0.0)
    assert demand == pytest.approx(wh(60, 30))


@pytest.mark.base
def test_the_mixing_valve_does_not_divide_by_zero_when_the_tap_asks_for_mains_water() -> None:
    """A warm-water temperature at the mains temperature asks for no heat and takes no hot water."""
    assert simple_water_storage.SimpleDHWStorage.hot_water_draw(60.0, 50.0, 10.0, 10.0) == (0.0, 0.0, 0.0)


def dhw_storage(volume_in_liter: float, seconds_per_timestep: int = 900) -> Any:
    """A hot-water tank of the given volume."""
    config = simple_water_storage.SimpleDHWStorageConfig(
        component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=volume_in_liter
    )
    return simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep), config=config
    )


@pytest.mark.base
@pytest.mark.parametrize("volume_in_liter", [0.0, -50.0])
def test_a_storage_without_water_is_refused_at_construction(volume_in_liter: float) -> None:
    """Both vessels divide by M c every step; a volume of zero or less is refused when they are built."""
    with pytest.raises(ValueError, match="positive volume_heating_water_storage_in_liter"):
        dhw_storage(volume_in_liter)
    config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer("Buffer")
    config.volume_heating_water_storage_in_liter = volume_in_liter
    with pytest.raises(ValueError, match="positive volume_heating_water_storage_in_liter"):
        simple_water_storage.SimpleHotWaterStorage(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 900), config=config
        )


@pytest.mark.base
def test_a_tank_too_cold_for_the_tap_reports_the_unmet_hot_water() -> None:
    """60 l from a 30 degC tank with no heating: 60 l * 0.992 kg/l * 20 K drawn, the 10 K to 40 degC unmet."""
    storage = dhw_storage(250.0)
    consumption = fake_output("Consumption", lt.LoadTypes.WARM_WATER, lt.Units.LITER)
    storage.water_consumption_channel.source_output = consumption
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([consumption, storage]))
    fft.add_global_index_of_components([consumption, storage])
    stsv.values[consumption.global_index] = 60.0
    storage.state.mean_water_temperature_in_celsius = 30.0

    storage.i_simulate(0, stsv, False)

    assert stsv.values[storage.thermal_energy_dhw_channel.global_index] == pytest.approx(-wh(60 * 0.992, 20))
    assert stsv.values[storage.thermal_energy_unmet_dhw_channel.global_index] == pytest.approx(wh(60 * 0.992, 10))


# --- the boiler's modulation law for any slope -----------------------------------------------------------------


def boiler_with_efficiencies(eff_th_min: float, eff_th_max: float) -> Any:
    """The 1 to 10 kW boiler of :func:`small_boiler` with its own efficiency end points."""
    from hisim.components import generic_boiler

    config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler")
    config.minimal_thermal_power_in_watt = 1000.0
    config.maximal_thermal_power_in_watt = 10000.0
    config.eff_th_min, config.eff_th_max = eff_th_min, eff_th_max
    return generic_boiler.GenericBoiler(SimulationParameters.one_day_only(2021, 900), config)


@pytest.mark.base
@pytest.mark.parametrize("fuel_power", [1500.0, 5000.0, 9500.0])
def test_a_boiler_whose_efficiency_falls_with_its_power_burns_what_yields_the_setpoint(fuel_power: float) -> None:
    """eff_th_min 0.95 > eff_th_max 0.85: the burner power found reproduces the thermal power asked for."""
    boiler = boiler_with_efficiencies(0.95, 0.85)
    thermal_power = fuel_power * boiler.combustion_efficiency_at_burner_power(fuel_power)

    fuel, efficiency = boiler.fuel_power_for_thermal_power(thermal_power)

    assert fuel == pytest.approx(fuel_power, rel=1e-12)
    assert fuel * efficiency == pytest.approx(thermal_power, rel=1e-12)
    assert boiler.fuel_power_for_thermal_power(8500.0) == (10000.0, 0.85)
    assert boiler.fuel_power_for_thermal_power(900.0) == (1000.0, 0.95)


@pytest.mark.base
def test_a_boiler_with_a_constant_efficiency_divides_by_it() -> None:
    """eff_th_min = eff_th_max = 0.9: 4.5 kW of heat takes 5 kW of fuel."""
    assert boiler_with_efficiencies(0.9, 0.9).fuel_power_for_thermal_power(4500.0) == (
        pytest.approx(5000.0),
        pytest.approx(0.9),
    )


@pytest.mark.base
def test_a_boiler_whose_maximal_power_is_below_its_minimal_is_refused() -> None:
    """A power band from 10 kW down to 1 kW is refused when the boiler is built."""
    from hisim.components import generic_boiler

    config = generic_boiler.GenericBoilerConfig.preset_condensing_gas("Boiler")
    config.minimal_thermal_power_in_watt = 10000.0
    config.maximal_thermal_power_in_watt = 1000.0
    with pytest.raises(ValueError, match="maximal thermal power"):
        generic_boiler.GenericBoiler(SimulationParameters.one_day_only(2021, 900), config)


# --- the heat pump's hot-water controller yields a step to the buffer ------------------------------------------


@pytest.mark.base
def test_the_heat_pump_yields_a_hot_water_step_to_a_buffer_about_to_run_dry_and_resumes() -> None:
    """A charge at a 50 degC tank: a buffer forecast 10.8 K under 25 degC, below a 35 degC flow, gets the step.

    5 kW drawn from 100 kg for 900 s lowers the buffer by 5000 * 900 / (100 c) = 10.8 K, far below the 35 degC
    set flow temperature less the 5 K margin; the controller outputs off for the step and keeps its charge, so
    with a buffer at 40 degC the next step charges again. A tank at 44 degC keeps its priority, and a forced step
    never yields.
    """
    from hisim.components import more_advanced_heat_pump_hplib as hplib

    controller = hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW"),
        my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
    )
    wired = [
        (controller.water_temperature_input_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        (controller.space_heating_buffer_temperature_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        (controller.space_heating_buffer_draw_forecast_channel, lt.LoadTypes.HEATING, lt.Units.WATT),
        (controller.space_heating_buffer_water_mass_channel, lt.LoadTypes.WARM_WATER, lt.Units.KG),
        (controller.heating_flow_temperature_channel, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
    ]
    fakes = []
    for number, (channel, load_type, unit) in enumerate(wired):
        fake = fake_output(f"Fake{number}", load_type, unit)
        channel.source_output = fake
        fakes.append(fake)
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, controller]))
    fft.add_global_index_of_components([*fakes, controller])

    def step(tank: float, buffer: float, draw: float, force_convergence: bool = False) -> float:
        for fake, value in zip(fakes, (tank, buffer, draw, 100.0, 35.0)):
            stsv.values[fake.global_index] = value
        controller.i_simulate(0, stsv, force_convergence)
        return stsv.values[controller.state_dhw_channel.global_index]

    controller.state_dhw = 2  # a charge in progress

    assert step(tank=50.0, buffer=25.0, draw=5000.0) == 0
    assert controller.state_dhw == 2
    assert step(tank=50.0, buffer=25.0, draw=5000.0, force_convergence=True) == 2
    assert step(tank=50.0, buffer=40.0, draw=0.0) == 2
    assert step(tank=44.0, buffer=25.0, draw=5000.0) == 2


# --- cooling through a water storage is refused (bead hisim-9uoo.17) -------------------------------------------


@pytest.mark.base
def test_a_heat_pump_asked_to_cool_through_a_water_storage_is_refused() -> None:
    """Controller state -1 with the space-heating accepted heat wired to a buffer raises, naming the bead."""
    from hisim.components import more_advanced_heat_pump_hplib as hplib

    config = hplib.MoreAdvancedHeatPumpHPLibConfig(
        component_id=ComponentID(name="HeatPump"),
        model="Generic",
        fluid_primary_side="air",
        group_id=1,
        heating_reference_temperature_in_celsius=-7,
        flow_temperature_in_celsius=52,
        set_thermal_output_power_in_watt=10000,
        cycling_mode=True,
        minimum_idle_time_in_seconds=600,
        minimum_running_time_in_seconds=600,
        minimum_thermal_output_power_in_watt=1500,
        position_hot_water_storage_in_system=hplib.PositionHotWaterStorageInSystemSetup.PARALLEL,
        with_domestic_hot_water_preparation=False,
        passive_cooling_with_brine=False,
        electrical_input_power_brine_pump_in_watt=None,
        massflow_nominal_secondary_side_in_kg_per_s=0.333,
        specific_heat_capacity_of_primary_fluid=0,
        device_co2_footprint_in_kg=1658.4,
        investment_costs_in_euro=15137.4,
        lifetime_in_years=10,
        maintenance_costs_in_euro_per_year=378.4,
        subsidy_as_percentage_of_investment_costs=0.3,
    )
    heatpump = hplib.MoreAdvancedHeatPumpHPLib(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
    )
    wired = [
        heatpump.on_off_switch_sh,
        heatpump.t_in_primary,
        heatpump.t_in_secondary_sh,
        heatpump.t_amb,
        heatpump.thermal_power_accepted_sh_channel,
    ]
    fakes = []
    for number, channel in enumerate(wired):
        fake = fake_output(f"Fake{number}", channel.loadtype, channel.unit)
        channel.source_output = fake
        fakes.append(fake)
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*fakes, heatpump]))
    fft.add_global_index_of_components([*fakes, heatpump])
    for fake, value in zip(fakes, (-1.0, 30.0, 22.0, 30.0, 0.0)):
        stsv.values[fake.global_index] = value
    heatpump.state.time_off = 3600  # idle long enough that the minimum idle time does not hold it off

    with pytest.raises(hplib.CoolingThroughWaterStorageNotSupportedError, match="hisim-9uoo.17"):
        heatpump.i_simulate(1, stsv, False)
