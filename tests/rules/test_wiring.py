"""Direct tests of the wiring rules: each rule on a hand-built wired file with its fault and on one without.

The wired systems are stand-ins: components with the outputs and inputs the rules read, the planned wires, the feeds,
the consuming outputs and the refusals the wiring records, put together by ``Rig``. The rules that refuse what the
wiring recorded are tested with a refusal of their code and with none; the rules of the plan with a plan that breaks
them and one that does not. Further tests pin the location a fault carries, the requirement of the rules that read the
whole plan, and the helpers the declarative layer gained when its checks moved here: the refusal record, the error's
identifier, the source map's suffix, the resolver's lookups and the wired system's application.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

import pytest

from hisim import loadtypes as lt
from hisim.energy_port import EnergyPort
from hisim.energy_system.assemblies.record import SourceLocation, SourceMap, SourceMapEntry
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError, WiringRefusal
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.model import ConsumingOutput
from hisim.energy_system.wiring import PlannedWire, WiredSystem
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import RuleStage, Skip
from hisim.setup_rules.subjects import BuiltDocument
from hisim.setup_rules.wiring import (
    ChannelMatches,
    ConsumingOutputCarrier,
    Constructed,
    DefaultsDeclared,
    DerivedPort,
    DispatchRuleHolds,
    DispatchSignalUnique,
    FeedUnique,
    InputExists,
    KnownTag,
    MandatoryInputConnected,
    MeterFeedCount,
    NoDoubleCount,
    NoInputFedTwice,
    OutputExists,
    PortNameFree,
    PortTypesAgree,
    TargetIsAnAggregator,
    UnconsumedSource,
    UnmeteredOutputOfANeed,
    WiringRule,
)
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base

GAS = lt.EnergyBalanceCarrier.NATURAL_GAS


class Rig:
    """Hand-built wired files of stand-in components for the wiring rules."""

    @staticmethod
    def output(field: str, port: Optional[EnergyPort] = None, unit: lt.Units = lt.Units.WATT) -> SimpleNamespace:
        """An electricity output in ``unit``, with or without an energy port."""
        return SimpleNamespace(field_name=field, load_type=lt.LoadTypes.ELECTRICITY, unit=unit, energy_port=port)

    @staticmethod
    def input(field: str, mandatory: bool = False, allowed_open: bool = False, unit: lt.Units = lt.Units.WATT) -> SimpleNamespace:
        """An electricity input in ``unit``; ``mandatory`` and ``allowed_open`` as a component declares them."""
        return SimpleNamespace(
            field_name=field, loadtype=lt.LoadTypes.ELECTRICITY, unit=unit, is_mandatory=mandatory, allow_unconnected_mandatory=allowed_open
        )

    @staticmethod
    def component(
        name: str, outputs: Sequence[SimpleNamespace] = (), inputs: Sequence[SimpleNamespace] = (), feeds: Optional[Dict[str, List[Any]]] = None
    ) -> Any:
        """A stand-in component named ``name`` with its ports and, for an aggregator, its default feeds by source class."""
        return SimpleNamespace(
            component_name=name,
            outputs=list(outputs),
            inputs=list(inputs),
            dynamic_default_connections=feeds or {},
            get_full_classname=lambda: f"tests.Stand{name}",
            get_classname=lambda: f"Stand{name}",
        )

    @staticmethod
    def wire(source: str, output: str, target: str, port: str) -> PlannedWire:
        """A planned wire from ``source.output`` into ``target.port``."""
        return PlannedWire(source, source, output, target, target, port, f"the explicit wire '{port}' of '{target}'")

    @staticmethod
    def feed(consumer: str, source: str, output: Optional[str]) -> FeedRequest:
        """A monitored feed of ``source.output`` into ``consumer``."""
        return FeedRequest(consumer=consumer, source=source, output=output, component_type=None, flow_tags=(), weight=FeedRequest.MONITORED_ONLY_WEIGHT)

    @staticmethod
    def declaration(source_class: str, output: str) -> SimpleNamespace:
        """A declared default feed of an aggregator for one output of a source class."""
        return SimpleNamespace(source_class_name=source_class, source_component_field_name=output, source_tags=[], source_weight=999)

    @staticmethod
    def subject(wired: WiredSystem, source_map: Optional[SourceMap] = None) -> BuiltDocument:
        """The built file of a wired system, named ``house``, read from ``house.energy_system.yaml``."""
        document = fft.WiringRules.subject(wired, name="house")
        return BuiltDocument(model=document.model, wired=wired, file="house.energy_system.yaml", source_map=source_map)

    @classmethod
    def wired(cls, components: Sequence[Any], wires: Sequence[PlannedWire] = (), **fields: Any) -> WiredSystem:
        """A wired system of the components by their names and the wires, all written; further fields as given."""
        fields.setdefault("written_wire_count", len(wires))
        return WiredSystem(components=tuple((component.component_name, component) for component in components), wires=tuple(wires), resolved_feeds=(), **fields)

    @classmethod
    def weather_and_building(cls, wires: Sequence[PlannedWire], building_unit: lt.Units = lt.Units.WATT, **fields: Any) -> WiredSystem:
        """A weather publishing ``Temperature`` and a building whose mandatory input ``Temperature`` takes ``building_unit``."""
        weather = cls.component("weather", outputs=[cls.output("Temperature")])
        building = cls.component("building", inputs=[cls.input("Temperature", mandatory=True, unit=building_unit)])
        return cls.wired([weather, building], wires, **fields)

    @classmethod
    def good_wire(cls) -> PlannedWire:
        """The weather's temperature into the building."""
        return cls.wire("weather", "Temperature", "building", "Temperature")

    @classmethod
    def refusal(cls, error_id: EnergySystemErrorId) -> WiringRefusal:
        """A refusal of this identifier at the meter's inputs."""
        return WiringRefusal(error_id, "components.meter.inputs", f"a recorded {error_id.value} problem.")

    @classmethod
    def gas_house(cls, consuming: Sequence[ConsumingOutput], fuel_port: Optional[EnergyPort], meter_feeds: Sequence[str] = ("FuelUse",)) -> WiredSystem:
        """A boiler with the outputs ``FuelUse`` (``fuel_port``) and ``PilotUse`` (gas), a gas meter declaring ``meter_feeds``, and the needs."""
        boiler = cls.component(
            "boiler", outputs=[cls.output("FuelUse", fuel_port), cls.output("PilotUse", EnergyPort(lt.EnergyRole.CONSUMPTION, GAS))]
        )
        meter = cls.component("meter", feeds={"Standboiler": [cls.declaration("Standboiler", output) for output in meter_feeds]})
        return cls.wired([boiler, meter], [cls.wire("boiler", "FuelUse", "meter", "x")], consuming=tuple(consuming))


def codes(rule: Type[WiringRule], wired: WiredSystem) -> List[str]:
    """The codes of the faults ``rule`` finds in the wired system."""
    return [fault.code for fault in rule().check(Rig.subject(wired))]


RECORDING_RULES = [
    (Constructed, EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED),
    (DefaultsDeclared, EnergySystemErrorId.NO_DECLARED_DEFAULTS),
    (FeedUnique, EnergySystemErrorId.DUPLICATE_FEED),
    (TargetIsAnAggregator, EnergySystemErrorId.NOT_AN_AGGREGATOR),
    (ChannelMatches, EnergySystemErrorId.NO_CHANNEL_MATCH),
    (DispatchRuleHolds, EnergySystemErrorId.DISPATCH_RULE_VIOLATED),
    (KnownTag, EnergySystemErrorId.UNKNOWN_TAG),
    (DispatchSignalUnique, EnergySystemErrorId.AMBIGUOUS_DISPATCH_SIGNAL),
    (PortNameFree, EnergySystemErrorId.PORT_NAME_COLLISION),
]


@pytest.mark.parametrize(("rule", "error_id"), RECORDING_RULES)
def test_a_rule_of_the_resolution_refuses_the_refusals_of_its_code_and_nothing_else(rule: Type[WiringRule], error_id: EnergySystemErrorId) -> None:
    """Catches a rule refusing another rule's refusals, or missing its own."""
    other = EnergySystemErrorId.DUPLICATE_WIRE
    faulty = Rig.wired([], refusals=(Rig.refusal(error_id), Rig.refusal(other)))

    assert codes(rule, faulty) == [error_id.value]
    assert not codes(rule, Rig.wired([], refusals=(Rig.refusal(other),)))


def test_output_exists_refuses_a_missing_output_a_derived_one_and_a_missing_consuming_output() -> None:
    """Catches ``EF-21`` missing one of its three sources, or a recorded feed output."""
    wires = [Rig.wire("weather", "Temperatur", "building", "Temperature"), Rig.wire("meter", "Grown", "building", "Temperature")]
    meter = Rig.component("meter", outputs=[Rig.output("Grown")])
    wired = Rig.wired(
        [Rig.component("weather", outputs=[Rig.output("Temperature")]), Rig.component("building", inputs=[Rig.input("Temperature")]), meter],
        wires,
        created_ports={"meter": ("Grown",)},
        consuming=(ConsumingOutput("weather", "Nope", "natural_gas", None),),
        refusals=(Rig.refusal(EnergySystemErrorId.UNKNOWN_OUTPUT_PORT),),
    )

    faults = OutputExists().check(Rig.subject(wired))

    assert len(faults) == 4
    assert any("names the output 'Temperatur', which 'weather'" in fault.message and "Did you mean: Temperature?" in fault.message for fault in faults)
    assert any("'meter.Grown', which the file does not contain" in fault.message for fault in faults)
    assert any("'weather.Nope', which Standweather does not have" in fault.message for fault in faults)
    assert not codes(OutputExists, Rig.weather_and_building([Rig.good_wire()]))


def test_input_exists_refuses_a_missing_input_and_a_written_wire_into_a_derived_one() -> None:
    """Catches ``EF-22`` passing a wire into an input the target lacks or that feed resolution grew."""
    meter = Rig.component("meter", inputs=[Rig.input("Grown")])
    weather = Rig.component("weather", outputs=[Rig.output("Temperature")])
    building = Rig.component("building", inputs=[Rig.input("Temperature")])
    wired = Rig.wired(
        [weather, building, meter],
        [Rig.wire("weather", "Temperature", "building", "Temperatur"), Rig.wire("weather", "Temperature", "meter", "Grown")],
        created_ports={"meter": ("Grown",)},
    )

    assert codes(InputExists, wired) == ["EF-22", "EF-22"]
    assert not codes(InputExists, Rig.weather_and_building([Rig.good_wire()]))


def test_a_wire_feed_resolution_derived_may_name_its_derived_port() -> None:
    """Catches the derived-port check reaching past the written wires to the ones feed resolution made."""
    meter = Rig.component("meter", inputs=[Rig.input("Grown")])
    wired = Rig.wired(
        [Rig.component("weather", outputs=[Rig.output("Temperature")]), meter],
        [Rig.wire("weather", "Temperature", "meter", "Grown")],
        created_ports={"meter": ("Grown",)},
        written_wire_count=0,
    )

    assert not codes(InputExists, wired)


def test_no_input_fed_twice_names_both_wires() -> None:
    """Catches the last of two wires into one input winning silently."""
    faults = NoInputFedTwice().check(Rig.subject(Rig.weather_and_building([Rig.good_wire(), Rig.good_wire()])))

    assert [fault.code for fault in faults] == ["EF-26"] and "is fed twice" in faults[0].message
    assert faults[0].component == "building" and faults[0].port == "Temperature"
    assert not codes(NoInputFedTwice, Rig.weather_and_building([Rig.good_wire()]))


def test_port_types_agree_refuses_a_unit_mismatch_and_skips_a_wire_to_a_missing_port() -> None:
    """Catches watts wired into another unit, and a missing port reported twice, as a mismatch as well."""
    mismatched = Rig.weather_and_building([Rig.good_wire()], building_unit=lt.Units.CELSIUS)
    missing = Rig.weather_and_building([Rig.wire("weather", "Nope", "building", "Temperature")], building_unit=lt.Units.CELSIUS)

    faults = PortTypesAgree().check(Rig.subject(mismatched))

    assert [fault.code for fault in faults] == ["EF-30"] and "unit mismatch" in faults[0].message
    assert not codes(PortTypesAgree, missing)
    assert not codes(PortTypesAgree, Rig.weather_and_building([Rig.good_wire()]))
    assert codes(PortTypesAgree, Rig.wired([], refusals=(Rig.refusal(EnergySystemErrorId.PORT_TYPE_MISMATCH),))) == ["EF-30"]


def test_mandatory_input_connected_refuses_an_open_input_unless_its_component_allows_it() -> None:
    """Catches an open mandatory input passing, or an input its component allows to stay open refused."""
    allowed = Rig.wired([Rig.component("building", inputs=[Rig.input("Temperature", mandatory=True, allowed_open=True)])])

    faults = MandatoryInputConnected().check(Rig.subject(Rig.weather_and_building([])))

    assert [fault.code for fault in faults] == ["EF-31"] and "of the energy system 'house'" in faults[0].message
    assert not codes(MandatoryInputConnected, Rig.weather_and_building([Rig.good_wire()]))
    assert not codes(MandatoryInputConnected, allowed)


def test_consuming_output_carrier_refuses_another_carrier_and_a_missing_port() -> None:
    """Catches a consuming output of another carrier, or of none, being landed as the need's carrier."""
    need = (ConsumingOutput("boiler", "FuelUse", "heating_oil", "meter"),)
    gas = EnergyPort(lt.EnergyRole.CONSUMPTION, GAS)

    oil_faults = ConsumingOutputCarrier().check(Rig.subject(Rig.gas_house(need, gas)))

    assert "consumed as heating_oil, but its energy port carries natural_gas" in oil_faults[0].message
    assert codes(ConsumingOutputCarrier, Rig.gas_house(need, None)) == ["EF-7M"]
    assert not codes(ConsumingOutputCarrier, Rig.gas_house((ConsumingOutput("boiler", "FuelUse", "natural_gas", "meter"),), gas))


def test_meter_feed_count_refuses_an_output_the_meter_declares_no_feed_or_two_feeds_for() -> None:
    """Catches a consuming output landing nowhere, or at one of two declared feeds chosen arbitrarily."""
    need = (ConsumingOutput("boiler", "FuelUse", "natural_gas", "meter"),)
    gas = EnergyPort(lt.EnergyRole.CONSUMPTION, GAS)

    assert "declares no feed for it" in MeterFeedCount().check(Rig.subject(Rig.gas_house(need, gas, meter_feeds=("PilotUse",))))[0].message
    assert "declares 2 feeds for it" in MeterFeedCount().check(Rig.subject(Rig.gas_house(need, gas, meter_feeds=("FuelUse", "FuelUse"))))[0].message
    assert not codes(MeterFeedCount, Rig.gas_house(need, gas))
    assert not codes(MeterFeedCount, Rig.gas_house((ConsumingOutput("boiler", "FuelUse", "natural_gas", None),), gas, meter_feeds=()))


def test_meter_feed_count_s_refusal_lists_what_the_meter_declares() -> None:
    """Catches the refusal of ``EF-7N`` leaving out the outputs the author could name instead."""
    house = Rig.gas_house((), None, meter_feeds=("PilotUse",))
    meter, boiler = house.component_of("meter"), house.component_of("boiler")

    refusal = MeterFeedCount.refusal_of(meter, boiler, ConsumingOutput("boiler", "FuelUse", "natural_gas", "meter"))

    assert refusal is not None and refusal.location == "components.meter.inputs" and "declares feeds for PilotUse" in refusal.problem
    assert MeterFeedCount.refusal_of(meter, boiler, ConsumingOutput("boiler", "PilotUse", "natural_gas", "meter")) is None


def test_no_double_count_refuses_an_aggregator_reading_another_while_both_observe_one_flow() -> None:
    """Catches a flow summed into a balance and read again beside it."""
    pv = Rig.component("pv", outputs=[Rig.output("Power")])
    ems = Rig.component("ems", outputs=[Rig.output("Balance")])
    meter = Rig.component("meter")
    both = {"ems": (Rig.feed("ems", "pv", "Power"),), "meter": (Rig.feed("meter", "ems", "Balance"), Rig.feed("meter", "pv", "Power"))}
    apart = {"ems": (Rig.feed("ems", "pv", "Power"),), "meter": (Rig.feed("meter", "ems", "Balance"),)}

    faults = NoDoubleCount().check(Rig.subject(Rig.wired([pv, ems, meter], feeds_by_target=both)))

    assert [fault.code for fault in faults] == ["EF-7T"] and "both observe pv.Power" in faults[0].message
    assert not codes(NoDoubleCount, Rig.wired([pv, ems, meter], feeds_by_target=apart))


def test_observed_flows_takes_a_feed_s_output_from_the_one_declaration_and_skips_an_undecidable_one() -> None:
    """Catches a feed without an output observing nothing although its aggregator declares its output."""
    single = Rig.component("ems", feeds={"Standpv": [Rig.declaration("Standpv", "Power")]})
    double = Rig.component("ems", feeds={"Standpv": [Rig.declaration("Standpv", "Power"), Rig.declaration("Standpv", "Other")]})
    pv = Rig.component("pv")

    assert NoDoubleCount.observed_flows({"ems": single, "pv": pv}, [Rig.feed("ems", "pv", None)]) == {("pv", "Power")}
    assert not NoDoubleCount.observed_flows({"ems": double, "pv": pv}, [Rig.feed("ems", "pv", None)])


def test_unconsumed_source_logs_a_component_no_wire_reads() -> None:
    """Catches a component nothing reads passing in silence, or one that is read being reported."""
    wired = Rig.weather_and_building([Rig.good_wire()])

    faults = UnconsumedSource().check(Rig.subject(wired))

    assert not UnconsumedSource.ENFORCED
    assert [fault.component for fault in faults] == ["building"]
    assert UnconsumedSource.lines(wired) == (UnconsumedSource.message_for("building"),)
    assert "'building' feeds no other component" in UnconsumedSource.message_for("building")
    weather_only = Rig.wired([Rig.component("weather", outputs=[Rig.output("Temperature")]), Rig.component("sink", inputs=[Rig.input("T")])],
                             [Rig.wire("weather", "Temperature", "sink", "T"), Rig.wire("sink", "x", "weather", "y")])
    assert not codes(UnconsumedSource, weather_only)


def test_unmetered_output_of_a_need_logs_an_output_of_the_carrier_the_need_leaves_out() -> None:
    """Catches a metered consumer's second gas output undercounting the gas without a word."""
    gas = EnergyPort(lt.EnergyRole.CONSUMPTION, GAS)
    one = Rig.gas_house((ConsumingOutput("boiler", "FuelUse", "natural_gas", "meter"),), gas)
    both = Rig.gas_house(
        (ConsumingOutput("boiler", "FuelUse", "natural_gas", "meter"), ConsumingOutput("boiler", "PilotUse", "natural_gas", "meter")), gas
    )

    faults = UnmeteredOutputOfANeed().check(Rig.subject(one))

    assert not UnmeteredOutputOfANeed.ENFORCED
    assert len(faults) == 1 and "names FuelUse but not PilotUse" in faults[0].message
    assert not codes(UnmeteredOutputOfANeed, both)


def test_a_fault_carries_the_location_the_file_and_the_source_of_an_imported_component() -> None:
    """Catches a wiring fault losing the key path, the file, or the import an expanded component came from."""
    source_map = SourceMap()
    source_map.add("meter", "component", SourceMapEntry("supply", None, "Meter", (SourceLocation("house.energy_system.yaml", 7),)))
    wired = Rig.wired([], refusals=(Rig.refusal(EnergySystemErrorId.NO_CHANNEL_MATCH),))

    fault = ChannelMatches.refuse(Rig.subject(wired, source_map), wired.refusals[0], port="Grown")

    assert fault.key_path == ("components", "meter", "inputs") and fault.component == "meter" and fault.port == "Grown"
    assert fault.file == "house.energy_system.yaml"
    assert fault.message.startswith("EF-28 at components.meter.inputs: a recorded EF-28 problem. [source: meter (import supply")
    assert fault.message.endswith("; see: hisim setup-rules show EF-28")
    assert ChannelMatches.recorded(Rig.subject(wired)) == [ChannelMatches.refuse(Rig.subject(wired), wired.refusals[0])]


def test_the_port_lookups_find_a_port_by_name_or_nothing() -> None:
    """Catches the rules' port lookups confusing inputs and outputs."""
    weather = Rig.component("weather", outputs=[Rig.output("Temperature")], inputs=[Rig.input("Clock")])

    assert WiringRule.output_of(weather, "Temperature") is weather.outputs[0] and WiringRule.output_of(weather, "Clock") is None
    assert WiringRule.input_of(weather, "Clock") is weather.inputs[0] and WiringRule.input_of(weather, "Temperature") is None


def test_the_derived_port_refusal_names_the_side_and_its_identifier() -> None:
    """Catches a derived output reported as an input, or the other way round."""
    wire = Rig.wire("meter", "Grown", "building", "Temperature")

    assert DerivedPort.refusal(wire, "meter", "Grown", "output").error_id is EnergySystemErrorId.UNKNOWN_OUTPUT_PORT
    refusal = DerivedPort.refusal(wire, "building", "Temperature", "input")
    assert refusal.error_id is EnergySystemErrorId.UNKNOWN_INPUT_PORT and "names the input 'building.Temperature'" in refusal.problem


def test_a_rule_reading_the_whole_plan_waits_for_the_rules_that_leave_it_incomplete() -> None:
    """Catches an open-input fault reported for an input whose feed a refused aggregator never wired."""
    wired = Rig.weather_and_building([], refusals=(Rig.refusal(EnergySystemErrorId.NO_CHANNEL_MATCH),))

    with pytest.raises(EnergySystemWiringError) as caught:
        fft.WiringRules.registry().run(RuleStage.BUILD, Rig.subject(wired), SetupKind.RESIDENTIAL)

    assert [fault.code for fault in caught.value.faults] == ["EF-28"]
    assert Skip("EF-31", ("EF-28",)) in caught.value.skipped
    assert caught.value.error_id is EnergySystemErrorId.NO_CHANNEL_MATCH


def test_a_component_that_could_not_be_constructed_holds_back_every_other_wiring_rule() -> None:
    """Catches the rules of the plan judging a system a component is missing from."""
    wired = Rig.weather_and_building([], refusals=(Rig.refusal(EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED),))

    with pytest.raises(EnergySystemWiringError) as caught:
        fft.WiringRules.registry().run(RuleStage.BUILD, Rig.subject(wired), SetupKind.DEMO)

    assert [fault.code for fault in caught.value.faults] == ["EF-33"]
    assert {skip.code for skip in caught.value.skipped} >= {"EF-21", "EF-31", "EF-7T"}


def test_a_wiring_refusal_assembles_the_catalogue_message() -> None:
    """Catches a moved check changing the message the catalogue error built."""
    refusal = WiringRefusal(
        EnergySystemErrorId.UNKNOWN_TAG, "components.meter.inputs", "'BATERY' is not a component type.",
        alternatives=("BATTERY", "PV"), alternatives_label="component types", offending_value="BATERY",
    )

    assert refusal.message == (
        "EF-2A at components.meter.inputs: 'BATERY' is not a component type. Did you mean: BATTERY? Valid component types: BATTERY, PV."
    )


def test_the_wiring_error_s_identifier_is_its_first_catalogue_fault() -> None:
    """Catches ``error_id`` naming a later fault, or answering for a violation without a catalogue fault."""
    first = ChannelMatches.fault("x")
    later = Constructed.fault("y")

    assert EnergySystemWiringError(RuleStage.BUILD, SetupKind.DEMO, [first, later], []).error_id is EnergySystemErrorId.NO_CHANNEL_MATCH
    with pytest.raises(ValueError, match="no fault of the energy-system catalogue"):
        _ = EnergySystemWiringError(RuleStage.BUILD, SetupKind.DEMO, [], []).error_id


def test_the_source_map_suffix_names_the_import_of_a_produced_component_only() -> None:
    """Catches a refusal of a component written in the file claiming an import, or an imported one losing it."""
    source_map = SourceMap()
    source_map.add("boiler-Boiler", "component", SourceMapEntry("boiler", None, "Boiler", (SourceLocation("house.yaml", 3),)))

    assert source_map.suffix("components.boiler-Boiler.inputs").startswith(" [source: boiler-Boiler (import boiler")
    assert source_map.suffix("components.weather.inputs") == ""
    assert source_map.suffix("simulation") == ""


def test_the_resolver_reads_the_declared_feeds_of_one_output_and_derives_a_feed_s_output() -> None:
    """Catches a meter landing an output on another output's declaration, or a feed's output derived from two."""
    meter = Rig.component("meter", feeds={"Standboiler": [Rig.declaration("Standboiler", "FuelUse"), Rig.declaration("Standboiler", "PilotUse")]})
    boiler = Rig.component("boiler")

    assert [declaration.source_component_field_name for declaration in DynamicConnectionResolver.declared_feeds_for(meter, boiler, "PilotUse")] == ["PilotUse"]
    assert not DynamicConnectionResolver.declared_feeds_for(meter, boiler, "FlueLoss")
    assert DynamicConnectionResolver.output_name_of(meter, boiler, Rig.feed("meter", "boiler", "FuelUse")) == "FuelUse"
    assert DynamicConnectionResolver.output_name_of(meter, boiler, Rig.feed("meter", "boiler", None)) is None


class Connectable:
    """A stand-in that records the connections made into it."""

    def __init__(self, name: str) -> None:
        """A component named ``name`` with one optional input ``Temperature`` and no connection yet."""
        self.component_name = name
        self.inputs = [Rig.input("Temperature")]
        self.outputs: List[SimpleNamespace] = []
        self.connections: List[Tuple[str, str, str]] = []

    def connect_input(self, input_fieldname: str, src_object_name: str, src_field_name: str) -> None:
        """Record one connection."""
        self.connections.append((input_fieldname, src_object_name, src_field_name))


def test_a_wired_system_connects_its_wires_in_plan_order_and_names_the_constructor_s_error() -> None:
    """Catches the application dropping or reordering a wire, or a construction refusal losing its cause."""
    building = Connectable("building")
    weather: Any = Connectable("weather")
    target: Any = building
    wired = WiredSystem(
        components=(("weather", weather), ("building", target)),
        wires=(Rig.good_wire(), Rig.wire("weather", "Humidity", "building", "Temperature")),
        resolved_feeds=(),
    )
    cause = ValueError("refused")
    failed = WiredSystem(
        components=(), wires=(), resolved_feeds=(),
        refusals=(Rig.refusal(EnergySystemErrorId.NO_CHANNEL_MATCH), WiringRefusal(EnergySystemErrorId.COMPONENT_CONSTRUCTION_FAILED, "components.x", "p", cause=cause)),
    )

    wired.apply("house")

    assert building.connections == [("Temperature", "weather", "Temperature"), ("Temperature", "weather", "Humidity")]
    assert failed.construction_cause() is cause and wired.construction_cause() is None
