"""Test for generic fuel cell."""
# clean
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_fuel_cell
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_electrolyzer():
    """Test electrolyzer."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2021, seconds_per_timestep)

    name: str = "NedstackFCS10XXL"
    type_electrolyzer: str = "PEM"
    nom_output: float = 10.3
    max_output: float = 8.81
    min_output: float = 0.0
    nom_h2_flow_rate: float = 0.647  # [kg/h]
    faraday_eff: float = 0.98
    i_cell_nom: float = 0.7
    ramp_up_rate: float = 0.1
    ramp_down_rate: float = 0.2

    timestep = 1

    # ===================================================================================================================
    # Setup Electrolyzer
    my_fuelcell_config = generic_fuel_cell.FuelCellConfig(
        name=name,
        type=type_electrolyzer,
        nom_output=nom_output,
        max_output=max_output,
        min_output=min_output,
        nom_h2_flow_rate=nom_h2_flow_rate,
        faraday_eff=faraday_eff,
        i_cell_nom=i_cell_nom,
        ramp_up_rate=ramp_up_rate,
        ramp_down_rate=ramp_down_rate,
    )
    my_fuelcell = generic_fuel_cell.FuelCell(config=my_fuelcell_config, my_simulation_parameters=my_simulation_parameters)

    # ===================================================================================================================
    # Set Fake Inputs
    demand_profile_target = cp.ComponentOutput(
        "FakeDemandProfile",
        "DemandProfile",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.KILOWATT,
    )

    control_signal = cp.ComponentOutput("FakeControlSignal", "ControlSignal", lt.LoadTypes.ANY, lt.Units.ANY)

    number_of_outputs = fft.get_number_of_outputs([demand_profile_target, control_signal])

    my_fuelcell.demand_profile_target.source_output = demand_profile_target
    my_fuelcell.control_signal.source_output = control_signal

    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([demand_profile_target, control_signal])

    stsv.values[demand_profile_target.global_index] = 7.6

    stsv.values[control_signal.global_index] = 1

    # Simulate
    my_fuelcell.i_restore_state()
    my_fuelcell.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Checking differnt values
    assert pytest.approx(stsv.values[my_fuelcell.current_hydrogen_demand.global_index]) == 0.3650165

    # python -m pytest ../tests/test_generic_fuel_cell.py
