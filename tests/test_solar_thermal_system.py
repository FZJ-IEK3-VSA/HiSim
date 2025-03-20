"""Test for generic pv system."""

import datetime
import pandas as pd
import pytest
from oemof.thermal.solar_thermal_collector import flat_plate_precalc
from tests import functions_for_testing as fft
from hisim import sim_repository
from hisim import component
from hisim.components import weather
from hisim.components import solar_thermal_system
from hisim import simulator as sim
from hisim import log


@pytest.mark.base
def test_solar_thermal_system():
    """Test solar thermal system."""
    # Inputs
    seconds_per_timestep = 60
    power_in_watt = 10 * 1e3

    repo = sim_repository.SimRepository()
    mysim: sim.SimulationParameters = sim.SimulationParameters.full_year(
        year=2021, seconds_per_timestep=seconds_per_timestep
    )

    # Configure weather
    my_weather_config = weather.WeatherConfig.get_default(
        location_entry=weather.LocationEnum.AACHEN
    )
    my_weather = weather.Weather(
        config=my_weather_config, my_simulation_parameters=mysim
    )
    my_weather.set_sim_repo(repo)
    my_weather.i_prepare_simulation()

    # Configure solar thermal
    my_sts_config = solar_thermal_system.SolarThermalSystemConfig.get_default_solar_thermal_system()
    my_sts_config.power_in_watt = power_in_watt
    my_sts = solar_thermal_system.SolarThermalSystem(
        config=my_sts_config, my_simulation_parameters=mysim
    )
    my_sts.set_sim_repo(repo)
    my_sts.i_prepare_simulation()

    # Outputs
    number_of_outputs = fft.get_number_of_outputs([my_weather, my_sts])
    stsv: component.SingleTimeStepValues = component.SingleTimeStepValues(
        number_of_outputs
    )

    my_sts.t_out_channel.source_output = my_weather.air_temperature_output
    my_sts.dhi_channel.source_output = my_weather.dhi_output
    my_sts.ghi_channel.source_output = my_weather.ghi_output

    # Simulate
    fft.add_global_index_of_components([my_weather, my_sts])
    timestep = 12 * 60 + 60 * 24 * 183  # 3rd July at noon
    my_weather.i_simulate(timestep, stsv, False)
    my_sts.i_simulate(timestep, stsv, False)
    log.information(
        "heat power output [W]: "
        + str(stsv.values[my_sts.thermal_power_w_output_channel.global_index])
    )

    assert (
        pytest.approx(
            stsv.values[my_sts.thermal_power_w_output_channel.global_index]
        )
        == 1108.9757922481404
    )


@pytest.mark.base
def test_precalc():
    """Test solar thermal system precalc function from oemof."""
    azimuth = (180.0,)
    tilt: float = 30.0
    eta_0: float = 0.78
    a_1_w_m2_k: float = 3.2  # W/(m2*K)
    a_2_w_m2_k: float = 0.015  # W/(m2*K2)
    coordinates = component.Coordinates(latitude=50.78, longitude=6.08)

    temperature_collector_inlet_deg_c = 55
    delta_temperature_n_k = 10

    global_horizontal_irradiance_w_m2 = 0
    diffuse_horizontal_irradiance_w_m2 = 0
    ambient_air_temperature_deg_c = 0
    timestep = 0
    time_ind = datetime.datetime(2021, 1, 1) + datetime.timedelta(
        0, 60 * timestep
    )

    precalc_data = flat_plate_precalc(
        lat=coordinates.latitude,
        long=coordinates.longitude,
        collector_tilt=tilt,
        collector_azimuth=azimuth,
        eta_0=eta_0,  # optical efficiency of the collector
        a_1=a_1_w_m2_k,  # thermal loss parameter 1
        a_2=a_2_w_m2_k,  # thermal loss parameter 2
        temp_collector_inlet=temperature_collector_inlet_deg_c,  # collectors inlet temperature
        delta_temp_n=delta_temperature_n_k,  # temperature difference between collector inlet and mean temperature
        irradiance_global=pd.Series(
            global_horizontal_irradiance_w_m2, index=[time_ind]
        ),
        irradiance_diffuse=pd.Series(
            diffuse_horizontal_irradiance_w_m2, index=[time_ind]
        ),
        temp_amb=pd.Series(ambient_air_temperature_deg_c, index=[time_ind]),
    )

    assert precalc_data["collectors_heat"].iloc[0] == 0
