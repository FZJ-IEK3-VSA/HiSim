"""Shared, behavior-preserving helpers for the system-setup smoke tests.

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

``run_setup_and_assert_artifacts`` additionally encapsulates the *orchestration*
of such a run -- the stale-config cleanup, a default one-day
:class:`SimulationParameters` with CSV export enabled, the ``hisim_main.main``
call, and the CSV-artifact assertions -- so that a setup-specific test reduces
to a thin call passing only its setup path. This separates the "which setup"
concern (the path) from the "how to run and verify" concern (this helper), so
adding a new household setup that shares this contract no longer requires
duplicating the whole run-then-verify body.
"""
from __future__ import annotations

import os
from pathlib import Path

from hisim import hisim_main, log
from hisim.postprocessingoptions import PostProcessingOptions
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


def run_setup_and_assert_artifacts(
    setup_path: str,
    sim_params: SimulationParameters | None = None,
) -> SimulationParameters:
    """Run a system setup through ``hisim_main.main`` and assert it produced CSV artifacts.

    Encapsulates the run-then-verify sequence shared by the ``test_basic_household``
    smoke tests that enable CSV export. It removes a stale config file an earlier run
    may have left in the working directory, builds a default one-day
    :class:`SimulationParameters` (with ``MAKE_NETWORK_CHARTS`` and ``EXPORT_TO_CSV``
    enabled) when none is supplied, runs the setup, and asserts that the simulator
    populated a result directory, wrote the ``finished.flag`` completion marker, and
    exported at least one ``.csv`` result file.

    Passing ``sim_params`` overrides the defaults; the caller is then responsible for
    enabling ``EXPORT_TO_CSV`` on the custom instance so that the ``.csv`` assertion
    can succeed.

    Args:
        setup_path: path to the system-setup ``.py`` file, as accepted by
            :func:`hisim.hisim_main.main` (relative to the current working directory,
            e.g. ``../system_setups/<setup>.py``).
        sim_params: optional :class:`SimulationParameters` to run with. When ``None``,
            a one-day profile (``year=2019``, ``seconds_per_timestep=60``) with
            ``MAKE_NETWORK_CHARTS`` and ``EXPORT_TO_CSV`` appended is used.

    Returns:
        The :class:`SimulationParameters` instance, with ``result_directory``
        populated by the simulator.

    Raises:
        AssertionError: if the simulator did not set a result directory, the
            directory does not exist, it lacks the ``finished.flag`` completion
            marker, or no ``.csv`` result files were written.
    """
    # Remove a stale config file an earlier run may have left in the cwd. The name
    # mirrors the setup file name with a ``.json`` suffix, matching the historical
    # artifact these setups wrote before the config write was disabled.
    config_filename = f"{Path(setup_path).name}.json"
    Path(config_filename).unlink(missing_ok=True)

    if sim_params is None:
        sim_params = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=60)
        sim_params.post_processing_options.append(PostProcessingOptions.MAKE_NETWORK_CHARTS)
        # one_day_only starts with no post-processing options, so CSV export must be
        # enabled explicitly for the ``*.csv`` result artifacts asserted on below.
        sim_params.post_processing_options.append(PostProcessingOptions.EXPORT_TO_CSV)

    hisim_main.main(setup_path, sim_params)
    log.information(os.getcwd())

    assert_run_produced_outputs(sim_params)
    result_directory = Path(sim_params.result_directory)
    assert any(result_directory.glob("*.csv")), (
        f"no CSV result files were written to the result directory: {result_directory}"
    )
    return sim_params
