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


def main() -> None:
    """Parse args, build the simulation from JSON, force the result dir, and run."""
    parser = argparse.ArgumentParser(description="Run a single HiSim simulation (HPC harness worker).")
    parser.add_argument("--scenario", required=True, help="Path to the *.scenario.json file.")
    parser.add_argument("--sim-params", required=True, dest="sim_params",
                        help="Path to the *.simulation.json file.")
    parser.add_argument("--result-dir", required=True, dest="result_dir",
                        help="Directory this simulation must write its results into.")
    args: argparse.Namespace = parser.parse_args()

    # Suppress noisy third-party warnings, matching hisim_main's CLI behaviour.
    warnings.filterwarnings("ignore")

    # Imported here (not at module load) so the harness CLI works without a full
    # HiSim import and so each subprocess starts from a clean interpreter.
    from hisim.hisim_main import initialize_from_json, run_simulation  # pylint: disable=import-outside-toplevel

    my_sim = initialize_from_json(
        scenario=args.scenario,
        simulation_parameters=args.sim_params,
        path_to_module=args.scenario,
        delta=None,
    )

    # JSON mode otherwise auto-derives a result path next to the scenario file
    # (see result_path_provider.py). Force the harness-assigned per-task directory;
    # Simulator.prepare_simulation_directory honours a non-empty result_directory.
    my_sim.get_simulation_parameters().result_directory = args.result_dir

    run_simulation(my_sim, path_to_module=args.scenario)


if __name__ == "__main__":
    main()
