#!/usr/bin/env python3
"""Regenerate the golden-reference snapshot for HiSim.

Loads ``scripts/golden_config.json``, runs every ``(setup, parameter_set)``
pair through HiSim via :func:`scripts.runner.run_all`, inventories the output
artifacts, and writes a ``manifest.json`` into ``results/golden_references/``.

This is the deliberate, human-only "bless new golden" button — never run it
automatically. Review the resulting snapshot before relying on it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

try:
    from runner import (  # type: ignore[import-not-found]  # run as a script from scripts/
        GoldenConfig,
        RunResult,
        collect_manifest,
        load_config,
        run_all,
        write_manifest,
    )
except ModuleNotFoundError:  # imported as scripts.golden_update (e.g. by the test suite)
    from scripts.runner import (
        GoldenConfig,
        RunResult,
        collect_manifest,
        load_config,
        run_all,
        write_manifest,
    )

DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"
DEFAULT_RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent


def main(
    config_path: Path = DEFAULT_CONFIG_PATH,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    repo_root: Path = DEFAULT_REPO_ROOT,
    run_fn: Callable[[GoldenConfig, Path, Path, str], list[RunResult]] = run_all,
) -> int:
    """Load config, run all ``(setup, parameter_set)`` pairs, write manifest, return 0.

    Fail hard (raise :exc:`FileNotFoundError`) if ``config_path`` does not exist.
    Never reads a previous snapshot — always writes a clean one.

    The ``run_fn`` parameter is injectable so tests can pass a fake that returns
    synthetic :class:`RunResult` lists without running HiSim.
    """
    config = load_config(config_path)
    golden_root = results_root / config.golden_subdir
    results = run_fn(config, results_root, repo_root, config.golden_subdir)
    manifest = collect_manifest(config, results, config_path)
    write_manifest(manifest, golden_root / "manifest.json")

    succeeded = sum(1 for r in results if r.error is None)
    errored = sum(1 for r in results if r.error is not None)
    total_artifacts = sum(len(r.artifacts) for r in results)
    manifest_path = golden_root / "manifest.json"
    print(
        f"Golden update: {len(results)} pair(s) — "
        f"{succeeded} succeeded, {errored} errored, "
        f"{total_artifacts} artifact(s). Manifest: {manifest_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
