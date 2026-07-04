"""Config-driven execution and artifact-inventory core for the golden-reference system.

This module turns a JSON config (``scripts/golden_config.json``) into
:class:`~hisim.simulationparameters.SimulationParameters`, runs a setup via
:func:`hisim.hisim_main.main`, inventories the resulting output artifacts with
SHA-256 hashes, and assembles a :class:`Manifest` describing the run.

The pure helpers (``load_config``, ``build_simulation_parameters``,
``resolve_setup_path``, ``classify_artifact``, ``compute_sha256``,
``inventory_directory``, ``config_hash``, ``collect_manifest``,
``write_manifest``/``load_manifest``) are fully unit-testable without running
HiSim. Only :func:`run_one` executes a real simulation, and it is isolated so
that :func:`run_all` (which drives every ``(setup, parameter_set)`` pair) can be
unit-tested with a monkeypatched ``run_one``.
"""
from __future__ import annotations

import dataclasses
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
    """The full golden-reference config: output roots plus setups and parameter sets."""

    results_root: str
    golden_subdir: str
    check_subdir: str
    setups: list[SetupConfig]
    parameter_sets: list[ParameterSetConfig]


@dataclass
class ArtifactEntry:
    """One file produced by a simulation run, relative to the result directory."""

    relative_path: str
    sha256: str
    size: int
    kind: str


@dataclass
class RunResult:
    """The outcome of running one ``(setup, parameter_set)`` pair."""

    setup_id: str
    parameter_set_id: str
    result_directory: str
    artifacts: list[ArtifactEntry]
    error: Optional[str] = None


@dataclass
class Manifest:
    """Environment metadata + per-pair results, serialized to ``manifest.json``."""

    hisim_commit: str
    python_version: str
    platform: str
    config_sha256: str
    generated_at: str
    pairs: list[RunResult]


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------
_REQUIRED_TOP_LEVEL_KEYS = ("results_root", "golden_subdir", "check_subdir", "setups", "parameter_sets")


def _validate_factory(factory: str) -> None:
    """Raise ``ValueError`` if ``factory`` is not a callable on ``SimulationParameters``."""
    func = getattr(SimulationParameters, factory, None)
    if func is None or not callable(func):
        raise ValueError(
            f"Unknown SimulationParameters factory {factory!r}; "
            f"must be a classmethod of SimulationParameters (e.g. 'one_day_only')."
        )


def _validate_option_names(option_names: list[str]) -> None:
    """Raise ``ValueError`` if any name is not a real ``PostProcessingOptions`` member."""
    valid = PostProcessingOptions.__members__
    for name in option_names:
        if name not in valid:
            raise ValueError(
                f"Unknown PostProcessingOptions member {name!r}; "
                f"valid members: {sorted(valid)}."
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
        results_root=raw["results_root"],
        golden_subdir=raw["golden_subdir"],
        check_subdir=raw["check_subdir"],
        setups=setups,
        parameter_sets=parameter_sets,
    )


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


# ---------------------------------------------------------------------------
# Artifact classification and inventory
# ---------------------------------------------------------------------------
def classify_artifact(relative_path: str) -> str:
    """Return ``"csv"`` | ``"json"`` | ``"image"`` | ``"other"`` from the file extension.

    Extension matching is case-insensitive.
    """
    suffix = Path(relative_path).suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix in (".png", ".jpg", ".jpeg"):
        return "image"
    return "other"


def compute_sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory_directory(result_directory: Path) -> list[ArtifactEntry]:
    """Recursively walk ``result_directory`` and return one :class:`ArtifactEntry` per file.

    Files are sorted by ``relative_path`` (POSIX-separated, relative to
    ``result_directory``). Directories are skipped. Returns an empty list if
    ``result_directory`` does not exist.
    """
    if not result_directory.exists():
        return []
    entries: list[ArtifactEntry] = []
    for path in sorted(result_directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(result_directory).as_posix()
        entries.append(
            ArtifactEntry(
                relative_path=rel,
                sha256=compute_sha256(path),
                size=path.stat().st_size,
                kind=classify_artifact(rel),
            )
        )
    entries.sort(key=lambda e: e.relative_path)
    return entries


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_one(
    setup: SetupConfig,
    parameter_set: ParameterSetConfig,
    result_directory: str,
    repo_root: Path,
) -> RunResult:
    """Run one ``(setup, parameter_set)`` pair and inventory the output.

    Builds :class:`SimulationParameters`, resolves the setup path, calls
    :func:`hisim.hisim_main.main`, then inventories ``result_directory``.
    Any exception is captured into :attr:`RunResult.error` as a traceback
    string. **Never raises.**
    """
    import traceback

    try:
        params = build_simulation_parameters(parameter_set, result_directory)
        setup_path = resolve_setup_path(setup, repo_root)
        # Imported lazily so the pure helpers stay importable without the full
        # HiSim execution stack.
        from hisim import hisim_main

        hisim_main.main(str(setup_path), params)
        artifacts = inventory_directory(Path(result_directory))
        return RunResult(
            setup_id=setup.id,
            parameter_set_id=parameter_set.id,
            result_directory=result_directory,
            artifacts=artifacts,
        )
    except Exception:  # noqa: BLE001 - run_one must never raise
        return RunResult(
            setup_id=setup.id,
            parameter_set_id=parameter_set.id,
            result_directory=result_directory,
            artifacts=[],
            error=traceback.format_exc(),
        )


def run_all(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdir: str
) -> list[RunResult]:
    """Run every ``(setup, parameter_set)`` pair (cartesian product, config order).

    For each pair, sets ``result_directory = base_root/subdir/<setup_id>/<param_id>/``,
    creates parent directories, and calls :func:`run_one`. Returns one
    :class:`RunResult` per pair.
    """
    results: list[RunResult] = []
    for setup in config.setups:
        for param_set in config.parameter_sets:
            result_directory = str(base_root / subdir / setup.id / param_set.id)
            Path(result_directory).mkdir(parents=True, exist_ok=True)
            results.append(run_one(setup, param_set, result_directory, repo_root))
    return results


# ---------------------------------------------------------------------------
# Manifest
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


def collect_manifest(
    config: GoldenConfig, results: list[RunResult], config_path: Path
) -> Manifest:
    """Assemble a :class:`Manifest` with environment metadata and per-pair results."""
    return Manifest(
        hisim_commit=_git_commit(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        config_sha256=config_hash(config_path),
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        pairs=results,
    )


def write_manifest(manifest: Manifest, path: Path) -> None:
    """Write the manifest as JSON (``indent=2``, ``sort_keys=True``), creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclasses.asdict(manifest), indent=2, sort_keys=True))


def load_manifest(path: Path) -> Manifest:
    """Load a manifest JSON and reconstruct the dataclass tree.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    data = json.loads(path.read_text())
    pairs: list[RunResult] = []
    for pair_data in data["pairs"]:
        artifacts = [
            ArtifactEntry(
                relative_path=a["relative_path"],
                sha256=a["sha256"],
                size=a["size"],
                kind=a["kind"],
            )
            for a in pair_data["artifacts"]
        ]
        pairs.append(
            RunResult(
                setup_id=pair_data["setup_id"],
                parameter_set_id=pair_data["parameter_set_id"],
                result_directory=pair_data["result_directory"],
                artifacts=artifacts,
                error=pair_data.get("error"),
            )
        )
    return Manifest(
        hisim_commit=data["hisim_commit"],
        python_version=data["python_version"],
        platform=data["platform"],
        config_sha256=data["config_sha256"],
        generated_at=data["generated_at"],
        pairs=pairs,
    )
