"""The post-construction port check: what the lowered ports mean, confirmed on the constructed components.

``assemblies_spec.md`` §3.2 and §3.3. The expansion of imports binds every port from the files
alone — every entry states its class, so which component is a candidate partner, whether a port has
none or several, and whether a verb decides it, are known before anything is built — and lowers a
need to a bare partner name or to explicit wires. What a bare name *means*, the member's default
connections from the partner's class, and whether a wire's input and output exist, only the
component itself says: HiSim creates a component's inputs, outputs and default connections in its
constructor, and there is no second declaration of them.

:meth:`EnergySystemExecutor.build <hisim.energy_system.executor.EnergySystemExecutor.build>`
therefore runs this check as a stage of its own, after every component is constructed and before
any wire is planned or connected. It reads the port-provenance table the expansion wrote
(:class:`~hisim.energy_system.assemblies.record.PortProvenance`) — from the import record on a run,
from the realized record's metadata on a re-run — and the constructed instances, never constructing
anything a second time:

- a **bare name** (``default``) needs the member to declare default connections, or default feeds
  of an aggregator, from the partner's class (``EF-7H``), exactly what the wiring stage expands the
  bare name through;
- a **wire** needs the named input on the member and the named output on the partner (``EF-7J``);
- a **provided output** needs the named output on the member providing it (``EF-7J``).

Every refusal is an :class:`~hisim.energy_system.errors.EnergySystemAssemblyError` whose message
names the import (and instance), the port, the member, the partner by name and class, the files and
lines the port came from, the ports or classes the component does declare, and ends in the same
paste-ready verb lines the expansion offers. Whether the two ends of a wire agree in load type and
unit stays where it was, in the wiring stage's ``EF-30`` check, which every lowered wire passes
through. Nothing simulates after a refusal.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from hisim.component import Component
from hisim.energy_system.assemblies.record import LoweredKind, LoweredPort
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.wiring_checks import find_input, find_output


class LoweredPortCheck:
    """Checks every entry of a port-provenance table against the constructed components."""

    def __init__(self, provenance: Sequence[LoweredPort], components: Mapping[str, Component]) -> None:
        """Prepares the check.

        Args:
            provenance: The port-provenance table, in lowering order.
            components: The constructed components by their name in the expanded file.
        """
        self.provenance = provenance
        self.components = components

    def check(self) -> None:
        """Checks every entry, in lowering order, and raises at the first that does not fit.

        Raises:
            EnergySystemAssemblyError: ``EF-7H`` for a bare name the member declares no defaults
                for, ``EF-7J`` for a wire or a provided output naming a port that does not exist
                and for an entry naming a component the system does not hold.
        """
        for entry in self.provenance:
            member = self._component(entry, entry.member)
            if entry.kind == LoweredKind.PROVIDED:
                self._check_provided(entry, member)
                continue
            partner = self._component(entry, entry.partner)
            if entry.kind == LoweredKind.DEFAULT:
                self._check_default(entry, member, partner)
            else:
                self._check_wire(entry, member, partner)

    def _component(self, entry: LoweredPort, name: str) -> Component:
        """The constructed component an entry names; a table naming another is a defect, refused."""
        component = self.components.get(name)
        if component is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' lowered an item for '{name}', which the built system does not hold "
                f"{entry.source_text()}.",
                alternatives=sorted(self.components),
                alternatives_label="components",
                offending_value=name,
            )
        return component

    @staticmethod
    def _class_name(component: Component) -> str:
        """The class name default connections are keyed by."""
        return str(component.get_classname())

    def _check_default(self, entry: LoweredPort, member: Component, partner: Component) -> None:
        """A bare name: the member declares default connections (or default feeds) from the partner's class."""
        partner_class = self._class_name(partner)
        if member.default_connections.get(partner_class) or DynamicConnectionResolver.default_feeds_of(
            member, partner_class
        ):
            return
        declared = sorted(
            set(member.default_connections)
            | set(getattr(member, DynamicConnectionResolver.DEFAULT_FEEDS_ATTRIBUTE, None) or {})
        )
        raise EnergySystemAssemblyError(
            EnergySystemErrorId.PARTNER_WITHOUT_DEFAULT_CONNECTIONS,
            entry.owner,
            f"port '{entry.port}' is bound to {entry.partner} ({partner_class}), but {entry.member} "
            f"({self._class_name(member)}) declares no default connections from {partner_class} "
            f"{entry.source_text()}.",
            alternatives=declared,
            alternatives_label="classes it declares default connections from",
            remedy="Bind a partner of one of those classes, or add the default connection to the class. "
            + entry.remedy,
        )

    def _check_wire(self, entry: LoweredPort, member: Component, partner: Component) -> None:
        """A wire: the member has the input, the partner has the output."""
        if find_input(member, entry.input) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' wires '{entry.input}', which is no input of {entry.member} "
                f"({self._class_name(member)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in member.inputs],
                alternatives_label="inputs",
                offending_value=entry.input,
                remedy=entry.remedy or None,
            )
        if find_output(partner, entry.output) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' wires '{entry.input}' from '{entry.output}', which is no output of "
                f"{entry.partner} ({self._class_name(partner)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in partner.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
                remedy=entry.remedy or None,
            )

    def _check_provided(self, entry: LoweredPort, member: Component) -> None:
        """A provided output: the member providing it has it."""
        if find_output(member, entry.output) is None:
            raise EnergySystemAssemblyError(
                EnergySystemErrorId.PORT_CONTRACT,
                entry.owner,
                f"port '{entry.port}' provides '{entry.output}', which is no output of {entry.member} "
                f"({self._class_name(member)}) {entry.source_text()}.",
                alternatives=[port.field_name for port in member.outputs],
                alternatives_label="outputs",
                offending_value=entry.output,
            )


def check_lowered_ports(provenance: Sequence[LoweredPort], components: Mapping[str, Component]) -> None:
    """Checks every lowered port item against the constructed components (see :class:`LoweredPortCheck`).

    Args:
        provenance: The port-provenance table.
        components: The constructed components by name.

    Raises:
        EnergySystemAssemblyError: ``EF-7H`` or ``EF-7J`` at the first item that does not fit.
    """
    LoweredPortCheck(provenance, components).check()
