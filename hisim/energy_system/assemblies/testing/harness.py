"""The harness: tiers, the library walk and its shards, and the test of one assembly (§9.4, D24).

**Tiers.** ``pr`` runs the contract test and the deterministic samples with every declaration.
``nightly`` adds the seeded Latin hypercube sample of the parameter box (:mod:`.samples`).

**The contract test** has two halves. The library check reads the file and refuses an assembly
without descriptions, ranges or a ``tests.monotone``, and every declaration naming a missing member,
parameter or preset; an assembly it refuses is not run at all. The member contract (:mod:`.contract`)
needs the constructed members: every base sample runs first, and on their members the harness checks
that every energy-carrying or temperature output has a ``tests.bounds`` entry, that a bounds entry's
unit is its output's, and that every named KPI is one the member reports. A violation is a contract
failure named by member and output (or KPI), and no declaration of the assembly is evaluated.

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
no partner serves, a sample that breaks its own constraints — raises at once, after writing the
report of the assemblies tested before (``aborted`` names the error).
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from hisim.energy_system.assemblies.library import check_assembly
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
from hisim.energy_system.assemblies.testing.contract import MemberContract
from hisim.energy_system.assemblies.testing.errors import (
    AssemblyHarnessError,
    HarnessUsageError,
    SampleConstructionError,
)
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
    CheckKind,
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

#: The resolution of every isolation run; assemblies declare none (§9.4 does not give them one).
SECONDS_PER_TIMESTEP = 900


@dataclass(frozen=True)
class HarnessSettings:
    """What one harness run draws and how it runs.

    Attributes:
        tier: ``pr`` or ``nightly``.
        sample_size: The hypercube's samples per constraint branch (nightly only).
        seed: The hypercube's seed (nightly only).
        monotone_steps: The values a monotone sweep moves its parameter through.

    Every run simulates one day at :data:`SECONDS_PER_TIMESTEP`.
    """

    tier: Tier = Tier.PR
    sample_size: int = DEFAULT_SAMPLE_SIZE
    seed: int = DEFAULT_SEED
    monotone_steps: int = DEFAULT_MONOTONE_STEPS

    def __post_init__(self) -> None:
        """Refuses settings the harness cannot run with."""
        if self.sample_size < 1:
            raise HarnessUsageError(f"the hypercube sample size is {self.sample_size}; it must be at least 1.")
        if self.monotone_steps < 2:
            raise HarnessUsageError(f"a monotone sweep takes at least 2 steps, not {self.monotone_steps}.")


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
            settings: Tier, sample size, seed and sweep steps.
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
            self.out / "isolation.simulation.yaml", SECONDS_PER_TIMESTEP
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
            seconds_per_timestep=SECONDS_PER_TIMESTEP,
            shard=shard,
            libraries=[str(directory) for directory in self.resolver.directories],
            partner_files=list(self.registry.files),
            out=str(self.out),
        )

    def test_library(self, paths: Sequence[str], shard: str = "1/1") -> HarnessReport:
        """Tests every assembly named, in order, and writes the report; never raises for a failed check.

        Raises:
            AssemblyHarnessError: When the harness cannot go on (a port no partner serves, a sample
                the harness built against the assembly's constraints); the report of every assembly
                tested before is written first, so what ran is never lost.
        """
        report = self.new_report(shard)
        start = time.perf_counter()
        try:
            for library_path in paths:
                report.assemblies.append(self.test(library_path))
        except AssemblyHarnessError as error:
            report.aborted = f"{type(error).__name__}: {error}"
            raise
        finally:
            report.seconds = time.perf_counter() - start
            report.write(self.out)
        return report

    def test(self, library_path: str) -> AssemblyReport:
        """Tests one assembly: its contract, then every sample and every declaration."""
        start = time.perf_counter()
        assembly = self.resolver.resolve(library_path, "the assembly test harness")
        report = AssemblyReport(path=assembly.path, file=str(assembly.file), sha256=assembly.sha256)
        problems = check_assembly(assembly, self.resolver)
        if problems:
            for problem in problems:
                report.fail(CheckKind.CONTRACT, "-", "library check", problem)
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
        self.checked: Set[str] = set()
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
            self.report.fail(CheckKind.CONTRACT, "-", "samples", str(error))
            return
        bases = list(self.book.samples)
        for sample in bases:
            self.run_sample(sample)
        if self.check_member_contract(bases):
            for sample in bases:
                self.outcome(sample)
            self.check_expect()
            self.check_monotone(bases)
        self.report.samples_by_kind = sample_summary(self.book.samples)

    def check_member_contract(self, bases: Sequence[Sample]) -> bool:
        """The member contract on the constructed members of the base runs; ``False`` when it fails.

        Every violation is a ``contract`` failure named by member and output (or KPI); the runs are
        recorded, and no declaration is evaluated. A KPI no finished run could read off its member is
        a contract failure too: the contract is verified, never assumed.
        """
        contract = MemberContract(self.tests)
        kpis_checked: Set[Tuple[str, str]] = set()
        constructed = False
        for sample in bases:
            outcome = self.outcomes[sample.sample_id]
            if not outcome.constructed:
                continue
            constructed = True
            contract.check_outputs(outcome.components)
            if outcome.finished and outcome.results is not None:
                kpis_checked |= contract.check_kpis(outcome.components, outcome.outputs, outcome.results)
        if not constructed:
            first = self.outcomes[bases[0].sample_id] if bases else None
            reason = first.failure[1] if first is not None and first.failure is not None else "no base sample ran"
            self.report.fail(
                CheckKind.CONTRACT, "-", "the constructed members", f"no base sample constructed: {reason}"
            )
            self.record_runs(bases)
            return False
        for member, kpi in contract.kpi_declarations():
            seen = any(member in self.outcomes[sample.sample_id].components for sample in bases)
            if seen and (member, kpi) not in kpis_checked:
                self.report.fail(
                    CheckKind.CONTRACT,
                    "-",
                    f"{member}: {kpi}",
                    f"no base run of '{member}' finished, so its KPI '{kpi}' is unverified.",
                )
        for violation in contract.violations:
            self.report.fail(CheckKind.CONTRACT, "-", violation.subject, violation.message)
        if self.report.failures:
            self.record_runs(bases)
            return False
        return True

    def record_runs(self, samples: Sequence[Sample]) -> None:
        """Records runs whose checks are not evaluated, because the contract failed."""
        for sample in samples:
            outcome = self.outcomes[sample.sample_id]
            outcome.release()
            self.report.runs.append(
                RunRecord(
                    sample=sample,
                    directory=str(outcome.directory),
                    seconds=outcome.seconds,
                    bindings=[binding.to_document() for binding in outcome.bindings],
                    failed_checks=0,
                )
            )

    # ----------------------------------------------------------------------------------------- runs

    @staticmethod
    def label(sample: Sample) -> str:
        """A sample as a failure names it: its id and its first origin."""
        return f"{sample.sample_id} ({sample.origins[0]})"

    def run_sample(self, sample: Sample) -> RunOutcome:
        """Builds and runs one sample once, without checking it."""
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
                failure=(CheckKind.EXCEPTION, f"the isolation system does not expand: {error}"),
            )
        else:
            outcome = self.harness.runner.run(system, self.space, sample, directory)
        self.outcomes[sample.sample_id] = outcome
        return outcome

    def outcome(self, sample: Sample) -> RunOutcome:
        """The sample's run, running it if needed and checking it the first time it is asked for."""
        if sample.sample_id in self.checked:
            return self.outcomes[sample.sample_id]
        outcome = self.run_sample(sample)
        self.checked.add(sample.sample_id)
        before = self.report.checks_failed
        self.check_run(sample, outcome)
        # The run's checks are done: only its KPIs (on disk), its members and how it ended stay needed.
        outcome.release()
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
        """The checks of one run: exception, non-finite values, energy balance, then every bounds declaration.

        A run that failed fails exactly one check, the kind its failure is of (:class:`CheckKind`
        ``EXCEPTION`` or ``ENERGY_BALANCE``); the other of the two is not counted, passed or failed.
        A run that finished passes both.
        """
        report = self.report
        label = self.label(sample)
        failure = outcome.failure
        if failure is None:
            report.passed_check()
            report.passed_check()
        elif failure[0] == CheckKind.ENERGY_BALANCE:
            report.fail(CheckKind.ENERGY_BALANCE, label, "the energy balance", failure[1])
        else:
            report.fail(CheckKind.EXCEPTION, label, "the run", failure[1])
        if outcome.results is not None:
            nonfinite = nonfinite_columns(outcome.results)
            if nonfinite:
                report.fail(CheckKind.NONFINITE, label, "every result column finite", "; ".join(nonfinite))
            else:
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
                self.report.fail(CheckKind.BOUNDS, label, text, f"{runtime} has no result column for {output}")
                return
            problem = series_violation(outcome.results[column], declaration.min, declaration.max)
        else:
            try:
                value = kpi_value(outcome.kpis(), declaration.kpi or "", SUBJECT, member, self.assembly.path)
            except ValueError as error:
                self.report.fail(CheckKind.BOUNDS, label, text, str(error))
                return
            problem = band_violation(value, declaration.min, declaration.max)
        if problem is None:
            self.report.passed_check()
        else:
            self.report.fail(CheckKind.BOUNDS, label, text, problem)

    # ------------------------------------------------------------------------------- across runs

    def check_expect(self) -> None:
        """Every ``expect`` on its preset's sample."""
        for declaration in self.tests.expect if self.tests is not None else ():
            text = DeclarationText.expect(declaration)
            sample = self.book.with_preset(declaration.preset)
            if sample is None:
                self.report.fail(CheckKind.EXPECT, "-", text, f"no sample resolved the preset '{declaration.preset}'")
                continue
            outcome = self.outcome(sample)
            label = self.label(sample)
            if not outcome.finished:
                self.report.fail(CheckKind.EXPECT, label, text, "the preset's run did not finish")
                continue
            try:
                value = kpi_value(outcome.kpis(), declaration.kpi, SUBJECT, declaration.member, self.assembly.path)
            except ValueError as error:
                self.report.fail(CheckKind.EXPECT, label, text, str(error))
                continue
            problem = band_violation(value, declaration.min, declaration.max)
            if problem is None:
                self.report.passed_check()
            else:
                self.report.fail(CheckKind.EXPECT, label, text, problem)

    def check_monotone(self, bases: Sequence[Sample]) -> None:
        """Every ``monotone`` from every base sample."""
        for declaration in self.tests.monotone if self.tests is not None else ():
            for base in bases:
                self.check_sweep(base, declaration)

    def check_sweep(self, base: Sample, declaration: MonotoneDeclaration) -> None:
        """One monotone declaration swept from one base."""
        text = DeclarationText.monotone(declaration)
        steps = self.harness.settings.monotone_steps
        swept, reason = sweep_samples(self.space, base, declaration.parameter, steps)
        if reason is not None:
            self.report.not_applicable.append(f"{text}: {reason}")
            return
        if len(swept) < steps:
            note = (
                f"{text}: the integer range of '{declaration.parameter}' holds {len(swept)} values, so the sweep "
                f"takes {len(swept)} of the {steps} steps asked for"
            )
            if note not in self.report.notes:
                self.report.notes.append(note)
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
                self.report.fail(
                    CheckKind.MONOTONE, self.label(sample), text, "the run of this sweep point did not finish"
                )
                return
            if declaration.member is not None and declaration.member not in outcome.members:
                self.report.not_applicable.append(
                    f"{text} from {base.sample_id}: '{declaration.member}' is not a member with its parameters"
                )
                return
            try:
                value = kpi_value(
                    outcome.kpis(), declaration.kpi, SUBJECT, declaration.member, self.assembly.path
                )
            except ValueError as error:
                self.report.fail(CheckKind.MONOTONE, self.label(sample), text, str(error))
                return
            points.append((sample, value))
        pair = MonotoneEvaluation.offending_pair([value for _sample, value in points], declaration.direction)
        if pair is None:
            self.report.passed_check()
            return
        (first, first_value), (second, second_value) = points[pair[0]], points[pair[1]]
        parameter = declaration.parameter
        self.report.fail(
            CheckKind.MONOTONE,
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
