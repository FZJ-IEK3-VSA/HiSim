"""The report of an assembly test run: per assembly, as JSON and as one screen of text (§9.4).

Every check the harness evaluates is counted, passed or failed; a failed one is kept with the
assembly, the check, the sample (its id, its origins and its parameters) and the declaration it
failed, so a reader of the report can reproduce it from the isolation system in the sample's run
directory. A declaration that cannot apply to a sample — a bound on a member a variant leaves out,
a sweep a constraint branch does not admit — is listed as not applicable, never counted as
passed. :class:`AssemblyTestFailure` is raised once every assembly has run, listing every failed
check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

#: The checks the report distinguishes, in the order a run is checked.
CHECKS = (
    "contract",
    "exception",
    "nonfinite",
    "energy_balance",
    "bounds",
    "expect",
    "monotone",
)


@dataclass(frozen=True)
class CheckFailure:
    """One failed check.

    Attributes:
        assembly: The assembly's library path.
        check: One of :data:`CHECKS`.
        sample: The sample (``s003``), the pair of samples of a monotone failure, or ``-``.
        declaration: The declaration that failed, as the report names it, or ``-``.
        message: What was found.
    """

    assembly: str
    check: str
    sample: str
    declaration: str
    message: str

    def text(self) -> str:
        """The failure in one line."""
        return f"{self.assembly} [{self.check}] {self.sample}: {self.declaration}: {self.message}"

    def to_document(self) -> Dict[str, str]:
        """The failure as plain data."""
        return {
            "assembly": self.assembly,
            "check": self.check,
            "sample": self.sample,
            "declaration": self.declaration,
            "message": self.message,
        }


@dataclass
class RunRecord:
    """One isolation run: the sample, where it ran, how long it took and whether its checks held.

    ``sample`` is the run's :class:`~.samples.Sample`, serialized when the report is written, so the
    origins a later sweep adds to it are listed too.
    """

    sample: Any
    directory: str
    seconds: float
    bindings: List[Dict[str, str]]
    failed_checks: int = 0

    def to_document(self) -> Dict[str, Any]:
        """The run as plain data."""
        return {
            **self.sample.to_document(),
            "directory": self.directory,
            "seconds": round(self.seconds, 3),
            "partners": self.bindings,
            "failed_checks": self.failed_checks,
        }


@dataclass
class AssemblyReport:
    """Everything the harness found about one assembly."""

    path: str
    file: str = ""
    sha256: str = ""
    branches: List[str] = field(default_factory=list)
    runs: List[RunRecord] = field(default_factory=list)
    samples_by_kind: Dict[str, int] = field(default_factory=dict)
    checks_passed: int = 0
    failures: List[CheckFailure] = field(default_factory=list)
    not_applicable: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def checks_failed(self) -> int:
        """How many checks failed."""
        return len(self.failures)

    @property
    def passed(self) -> bool:
        """Whether every check held."""
        return not self.failures

    def passed_check(self) -> None:
        """Counts one check that held."""
        self.checks_passed += 1

    def fail(self, check: str, sample: str, declaration: str, message: str) -> CheckFailure:
        """Records one failed check; the same failure found again (a sweep point two sweeps share) is kept once."""
        failure = CheckFailure(self.path, check, sample, declaration, message)
        if failure not in self.failures:
            self.failures.append(failure)
        return failure

    def to_document(self) -> Dict[str, Any]:
        """The report as plain data."""
        return {
            "assembly": self.path,
            "file": self.file,
            "sha256": self.sha256,
            "passed": self.passed,
            "seconds": round(self.seconds, 3),
            "samples": self.samples_by_kind,
            "branches": self.branches,
            "runs": len(self.runs),
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "failures": [failure.to_document() for failure in self.failures],
            "not_applicable": self.not_applicable,
            "run_records": [run.to_document() for run in self.runs],
        }


@dataclass
class HarnessReport:
    """The report of one harness run over a set of assemblies."""

    tier: str
    sample_size: Optional[int]
    seed: Optional[int]
    monotone_steps: int
    seconds_per_timestep: int
    shard: str
    libraries: List[str]
    partner_files: List[str]
    out: str
    assemblies: List[AssemblyReport] = field(default_factory=list)
    seconds: float = 0.0

    #: The file names the report is written under in the output directory.
    JSON_NAME = "assembly_test_report.json"
    TEXT_NAME = "assembly_test_summary.txt"

    #: How many failures the one-screen summary lists before it refers to the JSON.
    SUMMARY_FAILURES = 15

    @property
    def failures(self) -> List[CheckFailure]:
        """Every failed check of every assembly."""
        return [failure for assembly in self.assemblies for failure in assembly.failures]

    @property
    def passed(self) -> bool:
        """Whether every check of every assembly held."""
        return not self.failures

    def to_document(self) -> Dict[str, Any]:
        """The report as plain data."""
        return {
            "tier": self.tier,
            "hypercube_samples_per_branch": self.sample_size,
            "seed": self.seed,
            "monotone_steps": self.monotone_steps,
            "seconds_per_timestep": self.seconds_per_timestep,
            "shard": self.shard,
            "libraries": self.libraries,
            "partner_files": self.partner_files,
            "passed": self.passed,
            "seconds": round(self.seconds, 3),
            "assemblies": [assembly.to_document() for assembly in self.assemblies],
        }

    def summary(self) -> str:
        """The report on one screen."""
        sampling = (
            f"{self.sample_size} hypercube samples per branch, seed {self.seed}"
            if self.sample_size is not None
            else "deterministic samples"
        )
        lines = [
            f"assembly tests: tier {self.tier}, {sampling}, {self.monotone_steps} monotone steps, "
            f"{self.seconds_per_timestep} s per step, shard {self.shard}",
            f"libraries: {', '.join(self.libraries)}",
            f"{'assembly':<28}{'runs':>6}{'passed':>8}{'failed':>8}{'n/a':>6}{'seconds':>9}",
        ]
        for assembly in self.assemblies:
            lines.append(
                f"{assembly.path:<28}{len(assembly.runs):>6}{assembly.checks_passed:>8}{assembly.checks_failed:>8}"
                f"{len(assembly.not_applicable):>6}{assembly.seconds:>9.1f}"
            )
        lines.append(
            f"{'total':<28}{sum(len(a.runs) for a in self.assemblies):>6}"
            f"{sum(a.checks_passed for a in self.assemblies):>8}{len(self.failures):>8}"
            f"{sum(len(a.not_applicable) for a in self.assemblies):>6}{self.seconds:>9.1f}"
        )
        failures = self.failures
        if not failures:
            lines.append("every check held.")
        else:
            lines.append(f"{len(failures)} check{'s' if len(failures) != 1 else ''} failed:")
            lines.extend(f"  {failure.text()}" for failure in failures[: self.SUMMARY_FAILURES])
            if len(failures) > self.SUMMARY_FAILURES:
                lines.append(f"  … and {len(failures) - self.SUMMARY_FAILURES} more, listed in {self.JSON_NAME}.")
        lines.append(f"report: {Path(self.out) / self.JSON_NAME}")
        return "\n".join(lines)

    def write(self, directory: Path) -> Path:
        """Writes the JSON report and the summary into the output directory; returns the JSON's path."""
        path = directory / self.JSON_NAME
        path.write_text(json.dumps(self.to_document(), indent=2, default=repr) + "\n", encoding="utf-8")
        (directory / self.TEXT_NAME).write_text(self.summary() + "\n", encoding="utf-8")
        return path


class AssemblyTestFailure(Exception):
    """One or more checks of the assembly test harness failed; the message lists every one of them."""

    def __init__(self, failures: Sequence[CheckFailure], report_path: Optional[Path] = None) -> None:
        """Builds the message from every failure."""
        self.failures = list(failures)
        where = f" (report: {report_path})" if report_path is not None else ""
        message = (
            f"{len(self.failures)} assembly test check{'s' if len(self.failures) != 1 else ''} failed{where}:\n"
            + "\n".join(f"  {failure.text()}" for failure in self.failures)
        )
        super().__init__(message)
