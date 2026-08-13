"""Tests for the generic reversible Solid Oxide Cell (rSOC) component.

This module verifies the behavior of the generic_rsoc component which models
both SOEC (Solid Oxide Electrolysis Cell) for hydrogen production and SOFC
(Solid Oxide Fuel Cell) for power generation modes.
"""

# clean

# These tests deliberately read the private SOEC/SOFC load and efficiency
# arrays of the rSOC component to verify the simulated state.
# pylint: disable=protected-access

import json
from pathlib import Path

import numpy as np
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components import generic_rsoc
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_rsoc() -> None:
    """Test rSOC electrolyzer (SOEC) mode with ramp-up dynamics.

    Simulates the rSOC component with a -10 kW power input to activate
    SOEC mode. Verifies that hydrogen flow rates remain zero during the
    initial timestep due to the system's slow ramp-up rate.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )

    name: str = "rSOC1040kW"
    # SOEC
    nom_load_soec: float = 40.0
    min_load_soec: float = 2.315
    max_load_soec: float = 49.64
    faraday_eff_soec: float = 1.0
    ramp_up_rate_soec: float = 0.002841
    ramp_down_rate_soec: float = 0.002841
    # SOFC
    nom_power_sofc: float = 10.0
    min_power_sofc: float = 1.7
    max_power_sofc: float = 13.0
    faraday_eff_sofc: float = 1.0
    ramp_up_rate_sofc: float = 0.001538
    ramp_down_rate_sofc: float = 0.001538

    timestep = 1

    # ===================================================================================================================
    # Setup Electrolyzer
    my_rsoc_config = generic_rsoc.RsocConfig(
        building_name="BUI1",
        name=name,
        nom_load_soec=nom_load_soec,
        min_load_soec=min_load_soec,
        max_load_soec=max_load_soec,
        faraday_eff_soec=faraday_eff_soec,
        ramp_up_rate_soec=ramp_up_rate_soec,
        ramp_down_rate_soec=ramp_down_rate_soec,
        nom_power_sofc=nom_power_sofc,
        min_power_sofc=min_power_sofc,
        max_power_sofc=max_power_sofc,
        faraday_eff_sofc=faraday_eff_sofc,
        ramp_up_rate_sofc=ramp_up_rate_sofc,
        ramp_down_rate_sofc=ramp_down_rate_sofc,
    )
    my_rsoc = generic_rsoc.Rsoc(
        config=my_rsoc_config, my_simulation_parameters=my_simulation_parameters
    )

    # ===================================================================================================================
    # Set Fake Inputs
    power_input = cp.ComponentOutput(
        "FakePowerInput", "PowerInput", lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT
    )

    input_state_rsoc = cp.ComponentOutput(
        "FakeRSOCInputState", "RSOCInputState", lt.LoadTypes.ACTIVATION, lt.Units.ANY
    )

    number_of_outputs = fft.get_number_of_outputs([power_input, input_state_rsoc])

    my_rsoc.power_input.source_output = power_input
    my_rsoc.input_state_rsoc.source_output = input_state_rsoc

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([power_input, input_state_rsoc])

    stsv.values[power_input.global_index] = -10.0

    stsv.values[input_state_rsoc.global_index] = 1

    # Simulate
    my_rsoc.i_restore_state()
    my_rsoc.i_simulate(timestep, stsv, False)

    # Checking differnt values
    # These values are outputs of i_simulate, not constants assigned directly
    # to zero. Depending on platform/compiler/ramp-up arithmetic, the computed
    # result may be a tiny residual (e.g. 1e-16) rather than exactly 0.0, so a
    # tolerance-based check is used instead of exact equality.
    assert (
        abs(stsv.values[my_rsoc.soec_hydrogen_flow_rate.global_index]) < 1e-9
    )  # should be zero because the systems ramp up is slow
    assert abs(stsv.values[my_rsoc.sofc_hydrogen_flow_rate.global_index]) < 1e-9

    # python -m pytest ../tests/test_generic_rSOC.py


@pytest.mark.base
def test_rsoc_efficiency_cache_matches_file() -> None:
    """Efficiency-curve arrays cached in __init__ must be bit-identical to the JSON file.

    Regression guard for GitLab issue #1801: the per-timestep file read was replaced
    by a one-time load in __init__. The cached arrays must match the source file
    exactly so that np.interp produces identical outputs.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)

    for name in ("rSOC515kW", "rSOC1040kW"):
        config = generic_rsoc.RsocConfig(
            building_name="BUI1",
            name=name,
            nom_load_soec=40.0,
            min_load_soec=2.315,
            max_load_soec=49.64,
            faraday_eff_soec=1.0,
            ramp_up_rate_soec=0.002841,
            ramp_down_rate_soec=0.002841,
            nom_power_sofc=10.0,
            min_power_sofc=1.7,
            max_power_sofc=13.0,
            faraday_eff_sofc=1.0,
            ramp_up_rate_sofc=0.001538,
            ramp_down_rate_sofc=0.001538,
        )
        rsoc = generic_rsoc.Rsoc(
            config=config, my_simulation_parameters=my_simulation_parameters
        )

        data_file = Path(utils.HISIMPATH["inputs"]) / "rSOC_efficiency_curve_data.json"
        with open(data_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        np.testing.assert_array_equal(
            rsoc._soec_load_percentage, np.array(data[name]["load_percentage_soec"], dtype=float)
        )
        np.testing.assert_array_equal(
            rsoc._soec_sys_eff, np.array(data[name]["sys_eff_soec"], dtype=float)
        )
        np.testing.assert_array_equal(
            rsoc._sofc_load_percentage, np.array(data[name]["load_percentage_sofc"], dtype=float)
        )
        np.testing.assert_array_equal(
            rsoc._sofc_sys_eff, np.array(data[name]["sys_eff_sofc"], dtype=float)
        )


@pytest.mark.base
def test_rsoc_efficiency_interpolation_matches_direct_np_interp() -> None:
    """soec_efficiency / sofc_efficiency must match a fresh np.interp against the file.

    Ensures the cached hot path returns the same values as the previous per-call
    implementation that read the file every time.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    name = "rSOC1040kW"
    config = generic_rsoc.RsocConfig(
        building_name="BUI1",
        name=name,
        nom_load_soec=40.0,
        min_load_soec=2.315,
        max_load_soec=49.64,
        faraday_eff_soec=1.0,
        ramp_up_rate_soec=0.002841,
        ramp_down_rate_soec=0.002841,
        nom_power_sofc=10.0,
        min_power_sofc=1.7,
        max_power_sofc=13.0,
        faraday_eff_sofc=1.0,
        ramp_up_rate_sofc=0.001538,
        ramp_down_rate_sofc=0.001538,
    )
    rsoc = generic_rsoc.Rsoc(
        config=config, my_simulation_parameters=my_simulation_parameters
    )

    data_file = Path(utils.HISIMPATH["inputs"]) / "rSOC_efficiency_curve_data.json"
    with open(data_file, "r", encoding="utf-8") as file:
        data = json.load(file)

    soec_x = data[name]["load_percentage_soec"]
    soec_y = data[name]["sys_eff_soec"]
    sofc_x = data[name]["load_percentage_sofc"]
    sofc_y = data[name]["sys_eff_sofc"]

    # SOEC: in-range load -> interpolation; out-of-range -> 0.0
    for current_load in (2.315, 10.0, 25.0, 49.64):
        expected = float(np.interp(current_load / 49.64, soec_x, soec_y))
        assert rsoc.soec_efficiency(current_load, 2.315, 49.64) == expected
    # below min_load -> 0.0 (min_load <= current_load is False)
    assert rsoc.soec_efficiency(1.0, 2.315, 49.64) == 0.0
    # above max_load -> 0.0
    assert rsoc.soec_efficiency(60.0, 2.315, 49.64) == 0.0

    # SOFC: in-range demand -> interpolation; out-of-range -> 0.0
    for current_demand in (1.71, 5.0, 10.0, 13.0):
        expected = float(np.interp(current_demand / 13.0, sofc_x, sofc_y))
        assert rsoc.sofc_efficiency(current_demand, 1.7, 13.0) == expected
    # at min_power (exclusive lower bound) -> 0.0
    assert rsoc.sofc_efficiency(1.7, 1.7, 13.0) == 0.0
    # above max_power -> 0.0
    assert rsoc.sofc_efficiency(20.0, 1.7, 13.0) == 0.0


@pytest.mark.base
def test_rsoc_invalid_name_raises_at_construction() -> None:
    """An unknown rSOC name must fail loudly at construction, not silently degrade.

    The efficiency curve is validated once in __init__; an invalid name must raise
    ValueError immediately rather than producing wrong/empty results at runtime.
    """
    my_simulation_parameters = SimulationParameters.one_day_only(2021, 60)
    config = generic_rsoc.RsocConfig(
        building_name="BUI1",
        name="nonexistent_rSOC",
        nom_load_soec=40.0,
        min_load_soec=2.315,
        max_load_soec=49.64,
        faraday_eff_soec=1.0,
        ramp_up_rate_soec=0.002841,
        ramp_down_rate_soec=0.002841,
        nom_power_sofc=10.0,
        min_power_sofc=1.7,
        max_power_sofc=13.0,
        faraday_eff_sofc=1.0,
        ramp_up_rate_sofc=0.001538,
        ramp_down_rate_sofc=0.001538,
    )
    with pytest.raises(ValueError, match="nonexistent_rSOC"):
        generic_rsoc.Rsoc(
            config=config, my_simulation_parameters=my_simulation_parameters
        )
