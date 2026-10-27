"""Fluid loops (``hisim/energy_port.py``): their declaration on the channels, the heat they carry and how loops pair.

A component tags each channel of a water loop with ``fluid_loop=`` in its ``add_input`` or ``add_output`` call
(:class:`~hisim.energy_port.FluidLoopChannel`); the declaration refuses a tag that cannot describe its channel, and
the component's tagged channels, grouped by loop, are its port sets (:meth:`hisim.component.Component.port_sets`).
The balance check pairs the two members of a loop through the wiring alone (hydronic stage E, complete Sankey spec
§4.2): both resolve to the same three outputs and share one heat series, booked by the supplier's sign, the reader
of a mass flow is the other member of its owner, and the rules of :mod:`hisim.energy_port` hold: one reader per flow,
every member or none, exactly one supplier, a member that pumps water must see where it goes. Those tests build a
result table and component stand-ins by hand, as ``tests/test_energy_balance.py`` does.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import hydronics
from hisim import loadtypes as lt
from hisim.config import ComponentID
from hisim.energy_port import EnergyPort, FluidLoop, FluidLoopChannel, FluidLoopError, LoopFluid, LoopQuantity
from hisim.postprocessing.energy_balance import (
    CircuitColumns,
    DeclaredPorts,
    EnergyBalanceError,
    EnergyBalanceReport,
    EnergyPortWiringError,
    PeerKind,
    Wiring,
)
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder

pytestmark = pytest.mark.base


class Loops:

    """The fluid loops the tests of this module tag their channels with."""

    #: The heat the loops carry.
    CARRIER = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT

    @classmethod
    def loop(cls, *, supplies: bool, name: str = "space_heating", may_cool: bool = False) -> FluidLoop:
        """A water loop of space-heating heat, as a member that supplies it or not states it."""
        return FluidLoop(name, carrier=cls.CARRIER, fluid=LoopFluid.WATER, supplies=supplies, may_cool=may_cool)


def test_the_heat_is_the_circuit_power_times_the_step_to_the_last_bit() -> None:
    """A booked power and a derived heat evaluated in another order would differ in the last bits and unbalance.

    Each step of the vectorised heat must be :func:`hisim.hydronics.circuit_power_w` times the step over J per kWh,
    bit for bit, a cooling step and an idle one included; 0.1 kg/s over 10 K for one hour is 4.18 kWh.
    """
    flows = np.array([0.4, 0.333, 0.0, 1.7, 0.1])
    supplies = np.array([35.0, 7.0, 55.0, 40.1, 60.0])
    returns = np.array([30.04, 12.3, 45.0, 40.1, 50.0])
    for seconds_per_timestep in (1.0, 60.0, 900.0, 3600.0):
        heat = CircuitColumns.heat_in_kilowatt_hours_per_step(
            mass_flow_in_kg_per_second=flows,
            supply_temperature_in_celsius=supplies,
            return_temperature_in_celsius=returns,
            seconds_per_timestep=seconds_per_timestep,
            specific_heat_in_joule_per_kilogram_kelvin=LoopFluid.WATER.specific_heat_j_per_kg_k,
        )
        for step, kilowatt_hours in enumerate(heat):
            power_in_watt = hydronics.circuit_power_w(
                mass_flow_kg_per_s=float(flows[step]), t_supply_c=float(supplies[step]), t_return_c=float(returns[step])
            )
            assert kilowatt_hours == power_in_watt * seconds_per_timestep / hydronics.UnitConversion.JOULES_PER_KILOWATT_HOUR
    assert heat[1] < 0.0 and heat[2] == 0.0
    assert heat[4] == pytest.approx(4.18, rel=1e-15)


def test_the_heat_takes_its_fluid_s_specific_heat() -> None:
    """A brine loop carries less heat per kelvin than a water loop of the same flow; the fluid's ``c`` decides."""
    heat = CircuitColumns.heat_in_kilowatt_hours_per_step(
        mass_flow_in_kg_per_second=np.array([0.5]),
        supply_temperature_in_celsius=np.array([3.0]),
        return_temperature_in_celsius=np.array([0.0]),
        seconds_per_timestep=3600.0,
        specific_heat_in_joule_per_kilogram_kelvin=LoopFluid.BRINE.specific_heat_j_per_kg_k,
    )
    assert heat[0] == pytest.approx(0.5 * hydronics.Brine.SPECIFIC_HEAT_J_PER_KG_K * 3.0 / 1000.0, rel=1e-15)
    assert LoopFluid.WATER.specific_heat_j_per_kg_k == hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K


@pytest.mark.parametrize(
    ("flows", "seconds_per_timestep", "error", "match"),
    [
        ([0.1, -0.1], 900.0, hydronics.NegativeMassFlowError, "in step 1"),
        ([0.1, 0.1], 0.0, hydronics.NonPositiveTimestepError, "seconds_per_timestep"),
    ],
)
def test_the_heat_refuses_what_the_library_refuses(
    flows: List[float], seconds_per_timestep: float, error: type, match: str
) -> None:
    """A balance that booked a negative flow or an empty step would hide the caller's error."""
    with pytest.raises(error, match=match):
        CircuitColumns.heat_in_kilowatt_hours_per_step(
            mass_flow_in_kg_per_second=np.array(flows),
            supply_temperature_in_celsius=np.array([60.0, 60.0]),
            return_temperature_in_celsius=np.array([50.0, 50.0]),
            seconds_per_timestep=seconds_per_timestep,
            specific_heat_in_joule_per_kilogram_kelvin=LoopFluid.WATER.specific_heat_j_per_kg_k,
        )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"name": ""}, "non-empty text"),
        ({"carrier": lt.EnergyBalanceCarrier.ELECTRICITY}, "space-heating or hot-water heat"),
        ({"carrier": "space_heating_heat"}, "space-heating or hot-water heat"),
        ({"fluid": "water"}, "LoopFluid"),
        ({"supplies": 1}, "supplies as True or False"),
        ({"may_cool": None}, "may_cool as True or False"),
        ({"pumps": "yes"}, "pumps as True, False or None"),
    ],
)
def test_a_loop_refuses_what_cannot_describe_a_loop(changes: Dict[str, Any], match: str) -> None:
    """A loop without a name, of a carrier no loop carries, of no known fluid or with a flag that is no bool is refused."""
    fields: Dict[str, Any] = {
        "name": "space_heating",
        "carrier": Loops.CARRIER,
        "fluid": LoopFluid.WATER,
        "supplies": True,
        "may_cool": False,
        "pumps": None,
    }
    fields.update(changes)
    with pytest.raises(FluidLoopError, match=match):
        FluidLoop(
            fields["name"],
            carrier=fields["carrier"],
            fluid=fields["fluid"],
            supplies=fields["supplies"],
            may_cool=fields["may_cool"],
            pumps=fields["pumps"],
        )


def test_a_loop_s_name_is_free_text_and_its_carriers_are_the_heats() -> None:
    """A component may name as many loops as its configuration has, "apartment 3" too; both heats are accepted."""
    for carrier in FluidLoop.LOOP_CARRIERS:
        loop = FluidLoop("apartment 3", carrier=carrier, fluid=LoopFluid.BRINE, supplies=False, may_cool=True)
        assert (loop.name, loop.carrier, loop.pumps) == ("apartment 3", carrier, None)
    loop = Loops.loop(supplies=True)
    assert [loop.flow_in, loop.flow_out, loop.temperature_in, loop.temperature_out] == [
        FluidLoopChannel(loop, quantity) for quantity in LoopQuantity
    ]


@pytest.mark.parametrize(
    ("quantity", "is_input", "unit"),
    [
        (LoopQuantity.FLOW_IN, True, lt.Units.KG_PER_SEC),
        (LoopQuantity.FLOW_OUT, False, lt.Units.KG_PER_SEC),
        (LoopQuantity.TEMPERATURE_IN, True, lt.Units.CELSIUS),
        (LoopQuantity.TEMPERATURE_OUT, False, lt.Units.CELSIUS),
    ],
)
def test_each_quantity_has_its_kind_and_unit(quantity: LoopQuantity, is_input: bool, unit: lt.Units) -> None:
    """The fluid arriving is read through an input, the fluid leaving published; a flow in kg/s, a temperature in °C."""
    assert (quantity.is_input, quantity.unit) == (is_input, unit)


@pytest.mark.parametrize(
    ("quantity", "unit", "is_input", "energy_port", "match"),
    [
        (LoopQuantity.FLOW_IN, lt.Units.CELSIUS, True, None, "is in °C; a flow_in is in kg/s"),
        (LoopQuantity.TEMPERATURE_OUT, lt.Units.WATT, False, None, "a temperature_out is in °C"),
        (LoopQuantity.FLOW_IN, lt.Units.KG_PER_SEC, False, None, "is an output; a flow_in is an input"),
        (LoopQuantity.TEMPERATURE_OUT, lt.Units.CELSIUS, True, None, "is an input; a temperature_out is an output"),
        (
            LoopQuantity.FLOW_OUT,
            lt.Units.KG_PER_SEC,
            False,
            EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER),
            "also declares an energy port",
        ),
    ],
)
def test_a_tag_that_does_not_fit_its_channel_is_refused(
    quantity: LoopQuantity, unit: lt.Units, is_input: bool, energy_port: Optional[EnergyPort], match: str
) -> None:
    """A tag of the wrong unit, of the wrong kind or beside an energy port would pair the wrong columns."""
    with pytest.raises(FluidLoopError, match=match):
        FluidLoopChannel(Loops.loop(supplies=True), quantity).check_fits(
            "Boiler", "Channel", unit, is_input=is_input, energy_port=energy_port
        )


class FakeComponent(cp.PhysicsComponent):
    """A physics component built without a configuration, to declare tagged channels on."""


def bare_component(name: str) -> Any:
    """A component with its name and no channels, as ``add_input`` and ``add_output`` need, without a configuration."""
    component: Any = object.__new__(FakeComponent)
    component.component_name = name
    component.inputs = []
    component.outputs = []
    component.config = SimpleNamespace(component_id=ComponentID(name))
    component.my_display_config = None
    return component


def test_a_component_tags_its_channels_and_groups_them_into_port_sets() -> None:
    """The boiler's three tagged channels are one port set; its untagged channels belong to none."""
    boiler = bare_component("Boiler")
    loop = Loops.loop(supplies=True)
    returned = boiler.add_input("Boiler", "Return", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, False, fluid_loop=loop.temperature_in)
    flow = boiler.add_output(
        "Boiler", "Flow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, output_description="flow", fluid_loop=loop.flow_out
    )
    supply = boiler.add_output(
        "Boiler", "Supply", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, output_description="supply", fluid_loop=loop.temperature_out
    )
    boiler.add_output("Boiler", "Power", lt.LoadTypes.HEATING, lt.Units.WATT, output_description="power")
    port_sets = boiler.port_sets()
    assert len(port_sets) == 1
    port_set = port_sets[0]
    assert (port_set.component_name, port_set.loop, port_set.label) == ("Boiler", loop, "Boiler.space_heating")
    assert port_set.channels == {
        LoopQuantity.TEMPERATURE_IN: returned,
        LoopQuantity.FLOW_OUT: flow,
        LoopQuantity.TEMPERATURE_OUT: supply,
    }
    assert port_set.flow_quantity is LoopQuantity.FLOW_OUT
    # the declaration refuses a tag the channel's unit or kind cannot carry
    with pytest.raises(FluidLoopError, match=r"Boiler.Power2 .* is in W"):
        boiler.add_output(
            "Boiler", "Power2", lt.LoadTypes.HEATING, lt.Units.WATT, output_description="power", fluid_loop=loop.flow_out
        )
    with pytest.raises(FluidLoopError, match=r"Boiler.Flow2 .* is an input; a flow_out is an output"):
        boiler.add_input("Boiler", "Flow2", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False, fluid_loop=loop.flow_out)


def test_two_channels_with_one_tag_or_two_loops_of_one_name_are_refused() -> None:
    """A loop has one channel per quantity, and a component names each of its loops once."""
    tank = bare_component("Tank")
    loop = Loops.loop(supplies=False)
    tank.add_input("Tank", "Flow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True, fluid_loop=loop.flow_in)
    with pytest.raises(FluidLoopError, match="Tank.Flow2 and Tank.Flow both carry the flow_in"):
        tank.add_input("Tank", "Flow2", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True, fluid_loop=loop.flow_in)
    with pytest.raises(FluidLoopError, match="names a loop 'space_heating' that differs"):
        tank.add_input(
            "Tank", "Supply", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True, fluid_loop=Loops.loop(supplies=True).temperature_in
        )
    # the same loop on another quantity, and another loop on the same quantity, are fine
    tank.add_input("Tank", "Supply", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True, fluid_loop=loop.temperature_in)
    tank.add_input(
        "Tank", "SolarFlow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False,
        fluid_loop=Loops.loop(supplies=False, name="solar").flow_in,
    )


@pytest.mark.parametrize(
    "quantities",
    [
        (LoopQuantity.FLOW_IN,),
        (LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN),
        (LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
        (LoopQuantity.FLOW_IN, LoopQuantity.FLOW_OUT, LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
    ],
)
def test_a_loop_tagged_by_half_is_refused_when_grouped(quantities: Tuple[LoopQuantity, ...]) -> None:
    """A port set has both temperatures and, until stage 1b, exactly one flow; anything else names the loop."""
    loop = Loops.loop(supplies=False)
    channels: List[Union[cp.ComponentInput, cp.ComponentOutput]] = []
    for quantity in quantities:
        tag = FluidLoopChannel(loop, quantity)
        if quantity.is_input:
            channels.append(cp.ComponentInput("Tank", quantity.value, lt.LoadTypes.ANY, quantity.unit, True, fluid_loop=tag))
        else:
            channels.append(
                cp.ComponentOutput("Tank", quantity.value, lt.LoadTypes.ANY, quantity.unit, component_id=ComponentID("Tank"), fluid_loop=tag)
            )
    with pytest.raises(FluidLoopError, match="Tank tags the loop 'space_heating' by half"):
        cp.FluidLoopPortSet.group("Tank", channels)


class End:

    """One member's end of a loop in a hand-built run: its loop and the name of the channel of each quantity."""

    def __init__(self, loop: FluidLoop, names: Dict[LoopQuantity, str]) -> None:
        """Hold the loop and the channel names."""
        self.loop = loop
        self.names = names

    def port_set(self, component_name: str) -> cp.FluidLoopPortSet:
        """The end as the port set of ``component_name``, each channel a stand-in with only its name."""
        channels: Dict[LoopQuantity, Any] = {
            quantity: SimpleNamespace(field_name=name) for quantity, name in self.names.items()
        }
        return cp.FluidLoopPortSet(component_name, self.loop, channels)


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
    #: The boiler's end: it supplies the loop, publishes the flow and the supply and reads the return.
    SUPPLY_END = End(
        Loops.loop(supplies=True),
        {LoopQuantity.FLOW_OUT: "Flow", LoopQuantity.TEMPERATURE_OUT: "Supply", LoopQuantity.TEMPERATURE_IN: "Return"},
    )
    #: The tank's end: it reads the flow and the supply and publishes the return.
    RECEIVING_END = End(
        Loops.loop(supplies=False),
        {LoopQuantity.FLOW_IN: "Flow", LoopQuantity.TEMPERATURE_IN: "Supply", LoopQuantity.TEMPERATURE_OUT: "Return"},
    )

    @staticmethod
    def wired(
        field: str, source: Optional[str], source_field: Optional[str], port: Optional[EnergyPort] = None
    ) -> SimpleNamespace:
        """An input of a stand-in, connected to ``source.source_field`` or, with None, to nothing; ``port`` its port."""
        return SimpleNamespace(field_name=field, src_object_name=source, src_field_name=source_field, energy_port=port)

    @classmethod
    def report(
        cls,
        columns: Sequence["CircuitRig.Column"],
        inputs: Dict[str, List[SimpleNamespace]],
        ends: Dict[str, List[End]],
    ) -> EnergyBalanceReport:
        """The balance report of a one-step run: ``columns`` as (component, output, unit, port, value)."""
        outputs = cls.outputs_of(columns)
        table = pd.DataFrame(
            [[value for *_, value in columns]],
            index=pd.date_range("2021-01-01", periods=1, freq=f"{cls.SECONDS_PER_TIMESTEP}s"),
        )
        stand_ins = cls.stand_ins(outputs, inputs, ends)
        wrapped: List[Any] = [SimpleNamespace(my_component=stand_in) for stand_in in stand_ins]
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
        outputs: Sequence[cp.ComponentOutput],
        inputs: Dict[str, List[SimpleNamespace]],
        ends: Dict[str, List[End]],
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
                port_sets=lambda name=name: tuple(end.port_set(name) for end in ends.get(name, [])),
            )
            for name in names
        ]

    @classmethod
    def boiler_columns(cls, flow_in_kg_per_second: float = 0.1) -> List["CircuitRig.Column"]:
        """The boiler's flow, supply and heat taken in; the heat is what the circuit carries at 0.1 kg/s."""
        return [
            ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, flow_in_kg_per_second),
            ("Boiler", "Supply", lt.Units.CELSIUS, None, 60.0),
            ("Boiler", "Heat", lt.Units.WATT, EnergyPort(lt.EnergyRole.CONSUMPTION, cls.AMBIENT), 4180.0),
        ]

    @classmethod
    def tank_columns(cls, name: str = "Tank") -> List["CircuitRig.Column"]:
        """A tank's return and its standby loss, which is what the circuit brings."""
        return [
            (name, "Return", lt.Units.CELSIUS, None, 50.0),
            (name, "Loss", lt.Units.WATT, EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER), 4180.0),
        ]

    @classmethod
    def tank_inputs(cls) -> List[SimpleNamespace]:
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


def test_a_step_in_which_the_supplier_cools_the_water_is_booked_as_cooling_toward_it() -> None:
    """§3.4: where the supplier's heat is negative, ``COOLING`` goes from the other member to the supplier.

    The boiler sends 50 °C water and gets 60 °C back: it removes 1.045 kWh from the water, which it gives to the
    ambient, and the tank puts that heat in (a negative loss). The circuit is one ``COOLING`` link from the tank to the
    boiler, and the heating face carries nothing. Stage 1a refuses no cooling step on a loop whose members do not all
    allow cooling (owner, 2026-10-27: the heating-only check comes with stage 2), so the run closes.
    """
    columns = [
        ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, 0.1),
        ("Boiler", "Supply", lt.Units.CELSIUS, None, 50.0),
        ("Boiler", "Heat", lt.Units.WATT, EnergyPort(lt.EnergyRole.PRODUCTION, CircuitRig.AMBIENT), 4180.0),
        ("Tank", "Return", lt.Units.CELSIUS, None, 60.0),
        ("Tank", "Loss", lt.Units.WATT, EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER), -4180.0),
    ]
    report = CircuitRig.report(
        columns,
        {"Boiler": [CircuitRig.wired("Return", "Tank", "Return")], "Tank": CircuitRig.tank_inputs()},
        {"Boiler": [CircuitRig.SUPPLY_END], "Tank": [CircuitRig.RECEIVING_END]},
    )
    circuit_ports = {
        (series.component, series.port.role, series.port.carrier): series.annual_kilowatt_hours
        for series in report.declared.all_series()
        if series.circuit is not None
    }
    cooling = lt.EnergyBalanceCarrier.COOLING
    assert circuit_ports == pytest.approx(
        {
            ("Boiler", lt.EnergyRole.PRODUCTION, Loops.CARRIER): 0.0,
            ("Boiler", lt.EnergyRole.CONSUMPTION, cooling): CircuitRig.HEAT_IN_KILOWATT_HOUR,
            ("Tank", lt.EnergyRole.CONSUMPTION, Loops.CARRIER): 0.0,
            ("Tank", lt.EnergyRole.PRODUCTION, cooling): CircuitRig.HEAT_IN_KILOWATT_HOUR,
        },
        rel=1e-12,
    )
    links = {(link.sender, link.receiver, link.carrier): link for link in report.links}
    assert links[("Tank", "Boiler", cooling.value)].closes
    assert not report.declared.circuit_faults and not report.fails
    report.conclude()


def test_the_supplier_s_sign_splits_a_circuit_into_its_two_directions() -> None:
    """Each end books heating where the heat is positive and cooling where it is negative; the two sum to the heat.

    A heating-only run keeps the one series it always had, so its report does not change.
    """
    heat = np.array([2.0, -1.0, 0.0])
    for supplies, heating_role, cooling_role in ((True, lt.EnergyRole.PRODUCTION, lt.EnergyRole.CONSUMPTION), (False, lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION)):
        (heating_port, heating), (cooling_port, cooling) = DeclaredPorts.book_by_the_supplier_s_sign(heat, Loops.CARRIER, supplies=supplies)
        assert heating_port == EnergyPort(heating_role, Loops.CARRIER)
        assert cooling_port == EnergyPort(cooling_role, lt.EnergyBalanceCarrier.COOLING)
        assert heating.tolist() == [2.0, 0.0, 0.0] and cooling.tolist() == [0.0, 1.0, 0.0]
        assert (heating - cooling).tolist() == heat.tolist()
    heating_only = np.array([2.0, 0.0])
    ((port, series),) = DeclaredPorts.book_by_the_supplier_s_sign(heating_only, Loops.CARRIER, supplies=True)
    assert port == EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER) and series is heating_only


def test_one_flow_read_by_two_ends_fails_the_run() -> None:
    """One reader: a second tank declaring the boiler's flow would book its heat twice; the run fails naming both."""
    columns = CircuitRig.boiler_columns() + CircuitRig.tank_columns() + CircuitRig.tank_columns("Twin")
    inputs = {
        "Boiler": [CircuitRig.wired("Return", "Tank", "Return")],
        "Tank": CircuitRig.tank_inputs(),
        "Twin": CircuitRig.tank_inputs(),
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
    with pytest.raises(EnergyBalanceError, match="circuit: Boiler tags its end"):
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


@pytest.mark.parametrize(("boiler_supplies", "tank_supplies", "how_many"), [(False, False, "no"), (True, True, "two")])
def test_a_loop_with_no_or_two_suppliers_is_refused_at_build(boiler_supplies: bool, tank_supplies: bool, how_many: str) -> None:
    """Rule 4 of §3.3: exactly one member supplies a loop; two receivers or two suppliers would book its heat twice."""
    ends = {
        "Boiler": [End(Loops.loop(supplies=boiler_supplies), CircuitRig.SUPPLY_END.names)],
        "Tank": [End(Loops.loop(supplies=tank_supplies), CircuitRig.RECEIVING_END.names)],
    }
    columns = CircuitRig.boiler_columns() + CircuitRig.tank_columns()
    inputs = {"Boiler": [CircuitRig.wired("Return", "Tank", "Return")], "Tank": CircuitRig.tank_inputs()}
    report = CircuitRig.report(columns, inputs, ends)
    assert any(f"has {how_many} supplier" in fault for fault in report.declared.circuit_faults)
    with pytest.raises(EnergyPortWiringError, match=f"Boiler.space_heating and Tank.space_heating has {how_many} supplier"):
        Wiring.refuse_a_broken_wiring(CircuitRig.stand_ins(CircuitRig.outputs_of(columns), inputs, ends))


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
        assert "temperature_in input Return is not connected" in report.declared.circuit_faults[0]


def heat_output(component: str, field: str, port: Optional[EnergyPort]) -> cp.ComponentOutput:
    """An output in W of a stand-in, with or without an energy port."""
    return cp.ComponentOutput(
        component, field, lt.LoadTypes.HEATING, lt.Units.WATT, energy_port=port, component_id=ComponentID(component)
    )


RECEIVED_HEAT = EnergyPort(lt.EnergyRole.CONSUMPTION, Loops.CARRIER)


@pytest.mark.parametrize(
    ("port", "tagged", "refusal"),
    [
        (EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER), (), r"is read by 2 components .* \(First, Second\)"),
        (EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER), ("First",), None),
        (EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER), ("First", "Second"), r"by 2 components \(First, Second\)"),
        (EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY), (), None),
    ],
)
def test_an_output_two_declared_components_read_needs_exactly_one_tagged_reader(
    port: EnergyPort, tagged: Tuple[str, ...], refusal: Optional[str]
) -> None:
    """Rule (a) of stage E (owner, 2026-10-02) with input ports (D9): exactly one reader tags its reading input.

    A heat port read by two components that declare ports is refused unless exactly one of them reads it through an
    input carrying a port it pairs with; two such inputs are refused too, one energy being received once. A metered
    port is exempt from the rule: its meter comes first (owner decision Q-J, spec §4.3).
    """
    outputs = [
        heat_output("Source", "Heat", port),
        heat_output("First", "Loss", EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER)),
        heat_output("Second", "Loss", EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER)),
    ]
    inputs = {
        name: [CircuitRig.wired("HeatIn", "Source", "Heat", RECEIVED_HEAT if name in tagged else None)]
        for name in ("First", "Second")
    }
    components = CircuitRig.stand_ins(outputs, inputs, {})
    if refusal is None:
        Wiring.refuse_a_broken_wiring(components)
        return
    with pytest.raises(EnergyPortWiringError, match=r"Source.Heat .* " + refusal):
        Wiring.refuse_a_broken_wiring(components)
