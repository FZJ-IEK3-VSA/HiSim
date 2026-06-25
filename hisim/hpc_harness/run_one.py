"""Subprocess entry point: run exactly one HiSim simulation.

This is the new HiSim entry path used by the HPC harness. Each task runs in its own
fresh process for full isolation (a crash, hang or OOM here never touches the
managing node-agent). It reuses ``hisim_main``'s JSON pipeline but overrides the
result directory so the harness controls where every task writes its output.

Usage::

    python -m hisim.hpc_harness.run_one --scenario X.json --sim-params S.json --result-dir DIR
"""

import argparse
import warnings
from typing import Any, Callable, List, Optional


def run_single(
    scenario_path: str,
    sim_params_path: str,
    result_dir: str,
    *,
    init_fn: Optional[Callable[..., Any]] = None,
    run_fn: Optional[Callable[..., None]] = None,
) -> Any:
    """Build a simulation from JSON, force the result dir onto it, and run it.

    This is the harness-specific orchestration extracted from :func:`main`: it
    performs the ``init -> override result_directory -> run`` sequence that every
    harness worker executes. Splitting it out from argv parsing makes the
    ``result_dir`` override contract testable without JSON files, a real
    simulation, or any disk writes — callers pass fakes for ``init_fn``/``run_fn``
    and inspect the returned simulator.

    Args:
        scenario_path: Path to the ``*.scenario.json`` file. Also used as the
            ``path_to_module`` argument to both ``init_fn`` and ``run_fn``,
            matching the original ``main`` behaviour.
        sim_params_path: Path to the ``*.simulation.json`` file.
        result_dir: Directory this simulation must write its results into.
            Overrides the JSON-derived result directory (see
            ``result_path_provider.py``); ``Simulator.prepare_simulation_directory``
            honours a non-empty ``result_directory``.
        init_fn: Optional callable replacing
            :func:`hisim.hisim_main.initialize_from_json`. Defaults to the real
            implementation, imported lazily (only when this argument is ``None``)
            so importing this module never pulls in the full simulator. Tests pass
            a fake to verify the override ordering without disk I/O.
        run_fn: Optional callable replacing
            :func:`hisim.hisim_main.run_simulation`. Defaults to the real
            implementation, imported lazily. The harness contract is that
            ``result_directory`` is set to ``result_dir`` *before* ``run_fn`` is
            invoked; passing a fake lets a test assert that ordering.

    Returns:
        The simulator object returned by ``init_fn`` (after the result directory
        has been overridden and the simulation has been run). Exposed so tests can
        assert ``my_sim.get_simulation_parameters().result_directory`` without
        parsing argv or spinning up a real simulation.

    The defaults reproduce the original ``main`` behaviour exactly: the real
    ``initialize_from_json`` / ``run_simulation`` are imported lazily (only when a
    default is needed) and called in the same order with the same arguments.
    """
    # Imported here (not at module load) so the harness CLI works without a full
    # HiSim import and so each subprocess starts from a clean interpreter. The
    # import is skipped entirely when both functions are injected (e.g. by tests),
    # keeping the seam free of disk I/O and heavy computation.
    if init_fn is None or run_fn is None:
        from hisim.hisim_main import initialize_from_json, run_simulation  # pylint: disable=import-outside-toplevel
        if init_fn is None:
            init_fn = initialize_from_json
        if run_fn is None:
            run_fn = run_simulation

    my_sim = init_fn(
        scenario=scenario_path,
        simulation_parameters=sim_params_path,
        path_to_module=scenario_path,
        delta=None,
    )

    # JSON mode otherwise auto-derives a result path next to the scenario file
    # (see result_path_provider.py). Force the harness-assigned per-task directory;
    # Simulator.prepare_simulation_directory honours a non-empty result_directory.
    my_sim.get_simulation_parameters().result_directory = result_dir

    run_fn(my_sim, path_to_module=scenario_path)

    return my_sim


def main(
    argv: Optional[List[str]] = None,
    init_fn: Optional[Callable[..., Any]] = None,
    run_fn: Optional[Callable[..., None]] = None,
) -> None:
    """Parse args and delegate the init->override->run sequence to :func:`run_single`.

    Thin CLI wrapper: it parses the harness-worker command line and forwards to
    :func:`run_single`, threading the optional ``init_fn``/``run_fn`` seams through
    so the orchestration stays testable end to end.

    Args:
        argv: Optional list of command-line arguments; defaults to ``sys.argv``
            when ``None`` (matching :func:`hisim.hpc_harness.__main__.main`).
        init_fn: Optional callable replacing :func:`hisim.hisim_main.initialize_from_json`.
            Forwarded to :func:`run_single`; see its docstring.
        run_fn: Optional callable replacing :func:`hisim.hisim_main.run_simulation`.
            Forwarded to :func:`run_single`; see its docstring.

    The defaults reproduce the original behaviour exactly: the real
    ``initialize_from_json`` / ``run_simulation`` are imported lazily (only when a
    default is needed) and called in the same order with the same arguments.
    """
    parser = argparse.ArgumentParser(description="Run a single HiSim simulation (HPC harness worker).")
    parser.add_argument("--scenario", required=True, help="Path to the *.scenario.json file.")
    parser.add_argument("--sim-params", required=True, dest="sim_params",
                        help="Path to the *.simulation.json file.")
    parser.add_argument("--result-dir", required=True, dest="result_dir",
                        help="Directory this simulation must write its results into.")
    args: argparse.Namespace = parser.parse_args(argv)

    # Suppress noisy third-party warnings, matching hisim_main's CLI behaviour.
    warnings.filterwarnings("ignore")

    run_single(
        scenario_path=args.scenario,
        sim_params_path=args.sim_params,
        result_dir=args.result_dir,
        init_fn=init_fn,
        run_fn=run_fn,
    )


if __name__ == "__main__":
    main()
