"""Binding the ports of every import and site entry, and lowering them to ordinary input items (§3).

``assemblies_spec.md`` §3.1-§3.3, decided from the files alone (every entry states its ``class:``).
A **need** ``{into: [Member], partner: Class}`` binds to one component whose class is one of its
partner classes and lowers to a bare partner name at the member's ``{$port: …}`` placeholder — the
member class's default connections from the partner's class, which the wiring stage expands and
checks — or to the explicit wires its ``wires:`` names. A **provided** output ``{output:
Member.Output}`` is what a verb names when a need must read one particular output.

**The default rule** mirrors the sizing engine's: a port binds to the one component in scope whose
class is one of its partner classes, and a verb decides every other case. In scope are the site
entries, the live components of groups and variants, and every member of every import; a port never
binds into its own instance. A verb names an import or a component the file declares, absent only
through a disabled group or an unselected option. The refusals: ``EF-7A`` no partner, ``EF-7B``
several and no verb, ``EF-7C`` ``none:`` on a required port, ``EF-7D`` an undeclared partner or an
absent ``bind:`` partner, ``EF-7E`` an optional port with candidates and no verb,
``EF-7F`` a verb on an inactive port, ``EF-7G`` a verb naming no need, ``EF-7H`` a bound provided
output the need's wires do not read, ``EF-7J`` a partner of another class or a need with nowhere to
land. Each names the owner, the port and the candidates and ends in a paste-ready verb line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.assemblies.addresses import Unit
from hisim.energy_system.assemblies.record import PortRecord
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind, PortState
from hisim.energy_system.model import AnyInputItem, DefaultInputs, ExplicitWire


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
    """

    reference: str
    label: str
    verb_site: str
    verbs: BindingVerbs
    units: Dict[str, Unit]
    ports: Mapping[str, Port]
    states: Mapping[str, PortState]


class PortBinder:
    """Decides and lowers every port of one file, with every owner in scope."""

    def __init__(
        self, sites: Mapping[str, Owner], imports: Mapping[str, Sequence[Owner]], absent: Mapping[str, str]
    ) -> None:
        """Prepares the binding.

        Args:
            sites: Every live component of the file by name, the site entries first.
            imports: Import key to its instances' owners (one for an import without instances), in file order.
            absent: Each component a disabled group or an unselected option leaves out, to the reason.
        """
        self.sites = sites
        self.imports = imports
        self.absent = absent
        self.declared = tuple(sites) + tuple(absent) + tuple(imports)
        self.owners: List[Owner] = list(sites.values()) + [owner for owners in imports.values() for owner in owners]

    def bind(self) -> Dict[str, List[PortRecord]]:
        """Decides every port of every owner and lowers the bound needs.

        Returns:
            Owner reference (``Building``, ``heating``, ``pv.east``) to its ports' records.
        """
        records: Dict[str, List[PortRecord]] = {}
        for owner in self.owners:
            for port in owner.verbs.ports():
                if port not in owner.ports or owner.ports[port].kind != PortKind.NEED:
                    needs = [name for name, item in owner.ports.items() if item.kind == PortKind.NEED]
                    raise self.error(
                        EnergySystemErrorId.UNKNOWN_PORT,
                        owner,
                        f"a verb names the port '{port}', which is no need of {owner.label}; a verb binds a need, and "
                        "a "
                        "provided port is named on the partner's side.",
                        alternatives=needs,
                        alternatives_label="needs",
                        offending_value=port,
                    )
            records[owner.reference] = [self.decide(owner, name, port) for name, port in owner.ports.items()]
        return records

    @staticmethod
    def error(error_id: EnergySystemErrorId, owner: Owner, problem: str, **kwargs: object) -> EnergySystemAssemblyError:
        """One refusal of the binding, located at the owner."""
        return EnergySystemAssemblyError(error_id, owner.label, problem, **kwargs)  # type: ignore[arg-type]

    def candidates(self, owner: Owner, port: Port) -> List[Tuple[Owner, Unit]]:
        """The components in scope, outside the owner, whose class is one of the port's partner classes."""
        return [
            (other, unit)
            for other in self.owners
            if other is not owner
            for unit in other.units.values()
            if unit.class_name in port.partner
        ]

    @staticmethod
    def paste(owner: Owner, port: Port, candidates: Sequence[Tuple[Owner, Unit]], optional: bool) -> str:
        """The paste-ready verb lines of a refusal."""
        references = list(dict.fromkeys(other.reference for other, _unit in candidates)) or ["<partner>"]
        lines = [f"`bind: {{{port.name}: {reference}}}`" for reference in references]
        if optional:
            lines = [f"`optional-bind: {{{port.name}: {reference}}}`" for reference in references] + lines
            lines.append(f"`none: [{port.name}]`")
        return f"add to {owner.verb_site} one of " + ", ".join(lines) + "."

    def decide(self, owner: Owner, name: str, port: Port) -> PortRecord:
        """Decides one port by its verb or by the default rule, and lowers it (§3.1, §3.3)."""
        state = owner.states[name]
        written = owner.verbs.verb_for(name)
        if port.kind == PortKind.PROVIDED or state == PortState.INACTIVE:
            if written is not None:
                raise self.error(
                    EnergySystemErrorId.VERB_ON_INACTIVE_PORT,
                    owner,
                    f"the port '{name}' is inactive with these parameters (active_when/required_when), yet "
                    f"{owner.verb_site} writes '{written[0]}' for it.",
                    remedy=f"Remove the '{written[0]}' line for '{name}', or change the parameters that switch it off.",
                )
            return PortRecord(name, state.value, state.value)
        optional = state == PortState.OPTIONAL
        candidates = self.candidates(owner, port)
        listed = ", ".join(unit.name for _other, unit in candidates) or "none"
        what = f"the {state.value} port '{name}' (partner {', '.join(port.partner)})"
        if written is not None:
            verb, target = written
            if verb == "none":
                if not optional:
                    raise self.error(
                        EnergySystemErrorId.REQUIRED_PORT_DECLINED,
                        owner,
                        f"{what} is declined with 'none:', but a required port cannot be; candidates: {listed}.",
                        remedy=self.paste(owner, port, candidates, False),
                    )
                return PortRecord(name, state.value, "declined", "none")
            assert target is not None
            head = target.split(".")[0]
            if head not in self.declared:
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    owner,
                    f"{what} is bound to '{target}' with '{verb}:', but the file declares no import or component "
                    f"'{head}'; a house without the partner leaves the verb out. Candidates of the port: {listed}.",
                    alternatives=self.declared,
                    alternatives_label="imports and components the file declares",
                    offending_value=head,
                )
            partner, absent = self.resolve(owner, port, target)
            if partner is None:
                if verb == "optional-bind":
                    return PortRecord(name, state.value, f"not bound: {head} {absent}", verb)
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    owner,
                    f"{what} is bound to '{target}', but '{head}' is {absent}; candidates: {listed}.",
                    remedy=self.paste(owner, port, candidates, optional),
                )
            return self.lower(owner, port, state, partner, verb)
        if len(candidates) == 1 and not optional:
            return self.lower(owner, port, state, candidates[0][1], "default")
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
            remedy=f"Add a component of class {', '.join(port.partner)}, then " + self.paste(owner, port, (), False),
        )

    def resolve(self, owner: Owner, port: Port, target: str) -> Tuple[Optional[Tuple[Unit, str]], str]:
        """Resolves a verb's partner reference: the partner and the provided output it names (or ``""``).

        A reference whose head the file declares but a disabled group or an unselected option leaves out
        is an absent partner, which ``optional-bind:`` accepts; a reference whose head exists but whose
        instance or port does not is refused, whatever the verb, because it is a mistake rather than an
        absence. The caller has refused a head the file does not declare.

        Returns:
            ``((partner, output), "")``, or ``(None, why it is absent)``.

        Raises:
            EnergySystemAssemblyError: ``EF-7D`` for an instance or port the head does not have, ``EF-7B``
                for an instance holding several components of the partner class, ``EF-7J`` for one
                holding none, ``EF-7H`` for a provided output the need's wires do not read.
        """
        head, *rest = target.split(".")
        if head in self.sites:
            if rest:
                raise self.error(
                    EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                    owner,
                    f"'{target}': the component '{head}' provides no port.",
                )
            targets: Sequence[Owner] = (self.sites[head],)
        elif head in self.imports:
            instances = self.imports[head]
            targets = instances
            if rest and instances[0].reference != head:  # an import with instances: the next part names one
                matching = [other for other in instances if other.reference == f"{head}.{rest[0]}"]
                if not matching:
                    names = [other.reference.split(".")[1] for other in instances if "." in other.reference]
                    raise self.error(
                        EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                        owner,
                        f"'{target}': the import '{head}' has no instance '{rest[0]}'.",
                        alternatives=names,
                        alternatives_label="instances",
                    )
                targets, rest = matching, rest[1:]
            if rest:
                return self.provided(owner, port, targets[0], rest[0], target), ""
        else:
            return None, self.absent[head]
        if len(rest) > 1:
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT, owner, f"'{target}' names more than an instance and a port."
            )
        matching_units = [
            unit
            for other in targets
            if other is not owner
            for unit in other.units.values()
            if unit.class_name in port.partner
        ]
        if len(matching_units) == 1:
            return (matching_units[0], ""), ""
        if not matching_units:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the port '{port.name}' is bound to '{target}', which holds no component of its partner class "
                f"{', '.join(port.partner)} (it holds "
                + ", ".join(f"{unit.name} ({unit.class_name})" for other in targets for unit in other.units.values())
                + ").",
            )
        raise self.error(
            EnergySystemErrorId.PORT_AMBIGUOUS,
            owner,
            f"the port '{port.name}' is bound to '{target}', which holds {len(matching_units)} components of its "
            "partner "
            f"class: {', '.join(unit.name for unit in matching_units)}.",
            remedy=f"Name the instance, or a provided port: bind: {{{port.name}: {target}.<port>}}.",
        )

    def provided(self, owner: Owner, port: Port, target: Owner, name: str, written: str) -> Tuple[Unit, str]:
        """The partner of a need bound to a provided port, ``<import>[.<instance>].<port>`` (§3.3)."""
        provided = target.ports.get(name)
        if provided is None or provided.kind != PortKind.PROVIDED or target.states[name] != PortState.PROVIDED:
            offered = [
                key
                for key, item in target.ports.items()
                if item.kind == PortKind.PROVIDED and target.states[key] == PortState.PROVIDED
            ]
            raise self.error(
                EnergySystemErrorId.BOUND_PARTNER_ABSENT,
                owner,
                f"'{written}': {target.label} provides no active output '{name}'.",
                alternatives=offered,
                alternatives_label="provided ports",
                offending_value=name,
            )
        if port.wires is None or provided.output_name not in port.wires.values():
            read = (
                ", ".join(sorted(set(port.wires.values())))
                if port.wires
                else "its default connections, which name no output"
            )
            raise self.error(
                EnergySystemErrorId.BOUND_OUTPUT_NOT_READ,
                owner,
                f"the port '{port.name}' is bound to the provided output '{written}' ({provided.output}), "
                f"but it reads {read}.",
                remedy=f"Name '{provided.output_name}' in the port's wires, or bind the port to "
                f"'{target.reference}' itself.",
            )
        return target.units[provided.output_member], provided.output_name

    def lower(
        self, owner: Owner, port: Port, state: PortState, partner: Tuple[Unit, str] | Unit, verb: str
    ) -> PortRecord:
        """Lowers a decided need at its members' placeholders: a bare name, or the explicit wires it names."""
        unit = partner[0] if isinstance(partner, tuple) else partner
        if port.wires is None and unit.class_name not in port.partner:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the port '{port.name}' is bound to {unit.name} ({unit.class_name}), which is not of its partner "
                f"class "
                f"{', '.join(port.partner)}.",
            )
        lowered: List[str] = []
        landed = False
        for member in port.into:
            holder = owner.units.get(member)
            if holder is None:
                continue
            items: List[AnyInputItem] = (
                [ExplicitWire(source=unit.name, input=target, output=output) for target, output in port.wires.items()]
                if port.wires is not None
                else [DefaultInputs(source=unit.name)]
            )
            for position in holder.placeholder_positions(port.name):
                landed = True
                holder.lowered[position] = items
                holder.notes[position] = f"port {port.name} bound to {unit.name} ({verb})"
                lowered.extend(f"{holder.name}.inputs: {self.item_text(item)}" for item in items)
        if not landed:
            raise self.error(
                EnergySystemErrorId.PORT_CONTRACT,
                owner,
                f"the port '{port.name}' is bound, but none of its members {', '.join(port.into)} is present with "
                "these "
                f"parameters and carries a '{{$port: {port.name}}}' placeholder, so its items have nowhere to land.",
            )
        return PortRecord(port.name, state.value, "bound", verb, unit.name, tuple(lowered))

    @staticmethod
    def item_text(item: AnyInputItem) -> str:
        """An input item as the record lists it."""
        if isinstance(item, ExplicitWire):
            return f"{{input: {item.input}, from: {item.source}.{item.output}}}"
        return item.source
