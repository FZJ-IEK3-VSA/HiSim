"""Tests of the ``hisim setup-rules`` commands: the generated overview and the check of one file.

The overview is printed from the registry it is handed, so the tests hand it sample rules and compare the output
with what those rules declare, not with a committed page. The command line itself is driven through ``main`` on the
registry of the package.
"""

import io
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from hisim.cli import build_parser, main
from hisim.cli_exit import ExitCodes
from hisim.cli_setup_rules import SetupRuleCommands
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.subjects import BuiltSystem, LoadedDocument
from tests.rules.sample_rules import Finding, SampleRules

pytestmark = pytest.mark.base


class Overview:
    """Sample rules with waivers and requirements, and a way to run one command on them."""

    #: The reason the sample energy-port rule gives for each kind it waives.
    WAIVERS = MappingProxyType({SetupKind.DEMO: "a demo may run an occupancy without a building", SetupKind.PLANT: "a plant has no heated zone"})

    @classmethod
    def registry(cls) -> SetupRuleRegistry:
        """Return a registry of three sample rules: one waived for two kinds, one that requires it, one that only logs."""
        return SetupRuleRegistry(
            [
                SampleRules.rule("EP-06", "room-heat-needs-receiver", waived_for=cls.WAIVERS, requires=frozenset({"EP-05"})),
                SampleRules.rule("EP-05", "input-port-pairs"),
                SampleRules.rule("EF-34", "unconsumed-source", subject=LoadedDocument, enforced=False),
            ]
        )

    @classmethod
    def run(cls, words: list, registry: SetupRuleRegistry) -> tuple:
        """Run one ``setup-rules`` command on a registry and return its exit code, output and error output."""
        arguments = build_parser().parse_args(["setup-rules"] + words)
        out, error = io.StringIO(), io.StringIO()
        commands = SetupRuleCommands(registry)
        verb = {"list": commands.list, "waivers": commands.waivers, "show": commands.show, "check": commands.check}[arguments.verb]
        return verb(arguments, out, error), out.getvalue(), error.getvalue()


def test_list_prints_every_rule_once_in_code_order_with_its_declarations_and_waivers() -> None:
    """Catches an overview that drops a rule, a waiver or its reason, or disagrees with what the rule declares."""
    code, out, _ = Overview.run(["list"], Overview.registry())

    assert code == ExitCodes.OK
    assert out.splitlines() == [
        "EF-34 unconsumed-source  family=tests.rules.sample_rules stage=load nature=? enforced=no (logged) requires=-",
        "EP-05 input-port-pairs  family=tests.rules.sample_rules stage=build nature=? enforced=yes requires=-",
        "EP-06 room-heat-needs-receiver  family=tests.rules.sample_rules stage=build nature=? enforced=yes requires=EP-05",
        "    waived for demo: a demo may run an occupancy without a building",
        "    waived for plant: a plant has no heated zone",
    ]


def test_waivers_groups_every_waiver_by_kind() -> None:
    """Catches a waiver listed under the wrong kind, or a kind left out of the overview."""
    _, out, _ = Overview.run(["waivers"], Overview.registry())
    _, demo_only, _ = Overview.run(["waivers", "--kind", "demo"], Overview.registry())

    assert out.splitlines() == [
        "demo: 1 rule waived",
        "    EP-06 room-heat-needs-receiver: a demo may run an occupancy without a building",
        "residential: 0 rules waived",
        "commercial: 0 rules waived",
        "plant: 1 rule waived",
        "    EP-06 room-heat-needs-receiver: a plant has no heated zone",
    ]
    assert demo_only.splitlines() == out.splitlines()[:2]


def test_show_prints_the_same_rule_by_code_and_by_name_with_its_docstring() -> None:
    """Catches ``show`` resolving a name to another rule, or leaving out the fix every refusal points to."""
    _, by_code, _ = Overview.run(["show", "EP-06"], Overview.registry())
    _, by_name, _ = Overview.run(["show", "room-heat-needs-receiver"], Overview.registry())
    code, _, error = Overview.run(["show", "EP-99"], Overview.registry())

    assert by_code == by_name
    assert by_code.startswith("EP-06 room-heat-needs-receiver")
    assert "waived for plant: a plant has no heated zone" in by_code
    assert "How to fix: change the test." in by_code
    assert code == ExitCodes.USAGE and "no setup rule has the code or name 'EP-99'" in error


def test_check_prints_both_faults_as_text_and_as_json_and_exits_non_zero(tmp_path: Path) -> None:
    """Catches a check that reports only the first fault, loses the JSON fields, or exits zero on a refused file."""
    fixture = tmp_path / "two_faults.energy_system.yaml"
    fixture.write_text(
        "schema_version: 3\nname: two faults\nsetup_kind: demo\ncomponents:\n"
        "  weather:\n    class: hisim.components.weather.Weather\n    preset: aachen\n",
        encoding="utf-8",
    )
    registry = SetupRuleRegistry(
        [
            SampleRules.rule("EF-05", "first-file-rule", subject=LoadedDocument, findings=[Finding("first", key_path=("components", "weather"))]),
            SampleRules.rule("EF-06", "second-file-rule", subject=LoadedDocument, findings=[Finding("second", component="weather")]),
            SampleRules.rule("EP-01", "never-reached", subject=BuiltSystem, findings=[Finding("built")]),
        ]
    )

    code, text, _ = Overview.run(["check", str(fixture)], registry)
    json_code, printed, _ = Overview.run(["check", str(fixture), "--format", "json"], registry)

    assert code == json_code == ExitCodes.FILE_REJECTED
    assert text.splitlines() == [
        "load rules on the LoadedDocument (demo): 2 faults",
        "  - EF-05 first-file-rule [key_path=components.weather]: first; see: hisim setup-rules show EF-05",
        "  - EF-06 second-file-rule [component=weather]: second; see: hisim setup-rules show EF-06",
    ]
    report = json.loads(printed)
    assert report["kind"] == "demo" and len(report["stages"]) == 1
    assert [fault["code"] for fault in report["stages"][0]["faults"]] == ["EF-05", "EF-06"]
    assert report["stages"][0]["faults"][0]["key_path"] == ["components", "weather"]
    assert report["stages"][0]["faults"][1]["component"] == "weather"


def test_check_builds_a_clean_file_and_runs_every_build_subject() -> None:
    """Catches a check that stops after the load rules, or fails a file every run accepts."""
    twin = Path(__file__).resolve().parents[2] / "energy_systems" / "simple_system_setup_one.energy_system.yaml"

    code, out, _ = Overview.run(["check", str(twin)], SetupRuleRegistry([]))

    assert code == ExitCodes.OK
    assert out.splitlines() == [
        "load rules on the LoadedDocument (demo): no fault",
        "build rules on the BuiltDocument (demo): no fault",
        "build rules on the AddedSystem (demo): no fault",
        "build rules on the BuiltSystem (demo): no fault",
    ]


def test_the_command_line_answers_from_the_package_registry(capsys: pytest.CaptureFixture[str]) -> None:
    """Catches the noun or a verb missing from the command line."""
    assert main(["setup-rules", "list"]) == ExitCodes.OK
    assert main(["setup-rules", "waivers", "--kind", "plant"]) == ExitCodes.OK
    out = capsys.readouterr().out

    if not SetupRuleRegistry.collect().rules:
        assert "No setup rule is in the package yet" in out
    assert "plant: " in out
