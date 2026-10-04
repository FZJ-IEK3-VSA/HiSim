"""Observe and actuate: lowering observer selections and a controller's priorities (``assemblies_spec.md`` §4).

HiSim has no bus. A meter and an energy manager are dynamic components whose inputs are added as
their selectors match (§4.3, D13): each **observer** — a site entry with ``observes:``, or an
assembly's observer port, its default replaced by the import's ``observes:`` — selects among the
outputs the constructed observer declares dynamic default connections from (what its constructor
adds with ``add_dynamic_default_connections``, read as
:class:`~hisim.config.channels.ObservableFeed`), and every match becomes an ordinary
:class:`~hisim.energy_system.model.AggregatorFeed` with that declaration's tags and weight, written
where the observer's ``{$observes: <port>}`` placeholder stands. The expanded file carries explicit
feeds, so the channel matching and the feed resolution run unchanged.

**When.** What an observer may observe is declared in its constructor and nowhere else, so the
selection runs once the components are constructed and before anything is connected: the
expansion keeps every observer's context (:class:`~hisim.energy_system.assemblies.expansion.PendingSelection`)
and the build completes it. Every match is written into the port-provenance table as an ``observe``
item, which the post-construction port check verifies on the instances: the observed output exists,
a channel of the observer accepts it with its load type and unit, and a dispatch target is an input
of the participant on a channel that allows a dispatch.

**Candidates and order.** The candidates are the outputs of every component of the expanded system
— site entries in written order, then the imports in written order, each instance in written order,
its members in production order — that the observer declares a feed from, in the order it declares
them; an observer never matches its own outputs. ``declared`` selects every
candidate; a list of selectors the union of their matches, in candidate order (never a dict, file
system or hash order); the feed resolution sorts them again at build time (``feed_resolution.py``).

**Controllers** (§4.4, D11, D21). An assembly whose interface ``actuates:`` a priority list ranks the
feeds of its observer port that the controller declares at a ranked weight (anything but 999). Each
entry of the list starts from the default weight of the component types it ranks — the weight the
controller declares, so the controller's own constants (``L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS``)
are the one source — the k-th further participant of one type gets ``default + k``, and an entry not
above every earlier entry's weights is raised to the next free one, keeping its spacing. A default-order
list with one participant per type therefore reproduces the class's weights; a second battery gets 7;
a reordered list gets weights in list order. The feed's dispatch follows what the observed output
states: ``controllable: {target_input: …}`` lowers to ``dispatch.target_input``, an input of the
participant on a channel of the controller that allows a dispatch (verified after construction);
``controllable: {via: <need>}`` lowers to an
empty dispatch, and its need — bound to this controller — has already lowered to the L1's default
connections from the controller's class, the modifier; an output with no ``controllable`` the class
ranks is ranked only, with an empty dispatch (residents, solar thermal).

**Refusals.** A constructed observer that declares no dynamic default connections (``EF-7H``); a
``required`` selector matching nothing, an observer whose selection matches nothing (idle), a ranked
feed on an observer that is no controller, an import's ``observes:`` for a port its assembly lacks
(``EF-7S``); a component reading another observer's output and an output that observer reads, the
meter reading the EMS's grid balance and a flow the EMS observes (``EF-7T``, §3.3); a controllable
output no controller selects or two do, a target input actuated twice (``EF-7U``); an entry selecting
a measured output or one an earlier entry ranks, a ranked output no entry selects, a weight reaching
999, two ports of one type at one weight (``EF-7V``); an output selected and fed explicitly to one
observer (``EF-25``, as ``DUPLICATE_FEED``); and two participants of one observer whose derived port
names collide (``EF-7W``). A dispatch target that is no input of the participant, on a channel that
forbids a dispatch or of another quantity than the channel dispatches (``EF-7J``, ``EF-7U``), and an
observed output its participant does not have or no channel accepts (``EF-7J``, ``EF-7Q``), the
post-construction port check refuses. Every weight, dispatch and actuation goes to the import record,
:class:`~hisim.energy_system.assemblies.record.ObserverRecord` and
:class:`~hisim.energy_system.assemblies.record.ActuationRecord`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.config.channels import ObservableFeed, ResolvedDynamicConnection
from hisim.energy_system.assemblies.record import (
    ActuationRecord,
    FeedRecord,
    ImportRecord,
    ObserverRecord,
    PriorityRecord,
)
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import FeedOverride, Port, Selection, Selector
from hisim.energy_system.imports_reader import ImportsReader
from hisim.energy_system.model import AggregatorFeed, AnyInputItem, DispatchSpec, ExplicitWire

if TYPE_CHECKING:  # pragma: no cover - the expansion imports this module
    from hisim.energy_system.assemblies.expansion import Handle, Unit


def class_name_of(unit: "Unit") -> str:
    """The short class name of a unit, which declarations are keyed by."""
    return unit.class_path.rsplit(".", 1)[-1]


@dataclass
class ObserverSlot:
    """One observer while the selection pass lowers it.

    Attributes:
        unit: The observing component.
        owner: How messages name the owner: ``import grid`` or ``component Meter``.
        owner_path: The import path or the site component's name.
        port: The observer port (``observes`` for a site entry).
        selection: What it observes: the import's ``observes:`` or the port's default.
        position: Where its feeds land in the unit's written inputs.
        source: The source map of the observer, for a message.
        handle: The port's handle in its instance, whose record the pass fills; ``None`` on the site.
        priorities: For a controller, its priority list, resolved.
    """

    unit: "Unit"
    owner: str
    owner_path: str
    port: str
    selection: Selection
    position: int
    source: str
    handle: Optional["Handle"] = None
    priorities: Optional[Tuple[Selector, ...]] = None
    matches: List["Match"] = field(default_factory=list)

    @property
    def label(self) -> str:
        """How messages name the observer: ``grid-ElectricityMeter (import grid, port reading)``."""
        return f"{self.unit.name} ({self.owner}, port {self.port})"


@dataclass
class Controllable:
    """One provided output an assembly states a controller may actuate (§4.4).

    Attributes:
        unit: The component whose output it is.
        output: The output.
        port: The provided port stating it.
        owner: The import path holding the port.
        via_handle: For ``controllable: {via: <need>}``, the need's handle.
        source: The source map of the port, for a message.
    """

    unit: "Unit"
    output: str
    port: Port
    owner: str
    via_handle: Optional["Handle"]
    source: str

    @property
    def key(self) -> Tuple[str, str]:
        """The observed output, ``(component, output)``."""
        return (self.unit.name, self.output)

    @property
    def text(self) -> str:
        """How messages name it."""
        how = (
            f"controllable: {{target_input: {self.port.controllable_target}}}"
            if self.port.controllable_target is not None
            else f"controllable: {{via: {self.port.controllable_via}}}"
        )
        return f"{self.unit.name}.{self.output} (port {self.owner}.{self.port.name}, {how})"


@dataclass
class Match:
    """One output an observer's selection matched, with the declaration it matched by."""

    unit: "Unit"
    declared: ObservableFeed
    selected_by: str
    override: Optional[FeedOverride]
    weight: int
    dispatch: Optional[DispatchSpec] = None
    control: str = "measured"

    @property
    def key(self) -> Tuple[str, str]:
        """The observed output, ``(component, output)``."""
        return (self.unit.name, self.declared.output)

    @property
    def text(self) -> str:
        """How messages name the observed output."""
        return f"{self.unit.name}.{self.declared.output}"

    @property
    def component_type(self) -> Optional[str]:
        """The feed's component type: the override's, else the declared one."""
        if self.override is not None and self.override.component_type is not None:
            return self.override.component_type
        return self.declared.component_type

    @property
    def tags(self) -> Tuple[str, ...]:
        """The feed's flow tags: the override's, else the declared ones."""
        if self.override is not None and self.override.tags is not None:
            return tuple(self.override.tags)
        return tuple(self.declared.tags)

    @property
    def type_key(self) -> str:
        """What "one type" means for the k-th further instance: the component type, else the output."""
        return self.declared.component_type or f"{self.declared.source_class}.{self.declared.output}"

    def feed(self) -> AggregatorFeed:
        """The aggregator feed the match lowers to."""
        return AggregatorFeed(
            source=self.unit.name,
            output=self.declared.output,
            component_type=self.component_type,
            tags=self.tags,
            weight=self.weight,
            dispatch=self.dispatch,
        )


class SelectionLowering:  # pylint: disable=too-few-public-methods  # one pass, one entry point
    """Lowers every observer of one expanded system, after every port is bound."""

    def __init__(
        self,
        units: Sequence["Unit"],
        slots: Sequence[ObserverSlot],
        controllables: Sequence[Controllable],
        feeds_of: Callable[["Unit"], Mapping[str, Tuple[ObservableFeed, ...]]],
        record: ImportRecord,
        item_text: Callable[[AnyInputItem], str],
    ) -> None:
        """Prepares the pass.

        Args:
            units: Every component of the expanded system, in candidate order.
            slots: Every observer, site entries first, then by import, instance and member.
            controllables: Every active controllable output.
            feeds_of: The constructed observer's declared feeds, by source class name.
            record: The import record the pass writes its observers and actuations to.
            item_text: Renders an input item for the port records.
        """
        self.units = list(units)
        self.slots = list(slots)
        self.controllables = list(controllables)
        self.feeds_of = feeds_of
        self.record = record
        self.item_text = item_text
        self.by_name = {unit.name: unit for unit in self.units}

    @staticmethod
    def error(error_id: EnergySystemErrorId, location: str, problem: str, **kwargs: Any) -> EnergySystemAssemblyError:
        """One refusal of the pass."""
        return EnergySystemAssemblyError(error_id, location, problem, **kwargs)

    # ---------------------------------------------------------------------------------- entry point

    def lower(self) -> None:
        """Selects, ranks, lowers and checks every observer; fills the import record."""
        for slot in self.slots:
            slot.matches = self._select(slot)
        for slot in self.slots:
            if slot.priorities is None:
                self._refuse_ranked_without_controller(slot)
            else:
                self._rank(slot)
        self._check_controllables()
        for slot in self.slots:
            self._land(slot)
        self._check_actuated_once()
        aggregators = self._aggregators()
        self._check_duplicates(aggregators)
        self._check_port_names(aggregators)
        self._check_double_count(aggregators)

    # ------------------------------------------------------------------------------------ selection

    def _observer_feeds(self, slot: ObserverSlot) -> Mapping[str, Tuple[ObservableFeed, ...]]:
        """The constructed observer's declared feeds; an observer that declares none cannot observe (``EF-7H``)."""
        feeds = self.feeds_of(slot.unit)
        if not any(feeds.values()):
            raise self.error(
                EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
                slot.owner,
                f"{slot.label} observes, but the constructed {class_name_of(slot.unit)} declares no dynamic default "
                f"connections, so it has no candidates to select from ({slot.source}).",
                remedy=(
                    f"Add the outputs it observes to {slot.unit.class_path}'s constructor: "
                    "add_dynamic_default_connections, one connection with its tags and weight per source class and "
                    "output."
                ),
            )
        return feeds

    def _candidates(self, slot: ObserverSlot) -> List[Tuple["Unit", ObservableFeed]]:
        """Every output of the system the observer declares a feed from, in candidate order."""
        feeds = self._observer_feeds(slot)
        candidates: List[Tuple["Unit", ObservableFeed]] = []
        for unit in self.units:
            if unit is slot.unit:
                continue
            for declared in feeds.get(class_name_of(unit), ()):
                candidates.append((unit, declared))
        return candidates

    def _select(self, slot: ObserverSlot) -> List[Match]:
        """The matches of one observer's selection, in candidate order (§4.2)."""
        candidates = self._candidates(slot)
        listed = ", ".join(f"{unit.name}.{declared.output}" for unit, declared in candidates) or "none"
        matches: List[Match] = []
        if slot.selection.declared:
            for unit, declared in candidates:
                matches.append(Match(unit, declared, Selection.DECLARED, None, declared.weight))
        else:
            for selector in slot.selection.selectors:
                if selector.required and not any(selector.matches(declared) for _unit, declared in candidates):
                    raise self.error(
                        EnergySystemErrorId.OBSERVER_SELECTION,
                        slot.owner,
                        f"the required selector {selector.text()} of {slot.label} matches nothing; an output its "
                        f"class {class_name_of(slot.unit)} declares no feed from is no match. Candidates: {listed} "
                        f"({slot.source}).",
                    )
            for unit, declared in candidates:
                first = next((item for item in slot.selection.selectors if item.matches(declared)), None)
                if first is None:
                    continue
                override = first.feed
                weight = override.weight if override is not None and override.weight is not None else declared.weight
                matches.append(Match(unit, declared, first.text(), override, weight))
        if not matches:
            raise self.error(
                EnergySystemErrorId.OBSERVER_SELECTION,
                slot.owner,
                f"{slot.label} is idle: its selection {slot.selection.text()} matches no output of the system "
                f"(candidates: {listed}); an observer observing nothing is refused, never kept ({slot.source}).",
                remedy="Select what it observes, or remove the observer.",
            )
        return matches

    def _refuse_ranked_without_controller(self, slot: ObserverSlot) -> None:
        """Refuses a ranked feed on an observer that is no controller: ranking is the priorities' (§4.4)."""
        for match in slot.matches:
            if match.weight != ObservableFeed.MEASURED_ONLY_WEIGHT:
                raise self.error(
                    EnergySystemErrorId.OBSERVER_SELECTION,
                    slot.owner,
                    f"{slot.label} would rank {match.text} at weight {match.weight}, but it is no controller: only a "
                    f"controller assembly ranks, deriving its weights from its priorities ({slot.source}).",
                    remedy="Observe it through a controller assembly (control/ems_self_consumption), or narrow this "
                    "observer's selection to the outputs its class measures (weight 999).",
                )

    # -------------------------------------------------------------------------------------- ranking

    def _class_defaults(self, slot: ObserverSlot) -> Dict[str, int]:
        """The controller's default weight per type, from its own declarations; one per type."""
        defaults: Dict[str, int] = {}
        for declared in (feed for feeds in self._observer_feeds(slot).values() for feed in feeds):
            if not declared.is_ranked:
                continue
            key = declared.component_type or f"{declared.source_class}.{declared.output}"
            if defaults.setdefault(key, declared.weight) != declared.weight:
                raise self.error(
                    EnergySystemErrorId.PRIORITY_WEIGHTS,
                    slot.owner,
                    f"{slot.unit.class_path} declares the type {key} at the weights {defaults[key]} and "
                    f"{declared.weight}; a class default is one weight per type ({slot.source}).",
                )
        return defaults

    def _rank(self, slot: ObserverSlot) -> None:  # pylint: disable=too-many-locals  # the rule of §4.4 in one place
        """Derives the controller's weights from its priorities and decides every ranked feed's dispatch."""
        assert slot.priorities is not None
        defaults = self._class_defaults(slot)
        for match in slot.matches:
            if match.override is not None and match.override.weight is not None:
                raise self.error(
                    EnergySystemErrorId.PRIORITY_WEIGHTS,
                    slot.owner,
                    f"the selector {match.selected_by} of the controller {slot.label} writes the weight "
                    f"{match.override.weight}; a controller derives every weight from its priorities and no file "
                    f"authors one ({slot.source}).",
                )
        ranked = [match for match in slot.matches if match.declared.is_ranked]
        assigned: Dict[int, int] = {}
        counts: Dict[str, int] = {}
        previous = 0
        records: List[PriorityRecord] = []
        for entry in slot.priorities:
            if entry.feed is not None:
                raise self.error(
                    EnergySystemErrorId.PRIORITY_WEIGHTS,
                    slot.owner,
                    f"the priority {entry.text()} of {slot.label} writes a feed: block; a priority only orders what "
                    f"the controller observes, and every weight is derived ({slot.source}).",
                )
            selected = [match for match in slot.matches if entry.matches(match.declared)]
            if entry.required and not selected:
                raise self.error(
                    EnergySystemErrorId.PRIORITY_WEIGHTS,
                    slot.owner,
                    f"the required priority {entry.text()} of {slot.label} ranks nothing; it observes "
                    f"{', '.join(match.text for match in slot.matches)} ({slot.source}).",
                )
            for match in selected:
                if not match.declared.is_ranked:
                    raise self.error(
                        EnergySystemErrorId.PRIORITY_WEIGHTS,
                        slot.owner,
                        f"the priority {entry.text()} of {slot.label} selects {match.text}, which "
                        f"{class_name_of(slot.unit)} only measures (weight 999); a priority ranks what the class "
                        f"ranks ({slot.source}).",
                    )
                if id(match) in assigned:
                    raise self.error(
                        EnergySystemErrorId.PRIORITY_WEIGHTS,
                        slot.owner,
                        f"the priority {entry.text()} of {slot.label} selects {match.text}, which an earlier entry "
                        f"already ranks at {assigned[id(match)]}; each output has one rank ({slot.source}).",
                    )
            if not selected:
                records.append(PriorityRecord(entry=entry.text(), ranked=()))
                continue
            weights: List[int] = []
            for match in selected:
                k = counts.get(match.type_key, 0)
                counts[match.type_key] = k + 1
                weights.append(defaults[match.type_key] + k)
            lowest = min(weights)
            if lowest <= previous:
                weights = [weight + previous + 1 - lowest for weight in weights]
            previous = max(previous, *weights)
            lines = []
            for match, weight in zip(selected, weights):
                if weight >= ObservableFeed.MEASURED_ONLY_WEIGHT:
                    raise self.error(
                        EnergySystemErrorId.PRIORITY_WEIGHTS,
                        slot.owner,
                        f"the priorities of {slot.label} derive the weight {weight} for {match.text}; a ranked "
                        f"weight stays below {ObservableFeed.MEASURED_ONLY_WEIGHT}, which marks a measured feed "
                        f"({slot.source}).",
                    )
                assigned[id(match)] = weight
                match.weight = weight
                lines.append(
                    f"{match.text} ({match.declared.component_type or '-'}): class default "
                    f"{defaults[match.type_key]} -> weight {weight}"
                )
            records.append(PriorityRecord(entry=entry.text(), ranked=tuple(lines)))
        unranked = [match for match in ranked if id(match) not in assigned]
        if unranked:
            raise self.error(
                EnergySystemErrorId.PRIORITY_WEIGHTS,
                slot.owner,
                f"{slot.label} observes {', '.join(match.text for match in unranked)}, which its class ranks, but no "
                f"entry of its priorities selects "
                + ("it" if len(unranked) == 1 else "them")
                + f": its weight cannot be derived. Priorities: "
                f"[{', '.join(entry.text() for entry in slot.priorities)}] ({slot.source}).",
                remedy="Add an entry selecting it to the controller's 'priorities', or narrow its observes: selection.",
            )
        seen: Dict[Tuple[Optional[str], int], Match] = {}
        for match in ranked:
            key = (match.component_type, match.weight)
            if key in seen:
                raise self.error(
                    EnergySystemErrorId.PRIORITY_WEIGHTS,
                    slot.owner,
                    f"{slot.label} ranks {seen[key].text} and {match.text}, both {match.component_type}, at the weight "
                    f"{match.weight}; the controller pairs a dispatch with its input by type and weight, so two "
                    f"ports of one type never share one ({slot.source}).",
                )
            seen[key] = match
        for match in ranked:
            self._dispatch(slot, match)
        slot_record = self._observer_record(slot)
        slot_record.priorities.extend(records)

    def _dispatch(self, slot: ObserverSlot, match: Match) -> None:
        """Decides the dispatch of one ranked feed from what the observed output states (§4.4, D21).

        ``controllable: {target_input: …}`` lowers to that dispatch target; whether it is an input of
        the participant on a channel of the controller that allows a dispatch, the post-construction
        port check verifies on the instances.
        """
        controllable = next((item for item in self.controllables if item.key == match.key), None)
        if controllable is not None and controllable.port.controllable_target is not None:
            target = controllable.port.controllable_target
            match.dispatch = DispatchSpec(target_input=target)
            match.control = f"target_input {target}"
            return
        if controllable is not None:
            via = controllable.via_handle
            bound = via.bound_partner if via is not None else None
            if bound != slot.unit.name:
                raise self.error(
                    EnergySystemErrorId.ACTUATION,
                    slot.owner,
                    f"{slot.label} ranks {controllable.text}, but its need '{controllable.port.controllable_via}' "
                    + (f"is bound to {bound}" if bound is not None else "is not bound")
                    + f": the feed and its modifier are bound together or not at all ({controllable.source}).",
                    remedy=f"Bind it, optional-bind: {{{controllable.port.controllable_via}: <the controller's "
                    "import>}, or narrow the controller's observes: selection.",
                )
            match.dispatch = DispatchSpec()
            match.control = f"via {controllable.port.controllable_via}"
            return
        match.dispatch = DispatchSpec()
        match.control = "rank-only"

    def _check_controllables(self) -> None:
        """A controllable output is actuated by the one controller it binds, never by none or two (§4.3, §4.4)."""
        for controllable in self.controllables:
            rankers = [
                slot
                for slot in self.slots
                if slot.priorities is not None
                and any(match.key == controllable.key and match.declared.is_ranked for match in slot.matches)
            ]
            if len(rankers) > 1:
                raise self.error(
                    EnergySystemErrorId.ACTUATION,
                    f"import {controllable.owner}",
                    f"{controllable.text} is actuated twice, by {' and '.join(slot.label for slot in rankers)}; each "
                    f"target is actuated exactly once (D21) ({controllable.source}).",
                    remedy="Narrow the observes: selection of all but one controller.",
                )
            if controllable.port.controllable_target is not None:
                if not rankers and not controllable.port.controllable_optional:
                    raise self.error(
                        EnergySystemErrorId.ACTUATION,
                        f"import {controllable.owner}",
                        f"{controllable.text} has no controller: it states the device input it is actuated through "
                        f"and binds the one controller, but no controller assembly observes and ranks it "
                        f"({controllable.source}).",
                        remedy="Add a controller import (control/ems_self_consumption) whose selection observes it.",
                    )
                continue
            via = controllable.via_handle
            bound = via.bound_partner if via is not None else None
            if bound is not None and bound not in [slot.unit.name for slot in rankers]:
                raise self.error(
                    EnergySystemErrorId.ACTUATION,
                    f"import {controllable.owner}",
                    f"{controllable.text} is bound to the controller {bound} through "
                    f"'{controllable.port.controllable_via}', but {bound} does not rank it: a bound controllable "
                    f"output no priority selects is refused ({controllable.source}).",
                    remedy=f"Let {bound} observe it and add a priority entry selecting it.",
                )

    # -------------------------------------------------------------------------------------- landing

    def _observer_record(self, slot: ObserverSlot) -> ObserverRecord:
        """The record of one observer, created on first use."""
        for record in self.record.observers:
            if record.observer == slot.unit.name and record.port == slot.port:
                return record
        record = ObserverRecord(
            observer=slot.unit.name, owner=slot.owner_path, port=slot.port, selection=slot.selection.text()
        )
        self.record.observers.append(record)
        return record

    def _land(self, slot: ObserverSlot) -> None:
        """Writes the observer's feeds at its placeholder and records them and its actuations."""
        unit = slot.unit
        if slot.position in unit.lowered:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                slot.owner,
                f"the placeholder of '{unit.name}' at inputs[{slot.position}] is filled twice ({slot.source}).",
            )
        items: List[AnyInputItem] = [match.feed() for match in slot.matches]
        unit.lowered[slot.position] = items
        unit.notes[slot.position] = f"observer {slot.port} of {slot.owner_path}, selection {slot.selection.text()}"
        record = self._observer_record(slot)
        for match in slot.matches:
            target_input = match.dispatch.target_input if match.dispatch is not None else None
            record.feeds.append(
                FeedRecord(
                    source=match.unit.name,
                    output=match.declared.output,
                    component_type=match.component_type,
                    tags=match.tags,
                    weight=match.weight,
                    dispatch=None if match.dispatch is None else (target_input or "{}"),
                    input_port=ResolvedDynamicConnection.input_name_for(match.unit.name, match.declared.output),
                    dispatch_port=(
                        None
                        if match.dispatch is None
                        else ResolvedDynamicConnection.dispatch_name_for(
                            match.unit.name, match.declared.output, target_input
                        )
                    ),
                    selected_by=match.selected_by,
                    control=match.control,
                )
            )
            if slot.priorities is not None and match.control.startswith("target_input"):
                self.record.actuations.append(
                    ActuationRecord(
                        controller=unit.name,
                        output=match.text,
                        weight=match.weight,
                        target=f"{match.unit.name}.{target_input}",
                        kind="target_input",
                    )
                )
            elif slot.priorities is not None and match.control.startswith("via"):
                controllable = next(item for item in self.controllables if item.key == match.key)
                via = controllable.via_handle
                lowered = ", ".join(via.record.get("lowered_to", ())) if via is not None else ""
                self.record.actuations.append(
                    ActuationRecord(
                        controller=unit.name,
                        output=match.text,
                        weight=match.weight,
                        target=f"{controllable.owner}.{controllable.port.controllable_via} ({lowered})",
                        kind="via",
                    )
                )
        if slot.handle is not None:
            slot.handle.record = {
                "state": "controller" if slot.priorities is not None else "observer",
                "partner": f"{len(items)} feed{'s' if len(items) != 1 else ''}",
                "verb": "selection",
                "lowered_to": tuple(f"{unit.name}.inputs: {self.item_text(item)}" for item in items),
            }

    def _check_actuated_once(self) -> None:
        """A target input a controller actuates is wired by nothing else (§4.3: each exactly once)."""
        targets: Dict[Tuple[str, str], str] = {}
        for actuation in self.record.actuations:
            if actuation.kind != "target_input":
                continue
            component, target_input = actuation.target.rsplit(".", 1)
            key = (component, target_input)
            if key in targets:
                raise self.error(
                    EnergySystemErrorId.ACTUATION,
                    f"components.{component}",
                    f"{component}.{target_input} is actuated by {targets[key]} and by {actuation.controller}; each "
                    "target input is actuated exactly once (D21).",
                )
            targets[key] = actuation.controller
            unit = self.by_name[component]
            for item in self._items_of(unit):
                if isinstance(item, ExplicitWire) and item.input == target_input:
                    raise self.error(
                        EnergySystemErrorId.ACTUATION,
                        f"components.{component}",
                        f"{component}.{target_input} is actuated by {actuation.controller} and also wired from "
                        f"{item.source}.{item.output}; each target input is actuated exactly once (D21).",
                    )

    # --------------------------------------------------------------------------------------- checks

    @staticmethod
    def _items_of(unit: "Unit", written_only: bool = False) -> List[AnyInputItem]:
        """Every input item of a unit as the expanded file will write it, sources expanded."""
        items: List[AnyInputItem] = []
        for item in unit.entry.inputs:
            if unit.identity is None:
                items.append(item)
                continue
            if item.source in unit.dropped_names:
                continue
            items.append(item.model_copy(update={"source": unit.local_names.get(item.source, item.source)}))
        if not written_only:
            for position in sorted(unit.lowered):
                items.extend(unit.lowered[position])
        return items

    def _aggregators(self) -> Dict[str, List[Tuple[AggregatorFeed, str]]]:
        """Every unit holding aggregator feeds, with each feed and where it came from."""
        landed = {(slot.unit.name, slot.position): slot for slot in self.slots}
        aggregators: Dict[str, List[Tuple[AggregatorFeed, str]]] = {}
        for unit in self.units:
            found: List[Tuple[AggregatorFeed, str]] = []
            for item in self._items_of(unit, written_only=True):
                if isinstance(item, AggregatorFeed):
                    found.append((item, "written in its inputs"))
            for position in sorted(unit.lowered):
                slot = landed.get((unit.name, position))
                for item in unit.lowered[position]:
                    if not isinstance(item, AggregatorFeed):
                        continue
                    if slot is not None:
                        match = next(entry for entry in slot.matches if entry.key == (item.source, item.output))
                        found.append((item, f"selected by {slot.owner}.{slot.port} ({match.selected_by})"))
                    else:
                        found.append((item, unit.notes.get(position, "lowered by a port")))
            if found:
                aggregators[unit.name] = found
        return aggregators

    def _check_duplicates(self, aggregators: Dict[str, List[Tuple[AggregatorFeed, str]]]) -> None:
        """An output selected and fed explicitly to one observer is refused, as ``DUPLICATE_FEED`` (§4.2)."""
        for name, feeds in aggregators.items():
            seen: Dict[Tuple[str, str], str] = {}
            for feed, origin in feeds:
                key = (feed.source, feed.output or "")
                if key in seen:
                    raise self.error(
                        EnergySystemErrorId.DUPLICATE_FEED,
                        f"components.{name}.inputs",
                        f"'{name}' observes '{feed.source}.{feed.output}' twice: {seen[key]} and {origin}; a dynamic "
                        "component sums what it is given, so the flow would be counted twice.",
                        remedy="Remove the explicit feed, or narrow the selection.",
                    )
                seen[key] = origin

    def _check_port_names(self, aggregators: Dict[str, List[Tuple[AggregatorFeed, str]]]) -> None:
        """Two participants of one observer may not derive one port name (hisim-lt0b.11, ``EF-7W``)."""
        for name, feeds in aggregators.items():
            owners: Dict[str, str] = {}
            for feed, _origin in feeds:
                if feed.output is None:
                    continue
                ports = [ResolvedDynamicConnection.input_name_for(feed.source, feed.output)]
                if feed.dispatch is not None:
                    ports.append(
                        ResolvedDynamicConnection.dispatch_name_for(
                            feed.source, feed.output, feed.dispatch.target_input
                        )
                    )
                for port in ports:
                    previous = owners.get(port)
                    if previous is not None and previous != f"{feed.source}.{feed.output}":
                        raise self.error(
                            EnergySystemErrorId.DERIVED_PORT_COLLISION,
                            f"components.{name}.inputs",
                            f"'{name}' would grow the port '{port}' for {previous} and for "
                            f"{feed.source}.{feed.output}: a derived port name is the participant's name with '_' "
                            "for '-' (NameSyntax.port_name_part), unique per observer.",
                            remedy="Rename the site component or the import so the two names differ after the "
                            "replacement.",
                        )
                    owners[port] = f"{feed.source}.{feed.output}"

    def _check_double_count(self, aggregators: Dict[str, List[Tuple[AggregatorFeed, str]]]) -> None:
        """Refuses a component reading another observer's output and an output that observer reads (§3.3, §4.3)."""
        observed: Dict[str, Set[Tuple[str, str]]] = {
            name: {(feed.source, feed.output or "") for feed, _origin in feeds} for name, feeds in aggregators.items()
        }
        for name, feeds in aggregators.items():
            for other, other_observed in observed.items():
                if other == name:
                    continue
                reads = sorted({feed.output or "" for feed, _origin in feeds if feed.source == other})
                if not reads:
                    continue
                both = sorted(observed[name] & other_observed)
                if both:
                    listed = ", ".join(f"{source}.{output}" for source, output in both)
                    raise self.error(
                        EnergySystemErrorId.DOUBLE_COUNT,
                        f"components.{name}.inputs",
                        f"'{name}' observes {other}.{', '.join(reads)}, the balance {other} sums over what it "
                        f"observes, and also {listed}, which {other} observes: the flow would be counted twice "
                        "(assemblies_spec.md §3.3, §4.3).",
                        remedy=f"Let '{name}' observe only {other}'s balance — on the grid import, observes: "
                        f"[{{output: {reads[0]}}}] — or only the flows, without {other}.",
                    )


def resolve_priorities(raw: Any, location: str) -> Tuple[Selector, ...]:
    """Reads a controller's resolved priority list (the reader's selector grammar)."""
    return ImportsReader.selectors(raw, location)


__all__ = ["Controllable", "Match", "ObserverSlot", "SelectionLowering", "resolve_priorities"]
