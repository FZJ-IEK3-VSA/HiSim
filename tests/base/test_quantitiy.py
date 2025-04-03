"""Test quantities and units."""

import itertools

import pytest

from hisim.units import Quantity, Watt
from hisim.utils import InstanceCounterMeta


@pytest.mark.base
def test_instance_counter():
    """The InstanceCounter should raise RuntimeError if too many Instances of `Quantity` exist."""
    with pytest.raises(RuntimeError):
        for i in range(1001):
            _ = Quantity(i, Watt)

    # Reset counter in InstanceCounterMeta
    InstanceCounterMeta.ids = itertools.count(1)
