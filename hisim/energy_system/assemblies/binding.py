"""Binding the ports of every import and site entry, and lowering them to ordinary file items (§3-§6).

``assemblies_spec.md`` §3.1-§3.3, §5, §6 and the lean v1 of §13.1, decided from the files alone
(every entry states its ``class:``). What each port lowers to:

- a **need** ``{into: [Member], partner: Class}`` binds to one component whose class is a partner
  class and lowers to a bare name at the member's ``{$port: …}`` placeholder (its default
  connections from that class, which the wiring expands and checks), or to its ``wires:``;
- a **circuit end** ``{circuit: c, member: X | [X, Y]}`` binds to the one other end of circuit
  ``c`` and lowers, in both directions, to a bare name of every member of the other end at every
  placeholder of the port (D25: a member that neither owns nor reads a circuit output is refused by
  the wiring as a bare name without default connections);
- a **carrier need** ``{carrier: c, outputs: [Member.Output]}`` needs the one provider of ``c`` in
  scope; for a fuel it lowers to a bare name of each consumer at the provider meter's placeholder
  (the meter's dynamic default connections expand it), for electricity to nothing; the consuming
  outputs go to the wiring, which checks their carrier and the meter's feeds;
- a **fact need** ``{fact: f, into: [Member]}`` lowers to a ``sizing_sources`` line naming the one
  provider in scope — a site entry whose class contributes ``f``, or an import's provided fact
  ``{fact: f, member: M}`` — and with ``many: true`` to the list of every provider, in written order;
- an **observer port** and a **controllable** provided output go to the selection plan, which the
  wiring runs on the constructed components (:mod:`.selection`).

**The default rule** mirrors the sizing engine's: a port binds to the one candidate in scope, and a
verb (``bind:``, ``optional-bind:``, ``none:``) decides every other case for a need, a circuit end
and a scalar fact need. In scope are the site entries, the live components of groups and variants,
and every member of every import; a port never binds into its own instance. A verb names an import
or a component the file declares, absent only through a disabled group or an unselected option. The
refusals: ``EF-7A`` no partner, ``EF-7B`` several and no verb, ``EF-7C`` ``none:`` or
``optional-bind:`` on a required port, ``EF-7D`` an undeclared partner or an absent ``bind:``
partner, ``EF-7E`` an optional port with candidates and no verb, ``EF-7F`` a verb on an inactive
port, ``EF-7G`` a verb on a port no verb binds, ``EF-7H`` a bound provided output the need's wires do
not read, ``EF-7J`` a partner or a port that does not fit, ``EF-7K`` a circuit end of another
circuit, ``EF-7L`` a carrier without exactly one provider, or a fuel provider without a consumer.
Each names the owner and the port; the refusals that decide among candidates (``EF-7A``, ``EF-7B``,
``EF-7C``, ``EF-7E``, an absent ``bind:`` partner) also list the candidates and end in a paste-ready
verb line.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.config.contributions import declared_facts_of
from hisim.energy_system.assemblies.addresses import Unit
from hisim.energy_system.assemblies.record import ImportRecord, PortRecord
from hisim.energy_system.assemblies.selection import Controllable, Observer
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind, PortState, Selection
from hisim.energy_system.model import AnyInputItem, ConsumingOutput, DefaultInputs, ExplicitWire, SourceReference


@dataclass
class Owner:
    """An import instance or a site entry: what owns ports and what a verb names.

    Attributes:
        reference: How a verb names it: ``Building``, ``heating``, ``pv.east``.
        label: How a message names it: ``component 'Building'``, ``import 'pv' (instance 'east')``.
        verb_site: Where its verbs are written, for the paste-ready line.
        verbs: Those verbs.
        units: Its components by local name: an instance's members, or the site entry itself.
        ports: Its ports.
        states: Each port's state for its parameters.
        observes: The import's ``observes:``, replacing its observer port's default.
    """

    reference: str
    label: str
    verb_site: str
    verbs: BindingVerbs
    units: Dict[str, Unit]
    ports: Mapping[str, Port]
    states: Mapping[str, PortState]
    observes: Optional[Selection] = None


@dataclass(frozen=True)
class Candidate:
    """One thing a port may bind to: a component (a need's partner, a fact's provider) or a circuit end.

    Attributes:
        owner: The owner holding it.
        unit: The partner or provider component, for a need or a fact.
        port: The other end's port, for a circuit; the provided port, for a fact an import provides.
        output: A provided output a need is bound to (``<import>.<port>``), else ``""``.
    """

    owner: Owner
    unit: Optional[Unit] = None
    port: Optional[Port] = None
    output: str = ""

    @property
    def name(self) -> str:
        """How messages and records name it: a component, or a circuit end ``<owner>.<port>``."""
        return self.unit.name if self.unit is not None else f"{self.owner.reference}.{getattr(self.port, 'name', '')}"


class PortBinder:
    """Decides and lowers every port of one file, with every owner in scope."""

    #: The kinds a verb may bind; a carrier and a many fact need bind every provider in scope.
    VERB_KINDS = (PortKind.NEED, PortKind.CIRCUIT, PortKind.FACT)

    def __init__(
        self,
        sites: Mapping[str, Owner],
        imports: Mapping[str, Sequence[Owner]],
        record: ImportRecord,
        absent: Optional[Mapping[str, str]] = None,
    ) -> None:
        """Prepares the binding.

        Args:
            sites: Every live component of the file by name, the site entries first.
            imports: Import key to its instances' owners (one for an import without instances), in file order.
            record: The import record, which receives the consuming outputs and the selection plan.
            absent: Each component a disabled group or an unselected option leaves out, to the reason.
        """
        self.sites = sites
        self.imports = imports
        self.record = record
        self.absent = absent or {}
        self.declared = tuple(sites) + tuple(self.absent) + tuple(imports)
        self.owners: List[Owner] = list(sites.values()) + [owner for owners in imports.values() for owner in owners]
        #: A circuit end already joined, ``(owner, port)`` to the other end and the items lowered.
        self.joined: Dict[Tuple[str, str], Tuple[Candidate, Tuple[str, ...]]] = {}
        #: Each carrier's providers, ``(owner, port)``, and each provider's consumers.
        self.providers: Dict[str, List[Tuple[Owner, Port]]] = {}
        self.consumers: Dict[Tuple[str, str], List[str]] = {}
        self._facts: Dict[str, Tuple[str, ...]] = {}

    def bind(self) -> Dict[str, List[PortRecord]]:
        """Decides every port of every owner, lowers it, and plans the observers and controllables.

        Returns:
            Owner reference (``Building``, ``heating``, ``pv.east``) to its ports' records.
        """
        for owner in self.owners:
            for name, port in owner.ports.items():
                if port.kind == PortKind.CARRIER and port.is_provision and owner.states[name] != PortState.INACTIVE:
                    self.providers.setdefault(port.carrier or "", []).append((owner, port))
        for carrier, providers in self.providers.items():
            if len(providers) > 1:
                raise self.error(
                    EnergySystemErrorId.CARRIER_PROVIDER,
                    providers[1][0],
                    f"{carrier} has {len(providers)} providers, "
                    + ", ".join(f"{owner.reference}.{port.name}" for owner, port in providers)
                    + "; a system has exactly one provider per carrier.",
                )
        records: Dict[str, List[PortRecord]] = {}
        for owner in self.owners:
            for written in owner.verbs.ports():
                item = owner.ports.get(written)
                if item is None or item.kind not in self.VERB_KINDS or item.is_provision or item.many:
                    raise self.error(
                        EnergySystemErrorId.UNKNOWN_PORT,
                        owner,
                        f"a verb names the port '{written}', which "
                        + (f"is a {item.kind.value} port" if item is not None else f"is no port of {owner.label}")
                        + "; a verb binds a need, a circuit end or a fact need of one provider (a carrier need and a "
                        "many fact need bind every provider in scope).",
                        alternatives=[name for name, other in owner.ports.items() if other.kind in self.VERB_KINDS],
                        alternatives_label="ports a verb binds",
                        offending_value=written,
                    )
            records[owner.reference] = [self.decide(owner, name, port) for name, port in owner.ports.items()]
        for carrier, providers in self.providers.items():
            owner, port = providers[0]
            consumers = self.consumers.get((owner.reference, port.name), [])
            if not consumers and carrier != "electricity":
                raise self.error(
                    EnergySystemErrorId.CARRIER_PROVIDER,
                    owner,
                    f"the provider of {carrier} '{port.name}' has no bound consumer; an idle connection is refused "
                    "(§5.2).",
                )
            records[owner.reference] = [
                replace(item, partner=", ".join(consumers)) if item.port == port.name else item
                for item in records[owner.reference]
            ]
        self._plan_controllables(records)
        return records

    @staticmethod
    def error(error_id: EnergySystemErrorId, owner: Owner, problem: str, **kwargs: Any) -> EnergySystemAssemblyError:
        """One refusal of the binding, located at the owner."""
        return EnergySystemAssemblyError(error_id, owner.label, problem, **kwargs)

    # ----------------------------------------------------------------------------------- deciding

    @staticmethod
    def listed(candidates: Sequence[Candidate]) -> str:
        """The candidates' names for a message."""
        return ", ".join(candidate.name for candidate in candidates) or "none"

    def decide(  # pylint: disable=too-many-return-statements  # one return per decision of §3.1
        self, owner: Owner, name: str, port: Port
    ) -> PortRecord:
        """Decides one port by its verb or by the default rule, and lowers it (§3.1, §3.3)."""
        state = owner.states[name]
        written = owner.verbs.verb_for(name)
        if state == PortState.INACTIVE and written is not None:
            raise self.error(
                EnergySystemErrorId.VERB_ON_INACTIVE_PORT,
                owner,
                f"the port '{name}' is inactive with these parameters (active_when/required_when), yet "
                f"{owner.verb_site} writes '{written[0]}' for it.",
                remedy=f"Remove the '{written[0]}' line for '{name}', or change the parameters that switch it off.",
            )
        if state == PortState.INACTIVE:
            return PortRecord(name, state.value, state.value)
        if port.kind == PortKind.OBSERVER:
            return self._observe(owner, port)
        if port.is_provision:
            return PortRecord(name, state.value, "provided")
        if port.kind == PortKind.CARRIER:
            return self._carrier(owner, port, state)
        if port.many:
            return self._many_fact(owner, port, state)
        if (owner.reference, name) in self.joined:
            return self._joined(owner, port, state, written)
        optional = state == PortState.OPTIONAL
        what = f"the {state.value} port '{name}' ({self.wants(port)})"
        if written is not None:
            verb, target = written
            if verb == "none":
                if not optional:
                    candidates = self.candidates(owner, port)
                    raise self.error(
                        EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                        owner,
                        f"{what} is declined with 'none:', but a required port cannot be; candidates: "
                        f"{self.listed(candidates)}.",
                        remedy=self.paste(owner, port, candidates, False),
                    )
                return PortRecord(name, state.value, "declined", "none")
            assert target is not None
            if verb == "optional-bind" and not optional:
                raise self.error(
                    EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                    owner,
                    f"{what} is written 'optional-bind: {{{name}: {target}}}', but optional-bind: leaves a port "
                    "unbound when its partner is absent, and only an optional port may stay unbound.",
                    remedy=f"Write `bind: {{{name}: {target}}}` in {owner.verb_site} instead.",
                )
            head = target.split(".")[0]
            self.check_declared(owner, port, what, verb, target)
            partner, absent = self.resolve(owner, port, target)
            if partner is None:
                if verb == "optional-bind":
                    return PortRecord(name, state.value, f"not bound: {head} {absent}", verb)
                candidates = self.candidates(owner, port)
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    owner,
                    f"{what} is bound to '{target}', but '{head}' is {absent}; candidates: {self.listed(candidates)}.",
                    remedy=self.paste(owner, port, candidates, optional),
                )
            return self.lower(owner, port, state, partner, verb)
        candidates = self.candidates(owner, port)
        listed = self.listed(candidates)
        if len(candidates) == 1 and not optional:
            return self.lower(owner, port, state, candidates[0], "default")
        if len(candidates) > 1 and not optional:
            raise self.error(
                EnergySystemErrorId.PORT_AMBIGUOUS,
                owner,
                f"{what} has {len(candidates)} candidates, {listed}, and no verb.",
                remedy=self.paste(owner, port, candidates, False),
            )
        if candidates:
            raise self.error(
                EnergySystemErrorId.OPTIONAL_PORT_UNDECIDED,
                owner,
                f"{what} has the candidate{'s' if len(candidates) > 1 else ''} {listed} and no verb; adding a partner "
                "never silently changes another import.",
                remedy=self.paste(owner, port, candidates, True),
            )
        if optional:
            return PortRecord(name, state.value, "not bound: no candidate")
        raise self.error(
            EnergySystemErrorId.PORT_WITHOUT_PARTNER,
            owner,
            f"{what} has no partner in the file; candidates: none.",
            remedy=f"Add {self.wants(port, article=True)}, then " + self.paste(owner, port, (), False),
        )

    @staticmethod
    def wants(port: Port, article: bool = False) -> str:
        """What a port binds to, for a message: ``partner MockWeather``, ``circuit dhw``, ``fact f``."""
        if port.kind == PortKind.CIRCUIT:
            return f"{'an import or site entry with ' if article else ''}an end of the circuit {port.circuit}"
        if port.kind == PortKind.FACT:
            return f"{'a component contributing ' if article else ''}the fact {port.fact}"
        return f"{'a component of class' if article else 'partner'} {', '.join(port.partner)}"

    def candidates(self, owner: Owner, port: Port) -> List[Candidate]:
        """The candidates in scope, outside the owner, in file order (site entries first, then imports)."""
        if port.kind == PortKind.FACT:
            return [candidate for candidate in self.fact_providers(port.fact or "") if candidate.owner is not owner]
        if port.kind == PortKind.CIRCUIT:
            return [
                Candidate(other, port=end)
                for other in self.owners
                if other is not owner
                for end in other.ports.values()
                if end.kind == PortKind.CIRCUIT
                and end.circuit == port.circuit
                and other.states[end.name] != PortState.INACTIVE
                and other.verbs.verb_for(end.name) != ("none", None)
                and (other.reference, end.name) not in self.joined
            ]
        return [
            Candidate(other, unit=unit)
            for other in self.owners
            if other is not owner
            for unit in other.units.values()
            if unit.class_name in port.partner
        ]

    def fact_providers(self, fact: str) -> List[Candidate]:
        """Every provider of a fact in scope: a site entry whose class contributes it, an import's provided fact."""
        found: List[Candidate] = []
        for other in self.owners:
            if other.reference in self.sites:
                unit = other.units[other.reference]
                if unit.entry.class_path not in self._facts:
                    config = ClassBinder.config_class_of(unit.name, unit.entry)
                    self._facts[unit.entry.class_path] = declared_facts_of(config)
                if fact in self._facts[unit.entry.class_path]:
                    found.append(Candidate(other, unit=unit))
                continue
            for provided in other.ports.values():
                member = other.units.get(provided.members[0]) if provided.members else None
                if provided.kind == PortKind.FACT and provided.is_provision and provided.fact == fact and member:
                    if other.states[provided.name] != PortState.INACTIVE:
                        found.append(Candidate(other, unit=member, port=provided))
        return found

    @staticmethod
    def paste(owner: Owner, port: Port, candidates: Sequence[Candidate], optional: bool) -> str:
        """The paste-ready verb lines of a refusal."""
        references = list(
            dict.fromkeys(
                (
                    f"{candidate.owner.reference}.{candidate.port.name}"
                    if port.kind == PortKind.CIRCUIT and candidate.port is not None
                    else candidate.owner.reference
                )
                for candidate in candidates
            )
        ) or ["<partner>"]
        lines = [f"`bind: {{{port.name}: {reference}}}`" for reference in references]
        if optional:
            lines = [f"`optional-bind: {{{port.name}: {reference}}}`" for reference in references] + lines
            lines.append(f"`none: [{port.name}]`")
        return f"add to {owner.verb_site} one of " + ", ".join(lines) + "."

    def check_declared(self, owner: Owner, port: Port, what: str, verb: str, target: str) -> None:
        """Refuses a verb whose partner reference has a head the file does not declare.

        Raises:
            EnergySystemAssemblyError: ``EF-7D`` naming the head and the imports and components declared.
        """
        head = target.split(".")[0]
        if head not in self.declared:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                owner,
                f"{what} is bound to '{target}' with '{verb}:', but the file declares no import or component "
                f"'{head}'; a house without the partner leaves the verb out. Candidates of the port: "
                f"{self.listed(self.candidates(owner, port))}.",
                alternatives=self.declared,
                alternatives_label="imports and components the file declares",
                offending_value=head,
            )

    def resolve(self, owner: Owner, port: Port, target: str) -> Tuple[Optional[Candidate], str]:
        """Resolves a verb's partner reference among the port's candidates.

        A reference whose head the file declares but a disabled group or an unselected option leaves out
        is an absent partner, which ``optional-bind:`` accepts; a reference whose head exists but whose
        instance, port or members do not fit is refused, whatever the verb, because it is a mistake
        rather than an absence. The caller has refused a head the file does not declare.

        Returns:
            ``(candidate, "")``, or ``(None, why it is absent)``.

        Raises:
            EnergySystemAssemblyError: ``EF-7D`` for an instance or port the head does not have,
                ``EF-7B`` for several fitting candidates, ``EF-7J`` for none, ``EF-7K`` for a circuit
                end of another circuit, ``EF-7H`` for a provided output the need's wires do not read.
        """
        head, *rest = target.split(".")
        if head in self.sites:
            targets: Sequence[Owner] = (self.sites[head],)
        elif head in self.imports:
            targets = self.imports[head]
            if rest and targets[0].reference != head:  # an import with instances: the next part names one
                matching = [other for other in targets if other.reference == f"{head}.{rest[0]}"]
                if not matching:
                    raise self.error(
                        EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                        owner,
                        f"'{target}': the import '{head}' has no instance '{rest[0]}'.",
                        alternatives=[other.reference.split(".")[1] for other in targets],
                        alternatives_label="instances",
                    )
                targets, rest = matching, rest[1:]
        else:
            return None, self.absent[head]
        if len(rest) > 1:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT, owner, f"'{target}' names more than an instance and a port."
            )
        if rest and rest[0] not in targets[0].ports:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                owner,
                f"'{target}': {targets[0].label} has no port '{'.'.join(rest)}'.",
                alternatives=tuple(targets[0].ports),
                alternatives_label="ports",
            )
        if port.kind == PortKind.NEED and rest:
            return self.provided(owner, port, targets[0], rest[0], target), ""
        fitting = [
            candidate
            for candidate in self.candidates(owner, port)
            if candidate.owner in targets and (not rest or getattr(candidate.port, "name", None) == rest[0])
        ]
        if len(fitting) == 1:
            return fitting[0], ""
        if fitting:
            raise self.error(
                EnergySystemErrorId.PORT_AMBIGUOUS,
                owner,
                f"the port '{port.name}' is bound to '{target}', which holds {len(fitting)} of its candidates: "
                f"{', '.join(candidate.name for candidate in fitting)}.",
                remedy=f"Name the instance or the port: bind: {{{port.name}: {target}.<port>}}.",
            )
        ends = [end for other in targets for end in other.ports.values() if end.kind == PortKind.CIRCUIT]
        if port.kind == PortKind.CIRCUIT and any(end.circuit != port.circuit for end in ends):
            raise self.error(
                EnergySystemErrorId.CIRCUIT_MISMATCH,
                owner,
                f"the circuit end '{port.name}' (circuit {port.circuit}) is bound to '{target}', whose ends are of "
                + ", ".join(f"the circuit {end.circuit} ('{end.name}')" for end in ends)
                + "; a circuit binds only an end of its own circuit, the medium.",
            )
        raise self.error(
            EnergySystemErrorId.PORT_CONTRACT,
            owner,
            f"the port '{port.name}' is bound to '{target}', which holds no {self.wants(port)} that is free (it holds "
            + ", ".join(f"{unit.name} ({unit.class_name})" for other in targets for unit in other.units.values())
            + ").",
        )

    def provided(self, owner: Owner, port: Port, target: Owner, name: str, written: str) -> Candidate:
        """The partner of a need bound to a provided port, ``<import>[.<instance>].<port>`` (§3.3)."""
        provided = target.ports[name]
        if provided.kind != PortKind.PROVIDED or target.states[name] != PortState.PROVIDED:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                owner,
                f"'{written}': {target.label} provides no active output '{name}'.",
            )
        if port.wires is None or provided.output_name not in port.wires.values():
            read = ", ".join(sorted(set(port.wires.values()))) if port.wires else "its default connections"
            raise self.error(
                EnergySystemErrorId.BOUND_OUTPUT_NOT_READ,
                owner,
                f"the port '{port.name}' is bound to the provided output '{written}' ({provided.output}), "
                f"but it reads {read}.",
                remedy=f"Name '{provided.output_name}' in the port's wires, or bind the port to '{target.reference}'.",
            )
        if provided.output_member not in target.units:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                owner,
                f"'{written}': the member '{provided.output_member}' providing '{name}' is not in {target.label} "
                "with its parameters.",
            )
        return Candidate(target, unit=target.units[provided.output_member], output=provided.output_name)

    # ----------------------------------------------------------------------------------- lowering

    def lower(self, owner: Owner, port: Port, state: PortState, partner: Candidate, verb: str) -> PortRecord:
        """Lowers a decided need, circuit end or fact need at its members."""
        if port.kind == PortKind.CIRCUIT:
            return self._lower_circuit(owner, port, state, partner, verb)
        unit = partner.unit
        assert unit is not None
        lowered: List[str] = []
        if port.kind == PortKind.FACT:
            lowered = self._land_fact(owner, port, SourceReference(component=unit.name, fact=port.fact or ""), verb)
            return PortRecord(port.name, state.value, "bound", verb, unit.name, tuple(lowered))
        if port.wires is None and unit.class_name not in port.partner:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the port '{port.name}' is bound to {unit.name} ({unit.class_name}), which is not of its partner "
                f"class {', '.join(port.partner)}.",
            )
        items: List[AnyInputItem] = (
            [ExplicitWire(source=unit.name, input=target, output=output) for target, output in port.wires.items()]
            if port.wires is not None
            else [DefaultInputs(source=unit.name)]
        )
        for holder in (owner.units[member] for member in port.landing_members if member in owner.units):
            for position in holder.placeholder_positions(port.name):
                holder.lowered[position] = (items, f"port {port.name} bound to {unit.name} ({verb})")
                lowered.extend(f"{holder.name}.inputs: {self.item_text(item)}" for item in items)
        if not lowered:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the port '{port.name}' is bound, but none of its members {', '.join(port.landing_members)} is "
                f"present with these parameters and carries a '{{$port: {port.name}}}' placeholder, so its items have "
                "nowhere to land.",
            )
        return PortRecord(port.name, state.value, "bound", verb, unit.name, tuple(lowered))

    def _land_fact(self, owner: Owner, port: Port, value: Any, verb: str) -> List[str]:
        """Writes a fact need's ``sizing_sources`` line into every member it lowers into (§6)."""
        fact = port.fact or ""
        lowered: List[str] = []
        for unit in (owner.units[member] for member in port.into if member in owner.units):
            if fact in unit.entry.sizing_sources:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    owner,
                    f"the fact port '{port.name}' lowers {fact} into '{unit.name}', which writes a sizing_sources "
                    "line for it already.",
                )
            if fact in unit.sizing:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    owner,
                    f"the fact port '{port.name}' lowers {fact} into '{unit.name}', which another fact port lowered "
                    f"it into already ({unit.sizing[fact][1]}); a member has one sizing_sources line per fact.",
                )
            unit.sizing[fact] = (value, f"port {port.name} bound ({verb})")
            text = value.text if isinstance(value, SourceReference) else [item.text for item in value]
            lowered.append(f"{unit.name}.sizing_sources.{fact}: {text}")
        if not lowered:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the fact port '{port.name}' is active, but none of its members {', '.join(port.into)} is present.",
            )
        return lowered

    def _many_fact(self, owner: Owner, port: Port, state: PortState) -> PortRecord:
        """A ``many: true`` fact need: the list of every provider in scope, in written order (§6)."""
        providers = [
            candidate.unit for candidate in self.fact_providers(port.fact or "") if candidate.owner is not owner
        ]
        names = [unit.name for unit in providers if unit is not None]
        if not names:
            if state == PortState.OPTIONAL:
                return PortRecord(port.name, state.value, "not bound: no candidate")
            raise self.error(
                EnergySystemErrorId.PORT_WITHOUT_PARTNER,
                owner,
                f"the required port '{port.name}' (many providers of the fact {port.fact}) finds none in the file.",
                remedy=f"Add a component contributing {port.fact} (SIZING_CONTRIBUTIONS) or an import providing it.",
            )
        references = tuple(SourceReference(component=name, fact=port.fact or "") for name in names)
        lowered = self._land_fact(owner, port, references, "default")
        return PortRecord(port.name, state.value, "bound", "default", ", ".join(names), tuple(lowered))

    def _lower_circuit(self, owner: Owner, port: Port, state: PortState, other: Candidate, verb: str) -> PortRecord:
        """Joins two circuit ends: every placeholder at one end takes a bare name of each member of the other."""
        assert other.port is not None
        lowered: List[str] = []
        for this_owner, this_port, far_owner, far_port in (
            (owner, port, other.owner, other.port),
            (other.owner, other.port, owner, port),
        ):
            sources = [far_owner.units[member] for member in far_port.landing_members if member in far_owner.units]
            for unit in (
                this_owner.units[member] for member in this_port.landing_members if member in this_owner.units
            ):
                for position in unit.placeholder_positions(this_port.name):
                    unit.lowered[position] = (
                        [DefaultInputs(source=source.name) for source in sources],
                        f"circuit {port.circuit}: {this_owner.reference}.{this_port.name} joined to "
                        f"{far_owner.reference}.{far_port.name} ({verb})",
                    )
                    lowered.extend(f"{unit.name}.inputs: {source.name}" for source in sources)
        if not lowered:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the circuit {port.circuit} between '{port.name}' and {other.name} has no '{{$port: …}}' placeholder "
                "at either end, so no member reads the other end's outputs.",
            )
        self.joined[(other.owner.reference, other.port.name)] = (Candidate(owner, port=port), tuple(lowered))
        self.joined[(owner.reference, port.name)] = (other, tuple(lowered))
        return PortRecord(port.name, state.value, "bound", verb, other.name, tuple(lowered))

    def _joined(self, owner: Owner, port: Port, state: PortState, written: Optional[Tuple[str, Any]]) -> PortRecord:
        """The record of a circuit end the other end's decision joined; its own verb, if any, must agree."""
        other, lowered = self.joined[(owner.reference, port.name)]
        if written is not None and (written[0] == "none" or written[1] not in (other.owner.reference, other.name)):
            if written[0] != "none":
                # A target the file does not declare, or that is no free end of this circuit, says why.
                self.check_declared(owner, port, f"the circuit end '{port.name}'", written[0], written[1])
                self.resolve(owner, port, written[1])
            raise self.error(
                EnergySystemErrorId.CIRCUIT_MISMATCH,
                owner,
                f"the circuit end '{port.name}' is joined to {other.name} by that end, yet {owner.verb_site} writes "
                f"'{written[0]}{': ' + written[1] if written[1] else ''}' for it.",
                remedy="Write the binding on one end, or make both name each other.",
            )
        return PortRecord(port.name, state.value, "bound", f"joined by {other.name}", other.name, lowered)

    def _carrier(self, owner: Owner, port: Port, state: PortState) -> PortRecord:
        """A carrier need: the one provider of its carrier; a fuel's consumers land in the provider's meter (§5)."""
        carrier = port.carrier or ""
        providers = [(other, item) for other, item in self.providers.get(carrier, []) if other is not owner]
        if not providers:
            if state == PortState.OPTIONAL:
                return PortRecord(port.name, state.value, "not bound: no provider")
            raise self.error(
                EnergySystemErrorId.CARRIER_PROVIDER,
                owner,
                f"the required port '{port.name}' (carrier {carrier}) has no provider of {carrier} in the file; the "
                "format never adds one (§5.2).",
                remedy=f"Add an import (or a site entry with a 'ports' block) providing {carrier}.",
            )
        provider, provision = providers[0]
        meter: Optional[Unit] = None
        if provision.is_fuel_provision:
            meter = provider.units.get(provision.landing_members[0])
            if meter is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    provider,
                    f"the provision '{provision.name}' of {carrier} meters at '{provision.landing_members[0]}', which "
                    f"is not present with these parameters, so the consumers of '{owner.reference}.{port.name}' have "
                    "no meter to land in.",
                )
        consumers: Dict[str, Unit] = {}
        for item in port.outputs:
            member, output = item.split(".", 1) if "." in item else (owner.reference, item)
            unit = owner.units.get(member)
            if unit is None:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    owner,
                    f"the carrier port '{port.name}' names the consuming output '{item}', whose member is not present "
                    "with these parameters.",
                )
            consumers[unit.name] = unit
            self.record.consuming.append(ConsumingOutput(unit.name, output, carrier, meter.name if meter else None))
        lowered: List[str] = []
        if meter is not None:
            positions = meter.placeholder_positions(provision.name)
            if len(positions) != 1:
                raise self.error(
                    EnergySystemErrorId.PORT_CONTRACT,
                    provider,
                    f"the meter '{meter.name}' carries {len(positions)} '{{$port: {provision.name}}}' placeholders; "
                    "its consumers' feeds land at exactly one.",
                )
            landed = meter.lowered.get(positions[0], ([], ""))[0]
            for name in consumers:
                if DefaultInputs(source=name) not in landed:
                    landed.append(DefaultInputs(source=name))
                    lowered.append(f"{meter.name}.inputs: {name}")
            meter.lowered[positions[0]] = (
                landed,
                f"carrier {carrier}: the consumers of {provider.reference}.{provision.name}",
            )
        self.consumers.setdefault((provider.reference, provision.name), []).append(f"{owner.reference}.{port.name}")
        partner = f"{provider.reference}.{provision.name}" + (f" (meter {meter.name})" if meter else " (no link)")
        return PortRecord(port.name, state.value, "bound", "default", partner, tuple(lowered))

    # --------------------------------------------------------------------------- observe, actuate

    def _observe(self, owner: Owner, port: Port) -> PortRecord:
        """An observer port: its members observe the import's selection, else the port's default (§4.1)."""
        selection = owner.observes if owner.observes is not None else port.selection
        assert selection is not None
        members = [owner.units[member] for member in port.into if member in owner.units]
        if not members:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the observer port '{port.name}' is active, but none of its members {', '.join(port.into)} is "
                "present with these parameters, so nothing observes.",
            )
        for unit in members:
            self.record.selection.observers.append(Observer(unit.name, selection, owner.reference))
        return PortRecord(port.name, "observer", "selected by the wiring", "", ", ".join(unit.name for unit in members))

    def _plan_controllables(self, records: Mapping[str, Sequence[PortRecord]]) -> None:
        """Hands every active controllable output to the selection plan, a ``via`` with its need's partner."""
        for owner in self.owners:
            for name, port in owner.ports.items():
                if not port.controllable or owner.states[name] == PortState.INACTIVE:
                    continue
                unit = owner.units.get(port.output_member)
                if unit is None:
                    raise self.error(
                        EnergySystemErrorId.PORT_CONTRACT,
                        owner,
                        f"the controllable output '{name}' names '{port.output_member}', which is not present with "
                        "these parameters; switch the port off with active_when.",
                    )
                via = port.controllable.get("via")
                need = next((item for item in records[owner.reference] if item.port == via), None)
                self.record.selection.controllables.append(
                    Controllable(
                        component=unit.name,
                        output=port.output_name,
                        target_input=port.controllable.get("target_input"),
                        via_partner=need.partner if need is not None and need.decision == "bound" else None,
                        optional=bool(port.controllable.get("optional", False)),
                        label=f"{owner.reference}.{name}",
                    )
                )

    @staticmethod
    def item_text(item: AnyInputItem) -> str:
        """An input item as the record lists it."""
        if isinstance(item, ExplicitWire):
            return f"{{input: {item.input}, from: {item.source}.{item.output}}}"
        return item.source
