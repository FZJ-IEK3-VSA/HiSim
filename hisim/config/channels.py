"""What an aggregating component accepts, and what one accepted feed resolved to.

Most components are wired port to port: a named output of one goes into a named input of another.
An *aggregator* — an electricity meter, an energy management system — is different. It has no
input per participant; it has **channels**, described by sets of tags, and it grows one input per
participant it accepts, ranking them by a weight. Which channel a participant lands on decides how
the aggregator interprets its flow, so getting it wrong produces a plausible-looking but wrong
energy balance rather than a crash.

This module holds the two halves of that vocabulary a *component class* touches. A channel is a
class-level declaration, written in the component's own body next to its ports, exactly as a
preset is written in a configuration's body: it says which flows this aggregator understands, what
load type and unit they carry, and whether it sends a signal back. A resolved connection is the
other end of the same conversation — one participant, fully classified, handed back to the
aggregator so it can create the ports the participant needs — and it carries the derived port
names that end up in result files.

Neither half knows anything about files. The rule that *picks* a channel for a written feed, the
decoding of the tag names a document spells, and every check that can reject a document live in
:mod:`hisim.energy_system`, which imports this module rather than the other way round. That
direction is the whole point of putting the declarations here: an aggregator declaring its
channels, and a dynamic component creating the ports a resolved feed asks for, must not drag the
file format's reader — and its YAML and validation dependencies — into every component import.

**Errors.** The three refusals raised here are defects of a *component class*, never of a file: a
channel with no tags, a channel that forbids dispatch and then names dispatch tags, and a lookup
of a channel key the class never declared. They are raised as :class:`ChannelDeclarationError`,
which is deliberately not part of the file-format error catalogue — no author can cause one, and
no message about a document could help. Everything a document *can* get wrong about channels is
decided by the matcher and reported with its own catalogue identifier.
"""

# clean

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, FrozenSet, Iterable, List, Optional, Tuple, Union

from hisim import loadtypes as lt

if TYPE_CHECKING:  # pragma: no cover - annotations only, never imported at runtime
    from hisim.component import Component, ComponentOutput


class ChannelDeclarationError(Exception):
    """A component class declared a channel that cannot mean anything, or asked for one it has not.

    Separate from the energy-system file's error catalogue on purpose. Every condition raised
    through this class is a defect in a component's own class body — an empty tag set, a
    contradiction between a dispatch rule and its tags, a lookup of a key nothing declares — which
    no energy-system file can cause and no author can repair. Raising it the moment the class is
    imported means such a defect surfaces in the failing component's own test rather than in the
    first file that happens to name it.
    """


#: Either half of a dynamic connection's tag set: the participant's kind (a
#: :class:`~hisim.loadtypes.ComponentType`) or the semantics of the flow it carries (an
#: :class:`~hisim.loadtypes.InandOutputType`). The two live in separate fields of a written
#: feed and are recombined into one set for matching, which is what keeps the combined list
#: component-type-first by construction rather than by author discipline.
ConnectionTag = Union[lt.ComponentType, lt.InandOutputType]


@enum.unique
class DispatchRule(str, enum.Enum):
    """Whether a channel's participants may, must, or must not receive a dispatch signal.

    The rule belongs to the channel rather than to the feed because it is a property of what
    the aggregator does with the flow: an energy management system can never dispatch to a
    photovoltaic production feed, must be able to dispatch to a battery it controls, and may or
    may not dispatch to a generic controllable consumer. Declaring it turns "dispatch block
    missing on a controllable participant" into an error raised while the file is being read,
    instead of a runtime surprise in the middle of a simulated year.
    """

    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True)
class DynamicConnectionChannel:
    """One entry of an aggregator's declared vocabulary of accepted flows.

    A channel says: flows tagged like this are something I understand; they carry this load
    type in this unit; and this is whether I send a signal back to them. The aggregator's own
    simulation code then queries its participants by the channel :attr:`key` instead of by a
    raw tag list, which makes the declaration the single source of truth for validation and for
    simulation alike, so the two cannot drift apart.

    The class is frozen because channels are class-level declarations shared by every instance
    of an aggregator. The constructor normalizes the tag collections to frozen sets so a
    declaration may be written with plain set or list literals.
    """

    key: str
    tags: FrozenSet[ConnectionTag]
    load_type: lt.LoadTypes
    unit: lt.Units
    dispatch: DispatchRule
    dispatch_tags: FrozenSet[lt.InandOutputType] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Freezes the tag collections and rejects a declaration that cannot mean anything.

        A channel with no tags would be a subset of every feed and therefore match everything,
        which makes the most-specific rule meaningless; a channel that forbids dispatch but
        names dispatch tags contradicts itself. Both are defects of the component class rather
        than of any file, so they are caught the moment the class is imported.

        Raises:
            ChannelDeclarationError: For a channel without tags, and for dispatch tags on a
                channel that forbids dispatch.
        """
        object.__setattr__(self, "tags", frozenset(self.tags))
        object.__setattr__(self, "dispatch_tags", frozenset(self.dispatch_tags))
        if not self.tags:
            raise ChannelDeclarationError(
                f"channel '{self.key}' declares no tags, so it would match every feed and make "
                "most-specific matching meaningless."
            )
        if self.dispatch is DispatchRule.FORBIDDEN and self.dispatch_tags:
            raise ChannelDeclarationError(
                f"channel '{self.key}' forbids dispatch but declares the dispatch tags "
                f"{sorted(tag.name for tag in self.dispatch_tags)}; drop the tags, or make the "
                "rule OPTIONAL or REQUIRED."
            )

    def matches(self, feed_tags: Iterable[ConnectionTag]) -> bool:
        """Reports whether this channel's tags are a subset of a feed's effective tags.

        Subset rather than equality is what allows a participant to carry extra descriptive
        tags that no channel consumes and that survive as metadata for postprocessing and for
        key-performance-indicator filtering.

        Args:
            feed_tags: The effective tags of one feed.

        Returns:
            ``True`` when every tag of this channel appears in the feed.
        """
        return self.tags.issubset(frozenset(feed_tags))

    def describe(self) -> str:
        """Builds the compact rendering of the channel that error messages list.

        Returns:
            A string such as ``'storage' (tags BATTERY, ELECTRICITY_CONSUMPTION_EMS_CONTROLLED)``.
        """
        listed = ", ".join(sorted(tag.name for tag in self.tags))
        return f"'{self.key}' (tags {listed})"


@dataclass(frozen=True)
class ResolvedDispatch:
    """The back-channel half of a resolved feed: the signal an aggregator sends a participant.

    A participant an aggregator merely measures needs no back-channel; one the aggregator
    controls does, because the aggregator has to publish the power it wants that participant to
    draw. The target input is optional on purpose: an energy management system ranks several
    participants whose dispatch output nothing reads back — it is recorded for postprocessing
    while the participant's controller receives its signal over an ordinary wire — and making
    the input optional expresses that without inventing a fake port.

    The tags are the fully resolved ones the created output will carry: the channel's dispatch
    tags plus the participant's own component type.
    """

    target_input: Optional[str]
    tags: Tuple[ConnectionTag, ...]


@dataclass(frozen=True)
class ResolvedDynamicConnection:
    """One fully classified feed, ready for an aggregator to create its ports from.

    Everything an aggregator needs is present and already checked: the participant and the port
    of it that is measured, the tags and the weight verbatim as the file declared them, the
    channel the tags selected — which supplies the load type and the unit — and the optional
    back-channel. The derived port names are properties rather than stored fields, so that they
    can never drift from the templates.

    One of those names can be overruled, and only one. An aggregator may already publish the
    control signal a feed's dispatch block asks for — component constructors routinely create one
    per participant class they declare a default feed for — and in that case the connection adopts
    the existing port instead of growing a second one that no tag-and-weight lookup could tell from
    the first. :attr:`adopted_dispatch_output` records that decision, and it is the one place where
    a port this record names was not named by a template.

    The participant's kind and the flow's semantics are stored in the two separate fields the
    file uses, and the combined :attr:`tags` list an aggregator stores on the created port is
    rebuilt from them as the component type followed by the flow tags. That makes the list
    component-type-first by construction, which is what the existing tag-based runtime lookups
    rely on, without any author having to remember the order.
    """

    #: Template for the aggregator input created per feed.
    AGGREGATOR_INPUT_TEMPLATE: ClassVar[str] = "{source_output}From{source_name}"

    #: Template for the dispatch output of a feed that names a target input.
    DISPATCH_OUTPUT_TEMPLATE: ClassVar[str] = "DispatchTo{source_name}_{target_input}"

    #: Template for the dispatch output of a feed without a target input, whose signal is
    #: recorded for postprocessing rather than read by another component.
    RECORDED_DISPATCH_OUTPUT_TEMPLATE: ClassVar[str] = "DispatchFor{source_name}_{source_output}"

    source_name: str
    source_component: "Component"
    source_output: str
    source_port: "ComponentOutput"
    target_name: str
    component_type: Optional[lt.ComponentType]
    flow_tags: Tuple[lt.InandOutputType, ...]
    weight: int
    channel: DynamicConnectionChannel
    origin: str
    dispatch: Optional[ResolvedDispatch] = None
    adopted_dispatch_output: Optional[str] = None

    @property
    def tags(self) -> Tuple[ConnectionTag, ...]:
        """The combined tag list stored on the created port, component type first.

        Returns:
            The participant's component type, when it has one, followed by the flow tags.
        """
        combined: List[ConnectionTag] = []
        if self.component_type is not None:
            combined.append(self.component_type)
        combined.extend(self.flow_tags)
        return tuple(combined)

    @property
    def aggregator_input_name(self) -> str:
        """Name of the aggregator input this connection creates.

        Returns:
            The derived name, for example ``ElectricityOutputFromBoiler``.
        """
        return self.AGGREGATOR_INPUT_TEMPLATE.format(
            source_output=self.source_output, source_name=self.source_name
        )

    @property
    def dispatch_output_name(self) -> Optional[str]:
        """Name of the port this connection's control signal is published on.

        The port the aggregator already had when the connection adopted one, and otherwise the
        derived name. Two templates are in play for the derived case: a dispatch block naming a
        target input produces ``DispatchTo{source}_{input}``; one without a target input — the
        recorded but unread signal — produces ``DispatchFor{source}_{output}``, which stays
        collision-free for the same reason the input names do.

        Returns:
            The output name, or ``None`` when there is no back-channel.
        """
        if self.dispatch is None:
            return None
        if self.adopted_dispatch_output is not None:
            return self.adopted_dispatch_output
        if self.dispatch.target_input is not None:
            return self.DISPATCH_OUTPUT_TEMPLATE.format(
                source_name=self.source_name, target_input=self.dispatch.target_input
            )
        return self.RECORDED_DISPATCH_OUTPUT_TEMPLATE.format(
            source_name=self.source_name, source_output=self.source_output
        )

    @property
    def created_dispatch_output_name(self) -> Optional[str]:
        """Name of the dispatch output resolution has to create, if it has to create one at all.

        Separate from :attr:`dispatch_output_name` because the two answer different questions: the
        wiring asks which port the signal comes out of, while the aggregator and the port checks
        ask which port they are responsible for bringing into existence. An adopted signal already
        exists, so nobody creates it and no name-collision check may complain about it.

        Returns:
            The derived output name, or ``None`` when there is no back-channel or it was adopted.
        """
        if self.adopted_dispatch_output is not None:
            return None
        return self.dispatch_output_name

    def sort_key(self) -> Tuple[int, str, str]:
        """The deterministic ordering key of a target's connections.

        Returns:
            Weight, participant name and participant output, in that priority.
        """
        return (self.weight, self.source_name, self.source_output)

    def describe(self) -> str:
        """Builds the one-line rendering error messages and the wire log name it by.

        Returns:
            A string such as ``'boiler.ElectricityOutput' -> 'meter' (weight 999)``.
        """
        return (
            f"'{self.source_name}.{self.source_output}' -> '{self.target_name}' "
            f"(weight {self.weight}, from {self.origin})"
        )


@dataclass(frozen=True)
class ResolvedDynamicWire:
    """One field-level wire that feed resolution asks the wiring stage to make.

    Resolution creates ports; it does not connect them, so that a wire born from a feed passes
    through the same port-existence, type-agreement and duplicate-feed checks as every wire an
    author wrote. This small record is the hand-over, which is why it carries names rather than
    component objects.
    """

    source_name: str
    source_output: str
    target_name: str
    target_input: str
    origin: str
