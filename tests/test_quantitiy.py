"""Test quantities and units."""

import itertools

import pytest

from hisim.units import Kilowatt, Quantity, Watt
from hisim.utils import InstanceCounterMeta


@pytest.fixture(autouse=True)
def _reset_instance_counter() -> itertools.count:
    """Reset the shared ``InstanceCounter`` id counter around every test.

    ``Quantity`` shares a global id counter (see ``InstanceCounterMeta``) that
    raises ``RuntimeError`` once it exceeds 1000 instances. Resetting it here
    keeps the tests independent so one test cannot push another over the limit.
    """
    previous = InstanceCounterMeta.ids
    InstanceCounterMeta.ids = itertools.count(1)
    yield
    InstanceCounterMeta.ids = previous


@pytest.mark.base
def test_instance_counter() -> None:
    """The InstanceCounter should raise RuntimeError if too many Instances of `Quantity` exist."""
    with pytest.raises(RuntimeError):
        for i in range(1001):
            _ = Quantity(i, Watt)


@pytest.mark.base
def test_quantity_add_invalid_type() -> None:
    """Adding a non-Quantity raises TypeError, not AttributeError.

    The ``isinstance`` guard in ``_is_valid_operand`` makes ``__add__`` return
    ``NotImplemented`` for invalid operands so that Python's operator machinery
    raises a clear ``TypeError`` rather than an ``AttributeError`` leaking from
    accessing ``.unit`` on a bare ``int``.
    """
    quantity = Quantity(1, Watt)
    with pytest.raises(TypeError):
        _ = quantity + 1  # type: ignore[operator]


@pytest.mark.base
def test_quantity_radd_invalid_type() -> None:
    """Reverse-adding a non-Quantity raises TypeError via ``__radd__``."""
    quantity = Quantity(1, Watt)
    with pytest.raises(TypeError):
        _ = 1 + quantity  # type: ignore[operator]


@pytest.mark.base
def test_quantity_sub_invalid_type() -> None:
    """Subtracting a non-Quantity raises TypeError, not AttributeError."""
    quantity = Quantity(1, Watt)
    with pytest.raises(TypeError):
        _ = quantity - 1  # type: ignore[operator]


@pytest.mark.base
def test_quantity_rsub_invalid_type() -> None:
    """Reverse-subtracting a non-Quantity raises TypeError via ``__rsub__``."""
    quantity = Quantity(1, Watt)
    with pytest.raises(TypeError):
        _ = 1 - quantity  # type: ignore[operator]


@pytest.mark.base
@pytest.mark.parametrize("operator", ["lt", "le", "gt", "ge"])
def test_quantity_comparison_invalid_type(operator: str) -> None:
    """Comparing a Quantity with a non-Quantity raises TypeError."""
    quantity = Quantity(1, Watt)
    func = {
        "lt": lambda a, b: a < b,
        "le": lambda a, b: a <= b,
        "gt": lambda a, b: a > b,
        "ge": lambda a, b: a >= b,
    }[operator]
    with pytest.raises(TypeError):
        _ = func(quantity, 1)


@pytest.mark.base
def test_quantity_add_different_unit() -> None:
    """Adding Quantities with different units raises TypeError."""
    watts = Quantity(1, Watt)
    kilowatts = Quantity(1, Kilowatt)
    with pytest.raises(TypeError):
        _ = watts + kilowatts


@pytest.mark.base
def test_quantity_arithmetic_valid() -> None:
    """Arithmetic between same-unit Quantities returns the expected result."""
    one = Quantity(1, Watt)
    two = Quantity(2, Watt)
    assert (one + two).value == 3
    assert (one - two).value == -1
    assert (one + two).unit is Watt
    assert (one - two).unit is Watt


@pytest.mark.base
def test_quantity_comparison_valid() -> None:
    """Comparisons between same-unit Quantities behave like the wrapped values."""
    one = Quantity(1, Watt)
    two = Quantity(2, Watt)
    assert one < two
    assert one <= two
    assert two > one
    assert two >= one
    assert one == Quantity(1, Watt)
    assert one != two


@pytest.mark.base
def test_quantity_eq_invalid_type() -> None:
    """Equality with a non-Quantity returns False instead of raising."""
    quantity = Quantity(1, Watt)
    assert quantity == quantity  # pylint: disable=comparison-with-itself  # same object is fine
    assert (quantity == 1) is False
    assert (quantity == "1") is False
