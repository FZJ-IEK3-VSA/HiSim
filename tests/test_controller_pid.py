"""Tests for the PID controller and its extracted 5R1C thermal model / tuning collaborators."""

# clean

from typing import Iterator

import pytest

from hisim import component as cp
from hisim.components.controller_pid import (
    BuildingThermalModel5R1C,
    PIDController,
    PIDControllerConfig,
    compute_pi_gains,
)
from hisim.loadtypes import LoadTypes, Units
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID
from tests import functions_for_testing as fft

# 5R1C thermal coefficients used across the tests (W/K and J/K).
H_TR_W = 50.0
H_TR_MS = 300.0
H_TR_EM = 100.0
H_VE_ADJ = 40.0
H_TR_IS = 10.0
C_M = 1.5e7
SECONDS_PER_TIMESTEP = 60


def _build_thermal_model() -> BuildingThermalModel5R1C:
    """Constructs a thermal model with explicit coefficients (no singleton dependency)."""
    return BuildingThermalModel5R1C(
        h_tr_w=H_TR_W,
        h_tr_ms=H_TR_MS,
        h_tr_em=H_TR_EM,
        h_ve_adj=H_VE_ADJ,
        h_tr_is=H_TR_IS,
        c_m=C_M,
        seconds_per_timestep=SECONDS_PER_TIMESTEP,
    )


@pytest.fixture(autouse=True)
def populated_sim_repository() -> Iterator[None]:
    """Pre-populates the singleton with 5R1C coefficients (KB-5214) and resets it afterwards.

    Access goes through the public ``set_entry`` / ``reset`` accessors rather
    than the concrete ``my_dict`` storage, so the tests do not depend on the
    singleton's internal representation (DIP).  ``reset`` is used for both
    setup and teardown to guarantee a clean slate and prevent stale-state
    leakage across test runs (KB-5646).
    """
    repo = SingletonSimRepository()
    repo.reset()
    repo.set_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTGLAZING, entry=H_TR_W)
    repo.set_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEMS, entry=H_TR_MS)
    repo.set_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTOPAQUEEM, entry=H_TR_EM)
    repo.set_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONCOEFFICIENTVENTILLATION, entry=H_VE_ADJ)
    repo.set_entry(key=SingletonDictKeyEnum.THERMALTRANSMISSIONSURFACEINDOORAIR, entry=H_TR_IS)
    repo.set_entry(key=SingletonDictKeyEnum.THERMALCAPACITYENVELOPE, entry=C_M)
    yield
    repo.reset()


@pytest.mark.base
def test_building_thermal_model_identifies_gains_and_time_constant() -> None:
    """The extracted thermal model derives the same gains and time constant as the original god-method."""
    tm = _build_thermal_model()

    # State-space matrices are scaled by the deliberate 0.5 factor (KB-5213).
    assert tm.transition_matrix.shape == (1, 1)
    assert tm.selection_matrix.shape == (1, 6)

    assert tm.process_gain == pytest.approx(0.0011278195488721803)
    assert tm.t_out_gain == pytest.approx(0.9548872180451127)
    assert tm.phi_ia_gain == pytest.approx(0.0011278195488721803)
    assert tm.phi_st_gain == pytest.approx(0.005639097744360902)
    assert tm.phi_m_gain == pytest.approx(0.006729323308270676)
    assert tm.time_constant == 3348


@pytest.mark.base
def test_compute_pi_gains_returns_pole_placement_gains() -> None:
    """The extracted tuning function returns the PI gains computed via pole placement."""
    tm = _build_thermal_model()
    proportional_gain, integral_gain, derivative_gain = compute_pi_gains(tm)

    assert proportional_gain == pytest.approx(22875.446701181467)
    assert integral_gain == pytest.approx(227.60105749117776)
    assert derivative_gain == 0


@pytest.mark.base
def test_from_sim_repository_reads_coefficients() -> None:
    """from_sim_repository reads the 5R1C coefficients from the singleton (KB-5214)."""
    tm = BuildingThermalModel5R1C.from_sim_repository(SECONDS_PER_TIMESTEP)
    assert tm.h_tr_w == H_TR_W
    assert tm.h_tr_ms == H_TR_MS
    assert tm.h_tr_em == H_TR_EM
    assert tm.h_ve_adj == H_VE_ADJ
    assert tm.h_tr_is == H_TR_IS
    assert tm.c_m == C_M
    assert tm.process_gain == pytest.approx(0.0011278195488721803)


@pytest.mark.base
def test_from_sim_repository_raises_when_coefficients_missing() -> None:
    """Missing coefficients must raise loudly instead of degrading silently (KB-5214)."""
    repo = SingletonSimRepository()
    repo.reset()
    try:
        with pytest.raises(KeyError):
            BuildingThermalModel5R1C.from_sim_repository(SECONDS_PER_TIMESTEP)
    finally:
        repo.reset()


@pytest.mark.base
def test_pid_controller_tunes_from_thermal_model() -> None:
    """The PIDController class delegates tuning to the thermal model and compute_pi_gains."""
    simulation_parameters = SimulationParameters.one_day_only(2017, SECONDS_PER_TIMESTEP)
    controller = PIDController(
        my_simulation_parameters=simulation_parameters,
        config=PIDControllerConfig.get_default_config(),
    )

    assert controller.proportional_gain == pytest.approx(22875.446701181467)
    assert controller.integral_gain == pytest.approx(227.60105749117776)
    assert controller.derivative_gain == 0
    assert controller.thermal_model.process_gain == pytest.approx(0.0011278195488721803)
    assert controller.thermal_model.phi_st_gain == pytest.approx(0.005639097744360902)
    assert controller.thermal_model.phi_m_gain == pytest.approx(0.006729323308270676)


@pytest.mark.base
def test_pid_controller_feedforward() -> None:
    """The feedforward method uses the thermal model disturbance gains."""
    simulation_parameters = SimulationParameters.one_day_only(2017, SECONDS_PER_TIMESTEP)
    controller = PIDController(
        my_simulation_parameters=simulation_parameters,
        config=PIDControllerConfig.get_default_config(),
    )
    feed_forward_signal = controller.feedforward(phi_st=100.0, phi_m=200.0)
    assert feed_forward_signal == pytest.approx(-1693.3333333333333)


def _build_controller_with_inputs(
    simulation_parameters: SimulationParameters,
) -> tuple[PIDController, cp.SingleTimeStepValues, cp.ComponentOutput, cp.ComponentOutput, cp.ComponentOutput]:
    """Builds a PIDController with fake input channels wired via the direct-drive seam (KB-4557)."""
    controller = PIDController(
        my_simulation_parameters=simulation_parameters,
        config=PIDControllerConfig.get_default_config(),
    )
    fake_temperature = cp.ComponentOutput(
        "FakeBuilding",
        "TemperatureMean",
        LoadTypes.TEMPERATURE,
        Units.CELSIUS,
        component_id=ComponentID("FakeBuilding"),
    )
    fake_phi_st = cp.ComponentOutput(
        "FakeBuilding", "HeatFluxWallNode", LoadTypes.HEATING, Units.WATT, component_id=ComponentID("FakeBuilding")
    )
    fake_phi_m = cp.ComponentOutput(
        "FakeBuilding",
        "HeatFluxThermalMassNode",
        LoadTypes.HEATING,
        Units.WATT,
        component_id=ComponentID("FakeBuilding"),
    )
    controller.temperature_mean_channel.source_output = fake_temperature
    controller.heat_flow_rate_to_internal_surface_node_channel.source_output = fake_phi_st
    controller.heat_flow_rate_to_internal_mass_node_channel.source_output = fake_phi_m
    components = [fake_temperature, fake_phi_st, fake_phi_m, controller]
    fft.add_global_index_of_components(components)
    stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs(components))
    return controller, stsv, fake_temperature, fake_phi_st, fake_phi_m


@pytest.mark.base
def test_pid_controller_simulate_heating_scenario() -> None:
    """i_simulate drives the PI loop and produces the expected outputs."""
    simulation_parameters = SimulationParameters.one_day_only(2017, SECONDS_PER_TIMESTEP)
    controller, stsv, fake_temperature, fake_phi_st, fake_phi_m = _build_controller_with_inputs(simulation_parameters)
    stsv.values[fake_temperature.global_index] = 20.0
    stsv.values[fake_phi_st.global_index] = 100.0
    stsv.values[fake_phi_m.global_index] = 200.0

    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=False)

    # set_point = 24.0, building_temperature = 20.0  ->  error = 4.0
    assert stsv.values[controller.error_output_channel.global_index] == pytest.approx(4.0)
    assert stsv.values[controller.error_pvalue_output_channel.global_index] == pytest.approx(91501.78680472587)
    assert stsv.values[controller.error_ivalue_output_channel.global_index] == pytest.approx(910.404229964711)
    assert stsv.values[controller.error_dvalue_output_channel.global_index] == pytest.approx(0.0)
    assert stsv.values[controller.integrator_output_channel.global_index] == pytest.approx(4.0)
    assert stsv.values[controller.derivator_output_channel.global_index] == pytest.approx(4.0)
    assert stsv.values[controller.thermal_power_channel.global_index] == pytest.approx(92412.19103469058)
    assert stsv.values[controller.feed_forward_signal_channel.global_index] == pytest.approx(-1693.3333333333333)


@pytest.mark.base
def test_pid_controller_force_convergence_skips_outputs() -> None:
    """force_convergence=True returns without updating outputs (KB-5215)."""
    simulation_parameters = SimulationParameters.one_day_only(2017, SECONDS_PER_TIMESTEP)
    controller, stsv, fake_temperature, fake_phi_st, fake_phi_m = _build_controller_with_inputs(simulation_parameters)
    stsv.values[fake_temperature.global_index] = 20.0
    stsv.values[fake_phi_st.global_index] = 100.0
    stsv.values[fake_phi_m.global_index] = 200.0

    controller.i_simulate(timestep=0, stsv=stsv, force_convergence=True)

    # No outputs should be written when convergence is forced.
    assert stsv.values[controller.thermal_power_channel.global_index] == 0.0
    assert stsv.values[controller.feed_forward_signal_channel.global_index] == 0.0
    assert stsv.values[controller.error_output_channel.global_index] == 0.0
