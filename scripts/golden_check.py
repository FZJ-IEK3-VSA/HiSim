#!/usr/bin/env python3
"""Golden KPI regression gate for HiSim.

Re-runs the configured ``(setup, parameter_set)`` pairs, turns each run's
``all_kpis.json`` into golden leaves (``scripts/golden_kpis.py``), and compares them
against the committed golden in ``golden_references/``. Exits non-zero on any KPI
deviation (a value beyond tolerance, a changed unit — reported as a failure of its
own kind —, a changed address field, a missing or new KPI), a missing or unusable
golden (one not in the leaf form, such as the old flat form, which is refused before
any simulation runs), or a run failure. Writes a human-readable ``report.txt`` and a
machine-readable ``report.json``. Read-only: never writes golden references
(that is ``golden_update.py``'s job).

A CI job checks one *shard*: ``--pairs setup:param ...`` (from ``golden_matrix.py --shards``)
with ``--jobs N``, which runs N pairs at once, each in a child process of its own; the results
still land in the one report, and a pair that diverges or fails to run fails the whole check.
``--mode both`` runs every pair's Python setup and its recorded YAML twin side by side in that
one pool and writes one report per mode: YAML mode writes to ``<check_subdir>-yaml``, so the two
never share a result directory or read each other's ``all_kpis.json``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# Make the repo root importable whether invoked as ``python scripts/golden_check.py``
# or imported as ``scripts.golden_check`` (so ``hisim`` and siblings both resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from golden_kpis import (  # type: ignore[import-not-found]
        ABS_TOL,
        REL_TOL,
        UNIT,
        GoldenFormatError,
        compare,
        load_golden,
    )
    from runner import (  # type: ignore[import-not-found]
        GoldenConfig,
        RunResult,
        filter_config,
        load_config,
        parse_pair,
        run_all,
        run_modes,
        select_pairs,
    )
except ModuleNotFoundError:  # ... or imported as scripts.golden_check (tests)
    from scripts.golden_kpis import ABS_TOL, REL_TOL, UNIT, GoldenFormatError, compare, load_golden
    from scripts.runner import (
        GoldenConfig,
        RunResult,
        filter_config,
        load_config,
        parse_pair,
        run_all,
        run_modes,
        select_pairs,
    )

#: KPI names that carry a legacy aggregator port name (``Priority for Input_<source>_<field>_<n>``).
#: The legacy and declarative paths name aggregator ports differently (C-P3.2 of the P3
#: requirements), and the energy-management system names its priority KPI after the port, so these
#: KPIs differ between the two paths by NAME while their values agree. YAML mode excludes the
#: family from both sides of the comparison -- declared here, printed per pair, and covered
#: exactly by the parity rig through its renaming table. The exclusion dissolves when the legacy
#: aggregator ports adopt the declarative names (a P4/P5 item per C-P3.2).
PORT_NAMED_KPIS = re.compile(r"\.Priority for ")

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"
DEFAULT_GOLDEN_DIR = DEFAULT_REPO_ROOT / "golden_references"
DEFAULT_RESULTS_ROOT = DEFAULT_REPO_ROOT / "results"

RunFn = Callable[[GoldenConfig, Path, Path, str], list[RunResult]]


def golden_filename(setup_id: str, parameter_set_id: str) -> str:
    """Return the committed golden filename for a pair (matches golden_update.py)."""
    return f"{setup_id}__{parameter_set_id}.json"


@dataclass
class PairReport:
    """Comparison outcome for one ``(setup, parameter_set)`` pair."""

    setup_id: str
    parameter_set_id: str
    status: str  # "pass" | "fail" | "advisory" | "missing_golden" | "unusable_golden" | "run_error"
    nondeterministic: bool = False
    deviations: list[str] = field(default_factory=list)
    #: KPIs whose unit changed: a failure of its own kind, kept apart from ``deviations``.
    unit_changes: list[str] = field(default_factory=list)
    #: Wall time of the pair's run in seconds; the source of ``golden_config.json``'s weights.
    duration_s: Optional[float] = None


@dataclass
class ComparisonReport:
    """Full gate outcome across all compared pairs."""

    passed: bool
    pairs: list[PairReport] = field(default_factory=list)

    def summary_line(self) -> str:
        total = len(self.pairs)
        if self.passed:
            advisory = sum(1 for p in self.pairs if p.status == "advisory")
            extra = f" ({advisory} advisory)" if advisory else ""
            return f"GOLDEN CHECK OK ({total} pair(s)){extra}"
        diverged = sum(1 for p in self.pairs if p.status == "fail" and p.deviations)
        unit_changed = sum(1 for p in self.pairs if p.status == "fail" and p.unit_changes)
        errored = sum(1 for p in self.pairs if p.status == "run_error")
        missing = sum(1 for p in self.pairs if p.status == "missing_golden")
        unusable = sum(1 for p in self.pairs if p.status == "unusable_golden")
        reasons = []
        if errored:
            reasons.append(f"{errored} failed to run")
        if diverged:
            reasons.append(f"{diverged} with KPI divergences")
        if unit_changed:
            reasons.append(f"{unit_changed} with KPI unit changes")
        if missing:
            reasons.append(f"{missing} missing a golden reference")
        if unusable:
            reasons.append(f"{unusable} with an unusable golden reference")
        return f"GOLDEN CHECK FAILED ({total} pair(s)): {', '.join(reasons)}"


def _print_failure_details(report: ComparisonReport, out_dir: Path, advisory: bool) -> None:
    """Print an explicit, per-pair explanation of every failing pair to stdout.

    Each block names what actually happened — whether the run completed, whether
    the golden reference was found, and (for divergences) which KPIs changed by
    how much — so the console output alone answers "did it run?", "were the files
    found?", and "what deviated?" without opening the report files.
    """
    for pair in report.pairs:
        name = f"{pair.setup_id} / {pair.parameter_set_id}"
        if pair.status == "run_error":
            print(f"\n[RUN ERROR] {name}")
            print("    The simulation did NOT run successfully. Traceback:")
            for line in "".join(pair.deviations).splitlines():
                print(f"      {line}")
        elif pair.status == "fail":
            if pair.deviations:
                print(f"\n[KPI DIVERGENCE] {name}")
                print(
                    f"    The simulation ran successfully and the golden reference was found, "
                    f"but {len(pair.deviations)} KPI(s) differ from the golden:"
                )
                for dev in pair.deviations:
                    print(f"      - {dev}")
            if pair.unit_changes:
                print(f"\n[KPI UNIT CHANGE] {name}")
                print(f"    {len(pair.unit_changes)} KPI(s) are reported in another unit than the golden's:")
                for dev in pair.unit_changes:
                    print(f"      - {dev}")
        elif pair.status == "missing_golden":
            print(f"\n[MISSING GOLDEN] {name}")
            print("    No golden reference file was found to compare against:")
            for dev in pair.deviations:
                print(f"      - {dev}")
        elif pair.status == "unusable_golden":
            print(f"\n[UNUSABLE GOLDEN] {name}")
            print("    The golden reference file cannot be compared against:")
            for dev in pair.deviations:
                print(f"      - {dev}")
    print(f"\nFull report written to {out_dir / 'report.txt'}")
    if advisory:
        print("(advisory mode: divergences reported but not blocking)")


def _write_reports(report: ComparisonReport, out_dir: Path) -> None:
    """Write ``report.json`` and ``report.txt`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(asdict(report), indent=2, sort_keys=True))

    lines = [report.summary_line(), ""]
    for pair in report.pairs:
        tag = pair.status.upper()
        note = " [advisory: nondeterministic]" if pair.status == "advisory" else ""
        took = f" ({pair.duration_s} s)" if pair.duration_s is not None else ""
        lines.append(f"[{tag}] {pair.setup_id} / {pair.parameter_set_id}{took}{note}")
        for dev in pair.deviations:
            lines.append(f"    - {dev}")
        for dev in pair.unit_changes:
            lines.append(f"    - [unit] {dev}")
    (out_dir / "report.txt").write_text("\n".join(lines) + "\n")


#: Each mode's result subdirectory suffix (appended to the config's ``check_subdir``) and the KPI
#: names it excludes from the comparison.
MODE_SUFFIX = {"python": "", "yaml": "-yaml"}
MODE_IGNORED_KPIS: dict[str, Optional[re.Pattern]] = {"python": None, "yaml": PORT_NAMED_KPIS}

ModesRunFn = Callable[[GoldenConfig, Path, Path, dict[str, str], int], dict[str, list[RunResult]]]


def _unready_goldens(config: GoldenConfig, golden_dir: Path) -> Optional[ComparisonReport]:
    """Return the failing report of every pair whose golden is missing or unusable, or ``None``.

    A golden is unusable when :func:`scripts.golden_kpis.load_golden` refuses it: unreadable
    JSON, or not the leaf form (the old flat ``dotted key -> value`` form included, which is
    re-blessed rather than read). Checked before any simulation runs, so a reference that cannot
    be compared against never wastes compute.
    """
    unready: list[PairReport] = []
    for setup, param in select_pairs(config):
        path = golden_dir / golden_filename(setup.id, param.id)
        if not path.exists():
            unready.append(
                PairReport(
                    setup_id=setup.id,
                    parameter_set_id=param.id,
                    status="missing_golden",
                    nondeterministic=param.nondeterministic,
                    deviations=[f"no golden at {path} (run golden_update.py / golden-update.yml)"],
                )
            )
            continue
        try:
            load_golden(path)
        except GoldenFormatError as exc:
            unready.append(
                PairReport(
                    setup_id=setup.id,
                    parameter_set_id=param.id,
                    status="unusable_golden",
                    nondeterministic=param.nondeterministic,
                    deviations=[str(exc)],
                )
            )
    if not unready:
        return None
    return ComparisonReport(passed=False, pairs=unready)


def _evaluate(
    config: GoldenConfig,
    results: list[RunResult],
    golden_dir: Path,
    rel_tol: float,
    abs_tol: float,
    ignore_kpis: Optional[re.Pattern],
) -> ComparisonReport:
    """Compare each run's KPIs with its golden and return the verdicts of all of them."""
    param_by_id = {p.id: p for p in config.parameter_sets}
    pair_reports: list[PairReport] = []
    passed = True
    for result in results:
        param = param_by_id[result.parameter_set_id]
        nondet = param.nondeterministic
        name = f"{result.setup_id}/{result.parameter_set_id}"

        if result.error is not None:
            passed = False
            pair_reports.append(
                PairReport(
                    result.setup_id,
                    result.parameter_set_id,
                    "run_error",
                    nondet,
                    [result.error],
                    duration_s=result.duration_s,
                )
            )
            continue

        golden_path = golden_dir / golden_filename(result.setup_id, result.parameter_set_id)
        ref: dict[str, Any] = load_golden(golden_path)
        got = result.kpis
        if ignore_kpis is not None:
            got = {k: v for k, v in got.items() if not ignore_kpis.search(k)}
            filtered_ref = {k: v for k, v in ref.items() if not ignore_kpis.search(k)}
            excluded = (len(ref) - len(filtered_ref)) + (len(result.kpis) - len(got))
            ref = filtered_ref
            if excluded:
                print(f"  ({name}: {excluded} port-named KPI(s) excluded from comparison, C-P3.2)")
        found = compare(name, got, ref, rel_tol=rel_tol, abs_tol=abs_tol)
        deviations = [str(deviation) for deviation in found if deviation.kind != UNIT]
        unit_changes = [str(deviation) for deviation in found if deviation.kind == UNIT]

        if not found:
            status = "pass"
        elif nondet:
            status = "advisory"  # compared, but does not fail the gate
        else:
            status = "fail"
            passed = False
        pair_reports.append(
            PairReport(
                result.setup_id,
                result.parameter_set_id,
                status,
                nondet,
                deviations,
                unit_changes,
                result.duration_s,
            )
        )
    return ComparisonReport(passed=passed, pairs=pair_reports)


def _conclude(report: ComparisonReport, out_dir: Path, advisory: bool) -> int:
    """Write and print one report; return its exit code."""
    _write_reports(report, out_dir)
    print(report.summary_line())
    if not report.passed:
        _print_failure_details(report, out_dir, advisory)
    return 0 if (report.passed or advisory) else 1


def main(
    config_path: Path = DEFAULT_CONFIG_PATH,
    golden_dir: Path = DEFAULT_GOLDEN_DIR,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    repo_root: Path = DEFAULT_REPO_ROOT,
    setup_id: Optional[str] = None,
    param_id: Optional[str] = None,
    rel_tol: float = REL_TOL,
    abs_tol: float = ABS_TOL,
    run_fn: RunFn = run_all,
    advisory: bool = False,
    ignore_kpis: Optional[re.Pattern] = None,
    pairs: Optional[list[tuple[str, str]]] = None,
    subdir_suffix: str = "",
) -> int:
    """Run the (filtered) pairs and compare KPIs to committed goldens.

    Returns ``0`` if every compared pair matches (advisory-only mismatches on
    ``nondeterministic`` pairs still pass), ``1`` otherwise. Bails **before**
    running any simulation if a required golden file is missing or unusable, so
    such a reference never wastes compute.

    ``ignore_kpis`` drops matching KPI names from both the run and the reference before
    comparing; YAML mode passes :data:`PORT_NAMED_KPIS` for it, and every exclusion is
    printed with its pair so a shrinking comparison is never silent.

    When ``advisory`` is ``True`` the full comparison still runs and the reports
    are written exactly as usual, but the process return code is forced to ``0`` so
    the check can surface divergences without blocking a gate that is still burning
    in. The written ``report.json`` still records the true ``passed`` verdict.

    ``pairs`` restricts the check to exactly those pairs, in that order (a CI shard).
    ``subdir_suffix`` is appended to the config's ``check_subdir`` for both the runs and the
    report; YAML mode passes ``-yaml``. :func:`check_modes` is the same for several modes
    run side by side.
    """
    config = load_config(config_path)
    config = filter_config(config, setup_id=setup_id, param_id=param_id, pairs=pairs)
    subdir = config.check_subdir + subdir_suffix
    out_dir = results_root / subdir

    missing = _unready_goldens(config, golden_dir)
    if missing is not None:
        return _conclude(missing, out_dir, advisory)
    results = run_fn(config, results_root, repo_root, subdir)
    return _conclude(_evaluate(config, results, golden_dir, rel_tol, abs_tol, ignore_kpis), out_dir, advisory)


def check_modes(
    modes: Sequence[str],
    config_path: Path = DEFAULT_CONFIG_PATH,
    golden_dir: Path = DEFAULT_GOLDEN_DIR,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    repo_root: Path = DEFAULT_REPO_ROOT,
    setup_id: Optional[str] = None,
    param_id: Optional[str] = None,
    rel_tol: float = REL_TOL,
    abs_tol: float = ABS_TOL,
    advisory: bool = False,
    pairs: Optional[list[tuple[str, str]]] = None,
    jobs: int = 1,
    run_modes_fn: ModesRunFn = run_modes,
) -> int:
    """Check the pairs in every mode of ``modes`` from one pool of runs, one report per mode.

    Each mode is judged exactly as :func:`main` judges it — its own result directory
    (:data:`MODE_SUFFIX`), its own excluded KPIs (:data:`MODE_IGNORED_KPIS`), its own
    ``report.*`` — but the runs of all modes share one pool of ``jobs`` child processes, so a
    CI shard runs a pair's Python setup and its YAML twin side by side. Returns ``1`` if any
    mode's check fails (subject to ``advisory``), else ``0``; every mode is always reported.
    """
    config = load_config(config_path)
    config = filter_config(config, setup_id=setup_id, param_id=param_id, pairs=pairs)
    subdirs = {mode: config.check_subdir + MODE_SUFFIX[mode] for mode in modes}

    missing = _unready_goldens(config, golden_dir)
    if missing is not None:
        return max(_conclude(missing, results_root / subdir, advisory) for subdir in subdirs.values())
    results = run_modes_fn(config, results_root, repo_root, subdirs, jobs)
    exit_code = 0
    for mode, subdir in subdirs.items():
        print(f"\n== {mode} ==")
        report = _evaluate(config, results[mode], golden_dir, rel_tol, abs_tol, MODE_IGNORED_KPIS[mode])
        exit_code = max(exit_code, _conclude(report, results_root / subdir, advisory))
    return exit_code


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Golden KPI regression gate for HiSim.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--setup", dest="setup_id", default=None, help="Only check this setup id.")
    parser.add_argument("--param", dest="param_id", default=None, help="Only check this parameter-set id.")
    parser.add_argument(
        "--pairs",
        nargs="+",
        type=parse_pair,
        default=None,
        metavar="SETUP:PARAM",
        help="Check exactly these pairs, in this order (a CI shard from golden_matrix.py --shards).",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="How many pairs run at once, each in a child process of its own (default 1: one after "
        "another, in this process).",
    )
    parser.add_argument("--rel-tol", type=float, default=REL_TOL)
    parser.add_argument("--abs-tol", type=float, default=ABS_TOL)
    parser.add_argument(
        "--mode",
        choices=("python", "yaml", "both"),
        default="python",
        help="Run the '.py' setups (python) or their recorded '.energy_system.yaml' twins "
        "through the declarative executor (yaml), or both side by side from one pool of runs "
        "(both; one report per mode). All compare against the same committed golden references.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Report divergences but always exit 0 (never block).",
    )
    parsed = parser.parse_args(argv)
    if parsed.jobs < 1:
        parser.error("--jobs must be at least 1")
    return parsed


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(
        check_modes(
            ["python", "yaml"] if args.mode == "both" else [args.mode],
            config_path=args.config,
            golden_dir=args.golden_dir,
            results_root=args.results_root,
            repo_root=args.repo_root,
            setup_id=args.setup_id,
            param_id=args.param_id,
            rel_tol=args.rel_tol,
            abs_tol=args.abs_tol,
            advisory=args.advisory,
            pairs=args.pairs,
            jobs=args.jobs,
        )
    )
