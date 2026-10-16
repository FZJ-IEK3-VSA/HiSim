#!/usr/bin/env python3
"""The mid-year oracle: whether a window starting mid-year reproduces the same slice of a run from 1 January.

A run from 1 January is correct, so a run of a mid-year window must reproduce the calendar slice of a
run from 1 January to the window's end. This script runs both and compares them, for one setup and one
window, and prints one verdict table. It is how the mid-year-start epic (``roadmap/midyear_start_epic.md``)
proves each of its steps.

**Four runs in one process.** The reference, the perturbed reference and the cold window run each
start from an empty cache directory of their own; the warm window run reuses the cold run's.

1. *Reference*: 1 January to the window's end (to the year's end with ``--reference-through-year-end``).
2. *Perturbed reference*: the same, with every numeric ``initial_*`` field of every component's
   configuration moved (:class:`Perturbation`). A column whose values do not move is memoryless: it
   does not depend on the state the run started from.
3. *Window, cold*: the window alone, from an empty cache.
4. *Window, warm*: the window again, over the cache the cold run filled. It must equal the cold run
   exactly; a window lost on the way through a cache would make a fix a silent no-op.

**Three tiers of columns.**

- *The fixed exact set*: every output of the weather and of the occupancy, and the building's solar
  gains. These are the profiles the epic moves, so they are compared exactly whatever the perturbed
  reference says (:attr:`ColumnTiers.FIXED_EXACT`).
- *The discovered memoryless set*: every other column the perturbation did not move. A column of it
  that the window reproduces joins the exact gate. One it does not reproduce is reported as a false
  alarm, with its component: a component whose state no ``initial_*`` field sets cannot be perturbed,
  so its stateful columns look memoryless. That list is what a later state handover has to cover.
- *The stateful set*: the columns the perturbation moved. Their transient after the window's start is
  reported per day (days 1, 2, 3, 5 and 7), never gated.

The comparison is :class:`hisim.energy_system.parity.ResultComparison`, used as it is: its index check
refuses a reference slice and a window whose timestamps differ, so a window resolved to the wrong row
cannot pass by accident. The tolerance is the relative tolerance of that comparison; it is zero by
default, and the script says so loudly when it is not.

Examples
--------
    python scripts/midyear_oracle.py system_setups/basic_household.py
    python scripts/midyear_oracle.py energy_systems/basic_household.energy_system.yaml --start 2021-12-01 --days 3
    python scripts/midyear_oracle.py system_setups/basic_household.py --seconds-per-timestep 3600 --tolerance 1e-9

Exit status: 0 when the fixed exact set and the cold/warm equality hold, 1 when either fails.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from hisim import component as cp
from hisim.components.building import Building
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.weather import Weather
from hisim.energy_system.parity import ResultComparison
from hisim.simulationparameters import SimulationParameters


@dataclasses.dataclass(frozen=True)
class OracleWindow:
    """The window under test and the resolution both runs use.

    Example: ``OracleWindow(datetime.date(2021, 7, 1), 7, 900)`` is the first week of July at 15 minutes;
    its reference runs from 1 January 2021 to 8 July 2021.
    """

    #: The first day of the window.
    start: datetime.date

    #: The number of days of the window.
    days: int

    #: The resolution of every run, in seconds.
    seconds_per_timestep: int

    #: Whether the reference runs to the end of the year instead of to the window's end. The slice it
    #: is compared over is the same; the longer reference exists because the LoadProfileGenerator's
    #: conversion to UTC fails for a run that ends between the spring and the autumn clock change
    #: (it removes the spring hour and adds the autumn hour only when the run reaches each), until the
    #: epic's PR3 rewrites it.
    reference_through_year_end: bool = False

    #: The window days the stateful report shows the deviation of, counted from 1.
    REPORTED_DAYS: ClassVar[Tuple[int, ...]] = (1, 2, 3, 5, 7)

    @property
    def start_datetime(self) -> datetime.datetime:
        """The window's first instant, midnight of :attr:`start`."""
        return datetime.datetime.combine(self.start, datetime.time())

    @property
    def end_datetime(self) -> datetime.datetime:
        """The instant after the window's last timestep, midnight after its last day."""
        return self.start_datetime + datetime.timedelta(days=self.days)

    @property
    def last_timestep(self) -> datetime.datetime:
        """The label of the window's last timestep."""
        return self.end_datetime - datetime.timedelta(seconds=self.seconds_per_timestep)

    def parameters(self, reference: bool, result_directory: Path, cache_directory: Path) -> SimulationParameters:
        """Build the parameters of one run: the reference from 1 January, or the window.

        Args:
            reference: whether to build the reference, which starts on 1 January of the window's year
                and ends with the window, or with the year when :attr:`reference_through_year_end` is set.
            result_directory: where the run writes; created when missing.
            cache_directory: the run's own cache directory; created when missing.

        Returns:
            SimulationParameters: the parameters, with no post-processing options.
        """
        result_directory.mkdir(parents=True, exist_ok=True)
        cache_directory.mkdir(parents=True, exist_ok=True)
        start = datetime.datetime(self.start.year, 1, 1) if reference else self.start_datetime
        end = self.end_datetime
        if reference and self.reference_through_year_end:
            end = datetime.datetime(self.start.year + 1, 1, 1)
        return SimulationParameters(
            start_date=start,
            end_date=end,
            seconds_per_timestep=self.seconds_per_timestep,
            result_directory=str(result_directory),
            post_processing_options=[],
            cache_dir_path=str(cache_directory),
        )


@dataclasses.dataclass(frozen=True)
class PerturbedField:
    """One ``initial_*`` field the perturbed reference moved: on which component, from what to what."""

    component_name: str
    field_name: str
    original: float
    perturbed: float


class Perturbation:
    """Moves every numeric ``initial_*`` configuration field of every component the perturbed reference builds.

    The fields are moved while the component is being constructed, inside ``Component.__init__``, so a
    component that reads its configuration after calling it -- the building seeds its thermal mass
    there -- starts from the moved value. A field read before that call, or a state no ``initial_*``
    field describes, is not perturbed; its columns then look memoryless, and the verdict lists them as
    false alarms.
    """

    #: The prefixes of the configuration fields that describe a component's starting state.
    PREFIXES: ClassVar[Tuple[str, ...]] = ("initial_",)

    #: How far a temperature moves, in kelvin. A field is a temperature when its name contains this.
    TEMPERATURE_MARKER: ClassVar[str] = "temperature"
    TEMPERATURE_SHIFT_IN_KELVIN: ClassVar[float] = 3.0

    #: How far a share between 0 and 1 moves: up when it is at most a half, down otherwise.
    SHARE_SHIFT: ClassVar[float] = 0.25

    #: How far any other number moves, relative to itself, and at least by one.
    OTHER_RELATIVE_SHIFT: ClassVar[float] = 0.1

    def __init__(self) -> None:
        """Start with nothing perturbed."""
        self.perturbed_fields: List[PerturbedField] = []

    @classmethod
    def moved(cls, field_name: str, value: float) -> float:
        """Return the perturbed value of one field.

        Args:
            field_name: the field's name.
            value: its configured value.

        Returns:
            float: the value the perturbed reference starts from.
        """
        if cls.TEMPERATURE_MARKER in field_name:
            return value + cls.TEMPERATURE_SHIFT_IN_KELVIN
        if 0.0 <= value <= 1.0:
            return value + cls.SHARE_SHIFT if value <= 0.5 else value - cls.SHARE_SHIFT
        return value + max(1.0, abs(value) * cls.OTHER_RELATIVE_SHIFT)

    def perturb(self, component_name: str, config: Any) -> None:
        """Move the ``initial_*`` fields of one configuration in place and record them.

        Args:
            component_name: the component being built, for the record.
            config: its configuration dataclass.
        """
        if not dataclasses.is_dataclass(config):
            return
        for field in dataclasses.fields(config):
            if not field.name.startswith(self.PREFIXES):
                continue
            value = getattr(config, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            moved = self.moved(field.name, float(value))
            object.__setattr__(config, field.name, moved)
            self.perturbed_fields.append(PerturbedField(component_name, field.name, float(value), moved))

    @contextlib.contextmanager
    def applied(self) -> Iterator["Perturbation"]:
        """Perturb every component constructed inside the ``with`` block.

        Yields:
            Perturbation: this perturbation, recording what it moved.
        """
        original_init = cp.Component.__init__
        perturbation = self

        def perturbing_init(
            component: Any, name: str, my_simulation_parameters: Any, my_config: Any, *args: Any, **kwargs: Any
        ) -> None:
            perturbation.perturb(name, my_config)
            original_init(component, name, my_simulation_parameters, my_config, *args, **kwargs)

        cp.Component.__init__ = perturbing_init  # type: ignore[method-assign,assignment]
        try:
            yield self
        finally:
            cp.Component.__init__ = original_init  # type: ignore[method-assign]


@dataclasses.dataclass
class OracleRun:
    """One run's results and which component each result column belongs to."""

    #: The run's result frame, indexed by the timestamps the simulator built from its start date.
    frame: pd.DataFrame

    #: Result column -> (component instance, its class, the output's field name).
    owners: Dict[str, Tuple[str, type, str]]

    @classmethod
    def simulate(cls, setup: Path, parameters: SimulationParameters) -> "OracleRun":
        """Build the setup under ``parameters``, run it and keep its result frame.

        Args:
            setup: a ``system_setups/*.py`` module or an ``*.energy_system.yaml`` file.
            parameters: the run's parameters.

        Returns:
            OracleRun: the results.
        """
        if setup.suffix == ".py":
            # pylint: disable-next=import-outside-toplevel
            from hisim.hisim_main import initialize_from_python  # noqa: PLC0415

            simulator = initialize_from_python(str(setup), parameters, None)
            effective = simulator.get_simulation_parameters()
            effective.post_processing_options = []
            effective.result_directory = parameters.result_directory
            effective.cache_dir_path = parameters.cache_dir_path
        else:
            # pylint: disable-next=import-outside-toplevel
            from hisim.energy_system.executor import build_energy_system  # noqa: PLC0415

            simulator = build_energy_system(setup, parameters).simulator
        owners: Dict[str, Tuple[str, type, str]] = {}
        original_connect: Callable[[], None] = simulator.connect_all_components

        def connect_and_record_owners() -> None:
            original_connect()
            classes = {
                wrapper.my_component.component_name: type(wrapper.my_component)
                for wrapper in simulator.wrapped_components
            }
            for output in simulator.all_outputs:
                owners[output.get_pretty_name()] = (
                    output.component_name,
                    classes.get(output.component_name, type(None)),
                    output.field_name,
                )

        simulator.connect_all_components = connect_and_record_owners
        simulator.run_all_timesteps()
        return cls(frame=simulator.results_data_frame, owners=owners)


class ColumnTiers:
    """Sorts the result columns into the fixed exact set, the discovered memoryless set and the stateful set."""

    #: The components whose columns are compared exactly whatever the perturbation says, and which of
    #: their outputs: ``None`` (all of them) for the weather and the occupancy, only the solar gains
    #: for the building. They are the profiles a window has to reproduce.
    FIXED_EXACT: ClassVar[Mapping[type, Optional[Tuple[str, ...]]]] = {
        Weather: None,
        UtspLpgConnector: None,
        Building: (Building.SolarGainThroughWindows,),
    }

    @classmethod
    def is_fixed(cls, owner: Tuple[str, type, str]) -> bool:
        """Return whether a column belongs to the fixed exact set.

        Args:
            owner: the column's owner, from :attr:`OracleRun.owners`.

        Returns:
            bool: True for an output of a fixed component that the set names.
        """
        _, component_class, field_name = owner
        for fixed_class, outputs in cls.FIXED_EXACT.items():
            if issubclass(component_class, fixed_class):
                return outputs is None or field_name in outputs
        return False

    @staticmethod
    def unmoved(reference: pd.DataFrame, perturbed: pd.DataFrame, columns: Sequence[str]) -> List[str]:
        """Return the columns the perturbation did not move at any timestep.

        Args:
            reference: the reference frame.
            perturbed: the perturbed reference frame.
            columns: the columns to look at.

        Returns:
            List[str]: those whose two runs are identical.
        """
        return [
            column
            for column in columns
            if ResultComparison.between(reference[[column]], perturbed[[column]]).is_identical(0.0)
        ]


@dataclasses.dataclass
class Verdict:
    """What the oracle found, and the table it prints."""

    window: OracleWindow
    tolerance: float
    fixed_comparison: ResultComparison
    fixed_columns: List[str]
    gated_discovered: List[str]
    false_alarms: Dict[str, List[str]]
    cold_warm: ResultComparison
    stateful_report: pd.DataFrame
    perturbed_fields: List[PerturbedField]
    failing_fixed_columns: List[str]
    #: Per component of the fixed exact set: (columns reproduced, columns compared).
    fixed_by_component: Dict[str, Tuple[int, int]]

    #: How many stateful columns the report shows, worst first.
    REPORTED_STATEFUL_COLUMNS: ClassVar[int] = 15

    @property
    def passed(self) -> bool:
        """Whether the fixed exact set and the cold/warm equality hold."""
        return self.fixed_comparison.is_identical(self.tolerance) and self.cold_warm.is_identical(0.0)

    def render(self) -> str:
        """Render the verdict as one table and its notes.

        Returns:
            str: the text to print.
        """
        lines: List[str] = []
        if self.tolerance != 0.0:
            lines.append(f"!!! THE TOLERANCE IS {self.tolerance:g}, NOT ZERO: the exact gate is not exact. !!!")
        lines.append(
            f"Mid-year oracle: {self.window.start} for {self.window.days} days at {self.window.seconds_per_timestep} s"
        )
        lines.append("")
        rows = [
            ("fixed exact set (weather, occupancy, solar gains)", len(self.fixed_columns),
             self.fixed_comparison.is_identical(self.tolerance)),
            ("discovered memoryless columns reproduced", len(self.gated_discovered), True),
            ("cold window == warm window (all columns)", self.cold_warm.compared_columns,
             self.cold_warm.is_identical(0.0)),
        ]
        lines.append(f"{'check':<52} {'columns':>8}  verdict")
        for name, count, ok in rows:
            lines.append(f"{name:<52} {count:>8}  {'PASS' if ok else 'FAIL'}")
        lines.append(
            f"{'false alarms (memoryless-looking, not reproduced)':<52} "
            f"{sum(len(columns) for columns in self.false_alarms.values()):>8}  REPORTED"
        )
        lines.append(f"{'stateful columns (report only)':<52} {len(self.stateful_report.index):>8}  REPORTED")
        lines.append("")
        by_component = sorted(self.fixed_by_component.items())
        lines.append(
            "fixed exact set by component (reproduced/compared): "
            + ", ".join(f"{name} {reproduced}/{compared}" for name, (reproduced, compared) in by_component)
        )
        lines.append("fixed exact set: " + self.fixed_comparison.describe().replace("\n", "; "))
        if self.failing_fixed_columns:
            lines.append("fixed columns not reproduced:")
            lines.extend(f"  {column}" for column in self.failing_fixed_columns)
        lines.append("cold/warm: " + self.cold_warm.describe().replace("\n", "; "))
        lines.append("")
        if self.perturbed_fields:
            lines.append("perturbed initial_* fields:")
            lines.extend(
                f"  {field.component_name}.{field.field_name}: {field.original:g} -> {field.perturbed:g}"
                for field in self.perturbed_fields
            )
        else:
            lines.append("perturbed initial_* fields: none -- every column looks memoryless")
        if self.false_alarms:
            lines.append("false alarms, by component (state no initial_* field sets; the state handover's to-do):")
            for component_name, columns in sorted(self.false_alarms.items()):
                lines.append(f"  {component_name}: {len(columns)} column(s)")
        if not self.stateful_report.empty:
            lines.append("")
            lines.append(
                f"stateful transient, largest absolute deviation per window day (worst "
                f"{self.REPORTED_STATEFUL_COLUMNS}):"
            )
            worst = self.stateful_report.head(self.REPORTED_STATEFUL_COLUMNS)
            lines.append(worst.to_string(float_format="{:.4g}".format))
        lines.append("")
        lines.append(f"VERDICT: {'PASS' if self.passed else 'FAIL'}")
        if self.tolerance != 0.0:
            lines.append(f"!!! THE TOLERANCE WAS {self.tolerance:g}, NOT ZERO. !!!")
        return "\n".join(lines)


class MidyearOracle:
    """Runs the four runs of one window and builds the verdict."""

    #: The cache environment variables that would redirect every run into one shared cache; the oracle
    #: removes them so that each run gets the empty cache directory it is given.
    CACHE_ENVIRONMENT: ClassVar[Tuple[str, ...]] = (
        "HISIM_CACHE_DIR",
        "HISIM_CACHE_DIRECTORIES",
        "HISIM_CACHE_SHARED_DIR",
    )

    def __init__(self, setup: Path, window: OracleWindow, tolerance: float, work_directory: Path) -> None:
        """Remember what to run and where.

        Args:
            setup: the setup module or energy-system file.
            window: the window under test.
            tolerance: the relative tolerance of the exact gate.
            work_directory: where every run's results and caches go.
        """
        self.setup = setup
        self.window = window
        self.tolerance = tolerance
        self.work_directory = work_directory

    def run(self) -> Verdict:
        """Run the reference, the perturbed reference and the window twice, and compare.

        Returns:
            Verdict: the outcome.
        """
        for variable in self.CACHE_ENVIRONMENT:
            os.environ.pop(variable, None)
        reference = self.simulate("reference", reference=True)
        perturbation = Perturbation()
        with perturbation.applied():
            perturbed = self.simulate("perturbed_reference", reference=True)
        cold = self.simulate("window", reference=False)
        warm = self.simulate("window", reference=False, cache_name="window", result_name="window_warm")

        reference_slice = reference.frame.loc[self.window.start_datetime: self.window.last_timestep]
        columns = [column for column in cold.frame.columns if column in reference.frame.columns]
        fixed = [column for column in columns if column in cold.owners and ColumnTiers.is_fixed(cold.owners[column])]
        others = [column for column in columns if column not in fixed]
        memoryless = ColumnTiers.unmoved(reference.frame, perturbed.frame, others)
        stateful = [column for column in others if column not in memoryless]

        gated_discovered: List[str] = []
        false_alarms: Dict[str, List[str]] = {}
        for column in memoryless:
            if ResultComparison.between(reference_slice[[column]], cold.frame[[column]]).is_identical(self.tolerance):
                gated_discovered.append(column)
            else:
                owner = cold.owners.get(column, (column, type(None), ""))
                false_alarms.setdefault(owner[0], []).append(column)
        failing_fixed = [
            column
            for column in fixed
            if not ResultComparison.between(reference_slice[[column]], cold.frame[[column]]).is_identical(
                self.tolerance
            )
        ]
        fixed_by_component: Dict[str, Tuple[int, int]] = {}
        for column in fixed:
            component_name = cold.owners[column][0]
            reproduced, compared = fixed_by_component.get(component_name, (0, 0))
            fixed_by_component[component_name] = (reproduced + (column not in failing_fixed), compared + 1)
        return Verdict(
            window=self.window,
            tolerance=self.tolerance,
            fixed_comparison=ResultComparison.between(reference_slice[fixed], cold.frame[fixed]),
            fixed_columns=fixed,
            gated_discovered=gated_discovered,
            false_alarms=false_alarms,
            cold_warm=ResultComparison.between(cold.frame, warm.frame),
            stateful_report=self.stateful_report(reference_slice, cold.frame, stateful),
            perturbed_fields=perturbation.perturbed_fields,
            failing_fixed_columns=failing_fixed,
            fixed_by_component=fixed_by_component,
        )

    def simulate(
        self, name: str, reference: bool, cache_name: Optional[str] = None, result_name: Optional[str] = None
    ) -> OracleRun:
        """Run one of the four runs in its own result and cache directory.

        Args:
            name: the run's name, which names its directories unless overridden.
            reference: whether it is a reference run from 1 January.
            cache_name: the cache directory's name, when it is shared with an earlier run.
            result_name: the result directory's name, when it differs from ``name``.

        Returns:
            OracleRun: the results.
        """
        parameters = self.window.parameters(
            reference,
            self.work_directory / (result_name or name) / "results",
            self.work_directory / (cache_name or name) / "cache",
        )
        print(f"[midyear oracle] running {result_name or name} ({parameters.start_date} to {parameters.end_date})")
        return OracleRun.simulate(self.setup, parameters)

    def stateful_report(self, reference: pd.DataFrame, window: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
        """Return the largest absolute deviation per reported window day of each stateful column.

        Args:
            reference: the reference slice over the window.
            window: the window run's frame.
            columns: the stateful columns.

        Returns:
            pd.DataFrame: one row per column, worst first; one column per reported day.
        """
        if not columns or len(reference.index) != len(window.index):
            return pd.DataFrame()
        timesteps_per_day = 24 * 3600 // self.window.seconds_per_timestep
        deviation = (reference[list(columns)].to_numpy(dtype=float) - window[list(columns)].to_numpy(dtype=float))
        report: Dict[str, List[float]] = {}
        for day in OracleWindow.REPORTED_DAYS:
            if day > self.window.days:
                continue
            rows = slice((day - 1) * timesteps_per_day, day * timesteps_per_day)
            report[f"day {day}"] = list(abs(deviation[rows]).max(axis=0))
        frame = pd.DataFrame(report, index=list(columns))
        return frame.loc[frame.max(axis=1).sort_values(ascending=False).index]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the oracle from the command line and print its verdict.

    Args:
        argv: the arguments; the process's when omitted.

    Returns:
        int: 0 when the verdict passes, 1 when it fails.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("setup", type=Path, help="a system_setups/*.py module or an *.energy_system.yaml file")
    parser.add_argument("--start", type=datetime.date.fromisoformat, default=datetime.date(2021, 7, 1),
                        help="the window's first day, ISO 8601 (default 2021-07-01)")
    parser.add_argument("--days", type=int, default=7, help="the window's length in days (default 7)")
    parser.add_argument("--seconds-per-timestep", type=int, default=900, help="the resolution (default 900)")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="relative tolerance of the exact gate (default 0; anything else is reported loudly)")
    parser.add_argument("--reference-through-year-end", action="store_true",
                        help="run the reference to the end of the year instead of to the window's end")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="where the runs write their results and caches (default: a new temporary directory)")
    arguments = parser.parse_args(argv)
    work_directory = arguments.work_dir or Path(tempfile.mkdtemp(prefix="midyear_oracle_"))
    window = OracleWindow(
        arguments.start, arguments.days, arguments.seconds_per_timestep, arguments.reference_through_year_end
    )
    verdict = MidyearOracle(arguments.setup.resolve(), window, arguments.tolerance, work_directory).run()
    print(verdict.render())
    print(f"(runs kept in {work_directory})")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
