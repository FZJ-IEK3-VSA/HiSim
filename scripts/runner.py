"""Config-driven execution core for the golden-reference system.

This module turns a JSON config (``scripts/golden_config.json``) into
:class:`~hisim.simulationparameters.SimulationParameters`, runs a setup via
:func:`hisim.hisim_main.main`, reads the resulting ``all_kpis.json``, and
flattens it into a ``{kpi: value}`` mapping.

The pure helpers (``load_config``, ``build_simulation_parameters``,
``resolve_setup_path``, ``filter_config``, ``select_pairs``, ``config_hash``,
``environment_metadata``) are fully unit-testable without running HiSim. Only
:func:`run_one` executes a real simulation, and it is isolated so that
:func:`run_all` (which drives every ``(setup, parameter_set)`` pair) can be
unit-tested with a monkeypatched ``run_one``.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, cast

from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters

try:  # importable both as ``scripts.runner`` (tests) and ``runner`` (CLI from scripts/)
    from golden_kpis import flatten  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from scripts.golden_kpis import flatten


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SetupConfig:
    """One curated system-setup entry from the golden config."""

    id: str
    path: str


@dataclass
class ParameterSetConfig:
    """One parameter-set entry: a ``SimulationParameters`` factory plus options."""

    id: str
    factory: str
    year: int
    seconds_per_timestep: int
    post_processing_options: list[str]
    nondeterministic: bool = False


@dataclass
class GoldenConfig:
    """The golden-reference config: the check output subdir plus setups and parameter sets.

    The results root and the committed-golden directory are runtime paths (CLI
    args / defaults), not config concerns — only ``check_subdir`` (the name of the
    ephemeral fresh-run output directory) lives here.
    """

    check_subdir: str
    setups: list[SetupConfig]
    parameter_sets: list[ParameterSetConfig]


@dataclass
class RunResult:
    """The outcome of running one ``(setup, parameter_set)`` pair."""

    setup_id: str
    parameter_set_id: str
    result_directory: str
    kpis: dict[str, Any]
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------
_REQUIRED_TOP_LEVEL_KEYS = ("check_subdir", "setups", "parameter_sets")


def _validate_factory(factory: str) -> None:
    """Raise ``ValueError`` if ``factory`` is not a callable on ``SimulationParameters``."""
    func = getattr(SimulationParameters, factory, None)
    if func is None or not callable(func):
        raise ValueError(
            f"Unknown SimulationParameters factory {factory!r}; "
            f"must be a classmethod of SimulationParameters (e.g. 'one_week_only')."
        )


def _validate_option_names(option_names: list[str]) -> None:
    """Raise ``ValueError`` if any name is not a real ``PostProcessingOptions`` member."""
    valid = PostProcessingOptions.__members__
    for name in option_names:
        if name not in valid:
            raise ValueError(
                f"Unknown PostProcessingOptions member {name!r}; valid members: {sorted(valid)}."
            )


def load_config(config_path: Path) -> GoldenConfig:
    """Parse and validate ``golden_config.json`` into a :class:`GoldenConfig`.

    Raises:
        FileNotFoundError: if ``config_path`` does not exist.
        ValueError: if the JSON is malformed or violates the schema (missing
            required keys, empty setups/parameter_sets, unknown factory, unknown
            ``PostProcessingOptions`` member).
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Golden config not found: {config_path}")
    raw: Any = json.loads(config_path.read_text())

    if not isinstance(raw, dict):
        raise ValueError("Golden config must be a JSON object.")
    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in raw:
            raise ValueError(f"Golden config missing required key {key!r}.")

    setups_raw = raw["setups"]
    param_sets_raw = raw["parameter_sets"]
    if not isinstance(setups_raw, list) or len(setups_raw) == 0:
        raise ValueError("Golden config 'setups' must be a non-empty list.")
    if not isinstance(param_sets_raw, list) or len(param_sets_raw) == 0:
        raise ValueError("Golden config 'parameter_sets' must be a non-empty list.")

    setups: list[SetupConfig] = []
    for idx, entry in enumerate(setups_raw):
        if not isinstance(entry, dict) or "id" not in entry or "path" not in entry:
            raise ValueError(f"Golden config setups[{idx}] must have 'id' and 'path'.")
        setups.append(SetupConfig(id=entry["id"], path=entry["path"]))

    parameter_sets: list[ParameterSetConfig] = []
    for idx, entry in enumerate(param_sets_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Golden config parameter_sets[{idx}] must be a JSON object.")
        for req in ("id", "factory", "year", "seconds_per_timestep", "post_processing_options"):
            if req not in entry:
                raise ValueError(f"Golden config parameter_sets[{idx}] missing required key {req!r}.")
        _validate_factory(entry["factory"])
        _validate_option_names(entry["post_processing_options"])
        parameter_sets.append(
            ParameterSetConfig(
                id=entry["id"],
                factory=entry["factory"],
                year=int(entry["year"]),
                seconds_per_timestep=int(entry["seconds_per_timestep"]),
                post_processing_options=list(entry["post_processing_options"]),
                nondeterministic=bool(entry.get("nondeterministic", False)),
            )
        )

    return GoldenConfig(
        check_subdir=raw["check_subdir"],
        setups=setups,
        parameter_sets=parameter_sets,
    )


# ---------------------------------------------------------------------------
# Filtering (CI slices: --setup / --param)
# ---------------------------------------------------------------------------
def filter_config(
    config: GoldenConfig, setup_id: Optional[str] = None, param_id: Optional[str] = None
) -> GoldenConfig:
    """Return a copy of ``config`` restricted to the given setup and/or param id.

    Raises:
        ValueError: if a requested id matches no entry (a typo should fail loudly,
            not silently run nothing).
    """
    setups = [s for s in config.setups if setup_id in (None, s.id)]
    params = [p for p in config.parameter_sets if param_id in (None, p.id)]
    if setup_id is not None and not setups:
        raise ValueError(f"No setup with id {setup_id!r} in config.")
    if param_id is not None and not params:
        raise ValueError(f"No parameter set with id {param_id!r} in config.")
    return GoldenConfig(
        check_subdir=config.check_subdir,
        setups=setups,
        parameter_sets=params,
    )


def select_pairs(config: GoldenConfig) -> list[tuple[SetupConfig, ParameterSetConfig]]:
    """Return every ``(setup, parameter_set)`` pair (cartesian product, config order)."""
    return [(setup, param) for setup in config.setups for param in config.parameter_sets]


# ---------------------------------------------------------------------------
# SimulationParameters construction
# ---------------------------------------------------------------------------
def build_simulation_parameters(
    parameter_set: ParameterSetConfig, result_directory: str
) -> SimulationParameters:
    """Build a :class:`SimulationParameters` from a :class:`ParameterSetConfig`.

    Calls ``getattr(SimulationParameters, parameter_set.factory)(year,
    seconds_per_timestep)``, sets ``.result_directory``, and appends each
    ``PostProcessingOptions[name]``. Does **not** call ``enable_all_options``.

    Raises:
        ValueError: if the factory name or any option name is unknown.
    """
    _validate_factory(parameter_set.factory)
    _validate_option_names(parameter_set.post_processing_options)

    factory = cast("Callable[[int, int], SimulationParameters]", getattr(SimulationParameters, parameter_set.factory))
    params = factory(parameter_set.year, parameter_set.seconds_per_timestep)
    params.result_directory = result_directory
    for name in parameter_set.post_processing_options:
        params.post_processing_options.append(PostProcessingOptions[name])
    return params


# ---------------------------------------------------------------------------
# Setup-path resolution
# ---------------------------------------------------------------------------
def resolve_setup_path(setup: SetupConfig, repo_root: Path) -> Path:
    """Resolve ``setup.path`` relative to ``repo_root`` and return the absolute ``Path``.

    Raises:
        FileNotFoundError: if the file does not exist or does not have a ``.py`` suffix.
    """
    resolved = (repo_root / setup.path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Setup script not found: {setup.path} (resolved to {resolved})")
    if resolved.suffix != ".py":
        raise FileNotFoundError(f"Setup script must be a .py file: {setup.path} (resolved to {resolved})")
    return resolved


def resolve_scenario_path(setup: SetupConfig, repo_root: Path) -> Path:
    """Resolve the ``.scenario.json`` sibling of the setup's ``.py`` path.

    The JSON system-setup design mirrors each ``<name>.py`` with a same-named
    ``<name>.scenario.json`` in the same directory. This returns the absolute path
    of that sibling so the JSON golden check can run the identical setup expressed
    as JSON.

    Raises:
        FileNotFoundError: if the ``.scenario.json`` sibling does not exist.
    """
    py_path = Path(setup.path)
    scenario_rel = py_path.parent / f"{py_path.stem}.scenario.json"
    resolved = (repo_root / scenario_rel).resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"Scenario JSON not found for setup {setup.id!r}: {scenario_rel} (resolved to {resolved})"
        )
    return resolved


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_one(
    setup: SetupConfig,
    parameter_set: ParameterSetConfig,
    result_directory: str,
    repo_root: Path,
    mode: str = "python",
) -> RunResult:
    """Run one ``(setup, parameter_set)`` pair and return its flattened KPIs.

    Builds :class:`SimulationParameters`, resolves the setup, runs it, then reads
    and flattens ``<result_directory>/all_kpis.json``. With ``mode="python"``
    (default) it runs the ``.py`` setup via :func:`hisim.hisim_main.main`; with
    ``mode="json"`` it runs the same-named ``.scenario.json`` sibling via
    :func:`hisim.hisim_main.main_json`, passing the *same* built
    :class:`SimulationParameters` so the two runs are directly comparable.

    Any exception (including a missing ``all_kpis.json``, which means the parameter
    set did not enable both ``COMPUTE_KPIS`` and ``WRITE_KPIS_TO_JSON``) is captured
    into :attr:`RunResult.error` as a traceback string. **Never raises.**
    """
    import traceback

    try:
        params = build_simulation_parameters(parameter_set, result_directory)
        # Imported lazily so the pure helpers stay importable without the full
        # HiSim execution stack.
        from hisim import hisim_main

        if mode == "json":
            scenario_path = resolve_scenario_path(setup, repo_root)
            hisim_main.main_json(str(scenario_path), params)
        else:
            setup_path = resolve_setup_path(setup, repo_root)
            hisim_main.main(str(setup_path), params)

        kpi_path = Path(result_directory) / "all_kpis.json"
        if not kpi_path.exists():
            raise FileNotFoundError(
                f"{kpi_path} was not produced — the parameter set must enable both "
                "COMPUTE_KPIS and WRITE_KPIS_TO_JSON."
            )
        kpis = flatten(json.loads(kpi_path.read_text()))
        return RunResult(
            setup_id=setup.id,
            parameter_set_id=parameter_set.id,
            result_directory=result_directory,
            kpis=kpis,
        )
    except Exception:  # noqa: BLE001 - run_one must never raise
        return RunResult(
            setup_id=setup.id,
            parameter_set_id=parameter_set.id,
            result_directory=result_directory,
            kpis={},
            error=traceback.format_exc(),
        )


def run_all(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdir: str, mode: str = "python"
) -> list[RunResult]:
    """Run every ``(setup, parameter_set)`` pair in ``config``.

    For each pair, sets ``result_directory = base_root/subdir/<setup_id>/<param_id>/``,
    creates parent directories, and calls :func:`run_one` in the given ``mode``
    (``"python"`` or ``"json"``). Returns one :class:`RunResult` per pair.
    """
    results: list[RunResult] = []
    for setup, param_set in select_pairs(config):
        result_directory = str(base_root / subdir / setup.id / param_set.id)
        Path(result_directory).mkdir(parents=True, exist_ok=True)
        results.append(run_one(setup, param_set, result_directory, repo_root, mode=mode))
    return results


def run_all_json(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdir: str
) -> list[RunResult]:
    """Run every pair via its ``.scenario.json`` sibling (JSON mode).

    Thin ``mode="json"`` wrapper around :func:`run_all` so it can be injected as
    ``golden_check.main``'s ``run_fn`` (which expects the 4-argument signature).
    """
    return run_all(config, base_root, repo_root, subdir, mode="json")


# ---------------------------------------------------------------------------
# Environment metadata (informational manifest sidecar)
# ---------------------------------------------------------------------------
def config_hash(config_path: Path) -> str:
    """Return the SHA-256 hex digest of the config file's bytes."""
    return hashlib.sha256(config_path.read_bytes()).hexdigest()


def _git_commit() -> str:
    """Best-effort ``git rev-parse HEAD``; return ``"unknown"`` on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"


def environment_metadata(config_path: Path) -> dict[str, str]:
    """Return environment metadata recorded alongside a blessed snapshot.

    Purely informational — the checker never reads it. Includes the HiSim git
    commit, Python version, platform, config SHA-256, and an ISO-8601 timestamp.
    """
    return {
        "hisim_commit": _git_commit(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "config_sha256": config_hash(config_path),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
