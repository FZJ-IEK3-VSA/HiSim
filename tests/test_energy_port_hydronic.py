"""The :class:`HydronicPort` of ``hisim/energy_port.py``: its validation, the heat it derives and how circuits pair.

A port refuses names, roles and carriers that cannot describe a circuit, and its heat is the hydronics library's
and refuses what the library refuses. A component declares its end of a circuit with
:meth:`hisim.component.Component.add_hydronic_port`, by its own channels. The balance check pairs the two ends of a
circuit through the wiring alone (hydronic stage E, complete Sankey spec §4.2): both ends resolve to the same three
outputs and share one heat series, the reader of a mass flow is the other end of its owner, and the rules of
:mod:`hisim.energy_port` hold: one reader per flow, both ends or neither, an end that pumps water must see where it
goes. Those tests build a result table and component stand-ins by hand, as ``tests/test_energy_balance.py`` does.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import hydronics
from hisim import loadtypes as lt
from hisim.config import ComponentID
from hisim.energy_port import CircuitEnd, EnergyPort, HydronicPort, HydronicPortError
from hisim.postprocessing.energy_balance import (
    EnergyBalanceError,
    EnergyBalanceReport,
    EnergyPortWiringError,
    PeerKind,
    Wiring,
)
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder

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


def test_the_heat_of_a_run_is_the_heat_of_each_step() -> None:
    """The balance derives every step at once; each step must be the one-step rule's number, a cooling step too."""
    flows = np.array([0.1, 0.0, 0.25])
    supplies = np.array([60.0, 40.0, 18.0])
    returns = np.array([50.0, 40.0, 22.0])
    heat = HydronicPort.heat_in_kilowatt_hours_per_step(
        mass_flow_in_kg_per_second=flows,
        supply_temperature_in_celsius=supplies,
        return_temperature_in_celsius=returns,
        seconds_per_timestep=900.0,
    )
    for step, kilowatt_hours in enumerate(heat):
        assert kilowatt_hours == HydronicPort.heat_in_kilowatt_hour(
            mass_flow_in_kg_per_second=float(flows[step]),
            supply_temperature_in_celsius=float(supplies[step]),
            return_temperature_in_celsius=float(returns[step]),
            seconds_per_timestep=900.0,
        )
    assert heat[2] < 0.0
    with pytest.raises(hydronics.NegativeMassFlowError, match="in step 1"):
        HydronicPort.heat_in_kilowatt_hours_per_step(
            mass_flow_in_kg_per_second=np.array([0.1, -0.1]),
            supply_temperature_in_celsius=supplies[:2],
            return_temperature_in_celsius=returns[:2],
            seconds_per_timestep=900.0,
        )


class CircuitRig:

    """Hand-built runs of one boiler charging one tank through one circuit, and variations that break a rule.

    The boiler owns the circuit's flow and supply (``Flow``, ``Supply``) and reads the tank's return (input
    ``Return``); the tank reads the flow and the supply (inputs ``Flow`` and ``Supply``) and publishes the return.
    0.1 kg/s from 60 °C back at 50 °C over 900 s carry 1.045 kWh, which the boiler takes in as 4180 W of ambient
    heat (an unmetered fuel would fail a run without undeclared components) and the tank loses as 4180 W of standby
    loss, so both close.
    """

    #: One column of a one-step result table: component, output, unit, energy port or None, value.
    Column = Tuple[str, str, lt.Units, Optional[EnergyPort], float]
    #: The step length of every rig, in s.
    SECONDS_PER_TIMESTEP: int = 900
    #: What the boiler takes in.
    AMBIENT = lt.EnergyBalanceCarrier.AMBIENT_HEAT
    #: The heat the circuit carries in one step, in kWh.
    HEAT_IN_KILOWATT_HOUR: float = 0.1 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 10.0 * 900 / 3.6e6
    #: The boiler's end: it publishes the flow and the supply and reads the return.
    SUPPLY_END = CircuitEnd(
        HydronicPort("Flow", "Supply", "Return", role=lt.EnergyRole.OUT, carrier=Circuit.CARRIER),
        input_legs=frozenset({"return_temperature"}),
    )
    #: The tank's end: it reads the flow and the supply and publishes the return.
    RECEIVING_END = CircuitEnd(
        HydronicPort("Flow", "Supply", "Return", role=lt.EnergyRole.IN, carrier=Circuit.CARRIER),
        input_legs=frozenset({"mass_flow", "supply_temperature"}),
    )

    @staticmethod
    def wired(field: str, source: Optional[str], source_field: Optional[str]) -> SimpleNamespace:
        """An input of a stand-in, connected to ``source.source_field`` or, with None, to nothing."""
        return SimpleNamespace(field_name=field, src_object_name=source, src_field_name=source_field)

    @classmethod
    def report(
        cls,
        columns: Sequence["CircuitRig.Column"],
        inputs: Dict[str, List[SimpleNamespace]],
        ends: Dict[str, List[CircuitEnd]],
    ) -> EnergyBalanceReport:
        """The balance report of a one-step run: ``columns`` as (component, output, unit, port, value)."""
        outputs = cls.outputs_of(columns)
        table = pd.DataFrame(
            [[value for *_, value in columns]],
            index=pd.date_range("2021-01-01", periods=1, freq=f"{cls.SECONDS_PER_TIMESTEP}s"),
        )
        wrapped = [SimpleNamespace(my_component=stand_in) for stand_in in cls.stand_ins(outputs, inputs, ends)]
        return EnergyBalanceReport(table, outputs, wrapped, cls.SECONDS_PER_TIMESTEP)

    @staticmethod
    def outputs_of(columns: Sequence["CircuitRig.Column"]) -> List[cp.ComponentOutput]:
        """The outputs of the columns, each of its component, unit and port."""
        return [
            cp.ComponentOutput(
                component, field, lt.LoadTypes.HEATING, unit, energy_port=port, component_id=ComponentID(component)
            )
            for component, field, unit, port, _ in columns
        ]

    @staticmethod
    def stand_ins(
        outputs: Sequence[cp.ComponentOutput], inputs: Dict[str, List[SimpleNamespace]], ends: Dict[str, List[CircuitEnd]]
    ) -> List[Any]:
        """One stand-in per component of ``outputs``, in their order, carrying its wiring, outputs and circuit ends."""
        names: List[str] = []
        for output in outputs:
            if output.component_name not in names:
                names.append(output.component_name)
        return [
            SimpleNamespace(
                component_name=name,
                inputs=inputs.get(name, []),
                outputs=[output for output in outputs if output.component_name == name],
                hydronic_ports=ends.get(name, []),
            )
            for name in names
        ]

    @classmethod
    def boiler_columns(cls, flow_in_kg_per_second: float = 0.1) -> List["CircuitRig.Column"]:
        """The boiler's flow, supply and heat taken in; the heat is what the circuit carries at 0.1 kg/s."""
        return [
            ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, flow_in_kg_per_second),
            ("Boiler", "Supply", lt.Units.CELSIUS, None, 60.0),
            ("Boiler", "Heat", lt.Units.WATT, EnergyPort(lt.EnergyRole.IN, cls.AMBIENT), 4180.0),
        ]

    @classmethod
    def tank_columns(cls, name: str = "Tank") -> List["CircuitRig.Column"]:
        """A tank's return and its standby loss, which is what the circuit brings."""
        return [
            (name, "Return", lt.Units.CELSIUS, None, 50.0),
            (name, "Loss", lt.Units.WATT, EnergyPort(lt.EnergyRole.LOSS, Circuit.CARRIER), 4180.0),
        ]

    @classmethod
    def tank_inputs(cls, name: str = "Tank") -> List[SimpleNamespace]:
        """A tank's flow and supply inputs, connected to the boiler."""
        return [cls.wired("Flow", "Boiler", "Flow"), cls.wired("Supply", "Boiler", "Supply")]

    @classmethod
    def paired(cls) -> EnergyBalanceReport:
        """The boiler and the tank, both declaring their ends of the circuit."""
        return cls.report(
            cls.boiler_columns() + cls.tank_columns(),
            {"Boiler": [cls.wired("Return", "Tank", "Return")], "Tank": cls.tank_inputs()},
            {"Boiler": [cls.SUPPLY_END], "Tank": [cls.RECEIVING_END]},
        )


def test_both_ends_of_a_circuit_share_one_heat_and_pair_through_the_wiring() -> None:
    """The reader of the flow is the owner's other end and the owner the reader's; one heat, one closing link.

    Fails if the two ends derived their heat apart, named no peer, or booked a link that does not close.
    """
    report = CircuitRig.paired()
    (supply,) = report.declared.by_component["Boiler"][1:]
    (receiver,) = [series for series in report.declared.by_component["Tank"] if series.circuit is not None]
    assert supply.kilowatt_hours is receiver.kilowatt_hours
    assert supply.annual_kilowatt_hours == pytest.approx(CircuitRig.HEAT_IN_KILOWATT_HOUR, rel=1e-12)
    assert (supply.peer.kind, supply.peer.name) == (PeerKind.COMPONENT, "Tank")
    assert (receiver.peer.kind, receiver.peer.name) == (PeerKind.COMPONENT, "Boiler")
    assert supply.circuit == receiver.circuit and supply.circuit is not None
    assert supply.circuit.as_dict() == {
        "mass_flow": "Boiler.Flow",
        "supply_temperature": "Boiler.Supply",
        "return_temperature": "Tank.Return",
    }
    assert not report.declared.circuit_faults and not report.fails
    (link,) = report.links
    assert (link.sender, link.receiver, link.closes) == ("Boiler", "Tank", True)
    report.conclude()
    entry = report.report()
    tank_ports = [port for item in entry["components"] if item["component"] == "Tank" for port in item["ports"]]
    assert {"output": "circuit Boiler.Flow", "unit": "kWh"}.items() <= tank_ports[-1].items()
    # the Sankey draws the circuit once, at the receiver, from the boiler
    links = SankeyBuilder(report.declared, report.balances, (), report.links).build()
    assert [(link.source, link.target) for link in links if link.label.endswith("circuit Boiler.Flow")] == [
        ("Boiler", "Tank")
    ]


def test_one_flow_read_by_two_ends_fails_the_run() -> None:
    """One reader: a second tank declaring the boiler's flow would book its heat twice; the run fails naming both."""
    columns = CircuitRig.boiler_columns() + CircuitRig.tank_columns() + CircuitRig.tank_columns("Twin")
    inputs = {
        "Boiler": [CircuitRig.wired("Return", "Tank", "Return")],
        "Tank": CircuitRig.tank_inputs(),
        "Twin": CircuitRig.tank_inputs("Twin"),
    }
    ends = {"Boiler": [CircuitRig.SUPPLY_END], "Tank": [CircuitRig.RECEIVING_END], "Twin": [CircuitRig.RECEIVING_END]}
    report = CircuitRig.report(columns, inputs, ends)
    faults = report.declared.circuit_faults
    assert any("Boiler.Flow is read by 2 components" in fault and "Twin" in fault for fault in faults)
    with pytest.raises(EnergyBalanceError, match="one flow delivers its heat once"):
        report.conclude()
    # the wiring alone shows it: the run is refused before it simulates
    components = CircuitRig.stand_ins(CircuitRig.outputs_of(columns), inputs, ends)
    with pytest.raises(EnergyPortWiringError, match="Boiler.Flow is read by 2 components"):
        Wiring.refuse_a_broken_wiring(components)


def test_an_other_end_that_declares_ports_but_not_the_circuit_fails_the_run() -> None:
    """Both ends or neither: a tank that declares its loss but not its end of the boiler's circuit is a fault."""
    report = CircuitRig.report(
        CircuitRig.boiler_columns() + CircuitRig.tank_columns(),
        {"Boiler": [CircuitRig.wired("Return", "Tank", "Return")], "Tank": CircuitRig.tank_inputs()},
        {"Boiler": [CircuitRig.SUPPLY_END]},
    )
    assert any("its other end, Tank, declares ports and not this circuit" in f for f in report.declared.circuit_faults)
    with pytest.raises(EnergyBalanceError, match="circuit: Boiler declares its end"):
        report.conclude()


def test_an_other_end_that_declares_no_ports_is_drawn_as_undeclared() -> None:
    """A tank that declares nothing is a coverage gap, not a fault: the circuit goes to "undeclared: Tank"."""
    columns = CircuitRig.boiler_columns() + [("Tank", "Return", lt.Units.CELSIUS, None, 50.0)]
    report = CircuitRig.report(
        columns,
        {"Boiler": [CircuitRig.wired("Return", "Tank", "Return")], "Tank": CircuitRig.tank_inputs()},
        {"Boiler": [CircuitRig.SUPPLY_END]},
    )
    assert not report.declared.circuit_faults and report.declared.undeclared == ["Tank"]
    report.conclude()
    links = SankeyBuilder(report.declared, report.balances, (), report.links).build()
    assert ("Boiler", "undeclared: Tank") in {(link.source, link.target) for link in links}


def test_two_ends_with_the_same_role_fail_the_run() -> None:
    """A circuit has one supply owner and one receiver; two receivers would both count its heat as gained."""
    both_receive = CircuitEnd(
        HydronicPort("Flow", "Supply", "Return", role=lt.EnergyRole.IN, carrier=Circuit.CARRIER),
        input_legs=frozenset({"return_temperature"}),
    )
    report = CircuitRig.report(
        CircuitRig.boiler_columns() + CircuitRig.tank_columns(),
        {"Boiler": [CircuitRig.wired("Return", "Tank", "Return")], "Tank": CircuitRig.tank_inputs()},
        {"Boiler": [both_receive], "Tank": [CircuitRig.RECEIVING_END]},
    )
    assert any("both declare the circuit" in fault for fault in report.declared.circuit_faults)


def test_an_end_whose_flow_input_is_not_connected_is_idle() -> None:
    """A tank's circuit to a second generator the house does not have moves no water: it is left out, no fault."""
    report = CircuitRig.report(
        CircuitRig.tank_columns(),
        {"Tank": [CircuitRig.wired("Flow", None, None), CircuitRig.wired("Supply", None, None)]},
        {"Tank": [CircuitRig.RECEIVING_END]},
    )
    assert not report.declared.circuit_faults
    assert [series.port_name for series in report.declared.by_component["Tank"]] == ["Loss"]


@pytest.mark.parametrize(("flow_in_kg_per_second", "faults"), [(0.1, 1), (0.0, 0)])
def test_an_end_that_pumps_water_it_cannot_follow_fails_the_run(flow_in_kg_per_second: float, faults: int) -> None:
    """A boiler pumping into a circuit whose return it cannot read loses that heat from view; idle, it is left out."""
    columns = CircuitRig.boiler_columns(flow_in_kg_per_second)
    report = CircuitRig.report(
        columns, {"Boiler": [CircuitRig.wired("Return", None, None)]}, {"Boiler": [CircuitRig.SUPPLY_END]}
    )
    assert len(report.declared.circuit_faults) == faults
    if faults:
        assert "return_temperature input Return is not connected" in report.declared.circuit_faults[0]


class FakeComponent(cp.PhysicsComponent):
    """A physics component built without a configuration, to call :meth:`add_hydronic_port` on its channels."""


def bare_component(name: str) -> Any:
    """A component with its name and channels only, as :meth:`add_hydronic_port` needs."""
    component: Any = object.__new__(FakeComponent)
    component.component_name = name
    component.inputs = [cp.ComponentInput(name, "Return", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, False)]
    component.outputs = [
        cp.ComponentOutput(name, field, lt.LoadTypes.ANY, unit, component_id=ComponentID(name))
        for field, unit in (("Flow", lt.Units.KG_PER_SEC), ("Supply", lt.Units.CELSIUS), ("Power", lt.Units.WATT))
    ]
    component.hydronic_ports = []
    return component


def test_a_component_declares_its_end_by_its_own_channels() -> None:
    """Outputs are its published legs, inputs the legs it reads; a foreign channel or a wrong unit is refused."""
    boiler = bare_component("Boiler")
    flow, supply, power = boiler.outputs
    (returned,) = boiler.inputs
    end = boiler.add_hydronic_port(
        mass_flow=flow,
        supply_temperature=supply,
        return_temperature=returned,
        role=lt.EnergyRole.OUT,
        carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
    )
    assert end == CircuitRig.SUPPLY_END and boiler.hydronic_ports == [end]
    assert end.is_input("return_temperature") and not end.is_input("mass_flow")
    other = bare_component("Tank")
    with pytest.raises(HydronicPortError, match="not one of its own inputs or outputs"):
        boiler.add_hydronic_port(
            mass_flow=other.outputs[0],
            supply_temperature=supply,
            return_temperature=returned,
            role=lt.EnergyRole.OUT,
            carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
        )
    with pytest.raises(HydronicPortError, match="mass_flow Power is in W, not kg/s"):
        boiler.add_hydronic_port(
            mass_flow=power,
            supply_temperature=supply,
            return_temperature=returned,
            role=lt.EnergyRole.OUT,
            carrier=lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
        )
    with pytest.raises(HydronicPortError, match="input legs"):
        CircuitEnd(CircuitRig.SUPPLY_END.port, input_legs=frozenset({"flow"}))


def heat_output(component: str, field: str, port: Optional[EnergyPort]) -> cp.ComponentOutput:
    """An output in W of a stand-in, with or without an energy port."""
    return cp.ComponentOutput(
        component, field, lt.LoadTypes.HEATING, lt.Units.WATT, energy_port=port, component_id=ComponentID(component)
    )


@pytest.mark.parametrize(
    ("port", "refused"),
    [
        (EnergyPort(lt.EnergyRole.OUT, Circuit.CARRIER), True),
        (EnergyPort(lt.EnergyRole.OUT, Circuit.CARRIER, peer_output="Heat"), False),
        (EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.ELECTRICITY), False),
    ],
)
def test_an_output_two_declared_components_read_must_name_its_recipient(port: EnergyPort, refused: bool) -> None:
    """Rule (a) of stage E (owner, 2026-10-02): a heat port read by two components that declare ports names one.

    A metered port is exempt from the rule: its meter comes first (owner decision Q-J, spec §4.3).
    """
    outputs = [
        heat_output("Source", "Heat", port),
        heat_output("First", "Loss", EnergyPort(lt.EnergyRole.LOSS, Circuit.CARRIER)),
        heat_output("Second", "Loss", EnergyPort(lt.EnergyRole.LOSS, Circuit.CARRIER)),
    ]
    inputs = {name: [CircuitRig.wired("HeatIn", "Source", "Heat")] for name in ("First", "Second")}
    components = CircuitRig.stand_ins(outputs, inputs, {})
    if not refused:
        Wiring.refuse_a_broken_wiring(components)
        return
    with pytest.raises(EnergyPortWiringError, match=r"Source.Heat .* is read by 2 components .* \(First, Second\)"):
        Wiring.refuse_a_broken_wiring(components)
