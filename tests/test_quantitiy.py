from hisim.units import Quantity, Watt


def test_instance_counter():
    """InstanceCounter should raise RuntimeError if too many Instances of `Quantity` exist."""
    for i in range(1001):
        _ = Quantity(i, Watt)
