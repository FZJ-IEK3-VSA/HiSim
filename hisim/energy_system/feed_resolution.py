"""Resolving an aggregator's feeds into the ports it creates and the wires that fill them.

An aggregating component grows its inputs from the participants a file hands it. This module
performs that growth: it classifies every feed against the aggregator's declared channels, asks
the aggregator to create one input per feed and one output per dispatch block, and reports the
field-level wires that have to be made afterwards. Creating the ports and connecting them are
deliberately two steps, so that a wire born from a feed passes through exactly the same final
checks as a wire an author wrote by hand.

The order is deterministic: a target's feeds are sorted by weight, then participant, then output,
so the resulting wires — and with them the column order of a result frame — depend only on what
the file says and never on the order its lines happen to be written in. The derived port names
the sorted feeds produce are defined on the resolved connection itself, next door in
:mod:`hisim.energy_system.resolution`.

Between the sorting and the creation sits one decision this module delegates rather than makes:
which port each dispatch block's control signal comes out of. An aggregator often already publishes
the signal a feed asks for, and growing a second one would leave it unable to tell the two apart, so
:mod:`hisim.energy_system.dispatch_signals` settles that per feed before any port is created.

The module reaches into components by duck typing over two well-known names — a ``CHANNELS``
class attribute and a ``resolve_dynamic_connections`` method — and imports no component module
at all, which is what keeps a component free to import the resolved-connection record without
closing an import cycle.
"""

# clean

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from hisim import loadtypes as lt
from hisim.energy_system.aggregator_ports import AggregatorPortChecker
from hisim.energy_system.channel_matching import ChannelMatcher
from hisim.energy_system.channels import DynamicConnectionChannel, FeedRequest
from hisim.energy_system.dispatch_signals import DispatchSignalPlanner
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError
from hisim.energy_system.resolution import (
    ResolvedDispatch,
    ResolvedDynamicConnection,
    ResolvedDynamicWire,
)

if TYPE_CHECKING:  # pragma: no cover - for type checking only, never imported at runtime
    from hisim.component import Component, ComponentOutput


class DynamicConnectionResolver:
    """Resolves the feeds of one energy system against the aggregators they are addressed at.

    One resolver serves one build. It is constructed with the system's components, is handed the
    feeds grouped by the aggregator they address, and returns the wires that have to be made.
    Along the way it raises every wiring error that concerns a feed — a target that is not an
    aggregator, a participant port that does not exist, a feed matching no channel, two feeds
    measuring the same port, a derived name colliding with an existing port — and it records
    which ports it created, so that the wiring stage can reject an explicit wire reaching into
    one of them.

    Aggregator capability is discovered by duck typing over two well-known names rather than by
    a type test: a component that declares channels and implements the resolution hook is an
    aggregator, whatever it inherits from.
    """

    #: Class attribute through which a component publishes its channels. A component without it
    #: accepts no feeds at all.
    CHANNELS_ATTRIBUTE: ClassVar[str] = "CHANNELS"

    #: Attribute holding a dynamic component's declared default feeds, keyed by the class name
    #: of the participant. It is filled by the component's own constructor.
    DEFAULT_FEEDS_ATTRIBUTE: ClassVar[str] = "dynamic_default_connections"

    #: Method a component implements to create the ports of its resolved feeds.
    RESOLUTION_HOOK: ClassVar[str] = "resolve_dynamic_connections"

    def __init__(self, components_by_name: Mapping[str, "Component"]) -> None:
        """Prepares a resolver for one system's already constructed components.

        Args:
            components_by_name: Every component of the system, keyed by its name in the file.
                The resolver reads participants and targets from here and never adds to it.
        """
        self.components_by_name: Dict[str, "Component"] = dict(components_by_name)
        self.resolved_by_target: Dict[str, List[ResolvedDynamicConnection]] = {}
        self.created_ports: Dict[str, List[str]] = {}

    @classmethod
    def channels_of(cls, component: Any) -> Tuple[DynamicConnectionChannel, ...]:
        """Reads a component's declared channels, if it declares any.

        Args:
            component: The component addressed as an aggregator.

        Returns:
            The declared channels, or an empty tuple when the component declares none.
        """
        return tuple(getattr(component, cls.CHANNELS_ATTRIBUTE, ()) or ())

    @classmethod
    def is_aggregator(cls, component: Any) -> bool:
        """Whether a component can accept feeds at all.

        Args:
            component: The component to test.

        Returns:
            ``True`` when it declares channels and implements the resolution hook.
        """
        return bool(cls.channels_of(component)) and callable(
            getattr(component, cls.RESOLUTION_HOOK, None)
        )

    @classmethod
    def default_feeds_of(cls, target: Any, source_class_name: str) -> Tuple[Any, ...]:
        """Reads an aggregator's declared default feeds for one participant class.

        The lookup is per aggregator and participant class, exactly like the static default
        connection registry it parallels, and returns several because one bare item can
        legitimately expand into more than one feed — an energy management system declares a
        space-heating and a hot-water feed for the same heat pump class.

        Args:
            target: The aggregator the bare item is addressed at.
            source_class_name: Class name of the participant.

        Returns:
            The declarations for that class, in declaration order, or an empty tuple.
        """
        declared = getattr(target, cls.DEFAULT_FEEDS_ATTRIBUTE, None)
        if not isinstance(declared, Mapping):
            return ()
        return tuple(declared.get(source_class_name, ()) or ())

    @classmethod
    def feed_from_declaration(
        cls, declaration: Any, consumer: str, source: str
    ) -> FeedRequest:
        """Turns one declared default feed into the same request a written feed produces.

        Expanding a declaration into a real feed request rather than into some private shortcut
        is what guarantees that a bare item and the feed an author would have written by hand
        behave identically: from here on, channel matching, dispatch validation and the naming
        templates cannot tell the two apart.

        The declaration keeps the participant's kind and the flow's semantics in one tag list,
        so they are split back apart here by member type.

        Args:
            declaration: The component's own default-feed record.
            consumer: Name of the aggregator in the file.
            source: Name of the participant in the file.

        Returns:
            The equivalent feed request.
        """
        tags = list(getattr(declaration, "source_tags", ()) or ())
        component_type = next((tag for tag in tags if isinstance(tag, lt.ComponentType)), None)
        flow_tags = tuple(tag for tag in tags if isinstance(tag, lt.InandOutputType))
        return FeedRequest(
            consumer=consumer,
            source=source,
            output=declaration.source_component_field_name,
            component_type=component_type,
            flow_tags=flow_tags,
            weight=declaration.source_weight,
            origin=f"the declared default feed of '{consumer}' for class "
            f"'{declaration.source_class_name}'",
        )

    def expand_default_item(self, consumer: str, source: str) -> List[FeedRequest]:
        """Expands a bare input item addressed at an aggregator into its declared feeds.

        Args:
            consumer: Name of the aggregator.
            source: Name of the participant the bare item names.

        Returns:
            One feed request per declaration, in declaration order.
        """
        target = self.components_by_name[consumer]
        participant = self.components_by_name[source]
        return [
            self.feed_from_declaration(declaration, consumer, source)
            for declaration in self.default_feeds_of(target, participant.get_classname())
        ]

    def resolve_all(
        self, feeds_by_target: Mapping[str, Sequence[FeedRequest]]
    ) -> List[ResolvedDynamicWire]:
        """Resolves every aggregator's feeds and returns the wires that have to be made.

        Targets are processed in sorted name order and their feeds in the deterministic order
        described in the module docstring, so the resulting wire list depends only on the
        system's content.

        Args:
            feeds_by_target: The feeds of the system, grouped by the aggregator addressed.

        Returns:
            The forward and dispatch wires of every resolved feed, target by target.

        Raises:
            EnergySystemWiringError: On any feed condition of the error catalogue.
        """
        wires: List[ResolvedDynamicWire] = []
        for target_name in sorted(feeds_by_target):
            resolved = self.resolve_target(target_name, feeds_by_target[target_name])
            wires.extend(self.wires_for(resolved))
        return wires

    def resolve_target(
        self, target_name: str, feeds: Sequence[FeedRequest]
    ) -> List[ResolvedDynamicConnection]:
        """Resolves the feeds addressed at one aggregator and creates its ports.

        Args:
            target_name: Name of the aggregator in the file.
            feeds: The feeds addressed at it.

        Returns:
            The resolved connections in deterministic order, each dispatching one carrying the
            control port it was assigned.

        Raises:
            EnergySystemWiringError: On any feed condition of the error catalogue.
        """
        target = self.components_by_name[target_name]
        channels = self._require_channels(target_name, target, len(feeds))
        resolved = [self._resolve_feed(target_name, target, channels, feed) for feed in feeds]
        AggregatorPortChecker.check_participant_ports_are_unique(target_name, resolved)
        resolved.sort(key=lambda connection: connection.sort_key())
        resolved = DispatchSignalPlanner(target_name, target).plan(resolved)
        created = AggregatorPortChecker.check_port_names_are_free(target_name, target, resolved)
        getattr(target, self.RESOLUTION_HOOK)(list(resolved))
        AggregatorPortChecker.check_ports_were_created(target_name, target, resolved)
        self.resolved_by_target[target_name] = resolved
        self.created_ports.setdefault(target_name, []).extend(created)
        return resolved

    @classmethod
    def wires_for(
        cls, resolved: Sequence[ResolvedDynamicConnection]
    ) -> List[ResolvedDynamicWire]:
        """Builds the forward and back wires of one aggregator's resolved connections.

        Every connection contributes the forward wire from the participant's measured output
        into the created aggregator input. A dispatch block naming a target input contributes
        the back wire as well; one without a target input contributes no wire, because its
        output is read by postprocessing rather than by another component.

        Args:
            resolved: The resolved connections of one aggregator, in deterministic order.

        Returns:
            The wires, forward before back per connection.
        """
        wires: List[ResolvedDynamicWire] = []
        for connection in resolved:
            wires.append(
                ResolvedDynamicWire(
                    source_name=connection.source_name,
                    source_output=connection.source_output,
                    target_name=connection.target_name,
                    target_input=connection.aggregator_input_name,
                    origin=connection.origin,
                )
            )
            dispatch_name = connection.dispatch_output_name
            if connection.dispatch is None or connection.dispatch.target_input is None:
                continue
            assert dispatch_name is not None  # nosec - a dispatch block always derives a name
            wires.append(
                ResolvedDynamicWire(
                    source_name=connection.target_name,
                    source_output=dispatch_name,
                    target_name=connection.source_name,
                    target_input=connection.dispatch.target_input,
                    origin=connection.origin,
                )
            )
        return wires

    def _require_channels(
        self, target_name: str, target: Any, feed_count: int
    ) -> Tuple[DynamicConnectionChannel, ...]:
        """Fetches the target's channels or reports that it is not an aggregator.

        A component with no channel declaration cannot classify a single participant, so
        addressing a feed at it is the same authoring mistake as addressing one at a component
        that never implements the resolution hook, and it produces the same error rather than a
        confusing "no matching channel" message.

        Args:
            target_name: Name of the target in the file.
            target: The target component.
            feed_count: How many feeds are addressed at it, for the message.

        Returns:
            The target's declared channels.

        Raises:
            EnergySystemWiringError: ``EF-27`` if the target accepts no feeds.
        """
        if not self.is_aggregator(target):
            raise EnergySystemWiringError(
                EnergySystemErrorId.NOT_AN_AGGREGATOR,
                f"components.{target_name}.inputs",
                f"'{target_name}' ({target.get_full_classname()}) is not an aggregator — it "
                f"declares no dynamic-connection channels — so the {feed_count} feed(s) "
                "addressed at it cannot be applied.",
                remedy=(
                    "Wire these participants with explicit '{input, from}' items, or address "
                    "them at a component that aggregates."
                ),
            )
        return self.channels_of(target)

    def _resolve_feed(
        self,
        target_name: str,
        target: Any,
        channels: Tuple[DynamicConnectionChannel, ...],
        feed: FeedRequest,
    ) -> ResolvedDynamicConnection:
        """Classifies and checks one feed against the target's channels.

        Args:
            target_name: Name of the aggregator in the file.
            target: The aggregator component.
            channels: Its declared channels.
            feed: The feed to resolve.

        Returns:
            The resolved connection, with its channel, tags, weight and dispatch settled.

        Raises:
            EnergySystemWiringError: ``EF-21`` if the participant does not declare the named
                output, plus any channel or dispatch violation.
        """
        source = self.components_by_name[feed.source]
        source_output = self._require_output_name(target, feed)
        port = self._find_output(source, source_output)
        if port is None:
            raise EnergySystemWiringError(
                EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                feed.location,
                f"the {feed.describe()} names the output '{source_output}', which "
                f"'{feed.source}' ({source.get_full_classname()}) does not declare.",
                alternatives=[existing.field_name for existing in source.outputs],
                alternatives_label="outputs",
                offending_value=source_output,
            )
        description = f"'{target_name}' ({target.get_full_classname()})"
        channel = ChannelMatcher.match_and_validate(
            feed=feed,
            channels=channels,
            target=description,
            source_load_type=port.load_type,
            source_unit=port.unit,
        )
        dispatch: Optional[ResolvedDispatch] = None
        if feed.has_dispatch:
            dispatch = ResolvedDispatch(
                target_input=feed.dispatch_target_input,
                tags=ChannelMatcher.resolved_dispatch_tags(feed, channel),
            )
        return ResolvedDynamicConnection(
            source_name=feed.source,
            source_component=source,
            source_output=source_output,
            source_port=port,
            target_name=target_name,
            component_type=feed.component_type,
            flow_tags=feed.flow_tags,
            weight=feed.weight,
            channel=channel,
            origin=feed.origin or feed.describe(),
            dispatch=dispatch,
        )

    def _require_output_name(self, target: Any, feed: FeedRequest) -> str:
        """Determines which participant output a feed measures.

        A feed may omit the dotted half of its ``from`` when the aggregator's declared default
        for the participant's class already names the port unambiguously. Several declarations
        make the omission ambiguous and none makes it unanswerable; both are errors that name
        the candidates.

        Args:
            target: The aggregator component.
            feed: The feed whose participant port is wanted.

        Returns:
            The name of the participant output.

        Raises:
            EnergySystemWiringError: ``EF-21`` if the output cannot be derived.
        """
        if feed.output is not None:
            return feed.output
        source = self.components_by_name[feed.source]
        declarations = self.default_feeds_of(target, source.get_classname())
        if len(declarations) == 1:
            return str(declarations[0].source_component_field_name)
        candidates = [str(declaration.source_component_field_name) for declaration in declarations]
        raise EnergySystemWiringError(
            EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
            feed.location,
            f"the {feed.describe()} names no output of '{feed.source}', and the aggregator "
            f"'{feed.consumer}' declares {len(declarations)} default feeds for the class "
            f"'{source.get_classname()}', so which port is meant is undecidable.",
            alternatives=candidates,
            alternatives_label="outputs",
            remedy=(
                "Write the port as the dotted half of 'from', as in "
                f"'from: {feed.source}.<Output>'."
            ),
        )

    @classmethod
    def _find_output(cls, component: Any, field_name: str) -> Optional["ComponentOutput"]:
        """Looks one component output up by its port name.

        Args:
            component: The component to search.
            field_name: The port name as written in the file or in a declaration.

        Returns:
            The matching output, or ``None``.
        """
        for output in component.outputs:
            if output.field_name == field_name:
                return cast("ComponentOutput", output)
        return None
