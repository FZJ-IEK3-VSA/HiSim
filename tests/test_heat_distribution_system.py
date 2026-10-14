"""Test for heat distribution system."""

#  clean
from typing import Tuple
import pytest
from hisim import component as cp
from hisim.components import heat_distribution_system, building
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.config import ComponentID, SizingContext
from tests import functions_for_testing as fft


@pytest.mark.base
def test_hds() -> None:
    """Test the heat distribution system across various scenarios.

    Verifies that the heat distribution system correctly handles:
    - Heating scenarios (positive thermal demand)
    - Cooling scenarios (negative thermal demand)
    - Zero-demand scenarios
    Across a range of water input temperatures and building thermal demands.
    Validates that water output temperatures and thermal power delivery
    are calculated correctly based on heat exchange physics.
    """

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
                if input_water_temperature_in_celsius > residence_temperature_in_celsius:

                    assert water_output_temperature_after_heat_exchange == max(
                        calculated_water_output_temperature_in_celsius,
                        residence_temperature_in_celsius,
                    )

                # if water input temp is too low no heat exchange
                elif input_water_temperature_in_celsius < residence_temperature_in_celsius:

                    assert water_output_temperature_after_heat_exchange == input_water_temperature_in_celsius
                    assert effective_thermal_power_delivered_in_watt == 0

            # test the case where the building needs cooling
            elif theoretical_building_demand_in_watt < 0:
                # if water input temp is low enough, heat exchange is possible but water output temp can not get higher than residence temp
                if input_water_temperature_in_celsius < residence_temperature_in_celsius:

                    assert water_output_temperature_after_heat_exchange == min(
                        calculated_water_output_temperature_in_celsius,
                        residence_temperature_in_celsius,
                    )

                # if water input temp is too high no heat exchange
                elif input_water_temperature_in_celsius > residence_temperature_in_celsius:

                    assert water_output_temperature_after_heat_exchange == input_water_temperature_in_celsius
                    assert effective_thermal_power_delivered_in_watt == 0

            elif theoretical_building_demand_in_watt == 0:
                assert water_output_temperature_after_heat_exchange == input_water_temperature_in_celsius
                assert effective_thermal_power_delivered_in_watt == 0


def simulate_and_calculate_hds_outputs_for_a_given_theoretical_heating_demand_from_building(
    theoretical_building_demand_in_watt: float,
    water_input_temperature_in_celsius: float,
) -> Tuple[float, float, float, float, float, float]:
    """Simulate and calculate heat distribution system outputs for a given scenario.

    Sets up a minimal HiSim simulation with fake component outputs, runs one
    timestep, and returns both the expected and simulated heat distribution
    outputs for validation.

    Args:
        theoretical_building_demand_in_watt: The theoretical thermal demand
            from the building in watts. Positive values indicate heating demand,
            negative values indicate cooling demand.
        water_input_temperature_in_celsius: The temperature of the water
            entering the heat distribution system in degrees Celsius.

    Returns:
        A 6-tuple of floats containing:
        - input_water_temperature_in_celsius: The water input temperature
        - residence_temperature_in_celsius: The indoor air temperature of the residence
        - theoretical_building_demand_in_watt: The theoretical building thermal demand
        - calculated_water_output_temperature_in_celsius: The expected water output
            temperature based on heat exchange calculations
        - water_output_temperature_after_heat_exchange: The actual water output
            temperature from the simulation
        - effective_thermal_power_delivered_in_watt: The actual thermal power
            delivered by the heat distribution system
    """

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # Set Heat Distribution System
    hds_name = "HeatDistributionSystem"

    # ===================================================================================================================
    # The design outside temperature is the weather's, not the building's: the building reads
    # it as a sized field (D-21), and this test builds no weather, so it states the number.
    my_building_config = building.BuildingConfig.preset_german_single_family_home("Building")
    my_building_config.heating_reference_temperature_in_celsius = -7.0
    my_building_information = building.BuildingInformation(config=my_building_config)

    # Build Heat Distribution System
    my_hds_controller_config = heat_distribution_system.HeatDistributionControllerConfig.preset_building_derived(
        "HeatDistributionController"
    ).resolve(
        SizingContext(heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt)
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_hds_controller_config
    )

    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig(
        component_id=ComponentID(name=hds_name),
        device_co2_footprint_in_kg=0,
        investment_costs_in_euro=8000,
        lifetime_in_years=50,
        maintenance_costs_in_euro_per_year=80,
        subsidy_as_percentage_of_investment_costs=0,
        water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kg_per_second,
        position_hot_water_storage_in_system=heat_distribution_system.PositionHotWaterStorageInSystemSetup.PARALLEL,
        absolute_conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
        heating_system=my_hds_controller_information.hds_controller_config.heating_system,
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
        component_id=ComponentID("FakeWaterTemperatureInput"),
    )
    residence_temperature_indoor_air = cp.ComponentOutput(
        "FakeResidenceTemperatureInput",
        "ResidenceTemperatureInput",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=ComponentID("FakeResidenceTemperatureInput"),
    )
    theoretical_thermal_building_demand = cp.ComponentOutput(
        "FakeTheoreticalThermalDemand",
        "TheoreticalThermalDemand",
        lt.LoadTypes.HEATING,
        lt.Units.WATT,
        component_id=ComponentID("FakeTheoreticalThermalDemand"),
    )

    state_from_hds_controller = cp.ComponentOutput(
        "FakeStateController",
        "StateController",
        lt.LoadTypes.ANY,
        lt.Units.ANY,
        component_id=ComponentID("FakeStateController"),
    )

    # connect hds inputs to fake outputs
    my_heat_distribution_system.water_temperature_input_channel.source_output = (
        water_temperature_input_from_water_storage
    )
    my_heat_distribution_system.residence_temperature_input_channel.source_output = residence_temperature_indoor_air
    my_heat_distribution_system.theoretical_thermal_building_demand_channel.source_output = (
        theoretical_thermal_building_demand
    )
    my_heat_distribution_system.state_channel.source_output = state_from_hds_controller

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(
        fft.get_number_of_outputs(
            [
                water_temperature_input_from_water_storage,
                residence_temperature_indoor_air,
                theoretical_thermal_building_demand,
                state_from_hds_controller,
                my_heat_distribution_system,
            ]
        )
    )

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

    stsv.values[water_temperature_input_from_water_storage.global_index] = water_input_temperature_in_celsius

    stsv.values[state_from_hds_controller.global_index] = 1
    stsv.values[residence_temperature_indoor_air.global_index] = 19
    timestep = 300
    water_mass_flow_of_hds_in_kg_per_second = my_hds_controller_information.water_mass_flow_rate_in_kg_per_second
    # Simulate

    stsv.values[theoretical_thermal_building_demand.global_index] = theoretical_building_demand_in_watt

    my_heat_distribution_system.i_restore_state()
    my_heat_distribution_system.i_simulate(timestep, stsv, False)

    # if in hds component the state values are set as output values,
    # then here the water_temp_output and the thermal_power_delivered should be set, not the stsv.values[4] and [5]
    water_output_temperature_in_celsius_from_simulation = (
        my_heat_distribution_system.state.water_output_temperature_in_celsius
    )  # stsv.values[4]
    effective_thermal_power_delivered_in_watt = (
        my_heat_distribution_system.state.thermal_power_delivered_in_watt
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
        f"water input temperature in celsius {stsv.values[water_temperature_input_from_water_storage.global_index]}"
    )
    log.information(f"residence temperature in celsius {stsv.values[residence_temperature_indoor_air.global_index]}")
    log.information(f"theoretical thermal building demand in watt {theoretical_building_demand_in_watt}")
    log.information(
        f"theoretical water output temperature after heat exchange with building "
        f"{calculated_water_output_temperature_in_celsius}"
    )
    log.information(
        f"real water output temperature after heat exchange with building "
        f"{water_output_temperature_in_celsius_from_simulation}"
    )
    log.information(f"real thermal output delivered from hds {effective_thermal_power_delivered_in_watt}\n")
    return (
        stsv.values[water_temperature_input_from_water_storage.global_index],
        stsv.values[residence_temperature_indoor_air.global_index],
        stsv.values[theoretical_thermal_building_demand.global_index],
        calculated_water_output_temperature_in_celsius,
        water_output_temperature_in_celsius_from_simulation,
        effective_thermal_power_delivered_in_watt,
    )


@pytest.mark.base
def test_get_cost_capex_raises_on_unknown_heating_system() -> None:
    """Unknown heating_system values must raise ValueError, not silently return defaults.

    Regression test for the silent-default ``else`` branch in
    ``HeatDistribution.get_cost_capex``: an invalid ``heating_system`` used to
    return a default ``CapexCostDataClass`` (zero/default CAPEX), masking
    configuration errors. It must now raise a ``ValueError`` naming the
    offending value.
    """
    invalid_heating_system = "NOT_A_HEATING_SYSTEM"  # not a HeatDistributionSystemType member
    config = heat_distribution_system.HeatDistributionConfig.preset_building_derived("HeatDistributionSystem")
    config.water_mass_flow_rate_in_kg_per_second = 0.1
    config.absolute_conditioned_floor_area_in_m2 = 100.0
    config.heating_system = invalid_heating_system  # type: ignore[assignment]  # intentionally invalid
    simulation_parameters = SimulationParameters.one_day_only(2017, 60)
    with pytest.raises(ValueError, match="Unknown heating_system type"):
        heat_distribution_system.HeatDistribution.get_cost_capex(
            config=config, simulation_parameters=simulation_parameters
        )


class TestHeatDistributionValve:

    """The heat-distribution controller's valve: its transitions on the building's demand and its published state."""

    Mode = heat_distribution_system.HeatDistributionValveMode

    #: The valve's transition table: the mode before the pass, the building's theoretical demand in W, the next mode.
    TRANSITIONS = [
        (Mode.OFF, 300.0, Mode.HEATING),
        (Mode.OFF, -300.0, Mode.COOLING),
        (Mode.OFF, 0.0, Mode.OFF),
        (Mode.HEATING, 300.0, Mode.HEATING),
        (Mode.HEATING, -300.0, Mode.HEATING),
        (Mode.HEATING, 0.0, Mode.OFF),
        (Mode.COOLING, -300.0, Mode.COOLING),
        (Mode.COOLING, 300.0, Mode.COOLING),
        (Mode.COOLING, 0.0, Mode.OFF),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(("mode", "demand_in_watt", "expected"), TRANSITIONS)
    def test_every_transition_of_the_valve(
        self, mode: heat_distribution_system.HeatDistributionValveMode, demand_in_watt: float, expected: heat_distribution_system.HeatDistributionValveMode
    ) -> None:
        """Catches a valve that opens without a demand, or one that closes while the building still needs heat."""
        assert heat_distribution_system.HeatDistributionController.next_valve_mode(mode, demand_in_watt) is expected

    @pytest.mark.base
    @pytest.mark.parametrize(
        ("mode", "is_heating_season", "expected_state"),
        [
            (Mode.HEATING, True, 1),
            (Mode.HEATING, False, 0),
            (Mode.COOLING, True, -1),
            (Mode.COOLING, False, -1),
            (Mode.OFF, True, 0),
            (Mode.OFF, False, 0),
        ],
    )
    def test_the_valve_heats_only_in_the_heating_season(
        self, mode: heat_distribution_system.HeatDistributionValveMode, is_heating_season: bool, expected_state: int
    ) -> None:
        """Catches a valve that passes heat outside the heating season the heating curve publishes."""
        assert heat_distribution_system.HeatDistributionController.valve_state(mode, is_heating_season=is_heating_season) == expected_state


class TestValveState:

    """The heat-distribution controller's valve mode advances only between steps, not between the passes of one step."""

    @staticmethod
    def controller_with_inputs() -> tuple:
        """Build the controller with its two inputs wired to stand-in outputs.

        Returns:
            The controller, the demand output, the heating-season output and the step values.
        """
        config = heat_distribution_system.HeatDistributionControllerConfig(
            component_id=ComponentID(name="HeatDistributionController"), heating_load_of_building_in_watt=8000.0
        )
        controller = heat_distribution_system.HeatDistributionController(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 60), config=config
        )
        demand = cp.ComponentOutput("Building", "Demand", lt.LoadTypes.HEATING, lt.Units.WATT, component_id=ComponentID("Building"))
        season = cp.ComponentOutput("Curve", "Season", lt.LoadTypes.ANY, lt.Units.BINARY, component_id=ComponentID("Curve"))
        controller.theoretical_thermal_building_demand_channel.source_output = demand
        controller.heating_season_channel.source_output = season
        outputs = [demand, season, *controller.outputs]
        for index, output in enumerate(outputs):
            output.global_index = index
        stsv = cp.SingleTimeStepValues(len(outputs))
        stsv.set_output_value(season, 1.0)
        return controller, demand, stsv

    @staticmethod
    def published_state(
        controller: heat_distribution_system.HeatDistributionController,
        demand: cp.ComponentOutput,
        stsv: cp.SingleTimeStepValues,
        demand_in_watt: float,
    ) -> float:
        """Run one pass of the controller with this building demand and return the state it publishes.

        Args:
            controller: The controller.
            demand: The stand-in output of the building's theoretical demand.
            stsv: The step values.
            demand_in_watt: The building's theoretical demand of the pass, in W.

        Returns:
            The published valve state: 1 heating, -1 cooling, 0 closed.
        """
        stsv.set_output_value(demand, demand_in_watt)
        controller.i_restore_state()
        controller.i_simulate(0, stsv, False)
        return stsv.values[controller.state_channel.global_index]

    @pytest.mark.base
    def test_a_pass_starts_from_the_mode_the_last_step_converged_to(self) -> None:
        """Catches a valve mode that advances inside ``i_simulate``, which would make a pass depend on the pass before.

        A closed valve opens for heating on a positive demand in one pass; a later pass of the same step with a
        negative demand starts again from the closed valve and opens it for cooling, as a fresh step would.
        """
        controller, demand, stsv = self.controller_with_inputs()
        controller.i_save_state()
        assert self.published_state(controller, demand, stsv, 300.0) == 1
        assert self.published_state(controller, demand, stsv, -300.0) == -1
        assert self.published_state(controller, demand, stsv, 300.0) == 1
        controller.i_save_state()
        assert self.published_state(controller, demand, stsv, -300.0) == 1  # an open heating valve stays open
