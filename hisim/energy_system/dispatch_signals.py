"""Refusing two control signals an aggregator could never tell apart.

An aggregator does not find the output it steers a participant through by name. It searches its own
dynamic bookkeeping for the output carrying that participant's tags at that participant's weight,
and then pairs its participants against the signals it found, in order. That makes ``(tags, weight)``
the real identity of a control signal, and it makes a second output answering the same search far
worse than a name collision: nothing refuses it, the paired lists come out one entry too long, and
every participant after the duplicate is steered through the port belonging to another one. The
simulation stays plausible and the battery is simply never charged.

A file can walk into that in two ways. Two of its feeds can claim one signal — same tags, same
weight, two participants — which no naming scheme would notice, because the derived names differ.
And a claim can land on a signal the aggregator already publishes, which used to be the common
case: a component constructor created a control output for every participant class it declared a
default feed for, so an aggregator arrived at the wiring stage already publishing signals, and a
dispatch block took such a port over instead of growing a second one. No aggregator does that any
more (F-1) — before resolution a component built from a file has only its declared outputs — so a
published port answering a claim is no longer an arrangement to accommodate but a contradiction to
report: whoever grew it did so outside the format, and the run would carry two ports for one
signal.

Both refusals are this module. A published port counts as answering a claim when its weight is the
claim's and its tags contain the claim's set, which is precisely the runtime lookup's own rule —
so the question "would the aggregator find two answers?" is asked here in exactly the terms it will
be asked at run time.
"""

# clean

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError
from hisim.energy_system.resolution import ResolvedDynamicConnection

#: The planner is the module's one shared surface; the signal records are its private vocabulary.
__all__ = ["DispatchSignalPlanner"]

#: The identity a runtime dispatch lookup matches a control output on: the sorted names of its
#: tags — sorted so that two declarations listing the same tags in a different order produce one
#: key — paired with its weight.
SignalKey = Tuple[Tuple[str, ...], int]


@dataclass(frozen=True)
class PublishedSignal:
    """One control output an aggregator already publishes, as the planner needs to see it.

    A small record read off the aggregator's own dynamic-output bookkeeping rather than a use of
    that bookkeeping's type, because the planner reaches into components by duck typing and must
    not import component code to do it. Only two things about such an output matter here: what it
    is called, and which signal it answers.
    """

    name: str
    key: SignalKey

    def answers(self, claim: SignalKey) -> bool:
        """Whether the aggregator's runtime lookup would return this output for a claim.

        The lookup matches a signal by weight and by tag containment, so a port carrying the
        claimed tags — or those and more — at the claimed weight is one of the answers the
        aggregator would find, alongside the port the claim itself brings into existence.

        Args:
            claim: The signal a dispatch block asks for.

        Returns:
            ``True`` when this output would be found by a lookup for that claim.
        """
        return self.key[1] == claim[1] and set(claim[0]) <= set(self.key[0])


class DispatchSignalPlanner:
    """Checks that every resolved dispatch block asks for a signal nothing else answers.

    Constructed per aggregator and asked once, because the decision for one feed depends both on
    what the aggregator already publishes and on what the earlier feeds of the same batch have
    claimed. The planner runs before the aggregator is asked to create anything, so a refusal
    leaves the component exactly as it was.

    It reaches into the aggregator's ``my_component_outputs`` bookkeeping by plain attribute
    access and reads nothing else, so this module — like the port checks it runs beside — imports
    no component code. The access is deliberately strict: a target without the bookkeeping, or a
    bookkeeping entry missing a field, raises immediately rather than reading as "no published
    signals", because that silent reading is how a renamed field would grow duplicate ports.
    """

    def __init__(self, target_name: str, target: Any) -> None:
        """Reads the signals one aggregator already publishes.

        Args:
            target_name: Name of the aggregator in the file, quoted in messages.
            target: The aggregator component, read but never modified. It must carry the
                dynamic-output bookkeeping every aggregator has; anything else here is a bug in
                the caller, not a component without signals.
        """
        self.target_name = target_name
        self.published: List[PublishedSignal] = [
            self._published_signal(entry) for entry in target.my_component_outputs
        ]

    def check(self, resolved: Sequence[ResolvedDynamicConnection]) -> None:
        """Refuses any dispatch block of one aggregator whose signal is not its alone.

        Args:
            resolved: The aggregator's resolved connections, already sorted, so that the batch is
                walked in the same deterministic order its ports are created in.

        Raises:
            EnergySystemWiringError: ``EF-2B`` when a dispatch block claims a signal another feed
                of the same aggregator already claims, or one an existing port already answers.
        """
        claimed: Dict[SignalKey, str] = {}
        for connection in resolved:
            if connection.dispatch is None:
                continue
            key = self.signal_key(connection.dispatch.tags, connection.weight)
            self._refuse_if_claimed(connection, key, claimed)
            self._refuse_if_published(connection, key)
            dispatch_port = connection.dispatch_output_name
            # The property answers None only for a connection without a dispatch block, and
            # those were filtered out above; the assertion narrows the Optional for the checker.
            assert dispatch_port is not None  # nosec B101 - unreachable by the filter above
            claimed[key] = dispatch_port

    def _refuse_if_published(self, connection: ResolvedDynamicConnection, key: SignalKey) -> None:
        """Refuses a claim the aggregator already publishes a port for.

        Nothing an energy-system file builds publishes a control output before resolution, so a
        port answering a claim was grown outside the format — by a constructor, or by the
        imperative add-API — and the aggregator would find it *and* the port this claim creates.
        The refusal names the port and both tag sets so the two can be reconciled deliberately.

        Args:
            connection: The dispatching connection being planned.
            key: The signal it claims.

        Raises:
            EnergySystemWiringError: ``EF-2B`` naming the published port and both tag sets.
        """
        for signal in self.published:
            if not signal.answers(key):
                continue
            raise EnergySystemWiringError(
                EnergySystemErrorId.AMBIGUOUS_DISPATCH_SIGNAL,
                f"components.{self.target_name}.inputs",
                f"the dispatch block of {connection.describe()} claims the control signal "
                f"tagged {sorted(key[0])} at weight {key[1]}, and '{self.target_name}' already "
                f"publishes '{signal.name}' tagged {sorted(signal.key[0])} at that weight. The "
                "runtime finds a signal by tag containment, so it would answer this claim with "
                "that port and with the one the claim produces, and the aggregator could not "
                "tell the two apart.",
                remedy=(
                    "Rank the two at different weights, or drop the port the aggregator "
                    "publishes and let the feed grow the one it describes."
                ),
            )

    @classmethod
    def signal_key(cls, tags: Iterable[Any], weight: int) -> SignalKey:
        """Builds the identity an aggregator's runtime lookup matches a control output on.

        The single spelling of that identity, used for the ports an aggregator already publishes
        and for the ones a file asks it to grow alike, so that the comparison between the two
        cannot be made in two different ways.

        Args:
            tags: The tags the output carries, as enum members or anything else with a ``name``.
            weight: The output's weight.

        Returns:
            The sorted tag names paired with the weight.
        """
        return tuple(sorted(getattr(tag, "name", str(tag)) for tag in tags)), weight

    @classmethod
    def _published_signal(cls, entry: Any) -> PublishedSignal:
        """Reads one entry of an aggregator's dynamic-output bookkeeping.

        Args:
            entry: The bookkeeping record of one grown output.

        Returns:
            What the planner needs to know about that output.
        """
        return PublishedSignal(
            name=str(entry.source_output_field_name),
            key=cls.signal_key(entry.source_tags, int(entry.source_weight)),
        )

    def _refuse_if_claimed(
        self, connection: ResolvedDynamicConnection, key: SignalKey, claimed: Dict[SignalKey, str]
    ) -> None:
        """Refuses a signal an earlier feed of the same aggregator has already claimed.

        Args:
            connection: The dispatching connection being planned.
            key: The signal it claims.
            claimed: What the earlier connections of this batch claimed, port name per signal.

        Raises:
            EnergySystemWiringError: ``EF-2B`` when the signal is already claimed.
        """
        occupant = claimed.get(key)
        if occupant is None:
            return
        raise EnergySystemWiringError(
            EnergySystemErrorId.AMBIGUOUS_DISPATCH_SIGNAL,
            f"components.{self.target_name}.inputs",
            f"the dispatch block of {connection.describe()} claims the control signal tagged "
            f"{list(key[0])} at weight {key[1]}, which an earlier feed of '{self.target_name}' "
            f"already claims through '{occupant}'. The aggregator ranks its participants by "
            "weight and finds each one's signal by these tags, so two participants sharing both "
            "would be steered through one port and one of them never commanded at all.",
            remedy="Rank the two participants at different weights.",
        )
