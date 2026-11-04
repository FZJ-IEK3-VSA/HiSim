"""Run the cases of a verification suite and write the private report (FR14, spec §10, D23).

    python -m hisim.verification --output-dir <private folder>/runs
    python -m hisim.verification --case 600 --timestep 3600 --output-dir <private folder>/runs
    python -m hisim.verification --report-only --output-dir <private folder>/runs

The case files are read from ``$HISIM_VERIFICATION_DATA/cases/<suite>`` and the ranges from the
suite's reference file in that folder. Without ``--case`` every case of the suite runs, without
``--timestep`` at 3600, 900 and 60 s; each run is written under ``<output-dir>/BESTEST_output``.
The report is then built from every summary in that folder, so cases run one by one into the
same folder end up in one report; ``--report-only`` rebuilds it without running.

The report carries the private range bounds and statuses (D22), and the runs carry values derived
from the private case files (D19), so an output directory inside the HiSim checkout is refused:
nothing is written to the repository (spec §10).
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List

import hisim
from hisim import log
from hisim.verification import compare, export, runner, system_builder
from hisim.verification.case_loader import load_cases
from hisim.verification.suite import SUITES_DIRECTORY, Suite

DATA_ENVIRONMENT_VARIABLE = "HISIM_VERIFICATION_DATA"
DEFAULT_TIMESTEPS = (3600, 900, 60)

#: The HiSim checkout (or, installed, the folder the package lies in): no output goes below it.
REPOSITORY_ROOT = Path(hisim.__file__).resolve().parent.parent


def inside_repository(directory: Path) -> bool:
    """Whether a directory is the HiSim checkout or lies below it."""
    target = directory.resolve()
    return target == REPOSITORY_ROOT or REPOSITORY_ROOT in target.parents


def parse_arguments(arguments: List[str]) -> argparse.Namespace:
    """The command line: suite, cases, time steps, output directory, year and report-only."""
    suites = sorted(path.name for path in SUITES_DIRECTORY.iterdir() if (path / "suite.yaml").is_file())
    parser = argparse.ArgumentParser(prog="python -m hisim.verification", description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=suites, default="ashrae140", help="the suite; default ashrae140")
    parser.add_argument("--case", action="append", default=None, help="case id, repeatable; default every case of the suite")
    parser.add_argument("--timestep", action="append", type=int, default=None, help="seconds per time step, repeatable; default 3600, 900 and 60")
    parser.add_argument("--output-dir", type=Path, required=True, help="where the runs and BESTEST_output go")
    parser.add_argument("--year", type=int, default=system_builder.DEFAULT_YEAR, help="the year the typical year is laid onto")
    parser.add_argument("--report-only", action="store_true", help="build the report from the summaries in the output directory without running")
    return parser.parse_args(arguments)


def main(arguments: List[str]) -> int:
    """Run the requested cases, then write the private report of everything in the output directory."""
    options = parse_arguments(arguments)
    if inside_repository(options.output_dir):
        log.error(f"the output directory {options.output_dir} lies inside {REPOSITORY_ROOT}; the report is private, choose a folder outside (spec §10)")
        return 2
    data_root = os.environ.get(DATA_ENVIRONMENT_VARIABLE)
    if not data_root:
        log.error(f"set {DATA_ENVIRONMENT_VARIABLE} to the private data folder holding cases/{options.suite}")
        return 2
    suite = Suite.load(options.suite)
    if not options.report_only:
        cases = load_cases(Path(data_root) / "cases" / options.suite)
        unknown = sorted(set(options.case or []) - set(cases))
        if unknown:
            log.error(f"no case file for {unknown} in suite {options.suite}")
            return 2
        runner.write_guard_collects()
        for case_id in options.case or list(suite.cases):
            built = system_builder.build(cases[case_id], year=options.year)
            for seconds in options.timestep or DEFAULT_TIMESTEPS:
                settings = runner.RunSettings(year=options.year, seconds_per_timestep=seconds, output_directory=options.output_dir)
                run = runner.run_case(built, settings, suite.outputs_for(case_id))
                log.information(f"case {case_id} at {seconds} s done; written to {run.files['summary']}")
    reference_path = Path(data_root) / suite.reference_file
    references = compare.ReferenceRanges.load(reference_path) if reference_path.is_file() else None
    if references is None:
        log.warning(f"Reference data '{suite.reference_file}' not found (set {DATA_ENVIRONMENT_VARIABLE}); every range is skipped")
    files = export.write_report(export.build_report(options.output_dir, suite, references), options.output_dir)
    log.information(f"private report written to {files['summary.md'].parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
