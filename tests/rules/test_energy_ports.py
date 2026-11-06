"""Direct tests of the energy-port rules: each rule on a hand-built system with its fault and on one without.

The systems are stand-ins, each a component name with its outputs, its connected inputs and its fluid-loop port sets,
as the tests of the energy balance build them (``tests/test_energy_port_hydronic.py``). The room-heat rule is tested
for every kind: it refuses a residential and a commercial system whose occupancy heat nobody reads, waives a demo and
a plant, and refuses the demo again once its waiver is removed, which shows that the waiver, not the rule, is what the
demo relies on.
"""

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.cli_setup_rules import SetupRuleCommands
from hisim.config import ComponentID
from hisim.energy_port import EnergyPort
from hisim.postprocessing.energy_balance.ports import LoopFault
from hisim.setup_rules.energy_ports import (
    EnergyPortRule,
    EnergyPortWiringError,
    InputPortPairs,
    LoopPairing,
    NoLoopOnANode,
    OneReaderPerFlow,
    ReceiverUnambiguous,
    RoomHeatNeedsReceiver,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage, Skip
from hisim.setup_rules.subjects import BuiltSystem
from tests import functions_for_testing as fft
from tests.test_energy_port_hydronic import LoopRig, End, Loops

pytestmark = pytest.mark.base

SPACE_HEATING = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
ROOM_HEAT = lt.EnergyBalanceCarrier.ROOM_HEAT


class Rig:
    """Hand-built systems of stand-ins for the rules of the family."""

    #: The occupancy's two outputs that release heat into the heated zone.
    OCCUPANCY_OUTPUTS: ClassVar[Tuple[str, ...]] = ("HeatingByResidents", "HeatingByDevices")

    @staticmethod
    def output(component: str, field: str, port: Optional[EnergyPort]) -> cp.ComponentOutput:
        """An output in W of a stand-in, with or without an energy port."""
        return cp.ComponentOutput(component, field, lt.LoadTypes.HEATING, lt.Units.WATT, energy_port=port, component_id=ComponentID(component))

    @staticmethod
    def system(components: Sequence[Any]) -> BuiltSystem:
        """The built system of the stand-ins."""
        return BuiltSystem(components=tuple(components))

    @classmethod
    def loop_system(cls, inputs: Dict[str, List[SimpleNamespace]], ends: Dict[str, List[End]], twin: bool = False) -> BuiltSystem:
        """A boiler and a tank (and a second tank with ``twin``) with the given inputs and loop ends."""
        columns = LoopRig.boiler_columns() + LoopRig.tank_columns() + (LoopRig.tank_columns("Twin") if twin else [])
        return cls.system(LoopRig.stand_ins(LoopRig.outputs_of(columns), inputs, ends))

    @classmethod
    def paired_loop(cls) -> BuiltSystem:
        """The boiler and the tank, both declaring their ends of the loop."""
        return cls.loop_system(
            {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()},
            {"Boiler": [LoopRig.SUPPLY_END], "Tank": [LoopRig.RECEIVING_END]},
        )

    @classmethod
    def two_readers(cls, tagged: Tuple[str, ...]) -> BuiltSystem:
        """A source's space heating read by two components that declare ports; the ``tagged`` ones tag the reading input."""
        received = EnergyPort(lt.EnergyRole.CONSUMPTION, SPACE_HEATING)
        components = LoopRig.stand_ins(
            [
                cls.output("Source", "Heat", EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING)),
                cls.output("First", "Loss", EnergyPort(lt.EnergyRole.LOSS, SPACE_HEATING)),
                cls.output("Second", "Loss", EnergyPort(lt.EnergyRole.LOSS, SPACE_HEATING)),
            ],
            {name: [LoopRig.wired("HeatIn", "Source", "Heat", received if name in tagged else None)] for name in ("First", "Second")},
            {},
        )
        return cls.system(components)

    @classmethod
    def one_wire(cls, input_port: EnergyPort) -> BuiltSystem:
        """A tank's space-heating output read by a loop through an input carrying ``input_port``."""
        components = LoopRig.stand_ins(
            [cls.output("Tank", "HeatOut", EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING)), cls.output("Loop", "Loss", None)],
            {"Loop": [LoopRig.wired("HeatIn", "Tank", "HeatOut", input_port)]},
            {},
        )
        return cls.system(components)

    @classmethod
    def occupancy(cls, reader: Optional[str] = None, environment: Optional[lt.EnvironmentNode] = None) -> BuiltSystem:
        """An occupancy whose two room-heat outputs are read by ``reader`` through room-heat input ports, or by nobody.

        Args:
            reader: The reading component's name; ``"Controller"`` makes it an exempt controller, which does not count.
            environment: The environment the occupancy's ports name, ``None`` for the occupancy's own ports.
        """
        produced = EnergyPort(lt.EnergyRole.PRODUCTION, ROOM_HEAT, environment=environment)
        outputs = [cls.output("UTSPConnector", field, produced) for field in cls.OCCUPANCY_OUTPUTS]
        components: List[Any] = LoopRig.stand_ins(outputs, {}, {})
        if reader is not None:
            received = EnergyPort(lt.EnergyRole.CONSUMPTION, ROOM_HEAT)
            reading: Any = SimpleNamespace(
                component_name=reader,
                inputs=[LoopRig.wired(field, "UTSPConnector", field, None if reader == "Controller" else received) for field in cls.OCCUPANCY_OUTPUTS],
                outputs=[cls.output(reader, "Loss", EnergyPort(lt.EnergyRole.LOSS, ROOM_HEAT))] if reader != "Controller" else [],
                port_sets=tuple,
            )
            if reader == "Controller":
                reading = ExemptController(reading)
            components.append(reading)
        return cls.system(components)


class ExemptController:
    """A stand-in that is exempt from the energy balance by its stated reason, as a controller is."""

    ENERGY_PORTS_EXEMPT_BECAUSE = "a controller decides, it moves no energy"

    def __init__(self, stand_in: Any) -> None:
        """Take over the stand-in's name, inputs, outputs and port sets."""
        self.component_name = stand_in.component_name
        self.inputs = stand_in.inputs
        self.outputs = stand_in.outputs
        self.port_sets = stand_in.port_sets


def codes_and_places(faults: Sequence[Any]) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """The code, component and port of each fault."""
    return [(fault.code, fault.component, fault.port) for fault in faults]


class TestTheFamily:
    """What every rule of the family shares: the wiring it reads, how a loop fault becomes its fault, the error."""

    def test_the_wiring_of_a_system_classifies_its_exempt_components(self) -> None:
        """Catches a rule reading a wiring that would name a controller as the receiver of a port."""
        wiring = EnergyPortRule.wiring_of(Rig.occupancy("Controller"))

        assert set(wiring.exempt) == {"Controller"} and not wiring.moves_energy("Controller")
        assert wiring.readers[("UTSPConnector", "HeatingByResidents")] == ["Controller"]

    def test_a_loop_fault_becomes_a_fault_of_the_rule_at_its_component_and_output(self) -> None:
        """Catches a loop fault that loses its location or its pointer to the rule on the way into a refusal."""
        (fault,) = OneReaderPerFlow.faults_of([LoopFault("Boiler", "the flow is read twice", "Flow")])

        assert (fault.code, fault.component, fault.port) == ("EP-02", "Boiler", "Flow")
        assert fault.message == "the flow is read twice; see: hisim setup-rules show EP-02"

    def test_the_faults_of_the_family_raise_the_energy_port_wiring_error(self) -> None:
        """Catches an ``except EnergyPortWiringError`` that the move into the rules package broke."""
        with pytest.raises(EnergyPortWiringError, match=r"EP-05 input-port-pairs: the input Loop.HeatIn"):
            fft.EnergyPortRules.refuse_a_broken_wiring(Rig.one_wire(EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING)).components)


class TestLoopPairing:
    """``EP-01``: the other end tags the same loop, and exactly one of the two supplies it."""

    def test_an_other_end_that_declares_ports_but_not_the_loop_is_a_fault_at_the_end(self) -> None:
        """Catches a boiler booking the loop's heat while the tank it charges books none of it."""
        system = Rig.loop_system({"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()}, {"Boiler": [LoopRig.SUPPLY_END]})

        (fault,) = LoopPairing().check(system)

        assert (fault.component, fault.port) == ("Boiler", None)
        assert "its other end, Tank, declares ports and not this loop" in fault.message

    def test_two_suppliers_are_a_fault_and_a_paired_loop_none(self) -> None:
        """Catches a loop whose heat both ends book as delivered, and a rule that refuses a correct pair."""
        ends = {
            "Boiler": [End(Loops.loop(supplies=True, pumps=True), LoopRig.SUPPLY_END.names)],
            "Tank": [End(Loops.loop(supplies=True, pumps=False), LoopRig.RECEIVING_END.names)],
        }
        system = Rig.loop_system({"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs()}, ends)

        assert any("has two suppliers" in fault.message for fault in LoopPairing().check(system))
        assert not LoopPairing().check(Rig.paired_loop())


class TestOneReaderPerFlow:
    """``EP-02``: one loop end besides the owner per mass flow."""

    def test_a_flow_two_ends_read_is_a_fault_at_the_flow_and_one_end_none(self) -> None:
        """Catches the heat of one flow delivered twice, and a rule that refuses the one reader it allows."""
        inputs = {"Boiler": [LoopRig.wired("Return", "Tank", "Return")], "Tank": LoopRig.tank_inputs(), "Twin": LoopRig.tank_inputs()}
        ends = {"Boiler": [LoopRig.SUPPLY_END], "Tank": [LoopRig.RECEIVING_END], "Twin": [LoopRig.RECEIVING_END]}

        (fault,) = OneReaderPerFlow().check(Rig.loop_system(inputs, ends, twin=True))

        assert (fault.component, fault.port) == ("Boiler", "Flow")
        assert "the mass flow Boiler.Flow is read by 2 components that declare a loop on it (Tank, Twin)" in fault.message
        assert not OneReaderPerFlow().check(Rig.paired_loop())


class TestNoLoopOnANode:
    """``EP-03``: neither an exempt component nor a meter declares a loop end."""

    @staticmethod
    def controller_with_a_loop() -> BuiltSystem:
        """The paired loop, except that the tank is an exempt controller declaring the receiving end."""
        components: List[Any] = list(Rig.paired_loop().components)
        components[1] = ExemptController(components[1])
        return Rig.system(components)

    def test_a_loop_end_on_an_exempt_component_is_a_fault_and_one_on_a_device_none(self) -> None:
        """Catches a loop end that no balance can pair, its heat vanishing, and a refusal of an ordinary device's end."""
        (fault,) = NoLoopOnANode().check(self.controller_with_a_loop())

        assert (fault.code, fault.component) == ("EP-03", "Tank")
        assert "Tank declares the fluid loop 'space_heating', but it is exempt from the energy balance or a meter" in fault.message
        assert not NoLoopOnANode().check(Rig.paired_loop())

    def test_the_loop_rules_wait_for_it(self) -> None:
        """Catches pairing faults reported against a loop whose end on a controller was never resolved."""
        registry = fft.EnergyPortRules.registry([LoopPairing, OneReaderPerFlow, NoLoopOnANode])

        findings = registry.findings(RuleStage.BUILD, self.controller_with_a_loop(), SetupKind.RESIDENTIAL)

        assert [fault.code for fault in findings.faults] == ["EP-03"]
        assert findings.skipped == (Skip("EP-01", ("EP-03",)), Skip("EP-02", ("EP-03",)))
        assert findings.violation_type is EnergyPortWiringError


class TestReceiverUnambiguous:
    """``EP-04``: the wiring names one receiver for a port that is not metered."""

    def test_two_untagged_readers_are_a_fault_at_the_output_and_one_tagged_reader_none(self) -> None:
        """Catches a transfer silently booked to the first of two readers, and a refusal of the tagged receiver."""
        faults = ReceiverUnambiguous().check(Rig.two_readers(()))

        assert codes_and_places(faults) == [("EP-04", "Source", "Heat")]
        fault = faults[0]
        assert "is read by 2 components that declare ports (First, Second) and none of them tags its reading input" in fault.message
        assert not ReceiverUnambiguous().check(Rig.two_readers(("First",)))

    def test_two_tagged_readers_are_a_fault(self) -> None:
        """Catches one energy received twice through two inputs carrying a matching port."""
        faults = ReceiverUnambiguous().check(Rig.two_readers(("First", "Second")))

        assert len(faults) == 1
        assert "is read through inputs carrying a matching port by 2 components (First, Second): one energy is received once" in faults[0].message

    def test_the_ambiguity_of_one_output(self) -> None:
        """Catches the per-output judgement applying to a metered port, a port with an environment or an output without a port."""
        system = Rig.two_readers(())
        wiring = EnergyPortRule.wiring_of(system)
        production = EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING)

        assert ReceiverUnambiguous.ambiguity(wiring, "Source", "Heat", production) is not None
        assert ReceiverUnambiguous.ambiguity(wiring, "Source", "Heat", None) is None
        assert ReceiverUnambiguous.ambiguity(wiring, "Source", "Heat", EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY)) is None
        outdoors = EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING, environment=lt.EnvironmentNode.OUTDOORS)
        assert ReceiverUnambiguous.ambiguity(wiring, "Source", "Heat", outdoors) is None
        assert ReceiverUnambiguous.ambiguity(wiring, "Source", "Heat", EnergyPort(lt.EnergyRole.CONSUMPTION, SPACE_HEATING)) is None


class TestInputPortPairs:
    """``EP-05``: an input port pairs with the port of the output it reads."""

    @pytest.mark.parametrize(
        ("input_port", "refused"),
        [
            (EnergyPort(lt.EnergyRole.CONSUMPTION, SPACE_HEATING), False),
            (EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING), True),
            (EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.COOLING), True),
        ],
    )
    def test_an_input_port_of_the_same_role_or_another_carrier_is_a_fault_at_the_input(self, input_port: EnergyPort, refused: bool) -> None:
        """Catches a building booking a transfer its sender never made."""
        faults = InputPortPairs().check(Rig.one_wire(input_port))

        assert codes_and_places(faults) == ([("EP-05", "Loop", "HeatIn")] if refused else [])
        if refused:
            assert "reads the energy port Tank.HeatOut (production, space_heating_heat)" in faults[0].message


class TestRoomHeatNeedsReceiver:
    """``EP-06``: heat released into the heated zone has a receiver, except in a demo and a plant."""

    @pytest.mark.parametrize("kind", [SetupKind.RESIDENTIAL, SetupKind.COMMERCIAL])
    def test_an_occupancy_without_a_building_is_refused_for_a_house_naming_both_outputs(self, kind: SetupKind) -> None:
        """Catches the residents' heat dropped in silence in a house, which would then miss its internal gains."""
        with pytest.raises(EnergyPortWiringError) as refused:
            fft.EnergyPortRules.refuse_a_broken_wiring(Rig.occupancy().components, kind)

        assert codes_and_places(refused.value.faults) == [
            ("EP-06", "UTSPConnector", "HeatingByDevices"),
            ("EP-06", "UTSPConnector", "HeatingByResidents"),
        ]
        assert (
            "room heat needs a receiver: UTSPConnector.HeatingByResidents declares PRODUCTION of ROOM_HEAT and no component reads it; "
            "list the component under the inputs of the building whose heated zone it heats; see: hisim setup-rules show EP-06"
        ) in str(refused.value)

    @pytest.mark.parametrize("kind", [SetupKind.DEMO, SetupKind.PLANT])
    def test_a_demo_and_a_plant_run_without_a_receiver(self, kind: SetupKind) -> None:
        """Catches the rule refusing the demo setups without a building and a plant that has no heated zone."""
        fft.EnergyPortRules.refuse_a_broken_wiring(Rig.occupancy().components, kind)

    def test_without_its_waiver_the_rule_refuses_the_demo_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a demo that passes because the rule skips it some other way, not because of the waiver it declares."""
        monkeypatch.setattr(RoomHeatNeedsReceiver, "WAIVED_FOR", MappingProxyType({}))

        with pytest.raises(EnergyPortWiringError, match=r"EP-06 room-heat-needs-receiver: room heat needs a receiver: UTSPConnector\.HeatingByDevices"):
            fft.EnergyPortRules.refuse_a_broken_wiring(Rig.occupancy().components, SetupKind.DEMO)

    def test_a_building_reading_the_heat_satisfies_the_rule_and_an_exempt_reader_does_not(self) -> None:
        """Catches a refusal of the ordinary house, and a controller counted as the heat's receiver."""
        assert not RoomHeatNeedsReceiver().check(Rig.occupancy("Building"))
        assert len(RoomHeatNeedsReceiver().check(Rig.occupancy("Controller"))) == 2

    def test_a_port_that_names_its_environment_is_not_affected(self) -> None:
        """Catches the rule refusing heat whose boundary node takes it."""
        assert not RoomHeatNeedsReceiver().check(Rig.occupancy(environment=lt.EnvironmentNode.OUTDOORS))

    @pytest.mark.parametrize(("kind", "refused"), [("demo", False), ("residential", True)])
    def test_the_demo_twin_without_a_building_passes_as_a_demo_only(self, tmp_path: Path, kind: str, refused: bool) -> None:
        """Catches the rule missing the real occupancy's ports when a file is built, or refusing the demo it waives.

        The twin of ``system_setups/dynamic_components.py`` runs residents, PV, batteries and fuel cells without a
        building; declared ``residential``, its residents' two room-heat outputs are refused.
        """
        twin = Path(__file__).resolve().parents[2] / "energy_systems" / "dynamic_components.energy_system.yaml"
        text = twin.read_text(encoding="utf-8")
        assert "\nsetup_kind: demo\n" in text
        declared = tmp_path / twin.name
        declared.write_text(text.replace("\nsetup_kind: demo\n", f"\nsetup_kind: {kind}\n"), encoding="utf-8")

        findings = SetupRuleCommands(SetupRuleRegistry.collect()).findings_of(declared, RuleStage.BUILD)[-1]

        assert findings.subject == "BuiltSystem"
        expected = [("EP-06", "UTSPConnector", output) for output in sorted(Rig.OCCUPANCY_OUTPUTS)] if refused else []
        assert codes_and_places(findings.faults) == expected

    def test_it_waits_for_the_input_ports_to_pair(self) -> None:
        """Catches the rule judging a receiver whose input port does not pair with the output it reads, or grouping the port sets of a loop tagged by half."""
        assert RoomHeatNeedsReceiver.REQUIRES == frozenset({"EP-05", "FL-01"})

    @pytest.mark.parametrize(
        ("port", "releases"),
        [
            (EnergyPort(lt.EnergyRole.PRODUCTION, ROOM_HEAT), True),
            (EnergyPort(lt.EnergyRole.PRODUCTION, ROOM_HEAT, environment=lt.EnvironmentNode.OUTDOORS), False),
            (EnergyPort(lt.EnergyRole.LOSS, ROOM_HEAT), False),
            (EnergyPort(lt.EnergyRole.PRODUCTION, SPACE_HEATING), False),
            (None, False),
        ],
    )
    def test_which_ports_release_room_heat(self, port: Optional[EnergyPort], releases: bool) -> None:
        """Catches the rule applied to a building's room-heat loss, to space heating, or to an output without a port."""
        assert RoomHeatNeedsReceiver.releases_room_heat(port) is releases
