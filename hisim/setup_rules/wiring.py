"""The wiring rules of an energy-system file: what its connections must obey once its components are built.

Declarative mode only, stage ``BUILD``, subject ``BuiltDocument``: the expanded file with its constructed components,
the wires the file asks for (planned, not yet connected) and what the wiring recorded it could not do. Two kinds of
rule live here. The rules of the plan judge the planned wires and the components: a port that does not exist, two
ends that disagree on load type or unit, an input fed twice, a mandatory input left open, a consuming output of a
carrier need that its consumer lacks or that carries another carrier, a meter that cannot land a consuming output,
two aggregators counting one flow. The rules of the resolution refuse what building the components, expanding the
bare items and resolving the aggregators' feeds recorded instead of raising (``WiringRefusal``,
``hisim/energy_system/errors.py``): a constructor that raised, a bare item without declared defaults, an unknown
tag, a feed no channel takes, a broken dispatch rule, a control signal claimed twice, a derived port name taken.
The resolution itself stays in ``hisim/energy_system/`` (``feed_resolution.py``, ``channel_matching.py``,
``dispatch_signals.py``, ``aggregator_ports.py``, ``channels.py``, ``wiring.py``), which decides what it can build
and records the rest.

Every fault keeps the catalogue message the error raised before the checks moved, ``EF-nn at <location>:
<problem>``, with the source of a component an import produced, and the registry raises them together as
``EnergySystemWiringError``. Every rule requires ``EF-33``: a component that could not be constructed leaves nothing
to plan. Every rule also requires the assembly rules of the selection of the observers' feeds (``EF-7S``, ``EF-7U``,
``EF-7V``, ``hisim/setup_rules/assemblies.py``): the selection plans the feeds of meters and controllers, and a
refused selection leaves an observer without feeds or a controllable input fed by no controller or by two, which the
wiring rules would report again as an open or doubly fed input. The rules that read the whole plan, ``EF-31`` and the
logged ``EF-34``, also require every rule whose fault leaves the plan incomplete, since a refused item, feed or
aggregator contributes no wire. ``EF-34`` and ``EF-35`` only log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple, Type

from hisim import loadtypes as lt
from hisim.config.channels import PortTypeCompatibility
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError, WiringRefusal
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import BuiltDocument

if TYPE_CHECKING:
    from hisim.component import Component, ComponentInput, ComponentOutput
    from hisim.energy_system.channels import FeedRequest
    from hisim.energy_system.model import ConsumingOutput
    from hisim.energy_system.wiring import PlannedWire, WiredSystem


class WiringRule(SetupRule[BuiltDocument]):
    """The shared base of the wiring rules: the subject they read, the error they raise and how a refusal becomes a fault.

    It declares no ``CODE``, so it is no rule itself. Its rules read the built file and raise ``EnergySystemWiringError``;
    each requires ``EF-33``, since a component that could not be constructed leaves no plan to judge, and the rules of
    the selection, since a refused selection leaves the plan without the feeds it would have selected.
    """

    VIOLATION: ClassVar[Type[SetupRuleViolation]] = EnergySystemWiringError

    #: The assembly rules of the selection of the observers' feeds, which runs inside the wiring and plans feeds.
    SELECTION_RULES: ClassVar[FrozenSet[str]] = frozenset({"EF-7S", "EF-7U", "EF-7V"})

    REQUIRES: ClassVar[FrozenSet[str]] = frozenset({"EF-33"}) | SELECTION_RULES

    #: The rules whose faults leave the plan incomplete: an item, a feed or an aggregator they refuse contributes no
    #: wire, and a refused selection no feed, so a rule that reads the whole plan would report an input as open, or an
    #: output as unread, that the file does connect.
    PLAN_RULES: ClassVar[FrozenSet[str]] = (
        frozenset({"EF-21", "EF-22", "EF-23", "EF-25", "EF-27", "EF-28", "EF-29", "EF-2A", "EF-2B", "EF-30", "EF-32", "EF-33", "EF-7N"}) | SELECTION_RULES
    )

    @classmethod
    def refuse(cls, subject: BuiltDocument, refusal: WiringRefusal, port: Optional[str] = None) -> Fault:
        """Return the fault of one refusal: its catalogue message, the source of an imported component, and its location.

        Example: ``WiringRefusal(EF-26, "components.building.inputs", "the input ... is fed twice ...")`` gives a fault of
        ``component="building"``, ``key_path=("components", "building", "inputs")`` and the message ``EF-26 at
        components.building.inputs: the input ... is fed twice ...; see: hisim setup-rules show EF-26``.

        Args:
            subject: The built file, for its path and its source map.
            refusal: What the wiring or the rule found.
            port: The input or output the fault is at, when it names one.

        Returns:
            The fault.
        """
        message = refusal.message
        if subject.source_map is not None:
            message += subject.source_map.suffix(refusal.location)
        key_path = tuple(refusal.location.split("."))
        component = key_path[1] if len(key_path) > 1 and key_path[0] == "components" else None
        return cls.fault(message, component=component, port=port, key_path=key_path, file=subject.file)

    @classmethod
    def recorded(cls, subject: BuiltDocument) -> List[Fault]:
        """Return the faults of the refusals the wiring recorded under this rule's code.

        Example: in ``EF-28``, a feed whose tags match no channel of its meter, which the resolver recorded, gives one
        fault.
        """
        return [cls.refuse(subject, refusal) for refusal in subject.wired.refusals if refusal.error_id.value == cls.CODE]

    @staticmethod
    def output_of(component: Component, field_name: str) -> Optional[ComponentOutput]:
        """Return the component's output of this name, or ``None``; ``building.TemperatureOutside`` finds nothing."""
        return next((output for output in component.outputs if output.field_name == field_name), None)

    @staticmethod
    def input_of(component: Component, field_name: str) -> Optional[ComponentInput]:
        """Return the component's input of this name, or ``None``; ``building.TemperatureOutside`` finds the input."""
        return next((component_input for component_input in component.inputs if component_input.field_name == field_name), None)


class Constructed(WiringRule):
    """What it checks: every component of the file was constructed: its constructor raised nothing and returned a component.

    Why: HiSim creates a component's ports in its constructor, so nothing about a component's connections can be
    judged without it; a constructor that refuses its configuration names the entry here rather than as a bare
    traceback out of a component module.

    Example of a fault: ``EF-33 at components.heater: 'heater' (hisim.components.generic_heater.GenericHeater) could
    not be constructed: ConfigurationRefusedError(...)``.

    How to fix: change the entry's configuration so that its constructor accepts it, as the constructor's message
    says.
    """

    CODE = "EF-33"
    NAME = "constructed"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = frozenset()

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per component the wiring could not construct."""
        return self.recorded(subject)


class OutputExists(WiringRule):
    """What it checks: every output a wire, a feed or a consuming output names exists, and a written wire names no derived one.

    Why: a wire to an output a component does not declare is a renamed port or one its configuration switched off;
    a written wire into a port that feed resolution derived names a port the file does not contain, whose name is
    no part of what an author writes; a feed that names no output must find exactly one declared default to take
    it from; an aggregator must create the dispatch output its derived name promises.

    Example of a fault: ``EF-21 at components.building.inputs: the connection 'weather.TemperatureOutsid' ->
    'building.TemperatureOutside' (...) names the output 'TemperatureOutsid', which 'weather' (...) does not
    declare. Did you mean: TemperatureOutside?``.

    How to fix: name an output the source declares (the message lists them), or write the feed's output as the
    dotted half of its ``from``; reach a derived port through the feed's ``dispatch`` block.
    """

    CODE = "EF-21"
    NAME = "output-exists"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per missing or derived output a wire names, per missing consuming output, and the recorded ones."""
        wired = subject.wired
        components = dict(wired.components)
        faults = self.recorded(subject)
        for wire in wired.wires[: wired.written_wire_count]:
            if wire.source_output in wired.created_ports.get(wire.source_name, ()):
                faults.append(self.refuse(subject, DerivedPort.refusal(wire, wire.source_name, wire.source_output, "output"), wire.source_output))
        for wire in wired.wires:
            source = components[wire.source_name]
            if self.output_of(source, wire.source_output) is None:
                refusal = WiringRefusal(
                    EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                    f"components.{wire.target_name}.inputs",
                    f"the connection {wire.describe()} names the output '{wire.source_output}', "
                    f"which '{wire.source_name}' ({source.get_full_classname()}) does not "
                    "declare.",
                    alternatives=tuple(port.field_name for port in source.outputs),
                    alternatives_label="outputs",
                    offending_value=wire.source_output,
                )
                faults.append(self.refuse(subject, refusal, wire.source_output))
        for item in wired.consuming:
            consumer = components[item.consumer]
            if self.output_of(consumer, item.output) is None:
                refusal = WiringRefusal(
                    EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                    f"components.{item.consumer}",
                    f"the carrier need names the consuming output '{item.consumer}.{item.output}', which "
                    f"{consumer.get_classname()} does not have.",
                    alternatives=tuple(port.field_name for port in consumer.outputs),
                    alternatives_label="outputs",
                )
                faults.append(self.refuse(subject, refusal, item.output))
        return faults


class InputExists(WiringRule):
    """What it checks: every input a wire names exists, a written wire names no derived one, and an aggregator creates the inputs its feeds promise.

    Why: a wire into an input a component does not declare cannot be connected; a derived input is not in the file
    an author reads; an aggregator whose resolution hook invents its own names breaks the naming templates that
    result files and lookups quote.

    Example of a fault: ``EF-22 at components.building.inputs: the connection 'weather.TemperatureOutside' ->
    'building.TemperatureOutsid' (...) names the input 'TemperatureOutsid', which 'building' (...) does not
    declare.``.

    How to fix: name an input the target declares (the message lists them); a feed's input is derived, so write a
    feed instead of a wire into it.
    """

    CODE = "EF-22"
    NAME = "input-exists"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per missing or derived input a wire names, and the inputs an aggregator did not create."""
        wired = subject.wired
        components = dict(wired.components)
        faults = self.recorded(subject)
        for wire in wired.wires[: wired.written_wire_count]:
            if wire.target_input in wired.created_ports.get(wire.target_name, ()):
                faults.append(self.refuse(subject, DerivedPort.refusal(wire, wire.target_name, wire.target_input, "input"), wire.target_input))
        for wire in wired.wires:
            target = components[wire.target_name]
            if self.input_of(target, wire.target_input) is None:
                refusal = WiringRefusal(
                    EnergySystemErrorId.UNKNOWN_INPUT_PORT,
                    f"components.{wire.target_name}.inputs",
                    f"the connection {wire.describe()} names the input '{wire.target_input}', "
                    f"which '{wire.target_name}' ({target.get_full_classname()}) does not "
                    "declare.",
                    alternatives=tuple(port.field_name for port in target.inputs),
                    alternatives_label="inputs",
                    offending_value=wire.target_input,
                )
                faults.append(self.refuse(subject, refusal, wire.target_input))
        return faults


class DerivedPort:
    """The refusal of a written wire that names a port feed resolution derived, shared by ``EF-21`` and ``EF-22``."""

    @staticmethod
    def refusal(wire: PlannedWire, name: str, port: str, side: str) -> WiringRefusal:
        """Return the refusal of a written wire naming the derived ``side`` (``output`` or ``input``) ``name.port``.

        Example: a written wire into ``meter.ElectricalPowerConsumptionFromoccupancy``, which the meter grew for a feed,
        gives ``EF-22`` saying the file does not contain that input.
        """
        error_id = EnergySystemErrorId.UNKNOWN_OUTPUT_PORT if side == "output" else EnergySystemErrorId.UNKNOWN_INPUT_PORT
        return WiringRefusal(
            error_id,
            f"components.{wire.target_name}.inputs",
            f"the connection {wire.describe()} names the {side} "
            f"'{name}.{port}', which the file does not contain: it is created by "
            "resolving that aggregator's feeds.",
            remedy=(
                "A written wire may only name a port a component declares itself; "
                "use the feed's 'dispatch' block for the back-channel."
            ),
        )


class DefaultsDeclared(WiringRule):
    """What it checks: a bare input item finds declared defaults for its source's class at its target: static default connections or default feeds, not both.

    Why: a bare item means "connect what the target declares for this source", which has no answer when the target
    declares nothing for the class, and two answers when it declares both kinds.

    Example of a fault: ``EF-23 at components.meter.inputs: the bare item 'weather' of 'meter' cannot be expanded:
    'meter' (...) declares no default connections for the class 'Weather'. Valid source classes: ...``.

    How to fix: write the connection as an explicit ``{input, from}`` item, or declare the default in the consuming
    component's class.
    """

    CODE = "EF-23"
    NAME = "defaults-declared"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per bare item the planner could not expand."""
        return self.recorded(subject)


class FeedUnique(WiringRule):
    """What it checks: one participant output enters an aggregator once, after bare items are expanded.

    Why: a derived port name is made from the participant and its output, without a counter, and a flow fed twice
    would be counted twice; the duplicate that matters is one a bare item's expansion adds to a file that looks fine.

    Example of a fault: ``EF-25 at components.meter.inputs: 'meter' measures 'occupancy.ElectricalPowerConsumption'
    twice: via ... and via ....``.

    How to fix: remove one of the two items feeding the output.
    """

    CODE = "EF-25"
    NAME = "feed-unique"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per participant output an aggregator would measure twice."""
        return self.recorded(subject)


class NoInputFedTwice(WiringRule):
    """What it checks: after bare items are expanded and feeds resolved, every input is fed by one wire at most.

    Why: an input holds one source; two wires into it would let the last one win silently. The structural rules
    catch the duplicates written in the file, this one those an expansion adds.

    Example of a fault: ``EF-26 at components.building.inputs: the input 'building.TemperatureOutside' is fed twice:
    by ... and by ....``.

    How to fix: every input takes exactly one source; remove one of the two items.
    """

    CODE = "EF-26"
    NAME = "no-input-fed-twice"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per wire into an input an earlier wire already feeds."""
        faults: List[Fault] = []
        seen: Dict[Tuple[str, str], PlannedWire] = {}
        for wire in subject.wired.wires:
            key = (wire.target_name, wire.target_input)
            previous = seen.get(key)
            if previous is None:
                seen[key] = wire
                continue
            refusal = WiringRefusal(
                EnergySystemErrorId.DUPLICATE_WIRE,
                f"components.{wire.target_name}.inputs",
                f"the input '{wire.target_name}.{wire.target_input}' is fed twice: by "
                f"{previous.describe()} and by {wire.describe()}.",
                remedy="Every input takes exactly one source; remove one of the two items.",
            )
            faults.append(self.refuse(subject, refusal, wire.target_input))
        return faults


class TargetIsAnAggregator(WiringRule):
    """What it checks: feeds are addressed only at a component that declares dynamic-connection channels and resolves them.

    Why: a component without channels cannot classify a participant, so a feed addressed at it has no meaning.

    Example of a fault: ``EF-27 at components.building.inputs: 'building' (...) is not an aggregator — it declares no
    dynamic-connection channels — so the 1 feed(s) addressed at it cannot be applied.``.

    How to fix: wire the participants with explicit ``{input, from}`` items, or address them at a component that
    aggregates.
    """

    CODE = "EF-27"
    NAME = "target-is-an-aggregator"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per component that was addressed with feeds and is no aggregator."""
        return self.recorded(subject)


class ChannelMatches(WiringRule):
    """What it checks: a feed's tags select exactly one channel of its aggregator, the most specific one whose tags they contain.

    Why: the channel decides what the aggregator does with the participant; no channel leaves the feed meaningless, and
    two equally specific channels leave it undecidable, a defect of the aggregator's declaration.

    Example of a fault: ``EF-28 at components.meter.inputs: the feed 'occupancy.ElectricalPowerConsumption' -> 'meter'
    (weight 999) carries the tags [...], which match no channel of the aggregator 'meter' (...). Valid channels: ...``.

    How to fix: give the feed the tags of one channel (the message lists them), or make the aggregator's channel tag
    sets nested.
    """

    CODE = "EF-28"
    NAME = "channel-matches"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per feed that matches no channel, or two."""
        return self.recorded(subject)


class DispatchRuleHolds(WiringRule):
    """What it checks: a dispatch block where and only where the channel dispatches, a rank only where it may, dispatch tags as the channel's.

    A feed carries a dispatch block exactly when its channel dispatches; a rank other than the monitored-only weight only on
    a channel that may dispatch; and written dispatch tags equal to the channel's.

    Why: a channel that never dispatches can only measure, so its participant needs the reserved monitored-only
    weight and no dispatch block; a channel that always dispatches needs a rank and an output to write to; written
    dispatch tags document the channel's and may not override them.

    Example of a fault: ``EF-29 at components.ems.inputs: the feed 'battery.AcBatteryPowerUsed' -> 'ems' (weight 6) has
    no dispatch block, but the channel ... of the aggregator 'ems' (...) dispatches to every participant on it and
    needs an output to write to.``.

    How to fix: add or remove the dispatch block or the rank as the channel requires; omit ``dispatch.tags`` to inherit
    the channel's.
    """

    CODE = "EF-29"
    NAME = "dispatch-rule"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per broken condition of a feed's channel dispatch rule."""
        return self.recorded(subject)


class KnownTag(WiringRule):
    """What it checks: every component type and flow tag a feed or a dispatch block names is a member of its enumeration.

    Why: a misspelled tag would reach channel matching and look like a mismatch; named where it is written, with the
    vocabulary listed, it is a one-line fix.

    Example of a fault: ``EF-2A at components.meter.inputs: 'ELECTRICITY_CONSUMPTION_UNCONTROLLD' is not a flow tag.
    Did you mean: ELECTRICITY_CONSUMPTION_UNCONTROLLED? Valid flow tags: ...``.

    How to fix: write a tag the message lists; a component type goes under ``component_type``, not under ``tags``.
    """

    CODE = "EF-2A"
    NAME = "known-tag"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per tag name no enumeration knows."""
        return self.recorded(subject)


class DispatchSignalUnique(WiringRule):
    """What it checks: no two feeds of an aggregator claim one control signal (tags and weight), and no claim matches a signal the aggregator already publishes.

    Why: an aggregator finds a participant's signal by its tags and weight, not by name; two answers to one search
    pair the participants with the wrong ports, and the run stays plausible while a device is never commanded.

    Example of a fault: ``EF-2B at components.ems.inputs: the dispatch block of ... claims the control signal tagged
    ['BATTERY', 'ELECTRICITY_TARGET'] at weight 6, which an earlier feed of 'ems' already claims through '...'.``.

    How to fix: rank the participants at different weights, or drop the port the aggregator publishes.
    """

    CODE = "EF-2B"
    NAME = "dispatch-signal-unique"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per control signal claimed twice or already published."""
        return self.recorded(subject)


class PortTypesAgree(WiringRule):
    """What it checks: both ends of a wire, and a feed and its channel, agree on load type and unit; an aggregator takes its feeds' carriers.

    The wildcard ``ANY`` agrees with everything.

    Why: the simulator would add watts to degrees without a complaint; a meter priced for one fuel would bill another
    at its price.

    Example of a fault: ``EF-30 at components.building.inputs: unit mismatch on the connection ... : the output is in
    'W' but the input expects '°C'.``.

    How to fix: wire a pair of ports of one load type and unit, or feed the aggregator a carrier it is configured for.
    """

    CODE = "EF-30"
    NAME = "port-types-agree"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per wire whose existing ends disagree on load type or unit, and the recorded feed mismatches."""
        components = dict(subject.wired.components)
        faults = self.recorded(subject)
        for wire in subject.wired.wires:
            output = self.output_of(components[wire.source_name], wire.source_output)
            target_input = self.input_of(components[wire.target_name], wire.target_input)
            if output is None or target_input is None:
                continue
            location = f"components.{wire.target_name}.inputs"
            remedy = "Align the two port declarations, or wire a different pair of ports."
            if not PortTypeCompatibility.load_types_agree(output.load_type, target_input.loadtype):
                problem = (
                    f"load type mismatch on the connection {wire.describe()}: the output "
                    f"carries '{output.load_type.name}' but the input expects "
                    f"'{target_input.loadtype.name}'."
                )
                faults.append(self.refuse(subject, WiringRefusal(EnergySystemErrorId.PORT_TYPE_MISMATCH, location, problem, remedy=remedy), wire.target_input))
            if not PortTypeCompatibility.units_agree(output.unit, target_input.unit):
                problem = (
                    f"unit mismatch on the connection {wire.describe()}: the output is in "
                    f"'{output.unit.value}' but the input expects '{target_input.unit.value}'."
                )
                faults.append(self.refuse(subject, WiringRefusal(EnergySystemErrorId.PORT_TYPE_MISMATCH, location, problem, remedy=remedy), wire.target_input))
        return faults


class MandatoryInputFed(WiringRule):
    """What it checks: every mandatory input of every component is fed by a wire, unless its component declares that it may stay open.

    The component-build rule ``CB-06`` (``mandatory-input-connected``) asks the same of a simulator's connected inputs
    in either mode; this one asks it of the file's planned wires, before anything is connected, naming the file's items.

    Why: a half-wired system that starts and produces numbers is the failure the format exists to remove; a port
    whose source may legitimately be absent (a heat pump's hot-water input with hot-water preparation off) says so
    and is only reported in the log.

    Example of a fault: ``EF-31 at components.building.inputs: the mandatory input 'building.TemperatureOutside'
    (...) is connected by no item of the energy system 'house'.``.

    How to fix: add an input item feeding it, or declare the port as allowed to stay open if its source may
    legitimately be absent.
    """

    CODE = "EF-31"
    NAME = "mandatory-input-fed"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    REQUIRES: ClassVar[FrozenSet[str]] = WiringRule.PLAN_RULES

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per mandatory input no wire feeds and its component does not allow to stay open."""
        connected = {(wire.target_name, wire.target_input) for wire in subject.wired.wires}
        faults: List[Fault] = []
        for name, component in subject.wired.components:
            for component_input in component.inputs:
                if not component_input.is_mandatory or component_input.allow_unconnected_mandatory:
                    continue
                if (name, component_input.field_name) in connected:
                    continue
                refusal = WiringRefusal(
                    EnergySystemErrorId.UNCONNECTED_MANDATORY_INPUT,
                    f"components.{name}.inputs",
                    f"the mandatory input '{name}.{component_input.field_name}' "
                    f"({component.get_full_classname()}) is connected by no item of the energy "
                    f"system '{subject.model.name}'.",
                    remedy=(
                        "Add an input item feeding it, or declare the port as allowed to stay "
                        "open if its source may legitimately be absent."
                    ),
                )
                faults.append(self.refuse(subject, refusal, component_input.field_name))
        return faults


class PortNameFree(WiringRule):
    """What it checks: the ports feed resolution would create on an aggregator are named like none it has.

    Why: a derived port name carries no counter, so that it is stable across runs and quotable in a stored analysis;
    a collision has nowhere to go and would shadow or overwrite a declared port.

    Example of a fault: ``EF-32 at components.meter.inputs: resolving ... would create the port
    'ElectricalPowerConsumptionFromoccupancy' on 'meter', which already has a port of that name.``.

    How to fix: rename the participant or the output it is measured on.
    """

    CODE = "EF-32"
    NAME = "port-name-free"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per derived port name an aggregator already uses."""
        return self.recorded(subject)


class ConsumingOutputCarrier(WiringRule):
    """What it checks: every consuming output a carrier need names has an energy port of the need's carrier.

    Why: a carrier need states what a member consumes and no wire shows it; an output of another carrier, or without
    an energy port, would be billed and balanced as a carrier it does not take in.

    Example of a fault: ``EF-7M at components.boiler-Boiler: the output 'boiler-Boiler.FuelUse' is consumed as
    heating_oil, but its energy port carries natural_gas.``.

    How to fix: name the output of the need's carrier in the assembly's need, or correct the need's carrier.
    """

    CODE = "EF-7M"
    NAME = "consuming-output-carrier"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per existing consuming output whose energy port carries another carrier, or none."""
        components = dict(subject.wired.components)
        faults: List[Fault] = []
        for item in subject.wired.consuming:
            output = self.output_of(components[item.consumer], item.output)
            if output is None:
                continue
            carrier = output.energy_port.carrier.value if output.energy_port is not None else None
            if carrier == item.carrier:
                continue
            refusal = WiringRefusal(
                EnergySystemErrorId.CARRIER_MISMATCH,
                f"components.{item.consumer}",
                f"the output '{item.consumer}.{item.output}' is consumed as {item.carrier}, but "
                + (f"its energy port carries {carrier}." if carrier else "it declares no energy port."),
            )
            faults.append(self.refuse(subject, refusal, item.output))
        return faults


class MeterFeedCount(WiringRule):
    """What it checks: the meter of every metered consuming output declares exactly one feed for it in its class.

    Why: a consuming output lands at its meter as the feed the meter's class declares for it (D30 of the assemblies
    spec); with none it lands nowhere and its carrier is not counted, with several which one it lands as is not
    determined.

    Example of a fault: ``EF-7N at components.supply-Meter.inputs: the carrier need names the consuming output
    'boiler-Boiler.FlueLoss', but the meter 'supply-Meter' (MockGasMeter) declares no feed for it; of MockBoiler it
    declares feeds for FuelUse.``.

    How to fix: name only outputs the provider's meter declares exactly one feed for, or declare it once in the
    meter's class.
    """

    CODE = "EF-7N"
    NAME = "meter-feed-count"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per metered consuming output its meter declares no feed or several feeds for."""
        components = dict(subject.wired.components)
        faults: List[Fault] = []
        for item in subject.wired.consuming:
            if item.meter is None:
                continue
            refusal = self.refusal_of(components[item.meter], components[item.consumer], item)
            if refusal is not None:
                faults.append(self.refuse(subject, refusal, item.output))
        return faults

    @staticmethod
    def refusal_of(meter: Component, consumer: Component, item: ConsumingOutput) -> Optional[WiringRefusal]:
        """Return the refusal of a consuming output its meter declares no feed or several feeds for, or ``None``.

        Example: the gas meter declares one feed for ``GenericBoiler.EnergyDemandSh``, so that output gives ``None``; a
        flue loss it declares nothing for gives ``EF-7N`` listing the outputs it does declare feeds for.

        Args:
            meter: The meter the output lands at.
            consumer: The component the output belongs to.
            item: The consuming output, with its meter.

        Returns:
            The refusal, or ``None`` when the meter declares exactly one feed for the output.
        """
        match = DynamicConnectionResolver.declared_feeds_for(meter, consumer, item.output)
        if len(match) == 1:
            return None
        declared = DynamicConnectionResolver.default_feeds_of(meter, consumer)
        fed = ", ".join(str(declaration.source_component_field_name) for declaration in declared) or "nothing"
        problem = (
            f"declares {len(match)} feeds for it, so the one it lands as is not determined"
            if match
            else f"declares no feed for it; of {consumer.get_classname()} it declares feeds for {fed}"
        )
        return WiringRefusal(
            EnergySystemErrorId.METER_FEEDS,
            f"components.{item.meter}.inputs",
            f"the carrier need names the consuming output '{item.consumer}.{item.output}', but the meter "
            f"'{item.meter}' ({meter.get_classname()}) {problem}.",
            remedy=(
                "Name only outputs the provider's meter declares exactly one feed for, or declare it once in the "
                "meter's class."
            ),
        )


class NoDoubleCount(WiringRule):
    """What it checks: no aggregator reads an output of another aggregator while both observe one flow.

    Why: an energy manager sums what it observes into a balance; a meter that reads that balance and one of the
    flows behind it counts the flow twice. Every planned feed counts, written, expanded from a bare name or
    selected, each by the output it resolves to.

    Example of a fault: ``EF-7T at components.meter.inputs: 'meter' reads an output of 'ems' and both observe
    pv.ElectricityOutput: the flow would be counted twice.``.

    How to fix: let the aggregator read only the other one's output, or only the flows.
    """

    CODE = "EF-7T"
    NAME = "no-double-count"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per pair of aggregators of which one reads the other while both observe a flow."""
        components = dict(subject.wired.components)
        observed = {name: self.observed_flows(components, feeds) for name, feeds in subject.wired.feeds_by_target.items()}
        faults: List[Fault] = []
        for name, feeds in subject.wired.feeds_by_target.items():
            for other in sorted({feed.source for feed in feeds} & set(observed) - {name}):
                both = sorted(f"{source}.{output}" for source, output in observed[name] & observed[other])
                if not both:
                    continue
                refusal = WiringRefusal(
                    EnergySystemErrorId.DOUBLE_COUNT,
                    f"components.{name}.inputs",
                    f"'{name}' reads an output of '{other}' and both observe {', '.join(both)}: the flow would be "
                    "counted twice.",
                    remedy=f"Let '{name}' read only the output of '{other}', or only the flows.",
                )
                faults.append(self.refuse(subject, refusal))
        return faults

    @staticmethod
    def observed_flows(components: Mapping[str, Component], feeds: Sequence[FeedRequest]) -> Set[Tuple[str, str]]:
        """Return the ``(source, output)`` pairs an aggregator's feeds observe, each feed by the output it resolves to.

        Example: a feed ``from: battery`` into an energy manager that declares one feed for the battery's class
        observes ``("battery", <that feed's output>)``. A feed whose output cannot be derived observes nothing here;
        the rule ``EF-21`` refuses it.

        Args:
            components: Every constructed component by its name in the file.
            feeds: The planned feeds of one aggregator.

        Returns:
            The observed flows.
        """
        flows: Set[Tuple[str, str]] = set()
        for feed in feeds:
            output = DynamicConnectionResolver.output_name_of(components[feed.consumer], components[feed.source], feed)
            if output is not None:
                flows.add((feed.source, output))
        return flows


class UnconsumedSource(WiringRule):
    """What it checks: every component feeds some other component; one that feeds nobody is reported in the log.

    Why: a component nobody reads is legal and sometimes intended (a meter measuring a system it does not feed back
    into), but the far more common cause is a forgotten input item, which otherwise shows only as a suspiciously
    flat result curve.

    Example of a fault: ``'meter' feeds no other component of this energy system; if that is not intended, an input
    item naming it is missing somewhere.``.

    How to fix: add the input item that reads the component, or leave it if nothing is meant to read it.
    """

    CODE = "EF-34"
    NAME = "unconsumed-source"
    STAGE = RuleStage.BUILD
    ENFORCED = False
    REQUIRES: ClassVar[FrozenSet[str]] = WiringRule.PLAN_RULES

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per component no wire reads, in file order."""
        consumed = {wire.source_name for wire in subject.wired.wires}
        return [
            self.fault(self.message_for(name), component=name, key_path=("components", name), file=subject.file)
            for name, _ in subject.wired.components
            if name not in consumed
        ]

    @staticmethod
    def message_for(name: str) -> str:
        """Return the warning about one component nothing reads, ``'meter' feeds no other component ...``."""
        return (
            f"'{name}' feeds no other component of this energy system; if that is not intended, "
            "an input item naming it is missing somewhere."
        )

    @classmethod
    def lines(cls, wired: WiredSystem) -> Tuple[str, ...]:
        """Return the warning of every component of a wired system that no wire reads, in file order, as the run record keeps them.

        Example: a meter that measures an occupancy and feeds nothing gives one line naming ``'meter'``.
        """
        consumed = {wire.source_name for wire in wired.wires}
        return tuple(cls.message_for(name) for name, _ in wired.components if name not in consumed)


class UnmeteredOutputOfANeed(WiringRule):
    """What it checks: a metered consumer's outputs that take the metered carrier in are all named in its carrier need; one left out is reported in the log.

    Why: a carrier need may name only some of its consumer's outputs, and only the named ones land at the meter; an
    output of the same carrier left out is counted by no meter, and the carrier's consumption undercounts without an
    error. Leaving one out can be intended.

    Example of a fault: ``the carrier need of 'burner' names FuelSh, FuelDhw but not FuelPilot, which also take
    natural_gas in; the meter 'gas' does not count them, so the natural_gas consumption undercounts. ...``.

    How to fix: name the output in the need, unless leaving it out is intended.
    """

    CODE = "EF-35"
    NAME = "unmetered-output-of-a-need"
    STAGE = RuleStage.BUILD
    ENFORCED = False

    def check(self, subject: BuiltDocument) -> Sequence[Fault]:
        """Return a fault per metered consumer and carrier with an output of that carrier the need leaves out."""
        components = dict(subject.wired.components)
        named: Dict[Tuple[str, str], List[ConsumingOutput]] = {}
        for item in subject.wired.consuming:
            if item.meter is not None:
                named.setdefault((item.consumer, item.carrier), []).append(item)
        faults: List[Fault] = []
        for (consumer, carrier), items in named.items():
            named_outputs = [item.output for item in items]
            left_out = [
                output.field_name
                for output in components[consumer].outputs
                if output.energy_port is not None
                and output.energy_port.role == lt.EnergyRole.CONSUMPTION
                and output.energy_port.carrier.value == carrier
                and output.field_name not in named_outputs
            ]
            if not left_out:
                continue
            meters = ", ".join(sorted({str(item.meter) for item in items}))
            message = (
                f"the carrier need of '{consumer}' names {', '.join(named_outputs)} but not "
                f"{', '.join(left_out)}, which also take {carrier} in; the meter '{meters}' does not count them, "
                f"so the {carrier} consumption undercounts. Name them in the need unless leaving them out is intended."
            )
            faults.append(self.fault(message, component=consumer, key_path=("components", consumer), file=subject.file))
        return faults
