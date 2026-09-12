"""Unit tests for the HPC-harness run_one single-run entrypoint."""

import pytest

from hpc_harness import run_one

pytestmark = pytest.mark.hpcharness


# ------------------------------------------------------------------ run_one


class _FakeSimParams:
    """Minimal stand-in for SimulationParameters exposing a settable result_directory."""

    def __init__(self, result_directory):
        self.result_directory = result_directory


class _FakeSimulator:
    """Minimal stand-in for the Simulator that just hands back its parameters."""

    def __init__(self, result_directory):
        self.params = _FakeSimParams(result_directory)

    def get_simulation_parameters(self):
        """Return the fake simulation parameters."""
        return self.params


class _FakeBuiltSystem:
    """Minimal stand-in for the BuiltEnergySystem the executor returns."""

    def __init__(self, result_directory):
        self.simulator = _FakeSimulator(result_directory)


def test_run_single_hands_the_job_directory_to_the_executor():
    """run_single passes both files and the harness-assigned result dir to the executor."""
    calls = []

    def fake_run(energy_system_path, simulation_parameters_path, result_directory):
        """Fake run_fn: record the arguments and return a built system."""
        calls.append((energy_system_path, simulation_parameters_path, result_directory))
        return _FakeBuiltSystem(result_directory)

    built = run_one.run_single(
        "house.energy_system.yaml", "sim.simulation.yaml", "/results/000001", run_fn=fake_run
    )

    # The harness contract: the result directory is an argument of the run, not something
    # patched onto the parameters after the system was built.
    assert calls == [("house.energy_system.yaml", "sim.simulation.yaml", "/results/000001")]
    assert built.simulator.get_simulation_parameters().result_directory == "/results/000001"


def test_main_forwards_the_command_line_to_run_single():
    """The standalone entry point maps its three flags onto the executor's arguments."""
    calls = []

    def fake_run(energy_system_path, simulation_parameters_path, result_directory):
        """Fake run_fn: record the arguments the CLI resolved."""
        calls.append((energy_system_path, simulation_parameters_path, result_directory))
        return _FakeBuiltSystem(result_directory)

    run_one.main(
        [
            "--energy-system", "house.energy_system.yaml",
            "--sim-params", "sim.simulation.json",
            "--result-dir", "/results/000002",
        ],
        run_fn=fake_run,
    )

    assert calls == [("house.energy_system.yaml", "sim.simulation.json", "/results/000002")]
