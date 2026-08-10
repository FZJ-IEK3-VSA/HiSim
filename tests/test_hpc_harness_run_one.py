"""Unit tests for the HPC-harness run_one single-run entrypoint."""

import pytest

from hpc_harness import run_one

pytestmark = pytest.mark.hpcharness


# ------------------------------------------------------------------ run_one (moved)


class _FakeSimParams:
    """Minimal stand-in for SimulationParameters exposing a settable result_directory."""

    def __init__(self):
        self.result_directory = ""


class _FakeSimulator:
    """Minimal stand-in for the Simulator that just hands back its parameters."""

    def __init__(self):
        self.params = _FakeSimParams()

    def get_simulation_parameters(self):
        """Return the fake simulation parameters."""
        return self.params


def test_run_single_overrides_result_dir_before_running():
    """run_single sets result_directory before invoking run_fn, in init-then-run order."""
    order = []
    simulator = _FakeSimulator()

    def fake_init(scenario, simulation_parameters, path_to_module, delta):  # pylint: disable=unused-argument
        """Fake init_fn: assert the scenario/param args and return the fake simulator."""
        order.append("init")
        assert scenario == "scn.json" and simulation_parameters == "sim.json"
        return simulator

    def fake_run(sim, path_to_module):
        """Fake run_fn: assert result_directory was set and the module path forwarded."""
        order.append("run")
        # The harness contract: result_directory is set BEFORE run_fn is invoked.
        assert sim.get_simulation_parameters().result_directory == "/results/000001"
        assert path_to_module == "scn.json"

    returned = run_one.run_single(
        "scn.json", "sim.json", "/results/000001", init_fn=fake_init, run_fn=fake_run
    )
    assert order == ["init", "run"]
    assert returned is simulator
