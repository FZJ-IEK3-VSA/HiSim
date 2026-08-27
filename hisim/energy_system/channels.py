"""Reading an aggregator feed out of a file: what it decodes to, and what its tag names mean.

An aggregating component declares its channels in its own class body, and those declarations —
together with the record of one resolved feed — live in :mod:`hisim.config.channels`, below the
components, so that declaring a channel never pulls this format's reader into a component import.
This module is the file side of the same vocabulary and re-exports the declaration types, so that
everything the matcher needs is importable from one place.

What it adds is what only a document needs. A :class:`FeedRequest` is one aggregator feed of a
file with its tag names already decoded into enumeration members, because the model layer of this
package keeps every value as the plain string the author wrote and matching needs the members.
And :class:`TagDecoder` is the step between the two: it turns a written name into the member,
listing both tag vocabularies when neither knows the name.

The rule that picks a channel for a feed — most-specific subset — and the checks that follow from
it live next door in :mod:`hisim.energy_system.channel_matching`.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterable, List, Optional, Tuple

from hisim import loadtypes as lt
from hisim.config.channels import (
    ChannelDeclarationError,
    ConnectionTag,
    DispatchRule,
    DynamicConnectionChannel,
)
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemErrorId

__all__ = [
    "ChannelDeclarationError",
    "ConnectionTag",
    "DispatchRule",
    "DynamicConnectionChannel",
    "FeedRequest",
    "TagDecoder",
]


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
