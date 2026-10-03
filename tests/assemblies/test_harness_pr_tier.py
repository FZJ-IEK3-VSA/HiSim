"""The PR tier of the assembly test harness on the fixture library (``assemblies_spec.md`` §9.4, D24).

Every fixture assembly runs its contract test and its deterministic samples — the defaults, every
preset, every range boundary, every allowed value and every variant option — each in its isolation
system for one simulated day, with every ``bounds``, ``expect`` and ``monotone`` declaration it
carries. Every check must hold: the fixture contracts are truthful. The command runs one shard of
the library the way CI runs the nightly tier, and writes its report.
"""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from hisim.cli import main
from hisim.energy_system.assemblies.testing.harness import AssemblyHarness, library_paths
from tests.assemblies.helpers import Fixtures, fixture_resolver

#: Every assembly of the fixture library.
FIXTURE_ASSEMBLIES = library_paths(fixture_resolver())


@pytest.mark.base
@pytest.mark.parametrize("library_path", FIXTURE_ASSEMBLIES)
def test_every_fixture_assembly_passes_the_pr_tier(library_path: str, tmp_path: Path) -> None:
    """One PR-tier run of one assembly: every check holds, and every sample ran in its own directory."""
    report = AssemblyHarness(fixture_resolver(), tmp_path).test(library_path)

    assert report.passed, "\n".join(failure.text() for failure in report.failures)
    assert report.runs and report.checks_passed > len(report.runs)
    for run in report.runs:
        assert (Path(run.directory) / "isolation.energy_system.yaml").is_file()
        assert (Path(run.directory) / "all_kpis.json").is_file()


@pytest.mark.base
def test_the_command_runs_one_shard_and_writes_its_report(tmp_path: Path) -> None:
    """``--shard 2/3`` takes every third assembly from the second; the JSON report lists exactly those."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(
            [
                "energy-system",
                "test-assemblies",
                "--library",
                str(Fixtures.LIBRARY),
                "--shard",
                "2/3",
                "--out",
                str(tmp_path),
            ]
        )

    assert code == 0, out.getvalue()
    document = json.loads((tmp_path / "assembly_test_report.json").read_text(encoding="utf-8"))
    assert document["tier"] == "pr" and document["shard"] == "2/3" and document["passed"]
    assert [assembly["assembly"] for assembly in document["assemblies"]] == FIXTURE_ASSEMBLIES[1::3]
    assert "every check held." in out.getvalue()
