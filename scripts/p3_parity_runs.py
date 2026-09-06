#!/usr/bin/env python3
"""The two halves of one parity triple: the Python setup and its recorded twin, run side by side.

TEMPORARY — this module belongs to the P3 migration parity rig (requirements R11) and is deleted
together with the rig in phase P6 (R11.8 amended and AC-P3.20 deferred to P6, 2026-08-31).

The rig's whole claim is that the two paths produce the same simulation, so the two runs have to
differ in nothing a run can be made to share: the same period, the same resolution, the same
post-processing option set, the same interpreter and the same machine. This module is where that
sameness is arranged, and it is separate from the comparison because the two are answerable for
different things — this one for the runs being comparable, the other for what the comparison says.

Two arrangements here are deliberate and neither is incidental. **Each run gets its own empty
cache directory**: HiSim caches a PV system's yearly output to a CSV, so a shared cache would let
the second run read numbers the first one computed, which both masks a genuine configuration
difference and — because a CSV round trip is not bit-exact — injects a spurious one when only one
of the two runs happens to hit the cache. And **the wiring is snapshotted from inside the run**,
by wrapping the simulator's own connect step, because the snapshot needs the automatic default
connections resolved and calling that step a second time from outside would resolve them twice.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple, cast

import pandas as pd

from hisim.energy_system.parity import WiringSnapshot
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters


class ParityWindows:
    """The simulation windows every triple is measured over, and how they are built.

    R11.5 asks for two windows rather than one because every short parameter set HiSim ships
    starts on the first of January, which measures each cooling device, air conditioner and
    solar-thermal collector in the darkest week of the year. The July week is the answer to that,
    and both are named here so the checker, the matrix emitter and the workflow all spell them the
    same way.
    """

    #: Window name to the ``SimulationParameters`` classmethod that builds it.
    FACTORIES: ClassVar[Dict[str, str]] = {
        "january": "one_week_only",
        "july": "one_week_july",
    }

    #: The year both windows are taken from, matching the golden suites' year.
    YEAR: ClassVar[int] = 2021

    #: Resolution of both windows in seconds, matching the golden suites (R11.5).
    SECONDS_PER_TIMESTEP: ClassVar[int] = 60

    @classmethod
    def names(cls) -> Tuple[str, ...]:
        """The window names a caller may ask for, in a stable order.

        Returns:
            The names, January first.
        """
        return tuple(cls.FACTORIES)

    @classmethod
    def build(cls, window: str, result_directory: Path, cache_directory: Path) -> SimulationParameters:
        """Builds the parameters of one side of one triple.

        Args:
            window: One of :meth:`names`.
            result_directory: Where this side writes its results; created when missing.
            cache_directory: This side's private cache directory; created when missing.

        Returns:
            The parameters, with the rig's post-processing option set already applied.

        Raises:
            ValueError: If the window is not one this rig knows.
        """
        if window not in cls.FACTORIES:
            raise ValueError(f"Unknown window {window!r}; choose from {sorted(cls.FACTORIES)}.")
        factory = cast(
            "Callable[[int, int], SimulationParameters]",
            getattr(SimulationParameters, cls.FACTORIES[window]),
        )
        parameters = factory(cls.YEAR, cls.SECONDS_PER_TIMESTEP)
        result_directory.mkdir(parents=True, exist_ok=True)
        cache_directory.mkdir(parents=True, exist_ok=True)
        parameters.result_directory = str(result_directory)
        parameters.cache_dir_path = str(cache_directory)
        parameters.post_processing_options = list(ParitySide.POST_PROCESSING_OPTIONS)
        return parameters


@dataclass
class RunOutcome:
    """Everything one side of a triple produced, including the ways it failed to finish.

    A parity rig cannot treat every exception as a failure of parity. A crash inside the
    simulation loop means the run produced nothing to compare and is a hard failure; a crash
    inside KPI computation — which seven in-scope setups do today (R11.4) — leaves a complete
    result frame behind and only costs the third comparison. Keeping the two apart is what lets
    those seven setups still receive a structural verdict instead of an error, so the crash is
    recorded here rather than raised.
    """

    #: The resolved wiring, taken while the simulator still held its components.
    snapshot: Optional[WiringSnapshot] = None

    #: The result frame, which is the content of ``all_results.csv``.
    frame: Optional[pd.DataFrame] = None

    #: The flattened ``all_kpis.json``, when KPI computation produced one.
    kpis: Optional[Dict[str, Any]] = None

    #: The traceback of a crash inside post-processing, which does not invalidate the frame.
    kpi_error: Optional[str] = None

    #: The traceback of a crash that left no result frame at all.
    fatal_error: Optional[str] = None


class ParitySide:
    """Runs one side of a triple and reports what it produced, never raising for a KPI crash.

    Both sides go through :meth:`finish`, so the wiring capture, the KPI-crash tolerance and the
    reading of ``all_kpis.json`` are written once and cannot drift between the path under test and
    the path it is being compared against. The two entry points differ only in how the simulator
    comes into being: one imports a Python setup module and calls its ``setup_function``, the other
    loads a recorded file through the executor.
    """

    #: The post-processing both sides run. A setup routinely appends options of its own to the
    #: parameters it is handed — plots, reports, scenario exports — and those would make one side
    #: slower than the other and neither of them comparable, so the rig overrides the list after
    #: the setup has run. The two options kept are the ones the third comparison needs.
    POST_PROCESSING_OPTIONS: ClassVar[Tuple[PostProcessingOptions, ...]] = (
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
    )

    #: The file KPI computation writes, read back from the side's own result directory.
    KPI_FILENAME: ClassVar[str] = "all_kpis.json"

    @classmethod
    def python(cls, setup_path: Path, parameters: SimulationParameters) -> RunOutcome:
        """Runs a Python setup module and returns what it produced.

        Args:
            setup_path: The ``system_setups/*.py`` module to run.
            parameters: The parameters this side runs under; the setup may mutate them, and what
                it ends up with is what the run uses, except for the post-processing options.

        Returns:
            The outcome of this side.
        """
        from hisim.hisim_main import initialize_from_python  # noqa: PLC0415

        cls.reset_singletons()
        simulator = initialize_from_python(str(setup_path), parameters, None)
        effective = simulator.get_simulation_parameters()
        effective.post_processing_options = list(cls.POST_PROCESSING_OPTIONS)
        effective.result_directory = parameters.result_directory
        effective.cache_dir_path = parameters.cache_dir_path
        return cls.finish(simulator, Path(effective.result_directory))

    @classmethod
    def declarative(cls, energy_system_path: Path, parameters: SimulationParameters) -> RunOutcome:
        """Runs a recorded energy-system file and returns what it produced.

        Args:
            energy_system_path: The ``energy_systems/*.energy_system.yaml`` twin to run.
            parameters: The parameters this side runs under.

        Returns:
            The outcome of this side.
        """
        from hisim.energy_system.executor import build_energy_system  # noqa: PLC0415

        cls.reset_singletons()
        built = build_energy_system(energy_system_path, parameters)
        return cls.finish(built.simulator, Path(parameters.result_directory))

    @classmethod
    def reset_singletons(cls) -> None:
        """Empties the process-wide simulation repository before a side starts.

        Both sides run in one interpreter, which is what makes the comparison an A/B on one
        machine rather than a comparison of two environments; the price is that HiSim's singleton
        repository would otherwise carry the first side's entries into the second. Resetting it
        keeps the second side reading only what it published itself.
        """
        from hisim.sim_repository_singleton import SingletonSimRepository  # noqa: PLC0415

        SingletonSimRepository().reset()

    @classmethod
    def finish(cls, simulator: Any, result_directory: Path) -> RunOutcome:
        """Runs a prepared simulator, capturing its wiring, its results and any KPI crash.

        Args:
            simulator: A simulator whose components have been registered.
            result_directory: Where this side writes, read back for ``all_kpis.json``.

        Returns:
            The outcome of this side.
        """
        outcome = RunOutcome()
        cls.capture_wiring(simulator, outcome)
        try:
            simulator.run_all_timesteps()
        except Exception:  # noqa: BLE001 - a KPI crash must not end the triple (R11.4)
            outcome.kpi_error = traceback.format_exc()
        outcome.frame = getattr(simulator, "results_data_frame", None)
        if outcome.frame is None and outcome.kpi_error is not None:
            outcome.fatal_error = outcome.kpi_error
            outcome.kpi_error = None
        try:
            outcome.kpis = cls.read_kpis(result_directory)
        except Exception:  # noqa: BLE001 - an unreadable KPI file fails the KPI stage, not the dispatch
            if outcome.kpi_error is None and outcome.fatal_error is None:
                outcome.kpi_error = traceback.format_exc()
        if outcome.kpis is None and outcome.kpi_error is None and outcome.fatal_error is None:
            outcome.kpi_error = f"no {cls.KPI_FILENAME} was written to {result_directory}"
        return outcome

    @classmethod
    def capture_wiring(cls, simulator: Any, outcome: RunOutcome) -> None:
        """Arranges for the wiring to be snapshotted at the one moment it is complete.

        The snapshot must be taken after the simulator has resolved the automatic default
        connections and before it drops its component references at the end of the run, and the
        only point satisfying both is inside the run itself. Wrapping the simulator's own connect
        step is therefore not a trick but the only correct place: calling that step from outside
        would resolve the default connections a second time.

        Args:
            simulator: The simulator about to run.
            outcome: The outcome the snapshot is written into.
        """
        original = simulator.connect_all_components

        def connect_and_snapshot() -> None:
            original()
            outcome.snapshot = WiringSnapshot.from_simulator(simulator)

        simulator.connect_all_components = connect_and_snapshot

    @classmethod
    def read_kpis(cls, result_directory: Path) -> Optional[Dict[str, Any]]:
        """Reads and flattens the KPI file of one side, when there is one.

        Args:
            result_directory: The side's result directory.

        Returns:
            The flattened KPIs, or ``None`` when KPI computation wrote nothing.

        Raises:
            Exception: If the file exists but cannot be parsed or flattened; :meth:`finish`
                records that as the side's KPI error so the triple still receives a verdict.
        """
        path = result_directory / cls.KPI_FILENAME
        if not path.exists():
            return None
        return flatten_kpis(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class TripleInputs:
    """The three paths and the window that identify one triple, resolved once.

    A triple is named by a setup stem and a window, but running it needs four things — the setup
    module, its recorded twin, a place to write and a place to cache — and every one of them is
    derived from those two names. Deriving them here keeps the derivation in one place, and makes
    the checker's own reporting able to name the files it used.
    """

    stem: str
    window: str
    setup_path: Path
    energy_system_path: Path
    work_directory: Path

    #: Names of the two sides, used for their result and cache subdirectories and in the report.
    SIDES: ClassVar[Tuple[str, str]] = ("python", "declarative")

    def side_directories(self, side: str) -> Tuple[Path, Path]:
        """Where one side writes its results and keeps its private cache.

        Args:
            side: One of :attr:`SIDES`.

        Returns:
            The result directory and the cache directory of that side.
        """
        root = self.work_directory / side
        return root / "results", root / "cache"


def flatten_kpis(payload: Any) -> Dict[str, Any]:
    """Flattens an ``all_kpis.json`` tree the way the permanent golden gate does.

    The rig compares KPIs against each other rather than against a reference, but it compares the
    same *set* of numbers the golden gate does, so the flattening is imported from the golden
    tooling rather than written again. The import is guarded because ``scripts/`` is used both as
    a package and as a directory on the path.

    Args:
        payload: The parsed ``all_kpis.json``.

    Returns:
        A mapping of dotted KPI name to value.
    """
    try:
        from golden_kpis import flatten  # type: ignore[import-not-found]  # noqa: PLC0415
    except ModuleNotFoundError:  # pragma: no cover - depends on how scripts/ is on the path
        from scripts.golden_kpis import flatten  # noqa: PLC0415

    return cast(Dict[str, Any], flatten(payload))


@dataclass
class ColumnDifference:
    """One result column whose two runs disagree, with the worst row quoted.

    The rig's failure output has to name what moved, not just say that something did, and the
    natural unit of "what moved" in a result frame is a column plus the timestamp where the two
    runs are furthest apart. Held as data rather than formatted immediately so the report can
    show the worst few and count the rest.
    """

    column: str
    timestamp: str
    expected: float
    actual: float
    absolute: float
    relative: float

    def describe(self) -> str:
        """Renders one differing column for the failure report.

        Returns:
            A single line naming the column, the row and both values.
        """
        return (
            f"{self.column} @ {self.timestamp}: python={self.expected!r} "
            f"declarative={self.actual!r} (abs {self.absolute:.6g}, rel {self.relative:.6g})"
        )


@dataclass
class KpiDifference:
    """One KPI whose two runs disagree.

    Separate from :class:`ColumnDifference` because a KPI has no timestamp and is frequently not a
    number at all — the flattened tree carries strings and nulls — so the two cannot share a
    formatting rule without one of them lying about what it holds.
    """

    name: str
    expected: Any
    actual: Any

    def describe(self) -> str:
        """Renders one differing KPI for the failure report.

        Returns:
            A single line naming the KPI and both values.
        """
        return f"{self.name}: python={self.expected!r} declarative={self.actual!r}"


@dataclass
class Differences:
    """The differing columns and KPIs of one triple, collected for the report.

    Collected rather than printed as they are found, because the report shows the first few of
    each kind and states how many there were in total, and because the tests of the rig assert on
    what it found rather than on what it printed.
    """

    columns: List[ColumnDifference] = field(default_factory=list)
    kpis: List[KpiDifference] = field(default_factory=list)

    #: How many differences of each kind the report quotes before it starts counting.
    QUOTED: ClassVar[int] = 12
