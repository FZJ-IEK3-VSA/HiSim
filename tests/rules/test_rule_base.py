"""Direct tests of the setup rule base: the checks a rule's declarations meet when its class is created.

A malformed rule must fail when its module is imported, with a ``TypeError`` naming it, so that no run and no test
has to look for it: a waiver whose key is no kind or whose reason is blank, a code or a name of the wrong form, a
retired code, another family's prefix, a docstring that breaks the template, or a subject of another stage.
"""

from types import MappingProxyType
from typing import ClassVar, FrozenSet, Sequence

import pytest

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import Fault, RuleCodes, RuleDocstring, RuleStage, RuleSubject, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import BuiltSystem, LoadedDocument, RunResults
from tests.rules.sample_rules import SampleRules

pytestmark = pytest.mark.base


class TestWaivers:
    """``WAIVED_FOR``: a kind and a non-blank reason, nothing else."""

    def test_a_key_that_is_not_a_kind_fails_at_class_creation_naming_the_rule(self) -> None:
        """Catches a waiver spelled as a string, which would never match the declared kind and so waive nothing."""
        with pytest.raises(TypeError, match=r"SampleRule.*WAIVED_FOR names 'demo', which is not a SetupKind"):
            SampleRules.rule("EP-01", "a-rule", waived_for=MappingProxyType({"demo": "a reason"}))  # type: ignore[dict-item]

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_an_empty_or_blank_reason_fails_at_class_creation(self, reason: str) -> None:
        """Catches a waiver without a reason, which the overview of the waivers would show as a bare kind."""
        with pytest.raises(TypeError, match="WAIVED_FOR gives <SetupKind.DEMO: 'demo'> no reason"):
            SampleRules.rule("EP-01", "a-rule", waived_for=MappingProxyType({SetupKind.DEMO: reason}))

    def test_a_rule_without_waivers_inherits_the_empty_read_only_mapping(self) -> None:
        """Catches a default that is a shared mutable dict, which one rule could change for every other rule."""

        class Unwaived(SetupRule[BuiltSystem]):
            __doc__ = SampleRules.DOCSTRING
            CODE = "EP-01"
            NAME = "unwaived"
            STAGE = RuleStage.BUILD
            ENFORCED = True

        assert "WAIVED_FOR" not in Unwaived.__dict__ and not dict(Unwaived.WAIVED_FOR)
        assert Unwaived.WAIVED_FOR is SetupRule.WAIVED_FOR
        with pytest.raises(TypeError):
            Unwaived.WAIVED_FOR[SetupKind.DEMO] = "changed"  # type: ignore[index]

    def test_a_well_formed_mapping_is_kept_as_declared(self) -> None:
        """Catches the base rewriting, copying or narrowing a waiver it accepted."""
        waivers = MappingProxyType({SetupKind.DEMO: "a demo has no building", SetupKind.PLANT: "a plant has no heated zone"})

        rule = SampleRules.rule("EP-01", "a-rule", waived_for=waivers)

        assert rule.WAIVED_FOR is waivers

    def test_waiver_problems_lists_each_problem_once(self) -> None:
        """Catches the static check stopping at the first problem, which would hide the others of one rule."""
        problems = SetupRule.waiver_problems({"plant": "", SetupKind.DEMO: "fine"})  # type: ignore[dict-item]  # the malformed key is the test

        assert problems == ["its WAIVED_FOR names 'plant', which is not a SetupKind", "its WAIVED_FOR gives 'plant' no reason"]


class TestCodesAndNames:
    """``CODE`` and ``NAME``: the scheme, the retired codes and the family prefixes."""

    @pytest.mark.parametrize("code", ["EP-01", "EF-31", "EF-4K", "AR-1A"])
    def test_a_code_of_the_scheme_has_no_problem(self, code: str) -> None:
        """Catches a pattern that refuses the lettered EF codes the error catalogue already uses."""
        assert not RuleCodes.code_problems(code, "tests.rules.test_rule_base")

    @pytest.mark.parametrize("code", ["EP-6", "ep-06", "EP06", "EPX-06", "EP-A1", 6])
    def test_a_code_off_the_scheme_is_named(self, code: object) -> None:
        """Catches a code a message, a test or a bead could not cite unambiguously."""
        assert RuleCodes.code_problems(code, "tests.rules.test_rule_base") == [
            f"its CODE {code!r} is not of the form <PREFIX>-<nn>, for example 'EP-06'"
        ]

    def test_a_retired_code_is_refused(self) -> None:
        """Catches a removed rule's code given to a new rule, which would change what old messages and beads mean."""

        class Retiring(RuleCodes):
            """The scheme with one retired code."""

            RETIRED_CODES: ClassVar[FrozenSet[str]] = frozenset({"EP-09"})

        assert Retiring.code_problems("EP-09", "tests.rules.test_rule_base") == [
            "its CODE EP-09 is retired and is never given to another rule"
        ]

    def test_a_rule_of_the_package_carries_its_family_prefix(self) -> None:
        """Catches an energy-port rule numbered in the file checks' band, or a module that is no family."""
        assert not RuleCodes.code_problems("EP-06", "hisim.setup_rules.energy_ports")
        assert RuleCodes.code_problems("EF-31", "hisim.setup_rules.energy_ports") == [
            "its CODE EF-31 does not carry the prefix 'EP-' of the family 'energy_ports'"
        ]
        assert RuleCodes.code_problems("EP-06", "hisim.setup_rules.misc")[0].startswith("its module 'misc' is not a rule family")

    def test_the_family_of_a_module(self) -> None:
        """Catches a test's rule held to a family's prefix, or a package module not recognised as a family."""
        assert RuleCodes.family_of("hisim.setup_rules.meters") == "meters"
        assert RuleCodes.family_of("tests.rules.test_rule_base") is None
        assert RuleCodes.family_of("hisim.setup_rules") is None

    @pytest.mark.parametrize("name, good", [("room-heat-needs-receiver", True), ("finite", True), ("a2-b", True),
                                            ("RoomHeat", False), ("room_heat", False), ("-room", False), ("room-", False)])
    def test_a_name_is_kebab_case(self, name: str, good: bool) -> None:
        """Catches a name a reader cannot type after ``hisim setup-rules show``."""
        assert (not RuleCodes.name_problems(name)) is good

    def test_requirements_are_a_frozenset_of_codes_without_the_own_one(self) -> None:
        """Catches a requirement on oneself, a misspelt code, or a mutable set."""
        assert not RuleCodes.requires_problems(frozenset({"EP-05"}), "EP-06")
        assert RuleCodes.requires_problems(frozenset({"EP-06", "x"}), "EP-06") == [
            "its REQUIRES names 'x', which is not a code of the form <PREFIX>-<nn>",
            "its REQUIRES names its own code EP-06",
        ]
        assert RuleCodes.requires_problems({"EP-05"}, "EP-06") == ["its REQUIRES {'EP-05'} is not a frozenset of codes"]

    def test_a_malformed_rule_fails_at_class_creation_with_every_problem(self) -> None:
        """Catches the check stopping at the first problem, or a malformed rule that imports without error."""
        with pytest.raises(TypeError) as refusal:

            class Malformed(SetupRule[LoadedDocument]):  # pylint: disable=unused-variable  # its creation is the test
                """No template."""

                CODE = "X"
                NAME = "Bad Name"
                STAGE = RuleStage.LOAD
                ENFORCED = 1  # type: ignore[assignment]  # the malformed value is the test

        message = str(refusal.value)
        assert "Malformed is declared wrongly" in message
        for part in ("its CODE 'X'", "its NAME 'Bad Name'", "its ENFORCED 1 is not a bool", "its docstring has the headings []"):
            assert part in message

    def test_a_rule_must_declare_its_fields_on_its_own_class(self) -> None:
        """Catches a rule that inherits STAGE or ENFORCED silently from a parent it was copied from."""
        with pytest.raises(TypeError, match="it does not declare NAME, STAGE, ENFORCED on its own class"):

            class Partial(SetupRule[BuiltSystem]):  # pylint: disable=unused-variable  # its creation is the test
                """A rule with only a code."""

                CODE = "EP-01"

    def test_a_violation_that_is_no_setup_rule_violation_fails_at_class_creation(self) -> None:
        """Catches a family naming an error the registry cannot build with the faults, or one an ``except SetupRuleViolation`` misses."""
        with pytest.raises(TypeError, match="its VIOLATION <class 'ValueError'> is not a subclass of SetupRuleViolation"):

            class WrongError(SetupRule[BuiltSystem]):  # pylint: disable=unused-variable  # its creation is the test
                __doc__ = SampleRules.DOCSTRING
                CODE = "EP-01"
                NAME = "wrong-error"
                STAGE = RuleStage.BUILD
                ENFORCED = True
                VIOLATION = ValueError  # type: ignore[assignment]  # the wrong type is the test

    def test_a_rule_without_a_violation_raises_the_base_error(self) -> None:
        """Catches a default that is no error class, which would break every rule that names none."""
        assert SampleRules.rule("EP-01", "a-rule").VIOLATION is SetupRuleViolation

    def test_a_class_without_a_code_is_a_family_base_and_not_checked(self) -> None:
        """Catches the base refusing a shared parent of a family's rules, which has no code of its own."""

        class FamilyBase(SetupRule[BuiltSystem]):
            """The shared base of a family's rules."""

        assert FamilyBase.subject_type() is BuiltSystem


class TestSubject:
    """The subject a rule reads: named by the type argument, of the rule's stage."""

    def test_the_subject_is_the_type_argument_of_the_base(self) -> None:
        """Catches the registry handing a rule a subject of another class."""
        assert SampleRules.rule("AR-01", "finite", subject=RunResults).subject_type() is RunResults

    def test_a_rule_without_a_subject_fails(self) -> None:
        """Catches a rule the registry could never hand any subject, which would never run."""
        with pytest.raises(TypeError, match="it names no subject"):

            class Unbound(SetupRule):  # pylint: disable=unused-variable  # its creation is the test
                __doc__ = SampleRules.DOCSTRING
                CODE = "EP-01"
                NAME = "unbound"
                STAGE = RuleStage.BUILD
                ENFORCED = True

    def test_a_subject_of_another_stage_fails(self) -> None:
        """Catches a rule declared for one stage that reads the subject of another, and so would run at neither."""
        with pytest.raises(TypeError, match="its subject RunResults belongs to the stage after_run, not build"):

            class Mismatched(SetupRule[RunResults]):  # pylint: disable=unused-variable  # its creation is the test
                __doc__ = SampleRules.DOCSTRING
                CODE = "EP-01"
                NAME = "mismatched"
                STAGE = RuleStage.BUILD
                ENFORCED = True

                def check(self, subject: RunResults) -> Sequence[Fault]:
                    """Find nothing."""
                    return ()

    def test_the_stages_are_ordered_as_a_calculation_runs(self) -> None:
        """Catches a stage order the registry's requirement check would read backwards."""
        assert [stage.position for stage in (RuleStage.LOAD, RuleStage.BUILD, RuleStage.AFTER_RUN)] == [0, 1, 2]
        assert issubclass(LoadedDocument, RuleSubject)


class TestDocstringTemplate:
    """``RuleDocstring.problems``: four headings, in order, each followed by text."""

    def test_the_template_passes(self) -> None:
        """Catches a check that refuses the template it asks for."""
        assert RuleDocstring.problems(SampleRules.DOCSTRING) == []

    @pytest.mark.parametrize(
        "docstring",
        [
            None,
            "What it checks: x.\n\nWhy: y.\n\nHow to fix: z.",
            "Why: y.\n\nWhat it checks: x.\n\nExample of a fault: f.\n\nHow to fix: z.",
            "What it checks: x.\n\nWhy: y.\n\nWhy: again.\n\nExample of a fault: f.\n\nHow to fix: z.",
        ],
    )
    def test_a_missing_misplaced_or_repeated_heading_is_named(self, docstring: str) -> None:
        """Catches a rule whose ``show`` output would lack the fix or the example."""
        assert RuleDocstring.problems(docstring)[0].startswith("its docstring has the headings")

    def test_a_heading_without_text_is_named(self) -> None:
        """Catches a heading kept as a placeholder, which ``show`` would print empty."""
        docstring = "What it checks: x.\n\nWhy:\n\nExample of a fault: f.\n\nHow to fix: z."

        assert RuleDocstring.problems(docstring) == ["its docstring's heading 'Why' is followed by no text"]

    def test_the_text_may_start_on_the_next_line(self) -> None:
        """Catches a check that only accepts text on the heading's own line."""
        docstring = "What it checks:\n    x.\n\nWhy:\n    y.\n\nExample of a fault:\n    f.\n\nHow to fix:\n    z."

        assert RuleDocstring.problems(docstring) == []


class TestFaultFactory:
    """``SetupRule.fault``: the rule's code, name and stage, and the pointer at the end of the message."""

    def test_a_fault_carries_the_rule_and_ends_with_the_pointer(self) -> None:
        """Catches a fault whose reader is not told which command explains the rule."""
        rule = SampleRules.rule("EP-01", "a-rule")

        fault = rule.fault("the port is unread", component="buffer", port="HeatOut")

        assert fault == Fault(
            code="EP-01",
            name="a-rule",
            stage=RuleStage.BUILD,
            message="the port is unread; see: hisim setup-rules show EP-01",
            component="buffer",
            port="HeatOut",
        )
        assert fault.message.endswith(rule.pointer())
