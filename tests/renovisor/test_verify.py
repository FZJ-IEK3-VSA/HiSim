"""Tier 1 of the path verification: the anchor, one-change bases, the three stages and the report.

Every test runs a handful of probes, never the whole set: ``python -m hisim.renovisor verify``
runs all of them in CI (``.github/workflows/path-verification.yml``), and what is tested here is
that the harness tells a probe that works from one that does not. The broken cases are made by breaking
the translator or the probe on purpose, one at a time.
"""

import json
from pathlib import Path
from typing import Any, Dict, Sequence

import pytest

from hisim.renovisor import translate as translate_module
from hisim.renovisor.capabilities import Probe, ProbeKind, ProbeSet
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.request import Request
from hisim.renovisor.translate import Translator
from hisim.renovisor.verify import VerifyExitCode, verify
from hisim.renovisor.verify.leaves import ABSENT, RequestLeaves, request_hash
from hisim.renovisor.verify.probes import Completeness, MissingProbe, ProbeBases
from hisim.renovisor.verify.render import ReportWriter
from hisim.renovisor.verify.runner import CellState, Stage, VerificationReport, VerificationRunner
from hisim.renovisor.whitelist import TranslatorError

#: The probe of the spec's own worked example: the external insulation's thickness.
THICKNESS = "option:external_insulation.thickness_in_mm=500"

#: A leaf the translator lists as not implemented: shading has no parameter on the Building.
SHADING = "field:building.window.outside_shading=True"

#: A leaf that lands on exactly one configuration field and nothing else.
AZIMUTH = "field:pv_system.azimuth=0"

#: A probe that sends the anchor's own value, and so is measured from its sibling.
DETACHED = "field:building.building_type=detached_sfh"


def _probes(*names: str) -> Sequence[Probe]:
    """Return the named probes of the committed set, in the given order."""
    by_name = {probe.name: probe for probe in ProbeSet.build()}
    return [by_name[name] for name in names]


def _run(*names: str) -> VerificationReport:
    """Run tier 1 over the named probes only."""
    return VerificationRunner().run(_probes(*names))


def _verdict(report: VerificationReport, name: str) -> Any:
    """Return the verdict of one probe."""
    return next(verdict for verdict in report.verdicts if verdict.probe.name == name)


@pytest.mark.base
class TestTheAnchorAndTheBases:
    """Everything is measured from the anchor, and every base isolates one change."""

    def test_the_anchor_is_the_mockup_with_an_empty_measure_list(self) -> None:
        """Spec §3: the mostly-uncustomized house of mockup 1, with no package."""
        report = _run("anchor")
        verdict = _verdict(report, "anchor")
        mockup = ContractFiles.request_mockup()

        assert verdict.probe.document["measures"] == []
        assert verdict.probe.document["house"] == mockup["house"]
        assert verdict.probe.base_document is None
        assert verdict.cells[Stage.REQUEST] is CellState.AS_EXPECTED
        assert verdict.tested.finished

    def test_an_option_is_measured_from_its_measure(self) -> None:
        """The option probe's base switches the measure on, so the diff is the option alone."""
        probe = ProbeBases.build(_probes(THICKNESS))[0]

        assert probe.base_name == "measure:external_insulation"
        assert [change.path for change in probe.stage_one()] == [
            "measures[id=external_insulation].options.thickness_in_mm"
        ]

    def test_a_value_the_base_already_carries_is_measured_from_its_sibling(self) -> None:
        """The anchor is detached, so detached_sfh is measured from semi_detached_sfh."""
        probe = ProbeBases.build(_probes(DETACHED))[0]
        (change,) = probe.stage_one()

        assert probe.base_name == "field:building.building_type=semi_detached_sfh"
        assert (change.path, change.before, change.after) == (
            "house.building.building_type", "semi_detached_sfh", "detached_sfh"
        )

    def test_a_block_field_is_measured_from_its_block_probe(self) -> None:
        """The prelude that switches the block on is the base, not part of the change."""
        probe = ProbeBases.build(_probes(AZIMUTH))[0]

        assert probe.base_name == "block:pv_system"
        assert [change.path for change in probe.stage_one()] == ["house.pv_system.azimuth"]

    def test_building_a_request_leaves_the_probe_as_it_was(self) -> None:
        """Probe.document used to write a shared prelude block into the request and then mutate it."""
        probe, sibling = _probes(
            "field:hot_water.supply=separate_heat_pump", "field:hot_water.supply=together_with_heating_system"
        )
        before = json.dumps(sibling.house, sort_keys=True)

        probe.document(ProbeSet.anchor())

        assert json.dumps(sibling.house, sort_keys=True) == before
        assert probe.house["hot_water"] == {"supply": "together_with_heating_system"}

    def test_the_cache_key_is_the_translators_content_hash(self) -> None:
        """One recipe for the harness, the file name and the backend's job id."""
        anchor = ProbeSet.anchor()

        assert request_hash(anchor) == Request.parse(anchor).content_hash()

    def test_a_package_is_keyed_by_measure_id(self) -> None:
        """Adding a measure in front of another must not read as a change of the other."""
        leaves = RequestLeaves.of({"measures": [{"id": "a", "options": {"x": 1}}, {"id": "b"}]})

        assert leaves == {"measures[id=a].options.x": 1, "measures[id=b]": {}}


@pytest.mark.base
class TestTheThreeStages:
    """One probe that works, one that is listed, and two broken on purpose."""

    def test_the_thickness_probe_is_one_used_change_that_moves_the_facade(self) -> None:
        """Spec §5's worked example: request -> used -> Building.config.facade_u_value..., lower."""
        verdict = _verdict(_run(THICKNESS), THICKNESS)
        (line,) = verdict.stage_two
        facade = next(
            change for change in verdict.stage_three
            if change.path == "Building.config.facade_u_value_in_watt_per_m2_per_kelvin"
        )

        assert len(verdict.stage_one) == 1 and verdict.stage_one[0].after == 500
        assert line.status == "used"
        assert facade.after < facade.before
        assert facade.source == "house.building.facade.u_value_in_watt_per_m2_per_kelvin"
        assert set(verdict.cells.values()) == {CellState.AS_EXPECTED}

    def test_a_not_implemented_leaf_is_drawn_half(self) -> None:
        """Accepted, listed with its note, acted on by nothing: ◐ in both columns."""
        verdict = _verdict(_run(SHADING), SHADING)

        assert verdict.stage_two[0].status == "not_implemented_yet"
        assert verdict.stage_two[0].note
        assert verdict.stage_three == ()
        assert verdict.cells[Stage.MAPPING] is CellState.AS_LISTED
        assert verdict.cells[Stage.SYSTEM] is CellState.AS_LISTED
        assert CellState.AS_LISTED.glyph == "◐"

    def test_a_translator_that_ignores_a_used_field_is_a_no_effect_finding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The report says used, the file does not change: ○, listed as a finding, not a failure."""
        original = translate_module._TranslationState.write  # pylint: disable=protected-access

        def ignoring_the_azimuth(self: Any, component: str, field_name: str, *arguments: Any, **keywords: Any) -> bool:
            if field_name == "azimuth":
                return True
            return bool(original(self, component, field_name, *arguments, **keywords))

        monkeypatch.setattr(
            translate_module._TranslationState, "write", ignoring_the_azimuth  # pylint: disable=protected-access
        )
        report = _run(AZIMUTH)
        verdict = _verdict(report, AZIMUTH)

        assert verdict.stage_two[0].status == "used"
        assert verdict.stage_three == ()
        assert verdict.cells[Stage.SYSTEM] is CellState.NO_EFFECT
        assert [issue.probe for issue in report.findings()] == [AZIMUTH]
        assert not report.failures()

    def test_a_translation_that_raises_fails_the_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A probe the translator cannot take is ✖ and a failure by name."""
        original = Translator.translate

        def refusing_the_azimuth(self: Translator, request: Request, applied: Any) -> Any:
            if (request.document["house"].get("pv_system") or {}).get("azimuth") == 0:
                raise TranslatorError("broken on purpose")
            return original(self, request, applied)

        monkeypatch.setattr(Translator, "translate", refusing_the_azimuth)
        report = _run(AZIMUTH)
        verdict = _verdict(report, AZIMUTH)

        assert verdict.cells[Stage.MAPPING] is CellState.FAILED
        assert verdict.cells[Stage.SYSTEM] is CellState.FAILED
        assert [(issue.code, issue.probe) for issue in report.failures()] == [("translation_error", AZIMUTH)]

    def test_a_probe_that_changes_two_things_fails_stage_one(self) -> None:
        """A probe generator that slips a second change in is caught by the request diff."""
        broken = Probe(
            name="field:building.construction_year=2100",
            kind=ProbeKind.FIELD,
            house={"building.construction_year": 2100, "building.building_type": "bungalow"},
            subject="house.building.construction_year",
            value=2100,
        )
        report = VerificationRunner().run([broken])
        verdict = report.verdicts[0]

        assert verdict.stray == ("house.building.building_type",)
        assert verdict.cells[Stage.REQUEST] is CellState.FAILED
        assert [issue.code for issue in report.failures()] == ["request_diff"]

    def test_a_probe_identical_to_its_base_fails_stage_one_and_stops_there(self) -> None:
        """The material option is only ever sent as the mockup's row, which its measure probe already sends."""
        material = next(
            probe for probe in ProbeSet.build() if probe.name.startswith("option:external_insulation.material=")
        )
        report = VerificationRunner().run([material])
        verdict = report.verdicts[0]

        assert verdict.stage_one == ()
        assert verdict.cells[Stage.REQUEST] is CellState.FAILED
        assert verdict.cells[Stage.MAPPING] is CellState.NOT_RUN
        assert verdict.cells[Stage.SYSTEM] is CellState.NOT_RUN
        assert [issue.code for issue in report.failures()] == ["request_diff"]


@pytest.mark.base
class TestCompleteness:
    """Every settable leaf and value of the two contracts has a probe that changes it."""

    def test_a_probed_value_is_covered_and_its_other_value_is_named(self) -> None:
        """Checked against the request schema and the catalogue, never against the probe tables."""
        gaps = Completeness.missing(ProbeBases.build(_probes(SHADING)))

        assert MissingProbe("house.building.window.outside_shading", "False") in gaps
        assert MissingProbe("house.building.window.outside_shading", "True") not in gaps
        assert MissingProbe("measures[id=external_insulation]", "on") in gaps

    def test_a_subset_does_not_check_completeness_by_default(self) -> None:
        """A handful of probes is incomplete on purpose."""
        report = _run(SHADING)

        assert not report.completeness_checked
        assert not report.missing

    def test_the_whole_set_leaves_no_settable_leaf_unprobed(self) -> None:
        """hisim-qzyv: every leaf of the request schema and the catalogue has a probe that changes it."""
        assert not Completeness.missing(ProbeBases.build())

    def test_every_field_probe_changes_exactly_its_own_leaf(self) -> None:
        """Its base is its prelude, or a sibling where the prelude already carries the value: one change either way."""
        for probe in ProbeBases.build():
            if probe.probe.kind is ProbeKind.FIELD:
                assert [change.path for change in probe.stage_one()] == [probe.probe.subject], probe.name

    def test_an_applicant_and_a_cost_leaf_are_measured_from_their_preludes(self) -> None:
        """The applicant needs no prelude; a cost bound needs the priced measure with a band that stays valid."""
        role, cost = ProbeBases.build(
            _probes("field:applicant.role=tenant", "field:measures[id=external_insulation].cost.max_in_euro_per_m2=0")
        )

        assert role.base_name == "anchor"
        assert role.category == "applicant"
        (change,) = cost.stage_one()
        assert (change.path, change.before, change.after) == (
            "measures[id=external_insulation].cost.max_in_euro_per_m2", 70, 0
        )
        assert cost.category == "measure:external_insulation"


@pytest.mark.base
class TestTheReport:
    """report.json, index.html and one page per probe, and the exit code."""

    def test_the_files_are_written_and_say_the_same(self, tmp_path: Path) -> None:
        """The pages are rendered from the JSON, so they cannot disagree."""
        document = ReportWriter.write(_run("anchor", THICKNESS, SHADING), tmp_path)
        written: Dict[str, Any] = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        index = (tmp_path / "index.html").read_text(encoding="utf-8")

        assert written == document
        assert written["summary"]["probes"] == 3
        for probe in written["probes"]:
            page = tmp_path / probe["page"]
            assert page.is_file()
            assert ":" not in page.name
            assert probe["page"] in index
            assert probe["stage4"] == "not run (tier 2)"
        thickness = next(probe for probe in written["probes"] if probe["name"] == THICKNESS)
        assert "facade_u_value_in_watt_per_m2_per_kelvin" in (tmp_path / thickness["page"]).read_text(encoding="utf-8")
        assert "◐" in index

    def test_two_runs_write_the_same_bytes(self, tmp_path: Path) -> None:
        """No clock in any file, so a report is a function of the commit."""
        ReportWriter.write(_run(SHADING), tmp_path / "one")
        ReportWriter.write(_run(SHADING), tmp_path / "two")

        assert (tmp_path / "one" / "report.json").read_bytes() == (tmp_path / "two" / "report.json").read_bytes()
        assert (tmp_path / "one" / "index.html").read_bytes() == (tmp_path / "two" / "index.html").read_bytes()

    def test_an_absent_side_is_a_missing_key(self) -> None:
        """So a consumer tells an absent leaf from a null one."""
        verdict = _verdict(_run(THICKNESS), THICKNESS)

        assert verdict.stage_one[0].before is ABSENT
        assert "before" not in verdict.stage_one[0].to_json()

    def test_verify_exits_four_on_a_failure_and_zero_without(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The command's exit code is the report's verdict; findings never fail it."""
        original = VerificationRunner.run
        chosen = {"probes": _probes(SHADING)}

        def a_subset(self: VerificationRunner, probes: Any = None, completeness: Any = None) -> VerificationReport:
            del probes, completeness
            return original(self, chosen["probes"], completeness=False)

        monkeypatch.setattr(VerificationRunner, "run", a_subset)
        assert verify(tmp_path / "passing") is VerifyExitCode.PASSED

        chosen["probes"] = [
            Probe(name="broken", kind=ProbeKind.FIELD, subject="house.building.construction_year",
                  house={"building.construction_year": 2100, "building.building_type": "bungalow"})
        ]
        assert verify(tmp_path / "failing") is VerifyExitCode.FAILED
        assert (tmp_path / "failing" / "index.html").is_file()
