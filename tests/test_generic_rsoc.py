"""Tests for the generic reversible Solid Oxide Cell (rSOC) component.

This module verifies the behavior of the generic_rsoc component which models
both SOEC (Solid Oxide Electrolysis Cell) for hydrogen production and SOFC
(Solid Oxide Fuel Cell) for power generation modes.
"""

# clean
import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components import generic_rsoc
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_rsoc():
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
