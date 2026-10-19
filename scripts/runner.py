"""Config-driven execution core for the golden-reference system.

This module turns a JSON config (``scripts/golden_config.json``) into
:class:`~hisim.simulationparameters.SimulationParameters`, runs a setup via
:func:`hisim.hisim_main.main`, reads the resulting ``all_kpis.json``, and
turns it into the golden leaf map (``scripts/golden_kpis.py``, :func:`golden_leaves`).
An entry whose ``path`` names an energy-system file (``*.energy_system.yaml``) instead of a
Python setup runs that file through the declarative executor (:func:`run_energy_system_file`).

The pure helpers (``load_config``, ``build_simulation_parameters``,
``resolve_setup_path``, ``filter_config``, ``select_pairs``, ``config_hash``,
``environment_metadata``) are fully unit-testable without running HiSim. Only
:func:`run_one` executes a real simulation, and it is isolated so that
:func:`run_all` (which drives every ``(setup, parameter_set)`` pair) can be
unit-tested with a monkeypatched ``run_one``.

With ``jobs`` above one, :func:`run_all` runs each pair in a child process of its own instead
(:class:`ChildRuns`), several at once: setups mutate module state, so two pairs must not share an
interpreter, and a pair that crashes or is killed for memory then fails alone, as a ``run_error``
of its own pair. The child is this file, run as a script with :data:`ChildRuns.CHILD_FLAG`.
"""
from __future__ import annotations

import datetime
import functools
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, cast

from hisim.energy_system.assemblies.twins import COMPOSED_TWINS, ComposedTwin, rename_address
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters

try:  # importable both as ``scripts.runner`` (tests) and ``runner`` (CLI from scripts/)
    from golden_kpis import golden_leaves, renamed_leaves  # type: ignore[import-not-found]
    from golden_horizons import HorizonVocabulary  # type: ignore[import-not-found]
    from golden_matrix import pair_seconds  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from scripts.golden_kpis import golden_leaves, renamed_leaves
    from scripts.golden_horizons import HorizonVocabulary
    from scripts.golden_matrix import pair_seconds


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SetupConfig:
    """One curated system-setup entry from the golden config."""

    id: str
    path: str
    #: The horizons this setup participates in (``"day"``/``"week"``/``"year"``), or
    #: ``None`` for all of them. Every shipped setup runs both the week and the full year
    #: (owner decision 2026-10-11: every system setup has a full-year golden), so the
    #: shipped config sets this nowhere; it stays for a setup that may join one horizon only.
    horizons: Optional[list[str]] = None
    #: The recorded wall time of each of its pairs on a GitHub runner, in seconds, by parameter-set
    #: id: the config's ``seconds`` entry. The CI shards are balanced by it, and a pair run in a
    #: child process gets a timeout from it (:meth:`ChildRuns.timeout_in_seconds`).
    seconds: dict[str, float] = field(default_factory=dict)

    def recorded_seconds(self, parameter_set_id: str) -> float:
        """Return the recorded wall time of this setup's pair with one parameter set, in seconds.

        A pair the config records no time for weighs the shard balance's default
        (``golden_matrix.DEFAULT_PAIR_SECONDS``, a typical building-sizer week), so the matrix and
        the runner read the same number for every pair.

        Args:
            parameter_set_id: The id of the parameter set the pair runs.

        Returns:
            The recorded wall time in seconds, or the default when none is recorded.
        """
        return float(pair_seconds({"id": self.id, "seconds": self.seconds}, parameter_set_id))

    def is_energy_system_file(self) -> bool:
        """Whether this entry names an energy-system file rather than a Python setup.

        An entry is either a Python setup, ``system_setups/<stem>.py``, or an energy system written
        in YAML alone, ``energy_systems/<stem>.energy_system.yaml``, a file with no Python setup
        behind it. The second kind is recognised by the suffixes the ``hisim`` command line itself
        dispatches on (:class:`hisim.hisim_main.EnergySystemMode`), so the runner and the command
        line agree on what an energy-system file is.

        Returns:
            True when :attr:`path` ends in ``.energy_system.yaml`` or ``.energy_system.yml``.
        """
        # Imported here so the pure helpers stay importable without the HiSim execution stack.
        from hisim.hisim_main import EnergySystemMode

        return self.path.endswith(EnergySystemMode.SUFFIXES)

    def runs_factory(self, factory: str) -> bool:
        """Whether this setup participates in parameter sets built by ``factory``.

        The rule itself lives in :class:`HorizonVocabulary`, shared with the CI matrix
        emitter, so the pairs the gate fans out and the pairs a local run selects can never
        silently disagree.

        Args:
            factory: the ``SimulationParameters`` factory name of a parameter set.

        Returns:
            bool: True when the setup carries no restriction, or the factory's horizon
            is among the ones it names.
        """
        return bool(HorizonVocabulary.runs_factory(self.horizons, factory))

    def runs_local_lpg_household(self, repo_root: Path) -> bool:
        """Whether this setup computes a household with the local LoadProfileGenerator.

        The answer is read from the setup's recorded energy system (:func:`resolve_twin_path`),
        not from running it: a household is a ``UtspLpgConnector`` entry, and it is computed
        locally when its configuration, preset and ``config`` block applied, has the data
        acquisition mode ``USE_LOCAL_LPG``. Example: ``household_gas_building_sizer`` records
        ``data_acquisition_mode: USE_LOCAL_LPG`` on its ``UTSPConnector`` and is recognised;
        ``basic_household`` uses its preset's shipped profile (``USE_PREDEFINED_PROFILE``) and is
        not. Such a setup computes the household's whole calendar year on a cache miss, whatever
        its run's window (:meth:`ChildRuns.lpg_year_allowance_in_seconds`).

        A setup whose own file does not exist is not recognised: its child fails at once on the
        missing file (:func:`resolve_setup_path`) and reports that itself.

        Args:
            repo_root: The repository the setup's path and its recorded energy system lie in.

        Returns:
            True when at least one household of the setup is computed by the local LPG.

        Raises:
            FileNotFoundError: if the setup's file exists but its recorded energy system does not.
            ValueError: if the energy system imports assemblies; their households are only known
                after the expansion of imports, which this reading does not do.
        """
        if not (repo_root / self.path).exists():
            return False
        # Imported here so the pure helpers stay importable without the HiSim execution stack.
        from hisim.components.loadprofilegenerator_utsp_connector import (
            LpgDataAcquisitionMode,
            UtspLpgConnectorConfig,
        )
        from hisim.energy_system.classes import validate_classes
        from hisim.energy_system.configure import EntryConfigurator
        from hisim.energy_system.groups import expand_groups
        from hisim.energy_system.loader import load_energy_system
        from hisim.energy_system.path_resolver import PathResolver

        twin = resolve_twin_path(self, repo_root)
        model = load_energy_system(twin)
        if model.imports:
            raise ValueError(
                f"{twin} imports assemblies ({', '.join(model.imports)}); the golden runner reads its "
                "households only from a file without imports, such as a recorded energy system."
            )
        expanded, _ = expand_groups(model)
        resolver = PathResolver.default()
        for binding in validate_classes(expanded):
            if issubclass(binding.config_class, UtspLpgConnectorConfig):
                _, config = EntryConfigurator(binding, resolver, expanded.addresses.get(binding.name)).build()
                if config.data_acquisition_mode is LpgDataAcquisitionMode.USE_LOCAL_LPG:
                    return True
        return False


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
    #: An explicit ``(setup id, parameter set id)`` list, in the order to run it, or ``None`` for
    #: every pair the horizons allow. Set by :func:`filter_config` for a CI shard.
    pairs: Optional[list[tuple[str, str]]] = None


@dataclass
class RunResult:
    """The outcome of running one ``(setup, parameter_set)`` pair."""

    setup_id: str
    parameter_set_id: str
    result_directory: str
    #: The golden leaf map of the run (``scripts.golden_kpis.golden_leaves``): dotted address -> leaf.
    kpis: dict[str, Any]
    error: Optional[str] = None
    #: Wall time of the run in seconds; what ``golden_config.json``'s ``seconds`` weights come from.
    duration_s: Optional[float] = None


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
    if not isinstance(setups_raw, list) or not setups_raw:
        raise ValueError("Golden config 'setups' must be a non-empty list.")
    if not isinstance(param_sets_raw, list) or not param_sets_raw:
        raise ValueError("Golden config 'parameter_sets' must be a non-empty list.")

    setups: list[SetupConfig] = []
    for idx, entry in enumerate(setups_raw):
        if not isinstance(entry, dict) or "id" not in entry or "path" not in entry:
            raise ValueError(f"Golden config setups[{idx}] must have 'id' and 'path'.")
        horizons = entry.get("horizons")
        HorizonVocabulary.check_horizons(horizons, f"Golden config setups[{idx}]")
        pair_seconds(entry, "")  # refuses a malformed 'seconds' entry
        setups.append(
            SetupConfig(id=entry["id"], path=entry["path"], horizons=horizons, seconds=dict(entry.get("seconds", {})))
        )

    parameter_sets: list[ParameterSetConfig] = []
    for idx, entry in enumerate(param_sets_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Golden config parameter_sets[{idx}] must be a JSON object.")
        for req in ("id", "factory", "year", "seconds_per_timestep", "post_processing_options"):
            if req not in entry:
                raise ValueError(f"Golden config parameter_sets[{idx}] missing required key {req!r}.")
        _validate_factory(entry["factory"])
        HorizonVocabulary.check_factory(entry["factory"], f"Golden config parameter_sets[{idx}]")
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
def parse_pair(token: str) -> tuple[str, str]:
    """Split one ``setup:param`` token, as a CI shard lists its pairs.

    Raises:
        ValueError: if the token is not two non-empty ids joined by one colon.
    """
    setup_id, sep, param_id = token.partition(":")
    if not sep or not setup_id or not param_id or ":" in param_id:
        raise ValueError(f"A pair is written 'setup:param', not {token!r}.")
    return setup_id, param_id


def filter_config(
    config: GoldenConfig,
    setup_id: Optional[str] = None,
    param_id: Optional[str] = None,
    pairs: Optional[Sequence[tuple[str, str]]] = None,
) -> GoldenConfig:
    """Return a copy of ``config`` restricted to the given setup and/or param id, or to ``pairs``.

    ``pairs`` is a CI shard: exactly those pairs, run in the given order. It cannot be combined
    with ``setup_id`` / ``param_id``.

    Raises:
        ValueError: if a requested id matches no entry (a typo should fail loudly,
            not silently run nothing), or if both ids are given and the setup's
            ``horizons`` exclude that parameter set — asking explicitly for a pair
            the gate never runs is a mistake, not an empty run. The same holds for
            every entry of ``pairs``, which must also not repeat a pair.
    """
    if pairs is not None:
        if setup_id is not None or param_id is not None:
            raise ValueError("Pass either an explicit pair list or --setup/--param, not both.")
        if not pairs:
            raise ValueError("An explicit pair list must name at least one pair.")
        if len(set(pairs)) != len(pairs):
            raise ValueError(f"The pair list repeats a pair: {list(pairs)}.")
        for pair_setup, pair_param in pairs:
            filter_config(config, setup_id=pair_setup, param_id=pair_param)  # refuses an unknown or excluded pair
        setup_ids = {pair_setup for pair_setup, _ in pairs}
        param_ids = {pair_param for _, pair_param in pairs}
        return GoldenConfig(
            check_subdir=config.check_subdir,
            setups=[s for s in config.setups if s.id in setup_ids],
            parameter_sets=[p for p in config.parameter_sets if p.id in param_ids],
            pairs=list(pairs),
        )
    setups = [s for s in config.setups if setup_id in (None, s.id)]
    params = [p for p in config.parameter_sets if param_id in (None, p.id)]
    if setup_id is not None and not setups:
        raise ValueError(f"No setup with id {setup_id!r} in config.")
    if param_id is not None and not params:
        raise ValueError(f"No parameter set with id {param_id!r} in config.")
    if setup_id is not None and param_id is not None and not setups[0].runs_factory(params[0].factory):
        raise ValueError(
            f"Setup {setup_id!r} does not run parameter set {param_id!r}: its horizons are "
            f"{setups[0].horizons}. Asking for the pair explicitly is a mistake, not an empty run."
        )
    return GoldenConfig(
        check_subdir=config.check_subdir,
        setups=setups,
        parameter_sets=params,
    )


def select_pairs(config: GoldenConfig) -> list[tuple[SetupConfig, ParameterSetConfig]]:
    """Return every ``(setup, parameter_set)`` pair the config's horizons allow, in config order.

    A setup that restricts its horizons is simply absent from the other horizons' pairs;
    the golden matrix applies the identical rule, so CI slices and a full local run agree
    on what the gate covers. An explicit :attr:`GoldenConfig.pairs` list is returned as given,
    in its order (:func:`filter_config` has already checked every entry).
    """
    if config.pairs is not None:
        setups = {s.id: s for s in config.setups}
        params = {p.id: p for p in config.parameter_sets}
        return [(setups[setup_id], params[param_id]) for setup_id, param_id in config.pairs]
    return [
        (setup, param)
        for setup in config.setups
        for param in config.parameter_sets
        if setup.runs_factory(param.factory)
    ]


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

    The path names either a Python setup (``.py``) or an energy-system file
    (``.energy_system.yaml`` or ``.energy_system.yml``, :meth:`SetupConfig.is_energy_system_file`).

    Raises:
        FileNotFoundError: if the file does not exist or is neither a ``.py`` file nor an
            energy-system file.
    """
    resolved = (repo_root / setup.path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Setup script not found: {setup.path} (resolved to {resolved})")
    if resolved.suffix != ".py" and not setup.is_energy_system_file():
        # Imported here so the pure helpers stay importable without the HiSim execution stack.
        from hisim.hisim_main import EnergySystemMode

        raise FileNotFoundError(
            "Setup must be a .py file or an energy-system file "
            f"(*{' or *'.join(EnergySystemMode.SUFFIXES)}): {setup.path} (resolved to {resolved})"
        )
    return resolved


def resolve_twin_path(setup: SetupConfig, repo_root: Path) -> Path:
    """Resolve the recorded ``.energy_system.yaml`` twin of the setup.

    Every setup has a committed twin in ``energy_systems/``, produced by the recorder and
    held current by the energy-system freshness gate. This returns the absolute path of
    that twin so the YAML golden check can run the identical system through the
    declarative executor.

    An entry that names an energy-system file (:meth:`SetupConfig.is_energy_system_file`) has
    no recorded twin: it is a declarative file itself, so this returns that file, and YAML mode
    runs it a second time through the executor directly.

    Raises:
        FileNotFoundError: if the twin does not exist; the message names the recording
            command that produces it.
    """
    if setup.is_energy_system_file():
        return resolve_setup_path(setup, repo_root)
    twin_rel = Path("energy_systems") / f"{Path(setup.path).stem}.energy_system.yaml"
    resolved = (repo_root / twin_rel).resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"Recorded twin not found for setup {setup.id!r}: {twin_rel} (resolved to {resolved}). "
            "Record it with 'hisim energy-system record'."
        )
    return resolved


def composed_twin_of(setup: SetupConfig) -> Optional[ComposedTwin]:
    """The setup's entry in the composed-twin table (:data:`COMPOSED_TWINS`), or ``None`` if it has no composed file."""
    return COMPOSED_TWINS.get(Path(setup.path).stem)


def resolve_composed_path(setup: SetupConfig, repo_root: Path) -> tuple[Path, ComposedTwin]:
    """Resolve the composed file of the setup and return it with its table entry (``composed`` mode).

    Raises:
        KeyError: if the setup has no entry in :data:`COMPOSED_TWINS`; :func:`run_modes` never asks for one.
        FileNotFoundError: if the entry's composed file does not exist.
    """
    twin = composed_twin_of(setup)
    if twin is None:
        raise KeyError(f"Setup {setup.id!r} has no composed file in hisim/energy_system/assemblies/twins.py.")
    resolved = (repo_root / "energy_systems" / twin.composed).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Composed file not found for setup {setup.id!r}: {resolved}.")
    return resolved, twin


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_energy_system_file(path: Path, params: SimulationParameters) -> None:
    """Run one energy-system file the way ``hisim energy-system run`` runs it, with parameters built here.

    The command line's :func:`hisim.energy_system.executor.run_energy_system` reads its parameters
    from a file; the golden runner builds them in memory from the config's parameter set
    (:func:`build_simulation_parameters`). Apart from that, this does what it does: one
    calculation (:class:`hisim.calculation_scope.CalculationScope`, with its write guard) around
    the run both share, :func:`hisim.energy_system.executor.simulate_energy_system`: the file
    built, its run records written, every time step simulated and the post-processing run.

    Args:
        path: The ``*.energy_system.yaml`` file.
        params: The simulation parameters, their result directory already set.
    """
    # Imported here so the pure helpers stay importable without the HiSim execution stack.
    from hisim.calculation_scope import CalculationScope
    from hisim.energy_system.executor import simulate_energy_system

    with CalculationScope.open(label=str(path)):
        simulate_energy_system(str(path), params)


def run_one(
    setup: SetupConfig,
    parameter_set: ParameterSetConfig,
    result_directory: str,
    repo_root: Path,
    mode: str = "python",
) -> RunResult:
    """Run one ``(setup, parameter_set)`` pair and return its golden KPI leaves.

    Builds :class:`SimulationParameters`, resolves the setup, runs it, then reads
    turns ``<result_directory>/all_kpis.json`` into golden leaves. With ``mode="python"``
    (default) it runs the ``.py`` setup via :func:`hisim.hisim_main.main`, or, for an entry that
    names an energy-system file, that file via :func:`run_energy_system_file`; with
    ``mode="yaml"`` it runs the recorded ``.energy_system.yaml`` twin through the
    declarative executor, and with ``mode="composed"`` the setup's composed file
    (:func:`resolve_composed_path`), whose KPI leaves are then renamed to the twin's
    names (:func:`~hisim.energy_system.assemblies.twins.rename_address`). All receive the *same* built
    :class:`SimulationParameters`, so the runs are directly comparable. The oracle is
    the KPI set, deliberately: the legacy and declarative paths name aggregator result
    columns differently (C-P3.2), but KPIs do not depend on column names.

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

        rename: Optional[Mapping[str, str]] = None
        if mode in ("yaml", "composed"):
            from hisim.energy_system.executor import build_energy_system

            if mode == "composed":
                path, composed = resolve_composed_path(setup, repo_root)
                rename = composed.rename
            else:
                path = resolve_twin_path(setup, repo_root)
            built = build_energy_system(str(path), params)
            built.simulator.run_all_timesteps()
        elif setup.is_energy_system_file():
            run_energy_system_file(resolve_setup_path(setup, repo_root), params)
        else:
            setup_path = resolve_setup_path(setup, repo_root)
            hisim_main.main(str(setup_path), params)

        kpi_path = Path(result_directory) / "all_kpis.json"
        if not kpi_path.exists():
            raise FileNotFoundError(
                f"{kpi_path} was not produced — the parameter set must enable both "
                "COMPUTE_KPIS and WRITE_KPIS_TO_JSON."
            )
        kpis = golden_leaves(json.loads(kpi_path.read_text()))
        if rename is not None:
            kpis = renamed_leaves(kpis, functools.partial(rename_address, mapping=rename), str(kpi_path))
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


@dataclass
class PairTask:
    """One run to make: a pair in one mode, where its results and its child's files go."""

    setup: SetupConfig
    parameter_set: ParameterSetConfig
    result_directory: str
    mode: str
    work_dir: Path


def run_modes(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdirs: dict[str, str], jobs: int = 1
) -> dict[str, list[RunResult]]:
    """Run every pair of ``config`` in each mode of ``subdirs`` (mode -> result subdirectory).

    A pair's result directory is ``base_root/<subdir of the mode>/<setup_id>/<param_id>/``. The
    runs are ordered pair by pair, each pair's modes side by side (the Python setup, then its
    YAML twin), so with ``jobs`` above one a pair's two runs go into the one pool of child
    processes together and the heaviest pair — first in a CI shard — starts both at once. With
    ``jobs`` of one everything runs in this process, one after another. Returns each mode's
    results in pair order; ``composed`` mode has a result only for the pairs whose setup has a
    composed file (:func:`composed_twin_of`).
    """
    tasks: list[PairTask] = []
    for setup, param_set in select_pairs(config):
        for mode, subdir in subdirs.items():
            if mode == "composed" and composed_twin_of(setup) is None:
                continue  # no composed file: golden_check.py lists the pair as skipped
            result_directory = base_root / subdir / setup.id / param_set.id
            result_directory.mkdir(parents=True, exist_ok=True)
            tasks.append(
                PairTask(setup, param_set, str(result_directory), mode, base_root / subdir / ChildRuns.WORK_SUBDIR)
            )
    if jobs > 1:
        results = ChildRuns.run_many(tasks, repo_root, jobs)
    else:
        results = []
        for task in tasks:
            started = time.monotonic()
            result = run_one(task.setup, task.parameter_set, task.result_directory, repo_root, mode=task.mode)
            result.duration_s = round(time.monotonic() - started, 1)
            results.append(result)
    return {mode: [r for t, r in zip(tasks, results) if t.mode == mode] for mode in subdirs}


def run_all(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdir: str, mode: str = "python", jobs: int = 1
) -> list[RunResult]:
    """Run every ``(setup, parameter_set)`` pair in ``config`` in one mode.

    For each pair, sets ``result_directory = base_root/subdir/<setup_id>/<param_id>/``,
    creates parent directories, and calls :func:`run_one` in the given ``mode``
    (``"python"`` or ``"yaml"``). With ``jobs`` above one the pairs run in child processes,
    ``jobs`` at a time (:class:`ChildRuns`). Returns one :class:`RunResult` per pair, in pair
    order either way. :func:`run_modes` is the same for several modes at once.
    """
    return run_modes(config, base_root, repo_root, {mode: subdir}, jobs)[mode]


def run_all_yaml(
    config: GoldenConfig, base_root: Path, repo_root: Path, subdir: str, jobs: int = 1
) -> list[RunResult]:
    """Run every pair via its recorded ``.energy_system.yaml`` twin (YAML mode).

    Thin ``mode="yaml"`` wrapper around :func:`run_all` so it can be injected as
    ``golden_check.main``'s ``run_fn`` (which expects the 4-argument signature).
    """
    return run_all(config, base_root, repo_root, subdir, mode="yaml", jobs=jobs)


class ChildRuns:
    """Runs pairs in child processes, a few at a time, the way ``record_all_setups.py --jobs`` does.

    Each child is this file run as a script with :data:`CHILD_FLAG` and one JSON argument naming
    the pair; it calls :func:`run_one` and writes the :class:`RunResult` to a file the parent
    reads back. Everything the child prints goes to ``<pair>.log`` in :data:`WORK_SUBDIR`, and a
    child that ends without writing its result — a crash of the interpreter, or the kernel's
    out-of-memory killer — becomes a ``run_error`` of that pair carrying the log's last lines. A child
    that runs longer than its timeout (:meth:`timeout_in_seconds`) is killed together with the
    processes it started (:meth:`kill_with_its_children`) and becomes a ``run_error`` of its pair
    the same way, so a hung simulation fails its pair by name instead of holding a CI runner until
    the job's own limit.

    The children keep the process-id default of the LoadProfileGenerator base index
    (``PylpgWorkspace.default_base_index``) rather than borrowing from an ``LpgBaseIndexPool``:
    a child killed mid-calculation leaves its ``C<index>`` directory behind, and with a pool the
    next child borrowing that index would refuse to start in it.
    """

    #: The argument that makes this file run one pair instead of being a library.
    CHILD_FLAG = "--run-pair"
    #: Where the children's logs and result files go, below the check's own result directory.
    WORK_SUBDIR = "_children"
    #: How much of a failed child's log its ``run_error`` carries.
    LOG_TAIL_LINES = 40
    #: A child's timeout as a multiple of its pair's recorded wall time. The recorded times are
    #: measured on a GitHub runner; a composed run beside its Python pair took up to 1.3 times its
    #: pair's weight there (2026-10-13), so three leaves room for a slower machine.
    TIMEOUT_TO_RECORDED_SECONDS_RATIO = 3.0
    #: The shortest timeout a child gets, in seconds, so a pair recorded at a few seconds still
    #: has time to import HiSim and read its inputs on a busy machine.
    MINIMAL_TIMEOUT_IN_SECONDS = 600.0
    #: The time added to the timeout of a pair that runs a local LoadProfileGenerator household and
    #: covers less than a full year, in seconds (owner decision 2026-10-19, "LPG year allowance"): on
    #: a cache miss such a pair first computes the household's whole calendar year, which its recorded
    #: time does not contain. Source: CI run 39254406710 of #1030 (job 118081327774, cold household
    #: cache, four children at once). One LPG year took 130 to 155 s there, but the generator computes
    #: one household at a time per installation (``PylpgWorkspace.exclusive_generator_access``), so
    #: four local-LPG week pairs started together finished after 156, 304, 462 and 593 s, while the
    #: same pairs take about 20 s with a warm cache (job 118081280128). The last of four children waits
    #: for four LPG years, about 620 s; 900 s covers that with half as much again for a slower runner.
    LPG_YEAR_ALLOWANCE_IN_SECONDS = 900.0

    #: Whether a timed-out child is killed together with every process it started (POSIX), or alone
    #: (Windows, which has no process groups to kill).
    KILLS_PROCESS_GROUP = hasattr(os, "killpg")

    @classmethod
    def kill_with_its_children(cls, child: subprocess.Popen) -> None:
        """Kill a timed-out child and every process it started, then wait for the child.

        The child runs in a session of its own (``start_new_session``), so its process id is also
        the id of its process group, and ``os.killpg`` reaches the processes it started too: a
        LoadProfileGenerator calculation the child is waiting for, which killing the child alone
        would leave running on the machine. Example: a full-year pair hangs inside the LPG; the
        Python child and the LPG binary both receive SIGKILL. Where there are no process groups
        (Windows), only the child is killed.

        Args:
            child: The child process, started with ``start_new_session=KILLS_PROCESS_GROUP``.
        """
        if cls.KILLS_PROCESS_GROUP:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the whole group ended between the timeout and the kill
        else:
            child.kill()
        child.wait()

    @classmethod
    def lpg_year_allowance_in_seconds(
        cls, setup: SetupConfig, parameter_set: ParameterSetConfig, repo_root: Path
    ) -> float:
        """Return the time a pair's timeout gets for computing a full LoadProfileGenerator year, in seconds.

        The LoadProfileGenerator always computes a household's whole calendar year, and a run cuts
        its window from it. A pair that covers less than a full year and runs a local LPG household
        (:meth:`SetupConfig.runs_local_lpg_household`) therefore needs time on a cache miss that its
        recorded wall time does not contain, and gets :data:`LPG_YEAR_ALLOWANCE_IN_SECONDS`. A
        full-year pair gets nothing: its recorded time already contains the LPG year. A pair without
        a local LPG household gets nothing either. Example: ``household_gas_building_sizer`` gets
        900 s for its week and nothing for its full year; ``basic_household`` gets nothing.

        Args:
            setup: The pair's setup.
            parameter_set: The pair's parameter set.
            repo_root: The repository the setup's recorded energy system lies in.

        Returns:
            :data:`LPG_YEAR_ALLOWANCE_IN_SECONDS` or 0.
        """
        if HorizonVocabulary.FACTORY_HORIZONS.get(parameter_set.factory) == "year":
            return 0.0
        return cls.LPG_YEAR_ALLOWANCE_IN_SECONDS if setup.runs_local_lpg_household(repo_root) else 0.0

    @classmethod
    def timeout_in_seconds(cls, setup: SetupConfig, parameter_set: ParameterSetConfig, repo_root: Path) -> float:
        """Return how long the child running one pair may run before it is killed, in seconds.

        The timeout is :data:`TIMEOUT_TO_RECORDED_SECONDS_RATIO` times the pair's recorded wall
        time, at least :data:`MINIMAL_TIMEOUT_IN_SECONDS`, plus the pair's allowance for a full
        LoadProfileGenerator year (:meth:`lpg_year_allowance_in_seconds`). Example: the heat pump
        with car records 690 s for its full year, so its child is killed after 2070 s; its week,
        recorded at 124 s, runs a local LPG household and gets the minimum of 600 s plus 900 s, so
        1500 s; ``basic_household``'s week, recorded at 17 s, gets the minimum of 600 s.

        Args:
            setup: The pair's setup, which carries the recorded wall times.
            parameter_set: The pair's parameter set.
            repo_root: The repository the setup's recorded energy system lies in.

        Returns:
            The timeout in seconds.
        """
        recorded = setup.recorded_seconds(parameter_set.id)
        return max(
            cls.TIMEOUT_TO_RECORDED_SECONDS_RATIO * recorded, cls.MINIMAL_TIMEOUT_IN_SECONDS
        ) + cls.lpg_year_allowance_in_seconds(setup, parameter_set, repo_root)

    @classmethod
    def command(cls, payload: str) -> list[str]:
        """Return the command line of one child; a seam the tests replace."""
        return [sys.executable, str(Path(__file__).resolve()), cls.CHILD_FLAG, payload]

    @classmethod
    def run_in_child(
        cls,
        setup: SetupConfig,
        parameter_set: ParameterSetConfig,
        result_directory: str,
        repo_root: Path,
        mode: str,
        work_dir: Path,
    ) -> RunResult:
        """Run one pair in a child process and return its result. **Never raises** for the child's sake.

        Every path handed to the child is absolute, because the child runs in ``repo_root`` and a
        relative ``--results-root`` would otherwise name a different directory there.
        """
        work_dir = work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{setup.id}__{parameter_set.id}"
        log_path = work_dir / f"{stem}.log"
        out_path = work_dir / f"{stem}.result.json"
        out_path.unlink(missing_ok=True)
        payload = json.dumps(
            {
                "setup": asdict(setup),
                "parameter_set": asdict(parameter_set),
                "result_directory": str(Path(result_directory).resolve()),
                "repo_root": str(repo_root.resolve()),
                "mode": mode,
                "out": str(out_path),
            }
        )
        # The child imports hisim and its sibling scripts; neither has to be installed for it.
        env = dict(os.environ)
        repo_of_this_file = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (repo_of_this_file, env.get("PYTHONPATH", "")) if p)
        lpg_year_allowance_in_seconds = cls.lpg_year_allowance_in_seconds(setup, parameter_set, repo_root)
        timeout_in_seconds = cls.timeout_in_seconds(setup, parameter_set, repo_root)
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as log:
            # A session of its own makes the child the leader of a process group that also holds the
            # processes it starts, so a timeout can end the LoadProfileGenerator binary along with it.
            with subprocess.Popen(
                cls.command(payload),
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=repo_root,
                env=env,
                start_new_session=cls.KILLS_PROCESS_GROUP,
            ) as child:
                returncode: Optional[int]
                try:
                    returncode = child.wait(timeout=timeout_in_seconds)
                except subprocess.TimeoutExpired:
                    cls.kill_with_its_children(child)
                    returncode = None
        duration = round(time.monotonic() - started, 1)
        if returncode == 0 and out_path.exists():
            result = RunResult(**json.loads(out_path.read_text(encoding="utf-8")))
            result.duration_s = duration
            return result
        if returncode is None:
            allowance = (
                f", plus {lpg_year_allowance_in_seconds:g} s for computing its household's full "
                "LoadProfileGenerator year"
                if lpg_year_allowance_in_seconds
                else ""
            )
            how = (
                f"ran longer than its timeout of {timeout_in_seconds:g} s "
                f"({cls.TIMEOUT_TO_RECORDED_SECONDS_RATIO:g} times the {setup.recorded_seconds(parameter_set.id):g} s "
                f"recorded for {setup.id} / {parameter_set.id} in golden_config.json, at least "
                f"{cls.MINIMAL_TIMEOUT_IN_SECONDS:g} s{allowance}) and was killed"
            )
        else:
            code = returncode
            how = f"was killed by signal {-code}" if code < 0 else f"exited with {code}"
            if code in (-9, 137):
                how += " (SIGKILL: on a CI runner, most likely the out-of-memory killer)"
        lines = [line for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        tail = "\n".join(lines[-cls.LOG_TAIL_LINES:]) or "(the child wrote nothing)"
        return RunResult(
            setup_id=setup.id,
            parameter_set_id=parameter_set.id,
            result_directory=result_directory,
            kpis={},
            error=f"The child process running this pair {how} without reporting a result. "
            f"Last lines of {log_path}:\n{tail}\n",
            duration_s=duration,
        )

    @classmethod
    def run_many(cls, tasks: Sequence[PairTask], repo_root: Path, jobs: int) -> list[RunResult]:
        """Run the tasks ``jobs`` at a time in one pool, printing each verdict as it arrives.

        The tasks start in the given order — a CI shard lists its heaviest pair first — and the
        results come back in that order too, however the children were scheduled.
        """
        results: list[Optional[RunResult]] = [None] * len(tasks)
        finished = 0
        modes = sorted({task.mode for task in tasks})
        print(f"Running {len(tasks)} run(s) ({', '.join(modes)}), {jobs} at a time.", flush=True)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(
                    cls.run_in_child,
                    task.setup,
                    task.parameter_set,
                    task.result_directory,
                    repo_root,
                    task.mode,
                    task.work_dir,
                ): position
                for position, task in enumerate(tasks)
            }
            for future in as_completed(futures):
                result = future.result()
                results[futures[future]] = result
                finished += 1
                status = "RAN  " if result.error is None else "ERROR"
                print(
                    f"[{finished}/{len(tasks)}] {status} {tasks[futures[future]].mode:6} "
                    f"{result.setup_id} / {result.parameter_set_id} ({result.duration_s} s)",
                    flush=True,
                )
        return [result for result in results if result is not None]

    @classmethod
    def child_main(cls, payload: str) -> int:
        """The child's side: run the one pair the payload names and write its result file."""
        data = json.loads(payload)
        result = run_one(
            SetupConfig(**data["setup"]),
            ParameterSetConfig(**data["parameter_set"]),
            data["result_directory"],
            Path(data["repo_root"]),
            mode=data["mode"],
        )
        Path(data["out"]).write_text(json.dumps(asdict(result)), encoding="utf-8")
        return 0


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


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != ChildRuns.CHILD_FLAG:
        sys.exit(f"usage: runner.py {ChildRuns.CHILD_FLAG} <pair as JSON>  (the child of a --jobs run)")
    sys.exit(ChildRuns.child_main(sys.argv[2]))
