"""Test for heat distribution system."""

from hisim import component as cp
from hisim.components import heat_distribution_system
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


def test_hds():
    """Test for heat distribution system."""

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Set Heat Distribution System
    hds_name = "HeatDistributionSystem"
    water_temperature_in_distribution_system_in_celsius = 50
    heating_system = "FloorHeating"

    # ===================================================================================================================
    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig(
        name=hds_name,
        water_temperature_in_distribution_system_in_celsius=water_temperature_in_distribution_system_in_celsius,
        heating_system=heating_system,
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

    theoretical_thermal_building_demand = cp.ComponentOutput(
        "FakeTheoreticalThermalDemand",
        "TheoreticalThermalDemand",
        lt.LoadTypes.HEATING,
        lt.Units.WATT,
    )

    maximal_thermal_building_demand = cp.ComponentOutput(
        "FakeMaximalThermalDemand",
        "MaximalThermalDemand",
        lt.LoadTypes.HEATING,
        lt.Units.WATT,
    )

    state_from_hds_controller = cp.ComponentOutput(
        "FakeStateController", "StateController", lt.LoadTypes.ANY, lt.Units.ANY
    )

    my_heat_distribution_system.water_temperature_input_channel.source_output = (
        water_temperature_input_from_water_storage
    )
    my_heat_distribution_system.theoretical_thermal_building_demand_channel.source_output = (
        theoretical_thermal_building_demand
    )
    my_heat_distribution_system.state_channel.source_output = state_from_hds_controller
    my_heat_distribution_system.max_thermal_building_demand_channel.source_output = (
        maximal_thermal_building_demand
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            water_temperature_input_from_water_storage,
            theoretical_thermal_building_demand,
            maximal_thermal_building_demand,
            state_from_hds_controller,
            my_heat_distribution_system,
        ]
    )

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            water_temperature_input_from_water_storage,
            theoretical_thermal_building_demand,
            maximal_thermal_building_demand,
            state_from_hds_controller,
            my_heat_distribution_system,
        ]
    )

    stsv.values[water_temperature_input_from_water_storage.global_index] = 50
    stsv.values[maximal_thermal_building_demand.global_index] = 9000
    stsv.values[state_from_hds_controller.global_index] = 1

    timestep = 300

    # Simulate
    theoretical_thermal_building_demands_in_watt = [0, -10, +10, -8000, +3000]

    for demand in theoretical_thermal_building_demands_in_watt:

        stsv.values[theoretical_thermal_building_demand.global_index] = demand

        my_heat_distribution_system.i_restore_state()
        my_heat_distribution_system.i_simulate(timestep, stsv, False)

        water_mass_flow_of_hds_in_kg_per_second = stsv.values[6]
        water_output_temperature_in_celsius_from_simulation = stsv.values[4]

        # Test if water output of hds is correct for different theoretical thermal demands from building
        calculated_water_output_temperature_in_celsius = stsv.values[
            water_temperature_input_from_water_storage.global_index
        ] - stsv.values[theoretical_thermal_building_demand.global_index] / (
            water_mass_flow_of_hds_in_kg_per_second
            * my_heat_distribution_system.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius
        )

        log.information(
            "water output temperature after heat exchange with building "
            + str(water_output_temperature_in_celsius_from_simulation)
        )
        assert (
            calculated_water_output_temperature_in_celsius
            == water_output_temperature_in_celsius_from_simulation
        )
