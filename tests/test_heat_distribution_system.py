"""Test for heat distribution system."""
#  clean
from typing import Tuple
import pytest
from hisim import component as cp
from hisim.components import heat_distribution_system
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum
from tests import functions_for_testing as fft


@pytest.mark.base
def test_hds():
    """Test for heat distribution system."""

    # theoretical building thermal demands to test
    theoretical_thermal_building_demands_in_watt = [0, -10, +10, -8000, +3000, +1000000]
    # water input temperatures to test
    water_input_temperatures_in_celsius = [10, 15, 20, 25, 30, 40, 50]

    for demand in theoretical_thermal_building_demands_in_watt:
        for water_input_temperature in water_input_temperatures_in_celsius:

            # for each demand and water input temperature simulate hds and return outputs
            (
                input_water_temperature_in_celsius,
                residence_temperature_in_celsius,
                theoretical_building_demand_in_watt,
                calculated_water_output_temperature_in_celsius,
                water_output_temperature_after_heat_exchange,
                effective_thermal_power_delivered_in_watt,
            ) = simulate_and_calculate_hds_outputs_for_a_given_theoretical_heating_demand_from_building(
                theoretical_building_demand_in_watt=demand,
                water_input_temperature_in_celsius=water_input_temperature,
            )

            # test the case where the building needs heating
            if theoretical_building_demand_in_watt > 0:

                # if water input temp is high enough, heat exchange is possible but water output temp can not get lower than residence temp
                if (
                    input_water_temperature_in_celsius
                    > residence_temperature_in_celsius
                ):

                    assert water_output_temperature_after_heat_exchange == max(
                        calculated_water_output_temperature_in_celsius,
                        residence_temperature_in_celsius,
                    )

                # if water input temp is too low no heat exchange
                elif (
                    input_water_temperature_in_celsius
                    < residence_temperature_in_celsius
                ):

                    assert (
                        water_output_temperature_after_heat_exchange
                        == input_water_temperature_in_celsius
                    )
                    assert effective_thermal_power_delivered_in_watt == 0

            # test the case where the building needs cooling
            elif theoretical_building_demand_in_watt < 0:
                # if water input temp is low enough, heat exchange is possible but water output temp can not get higher than residence temp
                if (
                    input_water_temperature_in_celsius
                    < residence_temperature_in_celsius
                ):

                    assert water_output_temperature_after_heat_exchange == min(
                        calculated_water_output_temperature_in_celsius,
                        residence_temperature_in_celsius,
                    )

                # if water input temp is too high no heat exchange
                elif (
                    input_water_temperature_in_celsius
                    > residence_temperature_in_celsius
                ):

                    assert (
                        water_output_temperature_after_heat_exchange
                        == input_water_temperature_in_celsius
                    )
                    assert effective_thermal_power_delivered_in_watt == 0

            elif theoretical_building_demand_in_watt == 0:
                assert (
                    water_output_temperature_after_heat_exchange
                    == input_water_temperature_in_celsius
                )
                assert effective_thermal_power_delivered_in_watt == 0


def simulate_and_calculate_hds_outputs_for_a_given_theoretical_heating_demand_from_building(
    theoretical_building_demand_in_watt: float,
    water_input_temperature_in_celsius: float,
) -> Tuple[float, float, float, float, float, float]:
    """Simulate and calculate hds outputs."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Set Heat Distribution System
    hds_name = "HeatDistributionSystem"
    heating_system = heat_distribution_system.HeatingSystemType.FLOORHEATING

    # ===================================================================================================================
    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND, entry=9000
    )

    SingletonSimRepository().set_entry(
        key=SingletonDictKeyEnum.HEATINGSYSTEM, entry=heating_system
    )

    # Build Heat Distribution System
    my_heat_distribution_system_config = (
        heat_distribution_system.HeatDistributionConfig(
            name=hds_name,
        )
    )

    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    water_temperature_input_from_water_storage = cp.ComponentOutput(
        "FakeWaterTemperatureInput",
        "WaterTemperatureInput",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
    )
    residence_temperature_indoor_air = cp.ComponentOutput(
        "FakeResidenceTemperatureInput",
        "ResidenceTemperatureInput",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
    )
    theoretical_thermal_building_demand = cp.ComponentOutput(
        "FakeTheoreticalThermalDemand",
        "TheoreticalThermalDemand",
        lt.LoadTypes.HEATING,
        lt.Units.WATT,
    )

    state_from_hds_controller = cp.ComponentOutput(
        "FakeStateController", "StateController", lt.LoadTypes.ANY, lt.Units.ANY
    )

    # connect hds inputs to fake outputs
    my_heat_distribution_system.water_temperature_input_channel.source_output = (
        water_temperature_input_from_water_storage
    )
    my_heat_distribution_system.residence_temperature_input_channel.source_output = (
        residence_temperature_indoor_air
    )
    my_heat_distribution_system.theoretical_thermal_building_demand_channel.source_output = (
        theoretical_thermal_building_demand
    )
    my_heat_distribution_system.state_channel.source_output = state_from_hds_controller

    number_of_outputs = fft.get_number_of_outputs(
        [
            water_temperature_input_from_water_storage,
            residence_temperature_indoor_air,
            theoretical_thermal_building_demand,
            state_from_hds_controller,
            my_heat_distribution_system,
        ]
    )

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            water_temperature_input_from_water_storage,
            residence_temperature_indoor_air,
            theoretical_thermal_building_demand,
            state_from_hds_controller,
            my_heat_distribution_system,
        ]
    )

    stsv.values[
        water_temperature_input_from_water_storage.global_index
    ] = water_input_temperature_in_celsius

    stsv.values[state_from_hds_controller.global_index] = 1
    stsv.values[residence_temperature_indoor_air.global_index] = 19
    timestep = 300
    water_mass_flow_of_hds_in_kg_per_second = SingletonSimRepository().get_entry(
        key=SingletonDictKeyEnum.WATERMASSFLOWRATEOFHEATINGDISTRIBUTIONSYSTEM
    )
    # Simulate

    stsv.values[
        theoretical_thermal_building_demand.global_index
    ] = theoretical_building_demand_in_watt

    my_heat_distribution_system.i_restore_state()
    my_heat_distribution_system.i_simulate(timestep, stsv, False)

    # if in hds component the state values are set as output values,
    # then here the water_temp_output and the thermal_power_delivered should be set, not the stsv.values[4] and [5]
    water_output_temperature_in_celsius_from_simulation = (
        my_heat_distribution_system.water_temperature_output_in_celsius
    )  # stsv.values[4]
    effective_thermal_power_delivered_in_watt = (
        my_heat_distribution_system.thermal_power_delivered_in_watt
    )  # stsv.values[5]

    # Test if water output of hds is correct for different theoretical thermal demands from building
    calculated_water_output_temperature_in_celsius = float(
        stsv.values[water_temperature_input_from_water_storage.global_index]
        - stsv.values[theoretical_thermal_building_demand.global_index]
        / (
            water_mass_flow_of_hds_in_kg_per_second
            * my_heat_distribution_system.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        )
    )
    print(calculated_water_output_temperature_in_celsius)
    log.information(
        "water input temperature in celsius "
        + str(stsv.values[water_temperature_input_from_water_storage.global_index])
    )
    log.information(
        "residence temperature in celsius "
        + str(stsv.values[residence_temperature_indoor_air.global_index])
    )
    log.information(
        "theoretical thermal building demand in watt "
        + str(theoretical_building_demand_in_watt)
    )
    log.information(
        "theoretical water output temperature after heat exchange with building "
        + str(calculated_water_output_temperature_in_celsius)
    )
    log.information(
        "real water output temperature after heat exchange with building "
        + str(water_output_temperature_in_celsius_from_simulation)
    )
    log.information(
        "real thermal output delivered from hds "
        + str(effective_thermal_power_delivered_in_watt)
        + "\n"
    )
    return (
        stsv.values[water_temperature_input_from_water_storage.global_index],
        stsv.values[residence_temperature_indoor_air.global_index],
        stsv.values[theoretical_thermal_building_demand.global_index],
        calculated_water_output_temperature_in_celsius,
        water_output_temperature_in_celsius_from_simulation,
        effective_thermal_power_delivered_in_watt,
    )
