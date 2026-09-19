"""T-E2E: the vendored mockup through ``run`` twice, then through ``staged`` once.

Everything else about the staged economics is checked on synthetic stages, where a failure is a
statement about the splice. This case checks the seam the synthetic tests cannot: that a real
RenoVisor job leaves behind an ``economic_inputs.json`` the staged evaluator can read, that the
translator's register and envelope subjects survive the round trip through that file, and that
the document the backend serves comes out valid on the end of it.

The plan is the one step 10 §7 names: the mockup's baseline and its five-measure package, both
in year 0 -- the ordinary "what does the package cost against doing nothing" question -- run over
a single January day so the whole case finishes in seconds.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.economics.__main__ import main as economics_main
from hisim.economics.staged_document import StagedDocument
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.run import Calculation, ExitCode
from hisim.renovisor.simulation import Period

pytestmark = pytest.mark.system_setups

#: Where the recorded twins the translator writes into live.
BASE_FILES = Path(__file__).resolve().parents[2] / "energy_systems"


def _document(with_measures: bool) -> Dict[str, Any]:
    """The vendored mockup, with its package or with an empty one."""
    document = copy.deepcopy(ContractFiles.request_mockup())
    if not with_measures:
        document["measures"] = []
    return document


def _run(document: Dict[str, Any], directory: Path, name: str) -> Path:
    """Run one request over a single January day and return its output directory."""
    request_path = directory / f"{name}.json"
    request_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    out = directory / name
    code = Calculation(
        request_path=request_path,
        output_directory=out,
        period=Period.ONE_DAY_15MIN,
        base_files_directory=BASE_FILES,
    ).run()
    assert code == ExitCode.FINISHED, f"the {name} run did not finish"
    return out


@pytest.fixture(name="document", scope="module")
def fixture_document(tmp_path_factory) -> Dict[str, Any]:
    """Run the mockup's baseline and package, then price the two-stage plan out of them.

    Module-scoped: the two runs are the expensive part of the case and every assertion below
    reads the same document, so running them once is what keeps the whole file at a few seconds.
    """
    directory = tmp_path_factory.mktemp("staged_economics")
    baseline = _run(_document(with_measures=False), directory, "baseline")
    package = _run(_document(with_measures=True), directory, "package")
    out = directory / StagedDocument.FILE_NAME
    code = economics_main(
        [
            "staged",
            "--stage",
            f"{baseline}:0:baseline",
            "--stage",
            f"{package}:0:package",
            "--out",
            str(out),
        ]
    )
    assert code == 0, (directory / "problems.json").read_text(encoding="utf-8") if (
        directory / "problems.json"
    ).is_file() else "staged failed with no problems.json"
    document: Dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return document


class TestTheEndToEndDocument:
    """What the backend gets when it prices the mockup's package against doing nothing."""

    def test_the_document_validates_against_its_schema(self, document) -> None:
        """The file is a contract with a frontend that cannot check it, so this does."""
        StagedDocument.validate(document)

    def test_both_stages_are_in_it_in_year_zero(self, document) -> None:
        """The ordinary baseline-versus-package plan: the package supersedes the baseline at once."""
        assert [stage["label"] for stage in document["stages"]] == ["baseline", "package"]
        assert [stage["from_year"] for stage in document["stages"]] == [0, 0]

    def test_the_comparison_is_present_and_has_a_payback_verdict(self, document) -> None:
        """A plan is a difference question, so the comparison is not an optional extra."""
        comparison = document["comparison"]
        assert set(comparison["npv_delta_in_euro"]) == {"min", "best", "max"}
        assert set(comparison["discounted_payback_year"]) == {"min", "best", "max"}

    def test_the_heat_pump_carries_its_measure_and_its_stage(self, document) -> None:
        """Chart V4 filters the investment build-up by these two fields and by nothing else."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        heat_pumps = [
            row
            for row in rows.values()
            for asset_class in [row["asset_class"]]
            if asset_class == "HeatPump"
        ]
        assert heat_pumps, f"no heat pump among {sorted(rows)}"
        assert any(row["measure_id"] == "heating_system" for row in heat_pumps)
        assert any(row["stage"] == 1 for row in heat_pumps)

    def test_the_envelope_subject_is_unpriced(self, document) -> None:
        """The mockup carries no ``cost`` block, so the measure is in the plan with no price."""
        rows = {row["subject"]: row for row in document["plan"]["by_subject"]}
        assert "external_insulation" in rows
        assert rows["external_insulation"]["unpriced"] is True

    def test_the_subsidy_rows_are_no_longer_all_undetermined(self, document) -> None:
        """Ireland has a catalogue now, so the rows carry real verdicts (step 11 §3.12)."""
        rows = document["plan"]["subsidies"]
        assert rows
        statuses = {row["status"] for row in rows}
        assert statuses - {"undetermined"}, "every row is still undetermined"
        assert all(row["scheme"] for row in rows), "a row names no scheme, i.e. no catalogue ran"

    def test_the_heat_pump_unit_grant_is_awarded(self, document) -> None:
        """The mockup is a detached 1975 house buying a heat pump: SEAI pays 6,500 EUR for it."""
        awarded = {
            row["scheme"]: row for row in document["plan"]["subsidies"] if row["status"] == "awarded"
        }
        assert "IE_SEAI_HEAT_PUMP_UNIT_HOUSE" in awarded, sorted(awarded)
        # Support is signed as a credit on the timeline, so the published band is negative.
        amount = awarded["IE_SEAI_HEAT_PUMP_UNIT_HOUSE"]["amount_in_euro"]
        assert amount["best"] == pytest.approx(-6500.0)

    def test_every_row_names_a_scheme_whose_display_name_carries_the_ai_marker(self, document) -> None:
        """Step 11 §1: a user must see that the Irish amounts are an unexamined AI draft.

        The document puts the scheme *id* in ``scheme`` and the display name in ``note`` of an
        awarded row, so the marker is checked on the catalogue entry every row points at — which
        is the string a report renders — and, where the document carries it, on the note too.
        """
        from hisim.economics.subsidies import SubsidyCatalog

        catalog = SubsidyCatalog.load("IE")
        marker = " [AI draft \u2014 needs examination]"
        for row in document["plan"]["subsidies"]:
            scheme = catalog.scheme_by_id(row["scheme"])
            assert scheme is not None, row["scheme"]
            assert scheme.label.endswith(marker), scheme.id
            if row["status"] == "awarded":
                assert str(row["note"]).endswith(marker), row

    def test_the_reference_costs_money(self, document) -> None:
        """Doing nothing has a price too: twenty years of gas, maintenance and replacements."""
        assert document["reference"]["totals"]["npv_in_euro"]["best"] > 0

    def test_the_stacks_add_up_on_the_written_file(self, document) -> None:
        """The document's own promise, on a real run rather than on a synthetic plan."""
        for variant in ("reference", "plan"):
            evaluation = document[variant]
            for slot in ("min", "best", "max"):
                stack = sum(band[slot] for band in evaluation["by_group"].values())
                assert stack == pytest.approx(evaluation["totals"]["npv_in_euro"][slot], abs=0.01)

    def test_the_run_still_leaves_no_costs_block_in_the_payload(self, document) -> None:
        """The other half of the split: one implementation of the money, and this is it."""
        assert document["plan"]["totals"]["npv_in_euro"]["best"] != 0.0
