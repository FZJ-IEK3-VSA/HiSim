"""Tests for the solar thermal system component."""

import datetime
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd
import pvlib
import pytest
from oemof.thermal.solar_thermal_collector import calc_eta_c_flate_plate, flat_plate_precalc
from hisim import hydronics
from hisim import sim_repository, component, log, simulator as sim
from hisim.components import (
    generic_boiler,
    generic_district_heating,
    generic_electric_heating,
    solar_thermal_system,
    weather,
)
from hisim.components.controller_l2_energy_management_system import EMSConfig
from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLibControllerDHWConfig
from hisim.loadtypes import LoadTypes, Units
from hisim.config import AUTO, ComponentID, SizingContext
from hisim.subcomponents.pumps import FixedLiftPump, PumpOperatingPoint
from tests import functions_for_testing as fft
from tests.part_load_rigs import AllInputsRig, CollectorRig, Parameters, SolarControllerRig


@pytest.mark.base
def test_solar_thermal_system() -> None:
    """Verify a SolarThermalSystem produces the expected thermal power on a summer noon step.

    Builds a Weather and SolarThermalSystem with a 4 m² collector, drives them with a
    fake binary control signal at a 3rd-of-July noon timestep, and asserts the
    thermal power output matches the reference value (~3253.71 W).

    The sun is the weather's (hisim-midyear-windows-9g2.7): at noon of the run's standard-time clock, 13:00 summer time. Before,
    the collector worked out its own sun at noon UTC, an hour later, and the reference was 3260.38 W.
    """
    # Inputs
    seconds_per_timestep = 60

    repo = sim_repository.SimRepository()
    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Configure weather
    my_weather_config = weather.WeatherConfig.preset_aachen("Weather")
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=mysim)
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Configure solar thermal
    my_sts_config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")
    my_sts_config.area_m2 = 4
    my_sts = solar_thermal_system.SolarThermalSystem(config=my_sts_config, my_simulation_parameters=mysim)

    state_controller = component.ComponentOutput(
        "FakeControlState",
        "ControlSignal",
        LoadTypes.ANY,
        Units.BINARY,
        component_id=ComponentID("FakeControlState"),
    )
    my_sts.control_signal_channel.source_output = state_controller
    # the controller's part-load ratio reads the same fake, 1: the pump runs the whole step
    my_sts.part_load_command.requested_ratio_channel.source_output = state_controller

    my_sts.set_sim_repo(repo)
    my_sts.i_prepare_simulation()

    # Outputs
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_sts, state_controller])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(number_of_outputs)

    my_sts.t_out_channel.source_output = my_weather.air_temperature_output
    my_sts.dhi_channel.source_output = my_weather.dhi_output
    my_sts.ghi_channel.source_output = my_weather.ghi_output
    my_sts.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
    my_sts.azimuth_channel.source_output = my_weather.azimuth_output

    # Simulate
    fft.add_global_index_of_components([my_weather, my_sts, state_controller])
    stsv.values[state_controller.global_index] = 1
    timestep = 12 * 60 + 60 * 24 * 183  # 3rd July at noon
    my_weather.i_simulate(timestep, stsv, False)
    my_sts.i_simulate(timestep, stsv, False)
    log.information(f"heat power output [W]: {stsv.values[my_sts.thermal_power_w_output_channel.global_index]}")
    print(stsv.values)

    assert pytest.approx(stsv.values[my_sts.thermal_power_w_output_channel.global_index]) == 3253.712286152263


@pytest.mark.base
def test_precalc() -> None:
    """Verify oemof's flat_plate_precalc yields zero heat under zero irradiance.

    Calls flat_plate_precalc with Aachen coordinates, a 30° tilt, and zero
    global/diffuse irradiance at ambient temperature 0 °C, then asserts the
    resulting collector heat output is 0.
    """
    azimuth = (180.0,)
    tilt: float = 30.0
    eta_0: float = 0.78
    a_1_w_m2_k: float = 3.2  # W/(m2*K)
    a_2_w_m2_k: float = 0.015  # W/(m2*K2)
    coordinates = component.Coordinates(latitude_in_degrees=50.78, longitude_in_degrees=6.08)

    temperature_collector_inlet_deg_c = 55
    delta_temperature_n_k = 10

    global_horizontal_irradiance_w_m2 = 0
    diffuse_horizontal_irradiance_w_m2 = 0
    ambient_air_temperature_deg_c = 0
    timestep = 0
    time_ind = datetime.datetime(2021, 1, 1) + datetime.timedelta(0, 60 * timestep)

    precalc_data = flat_plate_precalc(
        lat=coordinates.latitude_in_degrees,
        long=coordinates.longitude_in_degrees,
        collector_tilt=tilt,
        collector_azimuth=azimuth,
        eta_0=eta_0,  # optical efficiency of the collector
        a_1=a_1_w_m2_k,  # thermal loss parameter 1
        a_2=a_2_w_m2_k,  # thermal loss parameter 2
        temp_collector_inlet=temperature_collector_inlet_deg_c,  # collectors inlet temperature
        delta_temp_n=delta_temperature_n_k,  # temperature difference between collector inlet and mean temperature
        irradiance_global=pd.Series(global_horizontal_irradiance_w_m2, index=[time_ind]),
        irradiance_diffuse=pd.Series(diffuse_horizontal_irradiance_w_m2, index=[time_ind]),
        temp_amb=pd.Series(ambient_air_temperature_deg_c, index=[time_ind]),
    )

    assert precalc_data["collectors_heat"].iloc[0] == pytest.approx(0, abs=1e-9)


@pytest.mark.base
def test_the_collector_reads_the_sun_the_weather_publishes() -> None:
    """Catches a collector that works out a sun of its own instead of reading the weather's.

    The collector used to compute the sun from the run's start date read as UTC, an hour off the weather's clock.
    With the sun's inputs set to 30 degrees from the zenith due south, the irradiance on the plane is the one pvlib
    gives for that sun, at any timestep.
    """
    outputs = CollectorRig.charge(seconds_per_timestep=3600, requested_ratio=1.0)
    area_in_m2 = solar_thermal_system.SolarThermalSystemConfig.COLLECTOR_AREA_IN_M2_PER_APARTMENT

    assert outputs[solar_thermal_system.SolarThermalSystem.SolarPowerOnCollector] == pytest.approx(
        area_in_m2
        * float(
            solar_thermal_system.plane_of_array_irradiance_w_m2(
                surface_tilt=30.0,
                surface_azimuth=180.0,
                apparent_zenith=30.0,
                solar_azimuth=180.0,
                global_horizontal_irradiance_w_m2=800.0,
                diffuse_horizontal_irradiance_w_m2=100.0,
            )
        ),
        rel=1e-12,
    )


@pytest.mark.base
def test_the_collector_grows_with_the_apartments() -> None:
    """Four square metres per apartment: a single-family house gets 4 m2, a triplex 12 m2.

    The arithmetic used to live in the setups -- one of them wrote ``4 * number_of_apartments``
    and another just ``4``, so the two disagreed for every multi-family building. The law is
    now the single place the factor is written down, and this pins both ends of it.
    """
    unresolved = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")

    assert unresolved.area_m2 is AUTO, "the preset must leave the area to its law"

    for apartments, expected_area_m2 in ((1, 4.0), (3, 12.0)):
        resolved = unresolved.resolve(SizingContext(number_of_apartments=apartments))
        assert resolved.area_m2 == expected_area_m2, (
            f"{apartments} apartment(s) must give {expected_area_m2} m2 of collector, "
            f"got {resolved.area_m2}"
        )


@pytest.mark.base
def test_a_named_collector_area_beats_the_law() -> None:
    """An author who names the area gets that area, whatever the building holds.

    Resolution is still allowed to run -- it is a no-op for a field that is already
    concrete -- so a setup can hand every config the same context without having to know
    which of them still has something to size.
    """
    config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")
    config.area_m2 = 1.5

    assert config.area_m2 == 1.5
    assert config.resolve(SizingContext(number_of_apartments=3)).area_m2 == 1.5


@pytest.mark.base
def test_the_timestep_reads_the_resolved_area() -> None:
    """Three times the collector, three times the heat: ``i_simulate`` reads the sized field.

    The area reaches the physics through ``self.area_m2``, read once in ``__init__`` through
    ``concrete()`` -- the constructor has already refused any config still carrying AUTO. Two
    collectors that differ in nothing but the dwelling count they were resolved against, driven
    by the same weather at the same instant, must give thermal power in the ratio of their areas.
    """
    seconds_per_timestep = 60
    repo = sim_repository.SimRepository()
    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    my_weather = weather.Weather(
        config=weather.WeatherConfig.preset_aachen("Weather"),
        my_simulation_parameters=mysim,
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    unresolved = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")

    def collector_of(apartments: int) -> solar_thermal_system.SolarThermalSystem:
        """Builds the collector the law gives a building with that many dwellings."""
        config = unresolved.resolve(SizingContext(number_of_apartments=apartments))
        config.component_id = ComponentID(name=f"SolarThermalSystem{apartments}")
        return solar_thermal_system.SolarThermalSystem(config=config, my_simulation_parameters=mysim)

    single_family = collector_of(1)
    triplex = collector_of(3)
    collectors = [single_family, triplex]

    assert single_family.area_m2 == 4.0
    assert triplex.area_m2 == 12.0

    state_controller = component.ComponentOutput(
        "FakeControlState",
        "ControlSignal",
        LoadTypes.ANY,
        Units.BINARY,
        component_id=ComponentID("FakeControlState"),
    )
    for collector in collectors:
        collector.control_signal_channel.source_output = state_controller
        # the controller's part-load ratio reads the same fake, 1: the pump runs the whole step
        collector.part_load_command.requested_ratio_channel.source_output = state_controller
        collector.set_sim_repo(repo)
        collector.i_prepare_simulation()
        collector.t_out_channel.source_output = my_weather.air_temperature_output
        collector.dhi_channel.source_output = my_weather.dhi_output
        collector.ghi_channel.source_output = my_weather.ghi_output
        collector.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
        collector.azimuth_channel.source_output = my_weather.azimuth_output

    everything = [my_weather, *collectors, state_controller]
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        fft.get_number_of_outputs(everything)
    )
    fft.add_global_index_of_components(everything)
    stsv.values[state_controller.global_index] = 1

    timestep = 12 * 60 + 60 * 24 * 183  # 3rd July at noon
    my_weather.i_simulate(timestep, stsv, False)
    for collector in collectors:
        collector.i_simulate(timestep, stsv, False)

    power_of = {
        collector: stsv.values[collector.thermal_power_w_output_channel.global_index]
        for collector in collectors
    }
    assert power_of[single_family] > 0, "the reference instant has to produce heat at all"
    assert power_of[triplex] == pytest.approx(3 * power_of[single_family]), (
        f"a 12 m2 collector gave {power_of[triplex]} W where a 4 m2 one gave "
        f"{power_of[single_family]} W; i_simulate is not reading the resolved area"
    )


def _collector_heat_the_series_way(
    config: solar_thermal_system.SolarThermalSystemConfig,
    apparent_zenith: float,
    solar_azimuth: float,
    global_horizontal_irradiance_w_m2: float,
    diffuse_horizontal_irradiance_w_m2: float,
    ambient_air_temperature_deg_c: float,
    temperature_collector_inlet_deg_c: float,
) -> Any:
    """The collector heat in W/m² as i_simulate computed it before hisim-e194, at half the pump's target lift.

    One-element pandas Series through ``pvlib.irradiance.dni``, ``get_total_irradiance`` and oemof's
    ``calc_eta_c_flate_plate``: correct, and about 7 ms a call. Kept here as the reference the fast
    path must equal bit for bit.
    """
    time_ind = datetime.datetime(2021, 1, 1)
    apparent_zenith_series = pd.Series(apparent_zenith, index=[time_ind])
    ghi = pd.Series(global_horizontal_irradiance_w_m2, index=[time_ind])
    dhi = pd.Series(diffuse_horizontal_irradiance_w_m2, index=[time_ind])
    dni = pvlib.irradiance.dni(ghi=ghi, dhi=dhi, zenith=apparent_zenith_series)
    total_irradiation = pvlib.irradiance.get_total_irradiance(
        surface_tilt=config.tilt,
        surface_azimuth=config.azimuth,
        solar_zenith=apparent_zenith_series,
        solar_azimuth=pd.Series(solar_azimuth, index=[time_ind]),
        dni=dni.fillna(0),
        ghi=ghi,
        dhi=dhi,
    )
    collector_efficiency = calc_eta_c_flate_plate(
        config.eta_0,
        config.a_1_w_m2_k,
        config.a_2_w_m2_k,
        temperature_collector_inlet_deg_c,
        config.pump_target_lift_in_kelvin / 2,
        pd.Series(ambient_air_temperature_deg_c, index=[time_ind]),
        total_irradiation["poa_global"],
    )
    return (collector_efficiency * total_irradiation["poa_global"]).iloc[0]


def _bits(value: Any) -> bytes:
    """The float64 bit pattern of a value: tells 0.0 from -0.0 and one NaN from a number."""
    return np.float64(value).tobytes()


@pytest.mark.base
@pytest.mark.parametrize(
    "apparent_zenith, solar_azimuth, ghi, dhi, ambient_deg_c, inlet_deg_c",
    [
        (28.3, 180.0, 870.0, 120.0, 25.0, 40.0),  # summer noon: the collector works
        (28.3, 180.0, 870.0, 120.0, 25.0, 95.0),  # so hot an inlet the efficiency clips at zero
        (75.0, 110.0, 140.0, 60.0, -3.0, 20.0),  # low winter morning sun
        (89.2, 60.0, 4.0, 3.0, 5.0, 30.0),  # past the 88° zenith: pvlib drops the DNI as NaN
        (60.0, 250.0, 100.0, 180.0, 12.0, 30.0),  # DHI above GHI: a negative DNI, dropped as NaN
        (120.0, 0.0, 0.0, 0.0, 2.0, 30.0),  # night
        (50.0, 200.0, 300.0, 300.0, 18.5, 18.5),  # overcast, no beam at all
        (95.0, 320.0, -0.4, 0.0, 1.0, 45.0),  # a slightly negative night GHI in the weather file
    ],
)
def test_the_fast_collector_equals_the_series_path(
    apparent_zenith: float,
    solar_azimuth: float,
    ghi: float,
    dhi: float,
    ambient_deg_c: float,
    inlet_deg_c: float,
) -> None:
    """The scalar collector calculation gives the old Series result bit for bit, edge cases included."""
    config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")

    poa = solar_thermal_system.plane_of_array_irradiance_w_m2(
        config.tilt, config.azimuth, apparent_zenith, solar_azimuth, ghi, dhi
    )
    fast = solar_thermal_system.flat_plate_collector_heat_w_m2(
        config.eta_0,
        config.a_1_w_m2_k,
        config.a_2_w_m2_k,
        inlet_deg_c,
        config.pump_target_lift_in_kelvin / 2,
        ambient_deg_c,
        poa,
    )
    reference = _collector_heat_the_series_way(
        config, apparent_zenith, solar_azimuth, ghi, dhi, ambient_deg_c, inlet_deg_c
    )

    assert _bits(fast) == _bits(reference), f"fast path gave {fast!r}, the Series path {reference!r}"


@pytest.mark.base
def test_the_timestep_output_equals_the_series_path() -> None:
    """The collector heat equals the old Series path at hand-picked steps of a year, and ``i_simulate`` books it.

    Every hour of a summer and a winter day in Aachen, each at three inlet temperatures simulated
    one after the other within the same timestep -- the way convergence iterations call it -- so
    the per-timestep reuse of the plane-of-array irradiance is exercised against a changing storage.
    """
    repo = sim_repository.SimRepository()
    mysim = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=3600)
    my_weather = weather.Weather(config=weather.WeatherConfig.preset_aachen("Weather"), my_simulation_parameters=mysim)
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")
    config.area_m2 = 4
    my_sts = solar_thermal_system.SolarThermalSystem(config=config, my_simulation_parameters=mysim)
    my_sts.set_sim_repo(repo)
    my_sts.i_prepare_simulation()

    fake_outputs = [
        component.ComponentOutput(
            "Fake", name, LoadTypes.ANY, Units.ANY, component_id=ComponentID("Fake" + name)
        )
        for name in ("ControlSignal", "InletTemperature")
    ]
    control, inlet = fake_outputs
    my_sts.control_signal_channel.source_output = control
    # the controller's part-load ratio reads the same fake, 1: the pump runs the whole step
    my_sts.part_load_command.requested_ratio_channel.source_output = control
    my_sts.water_temperature_input_channel.source_output = inlet
    my_sts.t_out_channel.source_output = my_weather.air_temperature_output
    my_sts.dhi_channel.source_output = my_weather.dhi_output
    my_sts.ghi_channel.source_output = my_weather.ghi_output
    my_sts.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
    my_sts.azimuth_channel.source_output = my_weather.azimuth_output
    fft.add_global_index_of_components([my_weather, my_sts, control, inlet])
    stsv = component.SingleTimeStepValues(fft.get_number_of_outputs([my_weather, my_sts, control, inlet]))
    stsv.values[control.global_index] = 1

    least_flow_kg_s = config.pump_minimum_mass_flow_in_kg_per_hour_per_m2 * config.area_m2 / 3600
    compared = 0
    above_the_minimum = 0
    for day in (15, 183):
        for hour in range(24):
            timestep = day * 24 + hour
            my_weather.i_simulate(timestep, stsv, False)
            for inlet_deg_c in (20.0, 55.0, 35.5):
                stsv.values[inlet.global_index] = inlet_deg_c
                my_sts.i_simulate(timestep, stsv, False)
                reference = config.area_m2 * _collector_heat_the_series_way(
                    config,
                    stsv.values[my_weather.apparent_zenith_output.global_index],
                    stsv.values[my_weather.azimuth_output.global_index],
                    stsv.values[my_weather.ghi_output.global_index],
                    stsv.values[my_weather.dhi_output.global_index],
                    stsv.values[my_weather.air_temperature_output.global_index],
                    inlet_deg_c,
                )
                fast = my_sts.area_m2 * my_sts.collector_curve.heat_in_watt_per_m2(
                    inlet_temperature_in_celsius=inlet_deg_c,
                    inlet_to_mean_temperature_difference_in_kelvin=config.pump_target_lift_in_kelvin / 2,
                    ambient_temperature_in_celsius=stsv.values[my_weather.air_temperature_output.global_index],
                    irradiance_in_watt_per_m2=float(my_sts.plane_of_array_irradiance_w_m2),
                )
                assert _bits(fast) == _bits(reference), (
                    f"timestep {timestep}, inlet {inlet_deg_c} °C: {fast!r} against the Series path's {reference!r}"
                )
                # the booked heat is what the circuit's water carries: the collector heat at the target lift to the
                # last digits while the pump runs above its minimum flow
                booked_w = stsv.values[my_sts.thermal_power_w_output_channel.global_index]
                flow_kg_s = stsv.values[my_sts.water_mass_flow_kg_s_output_channel.global_index]
                if flow_kg_s > least_flow_kg_s:
                    assert booked_w == pytest.approx(reference, rel=1e-12)
                    above_the_minimum += 1
                compared += 1
    assert compared == 2 * 24 * 3
    assert above_the_minimum > 10


@pytest.mark.base
def test_the_collector_pumps_its_heat_over_the_target_lift_and_its_sensor_reads_the_stagnation_temperature() -> None:
    """A collector circuit whose water carried another heat than the collector's would unbalance the tank.

    At noon on 2 July in Aachen, with the tank's step mean at 40 °C: the pump runs ``Q(40) / (c * 20 K)``, within
    its flow bounds, and the supply is 60 °C, so the water carries ``Q(40)`` at the target lift. The sensor of a
    collector whose pump stood in the previous step reads its stagnation temperature whether or not the pump runs now.
    With the pump off the circuit moves no water and its supply is its return.
    """

    repo = sim_repository.SimRepository()
    mysim = sim.SimulationParameters.full_year(year=2021, seconds_per_timestep=3600)
    my_weather = weather.Weather(config=weather.WeatherConfig.preset_aachen("Weather"), my_simulation_parameters=mysim)
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()
    config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")
    config.area_m2 = 4
    my_sts = solar_thermal_system.SolarThermalSystem(config=config, my_simulation_parameters=mysim)
    my_sts.set_sim_repo(repo)
    my_sts.i_prepare_simulation()
    fakes = [
        component.ComponentOutput("Fake", name, LoadTypes.ANY, Units.ANY, component_id=ComponentID("Fake" + name))
        for name in ("ControlSignal", "InletTemperature")
    ]
    control, inlet = fakes
    my_sts.control_signal_channel.source_output = control
    # the controller's part-load ratio reads the same fake, 1: the pump runs the whole step
    my_sts.part_load_command.requested_ratio_channel.source_output = control
    my_sts.water_temperature_input_channel.source_output = inlet
    my_sts.inlet_temperature_at_start_of_step_input_channel.source_output = inlet
    my_sts.t_out_channel.source_output = my_weather.air_temperature_output
    my_sts.dhi_channel.source_output = my_weather.dhi_output
    my_sts.ghi_channel.source_output = my_weather.ghi_output
    my_sts.apparent_zenith_channel.source_output = my_weather.apparent_zenith_output
    my_sts.azimuth_channel.source_output = my_weather.azimuth_output
    fft.add_global_index_of_components([my_weather, my_sts, *fakes])
    stsv = component.SingleTimeStepValues(fft.get_number_of_outputs([my_weather, my_sts, *fakes]))
    timestep = 183 * 24 + 12
    my_weather.i_simulate(timestep, stsv, False)
    air_temperature_deg_c = stsv.values[my_weather.air_temperature_output.global_index]
    stsv.values[inlet.global_index] = 40.0

    def output(channel: Any) -> float:
        return float(stsv.values[channel.global_index])

    stsv.values[control.global_index] = 1
    my_sts.i_simulate(timestep, stsv, False)
    irradiance_w_m2 = float(my_sts.plane_of_array_irradiance_w_m2)
    heat_w = my_sts.area_m2 * my_sts.collector_curve.heat_in_watt_per_m2(
        inlet_temperature_in_celsius=40.0,
        inlet_to_mean_temperature_difference_in_kelvin=10.0,
        ambient_temperature_in_celsius=air_temperature_deg_c,
        irradiance_in_watt_per_m2=irradiance_w_m2,
    )
    flow_kg_s = output(my_sts.water_mass_flow_kg_s_output_channel)
    assert flow_kg_s == pytest.approx(heat_w / (hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 20.0), rel=1e-12)
    assert 5.0 * 4 / 3600 < flow_kg_s < 40.0 * 4 / 3600
    assert output(my_sts.water_temperature_deg_c_output_channel) == 60.0
    assert output(my_sts.thermal_power_w_output_channel) == hydronics.circuit_power_w(
        mass_flow_kg_per_s=flow_kg_s, t_supply_c=60.0, t_return_c=40.0
    )
    assert output(my_sts.thermal_power_w_output_channel) == pytest.approx(heat_w, rel=1e-12)
    stagnation_c = my_sts.collector_curve.stagnation_temperature_in_celsius(
        ambient_temperature_in_celsius=air_temperature_deg_c, irradiance_in_watt_per_m2=irradiance_w_m2
    )
    sensor = my_sts.sensor_temperature_at_start_of_step_output_channel
    assert output(sensor) == stagnation_c > 60.0

    stsv.values[control.global_index] = 0
    my_sts.i_simulate(timestep, stsv, False)
    assert output(my_sts.water_mass_flow_kg_s_output_channel) == 0.0
    assert output(my_sts.water_temperature_deg_c_output_channel) == 40.0
    assert output(my_sts.thermal_power_w_output_channel) == 0.0
    assert output(sensor) == stagnation_c


class CollectorPhysics:
    """The flat-plate preset's curve and pump, and a grid of tanks, air and irradiance to check them on."""

    #: The flat-plate preset's configuration.
    CONFIG = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")

    #: The preset's efficiency curve.
    CURVE = solar_thermal_system.FlatPlateCollectorCurve(
        eta_0=CONFIG.eta_0,
        a_1_in_watt_per_m2_per_kelvin=CONFIG.a_1_w_m2_k,
        a_2_in_watt_per_m2_per_kelvin_squared=CONFIG.a_2_w_m2_k,
    )

    @classmethod
    def pump_for(cls, area_m2: float) -> FixedLiftPump:
        """Return the preset's pump for a collector field: 20 K target lift, 5 to 40 kg/(m² h) times the area.

        Args:
            area_m2: The collector area, in m².

        Returns:
            The pump, its bounds for the whole field in kg/s.
        """
        return FixedLiftPump(
            target_lift_in_kelvin=cls.CONFIG.pump_target_lift_in_kelvin,
            minimal_mass_flow_in_kg_per_second=cls.CONFIG.pump_minimum_mass_flow_in_kg_per_hour_per_m2 * area_m2 / 3600.0,
            maximal_mass_flow_in_kg_per_second=cls.CONFIG.pump_maximum_mass_flow_in_kg_per_hour_per_m2 * area_m2 / 3600.0,
        )

    @classmethod
    def point(
        cls,
        *,
        inlet_temperature_in_celsius: float,
        ambient_temperature_in_celsius: float,
        irradiance_in_watt_per_m2: float,
        area_m2: float,
        pump: Optional[FixedLiftPump] = None,
    ) -> PumpOperatingPoint:
        """Return the operating point of the preset's collector field, with the preset's pump unless one is given.

        Args:
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.
            ambient_temperature_in_celsius: The outside air temperature, in °C.
            irradiance_in_watt_per_m2: The irradiance on the collector plane, in W/m².
            area_m2: The collector area, in m².
            pump: The pump, the preset's for that area when None.

        Returns:
            The operating point (:meth:`SolarThermalSystem.collector_operating_point`).
        """
        return solar_thermal_system.SolarThermalSystem.collector_operating_point(
            pump=cls.pump_for(area_m2) if pump is None else pump,
            curve=cls.CURVE,
            inlet_temperature_in_celsius=inlet_temperature_in_celsius,
            ambient_temperature_in_celsius=ambient_temperature_in_celsius,
            irradiance_in_watt_per_m2=irradiance_in_watt_per_m2,
            area_m2=area_m2,
        )

    @staticmethod
    def heat_in_watt(point: PumpOperatingPoint, inlet_temperature_in_celsius: float) -> float:
        """Return the heat an operating point's water carries, ``c m (T_sup - T_in)``, in W.

        Args:
            point: The operating point.
            inlet_temperature_in_celsius: The collector's inlet temperature, in °C.

        Returns:
            The heat, in W.
        """
        return hydronics.circuit_power_w(
            mass_flow_kg_per_s=point.mass_flow_in_kg_per_second,
            t_supply_c=point.supply_temperature_in_celsius,
            t_return_c=inlet_temperature_in_celsius,
        )

    #: Tank (inlet) temperatures, °C.
    TANKS_IN_CELSIUS = (20.0, 45.0, 60.0)

    #: Air temperatures, °C.
    AIRS_IN_CELSIUS = (-5.0, 10.0, 25.0)

    #: Irradiances on the collector plane, W/m².
    IRRADIANCES_IN_WATT_PER_M2 = (0.0, 80.0, 150.0, 250.0, 400.0, 800.0, 1050.0)


@pytest.mark.base
@pytest.mark.parametrize("air_c", CollectorPhysics.AIRS_IN_CELSIUS)
@pytest.mark.parametrize("irradiance_w_m2", CollectorPhysics.IRRADIANCES_IN_WATT_PER_M2)
def test_the_stagnation_temperature_is_where_the_collector_gives_no_heat(air_c: float, irradiance_w_m2: float) -> None:
    """A collector sensor that read another temperature than the one at zero heat would switch the pump on too early.

    At the stagnation temperature the curve gives no heat with the whole collector at that temperature, a kelvin
    below it gives heat, and without irradiance the collector is at the air temperature.
    """
    curve = CollectorPhysics.CURVE
    stagnation_c = curve.stagnation_temperature_in_celsius(
        ambient_temperature_in_celsius=air_c, irradiance_in_watt_per_m2=irradiance_w_m2
    )
    if irradiance_w_m2 == 0.0:
        assert stagnation_c == air_c
        return
    excess_k = stagnation_c - air_c
    balance_w_m2 = (
        curve.eta_0 * irradiance_w_m2
        - curve.a_1_in_watt_per_m2_per_kelvin * excess_k
        - curve.a_2_in_watt_per_m2_per_kelvin_squared * excess_k**2
    )
    assert balance_w_m2 == pytest.approx(0.0, abs=1e-9)
    below = curve.heat_in_watt_per_m2(
        inlet_temperature_in_celsius=stagnation_c - 1.0,
        inlet_to_mean_temperature_difference_in_kelvin=0.0,
        ambient_temperature_in_celsius=air_c,
        irradiance_in_watt_per_m2=irradiance_w_m2,
    )
    assert below > 0.0


@pytest.mark.base
def test_the_stagnation_temperature_of_a_linear_curve_is_the_absorbed_irradiance_over_its_loss() -> None:
    """A curve without the quadratic loss stagnates at ``eta_0 G / a_1`` above the air, not at a division by zero."""
    curve = solar_thermal_system.FlatPlateCollectorCurve(
        eta_0=0.8, a_1_in_watt_per_m2_per_kelvin=4.0, a_2_in_watt_per_m2_per_kelvin_squared=0.0
    )
    assert curve.stagnation_temperature_in_celsius(
        ambient_temperature_in_celsius=10.0, irradiance_in_watt_per_m2=500.0
    ) == pytest.approx(110.0, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize("tank_c", CollectorPhysics.TANKS_IN_CELSIUS)
@pytest.mark.parametrize("air_c", CollectorPhysics.AIRS_IN_CELSIUS)
@pytest.mark.parametrize("irradiance_w_m2", CollectorPhysics.IRRADIANCES_IN_WATT_PER_M2)
def test_the_pump_runs_the_target_lift_within_its_bounds_and_its_water_carries_the_heat_at_the_mean_temperature(
    tank_c: float, air_c: float, irradiance_w_m2: float
) -> None:
    """The pump's flow lies within its bounds, and the heat is the curve's at the mean of inlet and outlet.

    Within the bounds the lift is the 20 K target and the heat the curve's 10 K above the inlet; at a bound the lift
    follows from the heat, and the water carries the curve's heat at half that lift above the inlet. A pump that broke
    its bounds, or a heat that did not match its lift, would book heat the collector cannot give.
    """
    area_m2 = 4.0
    pump = CollectorPhysics.pump_for(area_m2)
    point = CollectorPhysics.point(
        inlet_temperature_in_celsius=tank_c,
        ambient_temperature_in_celsius=air_c,
        irradiance_in_watt_per_m2=irradiance_w_m2,
        area_m2=area_m2,
    )
    if point.mass_flow_in_kg_per_second == 0.0:
        assert point == PumpOperatingPoint(mass_flow_in_kg_per_second=0.0, supply_temperature_in_celsius=tank_c, flow_is_clamped=False)
        return
    least_kg_s = pump.minimal_mass_flow_in_kg_per_second
    most_kg_s = pump.maximal_mass_flow_in_kg_per_second
    assert least_kg_s * (1 - 1e-12) <= point.mass_flow_in_kg_per_second <= most_kg_s * (1 + 1e-12)
    lift_in_kelvin = point.supply_temperature_in_celsius - tank_c
    heat_at_the_mean_w = area_m2 * CollectorPhysics.CURVE.heat_in_watt_per_m2(
        inlet_temperature_in_celsius=tank_c,
        inlet_to_mean_temperature_difference_in_kelvin=lift_in_kelvin / 2,
        ambient_temperature_in_celsius=air_c,
        irradiance_in_watt_per_m2=irradiance_w_m2,
    )
    assert CollectorPhysics.heat_in_watt(point, tank_c) == pytest.approx(heat_at_the_mean_w, rel=1e-9)
    if least_kg_s < point.mass_flow_in_kg_per_second < most_kg_s:
        assert not point.flow_is_clamped
        assert lift_in_kelvin == pytest.approx(pump.target_lift_in_kelvin, rel=1e-12)
    elif point.mass_flow_in_kg_per_second == least_kg_s:
        assert point.flow_is_clamped
        assert lift_in_kelvin < pump.target_lift_in_kelvin


@pytest.mark.base
def test_a_collector_without_heat_at_the_target_lift_still_gives_heat_at_the_minimum_flow() -> None:
    """A slower flow keeps the collector cooler, so it gives heat where the target lift's mean temperature gives none.

    With the sun just 9 K of stagnation above a 45 °C tank, the mean temperature 10 K above the inlet gives no heat,
    but the minimum flow lifts the water by a few kelvin and carries heat. A model that idled here would switch the
    pump on at 7 K and off again in the next step.
    """
    curve = CollectorPhysics.CURVE
    excess_k = 45.0 + 9.0 - 20.0
    irradiance_w_m2 = (3.2 * excess_k + 0.015 * excess_k**2) / 0.78
    at_target = curve.heat_in_watt_per_m2(
        inlet_temperature_in_celsius=45.0,
        inlet_to_mean_temperature_difference_in_kelvin=10.0,
        ambient_temperature_in_celsius=20.0,
        irradiance_in_watt_per_m2=irradiance_w_m2,
    )
    assert at_target == 0.0
    point = CollectorPhysics.point(
        inlet_temperature_in_celsius=45.0,
        ambient_temperature_in_celsius=20.0,
        irradiance_in_watt_per_m2=irradiance_w_m2,
        area_m2=4.0,
    )
    assert point.mass_flow_in_kg_per_second == pytest.approx(5.0 * 4.0 / 3600, rel=1e-12)
    assert 3.0 < point.supply_temperature_in_celsius - 45.0 < 9.0
    assert CollectorPhysics.heat_in_watt(point, 45.0) > 0.0


@pytest.mark.base
@pytest.mark.parametrize("irradiance_w_m2", [150.0, 400.0, 800.0, 1050.0])
def test_a_constant_flow_pump_runs_one_flow_and_its_lift_follows_the_heat(irradiance_w_m2: float) -> None:
    """A pump whose minimum equals its maximum runs that flow whatever the sun, and only its lift moves.

    A constant-flow pump that still ran the target lift's flow would ignore its own speed.
    """
    pump = FixedLiftPump(
        target_lift_in_kelvin=20.0,
        minimal_mass_flow_in_kg_per_second=30.0 * 6.0 / 3600,
        maximal_mass_flow_in_kg_per_second=30.0 * 6.0 / 3600,
    )
    point = CollectorPhysics.point(
        inlet_temperature_in_celsius=40.0,
        ambient_temperature_in_celsius=15.0,
        irradiance_in_watt_per_m2=irradiance_w_m2,
        area_m2=6.0,
        pump=pump,
    )
    assert point.mass_flow_in_kg_per_second == pytest.approx(30.0 * 6.0 / 3600, rel=1e-12)


@pytest.mark.base
def test_the_collector_s_pump_has_its_bounds_per_m2_and_hour_for_the_whole_field() -> None:
    """A collector whose pump bounds ignored its area, or the hour, would run a 12 m² field at a 1 m² field's flow.

    The flat-plate preset rates its pump at 5 to 40 kg/(m² h); on the 12 m² of three dwellings that is 0.0167 to
    0.1333 kg/s, at the configured 20 K target lift.
    """
    config = CollectorPhysics.CONFIG.resolve(SizingContext(number_of_apartments=3))
    collector = solar_thermal_system.SolarThermalSystem(
        config=config, my_simulation_parameters=sim.SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    )
    assert collector.area_m2 == 12.0
    assert collector.pump == FixedLiftPump(
        target_lift_in_kelvin=20.0,
        minimal_mass_flow_in_kg_per_second=5.0 * 12.0 / 3600.0,
        maximal_mass_flow_in_kg_per_second=40.0 * 12.0 / 3600.0,
    )


@pytest.mark.base
@pytest.mark.parametrize("tank_c", CollectorPhysics.TANKS_IN_CELSIUS)
@pytest.mark.parametrize("irradiance_w_m2", CollectorPhysics.IRRADIANCES_IN_WATT_PER_M2)
def test_the_pump_runs_every_collector_area_alike_per_m2(tank_c: float, irradiance_w_m2: float) -> None:
    """Four and twenty-four m² of the same collector run the same flow per m² and the same lift.

    The lift is what the sensor reads above the tank, so a pump whose flow depended on the field's size would switch
    a large field at other irradiances than a small one.
    """
    points = [
        CollectorPhysics.point(
            inlet_temperature_in_celsius=tank_c,
            ambient_temperature_in_celsius=10.0,
            irradiance_in_watt_per_m2=irradiance_w_m2,
            area_m2=area_m2,
        )
        for area_m2 in (4.0, 24.0)
    ]
    small, large = points
    assert large.supply_temperature_in_celsius == pytest.approx(small.supply_temperature_in_celsius, rel=1e-12, abs=1e-12)
    assert large.mass_flow_in_kg_per_second == pytest.approx(6 * small.mass_flow_in_kg_per_second, rel=1e-12)
    assert CollectorPhysics.heat_in_watt(large, tank_c) == pytest.approx(
        6 * CollectorPhysics.heat_in_watt(small, tank_c), rel=1e-12, abs=1e-9
    )


@pytest.mark.base
@pytest.mark.parametrize("tank_c", CollectorPhysics.TANKS_IN_CELSIUS)
@pytest.mark.parametrize("air_c", CollectorPhysics.AIRS_IN_CELSIUS)
def test_at_the_switch_on_difference_the_preset_pump_lifts_by_more_than_the_switch_off_difference(
    tank_c: float, air_c: float
) -> None:
    """A pump switched on at 7 K of stagnation must read more than 3 K at its outlet, or it switches off at once.

    With the sun just strong enough for the collector to stagnate 7 K above the tank, the preset pump's minimum flow
    lifts the water by more than the switch-off difference. With a faster minimum flow the lift there falls below it,
    and the pump switches on and off in alternate steps while the sun stands near the switch-on point.
    """
    controller = solar_thermal_system.SolarThermalSystemController
    excess_k = tank_c + controller.SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN - air_c
    curve = CollectorPhysics.CURVE
    irradiance_w_m2 = (
        curve.a_1_in_watt_per_m2_per_kelvin * excess_k + curve.a_2_in_watt_per_m2_per_kelvin_squared * excess_k**2
    ) / curve.eta_0
    point = CollectorPhysics.point(
        inlet_temperature_in_celsius=tank_c,
        ambient_temperature_in_celsius=air_c,
        irradiance_in_watt_per_m2=irradiance_w_m2,
        area_m2=4.0,
    )
    assert point.supply_temperature_in_celsius - tank_c > controller.SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN


class PumpTransitions:
    """The solar pump's on/off transitions: the saved pump state, the collector's sensor, the full-storage stop."""

    #: The switch-on difference, K.
    ON_IN_KELVIN = solar_thermal_system.SolarThermalSystemController.SWITCH_ON_TEMPERATURE_DIFFERENCE_IN_KELVIN

    #: The switch-off difference, K.
    OFF_IN_KELVIN = solar_thermal_system.SolarThermalSystemController.SWITCH_OFF_TEMPERATURE_DIFFERENCE_IN_KELVIN

    #: Each row: whether the pump ran in the previous step, how far the collector's sensor reads above the storage's
    #: start-of-step temperature in K, whether the full-storage stop holds, whether the pump runs, and what the row
    #: checks.
    TABLE = [
        (False, 20.0, False, True, "a standing pump with a hot collector and a storage to fill switches on"),
        (False, ON_IN_KELVIN + 0.01, False, True, "a standing pump switches on just above the switch-on difference"),
        (False, ON_IN_KELVIN, False, False, "a standing pump stays off at exactly the switch-on difference"),
        (False, 5.0, False, False, "a standing pump stays off between the two differences"),
        (False, OFF_IN_KELVIN + 0.01, False, False, "a standing pump stays off just above the switch-off difference"),
        (False, -10.0, False, False, "a standing pump stays off with a collector colder than the storage"),
        (False, 20.0, True, False, "a standing pump stays off while the full-storage stop holds"),
        (True, 20.0, False, True, "a running pump with a hot collector keeps running"),
        (True, 5.0, False, True, "a running pump keeps running between the two differences"),
        (True, OFF_IN_KELVIN + 0.01, False, True, "a running pump keeps running just above the switch-off difference"),
        (True, OFF_IN_KELVIN, False, False, "a running pump switches off at exactly the switch-off difference"),
        (True, 0.0, False, False, "a running pump switches off when the collector gives nothing"),
        (True, 20.0, True, False, "the full-storage stop stops a running pump"),
        (True, 5.0, True, False, "the full-storage stop stops a running pump inside the band too"),
    ]


class FullStorageStopTransitions:
    """The full-storage stop's transitions: the saved stop, the storage's start-of-step temperature, the stop."""

    #: The maximum storage temperature above which the stop sets, °C.
    MAXIMUM_IN_CELSIUS = solar_thermal_system.SolarThermalSystemController.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS

    #: The temperature at and below which a holding stop releases, °C: 3 K below the maximum.
    RESTART_IN_CELSIUS = (
        MAXIMUM_IN_CELSIUS
        - solar_thermal_system.SolarThermalSystemController.FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN
    )

    #: Each row: whether the stop held in the previous step, the storage's start-of-step temperature in °C, whether
    #: the stop holds, and what the row checks.
    TABLE = [
        (False, 44.0, False, "a storage well below the maximum sets no stop"),
        (False, 60.5, False, "a storage above the backups' 60 °C warm-water aim sets no stop"),
        (False, MAXIMUM_IN_CELSIUS, False, "exactly at the maximum the stop does not set (it sets strictly above)"),
        (False, MAXIMUM_IN_CELSIUS + 0.025, True, "a storage just above the maximum sets the stop"),
        (False, 78.5, False, "inside the restart band a released stop stays released"),
        (True, 81.0, True, "a holding stop holds above the maximum"),
        (True, MAXIMUM_IN_CELSIUS, True, "a holding stop holds at the maximum"),
        (True, 78.5, True, "a holding stop holds inside the restart band"),
        (True, RESTART_IN_CELSIUS + 1e-6, True, "a holding stop holds just above the restart temperature"),
        (True, RESTART_IN_CELSIUS, False, "a holding stop releases exactly 3 K below the maximum, at 77 °C"),
        (True, 59.0, False, "a holding stop releases well below the maximum"),
    ]


@pytest.mark.base
def test_the_switch_off_difference_lies_below_the_switch_on_difference_as_differential_controllers_set_them() -> None:
    """Catches a hysteresis band of the wrong width between the pump's switch-on and switch-off differences.

    A band of zero lets a pump whose start cools the collector's sensor switch off and on every step. Differential
    solar controllers switch on at about 5 to 8 K and off at about 2 to 4 K.
    """
    assert 5.0 <= PumpTransitions.ON_IN_KELVIN <= 8.0
    assert 2.0 <= PumpTransitions.OFF_IN_KELVIN <= 4.0
    assert PumpTransitions.OFF_IN_KELVIN < PumpTransitions.ON_IN_KELVIN


@pytest.mark.base
def test_the_full_storage_stop_restarts_three_to_five_kelvin_below_the_maximum() -> None:
    """Catches a full-storage restart band outside what solar controllers use.

    Without a band a storage that started a step just above the maximum cools below it by the next step's standby loss,
    and the pump switches every second step; a band wider than 5 K leaves solar heat on the roof that real controllers
    take.
    """
    band_in_kelvin = solar_thermal_system.SolarThermalSystemController.FULL_STORAGE_RESTART_BELOW_MAXIMUM_IN_KELVIN
    assert 3.0 <= band_in_kelvin <= 5.0


class BackupAims:
    """The highest tank temperature each backup generator's controller charges the hot-water tank to, in °C."""

    @staticmethod
    def built_controllers() -> Dict[str, Any]:
        """Return the boiler's, the district-heating substation's and the electric heater's controllers, built.

        Each is built from its first preset; the boiler's sizable power band is set by hand to 1 to 10 kW, on which
        its warm-water aim does not depend. The other two controllers have no sizable field.

        Returns:
            The controllers by name.
        """
        parameters = Parameters.one_day(900)
        boiler = generic_boiler.GenericBoilerControllerConfig.preset_modulating("BoilerController")
        boiler.minimal_thermal_power_in_watt = 1000.0
        boiler.maximal_thermal_power_in_watt = 10000.0
        district = generic_district_heating.DistrictHeatingControllerConfig.preset_standard("DistrictController")
        electric = generic_electric_heating.ElectricHeatingControllerConfig.preset_standard("ElectricController")
        return {
            "boiler": generic_boiler.GenericBoilerController(my_simulation_parameters=parameters, config=boiler),
            "district heating": generic_district_heating.DistrictHeatingController(
                my_simulation_parameters=parameters, config=district
            ),
            "electric heater": generic_electric_heating.ElectricHeatingController(
                my_simulation_parameters=parameters, config=electric
            ),
        }

    @staticmethod
    def heat_pump_raised_aim_in_celsius() -> float:
        """Return the tank temperature at which the heat pump ends a charge under the energy manager's full raise, °C.

        The heat pump's controller ends a hot-water charge its charge end (2 K) below its set temperature, and the
        energy manager raises that set temperature by its hot-water offset (10 K) while PV surplus is available: 68 °C.

        Returns:
            The raised aim, in °C.
        """
        config = MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard("HeatPumpControllerDHW")
        energy_manager = EMSConfig.preset_optimize_own_consumption("EMS")
        raise_in_kelvin = energy_manager.domestic_hot_water_storage_temperature_offset_value
        return (
            config.t_max_dhw_storage_in_celsius
            + raise_in_kelvin
            - config.charge_end_below_target_in_kelvin
        )


@pytest.mark.base
def test_the_solar_maximum_lies_above_the_warm_water_aim_of_every_backup() -> None:
    """Catches a solar maximum storage temperature at or below a temperature a backup generator charges the tank to.

    The boiler, the district-heating substation and the electric heater charge to their 60 °C warm-water aim, the
    heat pump to 58 °C and, under the energy manager's raise, to 68 °C. With the solar maximum at or below such an
    aim, the full-storage stop would hold the pump on sunny days while that backup supplies heat the sun could have
    delivered. The maximum stays within the 60 to 80 °C real solar controllers are set to.
    """
    maximum_in_celsius = solar_thermal_system.SolarThermalSystemController.MAXIMUM_STORAGE_TEMPERATURE_IN_CELSIUS
    aims_in_celsius = {
        name: controller.warm_water_temperature_aim_in_celsius
        for name, controller in BackupAims.built_controllers().items()
    }
    aims_in_celsius["heat pump, raised"] = BackupAims.heat_pump_raised_aim_in_celsius()
    assert aims_in_celsius == {
        "boiler": 60.0,
        "district heating": 60.0,
        "electric heater": 60.0,
        "heat pump, raised": 68.0,
    }
    assert max(aims_in_celsius.values()) < maximum_in_celsius <= 80.0


@pytest.mark.base
@pytest.mark.parametrize(
    ("pump_ran_in_previous_step", "collector_above_storage_in_kelvin", "full_storage_stop_holds", "expected"),
    [row[:4] for row in PumpTransitions.TABLE],
    ids=[row[4] for row in PumpTransitions.TABLE],
)
def test_the_pump_switches_on_above_seven_kelvin_off_at_three_and_stands_while_the_storage_is_full(
    pump_ran_in_previous_step: bool,
    collector_above_storage_in_kelvin: float,
    full_storage_stop_holds: bool,
    expected: bool,
) -> None:
    """Every transition of the pump's temperature-difference hysteresis, with the edges of both and the stop.

    A pump without the band would switch on and off every step; a band with the wrong edges would run the pump on too
    little heat or keep it standing on enough; a pump that ignored the stop would heat a full storage further.
    """
    storage_c = 45.0
    assert (
        solar_thermal_system.SolarThermalSystemController.pump_runs(
            collector_sensor_temperature_in_celsius=storage_c + collector_above_storage_in_kelvin,
            storage_temperature_at_start_in_celsius=storage_c,
            full_storage_stop_holds=full_storage_stop_holds,
            pump_ran_in_previous_step=pump_ran_in_previous_step,
        )
        is expected
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("stop_held_in_previous_step", "storage_temperature_at_start_in_celsius", "expected"),
    [row[:3] for row in FullStorageStopTransitions.TABLE],
    ids=[row[3] for row in FullStorageStopTransitions.TABLE],
)
def test_the_full_storage_stop_sets_above_the_maximum_and_releases_three_kelvin_below_it(
    stop_held_in_previous_step: bool, storage_temperature_at_start_in_celsius: float, expected: bool
) -> None:
    """Every transition of the full-storage stop, with the edges at the maximum and at the restart temperature.

    A stop without its band would release as soon as the storage cools below the maximum and switch the pump every
    second step; a stop that set at the maximum itself would stop a storage the sun has just brought to its maximum;
    a stop that set at the backups' 60 °C aim would leave the sun's heat above 60 °C on the roof.
    """
    assert (
        solar_thermal_system.SolarThermalSystemController.full_storage_stop_holds(
            storage_temperature_at_start_in_celsius=storage_temperature_at_start_in_celsius,
            stop_held_in_previous_step=stop_held_in_previous_step,
        )
        is expected
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("heat_in_watt", "pump_runs", "expected_mass_flow_in_kg_per_second", "expected_supply_temperature_in_celsius"),
    [(2090.0, True, 0.025, 60.0), (2090.0, False, 0.0, 40.0), (0.0, True, 0.0, 40.0)],
)
def test_the_collector_circuit_carries_its_heat_only_while_the_pump_runs_and_the_collector_has_heat(
    heat_in_watt: float,
    pump_runs: bool,
    expected_mass_flow_in_kg_per_second: float,
    expected_supply_temperature_in_celsius: float,
) -> None:
    """A circuit that moved water without heat, or with the pump off, would cool the tank or book phantom heat."""
    if heat_in_watt > 0:
        point = PumpOperatingPoint(mass_flow_in_kg_per_second=0.025, supply_temperature_in_celsius=60.0, flow_is_clamped=False)
    else:
        point = PumpOperatingPoint(mass_flow_in_kg_per_second=0.0, supply_temperature_in_celsius=40.0, flow_is_clamped=False)
    circuit = solar_thermal_system.SolarThermalSystem.collector_circuit(
        operating_point=point, inlet_temperature_in_celsius=40.0, pump_runs=pump_runs
    )
    assert circuit.mass_flow_kg_per_s == pytest.approx(expected_mass_flow_in_kg_per_second, rel=1e-12)
    assert circuit.t_supply_c == expected_supply_temperature_in_celsius
    assert circuit.power_w == pytest.approx(heat_in_watt if pump_runs else 0.0, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize(
    ("pump_runs", "is_old_pump", "expected_power_in_watt"),
    [(True, False, 10.0), (True, True, 35.0), (False, False, 0.0), (False, True, 0.0)],
)
def test_the_solar_pump_draws_its_kind_s_power_only_while_it_runs(
    pump_runs: bool, is_old_pump: bool, expected_power_in_watt: float
) -> None:
    """A pump that drew power while standing, or the wrong kind's power, would misstate the system's electricity."""
    assert (
        solar_thermal_system.SolarThermalSystem.pump_power_in_watt(pump_runs=pump_runs, is_old_pump=is_old_pump)
        == expected_power_in_watt
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("pump_target_lift_in_kelvin", 0.0),
        ("pump_target_lift_in_kelvin", -5.0),
        ("pump_target_lift_in_kelvin", float("nan")),
        ("pump_minimum_mass_flow_in_kg_per_hour_per_m2", 0.0),
        ("pump_minimum_mass_flow_in_kg_per_hour_per_m2", float("inf")),
        ("pump_maximum_mass_flow_in_kg_per_hour_per_m2", 4.0),
        ("pump_maximum_mass_flow_in_kg_per_hour_per_m2", float("nan")),
    ],
)
def test_a_solar_pump_without_a_finite_positive_lift_and_flow_range_is_refused(field_name: str, value: float) -> None:
    """A lift of 0 K divides the flow by zero, a minimum flow of 0 gives an infinite lift, a lower maximum no range."""
    fields: Dict[str, Any] = {field_name: value}
    with pytest.raises(ValueError, match=field_name):
        solar_thermal_system.SolarThermalSystemConfig(component_id=ComponentID(name="SolarThermalSystem"), **fields)


@pytest.mark.base
def test_the_flat_plate_preset_has_a_valid_pump_and_a_constant_flow_pump_is_accepted() -> None:
    """The refusals must not refuse the preset's own pump, nor a constant-flow pump whose minimum equals its maximum."""
    config = solar_thermal_system.SolarThermalSystemConfig.preset_flat_plate("SolarThermalSystem")
    assert config.pump_target_lift_in_kelvin == 20.0
    assert config.pump_minimum_mass_flow_in_kg_per_hour_per_m2 == 5.0
    assert config.pump_maximum_mass_flow_in_kg_per_hour_per_m2 == 40.0
    solar_thermal_system.SolarThermalSystemConfig(
        component_id=ComponentID(name="SolarThermalSystem"),
        pump_minimum_mass_flow_in_kg_per_hour_per_m2=30.0,
        pump_maximum_mass_flow_in_kg_per_hour_per_m2=30.0,
    )


class SolarControllerSteps:
    """Drive the solar controller through passes and steps as the simulator does, on the shared rig."""

    #: A step well into the run.
    TIMESTEP: int = 100

    #: A collector sensor between the switch-off and the switch-on difference above the storage, K.
    BETWEEN_THE_DIFFERENCES_IN_KELVIN: float = 5.0

    #: A collector sensor above the switch-on difference, K.
    ABOVE_THE_ON_DIFFERENCE_IN_KELVIN: float = 9.0

    #: A collector sensor below the switch-off difference, K.
    BELOW_THE_OFF_DIFFERENCE_IN_KELVIN: float = 2.0

    @staticmethod
    def values(
        collector_above_storage_in_kelvin: float, start_c: float = 45.0, actual_ratio: float = 1.0
    ) -> Dict[str, float]:
        """Return the controller's inputs: the tank's start temperature, the collector's sensor, the pump's actual ratio.

        The actual ratio is the part-load ratio the collector reports its pump ran with; a forced pass reads it.
        """
        return SolarControllerRig.values(
            start_c=start_c, actual_ratio=actual_ratio, collector_above_storage_in_kelvin=collector_above_storage_in_kelvin
        )

    @classmethod
    def signal(
        cls,
        controller: Any,
        stsv: Any,
        fakes: Dict[str, Any],
        values: Dict[str, float],
        force_convergence: bool = False,
    ) -> float:
        """Run one pass from the saved state, as the simulator does, and return the on/off signal it publishes."""
        controller.i_restore_state()
        outputs = AllInputsRig.step(
            controller, stsv, fakes, values, timestep=cls.TIMESTEP, force_convergence=force_convergence
        )
        return outputs[controller.ControlSignalToSolarThermalSystem]


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900])
def test_the_pump_state_of_the_previous_step_chooses_the_difference_and_no_pass_remembers_another(
    seconds_per_timestep: int,
) -> None:
    """Within a step the decision reads only the pass's inputs and the saved state; the state moves only between steps.

    A controller that kept a pass's decision would hold a pump on in the next pass of the same step, which is the pass
    memory that makes a converged step depend on the passes before it; one that saved nothing between steps would have
    no hysteresis at all.
    """
    steps = SolarControllerSteps
    controller, stsv, fakes = SolarControllerRig.build(seconds_per_timestep)
    controller.i_save_state()  # the first step starts with the pump standing
    assert steps.signal(controller, stsv, fakes, steps.values(steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN)) == 1
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)) == 0
    assert steps.signal(controller, stsv, fakes, steps.values(steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN)) == 1

    controller.i_save_state()  # the step ended with the pump running
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)) == 1
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BELOW_THE_OFF_DIFFERENCE_IN_KELVIN)) == 0
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)) == 1

    controller.i_save_state()  # the step ended with the pump running again
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BELOW_THE_OFF_DIFFERENCE_IN_KELVIN)) == 0
    controller.i_save_state()  # the step ended with the pump standing
    assert steps.signal(controller, stsv, fakes, steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)) == 0


@pytest.mark.base
@pytest.mark.parametrize("pump_ran_in_previous_step", [False, True])
def test_a_pass_after_other_passes_equals_the_first_pass_of_the_step(pump_ran_in_previous_step: bool) -> None:
    """Passes with other inputs before a pass change nothing it publishes: the generic iteration-safety check.

    A controller that wrote its state in a pass would publish a different signal after the other passes than in a
    fresh step.
    """
    steps = SolarControllerSteps
    fresh, fresh_stsv, fresh_fakes = SolarControllerRig.build(900)
    iterated, iterated_stsv, iterated_fakes = SolarControllerRig.build(900)
    for controller, stsv, fakes in ((fresh, fresh_stsv, fresh_fakes), (iterated, iterated_stsv, iterated_fakes)):
        controller.i_save_state()
        difference = steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN if pump_ran_in_previous_step else 0.0
        steps.signal(controller, stsv, fakes, steps.values(difference))
        controller.i_save_state()
    for difference in (steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN, 0.0, steps.BELOW_THE_OFF_DIFFERENCE_IN_KELVIN):
        steps.signal(iterated, iterated_stsv, iterated_fakes, steps.values(difference))
    full_storage = steps.values(steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN, start_c=81.0)
    steps.signal(iterated, iterated_stsv, iterated_fakes, full_storage)
    final = steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)
    expected = steps.signal(fresh, fresh_stsv, fresh_fakes, final)
    assert steps.signal(iterated, iterated_stsv, iterated_fakes, final) == expected
    assert expected == (1 if pump_ran_in_previous_step else 0)


@pytest.mark.base
@pytest.mark.parametrize(("actual_ratio", "expected_signal"), [(0.42, 1), (1.0, 1), (0.0, 0)])
def test_under_force_convergence_the_pump_stays_as_the_collector_reports_it_ran(
    actual_ratio: float, expected_signal: int
) -> None:
    """A forced pass keeps the pump as the collector reports it ran, whatever the sensors; the next step starts from it.

    The pump's signal and the part-load ratio the controller holds under ``force_convergence`` (the actual ratio) then
    describe the same run. A forced pass that decided on the sensors again could switch on a pump whose held ratio is
    0; one that kept an earlier pass's decision would remember a pass.
    """
    steps = SolarControllerSteps
    controller, stsv, fakes = SolarControllerRig.build(900)
    controller.i_save_state()
    if expected_signal:
        difference = steps.BELOW_THE_OFF_DIFFERENCE_IN_KELVIN
    else:
        difference = steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN
    forced_values = steps.values(difference, actual_ratio=actual_ratio)
    assert steps.signal(controller, stsv, fakes, forced_values, force_convergence=True) == expected_signal
    controller.i_save_state()
    between = steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)
    assert steps.signal(controller, stsv, fakes, between) == expected_signal


@pytest.mark.base
def test_an_unforced_pass_that_signals_the_pump_on_at_ratio_zero_saves_it_as_standing() -> None:
    """The saved pump state is what the collector will report: a pump signalled on at ratio 0 did not run.

    Above the part-load threshold a step that ends the storage at the maximum gets ratio 0 with the signal on; the
    collector idles and reports an actual ratio of 0. A controller that saved the signal instead would choose the
    switch-off difference in the next step as if the pump had run, unlike a forced pass on the same step.
    """
    steps = SolarControllerSteps
    controller, stsv, fakes = SolarControllerRig.build(900)
    controller.i_save_state()
    at_the_maximum = SolarControllerRig.values(
        start_c=78.0,
        end_c=80.03,
        actual_ratio=0.0,
        collector_above_storage_in_kelvin=steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN,
    )
    controller.i_restore_state()
    outputs = AllInputsRig.step(controller, stsv, fakes, at_the_maximum, timestep=steps.TIMESTEP)
    assert outputs[controller.ControlSignalToSolarThermalSystem] == 1
    assert outputs[controller.RequestedPartLoadRatio] == 0.0
    controller.i_save_state()
    assert controller.previous_state.pump_runs is False
    between = steps.values(steps.BETWEEN_THE_DIFFERENCES_IN_KELVIN)
    assert steps.signal(controller, stsv, fakes, between) == 0


@pytest.mark.base
def test_a_pump_the_full_storage_stopped_runs_again_only_three_kelvin_below_the_maximum() -> None:
    """The full-storage stop holds across steps until the storage starts a step 3 K below the maximum, at 77 °C.

    A controller without the band would restart the pump as soon as the storage cools below 80 °C and switch it every
    second step; one that kept the stop in a pass instead of the saved state would hold or release it within a step.
    """
    steps = SolarControllerSteps
    controller, stsv, fakes = SolarControllerRig.build(900)

    def signal(start_c: float) -> float:
        """Run one pass of a sunny step whose storage started at ``start_c`` and return the pump signal."""
        sunny = steps.values(steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN, start_c=start_c)
        return steps.signal(controller, stsv, fakes, sunny)

    controller.i_save_state()  # the first step starts with the pump standing and the stop released
    assert signal(60.5) == 1  # above the backups' 60 °C aim, below the maximum, no stop: the pump switches on
    controller.i_save_state()
    assert signal(79.5) == 1  # just below the maximum: the pump keeps running
    controller.i_save_state()
    assert signal(80.025) == 0  # the storage started just above the maximum: the stop sets
    controller.i_save_state()
    assert signal(79.5) == 0  # the storage cooled below the maximum, but not 3 K below it: the stop holds
    controller.i_save_state()
    assert signal(77.5) == 0
    controller.i_save_state()
    assert signal(77.0) == 1  # exactly 3 K below the maximum: the stop releases and the sunny collector starts the pump
    controller.i_save_state()
    assert signal(78.5) == 1  # inside the band, the released stop stays released


@pytest.mark.base
@pytest.mark.parametrize(("start_c", "expected_signal"), [(61.0, 1), (81.0, 0)])
def test_a_storage_above_the_backups_aim_keeps_the_sunny_pump_on_until_the_maximum(
    start_c: float, expected_signal: int
) -> None:
    """A storage at 61 °C, above the backups' 60 °C aim, keeps a sunny pump on; one above the 80 °C maximum stops it.

    A controller that stopped at the backups' aim would leave the heat on the roof; one without a maximum would heat
    the storage without bound.
    """
    steps = SolarControllerSteps
    controller, stsv, fakes = SolarControllerRig.build(900)
    controller.i_save_state()
    values = steps.values(steps.ABOVE_THE_ON_DIFFERENCE_IN_KELVIN, start_c=start_c)
    assert steps.signal(controller, stsv, fakes, values) == expected_signal


@pytest.mark.base
def test_the_collector_sensor_reads_the_stagnation_after_a_standing_step_and_the_outlet_after_a_run() -> None:
    """The sensor the controller switches on comes from the start of the step and the pump's previous step.

    After a step the pump stood the sensor reads the stagnation temperature, which neither the tank nor the pump
    signal moves. After a step it ran the sensor reads the outlet at the tank's start-of-step temperature: the start
    plus the lift there, not the step mean's supply. A sensor that read the step mean would let the pump's own heat
    switch the pump off in the step it switches on; one that read the pump signal would switch on its own output.
    """
    sensor_name = solar_thermal_system.SolarThermalSystem.CollectorSensorTemperatureAtStartOfStepInCelsius
    supply_name = solar_thermal_system.SolarThermalSystem.WaterTemperatureOutput
    stood = CollectorRig.charge(900, requested_ratio=1.0, inlet_at_start_of_step_c=40.0)
    stood_off = CollectorRig.charge(900, requested_ratio=0.0, control_signal=0.0, inlet_at_start_of_step_c=55.0)
    assert stood[sensor_name] == stood_off[sensor_name] > 80.0

    ran = CollectorRig.charge(900, requested_ratio=1.0, inlet_at_start_of_step_c=40.0, pump_ran_in_previous_step=True)
    ran_off = CollectorRig.charge(
        900, requested_ratio=0.0, control_signal=0.0, inlet_at_start_of_step_c=40.0, pump_ran_in_previous_step=True
    )
    assert ran[sensor_name] == ran_off[sensor_name] == 60.0  # the 20 K target lift on the 40 °C start
    assert ran[supply_name] == 65.0  # the circuit itself runs at the 45 °C step mean
    cooler = CollectorRig.charge(900, requested_ratio=1.0, inlet_at_start_of_step_c=35.0, pump_ran_in_previous_step=True)
    assert cooler[sensor_name] == 55.0
