"""Tier 1 of the path verification: the anchor, one-change bases, the three stages and the report.

Every test runs a handful of probes, never the whole set: ``python -m hisim.renovisor verify``
runs all of them in CI (``.github/workflows/path-verification.yml``), and what is tested here is
that the harness tells a probe that works from one that does not. The broken cases are made by breaking
the translator or the probe on purpose, one at a time.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, Sequence

import pytest

from hisim.renovisor import translate as translate_module
from hisim.renovisor.capabilities import Probe, ProbeKind, ProbeSet
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.request import Request
from hisim.renovisor.translate import Translator
from hisim import log
from hisim.renovisor.verify import LOG_DIRECTORY, VerifyExitCode, hisim_log_in, verify
from hisim.renovisor.verify.leaves import ABSENT, RequestLeaves
from hisim.renovisor.verify.probes import Completeness, MissingProbe, ProbeBases
from hisim.renovisor.verify.render import ReportWriter
from hisim.renovisor.verify import runner as runner_module
from hisim.renovisor.verify.runner import (
    Announcements,
    Artefacts,
    CellState,
    IssueCode,
    Refusal,
    Stage,
    VerificationReport,
    VerificationRunner,
)
from hisim.renovisor.whitelist import TranslatorError

#: The probe of the spec's own worked example: the external insulation's thickness.
THICKNESS = "option:external_insulation.thickness_in_mm=500"

#: A leaf the translator lists as not implemented: shading has no parameter on the Building.
SHADING = "field:building.window.outside_shading=True"

#: A leaf that lands on exactly one configuration field and nothing else.
AZIMUTH = "field:pv_system.azimuth=0"

#: A probe that sends the anchor's own value, and so is measured from its sibling.
DETACHED = "field:building.building_type=detached_sfh"

#: A pair whose leaf is not_implemented_yet beside a heat pump, although the field alone is approximated.
SCOP_ON_HEAT_PUMP = "pair:seasonal_efficiency_on_heat_pump"

#: The single-change probe that makes the capability document announce the SCOP as approximated.
SCOP = "field:heating.seasonal_efficiency_in_percent=400"

#: An option probe whose base, ``measure:battery_system``, carries the measure with no option at all.
BATTERY_CAPACITY = "option:battery_system.capacity_in_kwh=200"

#: A country the semantic checks refuse on purpose: it has no TABULA typology.
SPAIN = "field:location.country=ES"


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
        assert [change.path for change in probe.stage_one] == [
            "measures[id=external_insulation].options.thickness_in_mm"
        ]

    def test_a_value_the_base_already_carries_is_measured_from_its_sibling(self) -> None:
        """The anchor is detached, so detached_sfh is measured from semi_detached_sfh."""
        probe = ProbeBases.build(_probes(DETACHED))[0]
        (change,) = probe.stage_one

        assert probe.base_name == "field:building.building_type=semi_detached_sfh"
        assert (change.path, change.before, change.after) == (
            "house.building.building_type", "semi_detached_sfh", "detached_sfh"
        )

    def test_a_block_field_is_measured_from_its_block_probe(self) -> None:
        """The prelude that switches the block on is the base, not part of the change."""
        probe = ProbeBases.build(_probes(AZIMUTH))[0]

        assert probe.base_name == "block:pv_system"
        assert [change.path for change in probe.stage_one] == ["house.pv_system.azimuth"]

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

        assert Request.hash_of(anchor) == Request.parse(anchor).content_hash()
        assert ProbeBases.build(_probes("anchor"))[0].request_hash == Request.hash_of(anchor)

    def test_a_sibling_the_validation_accepts_is_preferred(self) -> None:
        """IE is the anchor's own country; ES would be the first sibling but is refused, so NL is the base."""
        probe = ProbeBases.build(_probes("field:location.country=IE"))[0]

        assert probe.base_name == "field:location.country=NL"

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
        assert [(issue.code, issue.probe) for issue in report.failures()] == [(IssueCode.TRANSLATION_ERROR, AZIMUTH)]
        row = verdict.to_json()
        assert row["error"] == "TranslatorError: broken on purpose"
        assert "Traceback (most recent call last)" in row["traceback"]
        assert "refusing_the_azimuth" in row["traceback"]

    def test_a_base_that_does_not_translate_is_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The azimuth probe translates, its base block:pv_system raises: ✖ in sys and a failure naming the base."""
        original = Translator.translate

        def refusing_the_base(self: Translator, request: Request, applied: Any) -> Any:
            pv_system = request.document["house"].get("pv_system") or {}
            if pv_system and pv_system.get("azimuth") != 0:
                raise TranslatorError("the base is broken on purpose")
            return original(self, request, applied)

        monkeypatch.setattr(Translator, "translate", refusing_the_base)
        report = VerificationRunner().run(_probes(AZIMUTH))
        verdict = report.verdicts[0]

        assert verdict.tested.finished
        assert verdict.cells[Stage.SYSTEM] is CellState.FAILED
        ((code, probe, message),) = [(issue.code, issue.probe, issue.message) for issue in report.failures()]
        assert (code, probe) == (IssueCode.BASE_NOT_TRANSLATED, AZIMUTH)
        assert "block:pv_system" in message and "the base is broken on purpose" in message

    def test_an_option_added_to_an_empty_options_block_leaves_the_measure_in_place(self) -> None:
        """The disappearing ``options: {}`` is structure: the measure is not reported removed; the option is read."""
        verdict = _verdict(_run(BATTERY_CAPACITY), BATTERY_CAPACITY)
        option = "measures[id=battery_system].options.capacity_in_kwh"

        assert [change.path for change in verdict.stage_one] == ["measures[id=battery_system].options", option]
        assert [(line.line, line.removed) for line in verdict.stage_two] == [(option, False)]
        assert verdict.stage_two[0].status == "used"
        assert verdict.cells[Stage.MAPPING] is CellState.AS_EXPECTED

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
        """The material probe as it was until hisim-8mjc: the mockup's row, which its measure probe already sends.

        It replaces the set's material probe by name, so no sibling sends another material either.
        """
        real = next(
            probe for probe in ProbeSet.build() if probe.name.startswith("option:external_insulation.material=")
        )
        material = dataclasses.replace(
            real, measures=[ProbeSet.package("external_insulation")], value=ProbeSet.material()
        )
        report = VerificationRunner().run([material])
        verdict = report.verdicts[0]

        assert verdict.stage_one == ()
        assert verdict.cells[Stage.REQUEST] is CellState.FAILED
        assert verdict.cells[Stage.MAPPING] is CellState.NOT_RUN
        assert verdict.cells[Stage.SYSTEM] is CellState.NOT_RUN
        assert [issue.code for issue in report.failures()] == ["request_diff"]

    def test_a_material_probe_changes_the_material_and_the_elements_u_value(self) -> None:
        """hisim-8mjc: a second real row, so stage 1 is the material alone and stage 3 the facade U-value."""
        material = next(
            probe for probe in ProbeSet.build() if probe.name.startswith("option:external_insulation.material=")
        )
        report = VerificationRunner().run([material])
        verdict = report.verdicts[0]
        option = "measures[id=external_insulation].options.material"

        assert verdict.stage_one and all(RequestLeaves.is_under(change.path, option) for change in verdict.stage_one)
        assert verdict.stray == ()
        assert all(verdict.cells[stage] is CellState.AS_EXPECTED for stage in Stage)
        u_value = next(
            change for change in verdict.stage_three
            if change.path == "Building.config.facade_u_value_in_watt_per_m2_per_kelvin"
        )
        assert u_value.after > u_value.before  # wood fibre conducts more than EPS
        assert not report.failures()


@pytest.mark.base
class TestRefusals:
    """A probe the request schema refuses is broken; one a semantic check refuses is refused on purpose."""

    def test_a_probe_the_schema_refuses_is_a_failure_naming_the_schema_error(self) -> None:
        """A construction year that is not a number: ✖ and probe_refused_by_schema, with the code and the path."""
        broken = Probe(
            name="field:building.construction_year=not-a-year",
            kind=ProbeKind.FIELD,
            house={"building.construction_year": "not-a-year"},
            subject="house.building.construction_year",
            value="not-a-year",
        )
        report = VerificationRunner().run([broken])
        verdict = report.verdicts[0]

        assert verdict.tested.refusal is not None and verdict.tested.refusal.structural
        assert verdict.cells[Stage.MAPPING] is CellState.FAILED
        assert verdict.cells[Stage.SYSTEM] is CellState.FAILED
        ((code, probe, message),) = [(issue.code, issue.probe, issue.message) for issue in report.failures()]
        assert (code, probe) == (IssueCode.PROBE_REFUSED_BY_SCHEMA, broken.name)
        assert "type.invalid at house.building.construction_year" in message
        assert verdict.to_json()["refused"]["by"] == "schema"

    def test_a_probe_a_semantic_check_refuses_is_drawn_half_with_its_code(self) -> None:
        """ES has no TABULA typology: ◐ in map with location.country.unsupported, not run in sys, no failure."""
        report = _run(SPAIN)
        verdict = _verdict(report, SPAIN)

        assert verdict.tested.refusal is not None and not verdict.tested.refusal.structural
        assert verdict.cells[Stage.MAPPING] is CellState.AS_LISTED
        assert "location.country.unsupported" in verdict.remarks[Stage.MAPPING]
        assert verdict.cells[Stage.SYSTEM] is CellState.NOT_RUN
        assert not report.failures()
        assert verdict.to_json()["refused"]["by"] == "semantic_check"


@pytest.mark.base
class TestConditionalStatuses:
    """The bridge until hisim-5dfc: a pair below the announced status is a finding, a single change still fails."""

    def test_a_pair_below_the_announced_status_is_a_finding(self) -> None:
        """The SCOP is approximated on its own and not_implemented_yet beside a heat pump: ◐, pointing to hisim-5dfc."""
        report = _run(SCOP, SCOP_ON_HEAT_PUMP)
        verdict = _verdict(report, SCOP_ON_HEAT_PUMP)
        line = next(line for line in verdict.stage_two if line.path == "house.heating.seasonal_efficiency_in_percent")

        assert (line.status, line.announced) == ("not_implemented_yet", "approximated")
        assert verdict.conditional == (line,)
        assert verdict.cells[Stage.MAPPING] is CellState.AS_LISTED
        assert "hisim-5dfc" in verdict.remarks[Stage.MAPPING]
        assert not report.failures()
        (finding,) = [issue for issue in report.findings() if issue.code == "conditional_status"]
        assert finding.probe == SCOP_ON_HEAT_PUMP
        assert "conditional" in finding.message and "hisim-5dfc" in finding.message
        assert "not_implemented_yet" in finding.message and "announces approximated" in finding.message

    def test_without_the_bridge_the_same_pair_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retiring the bridge is emptying CONDITIONAL_PROBE_KINDS; the pair then fails as a single change does."""
        monkeypatch.setattr(runner_module, "CONDITIONAL_PROBE_KINDS", ())
        report = _run(SCOP, SCOP_ON_HEAT_PUMP)

        assert _verdict(report, SCOP_ON_HEAT_PUMP).cells[Stage.MAPPING] is CellState.FAILED
        assert [(issue.code, issue.probe) for issue in report.failures()] == [
            ("status_below_announced", SCOP_ON_HEAT_PUMP)
        ]
        assert not [issue for issue in report.findings() if issue.code == "conditional_status"]

    def test_a_single_change_below_the_announced_status_still_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Shading is not_implemented_yet; announced as used, its field probe is ✖ and a failure, not a finding."""
        monkeypatch.setattr(Announcements, "field", lambda self, path, value: "used")
        report = _run(SHADING)
        verdict = _verdict(report, SHADING)

        assert verdict.cells[Stage.MAPPING] is CellState.FAILED
        assert verdict.conditional == ()
        assert [(issue.code, issue.probe) for issue in report.failures()] == [("status_below_announced", SHADING)]
        assert not report.findings()

    def test_a_single_change_approximated_where_used_is_announced_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Owner rule of 2026-09-26: approximated under an announced used is below it, as defaulted would be."""
        monkeypatch.setattr(Announcements, "field", lambda self, path, value: "used")
        report = _run(SCOP)
        verdict = _verdict(report, SCOP)
        (line,) = verdict.stage_two

        assert (line.status, line.announced) == ("approximated", "used")
        assert line.below_announcement
        assert verdict.cells[Stage.MAPPING] is CellState.FAILED
        assert [(issue.code, issue.probe) for issue in report.failures()] == [
            (IssueCode.STATUS_BELOW_ANNOUNCED, SCOP)
        ]

    def test_approximated_as_announced_is_listed_not_failed(self) -> None:
        """The same probe against the document's own announcement, approximated: ◐ and no failure."""
        report = _run(SCOP)
        verdict = _verdict(report, SCOP)

        assert (verdict.stage_two[0].status, verdict.stage_two[0].announced) == ("approximated", "approximated")
        assert verdict.cells[Stage.MAPPING] is CellState.AS_LISTED
        assert not report.failures()


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
                assert [change.path for change in probe.stage_one] == [probe.probe.subject], probe.name

    def test_an_applicant_and_a_cost_leaf_are_measured_from_their_preludes(self) -> None:
        """The applicant needs no prelude; a cost bound needs the priced measure with a band that stays valid."""
        role, cost = ProbeBases.build(
            _probes("field:applicant.role=tenant", "field:measures[id=external_insulation].cost.max_in_euro_per_m2=0")
        )

        assert role.base_name == "anchor"
        assert role.category == "applicant"
        (change,) = cost.stage_one
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

    def test_findings_alone_never_fail_the_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A no-effect finding and a conditional-status finding, no failure: exit 0, both findings in the report."""
        original_write = translate_module._TranslationState.write  # pylint: disable=protected-access

        def ignoring_the_azimuth(self: Any, component: str, field_name: str, *arguments: Any, **keywords: Any) -> bool:
            if field_name == "azimuth":
                return True
            return bool(original_write(self, component, field_name, *arguments, **keywords))

        monkeypatch.setattr(
            translate_module._TranslationState, "write", ignoring_the_azimuth  # pylint: disable=protected-access
        )
        original_run = VerificationRunner.run

        def a_subset(self: VerificationRunner, probes: Any = None, completeness: Any = None) -> VerificationReport:
            del probes, completeness
            return original_run(self, _probes(AZIMUTH, SCOP, SCOP_ON_HEAT_PUMP), completeness=False)

        monkeypatch.setattr(VerificationRunner, "run", a_subset)

        assert verify(tmp_path) is VerifyExitCode.PASSED
        written = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert written["failures"] == []
        assert sorted(finding["code"] for finding in written["findings"]) == ["conditional_status", "no_effect"]
        assert all(not IssueCode(finding["code"]).is_failure for finding in written["findings"])

    def test_the_run_logs_under_the_report_and_restores_the_logger(self, tmp_path: Path) -> None:
        """hisim.log writes to ../logs of the working directory unless set up; verify points it at DIR/logs."""
        before = (log.logger.logging_path, log.logger.before_result_dir_created)
        with hisim_log_in(tmp_path / LOG_DIRECTORY):
            log.information("written by the path-verification test")
            assert log.logger.logging_path == str(tmp_path / LOG_DIRECTORY)
        assert (log.logger.logging_path, log.logger.before_result_dir_created) == before
        assert "written by the path-verification test" in (
            tmp_path / LOG_DIRECTORY / "hisim_simulation.log"
        ).read_text(encoding="utf-8")

    def test_the_report_types_refuse_impossible_states(self) -> None:
        """Artefacts are one of refused, raised or translated; gaps are listed only when completeness was checked."""
        refusal = Refusal(problems=(), structural=False)
        with pytest.raises(ValueError):
            Artefacts(request_hash="x", refusal=refusal, error="E: both")
        with pytest.raises(ValueError):
            Artefacts(request_hash="x", refusal=refusal, report={})
        with pytest.raises(ValueError):
            Artefacts(request_hash="x")
        with pytest.raises(ValueError):
            VerificationReport(
                verdicts=(), missing=(MissingProbe("house.x", "any value"),), completeness_checked=False,
                translations=0, lookups=0,
            )

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

        chosen["probes"] = _probes(SCOP, SCOP_ON_HEAT_PUMP)
        assert verify(tmp_path / "conditional") is VerifyExitCode.PASSED
        written = json.loads((tmp_path / "conditional" / "report.json").read_text(encoding="utf-8"))
        assert [finding["code"] for finding in written["findings"]] == ["conditional_status"]
        assert "hisim-5dfc" in written["legend"]["as_listed"]["meaning"]
        assert "hisim-5dfc" in (tmp_path / "conditional" / "index.html").read_text(encoding="utf-8")
