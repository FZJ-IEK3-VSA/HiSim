"""Sample setup rules for the tests of the mechanism: rule classes built on demand, with the faults they report.

The rules package holds the real rules; the tests of the registry, the aggregation and the overview need rules
whose faults they choose. ``SampleRules.rule`` builds one such class; it lives in this test module, so it belongs to
no family of the package and the registry only knows it when a test hands it over.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import FrozenSet, Mapping, Optional, Sequence, Tuple, Type

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import RuleClass
from hisim.setup_rules.rule import Fault, KeyPathStep, RuleSubject, SetupRule
from hisim.setup_rules.subjects import BuiltSystem


@dataclass(frozen=True)
class Finding:
    """One fault a sample rule reports on every subject: the message and the location fields."""

    message: str
    component: Optional[str] = None
    port: Optional[str] = None
    key_path: Tuple[KeyPathStep, ...] = ()
    file: Optional[str] = None


class SampleRules:
    """Builds sample rule classes for the tests."""

    #: A docstring that follows the template of the four headings.
    DOCSTRING: str = """A sample rule of the tests.

    What it checks: whatever the test asks it to find.

    Why: a test of the mechanism needs a rule whose faults it chooses.

    Example of a fault: ``sample fault``.

    How to fix: change the test.
    """

    @classmethod
    def rule(
        cls,
        code: str,
        name: str,
        *,
        subject: Type[RuleSubject] = BuiltSystem,
        enforced: bool = True,
        requires: FrozenSet[str] = frozenset(),
        waived_for: Mapping[SetupKind, str] = MappingProxyType({}),
        findings: Sequence[Finding] = (),
    ) -> RuleClass:
        """Return a rule class with this code and name that reports the given findings on every subject it reads.

        Args:
            code: The rule's code.
            name: The rule's kebab-case name.
            subject: The subject class the rule reads; its stage is the rule's stage.
            enforced: Whether the rule's faults refuse.
            requires: The codes the rule requires.
            waived_for: The kinds that waive the rule, with their reasons.
            findings: The faults the rule reports, in this order.

        Returns:
            The rule class.
        """
        reported = tuple(findings)

        class SampleRule(SetupRule[subject]):  # type: ignore[valid-type]  # the subject is chosen per test
            __doc__ = cls.DOCSTRING
            CODE = code
            NAME = name
            STAGE = subject.STAGE
            ENFORCED = enforced
            REQUIRES = requires
            WAIVED_FOR = waived_for

            def check(self, subject: RuleSubject) -> Sequence[Fault]:
                """Report the chosen findings, whatever the subject holds."""
                del subject  # a sample rule reports the same faults on every subject
                return tuple(
                    self.fault(finding.message, component=finding.component, port=finding.port, key_path=finding.key_path, file=finding.file)
                    for finding in reported
                )

        SampleRule.__qualname__ = f"SampleRule_{code.replace('-', '_')}"
        return SampleRule
