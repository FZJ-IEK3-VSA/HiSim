"""Every rule of the package has direct tests: a test module of its family that builds a subject with the fault and one without."""

from pathlib import Path

import pytest

from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleCodes

pytestmark = pytest.mark.base


def test_every_rule_appears_in_the_test_module_of_its_family() -> None:
    """Catches a rule moved into the package without the tests that show it finds its fault and accepts a good subject.

    The test module of a family is ``tests/rules/test_<family>.py``; it names each rule's class. That the module
    builds a subject with the fault and one without is what a review checks.
    """
    directory = Path(__file__).resolve().parent
    for rule in SetupRuleRegistry.collect().rules:
        module = directory / f"test_{RuleCodes.family_of(rule.__module__)}.py"
        assert module.is_file(), f"{rule.CODE} has no test module {module.name}"
        assert rule.__name__ in module.read_text(encoding="utf-8"), f"{module.name} does not test {rule.__name__}"
