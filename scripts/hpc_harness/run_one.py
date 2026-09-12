"""Run exactly one HiSim simulation (the HiSimRunner core, spec §4.4).

Drives the declarative energy-system path: an ``*.energy_system.yaml`` file says what
the household is, an ``*.simulation.yaml`` or ``*.simulation.json`` file says what to do
with it, and the harness overrides the result directory so it controls where every job
writes its output. Also usable as a standalone subprocess entry point::

    python -m hpc_harness.run_one --energy-system X.energy_system.yaml \
        --sim-params S.simulation.yaml --result-dir DIR
"""

import argparse
import warnings
from typing import Any, Callable, List, Optional


def run_single(
    energy_system_path: str,
    sim_params_path: str,
    result_dir: str,
    *,
    run_fn: Optional[Callable[..., Any]] = None,
) -> Any:
    """Build an energy system from its two files, force the result dir onto it, and run it.

    ``run_fn`` is an injectable seam (tests pass a fake to verify the arguments without
    disk I/O); the default is :func:`hisim.energy_system.executor.run_energy_system`,
    imported lazily so importing this module never pulls in the full simulator. The
    executor takes the result directory as an argument rather than having it patched onto
    the parameters afterwards, so the harness-assigned per-job directory is in force
    before the first component is constructed.

    Returns the built system from ``run_fn`` so callers/tests can inspect
    ``built.simulator.get_simulation_parameters().result_directory``.
    """
    if run_fn is None:
        from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

        run_fn = run_energy_system

    return run_fn(
        energy_system_path=energy_system_path,
        simulation_parameters_path=sim_params_path,
        result_directory=result_dir,
    )


def main(
    argv: Optional[List[str]] = None,
    run_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """Parse args and delegate to :func:`run_single`."""
    parser = argparse.ArgumentParser(description="Run a single HiSim simulation (HPC harness).")
    parser.add_argument("--energy-system", required=True, dest="energy_system",
                        help="Path to the *.energy_system.yaml file.")
    parser.add_argument("--sim-params", required=True, dest="sim_params",
                        help="Path to the *.simulation.yaml or *.simulation.json file.")
    parser.add_argument("--result-dir", required=True, dest="result_dir",
                        help="Directory this simulation must write its results into.")
    args: argparse.Namespace = parser.parse_args(argv)

    # Suppress noisy third-party warnings, matching hisim_main's CLI behaviour.
    warnings.filterwarnings("ignore")

    run_single(
        energy_system_path=args.energy_system,
        sim_params_path=args.sim_params,
        result_dir=args.result_dir,
        run_fn=run_fn,
    )


if __name__ == "__main__":
    main()
