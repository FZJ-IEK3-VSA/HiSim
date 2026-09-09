"""Tests for the generic electrolyzer component for hydrogen production.

This module contains tests for the generic_electrolyzer_h2 component, which simulates
green hydrogen production via electrolysis. Tests verify hydrogen flow rate calculations
based on electrical load input and activation state.
"""

import json

import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_electrolyzer_h2
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID
from tests import functions_for_testing as fft


@pytest.mark.base
def test_electrolyzer() -> None:
    """Verify hydrogen flow rate output of the generic electrolyzer under a fixed electrical load.

    Constructs an `Electrolyzer` with a PEM configuration (nominal load 987 kW, max load
    ~1028 kW, nominal H2 flow rate 18.875 kg/h), feeds a fake electrical load of 850.6 kW
    and an activation state of 1, then asserts that the produced hydrogen flow rate matches
    the expected value (~0.6218 kg/h) when the electrolyzer is active, and is zero when the
    activation state indicates off or standby.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)

    name: str = "HTecME450"
    electrolyzer_type: str = "PEM"
    nom_load: float = 987.0  # [kW]
    max_load: float = 1028.225  # [kW]
    nom_h2_flow_rate: float = 18.875  # [kg/h]
    faraday_eff: float = 0.999
    i_cell_nom: float = 2.0  # [A/cm^2]
    ramp_up_rate: float = 0.03  # [%/s]
    ramp_down_rate: float = 0.25  # [%/s]

    timestep = 1

    # ===================================================================================================================
    # Setup Electrolyzer
    my_electrolyzer_config = generic_electrolyzer_h2.ElectrolyzerConfig(
        component_id=ComponentID(name=name),
        electrolyzer_type=electrolyzer_type,
        nom_load=nom_load,
        max_load=max_load,
        nom_h2_flow_rate=nom_h2_flow_rate,
        faraday_eff=faraday_eff,
        i_cell_nom=i_cell_nom,
        ramp_up_rate=ramp_up_rate,
        ramp_down_rate=ramp_down_rate,
    )
    my_electrolyzer = generic_electrolyzer_h2.Electrolyzer(
        config=my_electrolyzer_config, my_simulation_parameters=my_simulation_parameters
    )

    # ===================================================================================================================
    # Set Fake Inputs
    load_input = cp.ComponentOutput(
        "FakeLoadInput",
        "LoadInput",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.KILOWATT,
        component_id=ComponentID("FakeLoadInput"),
    )

    input_state = cp.ComponentOutput(
        "FakeInputState",
        "InputState",
        lt.LoadTypes.ACTIVATION,
        lt.Units.ANY,
        component_id=ComponentID("FakeInputState"),
    )

    number_of_outputs = fft.get_number_of_outputs([load_input, input_state])

    my_electrolyzer.load_input.source_output = load_input
    my_electrolyzer.input_state.source_output = input_state

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([load_input, input_state])

    stsv.values[load_input.global_index] = 850.6

    stsv.values[input_state.global_index] = 1

    # Simulate
    my_electrolyzer.i_restore_state()
    my_electrolyzer.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Checking differnt values
    if stsv.values[input_state.global_index] == -1:
        assert stsv.values[my_electrolyzer.hydrogen_flow_rate.global_index] == 0

    elif stsv.values[input_state.global_index] == 0:
        assert stsv.values[my_electrolyzer.hydrogen_flow_rate.global_index] == 0

    else:
        assert stsv.values[my_electrolyzer.hydrogen_flow_rate.global_index] == pytest.approx(0.621840650119573)

    # python -m pytest ../tests/test_generic_electrolyzer_h2.py


def _build_config(nom_load: float = 987.0) -> generic_electrolyzer_h2.ElectrolyzerConfig:
    """Build the PEM configuration of the tests above, optionally at another rating.

    The rating is a parameter because the capex tests below assert that the cost scales with it,
    and everything else about the machine is held fixed so that the rating is the only thing that
    can explain a difference.

    Args:
        nom_load: nominal electrical load in kW, which is also what the investment cost scales by.

    Returns:
        ElectrolyzerConfig: the HTecME450 PEM configuration at that rating, cost fields unset.
    """
    return generic_electrolyzer_h2.ElectrolyzerConfig(
        component_id=ComponentID(name="HTecME450"),
        electrolyzer_type="PEM",
        nom_load=nom_load,
        max_load=1028.225,
        nom_h2_flow_rate=18.875,
        faraday_eff=0.999,
        i_cell_nom=2.0,
        ramp_up_rate=0.03,
        ramp_down_rate=0.25,
    )


def _build_electrolyzer() -> generic_electrolyzer_h2.Electrolyzer:
    """Construct the PEM electrolyzer of the tests above, for the KPI tests below."""
    return generic_electrolyzer_h2.Electrolyzer(
        config=_build_config(), my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
    )


@pytest.mark.base
def test_electrolyzer_kpi_entries_read_the_final_cumulative_values() -> None:
    """The three electrolyzer KPIs are the final values of its own cumulative outputs.

    The component integrates hydrogen, energy and operating time per timestep itself, so the KPI
    must read the last value rather than sum the column -- summing a cumulative series
    double-counts, which is exactly what a wrong implementation would do and what the hand-picked
    monotone series here would expose.

    A foreign component's column is prepended the way the real caller hands the whole run's
    outputs over: the component_name filter is what keeps this electrolyzer from reading another
    device's values, and the prepend also moves its own columns off index zero.
    """
    electrolyzer = _build_electrolyzer()
    foreign = cp.ComponentOutput(
        "PVSystem",
        "TotalEnergyConsumed",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.KWH,
        component_id=ComponentID(name="PVSystem"),
    )
    outputs = [
        foreign,
        electrolyzer.total_hydrogen,
        electrolyzer.total_energy_consumed,
        electrolyzer.operating_time,
    ]
    frame = pd.DataFrame({0: [7000.0, 8000.0], 1: [1.0, 2.5], 2: [40.0, 90.0], 3: [0.5, 1.25]})

    entries = {e.name: e for e in electrolyzer.get_component_kpi_entries(outputs, frame)}

    assert entries["Hydrogen produced"].value == pytest.approx(2.5)
    assert entries["Electrical energy consumed"].value == pytest.approx(90.0)
    assert entries["Operating time"].value == pytest.approx(1.25)
    assert all(e.name_of_source_component == electrolyzer.component_name for e in entries.values()), (
        "the source component is the disambiguator a future multi-instance collision fix keys on"
    )
    for entry in entries.values():
        json.dumps(entry.to_dict())  # the webtool writer serializes exactly this; it must not raise


@pytest.mark.base
def test_electrolyzer_kpi_entries_refuse_a_missing_output() -> None:
    """A missing cumulative column raises naming the KPI instead of reporting nothing."""
    electrolyzer = _build_electrolyzer()

    with pytest.raises(ValueError, match="Hydrogen produced"):
        electrolyzer.get_component_kpi_entries([electrolyzer.total_energy_consumed], pd.DataFrame({0: [1.0]}))


@pytest.mark.base
def test_electrolyzer_kpi_entries_refuse_nan_instead_of_misreading() -> None:
    """A NaN in a cumulative column raises instead of becoming the reported final value.

    The KPIs read the last row, so a NaN there would be reported verbatim as the indicator, and a
    NaN earlier in the series marks a column that was not written every timestep -- either way the
    value cannot be trusted, and a wrong number that looks real is worse than a loud refusal.
    """
    electrolyzer = _build_electrolyzer()
    outputs = [
        electrolyzer.total_hydrogen,
        electrolyzer.total_energy_consumed,
        electrolyzer.operating_time,
    ]
    frame = pd.DataFrame({0: [1.0, float("nan")], 1: [40.0, 90.0], 2: [0.5, 1.25]})

    with pytest.raises(ValueError, match="Hydrogen produced"):
        electrolyzer.get_component_kpi_entries(outputs, frame)


@pytest.mark.base
def test_electrolyzer_config_leaves_the_cost_fields_unset_by_default() -> None:
    """The five cost fields default to ``None``, which is what selects the database lookup.

    They are read as a set: postprocessing looks the figures up from the device database for the
    simulated year and country only while all five are ``None``. A default that accidentally
    carried a number would silently pin every electrolyzer in the fleet to it.
    """
    config = _build_config()
    assert config.device_co2_footprint_in_kg is None
    assert config.investment_costs_in_euro is None
    assert config.lifetime_in_years is None
    assert config.maintenance_costs_in_euro_per_year is None
    assert config.subsidy_as_percentage_of_investment_costs is None


@pytest.mark.base
def test_electrolyzer_capex_scales_with_the_nominal_load() -> None:
    """The investment cost, embodied CO2 and maintenance cost are all linear in ``nom_load``.

    Published electrolyzer costs are quoted per kilowatt, so doubling the rating has to double all
    three figures exactly. Asserting the ratio rather than the euros keeps the test meaningful when
    the owner revises the proposed price per kilowatt, while still catching a capex that ignores
    the rating -- the failure this cost model exists to prevent -- or one that scales by the wrong
    power of it.
    """
    mysim = SimulationParameters.one_day_only(2021, 60)
    small = generic_electrolyzer_h2.Electrolyzer.get_cost_capex(_build_config(nom_load=500.0), mysim)
    large = generic_electrolyzer_h2.Electrolyzer.get_cost_capex(_build_config(nom_load=1000.0), mysim)

    assert small.capex_investment_cost_in_euro > 0.0
    assert small.device_co2_footprint_in_kg > 0.0
    assert small.maintenance_costs_in_euro_per_year > 0.0
    assert 5.0 < small.lifetime_in_years < 30.0, "an electrolyzer system is a one- to two-decade asset"
    assert large.capex_investment_cost_in_euro == pytest.approx(2 * small.capex_investment_cost_in_euro)
    assert large.device_co2_footprint_in_kg == pytest.approx(2 * small.device_co2_footprint_in_kg)
    assert large.maintenance_costs_in_euro_per_year == pytest.approx(2 * small.maintenance_costs_in_euro_per_year)
    # The rating is stated in kilowatts, so the price per kilowatt has to land in the range the
    # database entry cites (1400-1800 EUR/kW) rather than being off by a factor of a thousand.
    assert 1400.0 <= small.capex_investment_cost_in_euro / 500.0 <= 1800.0


@pytest.mark.base
def test_electrolyzer_capex_prefers_explicit_config_values_over_the_database() -> None:
    """A configuration carrying all five cost fields is used verbatim, with no database lookup.

    This is the escape hatch for a specific quoted machine, and it is all-or-nothing: the helper
    consults the database only while all five fields are ``None``. Pinning it here keeps a future
    change to the shared helper from silently overriding a user's own numbers.
    """
    config = _build_config()
    config.device_co2_footprint_in_kg = 5000.0
    config.investment_costs_in_euro = 900000.0
    config.lifetime_in_years = 12.0
    config.maintenance_costs_in_euro_per_year = 20000.0
    config.subsidy_as_percentage_of_investment_costs = 0.25

    capex = generic_electrolyzer_h2.Electrolyzer.get_cost_capex(config, SimulationParameters.one_day_only(2021, 60))

    assert capex.capex_investment_cost_in_euro == pytest.approx(900000.0)
    assert capex.device_co2_footprint_in_kg == pytest.approx(5000.0)
    assert capex.lifetime_in_years == pytest.approx(12.0)
    assert capex.maintenance_costs_in_euro_per_year == pytest.approx(20000.0)
    assert capex.subsidy_as_percentage_of_investment_costs == pytest.approx(0.25)


@pytest.mark.base
def test_electrolyzer_opex_prices_the_energy_it_actually_consumed() -> None:
    """The operating cost is built from the machine's own cumulative consumption, plus maintenance.

    The consumption is the final value of ``TotalEnergyConsumed`` -- the load the machine was
    actually given, not its rating -- so a run that idled all day must cost almost nothing in
    energy however large the device is. Summing that cumulative column instead of reading its last
    value would double-count every earlier timestep, which the monotone series here would expose.
    """
    electrolyzer = _build_electrolyzer()
    outputs = [
        electrolyzer.total_hydrogen,
        electrolyzer.total_energy_consumed,
        electrolyzer.operating_time,
    ]
    frame = pd.DataFrame({0: [1.0, 2.5], 1: [40.0, 90.0], 2: [0.5, 1.25]})

    opex = electrolyzer.get_cost_opex(outputs, frame)

    assert opex.total_consumption_in_kwh == pytest.approx(90.0)
    assert opex.loadtype == lt.LoadTypes.ELECTRICITY
    # Both the cost and the footprint are those 90 kWh times a positive per-kWh factor, so each
    # must be a plausible per-kWh multiple rather than zero or a thousandfold miss.
    assert 0.0 < opex.opex_energy_cost_in_euro / 90.0 < 2.0
    assert 0.0 < opex.co2_footprint_in_kg / 90.0 < 2.0
    assert opex.opex_maintenance_cost_in_euro > 0.0


@pytest.mark.base
def test_electrolyzer_opex_refuses_a_missing_consumption_column() -> None:
    """A missing cumulative column raises naming the KPI instead of costing the run as free.

    The operating cost reads the same three columns the KPIs do, so an absent consumption column
    would otherwise be reported as zero euros -- a run that looks free rather than unmeasured.
    """
    electrolyzer = _build_electrolyzer()

    with pytest.raises(ValueError, match="Electrical energy consumed"):
        electrolyzer.get_cost_opex([electrolyzer.total_hydrogen], pd.DataFrame({0: [1.0]}))


@pytest.mark.base
def test_electrolyzer_config_refuses_a_non_positive_nominal_load() -> None:
    """A nominal load of zero or less is refused at construction, by value.

    The investment cost is the nominal load times a price per kilowatt, so an unrated machine
    would be costed at zero euros and reported as an answer -- a device that reads as free rather
    than as unsized. The configuration is where that stops.
    """
    for wrong in (0.0, -1.0):
        with pytest.raises(ValueError, match="nominal load"):
            _build_config(nom_load=wrong)


@pytest.mark.base
def test_electrolyzer_config_refuses_a_maximum_load_below_the_nominal_one() -> None:
    """A maximum load below the nominal one is refused at construction, by value.

    Such a machine cannot reach its own rating: the controller would distribute a load it can
    never be given, and the run would report plausible numbers for a device that cannot exist.
    """
    with pytest.raises(ValueError, match="maximum load"):
        # _build_config pins max_load at 1028.225 kW, so a nominal load above that inverts the two.
        _build_config(nom_load=2000.0)
