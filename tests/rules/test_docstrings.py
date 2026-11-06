"""Every rule's docstring follows the template that ``hisim setup-rules show`` prints and every refusal points to."""

import inspect

import pytest

from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleDocstring

pytestmark = pytest.mark.base


def test_every_rule_carries_the_four_headings_in_order_each_followed_by_text() -> None:
    """Catches a rule whose ``show`` output lacks what it checks, why, an example of a fault or how to fix it."""
    for rule in SetupRuleRegistry.collect().rules:
        assert RuleDocstring.problems(rule.__doc__) == [], rule
        text = inspect.cleandoc(rule.__doc__ or "")
        positions = [text.index(f"{heading}:") for heading in RuleDocstring.HEADINGS]
        assert positions == sorted(positions), rule


def test_the_template_has_the_four_headings() -> None:
    """Catches a heading renamed in the check but not in the rules, or the other way round."""
    assert RuleDocstring.HEADINGS == ("What it checks", "Why", "Example of a fault", "How to fix")
