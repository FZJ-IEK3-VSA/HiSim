"""Tests for the TransformerConfig factory and classname classmethods.

These tests pin down the pure, side-effect-free classmethods on
``TransformerConfig`` that are otherwise untested. They only construct
dataclass instances / call classmethods and assert field values - no
simulation, no I/O.
"""

# clean

import pytest

from hisim.components.transformer_rectifier import Transformer, TransformerConfig


@pytest.mark.base
def test_get_default_transformer_returns_config_with_documented_defaults() -> None:
    """``get_default_transformer()`` returns a ``TransformerConfig`` with the hardcoded fields."""
    config = TransformerConfig.get_default_transformer()
    assert isinstance(config, TransformerConfig)
    assert config.building_name == "BUI1"
    assert config.name == "Generic Transformer and rectifier Unit"
    assert config.efficiency == pytest.approx(0.95)


@pytest.mark.base
def test_get_default_transformer_is_deterministic_but_distinct() -> None:
    """Two calls return equal values but not the same object identity."""
    first = TransformerConfig.get_default_transformer()
    second = TransformerConfig.get_default_transformer()
    assert first == second
    assert first is not second


@pytest.mark.base
def test_get_main_classname_is_str() -> None:
    """``get_main_classname()`` returns a string."""
    classname = TransformerConfig.get_main_classname()
    assert isinstance(classname, str)


@pytest.mark.base
def test_get_main_classname_delegates_to_transformer() -> None:
    """``get_main_classname()`` delegates to ``Transformer.get_full_classname()``."""
    classname = TransformerConfig.get_main_classname()
    assert classname == Transformer.get_full_classname()


@pytest.mark.base
def test_get_main_classname_ends_with_transformer() -> None:
    """The fully-qualified class path ends with the ``Transformer`` class name."""
    classname = TransformerConfig.get_main_classname()
    assert classname.endswith(".Transformer")
    # The exact full string is also known, so pin it exactly.
    assert classname == "hisim.components.transformer_rectifier.Transformer"
