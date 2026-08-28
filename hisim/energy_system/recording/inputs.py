"""Turning the wires that arrive at one component into the ``inputs`` list its entry carries.

Every connection of an energy-system file is written at its consumer and nowhere else, so the whole
job here is: given the wires arriving at one component, produce the shortest list of items that
recreates exactly those wires and no others. Three shapes are available and they are tried in that
order — a bare source name, an aggregator feed, an explicit wire — because each says less than the
one after it and the shortest true statement is the one a reader can check.

The bare item is the only one that is a *claim* rather than a transcription: writing it asserts that
the consumer's own declared default connections for the source's class produce precisely the wires
observed. So it is written only when that set comparison holds, never because the setup happened to
pass ``connect_automatically`` — a setup may ask for the defaults and then add a wire on top, and a
bare item would silently drop it on the next run.

An aggregator feed is a transcription of what the aggregator's own bookkeeping already says, minus
the port names, which the format derives from the participant and the port being measured. The
back-channel needs one more step, and it is the one place the two directions of the format meet: a
control signal is written *inside* the feed at the aggregator, so the wire it makes must not be
written a second time as an explicit item at the participant that reads it. Pairing the two is
therefore done once for the whole system, before any entry is written.
"""

# clean

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, Sequence, Set, Tuple

from hisim import loadtypes as lt
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.model import AggregatorFeed, AnyInputItem, DefaultInputs, DispatchSpec, ExplicitWire
from hisim.energy_system.parity import ResolvedWire
from hisim.energy_system.recording.names import RecordedNames
from hisim.energy_system.recording.observe import ObservedComponent, ObservedDispatch, RecordedSystem


class InputItemWriter:
    """Builds the ``inputs`` list of every component of one observed system.

    Constructed over the observation and asked once per component. It is a pure function of plain
    data — nothing it reads comes from a live component — which is what lets the three-way decision
    be tested against hand-written observations rather than only against a recorded fleet.

    The order of the produced list is bare items first, then feeds and explicit wires in the
    canonical wire order of the snapshot, so two recordings of the same setup produce the same list
    and no set iteration reaches the output.
    """

    #: Weight that marks a participant an aggregator only measures rather than controls. Such a feed
    #: never carries a back-channel, which is why the pairing stops before it looks for one. It is
    #: the resolver's own constant rather than a second copy of the number.
    MEASURED_ONLY_WEIGHT: ClassVar[int] = FeedRequest.MONITORED_ONLY_WEIGHT

    def __init__(self, recorded: RecordedSystem) -> None:
        """Prepares the writer and pairs every aggregator's back-channels with its feeds.

        Args:
            recorded: The observation, whose component index and wiring snapshot are read.
        """
        self.recorded = recorded
        self.by_name = recorded.by_name()
        self.dispatch_of: Dict[Tuple[str, str], ObservedDispatch] = {}
        self.written_at_source: Set[ResolvedWire] = set()
        self._pair_back_channels()

    def items(self, observed: ObservedComponent) -> Tuple[AnyInputItem, ...]:
        """Builds the whole ``inputs`` list of one component.

        Args:
            observed: The component whose entry is being written.

        Returns:
            Its input items.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` when a source port cannot be referenced by name.
        """
        feed_labels = set(observed.feed_labels())
        wires = [wire for wire in self.recorded.wires_into(observed.name) if wire not in self.written_at_source]
        bare = self._bare_sources(observed, [wire for wire in wires if wire.target_input not in feed_labels])
        items: List[AnyInputItem] = [DefaultInputs(source=source) for source in bare]
        for wire in wires:
            if wire.target_input in feed_labels:
                items.append(self._feed(observed, wire))
            elif wire.source_component not in bare:
                items.append(self._explicit(wire))
        return tuple(items)

    def _pair_back_channels(self) -> None:
        """Decides, once for the whole system, which feed each grown control output belongs to.

        Two matchings are needed, because the two ways a control signal can end up are different. An
        output another component reads names its consumer through the wiring, which settles the
        pairing outright, supplies the participant input to write and marks that wire as already
        described — writing it again at the participant would make the aggregator create the port
        and then find it occupied. An output nobody reads names no consumer, so it is matched on the
        participant *class* it was created for, and on the weight when a class has more than one.
        """
        for aggregator in self.recorded.components:
            if not aggregator.feeds:
                continue
            for wire in self.recorded.wires_into(aggregator.name):
                feed = next((entry for entry in aggregator.feeds if entry.port_label == wire.target_input), None)
                if feed is None:
                    continue
                dispatch = self._back_channel(aggregator, wire.source_component, feed.weight)
                if dispatch is None:
                    continue
                self.dispatch_of[(aggregator.name, feed.port_label)] = dispatch
                if dispatch.consumer is not None and dispatch.target_input is not None:
                    self.written_at_source.add(
                        ResolvedWire(
                            target_component=dispatch.consumer,
                            target_input=dispatch.target_input,
                            source_component=aggregator.name,
                            source_output=dispatch.port_label,
                        )
                    )

    def _back_channel(
        self, aggregator: ObservedComponent, participant: str, weight: int
    ) -> Optional[ObservedDispatch]:
        """Finds the control output one participant's feed carries, or reports that it has none.

        Args:
            aggregator: The aggregator, carrying its grown outputs.
            participant: Runtime name of the participant the feed comes from.
            weight: The feed's weight, used to tell two outputs of one class apart.

        Returns:
            The back-channel, or ``None``.
        """
        read = [entry for entry in aggregator.dispatches if entry.consumer == participant]
        if read:
            return self._narrowed(read, weight)
        if weight == self.MEASURED_ONLY_WEIGHT:
            return None
        producer = self.by_name.get(participant)
        participant_class = producer.class_name if producer is not None else None
        unread = [
            entry
            for entry in aggregator.dispatches
            if entry.consumer is None
            and entry.source_component_class in (None, participant_class)
        ]
        return self._narrowed(unread, weight)

    @classmethod
    def _narrowed(cls, candidates: Sequence[ObservedDispatch], weight: int) -> Optional[ObservedDispatch]:
        """Picks the one candidate of a class, using the weight to break a tie.

        A participant class an aggregator controls in two ways — space heating and hot water on one
        heat pump — has one output per way, and the feed's weight is what tells them apart. Where
        the weight does not single one out either, no back-channel is written at all rather than an
        arbitrary one: a wrong pairing would wire a control signal to the wrong participant.

        Args:
            candidates: The outputs still in the running.
            weight: The feed's weight.

        Returns:
            The single match, or ``None`` when there is none or more than one.
        """
        if len(candidates) > 1:
            candidates = [entry for entry in candidates if entry.weight == weight]
        return candidates[0] if len(candidates) == 1 else None

    def _bare_sources(self, observed: ObservedComponent, wires: Sequence[ResolvedWire]) -> List[str]:
        """Names the sources whose wires are exactly this component's declared defaults for them.

        The comparison is over the ``(target input, source output)`` pairs, which is what a default
        connection declares and what a wire realises; a source contributing one wire more or one
        wire less than its declaration produces no bare item and every one of its wires is written
        out instead. Sources are answered in the order their first wire appears, so the list is
        deterministic without being sorted.

        Args:
            observed: The consuming component, carrying its declaration table.
            wires: The wires arriving at it that did not land on a grown aggregator port.

        Returns:
            The source names that may be written bare.
        """
        arriving: Dict[str, List[Tuple[str, str]]] = {}
        for wire in wires:
            arriving.setdefault(wire.source_component, []).append((wire.target_input, wire.source_output))
        bare: List[str] = []
        for source, pairs in arriving.items():
            producer = self.by_name.get(source)
            if producer is None:
                continue
            declared = observed.default_connections.get(producer.class_name)
            if declared and len(declared) == len(pairs) and set(declared) == set(pairs):
                bare.append(source)
        return bare

    def _explicit(self, wire: ResolvedWire) -> ExplicitWire:
        """Writes one wire out in full, naming both of its ports.

        Args:
            wire: The wire to write.

        Returns:
            The explicit item.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` when the source port is not a referenceable name.
        """
        output = RecordedNames.check_source_port(wire.source_component, wire.source_output, self.recorded.setup)
        return ExplicitWire(source=wire.source_component, input=wire.target_input, output=output)

    def _feed(self, observed: ObservedComponent, wire: ResolvedWire) -> AggregatorFeed:
        """Writes one participant of an aggregator, with its back-channel where it has one.

        The tags the aggregator recorded for the feed mix the participant's kind with the flow's
        semantics; the format keeps the two apart, so they are split by member type here. The port
        the aggregator grew is not written at all: the format derives it from the participant and
        the measured output, which is exactly the pair this item states. The back-channel's tags are
        not written either, for the same reason — they are derived from the matched channel plus the
        participant's own kind, and a written set would either repeat that or contradict it.

        Args:
            observed: The aggregator, carrying its feed bookkeeping.
            wire: The wire that fills the grown input.

        Returns:
            The feed item.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` when the measured port is not referenceable.
        """
        feed = next(entry for entry in observed.feeds if entry.port_label == wire.target_input)
        output = RecordedNames.check_source_port(wire.source_component, wire.source_output, self.recorded.setup)
        component_type = next((tag for tag in feed.tags if isinstance(tag, lt.ComponentType)), None)
        dispatch = self.dispatch_of.get((observed.name, feed.port_label))
        return AggregatorFeed(
            source=wire.source_component,
            output=output,
            component_type=component_type.name if component_type is not None else None,
            tags=tuple(tag.name for tag in feed.tags if isinstance(tag, lt.InandOutputType)),
            weight=feed.weight,
            dispatch=None if dispatch is None else DispatchSpec(target_input=dispatch.target_input),
        )
