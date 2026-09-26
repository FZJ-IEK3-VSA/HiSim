"""Path verification of the RenoVisor translation layer, tier 1: request, mapping report, energy system.

The path-verification spec (renovisorissues ``specs/path_verification_spec.md``) asks whether a
setting made in the frontend reaches HiSim, lands on the right component field, and moves nothing
else. Tier 1 answers the first three links of that chain without a simulation, for every probe of
the capability probe set (:class:`hisim.renovisor.capabilities.ProbeSet`)::

    python -m hisim.renovisor verify --out path-report

writes ``report.json``, ``index.html`` (the matrix, one row per probe, columns ``req | map | sys``)
and one page per probe. The exit code is :attr:`VerifyExitCode.PASSED` or, when anything under
``failures`` is listed, :attr:`VerifyExitCode.FAILED`; findings ("no effect", and a pair's
conditional status below the announced one until ``hisim-5dfc``) never fail it.

Modules: :mod:`~hisim.renovisor.verify.leaves` (the artefacts as flat tables and their diffs),
:mod:`~hisim.renovisor.verify.probes` (each probe's base, and the completeness check),
:mod:`~hisim.renovisor.verify.runner` (the translations, the stages and the verdicts) and
:mod:`~hisim.renovisor.verify.render` (the files).
"""

import time
from enum import IntEnum
from pathlib import Path
from typing import Optional


class VerifyExitCode(IntEnum):
    """What ``python -m hisim.renovisor verify`` exits with.

    ``4`` is distinct from the codes of ``run`` (``2`` a refused request, ``3`` a translator error,
    ``5`` a simulation error), so a CI step can tell a verification failure from a crash, which
    exits ``1`` with a traceback.
    """

    PASSED = 0
    FAILED = 4


def verify(out: Path, base_files_directory: Optional[Path] = None) -> VerifyExitCode:
    """Run tier 1 over the whole probe set, write the report into *out*, and print a summary.

    Args:
        out: The report directory.
        base_files_directory: Where the recorded twins live; ``energy_systems/`` when omitted.

    Returns:
        :attr:`VerifyExitCode.FAILED` when the report lists a failure, else
        :attr:`VerifyExitCode.PASSED`.
    """
    from hisim.renovisor.verify.render import ReportWriter  # pylint: disable=import-outside-toplevel
    from hisim.renovisor.verify.runner import VerificationRunner  # pylint: disable=import-outside-toplevel

    started = time.perf_counter()
    report = VerificationRunner(base_files_directory).run()
    document = ReportWriter.write(report, out)
    elapsed = time.perf_counter() - started
    summary = document["summary"]
    print(
        f"path verification, tier 1: {summary['probes']} probes, {summary['translations']} translations "
        f"in {elapsed:.1f} s ({1000 * elapsed / max(summary['probes'], 1):.0f} ms per probe)"
    )
    for stage, counts in summary["cells"].items():
        print(f"  {stage}: " + ", ".join(f"{state} {count}" for state, count in counts.items() if count))
    print(f"  {summary['failures']} failure(s), {summary['findings']} finding(s); report in {out}")
    for issue in document["failures"]:
        where = f"{issue['probe']}: " if issue.get("probe") else ""
        print(f"FAILED {issue['code']}: {where}{issue['message']}")
    return VerifyExitCode.FAILED if summary["failures"] else VerifyExitCode.PASSED
