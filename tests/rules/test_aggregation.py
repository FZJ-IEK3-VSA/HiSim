"""Tests of how the registry runs a stage: every fault of every enforced rule in one violation, skips, and log-only rules.

A run that stopped at the first fault would cost one run per fault; the registry runs every applicable rule of the
stage and raises one ``SetupRuleViolation`` listing all faults in code order, then by component and port. A rule
that needs another to have passed is skipped, visibly, when that one found faults. A rule that is not enforced
logs its faults and refuses nothing.
"""

from typing import Type

import pytest

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import RuleClass, SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, SetupRuleViolation, Skip
from hisim.setup_rules.subjects import BuiltSystem
from tests.rules.sample_rules import Finding, SampleRules

pytestmark = pytest.mark.base


def built_system() -> BuiltSystem:
    """Return an empty built system; the sample rules report their faults on any subject."""
    return BuiltSystem(components=())


def test_two_rules_that_find_faults_give_one_violation_with_both_in_code_order() -> None:
    """Catches a stage that stops at the first rule with a fault, or lists the faults in the order the rules ran."""
    registry = SetupRuleRegistry(
        [
            SampleRules.rule("EP-02", "second", findings=[Finding("b on y", component="y"), Finding("b on x", component="x")]),
            SampleRules.rule("EP-01", "first", findings=[Finding("a")]),
        ]
    )

    with pytest.raises(SetupRuleViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    violation = refusal.value
    assert [(fault.code, fault.component) for fault in violation.faults] == [("EP-01", None), ("EP-02", "x"), ("EP-02", "y")]
    assert (violation.stage, violation.kind, violation.skipped) == (RuleStage.BUILD, SetupKind.RESIDENTIAL, ())
    assert str(violation).splitlines() == [
        "The build rules refuse this residential setup (3 faults):",
        "- EP-01 first: a; see: hisim setup-rules show EP-01",
        "- EP-02 second: b on x; see: hisim setup-rules show EP-02",
        "- EP-02 second: b on y; see: hisim setup-rules show EP-02",
    ]


def test_a_rule_that_requires_a_rule_with_faults_is_skipped_and_listed() -> None:
    """Catches a rule run on a subject an earlier rule found broken, or a skip that nobody is told about."""
    registry = SetupRuleRegistry(
        [
            SampleRules.rule("EP-05", "pairing", findings=[Finding("unpaired")]),
            SampleRules.rule("EP-06", "needs-pairing", requires=frozenset({"EP-05"}), findings=[Finding("would be nonsense")]),
            SampleRules.rule("EP-07", "needs-the-skipped", requires=frozenset({"EP-06"})),
        ]
    )

    with pytest.raises(SetupRuleViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    assert [fault.code for fault in refusal.value.faults] == ["EP-05"]
    assert refusal.value.skipped == (Skip("EP-06", ("EP-05",)), Skip("EP-07", ("EP-06",)))
    assert str(refusal.value).splitlines()[-3:] == [
        "Not run:",
        "- EP-06 was not run: it requires EP-05, which found faults or was not run",
        "- EP-07 was not run: it requires EP-06, which found faults or was not run",
    ]


def test_a_rule_whose_requirement_passed_runs() -> None:
    """Catches a requirement that skips its rule even when the required rule found nothing."""
    registry = SetupRuleRegistry(
        [SampleRules.rule("EP-05", "pairing"), SampleRules.rule("EP-06", "needs-pairing", requires=frozenset({"EP-05"}), findings=[Finding("x")])]
    )

    with pytest.raises(SetupRuleViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    assert [fault.code for fault in refusal.value.faults] == ["EP-06"]


def test_a_rule_that_is_not_enforced_logs_its_faults_and_does_not_raise(capsys: pytest.CaptureFixture[str]) -> None:
    """Catches a log-only rule that refuses the run, or one whose faults vanish without a line."""
    registry = SetupRuleRegistry([SampleRules.rule("EP-01", "only-logs", enforced=False, findings=[Finding("unlinked", component="tank", port="HeatOut")])])

    findings = registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    assert not findings.faults and [fault.code for fault in findings.logged] == ["EP-01"]
    assert "EP-01 only-logs [component=tank, port=HeatOut]: unlinked; see: hisim setup-rules show EP-01" in capsys.readouterr().out


def test_a_waived_rule_does_not_run_for_its_kind() -> None:
    """Catches a waiver that the registry ignores, so a demo is refused by a rule it waives."""
    registry = SetupRuleRegistry([SampleRules.rule("EP-06", "room-heat", waived_for={SetupKind.DEMO: "a demo has no building"}, findings=[Finding("x")])])

    assert not registry.run(RuleStage.BUILD, built_system(), SetupKind.DEMO).faults
    with pytest.raises(SetupRuleViolation):
        registry.run(RuleStage.BUILD, built_system(), SetupKind.COMMERCIAL)


def test_every_refusal_message_ends_with_the_pointer_to_its_rule() -> None:
    """Catches a fault whose reader is not told which command explains the rule and its fix."""
    registry = SetupRuleRegistry(
        [SampleRules.rule("EP-01", "first", findings=[Finding("a"), Finding("b")]), SampleRules.rule("FL-02", "second", findings=[Finding("c")])]
    )

    with pytest.raises(SetupRuleViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    assert all(fault.message.endswith(f"see: hisim setup-rules show {fault.code}") for fault in refusal.value.faults)
    assert all(line.endswith(f"see: hisim setup-rules show {line.split()[1]}") for line in str(refusal.value).splitlines()[1:])


class FamilyViolation(SetupRuleViolation):
    """The error of a sample family whose checks raised it before they moved into the rules package."""


class OtherFamilyViolation(SetupRuleViolation):
    """The error of a second sample family."""


def family_rule(code: str, name: str, violation: Type[SetupRuleViolation]) -> RuleClass:
    """Return a sample rule with one fault that names ``violation`` as its error."""
    rule = SampleRules.rule(code, name, findings=[Finding(name)])
    rule.VIOLATION = violation
    return rule


def test_the_faults_of_one_family_raise_its_own_error() -> None:
    """Catches an ``except EnergyPortWiringError`` that stopped working when the energy-port checks moved into the package."""
    registry = SetupRuleRegistry([family_rule("EP-01", "first", FamilyViolation), family_rule("EP-02", "second", FamilyViolation)])

    with pytest.raises(FamilyViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    assert [fault.code for fault in refusal.value.faults] == ["EP-01", "EP-02"]


def test_the_violation_type_is_the_common_error_of_the_rules_with_faults_else_the_base() -> None:
    """Catches the error of one family raised for faults of another, which an ``except`` of that family would wrongly catch."""
    own = family_rule("EP-01", "own", FamilyViolation)
    other = family_rule("EP-02", "other", OtherFamilyViolation)
    plain = SampleRules.rule("EP-03", "plain")

    assert SetupRuleRegistry.violation_type([own, own]) is FamilyViolation
    assert SetupRuleRegistry.violation_type([own, other]) is SetupRuleViolation
    assert SetupRuleRegistry.violation_type([own, plain]) is SetupRuleViolation
    assert SetupRuleRegistry.violation_type([]) is SetupRuleViolation


def test_a_rule_of_another_error_that_finds_nothing_does_not_change_the_error() -> None:
    """Catches the error chosen from every rule that ran rather than from the rules that found faults."""
    quiet = SampleRules.rule("EP-02", "quiet")
    quiet.VIOLATION = OtherFamilyViolation
    registry = SetupRuleRegistry([family_rule("EP-01", "loud", FamilyViolation), quiet])

    with pytest.raises(FamilyViolation):
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)


def test_a_violation_holds_the_findings_it_was_raised_from() -> None:
    """Catches a tool that catches the violation of a hook losing the logged faults or the subject of the stage."""
    registry = SetupRuleRegistry(
        [SampleRules.rule("EP-01", "refusing", findings=[Finding("a")]), SampleRules.rule("EP-02", "logging", enforced=False, findings=[Finding("b")])]
    )

    with pytest.raises(SetupRuleViolation) as refusal:
        registry.run(RuleStage.BUILD, built_system(), SetupKind.RESIDENTIAL)

    findings = refusal.value.findings
    assert findings is not None and findings.subject == "BuiltSystem"
    assert [fault.code for fault in findings.faults] == ["EP-01"] and [fault.code for fault in findings.logged] == ["EP-02"]
    assert SetupRuleViolation(RuleStage.BUILD, SetupKind.DEMO, [], []).findings is None
