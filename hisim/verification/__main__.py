"""Run cases of the verification suite from the command line (FR14, the part step 3b provides).

    python -m hisim.verification --case 600 --timestep 3600 --output-dir runs/verification

The case files are read from ``$HISIM_VERIFICATION_DATA/cases/ashrae140``. Every (case, time step)
pair is run in two passes and written under ``<output-dir>/BESTEST_output``.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List

from hisim import log
from hisim.verification import runner, system_builder
from hisim.verification.case_loader import load_cases
from hisim.verification.suite import Suite

DATA_ENVIRONMENT_VARIABLE = "HISIM_VERIFICATION_DATA"
DEFAULT_TIMESTEPS = (3600, 900, 60)


def parse_arguments(arguments: List[str]) -> argparse.Namespace:
    """The command line: cases, time steps, output directory and year."""
    parser = argparse.ArgumentParser(prog="python -m hisim.verification", description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", default=None, help="case id, repeatable; default 600")
    parser.add_argument("--timestep", action="append", type=int, default=None, help="seconds per time step, repeatable; default 3600, 900 and 60")
    parser.add_argument("--output-dir", type=Path, required=True, help="where the runs and BESTEST_output go")
    parser.add_argument("--year", type=int, default=system_builder.DEFAULT_YEAR, help="the year the typical year is laid onto")
    return parser.parse_args(arguments)


def main(arguments: List[str]) -> int:
    """Run the requested cases and print one summary line per run."""
    options = parse_arguments(arguments)
    data_root = os.environ.get(DATA_ENVIRONMENT_VARIABLE)
    if not data_root:
        log.error(f"set {DATA_ENVIRONMENT_VARIABLE} to the private data folder holding cases/ashrae140")
        return 2
    cases = load_cases(Path(data_root) / "cases" / "ashrae140")
    suite = Suite.load("ashrae140")
    runner.write_guard_collects()
    for case_id in options.case or ["600"]:
        built = system_builder.build(cases[case_id], year=options.year)
        for seconds in options.timestep or DEFAULT_TIMESTEPS:
            settings = runner.RunSettings(year=options.year, seconds_per_timestep=seconds, output_directory=options.output_dir)
            run = runner.run_case(built, settings, suite.outputs_for(case_id))
            summary = run.summary
            air = summary["zone_air_temperature_in_celsius"]
            log.information(
                f"case {case_id} at {seconds} s: heating {summary.get('annual_heating_in_megawatt_hour', float('nan')):.3f} MWh, "
                f"cooling {summary.get('annual_sensible_cooling_in_megawatt_hour', float('nan')):.3f} MWh, "
                f"air {air['min']['value']:.1f} to {air['max']['value']:.1f} degC, "
                f"mass at the end of pass 1: {summary['passes'][0]['final_thermal_mass_temperature_in_celsius']:.2f} degC; "
                f"written to {run.files['summary']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
