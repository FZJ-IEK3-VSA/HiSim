"""The port of the heat a device releases follows where the device stands (bead hisim-device-location-820d.1).

A device in the heated zone hands its heat to the building as an internal gain, a ``PRODUCTION`` port of
``ROOM_HEAT``; a device in an unheated space or outdoors loses it out of the house, a ``LOSS`` port of its own carrier.
"""

import pytest

from hisim import loadtypes as lt
from hisim.energy_port import EnergyPort


@pytest.mark.base
@pytest.mark.parametrize(
    ("location", "carrier", "expected"),
    [
        (
            lt.InstallationLocation.HEATED_ZONE,
            lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
            EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        ),
        (
            lt.InstallationLocation.HEATED_ZONE,
            lt.EnergyBalanceCarrier.ELECTRICITY,
            EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        ),
        (
            lt.InstallationLocation.UNHEATED_SPACE,
            lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
            EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        ),
        (
            lt.InstallationLocation.UNHEATED_SPACE,
            lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
            EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT),
        ),
        (
            lt.InstallationLocation.OUTDOORS,
            lt.EnergyBalanceCarrier.ELECTRICITY,
            EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
        ),
    ],
)
def test_the_heat_a_device_releases_is_a_gain_in_the_heated_zone_and_a_loss_elsewhere(
    location: lt.InstallationLocation, carrier: lt.EnergyBalanceCarrier, expected: EnergyPort
) -> None:
    """In the heated zone the heat is room heat for the building, in an unheated space and outdoors a loss.

    Catches: a heated-zone device whose heat goes out of the house, a cellar or outdoor device whose heat is handed to
    the building, and a loss booked under another carrier than the device's own.
    """
    assert EnergyPort.for_heat_released(location, carrier) == expected


@pytest.mark.base
def test_a_loss_out_of_the_house_goes_to_its_boundary_and_heat_into_the_zone_has_none() -> None:
    """A loss from an unheated space reaches its boundary node by itself; heat into the zone needs its reader.

    Catches: a released-heat port in the heated zone that a boundary would absorb, so the building's input would book
    a transfer the device did not send.
    """
    assert EnergyPort.for_heat_released(
        lt.InstallationLocation.UNHEATED_SPACE, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
    ).default_boundary() == lt.EnvironmentNode.OUTDOORS.value
    assert (
        EnergyPort.for_heat_released(
            lt.InstallationLocation.HEATED_ZONE, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
        ).default_boundary()
        is None
    )
