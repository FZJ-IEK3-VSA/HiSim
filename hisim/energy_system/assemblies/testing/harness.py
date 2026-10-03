"""The harness: tiers, the library walk and its shards, and the test of one assembly (§9.4, D24).

**Tiers.** ``pr`` runs the contract test — the library check, which refuses an assembly without
descriptions, ranges, ``tests.bounds`` on every energy-carrying or temperature output and a
``tests.monotone``, and every declaration naming a missing member, output, KPI, parameter or
preset — and the deterministic samples with every declaration. ``nightly`` adds the seeded Latin
hypercube sample of the parameter box (:mod:`.samples`). An assembly the contract test refuses is
not run at all: its problems are its failures.

**One assembly.** Every base sample (the deterministic ones, and the hypercube in the nightly
tier) runs once in its isolation system (:mod:`.isolation`) and is checked: the run raised
nothing, every result column is finite, the energy balance closed, and every ``bounds``
declaration holds. Every ``expect`` is checked on its preset's sample. Every ``monotone``
declaration sweeps its parameter across its range in ``steps`` values from every base sample, runs
each point (a point another sample already holds is not run twice; a new one is checked like any
run) and evaluates the KPI series within the golden gate's tolerance (:mod:`.checks`). Two bases
that differ only in the swept parameter reach the same sweep, which is one check, evaluated once.

**The library.** Every assembly the library directories offer, sorted by library path; a shard
``i/n`` takes every ``n``-th of them starting at the ``i``-th, so the shards are deterministic and
disjoint and together cover the library, as the golden shards do.

**Failing.** Every failed check is recorded and the run goes on; :func:`require_passed` raises
:class:`~.report.AssemblyTestFailure` with all of them once the report is written. A harness that
cannot do its job — an empty library or shard, a test-partner registry that does not read, a port
no partner serves, a sample that breaks its own constraints — raises at once.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from hisim.energy_system.assemblies.library import CheckStrength, check_assembly
from hisim.energy_system.assemblies.model import BoundsDeclaration, MonotoneDeclaration
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing.checks import (
    DeclarationText,
    MonotoneEvaluation,
    band_violation,
    kpi_value,
    nonfinite_columns,
    output_column,
    series_violation,
)
from hisim.energy_system.assemblies.testing.errors import HarnessUsageError, SampleConstructionError
from hisim.energy_system.assemblies.testing.isolation import (
    SUBJECT,
    IsolationBuilder,
    IsolationRunner,
    RunOutcome,
)
from hisim.energy_system.assemblies.testing.partners import TestPartnerRegistry
from hisim.energy_system.assemblies.testing.report import (
    AssemblyReport,
    AssemblyTestFailure,
    HarnessReport,
    RunRecord,
)
from hisim.energy_system.assemblies.testing.samples import (
    DeterministicSamples,
    HypercubeSampler,
    ParameterSpace,
    Sample,
    SampleBook,
    SampleKind,
    sample_summary,
    sweep_samples,
)
from hisim.energy_system.errors import EnergySystemError


class Tier(enum.Enum):
    """Which samples a harness run draws."""

    PR = "pr"
    NIGHTLY = "nightly"


#: The fixed seed of the nightly hypercube (the owner's decision date, 2026-10-03).
DEFAULT_SEED = 20261003

#: The nightly hypercube's samples per constraint branch.
DEFAULT_SAMPLE_SIZE = 16

#: The values a monotone sweep moves its parameter through.
DEFAULT_MONOTONE_STEPS = 4

#: The resolution of an isolation run; assemblies declare none (§9.4 does not give them one).
DEFAULT_SECONDS_PER_TIMESTEP = 900


@dataclass(frozen=True)
class HarnessSettings:
    """What one harness run draws and how it runs.

    Attributes:
        tier: ``pr`` or ``nightly``.
        sample_size: The hypercube's samples per constraint branch (nightly only).
        seed: The hypercube's seed (nightly only).
        monotone_steps: The values a monotone sweep moves its parameter through.
        seconds_per_timestep: The resolution of the one simulated day.
    """

    tier: Tier = Tier.PR
    sample_size: int = DEFAULT_SAMPLE_SIZE
    seed: int = DEFAULT_SEED
    monotone_steps: int = DEFAULT_MONOTONE_STEPS
    seconds_per_timestep: int = DEFAULT_SECONDS_PER_TIMESTEP

    def __post_init__(self) -> None:
        """Refuses settings the harness cannot run with."""
        if self.sample_size < 1:
            raise HarnessUsageError(f"the hypercube sample size is {self.sample_size}; it must be at least 1.")
        if self.monotone_steps < 2:
            raise HarnessUsageError(f"a monotone sweep takes at least 2 steps, not {self.monotone_steps}.")
        if self.seconds_per_timestep < 1 or 86400 % self.seconds_per_timestep:
            raise HarnessUsageError(
                f"{self.seconds_per_timestep} s per step does not divide the simulated day of 86400 s."
            )


class AssemblyHarness:
    """Runs the test contract of assemblies of one library, each in isolation."""

    #: The subdirectory of the output root the runs go to, one directory per sample.
    RUNS = "runs"

    def __init__(
        self,
        resolver: AssemblyResolver,
        out: Path,
        settings: HarnessSettings = HarnessSettings(),
        registry: Optional[TestPartnerRegistry] = None,
    ) -> None:
        """Prepares the harness over a library and an output root.

        Args:
            resolver: The library under test; its directories also hold the test-partner registry.
            out: Where the runs and the report go; its ``runs`` subdirectory must not exist yet.
            settings: Tier, sample size, seed, sweep steps and resolution.
            registry: The test partners; read from the library directories when omitted.

        Raises:
            HarnessUsageError: When the output root already holds runs.
            TestPartnerRegistryError: When a registry file does not read.
        """
        self.resolver = resolver
        self.settings = settings
        self.out = Path(out)
        self.registry = registry if registry is not None else TestPartnerRegistry.from_directories(resolver.directories)
        if (self.out / self.RUNS).exists():
            raise HarnessUsageError(
                f"{self.out / self.RUNS} exists; the harness writes every run into a fresh directory, so give it an "
                "output root it has not used."
            )
        (self.out / self.RUNS).mkdir(parents=True)
        self.parameters_path = IsolationRunner.write_parameters(
            self.out / "isolation.simulation.yaml", settings.seconds_per_timestep
        )
        self.builder = IsolationBuilder(resolver, self.registry)
        self.runner = IsolationRunner(resolver, self.parameters_path)

    def new_report(self, shard: str = "1/1") -> HarnessReport:
        """An empty report of this harness's settings."""
        nightly = self.settings.tier == Tier.NIGHTLY
        return HarnessReport(
            tier=self.settings.tier.value,
            sample_size=self.settings.sample_size if nightly else None,
            seed=self.settings.seed if nightly else None,
            monotone_steps=self.settings.monotone_steps,
            seconds_per_timestep=self.settings.seconds_per_timestep,
            shard=shard,
            libraries=[str(directory) for directory in self.resolver.directories],
            partner_files=list(self.registry.files),
            out=str(self.out),
        )

    def test_library(self, paths: Sequence[str], shard: str = "1/1") -> HarnessReport:
        """Tests every assembly named, in order, and writes the report; never raises for a failed check."""
        report = self.new_report(shard)
        start = time.perf_counter()
        for library_path in paths:
            report.assemblies.append(self.test(library_path))
        report.seconds = time.perf_counter() - start
        report.write(self.out)
        return report

    def test(self, library_path: str) -> AssemblyReport:
        """Tests one assembly: its contract, then every sample and every declaration."""
        start = time.perf_counter()
        assembly = self.resolver.resolve(library_path, "the assembly test harness")
        report = AssemblyReport(path=assembly.path, file=str(assembly.file), sha256=assembly.sha256)
        problems = check_assembly(assembly, self.resolver, CheckStrength.LIBRARY)
        if problems:
            for problem in problems:
                report.fail("contract", "-", "library check", problem)
        else:
            _AssemblyTest(self, assembly, report).run()
        report.seconds = time.perf_counter() - start
        return report


class _AssemblyTest:
    """The test of one assembly that passed its contract test: its samples, runs and declarations."""

    def __init__(self, harness: AssemblyHarness, assembly: ResolvedAssembly, report: AssemblyReport) -> None:
        """Prepares the test."""
        self.harness = harness
        self.assembly = assembly
        self.report = report
        self.space = ParameterSpace(assembly)
        self.book = SampleBook(self.space)
        self.outcomes: Dict[str, RunOutcome] = {}
        self.sweeps: Set[Tuple[str, Tuple[str, ...]]] = set()
        self.tests = assembly.model.tests
        self.directory = harness.out / AssemblyHarness.RUNS / assembly.path

    def run(self) -> None:
        """Draws the samples, runs them and checks every declaration."""
        try:
            DeterministicSamples.add_to(self.book)
            if self.harness.settings.tier == Tier.NIGHTLY:
                sampler = HypercubeSampler(self.space, self.harness.settings.sample_size, self.harness.settings.seed)
                self.report.branches = [branch.label for branch in sampler.add_to(self.book)]
        except SampleConstructionError as error:
            self.report.fail("contract", "-", "samples", str(error))
            return
        bases = list(self.book.samples)
        for sample in bases:
            self.outcome(sample)
        self.check_expect()
        self.check_monotone(bases)
        self.report.samples_by_kind = sample_summary(self.book.samples)

    # ----------------------------------------------------------------------------------------- runs

    @staticmethod
    def label(sample: Sample) -> str:
        """A sample as a failure names it: its id and its first origin."""
        return f"{sample.sample_id} ({sample.origins[0]})"

    def outcome(self, sample: Sample) -> RunOutcome:
        """The sample's run, running and checking it the first time it is asked for."""
        if sample.sample_id in self.outcomes:
            return self.outcomes[sample.sample_id]
        directory = self.directory / sample.sample_id
        try:
            system = self.harness.builder.build(self.assembly, self.space, sample)
        except EnergySystemError as error:
            directory.mkdir(parents=True, exist_ok=False)
            outcome = RunOutcome(
                sample_id=sample.sample_id,
                directory=directory,
                seconds=0.0,
                failure=("exception", f"the isolation system does not expand: {error}"),
            )
        else:
            outcome = self.harness.runner.run(system, self.space, sample, directory)
        self.outcomes[sample.sample_id] = outcome
        before = self.report.checks_failed
        self.check_run(sample, outcome)
        record = RunRecord(
            sample=sample,
            directory=str(outcome.directory),
            seconds=outcome.seconds,
            bindings=[binding.to_document() for binding in outcome.bindings],
            failed_checks=self.report.checks_failed - before,
        )
        self.report.runs.append(record)
        return outcome

    def check_run(self, sample: Sample, outcome: RunOutcome) -> None:
        """The checks of one run: exception, non-finite values, energy balance, then every bounds declaration."""
        report = self.report
        label = self.label(sample)
        failure = outcome.failure
        if failure is not None and failure[0] == "exception":
            report.fail("exception", label, "the run", failure[1])
        else:
            report.passed_check()
        if outcome.results is not None:
            nonfinite = nonfinite_columns(outcome.results)
            if nonfinite:
                report.fail("nonfinite", label, "every result column finite", "; ".join(nonfinite))
            else:
                report.passed_check()
        if failure is not None and failure[0] == "energy_balance":
            report.fail("energy_balance", label, "the energy balance", failure[1])
        elif failure is None:
            report.passed_check()
        if failure is not None:
            if self.tests is not None and self.tests.bounds:
                report.not_applicable.append(
                    f"{label}: the bounds declarations, because the run did not finish"
                )
            return
        for declaration in self.tests.bounds if self.tests is not None else ():
            self.check_bounds(sample, outcome, declaration)

    def check_bounds(self, sample: Sample, outcome: RunOutcome, declaration: BoundsDeclaration) -> None:
        """One bounds declaration on one finished run."""
        label = self.label(sample)
        text = DeclarationText.bounds(declaration)
        if declaration.output is not None:
            member, output = declaration.output.split(".", 1)
        else:
            member, output = declaration.member or "", ""
        runtime = outcome.members.get(member)
        if runtime is None:
            self.report.not_applicable.append(
                f"{label}: {text}, because '{member}' is not a member with its parameters"
            )
            return
        if declaration.output is not None:
            column = output_column(outcome.outputs, runtime, output)
            if column is None or outcome.results is None or column not in outcome.results:
                self.report.fail("bounds", label, text, f"{runtime} has no result column for {output}")
                return
            problem = series_violation(outcome.results[column], declaration.min, declaration.max)
        else:
            try:
                value = kpi_value(outcome.kpis(), declaration.kpi or "", SUBJECT, member, self.assembly.path)
            except ValueError as error:
                self.report.fail("bounds", label, text, str(error))
                return
            problem = band_violation(value, declaration.min, declaration.max)
        if problem is None:
            self.report.passed_check()
        else:
            self.report.fail("bounds", label, text, problem)

    # ------------------------------------------------------------------------------- across runs

    def check_expect(self) -> None:
        """Every ``expect`` on its preset's sample."""
        for declaration in self.tests.expect if self.tests is not None else ():
            text = DeclarationText.expect(declaration)
            sample = self.book.with_preset(declaration.preset)
            if sample is None:
                self.report.fail("expect", "-", text, f"no sample resolved the preset '{declaration.preset}'")
                continue
            outcome = self.outcome(sample)
            label = self.label(sample)
            if not outcome.finished:
                self.report.fail("expect", label, text, "the preset's run did not finish")
                continue
            try:
                value = kpi_value(outcome.kpis(), declaration.kpi, SUBJECT, declaration.member, self.assembly.path)
            except ValueError as error:
                self.report.fail("expect", label, text, str(error))
                continue
            problem = band_violation(value, declaration.min, declaration.max)
            if problem is None:
                self.report.passed_check()
            else:
                self.report.fail("expect", label, text, problem)

    def check_monotone(self, bases: Sequence[Sample]) -> None:
        """Every ``monotone`` from every base sample."""
        for declaration in self.tests.monotone if self.tests is not None else ():
            for base in bases:
                self.check_sweep(base, declaration)

    def check_sweep(self, base: Sample, declaration: MonotoneDeclaration) -> None:
        """One monotone declaration swept from one base."""
        text = DeclarationText.monotone(declaration)
        swept, reason = sweep_samples(self.space, base, declaration.parameter, self.harness.settings.monotone_steps)
        if reason is not None:
            self.report.not_applicable.append(f"{text}: {reason}")
            return
        samples = [
            self.book.add(values, f"sweep of {declaration.parameter} from {base.sample_id}", SampleKind.SWEEP)
            for values in swept
        ]
        key = (text, tuple(sample.sample_id for sample in samples))
        if key in self.sweeps:
            # Another base reached the very same sweep (it differs from that base only in the swept
            # parameter): one check, evaluated once.
            return
        self.sweeps.add(key)
        points: List[Tuple[Sample, float]] = []
        for sample in samples:
            outcome = self.outcome(sample)
            if not outcome.finished:
                self.report.fail("monotone", self.label(sample), text, "the run of this sweep point did not finish")
                return
            if declaration.member not in outcome.members:
                self.report.not_applicable.append(
                    f"{text} from {base.sample_id}: '{declaration.member}' is not a member with its parameters"
                )
                return
            try:
                value = kpi_value(
                    outcome.kpis(), declaration.kpi, SUBJECT, declaration.member, self.assembly.path
                )
            except ValueError as error:
                self.report.fail("monotone", self.label(sample), text, str(error))
                return
            points.append((sample, value))
        pair = MonotoneEvaluation.offending_pair([value for _sample, value in points], declaration.direction)
        if pair is None:
            self.report.passed_check()
            return
        (first, first_value), (second, second_value) = points[pair[0]], points[pair[1]]
        parameter = declaration.parameter
        self.report.fail(
            "monotone",
            f"{first.sample_id} → {second.sample_id} (from {base.sample_id})",
            text,
            f"{parameter} {first.values[parameter]!r} → {second.values[parameter]!r} moves {declaration.kpi} "
            f"{first_value:.6g} → {second_value:.6g}, which is not {declaration.direction.value}",
        )


def library_paths(resolver: AssemblyResolver) -> List[str]:
    """Every assembly the resolver's directories offer, sorted by library path.

    Raises:
        HarnessUsageError: When the directories hold no assembly.
    """
    paths = sorted(resolver.available())
    if not paths:
        raise HarnessUsageError(
            f"the library directories {', '.join(str(d) for d in resolver.directories) or '(none)'} hold no assembly."
        )
    return paths


def parse_shard(text: str) -> Tuple[int, int]:
    """``i/n`` as ``(i, n)``, 1-based.

    Raises:
        HarnessUsageError: For anything else, or ``i`` outside ``1..n``.
    """
    parts = text.split("/")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise HarnessUsageError(f"a shard is written 'i/n' (1-based), not {text!r}.")
    index, count = int(parts[0]), int(parts[1])
    if count < 1 or not 1 <= index <= count:
        raise HarnessUsageError(f"the shard {text!r} names no shard: i runs from 1 to n.")
    return index, count


def shard_of(paths: Sequence[str], index: int, count: int) -> List[str]:
    """The assemblies of shard ``index`` of ``count``: every ``count``-th, starting at the ``index``-th.

    Raises:
        HarnessUsageError: When the shard holds no assembly.
    """
    selected = [path for position, path in enumerate(sorted(paths)) if position % count == index - 1]
    if not selected:
        raise HarnessUsageError(f"the shard {index}/{count} holds none of the {len(paths)} assemblies.")
    return selected


def require_passed(report: HarnessReport) -> None:
    """Raises :class:`~.report.AssemblyTestFailure` listing every failed check, when any failed."""
    if not report.passed:
        raise AssemblyTestFailure(report.failures, Path(report.out) / HarnessReport.JSON_NAME)
