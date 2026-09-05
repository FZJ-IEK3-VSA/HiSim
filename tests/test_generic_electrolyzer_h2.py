"""Tests for the generic electrolyzer component for hydrogen production.

This module contains tests for the generic_electrolyzer_h2 component, which simulates
green hydrogen production via electrolysis. Tests verify hydrogen flow rate calculations
based on electrical load input and activation state.
"""

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


def _build_electrolyzer() -> generic_electrolyzer_h2.Electrolyzer:
    """Construct the PEM electrolyzer of the tests above, for the KPI tests below."""
    config = generic_electrolyzer_h2.ElectrolyzerConfig(
        component_id=ComponentID(name="HTecME450"),
        electrolyzer_type="PEM",
        nom_load=987.0,
        max_load=1028.225,
        nom_h2_flow_rate=18.875,
        faraday_eff=0.999,
        i_cell_nom=2.0,
        ramp_up_rate=0.03,
        ramp_down_rate=0.25,
    )
    return generic_electrolyzer_h2.Electrolyzer(
        config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 60)
    )


@pytest.mark.base
def test_electrolyzer_kpi_entries_read_the_final_cumulative_values() -> None:
    """The three electrolyzer KPIs are the final values of its own cumulative outputs.

    The component integrates hydrogen, energy and operating time per timestep itself, so the KPI
    must read the last value rather than sum the column -- summing a cumulative series
    double-counts, which is exactly what a wrong implementation would do and what the hand-picked
    monotone series here would expose.
    """
    electrolyzer = _build_electrolyzer()
    outputs = [
        electrolyzer.total_hydrogen,
        electrolyzer.total_energy_consumed,
        electrolyzer.operating_time,
    ]
    frame = pd.DataFrame({0: [1.0, 2.5], 1: [40.0, 90.0], 2: [0.5, 1.25]})

    entries = {e.name: e for e in electrolyzer.get_component_kpi_entries(outputs, frame)}

    assert entries["Hydrogen produced"].value == pytest.approx(2.5)
    assert entries["Electrical energy consumed"].value == pytest.approx(90.0)
    assert entries["Operating time"].value == pytest.approx(1.25)
    assert all(isinstance(e.value, float) for e in entries.values()), (
        "a numpy scalar here would crash the KPI json writer"
    )


@pytest.mark.base
def test_electrolyzer_kpi_entries_refuse_a_missing_output() -> None:
    """A missing cumulative column raises naming the KPI instead of reporting nothing."""
    electrolyzer = _build_electrolyzer()

    with pytest.raises(ValueError, match="Hydrogen produced"):
        electrolyzer.get_component_kpi_entries([electrolyzer.total_energy_consumed], pd.DataFrame({0: [1.0]}))
