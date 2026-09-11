#!/usr/bin/env python3
"""Regenerate ("bless") golden KPI references for HiSim.

Runs the configured ``(setup, parameter_set)`` pairs through HiSim, flattens each
run's ``all_kpis.json``, and writes one committed golden file per pair to
``golden_references/<setup_id>__<param_id>.json`` plus an informational
``manifest.json``.

Every golden file always carries the full KPI mapping; what the bless limits is
the *diff*. The bless is *sticky*: where a golden already exists, every key whose
fresh value the gate itself would accept (:func:`scripts.golden_kpis.compare` at
the very tolerances ``golden_check.py`` applies) keeps its stored value, so only
keys that genuinely moved or appeared reach the diff, and a file whose values come
out identical is not rewritten at all. Nothing ever disappears: a key the fresh run
no longer produces stays in the file (the gate then keeps failing with "missing
KPI" until someone retires it deliberately) and is named in the pair's summary.

Pass ``--force-rewrite`` to dump the fresh mapping verbatim instead, ignoring
whatever is on disk — the way to clear accumulated noise, to drop a retired KPI,
and to repair a golden that has become unreadable.

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

#: How many moved/new keys a summary line still names one by one. Beyond that the
#: line only counts them; the diff itself is the place to read a long list. Absent
#: keys are named regardless — they are the ones nobody would otherwise notice.
MAX_NAMED_KEYS = 10

#: The value types a flattened KPI mapping may hold (see ``scripts.golden_kpis.flatten``:
#: numbers become ``float``, other leaves — strings, ``None`` — are kept as they are).
_SCALAR_TYPES = (str, int, float, bool, type(None))


class UnusableGoldenError(RuntimeError):
    """The golden file for a pair exists but cannot be merged onto.

    Raised for a file that will not parse, that does not hold a JSON object, or
    whose values are not the flat scalars a golden consists of. Merging onto such
    a file would quietly produce nonsense, so the pair fails like a failed run;
    ``--force-rewrite`` bypasses the load and is the sanctioned repair path.
    """


def golden_filename(setup_id: str, parameter_set_id: str) -> str:
    """Return the committed golden filename for a pair."""
    return f"{setup_id}__{parameter_set_id}.json"


@dataclass(frozen=True)
class MergeRecord:
    """What merging a fresh KPI mapping onto a stored golden did, key by key."""

    merged: dict[str, Any]
    moved: tuple[str, ...]
    new: tuple[str, ...]
    absent: tuple[str, ...]
    kept: int

    @property
    def changed(self) -> bool:
        """True when some value in the file itself moved or newly appeared."""
        return bool(self.moved or self.new)


@dataclass(frozen=True)
class BlessOutcome:
    """What blessing one pair did to its golden file.

    ``record`` is ``None`` when there was nothing to merge onto — a first bless, or
    a ``--force-rewrite`` that ignored the stored values — and the fresh mapping was
    written verbatim.
    """

    path: Path
    rewritten: bool
    record: Optional[MergeRecord]


def _load_existing_golden(path: Path) -> Optional[dict[str, Any]]:
    """Return the golden mapping currently on disk, or ``None`` if the file is absent.

    An absent file simply means "no old values to keep" — the fresh run is then
    written verbatim, as it always was.

    Raises:
        UnusableGoldenError: the file exists but cannot be merged onto (unreadable,
            not a JSON object, or holding anything but flat scalar values).
    """
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise UnusableGoldenError(f"{path}: cannot be read as JSON ({exc})") from exc
    if not isinstance(stored, dict):
        raise UnusableGoldenError(
            f"{path}: holds a JSON {type(stored).__name__}, not an object mapping KPI keys to values"
        )
    for key, value in stored.items():
        if not isinstance(value, _SCALAR_TYPES):
            raise UnusableGoldenError(
                f"{path}: KPI '{key}' holds a {type(value).__name__}, not a scalar — a golden is the "
                "flat mapping scripts.golden_kpis.flatten produces, one value per dotted key"
            )
    return stored


def _within_tolerance(key: str, old_value: Any, new_value: Any) -> bool:
    """Whether the gate would accept ``new_value`` where the golden holds ``old_value``.

    The keep threshold is the gate's own default tolerance — ``REL_TOL``/``ABS_TOL``
    of :mod:`scripts.golden_kpis`, the tolerances the CI gates run at — asked of
    :func:`scripts.golden_kpis.compare` itself, one key at a time, so a value kept
    by a bless and a value passed by the gate can never drift apart.
    """
    return not compare("bless", {key: new_value}, {key: old_value}, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def merge_into_golden(old: dict[str, Any], new: dict[str, Any]) -> MergeRecord:
    """Merge a fresh KPI mapping onto an existing golden, and record what happened.

    Every key the gate would have accepted unchanged keeps its stored value; keys
    that deviate beyond tolerance or are new take the fresh value; keys the fresh
    run no longer produces are **kept** with their stored value, so nothing is ever
    silently removed from a golden — the gate goes on reporting them as missing
    until someone retires them deliberately with ``--force-rewrite``.

    Args:
        old: the golden mapping currently committed for this pair.
        new: the freshly computed mapping.

    Returns:
        MergeRecord: the mapping to store plus the moved/new/absent keys and the
        number of keys kept within tolerance.
    """
    merged: dict[str, Any] = {}
    moved: list[str] = []
    fresh: list[str] = []
    kept = 0
    for key, new_value in new.items():
        if key not in old:
            merged[key] = new_value
            fresh.append(key)
        elif _within_tolerance(key, old[key], new_value):
            merged[key] = old[key]
            kept += 1
        else:
            merged[key] = new_value
            moved.append(key)
    absent = [key for key in old if key not in new]
    for key in absent:
        merged[key] = old[key]
    return MergeRecord(merged=merged, moved=tuple(moved), new=tuple(fresh), absent=tuple(absent), kept=kept)


def summarize_record(record: MergeRecord) -> str:
    """Render one merge as the single human line a bless prints per pair.

    ``"unchanged"`` when no value moved, appeared or went absent; otherwise the
    counts — ``"N moved, M new, K absent (kept), J within tolerance kept"`` — naming
    the moved and new keys while there are at most :data:`MAX_NAMED_KEYS` of them,
    and always naming the absent ones.
    """
    if not record.changed and not record.absent:
        return "unchanged"
    summary = (
        f"{len(record.moved)} moved, {len(record.new)} new, {len(record.absent)} absent (kept), "
        f"{record.kept} within tolerance kept"
    )
    parts: list[str] = []
    if len(record.moved) + len(record.new) <= MAX_NAMED_KEYS:
        if record.moved:
            parts.append("moved: " + ", ".join(record.moved))
        if record.new:
            parts.append("new: " + ", ".join(record.new))
    if record.absent:
        parts.append("absent: " + ", ".join(record.absent))
    if not parts:
        return summary
    return summary + " — " + "; ".join(parts)


def write_golden(golden_dir: Path, result: RunResult, force_rewrite: bool = False) -> BlessOutcome:
    """Write one pair's flattened KPIs to its golden file (sorted, indented).

    Values the gate would have accepted are carried over from the golden already on
    disk (see :func:`merge_into_golden`), and a file whose values do not change is
    left untouched — value-level, so a hand-formatted golden that says the same
    thing keeps its formatting and its mtime and a bless that found nothing leaves
    no diff behind.

    Args:
        golden_dir: directory holding the committed goldens.
        result: one pair's successful run.
        force_rewrite: dump the fresh mapping verbatim, ignoring the stored values.

    Returns:
        BlessOutcome: the file's path, whether it was rewritten, and the merge record.

    Raises:
        UnusableGoldenError: the stored golden exists but cannot be merged onto.
    """
    golden_dir.mkdir(parents=True, exist_ok=True)
    path = golden_dir / golden_filename(result.setup_id, result.parameter_set_id)
    old = None if force_rewrite else _load_existing_golden(path)
    if old is None:
        path.write_text(json.dumps(result.kpis, indent=2, sort_keys=True))
        return BlessOutcome(path, True, None)

    record = merge_into_golden(old, result.kpis)
    if old == record.merged:
        return BlessOutcome(path, False, record)
    path.write_text(json.dumps(record.merged, indent=2, sort_keys=True))
    return BlessOutcome(path, True, record)


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
    pair errored — a failed run, or a stored golden that cannot be merged onto —
    so a bless run fails visibly.
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
    failures: list[str] = []
    for result in results:
        name = f"{result.setup_id}/{result.parameter_set_id}"
        if result.error is not None:
            failures.append(f"  ERROR {name}:\n{result.error}")
            continue
        try:
            outcome = write_golden(golden_dir, result, force_rewrite=force_rewrite)
        except UnusableGoldenError as exc:
            print(f"  {name}: unusable golden — {exc}")
            failures.append(f"  ERROR {name}: unusable golden — {exc} (bless it with --force-rewrite to repair)")
            continue
        if outcome.rewritten:
            rewritten += 1
        else:
            unchanged += 1
        summary = "written" if outcome.record is None else summarize_record(outcome.record)
        print(f"  {name}: {summary}")

    manifest_path = write_manifest(golden_dir, config_path)
    print(
        f"Golden update: {len(results)} pair(s) — {rewritten} rewritten, {unchanged} unchanged, "
        f"{len(failures)} errored. Goldens: {golden_dir}  Manifest: {manifest_path}"
    )
    for failure in failures:
        print(failure)
    return 1 if failures else 0


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
        help=(
            "Write every fresh value verbatim instead of keeping stored values the gate accepts. "
            "Ignores the stored file entirely, so this is also how a retired KPI is removed and how "
            "a golden that has become unreadable is repaired."
        ),
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
