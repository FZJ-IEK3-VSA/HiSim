#!/usr/bin/env python3
"""Regenerate ("bless") golden KPI references for HiSim.

Runs the configured ``(setup, parameter_set)`` pairs through HiSim, flattens each
run's ``all_kpis.json``, and writes one committed golden file per pair to
``golden_references/<setup_id>__<param_id>.json`` plus an informational
``manifest.json``.

The bless is *sticky*: where a golden already exists, every key whose fresh value
the gate itself would accept (:func:`scripts.golden_kpis.compare` at the very
tolerances ``golden_check.py`` applies) keeps its stored value. Only keys that
genuinely moved, appeared, or disappeared reach the file, and a file whose content
comes out identical is not rewritten at all. Last-digit float noise from the
container therefore stays out of the bless diff, which then says only what changed.
Pass ``--force-rewrite`` to dump the fresh mapping verbatim instead — the rare case
where someone wants the accumulated noise cleared deliberately.

Blessing is deliberate and, per spec, driven by the ``golden-update.yml`` CI job
so the reference environment matches the check environment. It may be run locally
for inspection, but locally produced goldens are not the canonical committed ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# Make the repo root importable whether invoked as ``python scripts/golden_update.py``
# or imported as ``scripts.golden_update`` (so ``hisim`` and siblings both resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from golden_kpis import ABS_TOL, REL_TOL, compare  # type: ignore[import-not-found]
    from runner import (  # type: ignore[import-not-found]
        GoldenConfig,
        RunResult,
        environment_metadata,
        filter_config,
        load_config,
        run_all,
    )
except ModuleNotFoundError:  # ... or imported as scripts.golden_update (tests)
    from scripts.golden_kpis import ABS_TOL, REL_TOL, compare
    from scripts.runner import (
        GoldenConfig,
        RunResult,
        environment_metadata,
        filter_config,
        load_config,
        run_all,
    )

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"
DEFAULT_GOLDEN_DIR = DEFAULT_REPO_ROOT / "golden_references"
DEFAULT_RESULTS_ROOT = DEFAULT_REPO_ROOT / "results"

RunFn = Callable[[GoldenConfig, Path, Path, str], list[RunResult]]

#: How many moved keys a summary line still names one by one. Beyond that the line
#: only counts them; the diff itself is the place to read a long list.
MAX_NAMED_KEYS = 10


def golden_filename(setup_id: str, parameter_set_id: str) -> str:
    """Return the committed golden filename for a pair."""
    return f"{setup_id}__{parameter_set_id}.json"


@dataclass
class BlessOutcome:
    """What blessing one pair did to its golden file."""

    path: Path
    rewritten: bool
    summary: str


def _load_existing_golden(path: Path) -> Optional[dict[str, Any]]:
    """Return the golden mapping currently on disk, or ``None`` if there is no usable one.

    An absent, unreadable or structurally unexpected file simply means "no old
    values to keep" — the fresh run is then written verbatim, as it always was.
    """
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return stored if isinstance(stored, dict) else None


def _within_tolerance(key: str, old_value: Any, new_value: Any) -> bool:
    """Whether the gate would accept ``new_value`` where the golden holds ``old_value``.

    Asks :func:`scripts.golden_kpis.compare` itself, at the check's own tolerances,
    so a value kept by a bless and a value passed by the gate can never drift apart.
    """
    return not compare("bless", {key: new_value}, {key: old_value}, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def merge_into_golden(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Merge a fresh KPI mapping onto an existing golden, and say what moved.

    Every key the gate would have accepted unchanged keeps its stored value; keys
    that deviate beyond tolerance or are new take the fresh value; keys the fresh
    run no longer produces are dropped.

    Args:
        old: the golden mapping currently committed for this pair.
        new: the freshly computed mapping.

    Returns:
        tuple: the mapping to store, and a one-line human summary — ``"unchanged"``
        or ``"N key(s) moved, M within tolerance kept"``, naming the keys while
        there are at most :data:`MAX_NAMED_KEYS` of them.
    """
    merged: dict[str, Any] = {}
    moved: list[str] = []
    kept = 0
    for key, new_value in new.items():
        if key in old and _within_tolerance(key, old[key], new_value):
            merged[key] = old[key]
            kept += 1
        else:
            merged[key] = new_value
            moved.append(key if key in old else f"{key} (new)")
    dropped = [f"{key} (dropped)" for key in old if key not in new]

    changed = moved + dropped
    if not changed:
        return merged, "unchanged"
    summary = f"{len(changed)} key(s) moved, {kept} within tolerance kept"
    if len(changed) <= MAX_NAMED_KEYS:
        summary += " — " + ", ".join(changed)
    elif dropped:
        summary += f", {len(dropped)} of them dropped"
    return merged, summary


def write_golden(golden_dir: Path, result: RunResult, force_rewrite: bool = False) -> BlessOutcome:
    """Write one pair's flattened KPIs to its golden file (sorted, indented).

    Values the gate would have accepted are carried over from the golden already on
    disk (see :func:`merge_into_golden`), and a file whose content does not change
    is left untouched, so a bless that found nothing leaves no diff behind.

    Args:
        golden_dir: directory holding the committed goldens.
        result: one pair's successful run.
        force_rewrite: dump the fresh mapping verbatim, ignoring the stored values.

    Returns:
        BlessOutcome: the file's path, whether it was rewritten, and the summary line.
    """
    golden_dir.mkdir(parents=True, exist_ok=True)
    path = golden_dir / golden_filename(result.setup_id, result.parameter_set_id)
    old = None if force_rewrite else _load_existing_golden(path)
    if old is None:
        payload, summary = result.kpis, "written"
    else:
        payload, summary = merge_into_golden(old, result.kpis)

    text = json.dumps(payload, indent=2, sort_keys=True)
    if path.is_file() and path.read_text() == text:
        return BlessOutcome(path, False, "unchanged")
    path.write_text(text)
    return BlessOutcome(path, True, summary)


def write_manifest(golden_dir: Path, config_path: Path) -> Path:
    """Write an informational ``manifest.json`` by scanning ``golden_dir``.

    Records environment metadata plus the sorted list of golden files currently
    present. Scan-based so it works whether the directory was filled by one full
    local run or assembled from many per-pair CI legs.
    """
    golden_dir.mkdir(parents=True, exist_ok=True)
    golden_files = sorted(p.name for p in golden_dir.glob("*.json") if p.name != "manifest.json")
    manifest = {**environment_metadata(config_path), "golden_files": golden_files}
    path = golden_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path


def main(
    config_path: Path = DEFAULT_CONFIG_PATH,
    golden_dir: Path = DEFAULT_GOLDEN_DIR,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    repo_root: Path = DEFAULT_REPO_ROOT,
    setup_id: Optional[str] = None,
    param_id: Optional[str] = None,
    manifest_only: bool = False,
    force_rewrite: bool = False,
    run_fn: RunFn = run_all,
) -> int:
    """Run the (filtered) pairs, write golden files + manifest. Return exit code.

    ``manifest_only`` skips running and just (re)writes the manifest from the
    files already in ``golden_dir`` — used by the CI ``collect`` job after it has
    assembled per-pair goldens from artifacts. ``force_rewrite`` disables the
    sticky merge and dumps every fresh mapping verbatim. Returns non-zero if any
    pair errored so a bless run fails visibly.
    """
    config = load_config(config_path)  # fail hard on a missing/invalid config

    if manifest_only:
        manifest_path = write_manifest(golden_dir, config_path)
        print(f"Golden update (manifest only): wrote {manifest_path}")
        return 0

    config = filter_config(config, setup_id=setup_id, param_id=param_id)
    results = run_fn(config, results_root, repo_root, "golden-update")

    rewritten = 0
    unchanged = 0
    errored = 0
    for result in results:
        if result.error is not None:
            errored += 1
            continue
        outcome = write_golden(golden_dir, result, force_rewrite=force_rewrite)
        if outcome.rewritten:
            rewritten += 1
        else:
            unchanged += 1
        print(f"  {result.setup_id}/{result.parameter_set_id}: {outcome.summary}")

    manifest_path = write_manifest(golden_dir, config_path)
    print(
        f"Golden update: {len(results)} pair(s) — {rewritten} rewritten, {unchanged} unchanged, "
        f"{errored} errored. Goldens: {golden_dir}  Manifest: {manifest_path}"
    )
    if errored:
        for result in results:
            if result.error is not None:
                print(f"  ERROR {result.setup_id}/{result.parameter_set_id}:\n{result.error}")
    return 1 if errored else 0


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bless golden KPI references for HiSim.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--setup", dest="setup_id", default=None, help="Only bless this setup id.")
    parser.add_argument("--param", dest="param_id", default=None, help="Only bless this parameter-set id.")
    parser.add_argument(
        "--manifest-only", action="store_true", help="Only (re)write manifest.json from existing goldens."
    )
    parser.add_argument(
        "--force-rewrite",
        action="store_true",
        help="Write every fresh value verbatim instead of keeping stored values the gate accepts.",
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
            manifest_only=args.manifest_only,
            force_rewrite=args.force_rewrite,
        )
    )
