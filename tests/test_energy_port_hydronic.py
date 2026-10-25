"""The :class:`HydronicPort` of ``hisim/energy_port.py``: its validation and the heat it derives.

No component declares a hydronic port yet; these tests pin that a port refuses names, roles and carriers that
cannot describe a circuit, and that its heat is the hydronics library's and refuses what the library refuses.
"""

import pytest

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.energy_port import HydronicPort, HydronicPortError

pytestmark = pytest.mark.base


class Circuit:

    """The output names of one valid circuit, shared by the tests of this module."""

    #: Mass flow, supply temperature and return temperature output names, in the order the port takes them.
    NAMES = ("MassFlowSh", "SupplyTemperatureSh", "ReturnTemperatureSh")
    #: The heat the circuit carries.
    CARRIER = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT


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
    ("mass_flow_in_kg_per_second", "supply_temperature_in_celsius", "return_temperature_in_celsius", "seconds_per_timestep"),
    [(0.4, 35.0, 30.04, 900.0), (0.333, 7.0, 12.3, 60.0), (0.0, 55.0, 45.0, 3600.0), (1.7, 40.1, 40.1, 1.0)],
)
def test_the_heat_is_the_circuit_power_times_the_step_to_the_last_bit(
    mass_flow_in_kg_per_second: float,
    supply_temperature_in_celsius: float,
    return_temperature_in_celsius: float,
    seconds_per_timestep: float,
) -> None:
    """A booked power and a derived heat evaluated in another order would differ in the last bits and unbalance."""
    power_in_watt = hydronics.circuit_power_w(
        mass_flow_kg_per_s=mass_flow_in_kg_per_second,
        t_supply_c=supply_temperature_in_celsius,
        t_return_c=return_temperature_in_celsius,
    )
    assert power_in_watt == mass_flow_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * (
        supply_temperature_in_celsius - return_temperature_in_celsius
    )
    heat_in_kilowatt_hour = HydronicPort.heat_in_kilowatt_hour(
        mass_flow_in_kg_per_second=mass_flow_in_kg_per_second,
        supply_temperature_in_celsius=supply_temperature_in_celsius,
        return_temperature_in_celsius=return_temperature_in_celsius,
        seconds_per_timestep=seconds_per_timestep,
    )
    assert heat_in_kilowatt_hour == power_in_watt * seconds_per_timestep / hydronics.UnitConversion.JOULES_PER_KILOWATT_HOUR


@pytest.mark.parametrize(
    ("mass_flow_in_kg_per_second", "supply_temperature_in_celsius", "seconds_per_timestep", "error"),
    [
        (-0.1, 60.0, 900.0, hydronics.NegativeMassFlowError),
        (0.1, 60.0, 0.0, hydronics.NonPositiveTimestepError),
    ],
)
def test_the_heat_refuses_what_the_library_refuses(
    mass_flow_in_kg_per_second: float, supply_temperature_in_celsius: float, seconds_per_timestep: float, error: type
) -> None:
    """A port that booked a negative flow or an empty step would hide the caller's error."""
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
        HydronicPort(*names, role=lt.EnergyRole.OUT, carrier=Circuit.CARRIER)


def test_names_obey_the_one_identifier_rule() -> None:
    """A Python identifier outside HiSim's ASCII rule would be accepted here but refused by ``add_output``."""
    assert "Vorlauftemperaturé".isidentifier()
    with pytest.raises(HydronicPortError, match="supply_temperature"):
        HydronicPort(
            "MassFlowSh", "Vorlauftemperaturé", "ReturnTemperatureSh", role=lt.EnergyRole.OUT, carrier=Circuit.CARRIER
        )


@pytest.mark.parametrize("role", [lt.EnergyRole.LOSS, "in"])
def test_role_is_in_or_out(role: object) -> None:
    """A circuit end is the supply owner (OUT) or the receiver (IN); any other role would unbalance the circuit."""
    with pytest.raises(HydronicPortError):
        # the refusal of a wrong type is tested
        HydronicPort(*Circuit.NAMES, role=role, carrier=Circuit.CARRIER)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "carrier", [lt.EnergyBalanceCarrier.ELECTRICITY, lt.EnergyBalanceCarrier.ROOM_HEAT, "space_heating_heat"]
)
def test_carrier_is_space_heating_or_hot_water_heat(carrier: object) -> None:
    """The Sankey must know which heat a circuit carries (spec §4.2); a circuit carries no other carrier."""
    with pytest.raises(HydronicPortError, match="space-heating or hot-water heat"):
        HydronicPort(*Circuit.NAMES, role=lt.EnergyRole.OUT, carrier=carrier)  # type: ignore[arg-type]


def test_a_circuit_names_its_heat() -> None:
    """Both circuit carriers are accepted and kept."""
    for carrier in HydronicPort.CIRCUIT_CARRIERS:
        assert HydronicPort(*Circuit.NAMES, role=lt.EnergyRole.IN, carrier=carrier).carrier is carrier
