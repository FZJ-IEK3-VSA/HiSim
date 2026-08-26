"""The tag vocabulary an aggregating component declares, and the matching of feeds against it.

Most components are wired port to port: a named output of one goes into a named input of
another. An *aggregator* — an electricity meter, an energy management system — is different.
It has no input per participant; it has *channels*, described by sets of tags, and it creates
one input per participant it accepts, ranking them by a weight. Which channel a participant
lands on decides how the aggregator interprets its flow, so getting it wrong produces a
plausible-looking but wrong energy balance rather than a crash.

Until an aggregator declares its channels, that vocabulary exists only implicitly inside the
hard-coded tag queries of its simulation code, and a participant whose tags match no query
wires cleanly and is then silently never read. This module closes that hole. An aggregator
publishes its channels as class-level data, and every feed written in an energy-system file is
matched and validated against that declaration before a single port is created.

This module holds the declaration side of that vocabulary: what a channel is, what a written
feed decodes into, and how the tag names a file spells are turned into the enumeration members
everything downstream works on. The rule that picks a channel for a feed — most-specific subset
— and the checks that follow from it live next door in
:mod:`hisim.energy_system.channel_matching`, so that a component module can declare its channels
without pulling the matcher in.
"""

# clean

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import ClassVar, FrozenSet, Iterable, List, Optional, Tuple, Union

from hisim import loadtypes as lt
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemErrorId

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
            EnergySystemBindingError: ``EF-28`` for a channel without tags, ``EF-29`` for
                dispatch tags on a channel that forbids dispatch.
        """
        object.__setattr__(self, "tags", frozenset(self.tags))
        object.__setattr__(self, "dispatch_tags", frozenset(self.dispatch_tags))
        if not self.tags:
            raise EnergySystemBindingError(
                EnergySystemErrorId.NO_CHANNEL_MATCH,
                f"channel '{self.key}'",
                "the channel declares no tags, so it would match every feed and make "
                "most-specific matching meaningless.",
            )
        if self.dispatch is DispatchRule.FORBIDDEN and self.dispatch_tags:
            raise EnergySystemBindingError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                f"channel '{self.key}'",
                f"the channel forbids dispatch but declares the dispatch tags "
                f"{sorted(tag.name for tag in self.dispatch_tags)}.",
                remedy="Drop the tags, or make the rule OPTIONAL or REQUIRED.",
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
class FeedRequest:
    """One aggregator feed of an energy-system file, with its tag names already decoded.

    An input item of the aggregator-feed shape carries plain strings — a component type, a list
    of flow tags — because the model layer of this package never imports HiSim's enumerations.
    Matching needs the members, so a feed is decoded into this record first, once, and every
    later stage works on the members alone.

    The participant's kind and the flow's semantics stay in separate fields, exactly as the file
    writes them, and :attr:`effective_tags` recombines them for matching. That is what makes the
    tag list handed to an aggregator component-type-first by construction: no file, no default
    declaration and no reordering downstream can produce a list that violates the ordering the
    existing tag-based runtime lookups grew up on.
    """

    #: Weight reserved for a participant an aggregator only measures and never controls. It is
    #: the value every legacy default connection of the electricity meter already spells out.
    MONITORED_ONLY_WEIGHT: ClassVar[int] = 999

    consumer: str
    source: str
    output: Optional[str]
    component_type: Optional[lt.ComponentType]
    flow_tags: Tuple[lt.InandOutputType, ...]
    weight: int
    dispatch_target_input: Optional[str] = None
    dispatch_tags: Optional[Tuple[lt.InandOutputType, ...]] = None
    has_dispatch: bool = False
    origin: str = ""

    @property
    def effective_tags(self) -> Tuple[ConnectionTag, ...]:
        """The tags a channel is matched against: the component type followed by the flow tags.

        Returns:
            The combined tuple, with the participant's kind first when it has one.
        """
        combined: List[ConnectionTag] = []
        if self.component_type is not None:
            combined.append(self.component_type)
        combined.extend(self.flow_tags)
        return tuple(combined)

    def describe(self) -> str:
        """Builds the one-line rendering of the feed that error messages name it by.

        Returns:
            A string such as ``feed 'battery.AcBatteryPowerUsed' -> 'ems' (weight 6)``.
        """
        port = f".{self.output}" if self.output else ""
        return f"feed '{self.source}{port}' -> '{self.consumer}' (weight {self.weight})"

    @property
    def location(self) -> str:
        """The dotted key path of the feed inside the document, for an error message.

        Returns:
            ``components.<consumer>.inputs``, which is where an author edits the feed.
        """
        return f"components.{self.consumer}.inputs"


class TagDecoder:
    """Turns the tag names a file writes into the enumeration members matching works on.

    The wire form of a tag is the member's name — ``ELECTRICITY_PRODUCTION``, ``BATTERY`` —
    because a name is stable, greppable and independent of whatever value the member happens to
    carry. Two enumerations are in play and an author writes both into the same file, so the
    decoder tries each in turn and, when neither knows the name, lists both vocabularies rather
    than only the one it happened to check last.
    """

    @classmethod
    def component_type(cls, name: Optional[str], location: str) -> Optional[lt.ComponentType]:
        """Decodes the ``component_type`` of a feed, which may legitimately be absent.

        Args:
            name: The written member name, or ``None`` when the tags alone identify the channel.
            location: Dotted key path of the feed, used in the message.

        Returns:
            The member, or ``None``.

        Raises:
            EnergySystemBindingError: ``EF-2A`` if no component type carries that name.
        """
        if name is None:
            return None
        member = getattr(lt.ComponentType, name, None)
        if not isinstance(member, lt.ComponentType):
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNKNOWN_TAG,
                location,
                f"'{name}' is not a component type.",
                alternatives=[entry.name for entry in lt.ComponentType],
                alternatives_label="component types",
                offending_value=name,
            )
        return member

    @classmethod
    def flow_tags(cls, names: Iterable[str], location: str) -> Tuple[lt.InandOutputType, ...]:
        """Decodes the ``tags`` list of a feed or of a dispatch block.

        Args:
            names: The written member names, in the order the file lists them.
            location: Dotted key path of the feed, used in the message.

        Returns:
            The members in the written order.

        Raises:
            EnergySystemBindingError: ``EF-2A`` if a name belongs to no flow tag; a
                component type written here is reported with the hint that it has its own key.
        """
        decoded: List[lt.InandOutputType] = []
        for name in names:
            member = getattr(lt.InandOutputType, name, None)
            if not isinstance(member, lt.InandOutputType):
                remedy = None
                if isinstance(getattr(lt.ComponentType, name, None), lt.ComponentType):
                    remedy = f"'{name}' is a component type; write it under 'component_type'."
                raise EnergySystemBindingError(
                    EnergySystemErrorId.UNKNOWN_TAG,
                    location,
                    f"'{name}' is not a flow tag.",
                    alternatives=[entry.name for entry in lt.InandOutputType],
                    alternatives_label="flow tags",
                    offending_value=name,
                    remedy=remedy,
                )
            decoded.append(member)
        return tuple(decoded)
