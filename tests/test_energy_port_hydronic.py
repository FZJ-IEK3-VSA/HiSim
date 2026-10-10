"""The :class:`HydronicPort` of ``hisim/energy_port.py``: its validation and the heat it derives.

No component declares a hydronic port yet; these tests pin that a port refuses names and roles that cannot
describe a circuit, and that its heat is the hydronics library's and refuses what the library refuses.
"""

import math

import pytest

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.energy_port import HydronicPort, HydronicPortError

pytestmark = pytest.mark.base


class Circuit:

    """The output names of one valid circuit, shared by the tests of this module."""

    #: Mass flow, supply temperature and return temperature output names, in the order the port takes them.
    NAMES = ("MassFlowSh", "SupplyTemperatureSh", "ReturnTemperatureSh")


def test_the_heat_is_the_library_s_circuit_heat() -> None:
    """0.1 kg/s over 10 K for one hour is 4.18 kWh; a port that derived its heat by another rule would fail."""
    heat_in_kilowatt_hour = HydronicPort.heat_in_kilowatt_hour(
        mass_flow_in_kg_per_second=0.1,
        supply_temperature_in_celsius=60.0,
        return_temperature_in_celsius=50.0,
        seconds_per_timestep=3600.0,
    )
    assert heat_in_kilowatt_hour == pytest.approx(4.18, rel=1e-15)


@pytest.mark.parametrize(
    ("mass_flow_in_kg_per_second", "supply_temperature_in_celsius", "seconds_per_timestep", "error"),
    [
        (-0.1, 60.0, 900.0, hydronics.NegativeMassFlowError),
        (0.1, math.nan, 900.0, hydronics.NonFiniteValueError),
        (0.1, 60.0, 0.0, hydronics.NonPositiveTimestepError),
    ],
)
def test_the_heat_refuses_what_the_library_refuses(
    mass_flow_in_kg_per_second: float, supply_temperature_in_celsius: float, seconds_per_timestep: float, error: type
) -> None:
    """A port that booked a negative flow, a NaN temperature or an empty step would hide the caller's error."""
    with pytest.raises(error):
        HydronicPort.heat_in_kilowatt_hour(
            mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
            supply_temperature_in_celsius=supply_temperature_in_celsius,
            return_temperature_in_celsius=50.0,
            seconds_per_timestep=seconds_per_timestep,
        )


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
    """A port whose names are not three distinct identifiers would pair the wrong outputs; it must be refused."""
    with pytest.raises(HydronicPortError):
        HydronicPort(*names, role=lt.EnergyRole.OUT)


def test_names_obey_the_one_identifier_rule() -> None:
    """A Python identifier outside HiSim's ASCII rule would be accepted here but refused by ``add_output``."""
    assert "Vorlauftemperaturé".isidentifier()
    with pytest.raises(HydronicPortError, match="supply_temperature"):
        HydronicPort("MassFlowSh", "Vorlauftemperaturé", "ReturnTemperatureSh", role=lt.EnergyRole.OUT)


@pytest.mark.parametrize("role", [lt.EnergyRole.LOSS, "in"])
def test_role_is_in_or_out(role: object) -> None:
    """A circuit end is the supply owner (OUT) or the receiver (IN); any other role would unbalance the circuit."""
    with pytest.raises(HydronicPortError):
        HydronicPort(*Circuit.NAMES, role=role)  # type: ignore[arg-type]  # the refusal of a wrong type is tested
