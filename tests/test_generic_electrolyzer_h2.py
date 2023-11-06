"""Test for generic electrolyzer h2."""
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_electrolyzer_h2
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_electrolyzer():
    """Test electrolyzer."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2021, seconds_per_timestep
    )

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
        name=name,
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
        "FakeLoadInput", "LoadInput", lt.LoadTypes.ELECTRICITY, lt.Units.KILOWATT
    )

    input_state = cp.ComponentOutput(
        "FakeInputState", "InputState", lt.LoadTypes.ACTIVATION, lt.Units.ANY
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
        assert (
            stsv.values[my_electrolyzer.hydrogen_flow_rate.global_index] == 0.621840650119573
        )

    # python -m pytest ../tests/test_generic_electrolyzer_h2.py
