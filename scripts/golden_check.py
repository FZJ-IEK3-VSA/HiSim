#!/usr/bin/env python3
"""Golden KPI regression gate for HiSim.

Re-runs the configured ``(setup, parameter_set)`` pairs, flattens each run's
``all_kpis.json``, and compares it against the committed golden in
``golden_references/``. Exits non-zero on any KPI deviation (beyond tolerance),
missing golden, or run failure. Writes a human-readable ``report.txt`` and a
machine-readable ``report.json``. Read-only: never writes golden references
(that is ``golden_update.py``'s job).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Make the repo root importable whether invoked as ``python scripts/golden_check.py``
# or imported as ``scripts.golden_check`` (so ``hisim`` and siblings both resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from golden_kpis import ABS_TOL, REL_TOL, compare  # type: ignore[import-not-found]
    from runner import (  # type: ignore[import-not-found]
        GoldenConfig,
        RunResult,
        filter_config,
        load_config,
        run_all,
        run_all_json,
        select_pairs,
    )
except ModuleNotFoundError:  # ... or imported as scripts.golden_check (tests)
    from scripts.golden_kpis import ABS_TOL, REL_TOL, compare
    from scripts.runner import (
        GoldenConfig,
        RunResult,
        filter_config,
        load_config,
        run_all,
        run_all_json,
        select_pairs,
    )

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
    status: str  # "pass" | "fail" | "advisory" | "missing_golden" | "run_error"
    nondeterministic: bool = False
    deviations: list[str] = field(default_factory=list)


@dataclass
class ComparisonReport:
    """Full gate outcome across all compared pairs."""

    passed: bool
    pairs: list[PairReport] = field(default_factory=list)

    def summary_line(self) -> str:
        failing = [p for p in self.pairs if p.status in ("fail", "missing_golden", "run_error")]
        if self.passed:
            advisory = sum(1 for p in self.pairs if p.status == "advisory")
            extra = f" ({advisory} advisory)" if advisory else ""
            return f"GOLDEN CHECK OK ({len(self.pairs)} pair(s)){extra}"
        return f"GOLDEN CHECK FAILED: {len(failing)} of {len(self.pairs)} pair(s) diverged/missing/errored"


def _write_reports(report: ComparisonReport, out_dir: Path) -> None:
    """Write ``report.json`` and ``report.txt`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(asdict(report), indent=2, sort_keys=True))

    lines = [report.summary_line(), ""]
    for pair in report.pairs:
        tag = pair.status.upper()
        note = " [advisory: nondeterministic]" if pair.status == "advisory" else ""
        lines.append(f"[{tag}] {pair.setup_id} / {pair.parameter_set_id}{note}")
        for dev in pair.deviations:
            lines.append(f"    - {dev}")
    (out_dir / "report.txt").write_text("\n".join(lines) + "\n")


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
) -> int:
    """Run the (filtered) pairs and compare KPIs to committed goldens.

    Returns ``0`` if every compared pair matches (advisory-only mismatches on
    ``nondeterministic`` pairs still pass), ``1`` otherwise. Bails **before**
    running any simulation if a required golden file is missing, so a missing
    reference never wastes compute.

    When ``advisory`` is ``True`` the full comparison still runs and the reports
    are written exactly as usual, but the process return code is forced to ``0`` so
    the check can surface divergences without blocking (used by the JSON golden
    check until JSON/Python parity is proven). The written ``report.json`` still
    records the true ``passed`` verdict.
    """
    config = load_config(config_path)
    config = filter_config(config, setup_id=setup_id, param_id=param_id)
    out_dir = results_root / config.check_subdir

    pairs = select_pairs(config)
    missing = [
        (setup, param)
        for setup, param in pairs
        if not (golden_dir / golden_filename(setup.id, param.id)).exists()
    ]
    if missing:
        report = ComparisonReport(
            passed=False,
            pairs=[
                PairReport(
                    setup_id=setup.id,
                    parameter_set_id=param.id,
                    status="missing_golden",
                    nondeterministic=param.nondeterministic,
                    deviations=[
                        f"no golden at {golden_dir / golden_filename(setup.id, param.id)} "
                        "(run golden_update.py / golden-update.yml)"
                    ],
                )
                for setup, param in missing
            ],
        )
        _write_reports(report, out_dir)
        print(report.summary_line())
        return 0 if advisory else 1

    param_by_id = {p.id: p for p in config.parameter_sets}
    results = run_fn(config, results_root, repo_root, config.check_subdir)

    pair_reports: list[PairReport] = []
    passed = True
    for result in results:
        param = param_by_id[result.parameter_set_id]
        nondet = param.nondeterministic
        name = f"{result.setup_id}/{result.parameter_set_id}"

        if result.error is not None:
            passed = False
            pair_reports.append(
                PairReport(result.setup_id, result.parameter_set_id, "run_error", nondet, [result.error])
            )
            continue

        golden_path = golden_dir / golden_filename(result.setup_id, result.parameter_set_id)
        ref: dict[str, Any] = json.loads(golden_path.read_text())
        deviations = compare(name, result.kpis, ref, rel_tol=rel_tol, abs_tol=abs_tol)

        if not deviations:
            status = "pass"
        elif nondet:
            status = "advisory"  # compared, but does not fail the gate
        else:
            status = "fail"
            passed = False
        pair_reports.append(PairReport(result.setup_id, result.parameter_set_id, status, nondet, deviations))

    report = ComparisonReport(passed=passed, pairs=pair_reports)
    _write_reports(report, out_dir)
    print(report.summary_line())
    if not passed:
        for pair in pair_reports:
            if pair.status in ("fail", "run_error"):
                print(f"  {pair.status.upper()} {pair.setup_id}/{pair.parameter_set_id}")
                for dev in pair.deviations[:10]:
                    print(f"    - {dev}")
        if advisory:
            print("(advisory mode: divergences reported but not blocking)")
    return 0 if (passed or advisory) else 1


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Golden KPI regression gate for HiSim.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--setup", dest="setup_id", default=None, help="Only check this setup id.")
    parser.add_argument("--param", dest="param_id", default=None, help="Only check this parameter-set id.")
    parser.add_argument("--rel-tol", type=float, default=REL_TOL)
    parser.add_argument("--abs-tol", type=float, default=ABS_TOL)
    parser.add_argument(
        "--mode",
        choices=("python", "json"),
        default="python",
        help="Run the '.py' setups (python) or their '.scenario.json' siblings (json). "
        "Both compare against the same committed golden references.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Report divergences but always exit 0 (never block). Used by the JSON check.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(
        main(
            config_path=args.config,
            golden_dir=args.golden_dir,
            results_root=args.results_root,
            repo_root=args.repo_root,
            setup_id=args.setup_id,
            param_id=args.param_id,
            rel_tol=args.rel_tol,
            abs_tol=args.abs_tol,
            run_fn=run_all_json if args.mode == "json" else run_all,
            advisory=args.advisory,
        )
    )
