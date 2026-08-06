"""Shared, behavior-preserving assertions for the system-setup smoke tests.

The ``test_basic_household`` family of tests all share the same "the run actually
produced outputs" verification contract: after ``hisim_main.main`` returns (it
returns ``None`` and only raises on failure), they confirm the simulator
populated ``SimulationParameters.result_directory`` with the directory it wrote
to and that a ``finished.flag`` completion marker was left there at the end of
post-processing.

That contract is a single responsibility that is independent of *which* system
setup is being orchestrated, so it lives here instead of being duplicated in
every setup-specific test. Centralizing it means the completion contract can
evolve in one place rather than across a dozen near-identical test bodies.
"""
from __future__ import annotations

from pathlib import Path

from hisim.simulationparameters import SimulationParameters


def assert_run_produced_outputs(sim_params: SimulationParameters) -> None:
    """Assert that a finished ``hisim_main.main`` run left tangible output behind.

    ``hisim_main.main`` returns ``None`` and only raises on failure, so "it did
    not crash" is not sufficient: a run that silently produced no artifacts would
    still pass. The simulator instead records the directory it actually wrote to
    on the passed ``SimulationParameters`` instance (``result_directory``) and
    writes a ``finished.flag`` marker at the end of post-processing, so both are
    meaningful proof that the run completed and produced its expected output.

    Args:
        sim_params: the ``SimulationParameters`` instance that was handed to
            ``hisim_main.main``. The simulator mutates this same object in place
            to record ``result_directory``.

    Raises:
        AssertionError: if the simulator did not set a result directory, the
            directory does not exist, it is empty, or it lacks the
            ``finished.flag`` completion marker.

    Note:
        Do NOT re-query ``ResultPathProviderSingleton`` here. These households
        configure it for index-enumerated directories, so
        ``get_result_directory_name()`` returns the *next free* ``__N`` path on
        each call -- i.e. a different, non-existent directory once the run has
        created the one it used. The directory recorded on ``sim_params`` is the
        authoritative one.
    """
    assert sim_params.result_directory, "simulation did not set a result directory"
    results_path = Path(sim_params.result_directory)
    assert results_path.is_dir(), f"Results directory was not created: {results_path}"
    assert any(results_path.iterdir()), f"Results directory is empty: {results_path}"
    assert (results_path / "finished.flag").is_file(), (
        f"Simulation did not write its completion marker (finished.flag) in {results_path}."
    )
