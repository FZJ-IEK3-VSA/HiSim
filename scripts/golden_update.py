#!/usr/bin/env python3
"""Regenerate ("bless") golden KPI references for HiSim.

Runs the configured ``(setup, parameter_set)`` pairs through HiSim, flattens each
run's ``all_kpis.json``, and writes one committed golden file per pair to
``golden_references/<setup_id>__<param_id>.json`` plus an informational
``manifest.json``.

Blessing is deliberate and, per spec, driven by the ``golden-update.yml`` CI job
so the reference environment matches the check environment. It may be run locally
for inspection, but locally produced goldens are not the canonical committed ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

# Make the repo root importable whether invoked as ``python scripts/golden_update.py``
# or imported as ``scripts.golden_update`` (so ``hisim`` and siblings both resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # run as a script from scripts/ ...
    from runner import (  # type: ignore[import-not-found]
        GoldenConfig,
        RunResult,
        environment_metadata,
        filter_config,
        load_config,
        run_all,
    )
except ModuleNotFoundError:  # ... or imported as scripts.golden_update (tests)
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


def golden_filename(setup_id: str, parameter_set_id: str) -> str:
    """Return the committed golden filename for a pair."""
    return f"{setup_id}__{parameter_set_id}.json"


def write_golden(golden_dir: Path, result: RunResult) -> Path:
    """Write one pair's flattened KPIs to its golden file (sorted, indented)."""
    golden_dir.mkdir(parents=True, exist_ok=True)
    path = golden_dir / golden_filename(result.setup_id, result.parameter_set_id)
    path.write_text(json.dumps(result.kpis, indent=2, sort_keys=True))
    return path


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
    run_fn: RunFn = run_all,
) -> int:
    """Run the (filtered) pairs, write golden files + manifest. Return exit code.

    ``manifest_only`` skips running and just (re)writes the manifest from the
    files already in ``golden_dir`` — used by the CI ``collect`` job after it has
    assembled per-pair goldens from artifacts. Returns non-zero if any pair
    errored so a bless run fails visibly.
    """
    config = load_config(config_path)  # fail hard on a missing/invalid config

    if manifest_only:
        manifest_path = write_manifest(golden_dir, config_path)
        print(f"Golden update (manifest only): wrote {manifest_path}")
        return 0

    config = filter_config(config, setup_id=setup_id, param_id=param_id)
    results = run_fn(config, results_root, repo_root, "golden-update")

    written = 0
    errored = 0
    for result in results:
        if result.error is not None:
            errored += 1
            continue
        write_golden(golden_dir, result)
        written += 1

    manifest_path = write_manifest(golden_dir, config_path)
    print(
        f"Golden update: {len(results)} pair(s) — {written} written, {errored} errored. "
        f"Goldens: {golden_dir}  Manifest: {manifest_path}"
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
        )
    )
