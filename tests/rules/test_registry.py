"""Tests of the registry of the setup rules: the package's rules, how rules fit together, and the precondition of a kind.

The first group holds for every rule the package defines, whatever the family pull requests add: a code of the
family's prefix, a kebab-case name, a stage and an enforcement, no shared code or name, no live code retired, and
requirements that name live rules of the same or an earlier stage without a cycle. The second group checks the
registry's own functions on sample rules.
"""

import subprocess
import sys

import pytest

from hisim.setup_rules.inventory import Inventory
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleCodes, RuleStage, UndeclaredSetupKindError
from hisim.setup_rules.subjects import BuiltDocument, BuiltSystem, LoadedDocument, RunResults
from tests.rules.sample_rules import Finding, SampleRules

pytestmark = pytest.mark.base


class TestThePackage:
    """Every rule ``SetupRuleRegistry.collect()`` finds in ``hisim/setup_rules/``."""

    def test_every_rule_declares_a_code_of_its_family_a_kebab_name_a_stage_and_an_enforcement(self) -> None:
        """Catches a rule whose declarations the base let through, for example one defined outside a family module."""
        for rule in SetupRuleRegistry.collect().rules:
            family = RuleCodes.family_of(rule.__module__)
            assert family is not None and family in RuleCodes.PREFIX_BY_FAMILY, rule
            assert rule.CODE.startswith(RuleCodes.PREFIX_BY_FAMILY[family] + "-"), rule
            assert not RuleCodes.name_problems(rule.NAME), rule
            assert isinstance(rule.STAGE, RuleStage) and isinstance(rule.ENFORCED, bool), rule

    def test_no_two_rules_share_a_code_or_a_name_and_no_live_code_is_retired(self) -> None:
        """Catches a copied rule that kept its template's code, or a retired code brought back."""
        rules = SetupRuleRegistry.collect().rules
        codes = [rule.CODE for rule in rules]
        names = [rule.NAME for rule in rules]

        assert len(set(codes)) == len(codes) and len(set(names)) == len(names)
        assert not set(codes) & RuleCodes.RETIRED_CODES

    def test_every_requirement_names_a_live_rule_of_the_same_or_an_earlier_stage_without_a_cycle(self) -> None:
        """Catches a requirement on a removed rule, on a rule that runs later, or a cycle that would skip both rules."""
        rules = SetupRuleRegistry.collect().rules

        assert not SetupRuleRegistry.registry_problems(rules)

    def test_the_registry_collects_exactly_the_rules_the_inventory_places_in_the_package(self) -> None:
        """Catches a moved rule whose record still names its old place, or a record that claims a rule nobody wrote."""
        collected = {
            f"{RuleCodes.family_of(rule.__module__)}.{rule.NAME.replace('-', '_')}" for rule in SetupRuleRegistry.collect().rules
        }
        recorded = {record.rule_id for record in Inventory.RECORDS if record.in_package}

        assert collected == recorded

    def test_discovery_finds_only_classes_that_declare_a_code(self) -> None:
        """Catches a shared base of a family, or a rule imported into another module, collected as a rule of its own."""
        discovered = SetupRuleRegistry.discover()

        assert all("CODE" in rule.__dict__ for rule in discovered)
        assert len(set(discovered)) == len(discovered)

    def test_the_package_imports_no_component(self) -> None:
        """Catches a rule importing a component module: the rules read their subject and nothing else.

        A fresh interpreter collects the registry and lists what that loaded; ``hisim.components`` must not be
        among it, so the schema exporter and the editor-side checks can import the rules without the component tree.
        """
        program = (
            "import sys; from hisim.setup_rules.registry import SetupRuleRegistry; SetupRuleRegistry.collect(); "
            "print('\\n'.join(sorted(sys.modules)))"
        )
        finished = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=False)

        assert finished.returncode == 0, finished.stderr
        assert [name for name in finished.stdout.split() if name.startswith("hisim.components")] == []


class TestFittingTogether:
    """``registry_problems``, ``requirement_cycle`` and ``execution_order`` on sample rules."""

    def test_rules_that_fit_together_have_no_problem(self) -> None:
        """Catches a check that refuses a requirement on an earlier stage."""
        rules = [
            SampleRules.rule("EF-05", "no-absolute-path", subject=LoadedDocument),
            SampleRules.rule("EP-05", "input-port-pairs", requires=frozenset({"EF-05"})),
        ]

        assert not SetupRuleRegistry.registry_problems(rules)

    def test_a_shared_code_or_name_is_refused(self) -> None:
        """Catches two rules a message could not tell apart."""
        with pytest.raises(TypeError, match="share the CODE EP-01.*share the NAME same-name"):
            SetupRuleRegistry([SampleRules.rule("EP-01", "one"), SampleRules.rule("EP-01", "two"), SampleRules.rule("EP-02", "same-name"),
                               SampleRules.rule("EP-03", "same-name")])

    def test_a_requirement_on_no_rule_or_on_a_later_stage_is_refused(self) -> None:
        """Catches a requirement that can never be met: a removed rule, or one whose result comes after."""
        rules = [
            SampleRules.rule("EP-01", "early", requires=frozenset({"AR-01", "EP-99"})),
            SampleRules.rule("AR-01", "late", subject=RunResults),
        ]

        assert SetupRuleRegistry.registry_problems(rules) == [
            "EP-01 (build) requires AR-01 of the later stage after_run",
            "EP-01 requires EP-99, which is no rule",
        ]

    def test_a_cycle_of_requirements_is_refused(self) -> None:
        """Catches two rules that wait for each other and would both be skipped for ever."""
        with pytest.raises(TypeError, match="the requirements form a cycle: EP-01 -> EP-02 -> EP-01"):
            SetupRuleRegistry([SampleRules.rule("EP-01", "one", requires=frozenset({"EP-02"})),
                               SampleRules.rule("EP-02", "two", requires=frozenset({"EP-01"}))])

    def test_requirement_cycle(self) -> None:
        """Catches a cycle search that misses a longer cycle, or reports one where a required code is only a leaf."""
        assert SetupRuleRegistry.requirement_cycle({"A": frozenset({"B"}), "B": frozenset({"C"}), "C": frozenset({"A"})}) == ("A", "B", "C", "A")
        assert SetupRuleRegistry.requirement_cycle({"A": frozenset({"B"}), "B": frozenset({"X"})}) == ()
        assert SetupRuleRegistry.requirement_cycle({"A": frozenset({"A"})}) == ("A", "A")
        assert SetupRuleRegistry.requirement_cycle({}) == ()

    def test_execution_order_is_code_order_except_after_a_requirement(self) -> None:
        """Catches a stage that runs a rule before the rule it requires, or leaves code order without a reason."""
        first = SampleRules.rule("EF-31", "needs-pairing", requires=frozenset({"EP-05"}))
        second = SampleRules.rule("EP-01", "independent")
        third = SampleRules.rule("EP-05", "pairing")
        outside = SampleRules.rule("EP-07", "needs-another-stage", requires=frozenset({"EF-05"}))

        order = SetupRuleRegistry.execution_order([first, second, third, outside])

        assert [rule.CODE for rule in order] == ["EP-01", "EP-05", "EF-31", "EP-07"]

    def test_the_registry_holds_its_rules_in_code_order_and_finds_one_by_code_or_name(self) -> None:
        """Catches a stage whose message changes order between runs, or ``show`` missing a rule by its name."""
        registry = SetupRuleRegistry([SampleRules.rule("EP-02", "second"), SampleRules.rule("EP-01", "first")])

        assert [rule.CODE for rule in registry.rules] == ["EP-01", "EP-02"]
        assert registry.rule("second") is registry.rule("EP-02") is registry.rules[1]
        assert registry.rule("EP-09") is None

    def test_rules_for_drops_the_rules_a_kind_waives_and_the_other_stages(self) -> None:
        """Catches a waiver that does not take its rule out of the kind's stage."""
        waived = SampleRules.rule("EP-01", "waived", waived_for={SetupKind.DEMO: "a demo has no building"})
        applies = SampleRules.rule("EP-02", "applies")
        other_stage = SampleRules.rule("AR-01", "after", subject=RunResults)
        registry = SetupRuleRegistry([waived, applies, other_stage])

        assert registry.rules_for(RuleStage.BUILD, SetupKind.DEMO) == (applies,)
        assert registry.rules_for(RuleStage.BUILD, SetupKind.RESIDENTIAL) == (waived, applies)


class TestPrecondition:
    """The registry refuses a setup without a kind, and a subject of another stage."""

    def test_a_subject_without_a_kind_is_refused_with_the_message_that_says_where_to_declare_it(self) -> None:
        """Catches an undeclared setup that runs as if it had a default kind."""
        registry = SetupRuleRegistry([])

        with pytest.raises(UndeclaredSetupKindError) as refusal:
            registry.run(RuleStage.BUILD, BuiltSystem(components=()), None)

        assert str(refusal.value) == (
            "this setup declares no kind: write `setup_kind: residential | commercial | plant | demo` into the file, "
            "or call `my_sim.declare_setup_kind(SetupKind.RESIDENTIAL)` in the setup function"
        )

    def test_a_subject_of_another_stage_is_refused(self) -> None:
        """Catches a hook that hands the results to the build rules."""
        with pytest.raises(TypeError, match="BuiltSystem is the subject of the stage build, not after_run"):
            SetupRuleRegistry([]).run(RuleStage.AFTER_RUN, BuiltSystem(components=()), SetupKind.DEMO)

    def test_a_stage_without_faults_returns_its_findings(self) -> None:
        """Catches a hook that fails a clean system."""
        registry = SetupRuleRegistry([SampleRules.rule("EP-01", "clean")])

        findings = registry.run(RuleStage.BUILD, BuiltSystem(components=()), SetupKind.RESIDENTIAL)

        assert (findings.faults, findings.logged, findings.skipped, findings.subject) == ((), (), (), "BuiltSystem")

    def test_a_rule_reads_only_the_subject_it_names(self) -> None:
        """Catches the two build hooks running each other's rules: a rule of the built file on the components."""
        on_document = SampleRules.rule("EF-31", "on-the-file", subject=BuiltDocument, findings=[Finding("x")])
        registry = SetupRuleRegistry([on_document])

        assert not registry.findings(RuleStage.BUILD, BuiltSystem(components=()), SetupKind.DEMO).faults
