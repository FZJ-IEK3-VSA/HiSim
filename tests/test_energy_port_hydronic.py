"""The :class:`HydronicPort` of ``hisim/energy_port.py`` (hydronic coupling spec §3.5, stage A, hisim-fxix.2).

Declared by no component yet and read by no balance check yet (stage E); these tests pin its validation and
that its energy refuses what the hydronics library refuses.
"""

import math

import pytest

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.energy_port import HydronicPort, HydronicPortError

pytestmark = pytest.mark.base

NAMES = ("MassFlowSh", "SupplyTemperatureSh", "ReturnTemperatureSh")


def test_kilowatt_hours_refuses_what_the_library_refuses() -> None:
    """A negative flow, a non-finite temperature and a step that is not positive are refused."""
    port = HydronicPort(*NAMES, role=lt.EnergyRole.IN)
    with pytest.raises(hydronics.NegativeMassFlowError):
        port.kilowatt_hours(-0.1, 60.0, 50.0, 900.0)
    with pytest.raises(hydronics.NonFiniteValueError):
        port.kilowatt_hours(0.1, math.nan, 50.0, 900.0)
    with pytest.raises(hydronics.NonPositiveTimestepError):
        port.kilowatt_hours(0.1, 60.0, 50.0, 0.0)


@pytest.mark.parametrize(
    "names",
    [
        ("", "SupplyTemperatureSh", "ReturnTemperatureSh"),
        ("Mass Flow", "SupplyTemperatureSh", "ReturnTemperatureSh"),
        ("MassFlowSh", "1Supply", "ReturnTemperatureSh"),
        ("MassFlowSh", "SupplyTemperatureSh", None),
        ("MassFlowSh", "MassFlowSh", "ReturnTemperatureSh"),
    ],
)
def test_names_must_be_distinct_identifiers(names: tuple) -> None:
    """Each name is an identifier, and the three are distinct."""
    with pytest.raises(HydronicPortError):
        HydronicPort(*names, role=lt.EnergyRole.OUT)


def test_names_obey_the_one_identifier_rule() -> None:
    """A Python identifier outside HiSim's ASCII rule is refused, as ``add_output`` refuses it (NameSyntax)."""
    assert "Vorlauftemperaturé".isidentifier()
    with pytest.raises(HydronicPortError, match="supply_temperature"):
        HydronicPort("MassFlowSh", "Vorlauftemperaturé", "ReturnTemperatureSh", role=lt.EnergyRole.OUT)


@pytest.mark.parametrize("role", [lt.EnergyRole.LOSS, "in"])
def test_role_is_in_or_out(role: object) -> None:
    """A circuit end is the supply owner (OUT) or the receiver (IN); a raw string is not an EnergyRole."""
    with pytest.raises(HydronicPortError):
        HydronicPort(*NAMES, role=role)  # type: ignore[arg-type]


def test_the_port_error_is_a_value_error() -> None:
    """Callers that catch ValueError for invalid declarations also catch this one."""
    assert issubclass(HydronicPortError, ValueError)
