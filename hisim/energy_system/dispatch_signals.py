"""Deciding, per resolved feed, which port an aggregator publishes its control signal on.

An aggregator does not find the output it steers a participant through by name. It searches its own
dynamic bookkeeping for the output carrying that participant's tags at that participant's weight,
and then pairs its participants against the signals it found, in order. That makes ``(tags, weight)``
the real identity of a control signal, and it makes a second output answering the same search far
worse than a name collision: nothing refuses it, the paired lists come out one entry too long, and
every participant after the duplicate is steered through the port belonging to another one. The
simulation stays plausible and the battery is simply never charged.

Two facts make that easy to walk into. A component constructor creates a control output for every
participant class it declares a default feed for, whether or not that class is in the system — so an
aggregator arrives at the wiring stage already publishing signals. And a channel may declare that it
dispatches to *every* participant on it, so a file describing such a participant has to write a
dispatch block whether or not the port already exists. Written naively, those two combine into
exactly the duplicate above.

The rule here resolves that: a dispatch block asks the aggregator for a signal at ``(tags, weight)``,
and it is served by the port the aggregator already publishes for that participant where there is
one, and by a newly grown port otherwise. Only what cannot be served either way is an error — two
feeds of one file claiming one signal, or a claim on a signal an existing port already answers for a
*different* participant class, which no naming scheme could disentangle.

Two signals count as one when their tag sets are equal, not when one merely contains the other,
even though the runtime search tests containment. That is exact for everything the format can
produce — a dispatch output's tags are the channel's dispatch tags plus the participant's component
type and nothing else, so one channel gives one tag set per component type — and it keeps the rule
something a reader can check against the ports in front of them.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemWiringError
from hisim.energy_system.resolution import ResolvedDynamicConnection

#: The identity a runtime dispatch lookup matches a control output on: the sorted names of its
#: tags — sorted so that two declarations listing the same tags in a different order produce one
#: key — paired with its weight.
SignalKey = Tuple[Tuple[str, ...], int]


@dataclass(frozen=True)
class PublishedSignal:
    """One control output an aggregator already publishes, as the planner needs to see it.

    A small record read off the aggregator's own dynamic-output bookkeeping rather than a use of
    that bookkeeping's type, because the planner reaches into components by duck typing and must
    not import component code to do it. Only three things about such an output matter here: what it
    is called, which signal it answers, and which participant class it was created for.
    """

    name: str
    key: SignalKey
    component_class: Optional[str]

    def serves(self, participant_class: Optional[str]) -> bool:
        """Whether this output may be adopted by a participant of one class.

        An output created without a participant class is unclaimed and serves anyone, which is what
        a setup calling the imperative add-API produces. An output created *for* a class serves
        that class only: adopting it for another participant would hand that participant a signal
        which the output registry — and the pruning that drops the outputs of absent classes —
        believes belongs to somebody else.

        Args:
            participant_class: Class name of the participant asking to adopt it.

        Returns:
            ``True`` when the output is unclaimed or was created for that class.
        """
        return self.component_class is None or self.component_class == participant_class


class DispatchSignalPlanner:
    """Assigns every resolved dispatch block a port, adopting one where the aggregator has it.

    Constructed per aggregator and asked once, because the decision for one feed depends both on
    what the aggregator already publishes and on what the earlier feeds of the same batch have
    claimed. The planner runs before the aggregator is asked to create anything, so a refusal
    leaves the component exactly as it was.

    It reaches into the aggregator through the well-known attribute names below and reads nothing
    else, so this module — like the port checks it runs beside — imports no component code.
    """

    #: Attribute through which a dynamic component publishes the bookkeeping of the outputs it has
    #: grown, which is the same list its own tag-and-weight lookups search.
    DYNAMIC_OUTPUTS_ATTRIBUTE: ClassVar[str] = "my_component_outputs"

    #: Attribute on one entry of that bookkeeping holding the port's field name.
    OUTPUT_NAME_ATTRIBUTE: ClassVar[str] = "source_output_field_name"

    #: Attribute holding the tags the port carries.
    OUTPUT_TAGS_ATTRIBUTE: ClassVar[str] = "source_tags"

    #: Attribute holding the port's weight.
    OUTPUT_WEIGHT_ATTRIBUTE: ClassVar[str] = "source_weight"

    #: Attribute holding the participant class the port was created for, if the creator named one.
    OUTPUT_CLASS_ATTRIBUTE: ClassVar[str] = "source_component_class"

    def __init__(self, target_name: str, target: Any) -> None:
        """Reads the signals one aggregator already publishes.

        Args:
            target_name: Name of the aggregator in the file, quoted in messages.
            target: The aggregator component, read but never modified.
        """
        self.target_name = target_name
        self.published: Dict[SignalKey, List[PublishedSignal]] = {}
        for entry in getattr(target, self.DYNAMIC_OUTPUTS_ATTRIBUTE, ()) or ():
            signal = self._published_signal(entry)
            self.published.setdefault(signal.key, []).append(signal)

    def plan(self, resolved: Sequence[ResolvedDynamicConnection]) -> List[ResolvedDynamicConnection]:
        """Settles the signal port of every connection of one aggregator.

        Args:
            resolved: The aggregator's resolved connections, already sorted, so that the batch is
                walked in the same deterministic order its ports are created in.

        Returns:
            The same connections, each dispatching one carrying the port it adopted where it
            adopted one; a connection without a dispatch block is returned untouched.

        Raises:
            EnergySystemWiringError: ``EF-2B`` when a dispatch block claims a signal another feed
                of the same aggregator already claims, or one an existing port answers for a
                participant of another class.
        """
        claimed: Dict[SignalKey, str] = {}
        planned: List[ResolvedDynamicConnection] = []
        for connection in resolved:
            if connection.dispatch is None:
                planned.append(connection)
                continue
            key = self.signal_key(connection.dispatch.tags, connection.weight)
            self._refuse_if_claimed(connection, key, claimed)
            adopted = self._adoptable(connection, key)
            settled = (
                connection
                if adopted is None
                else dataclasses.replace(connection, adopted_dispatch_output=adopted)
            )
            claimed[key] = settled.dispatch_output_name or ""
            planned.append(settled)
        return planned

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
            name=str(getattr(entry, cls.OUTPUT_NAME_ATTRIBUTE)),
            key=cls.signal_key(
                getattr(entry, cls.OUTPUT_TAGS_ATTRIBUTE, ()),
                int(getattr(entry, cls.OUTPUT_WEIGHT_ATTRIBUTE)),
            ),
            component_class=getattr(entry, cls.OUTPUT_CLASS_ATTRIBUTE, None),
        )

    def _adoptable(self, connection: ResolvedDynamicConnection, key: SignalKey) -> Optional[str]:
        """Names the published port this connection may take over, if there is one.

        Args:
            connection: The dispatching connection.
            key: The signal it claims.

        Returns:
            The name of the port to adopt, or ``None`` when the aggregator has to grow one.

        Raises:
            EnergySystemWiringError: ``EF-2B`` when a port answers this signal but was created for
                another participant class, so that growing a second one would leave the aggregator
                with two ports it cannot tell apart.
        """
        candidates = self.published.get(key, [])
        if not candidates:
            return None
        participant_class = connection.source_component.get_classname()
        for candidate in candidates:
            if candidate.serves(participant_class):
                return candidate.name
        raise EnergySystemWiringError(
            EnergySystemErrorId.AMBIGUOUS_DISPATCH_SIGNAL,
            f"components.{self.target_name}.inputs",
            f"the dispatch block of {connection.describe()} claims the control signal tagged "
            f"{list(key[0])} at weight {key[1]}, which '{self.target_name}' already publishes on "
            f"'{candidates[0].name}' for the class '{candidates[0].component_class}'. The "
            "aggregator finds a participant's signal by those tags and that weight, so a second "
            "port would leave it unable to tell the two apart.",
            remedy=(
                "Rank the two participants at different weights, or feed the one the existing "
                "signal belongs to instead."
            ),
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
