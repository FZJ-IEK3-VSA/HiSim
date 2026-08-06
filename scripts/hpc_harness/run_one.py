"""Run exactly one HiSim simulation (the HiSimRunner core, spec §4.4).

Moved unchanged from ``hisim/hpc_harness/run_one.py``. Reuses ``hisim_main``'s JSON
pipeline but overrides the result directory so the harness controls where every job
writes its output. Also usable as a standalone subprocess entry point::

    python -m hpc_harness.run_one --scenario X.json --sim-params S.json --result-dir DIR
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

    Performs the ``init -> override result_directory -> run`` sequence every harness
    job executes. ``init_fn``/``run_fn`` are injectable seams (tests pass fakes to
    verify the override ordering without disk I/O); the defaults are the real
    ``hisim.hisim_main`` functions, imported lazily so importing this module never
    pulls in the full simulator.

    Returns the simulator object from ``init_fn`` so callers/tests can inspect
    ``simulator.get_simulation_parameters().result_directory``.
    """
    if init_fn is None or run_fn is None:
        from hisim.hisim_main import initialize_from_json, run_simulation  # pylint: disable=import-outside-toplevel
        if init_fn is None:
            init_fn = initialize_from_json
        if run_fn is None:
            run_fn = run_simulation

    simulator = init_fn(
        scenario=scenario_path,
        simulation_parameters=sim_params_path,
        path_to_module=scenario_path,
        delta=None,
    )

    # JSON mode otherwise auto-derives a result path next to the scenario file
    # (see result_path_provider.py). Force the harness-assigned per-job directory;
    # Simulator.prepare_simulation_directory honours a non-empty result_directory.
    simulator.get_simulation_parameters().result_directory = result_dir

    run_fn(simulator, path_to_module=scenario_path)

    return simulator


def main(
    argv: Optional[List[str]] = None,
    init_fn: Optional[Callable[..., Any]] = None,
    run_fn: Optional[Callable[..., None]] = None,
) -> None:
    """Parse args and delegate to :func:`run_single`."""
    parser = argparse.ArgumentParser(description="Run a single HiSim simulation (HPC harness).")
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
