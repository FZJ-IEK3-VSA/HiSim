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
    LoopColumns,
    DeclaredPorts,
    EnergyBalanceReport,
    PeerKind,
)
from hisim.postprocessing.energy_balance.sankey import SankeyBuilder
from hisim.setup_rules.energy_ports import EnergyPortWiringError
from hisim.setup_rules.results import EnergyBalanceError
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class Loops:

    """The fluid loops the tests of this module tag their channels with."""

    #: The heat the loops carry.
    CARRIER = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT

    @classmethod
    def loop(cls, *, supplies: bool, pumps: bool, name: str = "space_heating", may_cool: bool = False) -> FluidLoop:
        """A water loop of space-heating heat, as a member that supplies it or not, and pumps it or not, states it."""
        return FluidLoop(
            name, carrier=cls.CARRIER, fluid=LoopFluid.WATER, supplies=supplies, may_cool=may_cool, pumps=pumps
        )


def test_the_heat_is_the_circuit_power_times_the_step_to_the_last_bit() -> None:
    """A booked power and a derived heat evaluated in another order would differ in the last bits and unbalance.

    Each step of the vectorised heat must be :func:`hisim.hydronics.circuit_power_w` times the step over J per kWh,
    bit for bit, a cooling step and an idle one included; 0.1 kg/s over 10 K for one hour is 4.18 kWh.
    """
    flows = np.array([0.4, 0.333, 0.0, 1.7, 0.1])
    supplies = np.array([35.0, 7.0, 55.0, 40.1, 60.0])
    returns = np.array([30.04, 12.3, 45.0, 40.1, 50.0])
    for seconds_per_timestep in (1.0, 60.0, 900.0, 3600.0):
        heat = LoopColumns.heat_in_kilowatt_hours_per_step(
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
    heat = LoopColumns.heat_in_kilowatt_hours_per_step(
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
        LoopColumns.heat_in_kilowatt_hours_per_step(
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
        ({"carrier": lt.EnergyBalanceCarrier.ELECTRICITY}, "space-heating, hot-water or ambient heat"),
        ({"carrier": "space_heating_heat"}, "space-heating, hot-water or ambient heat"),
        ({"fluid": "water"}, "LoopFluid"),
        ({"supplies": 1}, "supplies as True or False"),
        ({"may_cool": None}, "may_cool as True or False"),
        ({"pumps": "yes"}, "pumps as True or False"),
        ({"pumps": None}, "pumps as True or False"),
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
        "pumps": True,
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
    """A component may name as many loops as its configuration has, "apartment 3" too; each loop carrier is accepted."""
    for carrier in FluidLoop.LOOP_CARRIERS:
        loop = FluidLoop("apartment 3", carrier=carrier, fluid=LoopFluid.BRINE, supplies=False, may_cool=True, pumps=False)
        assert (loop.name, loop.carrier, loop.pumps) == ("apartment 3", carrier, False)
    loop = Loops.loop(supplies=True, pumps=True)
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
        FluidLoopChannel(Loops.loop(supplies=True, pumps=True), quantity).check_fits(
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
    """The boiler's four tagged channels are one port set; its untagged channels belong to none."""
    boiler = bare_component("Boiler")
    loop = Loops.loop(supplies=True, pumps=True)
    returned = boiler.add_input("Boiler", "Return", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, False, fluid_loop=loop.temperature_in)
    returning_flow = boiler.add_input(
        "Boiler", "FlowBack", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False, fluid_loop=loop.flow_in
    )
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
        LoopQuantity.FLOW_IN: returning_flow,
        LoopQuantity.FLOW_OUT: flow,
        LoopQuantity.TEMPERATURE_OUT: supply,
    }
    # the pump owner's own flow out is the loop's flow, a pass-through member's flow in
    assert port_set.pumped_flow_quantity is LoopQuantity.FLOW_OUT
    assert cp.FluidLoopPortSet("Tank", Loops.loop(supplies=False, pumps=False), {}).pumped_flow_quantity is LoopQuantity.FLOW_IN
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
    loop = Loops.loop(supplies=False, pumps=False)
    tank.add_input("Tank", "Flow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True, fluid_loop=loop.flow_in)
    with pytest.raises(FluidLoopError, match="Tank.Flow2 and Tank.Flow both carry the flow_in"):
        tank.add_input("Tank", "Flow2", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, True, fluid_loop=loop.flow_in)
    with pytest.raises(FluidLoopError, match="names a loop 'space_heating' that differs"):
        tank.add_input(
            "Tank", "Supply", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True, fluid_loop=Loops.loop(supplies=True, pumps=False).temperature_in
        )
    # the same loop on another quantity, and another loop on the same quantity, are fine
    tank.add_input("Tank", "Supply", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True, fluid_loop=loop.temperature_in)
    tank.add_input(
        "Tank", "SolarFlow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False,
        fluid_loop=Loops.loop(supplies=False, pumps=False, name="solar").flow_in,
    )


@pytest.mark.parametrize(
    "quantities",
    [
        (LoopQuantity.FLOW_IN,),
        (LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN),
        (LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
        (LoopQuantity.FLOW_OUT, LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
        (LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
    ],
)
def test_a_loop_tagged_by_half_is_refused_when_grouped(quantities: Tuple[LoopQuantity, ...]) -> None:
    """Rule 0 of §3.3: a port set has all four quantities; one with one to three names the component, loop and gap.

    The three-channel sets of stage 1a, one flow beside both temperatures, are among them.
    """
    loop = Loops.loop(supplies=False, pumps=False)
    channels: List[Union[cp.ComponentInput, cp.ComponentOutput]] = []
    for quantity in quantities:
        tag = FluidLoopChannel(loop, quantity)
        if quantity.is_input:
            channels.append(cp.ComponentInput("Tank", quantity.value, lt.LoadTypes.ANY, quantity.unit, True, fluid_loop=tag))
        else:
            channels.append(
                cp.ComponentOutput("Tank", quantity.value, lt.LoadTypes.ANY, quantity.unit, component_id=ComponentID("Tank"), fluid_loop=tag)
            )
    missing = [quantity.value for quantity in LoopQuantity if quantity not in quantities]
    with pytest.raises(FluidLoopError, match=f"Tank tags the loop 'space_heating' by half: .* lacks {', '.join(missing)};"):
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


class LoopRig:

    """Hand-built runs of one boiler charging one tank through one loop, and variations that break a rule.

    The boiler owns the loop's flow and supply (``Flow``, ``Supply``) and reads the tank's return (input
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
    #: The heat the loop carries in one step, in kWh.
    HEAT_IN_KILOWATT_HOUR: float = 0.1 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * 10.0 * 900 / 3.6e6
    #: The boiler's end: it supplies the loop, publishes the flow and the supply and reads the return.
    SUPPLY_END = End(
        Loops.loop(supplies=True, pumps=True),
        {LoopQuantity.FLOW_OUT: "Flow", LoopQuantity.TEMPERATURE_OUT: "Supply", LoopQuantity.TEMPERATURE_IN: "Return"},
    )
    #: The tank's end: it reads the flow and the supply and publishes the return.
    RECEIVING_END = End(
        Loops.loop(supplies=False, pumps=False),
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
        columns: Sequence["LoopRig.Column"],
        inputs: Dict[str, List[SimpleNamespace]],
        ends: Dict[str, List[End]],
    ) -> EnergyBalanceReport:
        """The balance report of a one-step run: ``columns`` as (component, output, unit, port, value)."""
        outputs = cls.outputs_of(columns)
        table = pd.DataFrame(
            [[value for *_, value in columns]],
            index=pd.date_range("2021-01-01", periods=1, freq=f"{cls.SECONDS_PER_TIMESTEP}s"),
        )
        return EnergyBalanceReport(table, outputs, cls.stand_ins(outputs, inputs, ends), cls.SECONDS_PER_TIMESTEP)

    @staticmethod
    def outputs_of(columns: Sequence["LoopRig.Column"]) -> List[cp.ComponentOutput]:
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
        """One stand-in per component of ``outputs``, in their order, carrying its wiring, outputs and loop ends."""
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
    def boiler_columns(cls, flow_in_kg_per_second: float = 0.1) -> List["LoopRig.Column"]:
        """The boiler's flow, supply and heat taken in; the heat is what the loop carries at 0.1 kg/s."""
        return [
            ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, flow_in_kg_per_second),
            ("Boiler", "Supply", lt.Units.CELSIUS, None, 60.0),
            ("Boiler", "Heat", lt.Units.WATT, EnergyPort(lt.EnergyRole.CONSUMPTION, cls.AMBIENT), 4180.0),
        ]

    @classmethod
    def tank_columns(cls, name: str = "Tank") -> List["LoopRig.Column"]:
        """A tank's return and its standby loss, which is what the loop brings."""
        return [
            (name, "Return", lt.Units.CELSIUS, None, 50.0),
            (name, "Loss", lt.Units.WATT, EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER), 4180.0),
        ]

    @classmethod
    def tank_inputs(cls) -> List[SimpleNamespace]:
        """A tank's flow and supply inputs, connected to the boiler."""
        return [cls.wired("Flow", "Boiler", "Flow"), cls.wired("Supply", "Boiler", "Supply")]

    @classmethod
    def cooling(cls, return_temperature_in_celsius: float, *, may_cool: bool) -> EnergyBalanceReport:
        """The boiler and the tank, the tank's return at ``return_temperature_in_celsius`` against the 60 °C supply.

        A return above the supply is a step in which the boiler removes heat from the water. The boiler takes in
        ``m c (T_sup - T_ret)`` of ambient heat, negative while it cools, and the tank loses as much, so both close in
        either direction; both ends say ``may_cool`` as given.
        """
        heat_in_watt = 0.1 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K * (60.0 - return_temperature_in_celsius)
        columns: List[LoopRig.Column] = [
            ("Boiler", "Flow", lt.Units.KG_PER_SEC, None, 0.1),
            ("Boiler", "Supply", lt.Units.CELSIUS, None, 60.0),
            ("Boiler", "Heat", lt.Units.WATT, EnergyPort(lt.EnergyRole.CONSUMPTION, cls.AMBIENT), heat_in_watt),
            ("Tank", "Return", lt.Units.CELSIUS, None, return_temperature_in_celsius),
            ("Tank", "Loss", lt.Units.WATT, EnergyPort(lt.EnergyRole.LOSS, Loops.CARRIER), heat_in_watt),
        ]
        supply_end = End(Loops.loop(supplies=True, pumps=True, may_cool=may_cool), cls.SUPPLY_END.names)
        receiving_end = End(Loops.loop(supplies=False, pumps=False, may_cool=may_cool), cls.RECEIVING_END.names)
        return cls.report(
            columns,
            {"Boiler": [cls.wired("Return", "Tank", "Return")], "Tank": cls.tank_inputs()},
            {"Boiler": [supply_end], "Tank": [receiving_end]},
        )

    @classmethod
    def paired(cls) -> EnergyBalanceReport:
        """The boiler and the tank, both declaring their ends of the loop."""
        return cls.report(
            cls.boiler_columns() + cls.tank_columns(),
            {"Boiler": [cls.wired("Return", "Tank", "Return")], "Tank": cls.tank_inputs()},
            {"Boiler": [cls.SUPPLY_END], "Tank": [cls.RECEIVING_END]},
        )


def test_both_ends_of_a_loop_share_one_heat_and_pair_through_the_wiring() -> None:
    """The reader of the flow is the owner's other end and the owner the reader's; one heat, one closing link.

    Fails if the two ends derived their heat apart, named no peer, or booked a link that does not close.
    """
    report = LoopRig.paired()
    (supply,) = report.declared.by_component["Boiler"][1:]
    (receiver,) = [series for series in report.declared.by_component["Tank"] if series.loop_columns is not None]
    assert supply.kilowatt_hours is receiver.kilowatt_hours
    assert supply.annual_kilowatt_hours == pytest.approx(LoopRig.HEAT_IN_KILOWATT_HOUR, rel=1e-12)
    assert (supply.peer.kind, supply.peer.name) == (PeerKind.COMPONENT, "Tank")
    assert (receiver.peer.kind, receiver.peer.name) == (PeerKind.COMPONENT, "Boiler")
    assert supply.loop_columns == receiver.loop_columns and supply.loop_columns is not None
    assert supply.loop_columns.as_dict() == {
        "mass_flow": "Boiler.Flow",
        "supply_temperature": "Boiler.Supply",
        "return_temperature": "Tank.Return",
    }
    assert not report.declared.loop_faults and not report.fails
    (link,) = report.links
    assert (link.sender, link.receiver, link.closes) == ("Boiler", "Tank", True)
    fft.AfterRunRules.refuse(report)
    entry = report.report()
    tank_ports = [port for item in entry["components"] if item["component"] == "Tank" for port in item["ports"]]
    assert {"output": "loop Boiler.Flow", "unit": "kWh"}.items() <= tank_ports[-1].items()
    # the Sankey draws the loop once, at the receiver, from the boiler
    links = SankeyBuilder(report.declared, report.balances, (), report.links).build()
    assert [(link.source, link.target) for link in links if link.label.endswith("loop Boiler.Flow")] == [
        ("Boiler", "Tank")
    ]


def test_a_step_in_which_the_supplier_cools_the_water_is_booked_as_cooling_toward_it() -> None:
    """§3.4: where the supplier's heat is negative, ``COOLING`` goes from the other member to the supplier.

    The boiler sends 60 °C water and gets 70 °C back: it removes 1.045 kWh from the water, which it gives to the
    ambient, and the tank puts that heat in (a negative loss). The loop is one ``COOLING`` link from the tank to the
    boiler, and the heating face carries nothing. Both members say ``may_cool``, so the run closes; on a loop that
    heats only the same step is the fault ``AR-11`` (``tests/rules/test_results.py``).
    """
    report = LoopRig.cooling(70.0, may_cool=True)
    loop_ports = {
        (series.component, series.port.role, series.port.carrier): series.annual_kilowatt_hours
        for series in report.declared.all_series()
        if series.loop_columns is not None
    }
    cooling = lt.EnergyBalanceCarrier.COOLING
    assert loop_ports == pytest.approx(
        {
            ("Boiler", lt.EnergyRole.PRODUCTION, Loops.CARRIER): 0.0,
            ("Boiler", lt.EnergyRole.CONSUMPTION, cooling): LoopRig.HEAT_IN_KILOWATT_HOUR,
            ("Tank", lt.EnergyRole.CONSUMPTION, Loops.CARRIER): 0.0,
            ("Tank", lt.EnergyRole.PRODUCTION, cooling): LoopRig.HEAT_IN_KILOWATT_HOUR,
        },
        rel=1e-12,
    )
    links = {(link.sender, link.receiver, link.carrier): link for link in report.links}
    assert links[("Tank", "Boiler", cooling.value)].closes
    assert not report.declared.loop_faults and not report.fails
    fft.AfterRunRules.refuse(report)


def test_the_supplier_s_sign_splits_a_loop_into_its_two_directions() -> None:
    """Each end books heating where the heat is positive and cooling where it is negative; the two sum to the heat.

    A heating-only run keeps the one series it always had, so its report does not change.
    """
    heat = np.array([2.0, -1.0, 0.0])
    for supplies, heating_role, cooling_role in (
        (True, lt.EnergyRole.PRODUCTION, lt.EnergyRole.CONSUMPTION),
        (False, lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION),
    ):
        (heating_port, heating), (cooling_port, cooling) = DeclaredPorts.book_by_the_supplier_s_sign(
            heat, Loops.CARRIER, supplies=supplies, noise_in_kilowatt_hours=0.001
        )
        assert heating_port == EnergyPort(heating_role, Loops.CARRIER)
        assert cooling_port == EnergyPort(cooling_role, lt.EnergyBalanceCarrier.COOLING)
        assert heating.tolist() == [2.0, 0.0, 0.0] and cooling.tolist() == [0.0, 1.0, 0.0]
        assert (heating - cooling).tolist() == heat.tolist()
    heating_only = np.array([2.0, 0.0])
    ((port, series),) = DeclaredPorts.book_by_the_supplier_s_sign(heating_only, Loops.CARRIER, supplies=True, noise_in_kilowatt_hours=0.001)
    assert port == EnergyPort(lt.EnergyRole.PRODUCTION, Loops.CARRIER) and series is heating_only


def test_a_negative_heat_within_the_noise_stays_on_the_carrier_s_face() -> None:
    """Catches convergence noise drawn as a cooling flow (hisim-false-cooling-edge-7130): -1e-8 kWh is no cooling step.

    The hot-water loop of the heat-pump twin showed 1.2e-8 kWh of ``COOLING`` in a July week, a supply 4e-8 K below
    the tank's step mean. Within the noise the step keeps its sign on the loop's carrier, so both ends' balances keep
    it; at the noise exactly it is still noise, beyond it cooling.
    """
    heat = np.array([2.0, -1e-8, -0.001])
    ((port, series),) = DeclaredPorts.book_by_the_supplier_s_sign(heat, Loops.CARRIER, supplies=False, noise_in_kilowatt_hours=0.001)
    assert port == EnergyPort(lt.EnergyRole.CONSUMPTION, Loops.CARRIER) and series is heat
    (_, heating), (cooling_port, cooling) = DeclaredPorts.book_by_the_supplier_s_sign(
        np.array([2.0, -1e-8, -0.0011]), Loops.CARRIER, supplies=True, noise_in_kilowatt_hours=0.001
    )
    assert cooling_port == EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.COOLING)
    assert heating.tolist() == [2.0, -1e-8, 0.0] and cooling.tolist() == [0.0, 0.0, 0.0011]


def test_the_noise_of_a_step_is_one_watt_over_the_step() -> None:
    """Catches a noise bound that does not scale with the step: 1 W over 900 s, 0.00025 kWh, is noise; 1.01 W is not.

    A heating-only rig whose return is above the supply by 1.01 W books a cooling step and records it at the boiler,
    one by 0.99 W books the negative heat on the carrier and records nothing.
    """
    for excess_in_watt, cools in ((0.99, False), (1.01, True)):
        report = LoopRig.cooling(60.0 + excess_in_watt / (0.1 * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K), may_cool=False)
        carriers = {series.port.carrier for series in report.declared.by_component["Boiler"] if series.loop_columns is not None}
        assert (lt.EnergyBalanceCarrier.COOLING in carriers) is cools
        assert bool(report.declared.cooling_on_heating_only_loops) is cools
        assert all(balance.closes for balance in report.balances)


def test_cooling_beyond_the_noise_names_the_first_step_the_count_and_the_largest_heat() -> None:
    """Catches a breach that reports the wrong step, count or power, or one reported for noise alone."""
    supplier = LoopRig.SUPPLY_END.port_set("Boiler")
    breach = DeclaredPorts.cooling_beyond_the_noise(
        np.array([1.0, -0.0001, -0.5, -0.25]), 0.00025, 900.0, supplier=supplier, heating_only_members=("Tank.space_heating",), flow="Flow"
    )
    assert breach is not None
    assert (breach.supplier, breach.loop, breach.flow, breach.first_step, breach.steps) == ("Boiler", "Boiler.space_heating", "Flow", 2, 2)
    assert breach.largest_heat_removed_in_watt == pytest.approx(2000.0, rel=1e-12)
    assert breach.describe("2021-01-01T00:30:00") == (
        "the loop Boiler.space_heating heats only (Tank.space_heating says may_cool=False), and its supplier Boiler "
        "removed heat from the water in 2 steps, first in step 2 (2021-01-01T00:30:00), up to 2,000 W"
    )
    assert DeclaredPorts.cooling_beyond_the_noise(
        np.array([1.0, -0.00025]), 0.00025, 900.0, supplier=supplier, heating_only_members=("Tank.space_heating",), flow="Flow"
    ) is None


def test_the_heating_only_members_of_a_loop_are_its_ends_that_say_may_cool_false() -> None:
    """Catches a loop judged by one member only: a loop may cool only if every member allows it (D19)."""
    columns = LoopColumns(("Boiler", "Flow"), ("Boiler", "Supply"), ("Tank", "Return"))
    swapped = LoopColumns(("Boiler", "Flow"), ("Tank", "Return"), ("Boiler", "Supply"))
    cooling_end = Loops.loop(supplies=True, pumps=True, may_cool=True)
    heating_end = Loops.loop(supplies=False, pumps=False, may_cool=False, name="generator")
    wiring = SimpleNamespace(
        ends=[
            ("Boiler", cp.FluidLoopPortSet("Boiler", cooling_end, {}), columns, "Tank"),
            ("Tank", cp.FluidLoopPortSet("Tank", heating_end, {}), swapped, "Boiler"),
        ]
    )
    assert columns.key == swapped.key
    assert DeclaredPorts.heating_only_members(wiring) == {columns.key: ("Tank.generator",)}  # type: ignore[arg-type]
    wiring.ends[1] = ("Tank", cp.FluidLoopPortSet("Tank", Loops.loop(supplies=False, pumps=False, may_cool=True), {}), swapped, "Boiler")
    assert not DeclaredPorts.heating_only_members(wiring)  # type: ignore[arg-type]


def test_the_step_time_is_the_index_s_stamp_or_none() -> None:
    """Catches a fault naming the wrong time, or one that fails on a step the index does not have."""
    declared = DeclaredPorts(index=pd.date_range("2021-01-01", periods=5, freq="900s"))
    assert declared.step_time(4) == "2021-01-01T01:00:00"
    assert declared.step_time(5) is None and DeclaredPorts().step_time(0) is None


def test_one_flow_read_by_two_ends_fails_the_run() -> None:
    """One reader: a second tank declaring the boiler's flow would book its heat twice; the run fails naming both."""
    columns = LoopRig.boiler_columns() + LoopRig.tank_columns() + LoopRig.tank_columns("Twin")
    inputs = {
        "Boiler": [LoopRig.wired("Return", "Tank", "Return")],
        "Tank": LoopRig.tank_inputs(),
        "Twin": LoopRig.tank_inputs(),
    }
    ends = {"Boiler": [LoopRig.SUPPLY_END], "Tank": [LoopRig.RECEIVING_END], "Twin": [LoopRig.RECEIVING_END]}
    report = LoopRig.report(columns, inputs, ends)
    faults = report.declared.loop_faults
    assert any("Boiler.Flow is read by 2 components" in fault and "Twin" in fault for fault in faults)
    with pytest.raises(EnergyBalanceError, match="one flow delivers its heat once"):
        fft.AfterRunRules.refuse(report)
    # the wiring alone shows it: the run is refused before it simulates
    components = LoopRig.stand_ins(LoopRig.outputs_of(columns), inputs, ends)
    with pytest.raises(EnergyPortWiringError, match="Boiler.Flow is read by 2 components"):
        fft.EnergyPortRules.refuse_a_broken_wiring(components)


def test_an_other_end_that_declares_ports_but_not_the_loop_fails_the_run() -> None:
    """Both ends or neither: a tank that declares its loss but not its end of the boiler's loop is a fault."""
    report = LoopRig.report(
        LoopRig.boiler_columns() + LoopRig.tank_columns(),
        {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()},
        {"Boiler": [LoopRig.SUPPLY_END]},
    )
    assert any("its other end, Tank, declares ports and not this loop" in f for f in report.declared.loop_faults)
    with pytest.raises(EnergyBalanceError, match="loop: Boiler tags its end"):
        fft.AfterRunRules.refuse(report)


def test_an_other_end_that_declares_no_ports_is_drawn_as_undeclared() -> None:
    """A tank that declares nothing is a coverage gap, not a fault: the loop goes to "undeclared: Tank"."""
    columns = LoopRig.boiler_columns() + [("Tank", "Return", lt.Units.CELSIUS, None, 50.0)]
    report = LoopRig.report(
        columns,
        {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()},
        {"Boiler": [LoopRig.SUPPLY_END]},
    )
    assert not report.declared.loop_faults and report.declared.undeclared == ["Tank"]
    fft.AfterRunRules.refuse(report)
    links = SankeyBuilder(report.declared, report.balances, (), report.links).build()
    assert ("Boiler", "undeclared: Tank") in {(link.source, link.target) for link in links}


@pytest.mark.parametrize(("boiler_supplies", "tank_supplies", "how_many"), [(False, False, "no"), (True, True, "two")])
def test_a_loop_with_no_or_two_suppliers_is_refused_at_build(boiler_supplies: bool, tank_supplies: bool, how_many: str) -> None:
    """Rule 4 of §3.3: exactly one member supplies a loop; two receivers or two suppliers would book its heat twice."""
    ends = {
        "Boiler": [End(Loops.loop(supplies=boiler_supplies, pumps=True), LoopRig.SUPPLY_END.names)],
        "Tank": [End(Loops.loop(supplies=tank_supplies, pumps=False), LoopRig.RECEIVING_END.names)],
    }
    columns = LoopRig.boiler_columns() + LoopRig.tank_columns()
    inputs = {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()}
    report = LoopRig.report(columns, inputs, ends)
    assert any(f"has {how_many} supplier" in fault for fault in report.declared.loop_faults)
    with pytest.raises(EnergyPortWiringError, match=f"Boiler.space_heating and Tank.space_heating has {how_many} supplier"):
        fft.EnergyPortRules.refuse_a_broken_wiring(LoopRig.stand_ins(LoopRig.outputs_of(columns), inputs, ends))


def test_an_end_whose_flow_input_is_not_connected_is_idle() -> None:
    """A tank's loop to a second generator the house does not have moves no water: it is left out, no fault."""
    report = LoopRig.report(
        LoopRig.tank_columns(),
        {"Tank": [LoopRig.wired("Flow", None, None), LoopRig.wired("Supply", None, None)]},
        {"Tank": [LoopRig.RECEIVING_END]},
    )
    assert not report.declared.loop_faults
    assert [series.port_name for series in report.declared.by_component["Tank"]] == ["Loss"]


@pytest.mark.parametrize(("flow_in_kg_per_second", "faults"), [(0.1, 1), (0.0, 0)])
def test_an_end_that_pumps_water_it_cannot_follow_fails_the_run(flow_in_kg_per_second: float, faults: int) -> None:
    """A boiler pumping into a loop whose return it cannot read loses that heat from view; idle, it is left out."""
    columns = LoopRig.boiler_columns(flow_in_kg_per_second)
    report = LoopRig.report(
        columns, {"Boiler": [LoopRig.wired("Return", None, None)]}, {"Boiler": [LoopRig.SUPPLY_END]}
    )
    assert len(report.declared.loop_faults) == faults
    if faults:
        assert "temperature_in input Return is not connected" in report.declared.loop_faults[0]


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
        name: [LoopRig.wired("HeatIn", "Source", "Heat", RECEIVED_HEAT if name in tagged else None)]
        for name in ("First", "Second")
    }
    components = LoopRig.stand_ins(outputs, inputs, {})
    if refusal is None:
        fft.EnergyPortRules.refuse_a_broken_wiring(components)
        return
    with pytest.raises(EnergyPortWiringError, match=r"Source.Heat .* " + refusal):
        fft.EnergyPortRules.refuse_a_broken_wiring(components)
