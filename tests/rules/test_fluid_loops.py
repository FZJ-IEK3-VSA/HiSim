"""Direct tests of the fluid-loop rules: each rule on a hand-built system with its fault and on one without.

The systems are stand-in members of loops, each a physics component that tags the four channels of one loop, wired by
hand as the tests of the loop walk wire them (``tests/test_fluid_loops.py``, which proves that the walk's refusals kept
their messages when they moved here). Beside the rules, the tests cover what the family shares (the walk it reads,
the helpers of a partly connected port set, the error it raises), the grouping of a component's channels the first
rule reads, the walk's discovery of closed loops, and the requirements that keep the energy-port rules off a wiring
that is no loop.
"""

from typing import Any, List, Optional, Sequence, Tuple

import pytest

from hisim import loadtypes as lt
from hisim.component import FluidLoopPortSet
from hisim.energy_port import FluidLoop, FluidLoopError, LoopFluid, LoopQuantity
from hisim.fluid_loops import FluidLoopWalk
from hisim.setup_rules.energy_ports import EnergyPortRule
from hisim.setup_rules.fluid_loops import (
    FarEndTagged,
    FlowReadOnce,
    FluidLoopRule,
    FluidLoopWiringError,
    LoopCloses,
    LoopTaggedWhole,
    OneCarrierOneFluid,
    OnePumpOwner,
    TemperatureFollowsFlow,
    WiredWhole,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, SetupRuleViolation
from hisim.setup_rules.subjects import BuiltSystem
from tests import functions_for_testing as fft
from tests.test_fluid_loops import Member

pytestmark = pytest.mark.base


class Rig:
    """Hand-built systems of stand-in members for the rules of the family."""

    @staticmethod
    def system(components: Sequence[Any]) -> BuiltSystem:
        """The built system of the stand-ins."""
        return BuiltSystem(components=tuple(components))

    @staticmethod
    def pair(*, generator_pumps: bool = True, tank_pumps: bool = False) -> BuiltSystem:
        """A boiler that pumps its loop to a tank that passes it back, wired both ways: one closed loop."""
        return Rig.system(Member.generator_and_tank(generator_pumps=generator_pumps, tank_pumps=tank_pumps))

    @staticmethod
    def half_tagged_tank() -> Any:
        """A tank that tags its loop's flow in and both temperatures, but no flow out."""
        loop = FluidLoop("generator", carrier=Member.CARRIER, fluid=LoopFluid.WATER, supplies=False, may_cool=False, pumps=False)
        return Member.tag(
            Member.component("Tank"), loop, quantities=(LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT)
        )

    @staticmethod
    def open_pair() -> BuiltSystem:
        """A boiler whose flow and temperature a tank reads, and whose temperature in reads the tank: the flow never returns."""
        boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
        tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False)
        Member.link(boiler, tank)
        Member.wire(boiler, "TemperatureIn", tank, "TemperatureOut")
        return Rig.system([boiler, tank])

    @staticmethod
    def flows_only() -> BuiltSystem:
        """A boiler and a tank whose flows go round but whose return temperature does not: both wired by half."""
        boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
        tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False)
        Member.link(boiler, tank)
        Member.wire(boiler, "FlowIn", tank, "FlowOut")
        return Rig.system([boiler, tank])

    @staticmethod
    def split() -> BuiltSystem:
        """The closed pair, and a second tank that reads the boiler's flow and sends nothing back."""
        boiler, tank = Member.generator_and_tank()
        twin = Member.of("Twin", loop_name="generator", supplies=False, pumps=False)
        Member.link(boiler, twin)
        return Rig.system([boiler, tank, twin])

    @staticmethod
    def metered(*, fed: bool) -> BuiltSystem:
        """The closed pair with an untagged meter reading the boiler's flow out, and with ``fed`` feeding the tank's flow in."""
        boiler, tank = Member.generator_and_tank()
        meter = Member.component("Meter")
        meter.add_input("Meter", "Flow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False)
        meter.add_output("Meter", "FlowCopy", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, output_description="copy")
        Member.wire(meter, "Flow", boiler, "FlowOut")
        if fed:
            Member.wire(tank, "FlowIn", meter, "FlowCopy")
        return Rig.system([boiler, tank, meter])

    @staticmethod
    def crossed_temperatures() -> BuiltSystem:
        """Two boilers charging two tanks, the tanks' supply temperatures read from the other boiler."""
        boiler, tank = Member.generator_and_tank()
        other = Member.of("Other", loop_name="space_heating", supplies=True, pumps=True)
        other_tank = Member.of("OtherTank", loop_name="generator", supplies=False, pumps=False)
        Member.link(other, other_tank)
        Member.link(other_tank, other)
        Member.wire(tank, "TemperatureIn", other, "TemperatureOut")
        Member.wire(other_tank, "TemperatureIn", boiler, "TemperatureOut")
        return Rig.system([boiler, tank, other, other_tank])

    @staticmethod
    def disagreeing(**changes: Any) -> BuiltSystem:
        """A closed pair whose tank states the loop with ``changes`` (another carrier or fluid)."""
        boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
        tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False, **changes)
        Member.link(boiler, tank)
        Member.link(tank, boiler)
        return Rig.system([boiler, tank])


def codes_and_places(faults: Sequence[Any]) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """The code, component and port of each fault, sorted as the registry sorts them."""
    return sorted((fault.code, fault.component, fault.port) for fault in faults)


class TestTheFamily:
    """What every rule of the family shares: the walk, the quantities, the partly connected port set, the error."""

    def test_the_walk_of_a_system_holds_its_port_sets_and_their_wiring(self) -> None:
        """Catches a rule reading a walk that misses a member or the wire between two."""
        walk = FluidLoopRule.walk_of(Rig.pair())

        assert [port_set.label for port_set in walk.port_sets] == ["Boiler.space_heating", "Tank.generator"]
        assert walk.sources[("Tank", "FlowIn")] == ("Boiler", "FlowOut")

    def test_the_loop_quantities_are_the_energy_port_module_s(self) -> None:
        """Catches a rule comparing tags against another enum than the one the channels carry."""
        assert FluidLoopRule.loop_quantity() is LoopQuantity

    def test_a_partly_connected_port_set_shows_which_channels_are_connected(self) -> None:
        """Catches the two rules of a partly connected set judging a set wired whole, an idle one or one read twice."""
        open_pair = Rig.open_pair()
        walk = FluidLoopRule.walk_of(open_pair)
        boiler = walk.port_sets[0]

        assert FluidLoopRule.partly_connected(walk, boiler) == {
            LoopQuantity.FLOW_IN: False,
            LoopQuantity.TEMPERATURE_IN: True,
            LoopQuantity.FLOW_OUT: True,
            LoopQuantity.TEMPERATURE_OUT: True,
        }
        whole = FluidLoopRule.walk_of(Rig.pair())
        assert all(FluidLoopRule.partly_connected(whole, port_set) is None for port_set in whole.port_sets)
        idle = FluidLoopRule.walk_of(Rig.system([Member.of("Boiler", loop_name="dhw", supplies=True, pumps=True)]))
        assert FluidLoopRule.partly_connected(idle, idle.port_sets[0]) is None
        split = FluidLoopRule.walk_of(Rig.split())
        assert FluidLoopRule.partly_connected(split, split.port_sets[0]) is None

    def test_the_open_channels_are_named_with_their_quantities(self) -> None:
        """Catches a message that names a connected channel as open, or drops the quantity."""
        walk = FluidLoopRule.walk_of(Rig.open_pair())
        boiler = walk.port_sets[0]
        connected = FluidLoopRule.partly_connected(walk, boiler)
        assert connected is not None

        assert FluidLoopRule.open_channels(walk, boiler, connected) == "Boiler.FlowIn (flow_in)"

    def test_the_faults_of_the_family_raise_the_fluid_loop_wiring_error(self) -> None:
        """Catches an ``except FluidLoopWiringError`` that the move into the rules package broke."""
        assert issubclass(FluidLoopWiringError, SetupRuleViolation) and issubclass(FluidLoopWiringError, ValueError)
        with pytest.raises(FluidLoopWiringError, match=r"FL-08 one-pump-owner: the loop \(Boiler.space_heating, Tank.generator\) has 2"):
            fft.FluidLoopRules.refuse_a_broken_wiring(Rig.pair(tank_pumps=True).components)

    def test_the_build_refuses_a_loop_tagged_by_half_before_any_rule_groups_port_sets(self) -> None:
        """Catches a rule of either family grouping the port sets of a component that tags a loop by half, which raises.

        The simulator runs every rule of the package; with the tank's loop tagged by half only ``FL-01`` runs and the
        others are listed as not run.
        """
        with pytest.raises(FluidLoopWiringError) as refused:
            SetupRuleRegistry.collect().run(RuleStage.BUILD, Rig.system([Rig.half_tagged_tank()]), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in refused.value.faults] == ["FL-01"]
        skipped = {skip.code for skip in refused.value.skipped}
        assert {"EP-01", "EP-02", "EP-03", "EP-04", "EP-05", "EP-06", "FL-02", "FL-08"} <= skipped

    def test_the_circuit_rules_wait_for_every_loop_rule(self) -> None:
        """Catches a circuit rule reporting a broken loop again, as a circuit whose other end does not pair.

        A loop with two pump owners is the fluid-loop rules' fault; the circuit rules, which find a circuit's other end
        along the same wiring, are not run, so the refusal stays the family's own error.
        """
        assert EnergyPortRule.LOOP_RULES == frozenset(rule.CODE for rule in fft.FluidLoopRules.registry().rules)
        with pytest.raises(FluidLoopWiringError) as refused:
            SetupRuleRegistry.collect().run(RuleStage.BUILD, Rig.pair(tank_pumps=True), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in refused.value.faults] == ["FL-08"]
        assert [(skip.code, skip.because_of) for skip in refused.value.skipped] == [("EP-01", ("FL-08",)), ("EP-02", ("FL-08",))]


class TestPortSetGrouping:
    """The grouping of a component's channels by loop, which ``FL-01`` and every port set read."""

    def test_the_tagged_channels_are_grouped_by_loop_and_keyed_by_quantity(self) -> None:
        """Catches a channel of one loop filed under another, or an untagged channel taken for a member."""
        boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
        Member.tag(boiler, FluidLoop("dhw", carrier=Member.CARRIER, fluid=LoopFluid.WATER, supplies=True, may_cool=False, pumps=True), prefix="Dhw")
        boiler.add_input("Boiler", "Untagged", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, False)

        grouped = FluidLoopPortSet.tagged_channels([*boiler.inputs, *boiler.outputs])

        assert [loop.name for loop in grouped] == ["space_heating", "dhw"]
        assert {quantity: channel.field_name for quantity, channel in list(grouped.values())[1].items()} == {
            LoopQuantity.FLOW_IN: "DhwFlowIn",
            LoopQuantity.TEMPERATURE_IN: "DhwTemperatureIn",
            LoopQuantity.FLOW_OUT: "DhwFlowOut",
            LoopQuantity.TEMPERATURE_OUT: "DhwTemperatureOut",
        }

    def test_a_loop_tagged_by_half_gives_its_sentence_and_a_whole_one_none(self) -> None:
        """Catches a gap in the tags that passes, or a whole loop refused."""
        tank = Rig.half_tagged_tank()

        assert FluidLoopPortSet.half_tagged("Tank", [*tank.inputs, *tank.outputs]) == [
            "Tank tags the loop 'generator' by half: it tags FlowIn (flow_in), TemperatureIn (temperature_in), TemperatureOut "
            "(temperature_out) and lacks flow_out; a member of a fluid loop has a flow in, a flow out, a temperature in and a "
            "temperature out."
        ]
        whole = Member.of("Tank", loop_name="generator", supplies=False, pumps=False)
        assert not FluidLoopPortSet.half_tagged("Tank", [*whole.inputs, *whole.outputs])

    def test_grouping_a_loop_tagged_by_half_outside_a_build_is_refused_with_the_same_sentence(self) -> None:
        """Catches the port sets of a half-tagged loop handed to a caller that did not run the build's rules."""
        tank = Rig.half_tagged_tank()

        with pytest.raises(FluidLoopError, match=r"^Tank tags the loop 'generator' by half: .* lacks flow_out;"):
            tank.port_sets()


class TestTheWalk:
    """The walk's discovery of closed loops, which the loop rules judge and ``find_loops`` names."""

    def test_the_complete_port_sets_are_wired_whole_and_read_once(self) -> None:
        """Catches a set read twice or wired by half taken as a member of a loop."""
        assert [port_set.label for port_set in FluidLoopRule.walk_of(Rig.pair()).complete_port_sets()] == ["Boiler.space_heating", "Tank.generator"]
        assert [port_set.label for port_set in FluidLoopRule.walk_of(Rig.split()).complete_port_sets()] == ["Tank.generator"]
        assert not FluidLoopRule.walk_of(Rig.flows_only()).complete_port_sets()

    def test_each_closed_loop_is_listed_once_in_the_order_of_its_flow(self) -> None:
        """Catches a loop listed from each of its members, or a broken chain listed as a loop."""
        closed = FluidLoopRule.walk_of(Rig.crossed_temperatures()).closed_loops()

        assert [[member.label for member in members] for members in closed] == [
            ["Boiler.space_heating", "Tank.generator"],
            ["Other.space_heating", "OtherTank.generator"],
        ]
        assert not FluidLoopRule.walk_of(Rig.split()).closed_loops()

    def test_find_loops_leaves_out_a_loop_it_cannot_name(self) -> None:
        """Catches the discovery inventing an id for a loop without a pump owner, which ``FL-08`` refuses."""
        assert not FluidLoopWalk.find_loops(Rig.pair(generator_pumps=False).components)
        assert [loop.loop_id for loop in FluidLoopWalk.find_loops(Rig.pair().components)] == ["Boiler.space_heating"]


class TestLoopTaggedWhole:
    """``FL-01``: every loop a component tags has all four channels."""

    def test_a_loop_tagged_by_half_is_a_fault_at_the_component_and_a_whole_one_none(self) -> None:
        """Catches a half-tagged loop that would crash every rule that groups port sets, and a refusal of a whole loop."""
        faults = LoopTaggedWhole().check(Rig.system([Rig.half_tagged_tank()]))

        assert len(faults) == 1
        fault = faults[0]
        assert (fault.code, fault.component, fault.port) == ("FL-01", "Tank", None)
        assert fault.message.startswith("Tank tags the loop 'generator' by half: it tags FlowIn (flow_in)")
        assert fault.message.endswith("; see: hisim setup-rules show FL-01")
        assert not LoopTaggedWhole().check(Rig.pair())


class TestFarEndTagged:
    """``FL-02``: a tagged flow leaves for and arrives from tagged channels."""

    def test_an_untagged_reader_and_an_untagged_source_are_faults_at_the_flow_and_a_tagged_loop_none(self) -> None:
        """Catches a loop's flow leaving the walk through a channel that is no member, and a refusal of a closed loop."""
        faults = FarEndTagged().check(Rig.metered(fed=True))

        assert codes_and_places(faults) == [("FL-02", "Boiler", "FlowOut"), ("FL-02", "Tank", "FlowIn")]
        assert "the loop Boiler.space_heating leaves Boiler.FlowOut for Meter.Flow, which carries no flow_in tag" in faults[0].message
        assert "the loop Tank.generator arrives at Tank.FlowIn from Meter.FlowCopy, which carries no flow_out tag" in faults[1].message
        assert codes_and_places(FarEndTagged().check(Rig.metered(fed=False))) == [("FL-02", "Boiler", "FlowOut")]
        assert not FarEndTagged().check(Rig.pair())


class TestFlowReadOnce:
    """``FL-03``: one flow out, one flow in."""

    def test_a_flow_two_port_sets_read_is_a_fault_at_the_flow_and_one_reader_none(self) -> None:
        """Catches a split without a valve delivering one flow's heat twice, and a refusal of a closed loop."""
        faults = FlowReadOnce().check(Rig.split())

        assert len(faults) == 1
        fault = faults[0]
        assert (fault.code, fault.component, fault.port) == ("FL-03", "Boiler", "FlowOut")
        assert "is read by 2 port sets, Tank.generator, Twin.generator: one flow delivers its heat once" in fault.message
        assert not FlowReadOnce().check(Rig.pair())


class TestLoopCloses:
    """``FL-04``: a port set wired at all is part of a flow that returns."""

    def test_an_open_flow_is_a_fault_at_each_member_and_a_closed_loop_none(self) -> None:
        """Catches a loop whose flow goes nowhere, and a refusal of a closed loop or an idle port set."""
        faults = LoopCloses().check(Rig.open_pair())

        assert codes_and_places(faults) == [("FL-04", "Boiler", None), ("FL-04", "Tank", None)]
        assert "the loop through (Boiler.space_heating, Tank.generator) does not close: Boiler.FlowIn (flow_in)" in faults[0].message
        assert not LoopCloses().check(Rig.pair())
        assert not LoopCloses().check(Rig.system([Member.of("Boiler", loop_name="dhw", supplies=True, pumps=True)]))

    def test_a_set_whose_flow_is_read_twice_is_left_to_flow_read_once(self) -> None:
        """Catches one port set refused twice for one mistake: the split boiler is ``FL-03``'s, the twin's open flow ``FL-04``'s."""
        assert codes_and_places(LoopCloses().check(Rig.split())) == [("FL-04", "Twin", None)]


class TestWiredWhole:
    """``FL-05``: flows and temperatures connected together, or neither."""

    def test_flows_without_a_temperature_are_a_fault_at_each_member_and_a_whole_loop_none(self) -> None:
        """Catches a member booking heat on a return it never reads, and a refusal of a closed loop."""
        faults = WiredWhole().check(Rig.flows_only())

        assert codes_and_places(faults) == [("FL-05", "Boiler", None), ("FL-05", "Tank", None)]
        assert "Boiler.space_heating is wired by half: its flows are connected and Boiler.TemperatureIn (temperature_in) is not" in faults[0].message
        assert not WiredWhole().check(Rig.pair())

    def test_a_set_with_an_open_flow_is_left_to_loop_closes(self) -> None:
        """Catches one port set refused by both rules of a partly connected set."""
        assert not WiredWhole().check(Rig.open_pair())


class TestTemperatureFollowsFlow:
    """``FL-06``: the temperature wire follows the flow wire."""

    def test_a_crossed_temperature_is_a_fault_at_the_input_and_a_following_one_none(self) -> None:
        """Catches a tank pairing one boiler's flow with another boiler's supply temperature, and a refusal of a closed loop."""
        faults = TemperatureFollowsFlow().check(Rig.crossed_temperatures())

        assert codes_and_places(faults) == [("FL-06", "OtherTank", "TemperatureIn"), ("FL-06", "Tank", "TemperatureIn")]
        assert any(
            "Tank.TemperatureIn reads Other.TemperatureOut, but the flow arrives from Boiler.space_heating, so it reads "
            "Boiler.TemperatureOut" in fault.message
            for fault in faults
        )
        assert not TemperatureFollowsFlow().check(Rig.pair())


class TestOneCarrierOneFluid:
    """``FL-07``: the members of a loop state one carrier and one fluid."""

    @pytest.mark.parametrize(
        ("changes", "expected"),
        [
            ({"carrier": lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT}, "every member of a loop states the same carrier"),
            ({"fluid": LoopFluid.BRINE}, "is water at Boiler.space_heating and brine at Tank.generator: every member of a loop states the same fluid"),
        ],
    )
    def test_a_disagreement_is_a_fault_at_the_loop_s_first_member(self, changes: Any, expected: str) -> None:
        """Catches a space-heating end wired to a hot-water end, or a water end to a brine end."""
        faults = OneCarrierOneFluid().check(Rig.disagreeing(**changes))

        assert len(faults) == 1
        fault = faults[0]
        assert (fault.code, fault.component, fault.port) == ("FL-07", "Boiler", None)
        assert expected in fault.message

    def test_members_that_agree_are_no_fault(self) -> None:
        """Catches a refusal of a loop whose members state the same carrier and fluid."""
        assert not OneCarrierOneFluid().check(Rig.pair())


class TestOnePumpOwner:
    """``FL-08``: exactly one member pumps."""

    @pytest.mark.parametrize(("generator_pumps", "tank_pumps", "count"), [(True, True, 2), (False, False, 0)])
    def test_no_or_two_pump_owners_are_a_fault_at_the_loop_s_first_member(self, generator_pumps: bool, tank_pumps: bool, count: int) -> None:
        """Catches a loop with two pumps setting its flow, or one with none and no id."""
        faults = OnePumpOwner().check(Rig.pair(generator_pumps=generator_pumps, tank_pumps=tank_pumps))

        assert len(faults) == 1
        fault = faults[0]
        assert (fault.code, fault.component, fault.port) == ("FL-08", "Boiler", None)
        assert f"the loop (Boiler.space_heating, Tank.generator) has {count} pumping port sets" in fault.message

    def test_one_pump_owner_is_no_fault(self) -> None:
        """Catches a refusal of the ordinary loop, the generator pumping and the tank passing the flow back."""
        assert not OnePumpOwner().check(Rig.pair())
