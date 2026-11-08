"""Which of its two released-heat outputs a device fills follows where it stands (bead hisim-device-location-820d.1).

A located device has two fixed outputs of the heat it releases (owner decision D-h of the device-location spec):
``HeatToHeatedZoneInWatt``, which declares ``EnergyPort.HEAT_RELEASED_INTO_THE_HEATED_ZONE`` (``PRODUCTION`` of
``ROOM_HEAT``) and which the building reads, and ``HeatLossInWatt``, a ``LOSS`` of the device's own carrier.
``InstallationLocation.releases_into_heated_zone`` picks the one the location fills.
"""

import pytest

from hisim import loadtypes as lt
from hisim.energy_port import EnergyPort


@pytest.mark.base
@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (lt.InstallationLocation.HEATED_ZONE, True),
        (lt.InstallationLocation.UNHEATED_SPACE, False),
        (lt.InstallationLocation.OUTDOORS, False),
    ],
)
def test_only_a_device_in_the_heated_zone_releases_its_heat_into_it(
    location: lt.InstallationLocation, expected: bool
) -> None:
    """Catches a cellar or outdoor device handing its heat to the building, and a heated-zone device losing it outside."""
    assert location.releases_into_heated_zone is expected


@pytest.mark.base
def test_the_heat_released_into_the_heated_zone_is_room_heat_produced_with_no_boundary() -> None:
    """The port the building reads is production of room heat and needs its reader: no boundary node absorbs it.

    Catches: a port the building's dynamic inputs would not pair with (another role or carrier), and a boundary
    default that would book the heat as delivered when no building reads it.
    """
    port = EnergyPort.HEAT_RELEASED_INTO_THE_HEATED_ZONE
    assert port == EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT)
    assert port.pairs_with(EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT))
    assert port.default_boundary() is None
