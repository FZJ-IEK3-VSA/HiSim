"""Selecting the channel an aggregator feed belongs to, and checking the feed against it.

Matching a feed to a channel is a small rule with a large consequence, which is why it lives in
a module of its own rather than as a method on the channel. The rule is *most-specific subset*:
every channel whose tags are a subset of the feed's effective tags is a candidate, and the
candidate with the largest tag set wins, because a more specific declaration expresses a more
precise reading of the same participant. Subset rather than equality is what lets a participant
carry extra descriptive tags that no channel consumes and that survive as metadata for
postprocessing; largest-wins is what routes a battery to a storage channel while an ordinary
controllable consumer falls to the generic one.

Channel tag sets must therefore be *nested*, never merely overlapping. Two equally specific
candidates leave the feed's meaning undecidable, and rather than pick one by declaration order
— which would make an aggregator's behaviour depend on the order its class body happens to list
its channels in — the tie is rejected as the declaration defect it is.

On top of matching sit the checks that only make sense once the channel is known: that the
participant's port carries what the channel says it carries, and that the dispatch block agrees
with the channel's dispatch rule and with the weight. The last of those ties two things the
format keeps in separate places — a channel that never dispatches can only measure, so a
participant on it must carry the reserved monitored-only weight, and a channel that always
dispatches needs a real rank to serve.
"""

# clean

from __future__ import annotations

from typing import Any, FrozenSet, List, Optional, Sequence, Tuple

from hisim import loadtypes as lt
from hisim.energy_system.channels import (
    ConnectionTag,
    DispatchRule,
    DynamicConnectionChannel,
    FeedRequest,
)
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError


class ChannelMatcher:
    """Selects the channel a feed belongs to and checks the feed against that channel.

    Two halves, kept as separate entry points so that a tool wanting to know only *which*
    channel a feed would select does not also have to satisfy the validation rules:
    :meth:`match` performs the most-specific subset selection, and :meth:`validate` checks the
    participant's port types and the dispatch rules against the channel that won.

    Everything is a classmethod over explicit arguments, so the check needs no aggregator
    instance beyond the channel declaration it is handed.
    """

    @classmethod
    def match(
        cls,
        feed: FeedRequest,
        channels: Sequence[DynamicConnectionChannel],
        target: str,
    ) -> DynamicConnectionChannel:
        """Selects the most specific channel of an aggregator that accepts a feed's tags.

        Every channel whose tags are a subset of the feed's effective tags is a candidate, and
        the candidate with the largest tag set wins, because a more specific declaration
        expresses a more precise reading of the same participant. Nested tag sets are the
        intended design; only a genuine tie between two equally specific candidates is a defect.

        Args:
            feed: The feed to classify.
            channels: The aggregator's declared channels.
            target: Human-readable description of the aggregator, used in messages.

        Returns:
            The winning channel.

        Raises:
            EnergySystemWiringError: ``EF-28`` if no channel matches, or if two channels tie
                for the largest matching tag set.
        """
        feed_tags = frozenset(feed.effective_tags)
        candidates = [channel for channel in channels if channel.matches(feed_tags)]
        if not candidates:
            raise EnergySystemWiringError(
                EnergySystemErrorId.NO_CHANNEL_MATCH,
                feed.location,
                f"the {feed.describe()} carries the tags "
                f"{sorted(tag.name for tag in feed_tags)}, which match no channel of the "
                f"aggregator {target}.",
                alternatives=[channel.describe() for channel in channels],
                alternatives_label="channels",
            )
        widest = max(len(channel.tags) for channel in candidates)
        winners = [channel for channel in candidates if len(channel.tags) == widest]
        if len(winners) > 1:
            raise EnergySystemWiringError(
                EnergySystemErrorId.NO_CHANNEL_MATCH,
                feed.location,
                f"the {feed.describe()} matches {len(winners)} equally specific channels of "
                f"the aggregator {target}, so which one it belongs to is undecidable.",
                alternatives=[channel.describe() for channel in winners],
                alternatives_label="channels",
                remedy=(
                    "Channel tag sets must be nested, never overlapping: give the feed a tag "
                    "that tells them apart, or redesign the aggregator's channels."
                ),
            )
        return winners[0]

    @classmethod
    def validate(
        cls,
        feed: FeedRequest,
        channel: DynamicConnectionChannel,
        target: str,
        source_load_type: Optional[lt.LoadTypes] = None,
        source_unit: Optional[lt.Units] = None,
    ) -> None:
        """Checks a feed against the channel it selected.

        The load type and unit of the participant's output are compared with the channel's when
        the caller knows them, which it does once the components are built; a purely textual
        check passes ``None`` and skips that comparison. The dispatch rules are always checked,
        since they need nothing but the feed.

        Args:
            feed: The feed to check.
            channel: The channel :meth:`match` selected for it.
            target: Human-readable description of the aggregator, used in messages.
            source_load_type: Load type of the participant's output port, if known.
            source_unit: Unit of the participant's output port, if known.

        Raises:
            EnergySystemWiringError: ``EF-30`` on a load type or unit mismatch, ``EF-29`` on
                any violation of the channel's dispatch rule.
        """
        cls._check_port_types(feed, channel, target, source_load_type, source_unit)
        cls._check_dispatch_rule(feed, channel, target)
        cls._check_dispatch_tags(feed, channel, target)

    @classmethod
    def match_and_validate(
        cls,
        feed: FeedRequest,
        channels: Sequence[DynamicConnectionChannel],
        target: str,
        source_load_type: Optional[lt.LoadTypes] = None,
        source_unit: Optional[lt.Units] = None,
    ) -> DynamicConnectionChannel:
        """Matches a feed to a channel and validates it against that channel in one call.

        Args:
            feed: The feed to classify and check.
            channels: The aggregator's declared channels.
            target: Human-readable description of the aggregator, used in messages.
            source_load_type: Load type of the participant's output port, if known.
            source_unit: Unit of the participant's output port, if known.

        Returns:
            The channel the feed belongs to.

        Raises:
            EnergySystemWiringError: On any matching or validation failure.
        """
        channel = cls.match(feed, channels, target)
        cls.validate(feed, channel, target, source_load_type, source_unit)
        return channel

    @classmethod
    def resolved_dispatch_tags(
        cls, feed: FeedRequest, channel: DynamicConnectionChannel
    ) -> Tuple[ConnectionTag, ...]:
        """Computes the tags the dispatch output of a feed will carry.

        The result is the channel's dispatch tags — inherited whether or not the feed spells
        them out — plus the participant's own component type, which is what preserves the
        tag-based runtime lookups an aggregator's simulation code performs. The order is by
        member name so that derived artifacts are stable across runs.

        Args:
            feed: The feed whose dispatch output is being created.
            channel: The channel it belongs to.

        Returns:
            The dispatch output's tags, ordered by member name.
        """
        combined: List[ConnectionTag] = list(channel.dispatch_tags)
        if feed.component_type is not None and feed.component_type not in combined:
            combined.append(feed.component_type)
        return tuple(sorted(combined, key=lambda tag: tag.name))

    @classmethod
    def _check_port_types(
        cls,
        feed: FeedRequest,
        channel: DynamicConnectionChannel,
        target: str,
        source_load_type: Optional[lt.LoadTypes],
        source_unit: Optional[lt.Units],
    ) -> None:
        """Compares the participant port's load type and unit with the channel's declaration.

        A watt-valued electricity channel fed by a temperature output would produce numbers the
        aggregator sums into a meaningless total, which is precisely the class of mistake the
        channel declaration exists to catch. Unknown types are skipped rather than assumed to
        agree, and a channel declaring the wildcard load type or unit accepts every counterpart —
        the fuel meter's case, where the carrier of the same flow differs per household.

        Args:
            feed: The feed being checked.
            channel: The channel it selected.
            target: Human-readable description of the aggregator.
            source_load_type: Load type of the participant's output port, or ``None``.
            source_unit: Unit of the participant's output port, or ``None``.

        Raises:
            EnergySystemWiringError: ``EF-30`` on a load type or unit mismatch.
        """
        if source_load_type is not None and not channel.accepts_load_type(source_load_type):
            raise EnergySystemWiringError(
                EnergySystemErrorId.PORT_TYPE_MISMATCH,
                feed.location,
                f"the {feed.describe()} feeds a '{source_load_type.name}' output into the "
                f"channel {channel.describe()} of the aggregator {target}, which carries "
                f"'{channel.load_type.name}'.",
            )
        if source_unit is not None and not channel.accepts_unit(source_unit):
            raise EnergySystemWiringError(
                EnergySystemErrorId.PORT_TYPE_MISMATCH,
                feed.location,
                f"the {feed.describe()} feeds an output in '{source_unit.value}' into the "
                f"channel {channel.describe()} of the aggregator {target}, which is in "
                f"'{channel.unit.value}'.",
            )

    @classmethod
    def _check_dispatch_rule(
        cls, feed: FeedRequest, channel: DynamicConnectionChannel, target: str
    ) -> None:
        """Checks the presence or absence of a dispatch block, and the weight that goes with it.

        Beyond the channel's own rule this ties the weight to it, which the format leaves
        implicit. A channel that forbids dispatch can only ever measure, so a participant on it
        must carry the reserved monitored-only weight; otherwise it would enter the dispatch
        ranking, where an aggregator demands an output for it that the channel forbids it to
        have. The converse holds for a channel that requires dispatch: it needs a real rank to
        serve.

        Args:
            feed: The feed being checked.
            channel: The channel it selected.
            target: Human-readable description of the aggregator.

        Raises:
            EnergySystemWiringError: ``EF-29`` on any violation.
        """
        monitored_only = feed.weight == FeedRequest.MONITORED_ONLY_WEIGHT
        if feed.has_dispatch and monitored_only:
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} uses the reserved monitored-only weight "
                f"{FeedRequest.MONITORED_ONLY_WEIGHT} and therefore cannot carry a dispatch "
                "block.",
            )
        if feed.has_dispatch and channel.dispatch is DispatchRule.FORBIDDEN:
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} carries a dispatch block, but the channel "
                f"{channel.describe()} of the aggregator {target} never sends a signal back.",
            )
        if not feed.has_dispatch and channel.dispatch is DispatchRule.REQUIRED:
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} has no dispatch block, but the channel "
                f"{channel.describe()} of the aggregator {target} dispatches to every "
                "participant on it and needs an output to write to.",
            )
        if channel.dispatch is DispatchRule.FORBIDDEN and not monitored_only:
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} carries a dispatch rank, but the channel "
                f"{channel.describe()} of the aggregator {target} forbids dispatch, so the "
                "participant could never be served.",
                remedy=(
                    f"Give it the reserved monitored-only weight "
                    f"{FeedRequest.MONITORED_ONLY_WEIGHT}."
                ),
            )
        if channel.dispatch is DispatchRule.REQUIRED and monitored_only:
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} uses the reserved monitored-only weight "
                f"{FeedRequest.MONITORED_ONLY_WEIGHT}, but the channel {channel.describe()} of "
                f"the aggregator {target} dispatches to every participant and needs a real rank.",
            )

    @classmethod
    def _check_dispatch_tags(
        cls, feed: FeedRequest, channel: DynamicConnectionChannel, target: str
    ) -> None:
        """Checks explicitly written dispatch tags against the channel's declaration.

        A dispatch block may spell its tags out, and when it does they must equal the channel's:
        the point of writing them is documentation, not override. Omitting them inherits the
        channel's, which is what nearly every feed does.

        Args:
            feed: The feed being checked.
            channel: The channel it selected.
            target: Human-readable description of the aggregator.

        Raises:
            EnergySystemWiringError: ``EF-29`` if the written tags differ from the channel's.
        """
        if feed.dispatch_tags is None:
            return
        written: FrozenSet[Any] = frozenset(feed.dispatch_tags)
        if written != frozenset(channel.dispatch_tags):
            raise EnergySystemWiringError(
                EnergySystemErrorId.DISPATCH_RULE_VIOLATED,
                feed.location,
                f"the {feed.describe()} declares the dispatch tags "
                f"{sorted(tag.name for tag in written)}, but the channel {channel.describe()} "
                f"of the aggregator {target} dispatches with "
                f"{sorted(tag.name for tag in channel.dispatch_tags)}.",
                remedy="Omit 'dispatch.tags' to inherit the channel's tags.",
            )
