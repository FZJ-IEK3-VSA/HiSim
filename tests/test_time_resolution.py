"""Test if components deliver similar results for different time resolutions.

Here we test the cluster household.
"""

import os
import shutil
from math import isclose, isnan
from typing import Dict, List, Tuple
from uuid import uuid4
import pandas as pd
import pytest
import hisim.simulator as sim
from hisim.simulator import SimulationParameters
from hisim.config import SizingContext, concrete
from hisim import utils
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.components import heating_curve
from hisim.components import (
    building,
    weather,
    loadprofilegenerator_utsp_connector,
    generic_pv_system,
    advanced_battery_bslib,
    controller_l2_energy_management_system,
    simple_water_storage,
    heat_distribution_system,
    electricity_meter,
    more_advanced_heat_pump_hplib
)

from hisim import loadtypes as lt
from tests.testing_utils import TestingUtils


def values_are_similar(lst: List, relative_tolerance: float = 0.05) -> bool:
    """Function to check if values are similar within a certain tolerance (rel tolerance = 5%, absolute tolerance = 0.1)."""
    reference = float(lst[0])
    return all(isclose(float(value), reference, rel_tol=relative_tolerance, abs_tol=0.1) for value in lst)


class OrderOfMagnitude:
    """The limits of the order-of-magnitude check on computed outputs across the three step lengths.

    ``FACTOR`` is the largest max/min ratio of the absolute yearly values the check accepts; where it comes from is
    in :func:`values_are_same_order_of_magnitude`. ``NEAR_ZERO_FLOOR`` is the absolute value below which a yearly
    value counts as zero, in the output's own unit: a net sum of large opposite flows ends near zero, and its ratio
    across step lengths means nothing. Example: the battery's net DC power over the one-day run, -0.20 / 0 / -6.16 W
    at 15 / 30 / 60 min, has a ratio of 31.5, but only -6.16 W lies above the floor, so there is no ratio to check
    (hisim-hydronics-fxix.18). The floor of 1.0 is ten times the absolute tolerance 0.1 of :func:`values_are_similar`.
    """

    #: The largest accepted max/min ratio of the absolute values, dimensionless.
    FACTOR: float = 15.0
    #: The absolute value below which a yearly value counts as zero, in the output's own unit.
    NEAR_ZERO_FLOOR: float = 1.0

    @classmethod
    def significant_magnitudes(cls, lst: List) -> List[float]:
        """Return the absolute values of a list that lie at or above the near-zero floor.

        Example: ``[0.25, 1.8e-8, 9.1e-8]`` (the grid feed-in in W of the one-day run) gives ``[]``, and
        ``[-0.2, 0.0, -6.16]`` gives ``[6.16]``.

        Args:
            lst: The yearly values of one output at the three step lengths.

        Returns:
            The absolute values at or above :attr:`NEAR_ZERO_FLOOR`, in their order.
        """
        return [magnitude for value in lst if (magnitude := abs(float(value))) >= cls.NEAR_ZERO_FLOOR]

    @classmethod
    def observed_ratio(cls, lst: List) -> float:
        """Return the max/min ratio of the significant magnitudes, or infinity with fewer than two of them.

        Example: ``[1.0, 20.0, 0.0]`` gives 20.0, ``[0.5, 20.0]`` gives infinity.

        Args:
            lst: The yearly values of one output at the three step lengths.

        Returns:
            The ratio, dimensionless; only used in the failure message.
        """
        magnitudes = cls.significant_magnitudes(lst)
        return max(magnitudes) / min(magnitudes) if len(magnitudes) >= 2 else float("inf")


def values_are_same_order_of_magnitude(lst: List, factor: float = OrderOfMagnitude.FACTOR) -> bool:
    """Check if values are within a multiplicative *factor* of each other.

    This is a looser check than :func:`values_are_similar`, intended for
    computed (non-invariant) components whose aggregated results legitimately
    vary across time resolutions due to different integration step sizes and
    control decisions.  The check uses absolute values so that quantities which
    can legitimately cross zero (e.g. ``SimpleHotWaterStorage|Heating|
    ThermalEnergyIncreaseInStorage`` going from -521 to +2262) are not flagged
    as regressions.  Values below :attr:`OrderOfMagnitude.NEAR_ZERO_FLOOR`
    (1.0 in the output's own unit) count as zero: a net sum of large opposite
    flows, such as the battery's net DC power (-0.20 / 0 / -6.16 W) or the
    grid feed-in (0.25 / 1.8e-8 / 9.1e-8 W) of the one-day run, ends near zero
    and its ratio means nothing (hisim-hydronics-fxix.18).  When at least two values lie
    at or above the floor the max/min ratio of their absolute values must stay
    below *factor*; with fewer than two the ratio check is skipped.

    The default factor of 15 was set when the yearly value of every output in
    seconds was its sum over all steps: ``MoreAdvancedHeatPumpHPLib|Time|TimeOff``,
    the seconds since the heat pump last switched off, summed to a value 4 times
    larger at 15 min than at 60 min from the step size alone, and reached a ratio
    of about 11. Since the outputs declare their aggregation
    (:class:`hisim.loadtypes.OutputAggregation`), such a restarting timer declares
    ``NONE`` and has no yearly value (the test loop skips it), and running totals
    (``Cumulative*``, ``LAST``) and stored energies (``ThermalEnergyInStorage``,
    ``MEAN``) no longer carry the factor of 4. The largest ratio left on
    2026-10-18 is about 14, for ``DHWStorage|Heating|ThermalEnergyUnmetDHWInWattHour``
    (22 to 309 Wh of unmet hot water, which depends on when the hot-water
    controller switches). A factor of 15 still catches gross regressions such as
    a 20x error.

    Known limitations (by design):

    - **Zero-at-one-resolution is not flagged.**  Several computed components
      legitimately produce 0 at one or two resolutions but not all three --
      e.g. ``Battery`` charging power/energy/losses (the EMS only triggers
      charging at 60-min resolution), ``ElectricityMeter`` feed-in
      (``ElectricityToGrid`` is non-zero only at 15-min), and
      ``L2EMSElectricityController`` storage temperature modifiers.  When
      fewer than two values lie at or above the near-zero floor the function
      returns ``True``, so a
      future regression that zeroes out a previously non-zero output at one
      resolution would not be caught by this check alone.  Such divergences
      are still *printed* by the test loop (the ``print(key, values, ...)``
      line) and are visible in the test output for manual inspection.
    - **Outputs whose yearly values all lie below the floor are not checked.**
      An output that is small in its own unit at every resolution (below 1.0)
      has no ratio to check; such values are printed by the test loop like the
      zero-at-one-resolution case.
    - **Sign flips of equal magnitude are not flagged.**  Because absolute
      values are used, a regression that flips a value from +X to -X (same
      magnitude) yields a ratio of 1.0 and passes.  This is the trade-off for
      handling the legitimate zero-crossing case above; a sign-aware check
      would falsely flag ``ThermalEnergyIncreaseInStorage``.
    """
    magnitudes = OrderOfMagnitude.significant_magnitudes(lst)
    if len(magnitudes) < 2:
        return True  # All near zero or a single significant value -- no ratio to check.
    return max(magnitudes) / min(magnitudes) < factor


@pytest.mark.base
@pytest.mark.parametrize(
    "values, expected",
    [
        # All equal -- trivially within factor.
        ([1.0, 1.0, 1.0], True),
        # Within factor -- similar.
        ([1.0, 10.0, 5.0], True),
        # Just below factor boundary -- similar (strict <).
        ([1.0, 14.99], True),
        # At factor boundary -- NOT similar (15.0 is not < 15.0).
        ([1.0, 15.0], False),
        # Across factor -- NOT similar.
        ([1.0, 16.0], False),
        ([1.0, 100.0], False),
        # Zero-crossing -- legitimate (e.g. ThermalEnergyIncreaseInStorage
        # goes from -521 to +2262; abs ratio ~4.7 < 15).
        ([-521.0, 483.0, 2262.0], True),
        # All zero -- no ratio to check.
        ([0.0, 0.0, 0.0], True),
        # Single non-zero -- no ratio to check (documented: battery only
        # charges at one resolution).
        ([100.0, 0.0, 0.0], True),
        ([0.0, 0.0, 13.9], True),
        # Empty list -- no ratio to check.
        ([], True),
        # Mixed zero within factor -- non-zero values are similar, zero is
        # skipped (documented behavior).
        ([100.0, 100.0, 0.0], True),
        # Mixed zero across factor -- non-zero values differ too much.
        ([1.0, 20.0, 0.0], False),
        # Consistent negative values -- ratio of absolute values.
        ([-100.0, -200.0, -150.0], True),
        # Sign flip of equal magnitude -- NOT detected (documented
        # limitation; abs-value ratio is 1.0).
        ([100.0, -100.0], True),
        # Value below near-zero threshold is treated as zero.
        ([1e-13, 1.0], True),
        # Net sums of large opposite flows below the 1.0 floor count as zero
        # (hisim-hydronics-fxix.18): the battery's net DC power and the grid feed-in in W
        # of the one-day cluster-house run at 15 / 30 / 60 min.
        ([-0.1955, 0.0, -6.157], True),
        ([0.25, 1.8e-8, 9.1e-8], True),
        # At the floor a value counts: 1.0 against 20.0 is a ratio of 20.
        ([1.0, 20.0], False),
        ([0.99, 20.0], True),
    ],
)
def test_values_are_same_order_of_magnitude(values: List, expected: bool) -> None:
    """Unit tests for the order-of-magnitude similarity helper.

    Covers edge cases that the extendedbase integration test does not exercise
    in the base CI gate: factor boundary, zero-crossing, all-zero, single
    non-zero, mixed zero, sign-flip limitation, and empty list.
    """
    assert values_are_same_order_of_magnitude(lst=values) == expected


# PATH and FUNC needed to build simulator, PATH is fake
PATH = "../system_setups/household_test_timeresolutions.py"


@utils.measure_execution_time
@pytest.mark.extendedbase
def test_cluster_house_for_several_time_resolutions():
    """Test cluster house for several time resolutions."""

    opex_consumption_dict: Dict = {}
    yearly_results_dict: Dict = {}
    run_id = uuid4().hex
    # do not use seconds per timestep = 60 because test takes then too long
    for seconds_per_timestep in [60 * 15, 60 * 30, 60 * 60]:
        print("\n")
        print("Seconds per timestep ", seconds_per_timestep)
        # run simulation of cluster house
        result_dict, opex_consumption_dict = run_cluster_house(
            seconds_per_timestep=seconds_per_timestep,
            yearly_result_dict=yearly_results_dict,
            opex_consumptions_dict=opex_consumption_dict,
            run_id=run_id,
        )

    # go through all results and compare if aggregated results are all the same
    print("\n")
    print("Yearly results including KPIs")
    # The yearly-results CSV (yearly_<days>_days.csv in
    # result_data_for_scenario_evaluation) keys its "variable" column by the
    # configured component name (Component.component_name == config.component_id.name for this
    # single-building simulation), not by the Python class name -- see
    # PostProcessingChartController.get_variable_name_and_unit_from_ppdt_results_column,
    # which builds the variable name from the component output's pretty name
    # (object_name == component_name). The load-profile generator is configured
    # with the name "UTSPConnector"
    # (the name every setup passes to UtspLpgConnectorConfig.preset_couple_both_at_work), so its class
    # name "UtspLpgConnector" does not appear in the yearly-result keys -- match
    # the configured name instead. PVSystem's and Weather's configured names equal
    # their class names, so their get_classname() matches their yearly-result keys.
    utsp_connector_name = (
        loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.preset_couple_both_at_work("UTSPConnector").component_id.name
    )
    # Predefined-input components whose aggregated yearly results must be stable
    # across the three time resolutions (15/30/60 min).
    invariant_yearly_component_names = [
        utsp_connector_name,
        generic_pv_system.PVSystem.get_classname(),
        weather.Weather.get_classname(),
    ]
    # Track which invariant components were actually seen so the similarity
    # checks below cannot silently go dead (e.g. if a component is renamed and the
    # substring match no longer hits any yearly-result key).
    seen_invariant_yearly_components = set()
    for key, values in result_dict.items():
        # An output declaring the aggregation NONE (a sun angle, a controller mode) has no run value:
        # its yearly cell is NaN at every resolution, which is the same result each time.
        if all(isnan(float(value)) for value in values):
            continue
        is_invariant = False
        # for these components the outputs must be identical as they are predefined input data
        for component_name in invariant_yearly_component_names:
            if component_name in key:
                is_invariant = True
                seen_invariant_yearly_components.add(component_name)
                assert values_are_similar(lst=values), f"{key}: {values} not all similar."
        if not values_are_similar(lst=values):
            print(key, values, "not all similar. ")
            if not is_invariant:
                # Computed (non-invariant) components legitimately vary across
                # time resolutions, but should stay within a multiplicative
                # factor -- a larger divergence signals a numerical regression.
                assert values_are_same_order_of_magnitude(lst=values), (
                    f"{key}: {values} differ by more than a factor of "
                    f"{OrderOfMagnitude.FACTOR} (observed max/min ratio = "
                    f"{OrderOfMagnitude.observed_ratio(values):.1f}) across time resolutions, indicating "
                    f"a likely regression."
                )

    missing_invariant_yearly_components = (
        set(invariant_yearly_component_names) - seen_invariant_yearly_components
    )
    assert not missing_invariant_yearly_components, (
        f"Expected invariant yearly-result components {sorted(missing_invariant_yearly_components)} not found in "
        f"yearly results; available keys: {list(result_dict)}."
    )
    # go through all opex consumptions and compare if results are all the same
    print("\n")
    print("Opex consumptions in kWh")
    # The opex CSV (operational_costs_co2_footprint.csv) indexes components by their
    # configured component name (Component.component_name == config.component_id.name for this
    # single-building simulation), not by the Python class name. The load-profile
    # generator is configured with the name "UTSPConnector"
    # (the name every setup passes to UtspLpgConnectorConfig.preset_couple_both_at_work), so its class
    # name "UtspLpgConnector" does not appear in the opex keys -- match the
    # configured name instead. PVSystem's configured name equals its class name, so
    # PVSystem.get_classname() matches its opex key. The opex CSV also contains
    # empty separator rows whose index is read as NaN (a float); skip those before
    # treating the key as a string so the substring checks below do not raise a
    # TypeError.
    # Predefined-input components whose opex "Total energy consumption [kWh]" must be
    # stable across time resolutions. Weather is deliberately excluded here: it only
    # provides input data and its get_cost_opex returns the default OpexCostDataClass
    # (kpi_tag=None), so it is skipped when the opex CSV is written and therefore has
    # no consumption row. Weather is still asserted in the yearly-results loop above.
    # utsp_connector_name is defined above, next to the yearly-results loop, because
    # both the yearly-results and opex CSVs key components by the configured name.
    invariant_opex_component_names = [
        utsp_connector_name,
        generic_pv_system.PVSystem.get_classname(),
    ]
    # Track which invariant components were actually seen so the similarity checks
    # below cannot silently go dead (e.g. if a component is renamed and the substring
    # match no longer hits any opex key).
    seen_invariant_components = set()
    for key, values in opex_consumption_dict.items():
        if not isinstance(key, str):
            continue
        is_invariant = False
        for component_name in invariant_opex_component_names:
            if component_name in key:
                is_invariant = True
                seen_invariant_components.add(component_name)
                # for these components the consumption must be similar as they are
                # predefined input data
                assert values_are_similar(lst=values), f"{key}: {values} not all similar."
        if not values_are_similar(lst=values):
            print(key, values, "not all similar. ")
            if not is_invariant:
                # Computed (non-invariant) components legitimately vary across
                # time resolutions, but should stay within a multiplicative
                # factor -- a larger divergence signals a numerical regression.
                assert values_are_same_order_of_magnitude(lst=values), (
                    f"{key}: {values} differ by more than a factor of "
                    f"{OrderOfMagnitude.FACTOR} (observed max/min ratio = "
                    f"{OrderOfMagnitude.observed_ratio(values):.1f}) across time resolutions, indicating "
                    f"a likely regression."
                )

    missing_invariant_components = set(invariant_opex_component_names) - seen_invariant_components
    assert not missing_invariant_components, (
        f"Expected invariant opex components {sorted(missing_invariant_components)} not found in opex "
        f"results; available keys: {list(opex_consumption_dict)}."
    )

    print(
        "Please make sure that your data is correctly resampled. "
        "In some cases different time resolutions can lead to different calculation results."
    )


def run_cluster_house(
    seconds_per_timestep: int, yearly_result_dict: Dict, opex_consumptions_dict: Dict, run_id: str
) -> Tuple[Dict, Dict]:  # noqa: too-many-statements
    """The test should check if a normal simulation works with the electricity grid implementation."""

    # =========================================================================================================================================================
    # System Parameters

    # Set Simulation Parameters
    year = 2021
    # Build Simulation Parameters

    my_simulation_parameters = SimulationParameters.one_day_only(year=year, seconds_per_timestep=seconds_per_timestep)
    my_simulation_parameters.logging_level = log.LogPrio.ERROR
    my_simulation_parameters.result_directory = TestingUtils.get_result_directory(
        test_name=f"test_cluster_house_for_several_time_resolutions_{run_id}_{seconds_per_timestep}s"
    )
    if os.path.isdir(my_simulation_parameters.result_directory):
        shutil.rmtree(my_simulation_parameters.result_directory)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_CAPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_OPEX)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.COMPUTE_KPIS)
    my_simulation_parameters.post_processing_options.append(PostProcessingOptions.WRITE_KPIS_TO_JSON)
    my_simulation_parameters.post_processing_options.append(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION
    )

    # this part is copied from hisim_main
    # Build Simulator
    normalized_path = os.path.normpath(PATH)
    path_in_list = normalized_path.split(os.sep)
    if len(path_in_list) >= 1:
        path_to_be_added = os.path.join(os.getcwd(), *path_in_list[:-1])

    my_sim: sim.Simulator = sim.Simulator(
        module_directory=path_to_be_added,
        my_simulation_parameters=my_simulation_parameters,
        module_filename="household_test_timeresolutions",
    )
    my_sim.set_simulation_parameters(my_simulation_parameters)

    # =================================================================================================================================
    # Set System Parameters

    # Set Heat Pump Controller
    hp_controller_mode = 2  # mode 1 for heating/off and mode 2 for heating/cooling/off
    heating_reference_temperature_in_celsius = -7.0

    # Set Weather
    weather_location = "AACHEN"

    # =================================================================================================================================
    # Build Basic Components
    # Build Building
    # The weather config is created first: the building and PV configs copy its identity
    # (weather_identity) and must have it before those components are built. The weather
    # component itself is still added further down, so the simulator's component order is unchanged.
    my_weather_config = weather.WeatherConfig.for_location(
        "Weather", weather.LocationEnum[weather_location], heating_reference_temperature_in_celsius
    )

    my_building_config = building.BuildingConfig.preset_german_single_family_home("Building")
    my_building_config.weather_identity = my_weather_config.identity()
    # The design outside temperature is the weather's, not the building's: the building reads
    # it as a sized field, so it is resolved before the archetype lookup runs on the config.
    my_building_config = my_building_config.resolve(
        SizingContext(heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius)
    )
    my_building_information = building.BuildingInformation(config=my_building_config)
    my_building = building.Building(config=my_building_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_building, connect_automatically=True)

    # Build Occupancy
    my_occupancy_config = loadprofilegenerator_utsp_connector.UtspLpgConnectorConfig.preset_couple_both_at_work("UTSPConnector")
    my_occupancy_config.data_acquisition_mode = (
        loadprofilegenerator_utsp_connector.LpgDataAcquisitionMode.USE_PREDEFINED_PROFILE
    )

    my_occupancy = loadprofilegenerator_utsp_connector.UtspLpgConnector(
        config=my_occupancy_config, my_simulation_parameters=my_simulation_parameters
    )
    # Add to simulator
    my_sim.add_component(my_occupancy)

    # Build Weather
    my_weather = weather.Weather(config=my_weather_config, my_simulation_parameters=my_simulation_parameters)
    # Add to simulator
    my_sim.add_component(my_weather)

    # Build PV
    my_photovoltaic_system_config = generic_pv_system.PVSystemConfig.preset_rooftop("PVSystem")
    my_photovoltaic_system_config.location = weather_location
    my_photovoltaic_system_config = my_photovoltaic_system_config.resolve(
        SizingContext(
            roof_area_in_m2=my_building_information.roof_area_in_m2,
            weather_identity=my_weather_config.identity(),
        )
    )
    my_photovoltaic_system = generic_pv_system.PVSystem(
        config=my_photovoltaic_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_photovoltaic_system, connect_automatically=True)

    # Build Heat Distribution Controller
    my_heat_distribution_controller_config = (
        heat_distribution_system.HeatDistributionControllerConfig.preset_building_derived(
            "HeatDistributionController"
        ).resolve(
            SizingContext(
                heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
                conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
                heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
                set_heating_temperature_in_celsius=(
                    my_building_information.set_heating_temperature_for_building_in_celsius
                ),
                set_cooling_temperature_in_celsius=(
                    my_building_information.set_cooling_temperature_for_building_in_celsius
                ),
            )
        )
    )

    my_heat_distribution_controller = heat_distribution_system.HeatDistributionController(
        my_simulation_parameters=my_simulation_parameters, config=my_heat_distribution_controller_config,
    )
    my_hds_controller_information = heat_distribution_system.HeatDistributionControllerInformation(
        config=my_heat_distribution_controller_config
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_controller, connect_automatically=True)

    # Build the heating curve, the L2 controller that sends the supply temperature and the heating season to the
    # generator's controller and the heat distribution controller
    my_heating_curve_config = heating_curve.HeatingCurveControllerConfig.preset_building_derived(
        "HeatingCurveController"
    ).resolve(
        SizingContext(
            heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            set_heating_temperature_in_celsius=my_building_information.set_heating_temperature_for_building_in_celsius,
            heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
        )
    )
    my_heating_curve = heating_curve.HeatingCurveController(
        my_simulation_parameters=my_simulation_parameters, config=my_heating_curve_config
    )
    my_sim.add_component(my_heating_curve, connect_automatically=True)

    # Build Heat Pump Controller for space heating
    my_heatpump_controller_sh_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeatingConfig.preset_standard(
            "MoreAdvancedHeatPumpHPLibControllerSH"
        ).resolve(
            SizingContext(
                set_heating_temperature_in_celsius=concrete(
                    my_building_information.set_heating_temperature_for_building_in_celsius
                ),
                heat_distribution_system_type=my_hds_controller_information.heat_distribution_system_type,
            )
        )
    )
    my_heatpump_controller_sh_config.mode = hp_controller_mode

    my_heatpump_controller_sh = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating(
        config=my_heatpump_controller_sh_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_sh, connect_automatically=True)

    my_heatpump_controller_dhw_config = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHWConfig.preset_standard(
            "HeatPumpControllerDHW"
        )
    )

    # Build Heat Pump Controller for dhw
    my_heatpump_controller_dhw = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW(
        config=my_heatpump_controller_dhw_config, my_simulation_parameters=my_simulation_parameters
    )
    my_sim.add_component(my_heatpump_controller_dhw, connect_automatically=True)

    # Build Heat Pump (for dhw and space heating)
    my_heatpump_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water(
        "MoreAdvancedHeatPumpHPLib"
    ).resolve(
        SizingContext(
            heating_load_in_watt=my_building_information.max_thermal_building_demand_in_watt,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
        )
    )
    my_heatpump_config.with_domestic_hot_water_preparation = True

    my_heatpump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
        config=my_heatpump_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Verknüpfung mit Luft als Umgebungswärmeqzuelle
    if my_heatpump.parameters["Group"].iloc[0] == 1.0 or my_heatpump.parameters["Group"].iloc[0] == 4.0:
        my_heatpump.connect_input(
            my_heatpump.TemperatureInputPrimary,
            my_weather.component_name,
            my_weather.DailyAverageOutsideTemperatures,
        )
    else:
        raise KeyError(
            "Wasser oder Sole als primäres Wärmeträgermedium muss über extra Wärmenetz-Modell noch bereitgestellt werden"
        )
    # Add to simulator
    my_sim.add_component(my_heatpump, connect_automatically=True)

    # DHW storage configs
    my_dhw_storage_config = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=my_building_information.number_of_apartments)
    )

    my_dhw_storage = simple_water_storage.SimpleDHWStorage(
        my_simulation_parameters=my_simulation_parameters, config=my_dhw_storage_config
    )

    my_sim.add_component(my_dhw_storage, connect_automatically=True)

    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer(
        "SimpleHotWaterStorage"
    )
    # The litres-per-kilowatt figure is per kind of generator, and the volume law reads the field,
    # so the option is set on the preset before the configuration is resolved.
    my_simple_heat_water_storage_config.sizing_option = (
        simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_HEAT_PUMP
    )
    my_simple_heat_water_storage_config = my_simple_heat_water_storage_config.resolve(
        SizingContext(
            maximal_thermal_power_in_watt=concrete(my_heatpump_config.set_thermal_output_power_in_watt)
        )
    )
    my_simple_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_simple_water_storage, connect_automatically=True)

    # Build Heat Distribution System
    my_heat_distribution_system_config = heat_distribution_system.HeatDistributionConfig.preset_building_derived(
        "HeatDistributionSystem"
    ).resolve(
        SizingContext(
            water_mass_flow_rate_in_kg_per_second=my_hds_controller_information.water_mass_flow_rate_in_kg_per_second,
            conditioned_floor_area_in_m2=my_building_information.scaled_conditioned_floor_area_in_m2,
            heat_distribution_system_type=my_hds_controller_information.hds_controller_config.heating_system,
    )
    )
    my_heat_distribution_system = heat_distribution_system.HeatDistribution(
        config=my_heat_distribution_system_config, my_simulation_parameters=my_simulation_parameters,
    )
    # Add to simulator
    my_sim.add_component(my_heat_distribution_system, connect_automatically=True)

    # Build Electricity Meter
    my_electricity_meter = electricity_meter.ElectricityMeter(
        my_simulation_parameters=my_simulation_parameters,
        config=electricity_meter.ElectricityMeterConfig.preset_standard("ElectricityMeter"),
    )

    # Build EMS
    my_electricity_controller_config = (
        controller_l2_energy_management_system.EMSConfig.preset_optimize_own_consumption(
            "L2EMSElectricityController"
        )
    )

    my_electricity_controller = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=my_simulation_parameters, config=my_electricity_controller_config,
    )

    # Build Battery
    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
        SizingContext(pv_peak_power_in_watt=(concrete(my_photovoltaic_system_config.power_in_watt),))
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        my_simulation_parameters=my_simulation_parameters, config=my_advanced_battery_config,
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Add outputs to EMS
    loading_power_input_for_battery_in_watt = my_electricity_controller.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    )

    # -----------------------------------------------------------------------------------------------------------------
    # Connect Battery
    my_advanced_battery.connect_dynamic_input(
        input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput,
        src_object=loading_power_input_for_battery_in_watt,
    )

    # =================================================================================================================================
    # Add Remaining Components to Simulation Parameters

    # the meter reads every electricity port by its role, never the energy manager's balance
    my_sim.add_component(my_electricity_meter, connect_automatically=True)
    my_sim.add_component(my_advanced_battery)
    my_sim.add_component(my_electricity_controller, connect_automatically=True)

    my_sim.run_all_timesteps()

    # =========================================================================================================================================================
    # Get yearly results from scenario preparation
    yearly_results_path = os.path.join(
        my_simulation_parameters.result_directory,
        "result_data_for_scenario_evaluation",
        f"yearly_{my_simulation_parameters.duration.days}_days.csv",
    )
    yearly_results = pd.read_csv(yearly_results_path, usecols=["variable", "value"])
    # Get opex consumptions
    opex_results_path = os.path.join(my_simulation_parameters.result_directory, "operational_costs_co2_footprint.csv")

    opex_df = pd.read_csv(opex_results_path, index_col=0, sep=";")

    # append results to result dictionaries
    if yearly_result_dict == {} and opex_consumptions_dict == {}:
        yearly_result_dict.update(
            {yearly_results["variable"][i]: [yearly_results["value"][i]] for i in range(len(yearly_results))}
        )
        for j in range(len(opex_df)):
            opex_consumptions_dict.update({opex_df.index[j]: [opex_df["Total energy consumption [kWh]"].iloc[j]]})
    else:
        for i in range(len(yearly_results)):
            yearly_result_dict[yearly_results["variable"][i]].append(yearly_results["value"][i])

        for j in range(len(opex_df)):
            opex_consumptions_dict[opex_df.index[j]].append(opex_df["Total energy consumption [kWh]"].iloc[j])

    return yearly_result_dict, opex_consumptions_dict
