"""Test quantities and units."""

import pytest
from hisim.units import Quantity, Watt


def test_instance_counter():
    """The InstanceCounter should raise RuntimeError if too many Instances of `Quantity` exist."""
    with pytest.raises(RuntimeError):
        for i in range(1001):
            _ = Quantity(i, Watt)
