"""Tests of a fault's fields and of the violation's plain-data form.

A fault carries its location as fields beside the message: the component and the port for a rule on the built
system, the key path and the file for a rule on a document. The violation's ``to_dict`` holds every field and
round-trips through JSON, which is what ``hisim setup-rules check --format json`` prints.
"""

import dataclasses
import json

import pytest

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import Fault, RuleStage, SetupRuleViolation, Skip
from hisim.setup_rules.subjects import BuiltSystem, LoadedDocument
from tests.rules.sample_rules import Finding, SampleRules

pytestmark = pytest.mark.base


def test_a_fault_is_frozen() -> None:
    """Catches a fault a later rule or a renderer could change after the rule reported it."""
    fault = Fault(code="EP-01", name="a-rule", stage=RuleStage.BUILD, message="m")

    with pytest.raises(dataclasses.FrozenInstanceError):
        fault.message = "changed"  # type: ignore[misc]


def test_a_document_rule_fills_the_key_path_and_the_file_and_a_build_rule_the_component_and_the_port() -> None:
    """Catches a location that is lost between the rule and the fault, so a tool cannot point at it."""
    on_file = SampleRules.rule(
        "EF-31", "on-the-file", subject=LoadedDocument,
        findings=[Finding("unfed", key_path=("components", "Battery1", "inputs", 0), file="house.energy_system.yaml")],
    )
    on_components = SampleRules.rule("EP-06", "on-the-components", subject=BuiltSystem, findings=[Finding("unread", component="buffer", port="HeatOut")])

    (document_fault,) = on_file().check(BuiltSystem(components=()))  # type: ignore[arg-type]  # the sample reads nothing
    (build_fault,) = on_components().check(BuiltSystem(components=()))

    assert (document_fault.key_path, document_fault.file, document_fault.component) == (
        ("components", "Battery1", "inputs", 0), "house.energy_system.yaml", None)
    assert (build_fault.component, build_fault.port, build_fault.key_path, build_fault.file) == ("buffer", "HeatOut", (), None)


def test_the_violation_as_plain_data_holds_every_field_and_round_trips_through_json() -> None:
    """Catches a field missing from the JSON report that CI or RenoVisor's problem list would read."""
    fault = Fault(code="EF-31", name="mandatory-input-connected", stage=RuleStage.LOAD, message="m; see: hisim setup-rules show EF-31",
                  component="Battery1", port="LoadingPowerInput", key_path=("components", "Battery1", "inputs", 0), file="a.energy_system.yaml")
    violation = SetupRuleViolation(RuleStage.LOAD, SetupKind.DEMO, [fault], [Skip("EF-32", ("EF-31",))])

    document = violation.to_dict()

    assert document == {
        "stage": "load",
        "kind": "demo",
        "faults": [{
            "code": "EF-31", "name": "mandatory-input-connected", "stage": "load", "message": "m; see: hisim setup-rules show EF-31",
            "component": "Battery1", "port": "LoadingPowerInput", "key_path": ["components", "Battery1", "inputs", 0], "file": "a.energy_system.yaml",
        }],
        "skipped": [{"code": "EF-32", "because_of": ["EF-31"]}],
    }
    assert json.loads(json.dumps(document)) == document


def test_the_log_line_names_the_location_that_applies() -> None:
    """Catches a logged fault without its place, or with empty brackets for a fault of the whole system."""
    located = Fault(code="EP-01", name="a", stage=RuleStage.BUILD, message="m", component="tank", port="HeatOut")
    whole = Fault(code="EF-05", name="b", stage=RuleStage.LOAD, message="m", key_path=("components", "pv", 2), file="f.yaml")

    assert located.log_line() == "EP-01 a [component=tank, port=HeatOut]: m"
    assert whole.log_line() == "EF-05 b [file=f.yaml, key_path=components.pv.2]: m"
    assert Fault(code="AR-01", name="c", stage=RuleStage.AFTER_RUN, message="m").log_line() == "AR-01 c: m"
