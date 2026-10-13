"""Test for the advanced battery lib."""

import dataclasses
from typing import List, Tuple

import pytest
from hisim import component as cp
from hisim.components import advanced_battery_bslib
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.config import ComponentID, ConfigSizingError, SizingContext, auto_fields, concrete
from tests import functions_for_testing as fft


@pytest.mark.base
def test_advanced_battery_bslib() -> None:
    """Performs a basic test for a single calculation of the battery lib."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # ===================================================================================================================
    # Set Advanced Battery
    system_id = "SG1"  # Generic ac coupled battery storage system
    p_inv_custom = 5000  # W
    e_bat_custom = 10  # kWh
    name = "Battery"
    source_weight = 1
    charge_in_kwh = 0
    discharge_in_kwh = 0
    co2_footprint = e_bat_custom * 130.7
    cost = e_bat_custom * 535.81
    lifetime = 10
    lifetime_in_cycles = 5e3
    maintenance_costs_in_euro_per_year = 0.02 * cost
    subsidy_as_percentage_of_investment_costs = 0.0

    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig(
        system_id=system_id,
        custom_pv_inverter_power_generic_in_watt=p_inv_custom,
        custom_battery_capacity_generic_in_kilowatt_hour=e_bat_custom,
        component_id=ComponentID(name=name),
        source_weight=source_weight,
        charge_in_kwh=charge_in_kwh,
        discharge_in_kwh=discharge_in_kwh,
        device_co2_footprint_in_kg=co2_footprint,
        investment_costs_in_euro=cost,
        lifetime_in_years=lifetime,
        lifetime_in_cycles=lifetime_in_cycles,
        maintenance_costs_in_euro_per_year=maintenance_costs_in_euro_per_year,
        subsidy_as_percentage_of_investment_costs=subsidy_as_percentage_of_investment_costs,
    )
    my_advanced_battery = advanced_battery_bslib.Battery(
        config=my_advanced_battery_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    # Set Fake Input
    loading_power_input = cp.ComponentOutput(
        "FakeLoadingPowerInput",
        "LoadingPowerInput",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("FakeLoadingPowerInput"),
    )

    number_of_outputs = fft.get_number_of_outputs([my_advanced_battery, loading_power_input])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    my_advanced_battery.loading_power_input_channel.source_output = loading_power_input

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([my_advanced_battery, loading_power_input])

    stsv.values[loading_power_input.global_index] = 4000

    timestep = 1000

    # Simulate
    my_advanced_battery.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Check if set power is charged
    assert stsv.values[my_advanced_battery.ac_battery_power_channel.global_index] == 4000  # noqa B101
    assert stsv.values[my_advanced_battery.dc_battery_power_channel.global_index] == 3807.546  # noqa B101
    assert stsv.values[my_advanced_battery.state_of_charge_channel.global_index] == 0.006185227970066665  # noqa B101


@pytest.mark.base
def test_advanced_battery_bslib_get_cost_capex_zero_lifetime_in_cycles() -> None:
    """Regression test: get_cost_capex must not raise NameError when lifetime_in_cycles <= 0.

    When ``lifetime_in_cycles`` is zero (or negative), the original code logged a warning but
    then fell through to lines that referenced ``capex_per_simulated_period`` and
    ``device_co2_footprint_per_simulated_period`` which were only defined in the ``if`` branch,
    causing a ``NameError``. The fix adds an early ``return capex_cost_data_class`` in the
    ``else`` branch so that the base capex data (computed from ``lifetime_in_years``) is
    returned unchanged.

    Expected behaviour: the returned ``CapexCostDataClass`` has all fields populated from the
    ``compute_capex_costs_and_emissions`` helper (scaled by simulation duration, rounded to 2 dp).
    It must NOT have zero/None per-simulated-period fields, which would indicate the return
    statement was accidentally removed.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # Set Advanced Battery with lifetime_in_cycles = 0
    system_id = "SG1"  # Generic ac coupled battery storage system
    p_inv_custom = 5000  # W
    e_bat_custom = 10  # kWh
    name = "Battery"
    source_weight = 1
    charge_in_kwh = 0
    discharge_in_kwh = 0
    co2_footprint = e_bat_custom * 130.7
    cost = e_bat_custom * 535.81
    lifetime = 10
    lifetime_in_cycles = 0  # This should trigger the warning path
    maintenance_costs_in_euro_per_year = 0.02 * cost
    subsidy_as_percentage_of_investment_costs = 0.0

    my_advanced_battery_config = advanced_battery_bslib.BatteryConfig(
        system_id=system_id,
        custom_pv_inverter_power_generic_in_watt=p_inv_custom,
        custom_battery_capacity_generic_in_kilowatt_hour=e_bat_custom,
        component_id=ComponentID(name=name),
        source_weight=source_weight,
        charge_in_kwh=charge_in_kwh,
        discharge_in_kwh=discharge_in_kwh,
        device_co2_footprint_in_kg=co2_footprint,
        investment_costs_in_euro=cost,
        lifetime_in_years=lifetime,
        lifetime_in_cycles=lifetime_in_cycles,
        maintenance_costs_in_euro_per_year=maintenance_costs_in_euro_per_year,
        subsidy_as_percentage_of_investment_costs=subsidy_as_percentage_of_investment_costs,
    )

    # Call get_cost_capex - should not raise NameError
    result = advanced_battery_bslib.Battery.get_cost_capex(
        config=my_advanced_battery_config, simulation_parameters=my_simulation_parameters
    )

    # Result should be a CapexCostDataClass
    assert result is not None

    # Verify base fields are set correctly (values are rounded to 2 decimal places by the helper)
    assert result.capex_investment_cost_in_euro == pytest.approx(round(cost, 2), rel=1e-9)
    assert result.device_co2_footprint_in_kg == pytest.approx(round(co2_footprint, 2), rel=1e-9)
    assert result.lifetime_in_years == pytest.approx(round(lifetime, 2), rel=1e-9)

    # Verify per-simulated-period fields are sensible (derived from lifetime_in_years, not zeroed).
    # compute_capex_costs_and_emissions scales by (simulation_duration / seconds_per_year).
    # For a 1-day simulation starting Jan 1, duration.total_seconds() == 86400 exactly,
    # so the scale factor is 86400 / 31536000 == 1/365.
    seconds_per_year = 365 * 24 * 60 * 60
    scale_factor = my_simulation_parameters.duration.total_seconds() / seconds_per_year
    expected_capex_per_simulated_period = round(cost / lifetime * scale_factor, 2)
    expected_co2_per_simulated_period = round(co2_footprint / lifetime * scale_factor, 2)

    assert result.capex_investment_cost_for_simulated_period_in_euro == pytest.approx(
        expected_capex_per_simulated_period, rel=1e-9
    ), (
        f"Expected capex_per_simulated_period to be {expected_capex_per_simulated_period}, "
        f"got {result.capex_investment_cost_for_simulated_period_in_euro}"
    )
    assert result.device_co2_footprint_for_simulated_period_in_kg == pytest.approx(
        expected_co2_per_simulated_period, rel=1e-9
    ), (
        f"Expected co2_footprint_per_simulated_period to be {expected_co2_per_simulated_period}, "
        f"got {result.device_co2_footprint_for_simulated_period_in_kg}"
    )


#: The array the rooftop PV law builds on the fleet's archetype roof of 168.9 m2. Every battery
#: of the eleven building sizers is sized from exactly this number, so it is what the two laws
#: below are pinned against.
FLEET_PV_PEAK_POWER_IN_WATT = 22272.28


@pytest.mark.base
def test_the_sized_to_pv_preset_sizes_both_power_numbers_from_the_pv_peak_power() -> None:
    """Test that the preset reproduces the fleet's battery from the array's peak power.

    The two laws are the arithmetic the deleted ``get_scaled_battery`` factory performed: one
    kilowatt hour of storage per kilowatt peak, and a C-rate of 0.5 on the array's peak power.
    On the archetype roof the fleet's eleven building sizers stand on this is 22.27 kWh behind
    an 11 136.14 W inverter, which is the pair every recorded twin of those setups carries.
    """
    config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
        SizingContext(pv_peak_power_in_watt=(FLEET_PV_PEAK_POWER_IN_WATT,))
    )

    assert config.custom_battery_capacity_generic_in_kilowatt_hour == 22.27
    assert config.custom_pv_inverter_power_generic_in_watt == 11136.14


@pytest.mark.base
def test_the_inverter_law_reads_the_fact_and_not_the_rounded_capacity() -> None:
    """Test that the inverter power is derived from the array, not from the stored capacity.

    The capacity is rounded to two decimals before it is stored, so multiplying the *stored*
    capacity by 500 W/kWh is not the same number as applying the C-rate to the array itself.
    On the fleet archetype the difference is 1.14 W -- invisible to a reader and far outside
    the golden suites' relative tolerance of 1e-9 -- so the law has to read the fact. This
    test pins the difference rather than only the result, so that a later rewrite of the law
    into a sibling read fails here with the reason spelled out.
    """
    config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
        SizingContext(pv_peak_power_in_watt=(FLEET_PV_PEAK_POWER_IN_WATT,))
    )

    from_the_stored_capacity = round(
        concrete(config.custom_battery_capacity_generic_in_kilowatt_hour) * 0.5 * 1e3, 2
    )
    assert from_the_stored_capacity == 11135.0
    assert config.custom_pv_inverter_power_generic_in_watt != from_the_stored_capacity


@pytest.mark.base
def test_the_sized_to_pv_preset_pins_the_device_and_leaves_the_size_open() -> None:
    """Test that the preset fixes the device's constants and nothing else.

    What the preset states is the bslib system it is, its place in the energy management
    hierarchy, its empty starting charge and its cycle life; what it deliberately does not
    state is how big it is. The two power numbers stay unresolved until a sizing context
    supplies the array's peak power, and the capex fields stay ``None`` so post-processing
    looks the device up in the cost database.
    """
    config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery")

    assert config.component_id == ComponentID(name="Battery")
    assert config.system_id == "SG1"
    assert config.source_weight == 1
    assert config.charge_in_kwh == 0
    assert config.discharge_in_kwh == 0
    assert config.lifetime_in_cycles == 5e3
    assert config.device_co2_footprint_in_kg is None
    assert config.investment_costs_in_euro is None
    assert config.lifetime_in_years is None
    assert config.maintenance_costs_in_euro_per_year is None
    assert config.subsidy_as_percentage_of_investment_costs is None
    assert set(auto_fields(config)) == {
        "custom_battery_capacity_generic_in_kilowatt_hour",
        "custom_pv_inverter_power_generic_in_watt",
    }


@pytest.mark.base
def test_a_battery_cannot_be_sized_without_an_array_beside_it() -> None:
    """Test that a context carrying no PV peak power is refused, naming the missing fact.

    The battery has no size of its own: both laws read ``pv_peak_power_in_watt``, which the PV
    configuration contributes. Resolving against a context without it has to fail loudly rather
    than leave a battery of zero capacity in the system, which would divide by zero in the
    aging calculation and price a device that is not there.
    """
    with pytest.raises(ConfigSizingError, match="pv_peak_power_in_watt"):
        advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(SizingContext())


class TestTheChargingLimits:
    """The battery publishes, from its state at the step's start, the largest and smallest power it charges with."""

    #: A generic bslib battery of 11.1 kWh with an inverter of 11.1 kW.
    PARAMETERS = advanced_battery_bslib.BslibChargingParameters(
        capacity_in_watt_hour=11100.0,
        rated_charging_power_in_watt=11100.0,
        minimum_charging_power_in_watt=202.353,
        recharge_threshold_state_of_charge=0.98,
    )

    #: State of charge, whether the recharge hysteresis blocks, step length in s, and the maximum in W.
    MAXIMA = [
        (0.0, False, 60, 11100.0),
        (0.5, False, 60, 11100.0),
        (0.999, False, 60, 666.0),
        (0.999, False, 900, 0.0),
        (0.995, False, 900, 222.0),
        (1.0, False, 60, 0.0),
        (1.0000001, False, 60, 0.0),
        (0.99, True, 60, 0.0),
        (0.98, True, 60, 0.0),
        (0.97, True, 60, 11100.0),
        (0.97, False, 3600, 333.0),
    ]

    @pytest.mark.base
    @pytest.mark.parametrize(("state_of_charge", "blocked", "seconds_per_timestep", "expected_in_watt"), MAXIMA)
    def test_every_maximum_of_the_table(self, state_of_charge: float, blocked: bool, seconds_per_timestep: int, expected_in_watt: float) -> None:
        """Catches a maximum beyond the rated power or what fills the battery in the step, or one it refuses.

        The battery refuses any power while it is full and any power at or below its minimum charging power: 0.999 at
        900 s could fill at 44.4 W, below the minimum of 202.4 W, so the maximum is 0 W there.
        """
        maximum_in_watt = advanced_battery_bslib.Battery.maximum_charging_power_in_watt(
            self.PARAMETERS, state_of_charge=state_of_charge, recharging_is_blocked=blocked, seconds_per_timestep=seconds_per_timestep
        )
        assert maximum_in_watt == pytest.approx(expected_in_watt)

    @pytest.mark.base
    def test_a_step_length_that_is_not_positive_is_refused(self) -> None:
        """Catches a zero step length turning into an infinite filling power."""
        with pytest.raises(ValueError, match="positive"):
            advanced_battery_bslib.Battery.maximum_charging_power_in_watt(
                self.PARAMETERS, state_of_charge=0.5, recharging_is_blocked=False, seconds_per_timestep=0
            )

    @pytest.mark.base
    def test_the_adapter_reads_the_bslib_battery(self) -> None:
        """Catches an adapter that reads the wrong bslib attributes: SG1 scales its minimum with the inverter power."""
        battery = battery_with_set_point_input()[0]
        assert battery.charging_parameters.capacity_in_watt_hour == 10000.0
        assert battery.charging_parameters.rated_charging_power_in_watt == 5000.0
        assert battery.charging_parameters.minimum_charging_power_in_watt == pytest.approx(18.23 * 5)
        assert battery.charging_parameters.recharge_threshold_state_of_charge == 0.98

    @pytest.mark.base
    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("capacity_in_watt_hour", float("nan")),
            ("rated_charging_power_in_watt", float("inf")),
            ("minimum_charging_power_in_watt", float("nan")),
            ("minimum_charging_power_in_watt", -1.0),
            ("recharge_threshold_state_of_charge", -0.5),
        ],
    )
    def test_a_parameter_that_is_not_finite_or_is_negative_is_refused(self, field_name: str, value: float) -> None:
        """Catches a NaN bound that would reach the energy manager in the first step instead of failing construction."""
        with pytest.raises(ValueError, match=rf"{field_name} must be a finite number of at least 0, got {value!r}"):
            dataclasses.replace(self.PARAMETERS, **{field_name: value})

    @pytest.mark.base
    def test_a_dc_coupled_bslib_system_is_refused_when_the_battery_is_built(self) -> None:
        """S3 gives NaN charging powers; the battery refuses it at construction, naming the system and the field."""
        config = advanced_battery_bslib.BatteryConfig(
            component_id=ComponentID(name="Battery"),
            system_id="S3",
            custom_pv_inverter_power_generic_in_watt=5000.0,
            custom_battery_capacity_generic_in_kilowatt_hour=10.0,
        )
        with pytest.raises(ValueError, match=r"bslib system 'S3' .*rated_charging_power_in_watt .* got nan"):
            advanced_battery_bslib.Battery(
                config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
            )

    @pytest.mark.base
    def test_a_battery_offered_its_maximum_takes_it(self) -> None:
        """Catches a published maximum the battery does not take: the energy manager's share would then be wrong."""
        battery, set_point, stsv = battery_with_set_point_input()
        for state_of_charge in (0.1, 0.9995):
            battery.state = advanced_battery_bslib.BatteryState(state_of_charge=state_of_charge)
            battery.i_save_state()
            battery.i_restore_state()
            battery.i_simulate(0, stsv, False)
            maximum_in_watt = stsv.values[battery.maximum_charging_power_channel.global_index]
            stsv.values[set_point.global_index] = maximum_in_watt + 1000.0
            battery.i_restore_state()
            battery.i_simulate(0, stsv, False)
            assert stsv.values[battery.ac_battery_power_channel.global_index] == pytest.approx(maximum_in_watt)

    @pytest.mark.base
    def test_a_battery_that_could_take_no_more_than_its_minimum_publishes_0_watt_and_takes_nothing(self) -> None:
        """Catches a published maximum below the minimum, a power the battery refuses.

        At a state of charge of 0.99985 the 10 kWh battery has 1.5 Wh left, which fill within 60 s at 90 W, below its
        minimum of 91.15 W. bslib then takes nothing even when offered 1,000 W, so the published maximum is 0 W.
        """
        battery, set_point, stsv = battery_with_set_point_input()
        battery.state = advanced_battery_bslib.BatteryState(state_of_charge=0.99985)
        stsv.values[set_point.global_index] = 1000.0
        battery.i_simulate(0, stsv, False)
        assert stsv.values[battery.maximum_charging_power_channel.global_index] == 0.0
        assert stsv.values[battery.ac_battery_power_channel.global_index] == 0.0

    @pytest.mark.base
    def test_the_battery_is_safe_to_iterate(self) -> None:
        """Catches limits that depend on an earlier pass of the step or on the set point of the pass.

        The iteration-safety test: save, run passes with other set points, restore and run with set point X; every
        output must equal that of a fresh save followed by one pass with X. The state of charge stays away from full,
        where bslib's own recharge hysteresis changes from pass to pass.
        """

        def outputs_after(earlier_set_points_in_watt: List[float]) -> List[float]:
            battery, set_point, stsv = battery_with_set_point_input()
            battery.state = advanced_battery_bslib.BatteryState(state_of_charge=0.5)
            battery.i_save_state()
            for set_point_in_watt in earlier_set_points_in_watt:
                stsv.values[set_point.global_index] = set_point_in_watt
                battery.i_restore_state()
                battery.i_simulate(0, stsv, False)
            stsv.values[set_point.global_index] = 1200.0
            battery.i_restore_state()
            battery.i_simulate(0, stsv, False)
            return list(stsv.values)

        assert outputs_after([4000.0, -3000.0, 50.0]) == outputs_after([])


def battery_with_set_point_input() -> Tuple[advanced_battery_bslib.Battery, cp.ComponentOutput, cp.SingleTimeStepValues]:
    """Build a generic 10 kWh, 5 kW battery at 60 s steps whose set point a stand-in output feeds.

    Returns:
        The battery, the set-point output and the step values.
    """
    parameters = SimulationParameters.one_day_only(2021, 60)
    config = advanced_battery_bslib.BatteryConfig(
        component_id=ComponentID(name="Battery"),
        custom_pv_inverter_power_generic_in_watt=5000.0,
        custom_battery_capacity_generic_in_kilowatt_hour=10.0,
    )
    battery = advanced_battery_bslib.Battery(config=config, my_simulation_parameters=parameters)
    set_point = cp.ComponentOutput(
        "SetPoint", "LoadingPowerInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, component_id=ComponentID("SetPoint")
    )
    battery.loading_power_input_channel.source_output = set_point
    fft.add_global_index_of_components([battery, set_point])
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([battery, set_point]))
    return battery, set_point, stsv
