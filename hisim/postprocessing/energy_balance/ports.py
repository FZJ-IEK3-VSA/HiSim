"""The declared energy ports of a finished run, as kWh per step, and who is on the other side of each (hisim-9uoo.2).

:class:`DeclaredPorts` reads the result table once: every output that declares an
:class:`~hisim.energy_port.EnergyPort` becomes a :class:`PortSeries`, its column converted to kWh per step
(a power with the timestep) and turned round when the port is declared ``negated``; the sign of its role is
applied by the check (:mod:`.check`). A component none of whose outputs declares a port is *undeclared*. The
peer of each port comes from the wiring the simulator left on the components' inputs
(:attr:`~hisim.component.ComponentInput.src_object_name`), as :mod:`hisim.energy_port` describes.

:meth:`DeclaredPorts.declared_links` pairs a sender's ``OUT`` port with its receiver's ``IN`` port where both
describe the same transfer, and lists a transfer between two declared components that only one side books with
the other side None: the check compares the totals of a pair and fails a one-sided booking, the Sankey draws a
pair once (:meth:`DeclaredPorts.paired_links`). A booking toward a component that declares no ports at all, an
environment node or nobody stays one-sided without failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from hisim import loadtypes as lt
from hisim.component import Component, ComponentOutput
from hisim.component_wrapper import ComponentWrapper
from hisim.energy_port import EnergyPort


@dataclass(frozen=True)
class Peer:
    """The other side of a port: a component, an environment node, or nobody the wiring names."""

    #: ``component``, ``environment``, ``store`` or ``unlinked``.
    kind: str
    name: str

    COMPONENT = "component"
    ENVIRONMENT = "environment"
    STORE = "store"
    UNLINKED = "unlinked"

    @classmethod
    def store_of(cls, component_name: str) -> "Peer":
        """The component's own store, the other side of its storage change."""
        return cls(cls.STORE, f"{component_name} (store)")

    @classmethod
    def unlinked(cls) -> "Peer":
        """A port whose peer the wiring does not name."""
        return cls(cls.UNLINKED, "unlinked")


@dataclass
class PortSeries:
    """One declared port of one component: its declaration, its peer and its kWh per step."""

    component: str
    output: ComponentOutput
    port: EnergyPort
    peer: Peer
    kilowatt_hours: np.ndarray

    @property
    def annual_kilowatt_hours(self) -> float:
        """The port's total over the run."""
        return float(self.kilowatt_hours.sum())

    def as_dict(self) -> Dict[str, object]:
        """The port as the balance report lists it."""
        return {
            "output": self.output.field_name,
            "unit": self.output.unit.value,
            **self.port.as_dict(),
            "peer": self.peer.name,
            "peer_kind": self.peer.kind,
            "total_kwh": self.annual_kilowatt_hours,
        }


@dataclass
class DeclaredPorts:
    """Every declared port of a run, by component, and the components that declare none."""

    by_component: Dict[str, List[PortSeries]] = field(default_factory=dict)
    class_names: Dict[str, str] = field(default_factory=dict)
    undeclared: List[str] = field(default_factory=list)
    index: Optional[pd.Index] = None

    @classmethod
    def collect(
        cls,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        wrapped_components: Sequence[ComponentWrapper],
        seconds_per_timestep: float,
    ) -> "DeclaredPorts":
        """Read the declared ports from the result table, whose columns are ``all_outputs`` in order.

        Raises:
            ValueError: If the table does not have one column per output.
        """
        if results.shape[1] != len(all_outputs):
            raise ValueError(f"The result table has {results.shape[1]} columns for {len(all_outputs)} outputs.")
        components = [wrapped.my_component for wrapped in wrapped_components]
        wiring = Wiring(components)
        declared = cls(index=results.index)
        columns = [column for column, output in enumerate(all_outputs) if output.energy_port is not None]
        # only the declared columns are copied: the check costs what the ports are, not the whole table
        values = results.iloc[:, columns].to_numpy(dtype=float) if columns else np.empty((len(results), 0))
        for position, column in enumerate(columns):
            output = all_outputs[column]
            port = output.energy_port
            if port is None:  # pragma: no cover - the columns are the declared ones
                continue
            factor = EnergyPort.kilowatt_hours_per_step(output.unit, seconds_per_timestep)
            sign = -1.0 if port.negated else 1.0
            series = PortSeries(
                component=output.component_name,
                output=output,
                port=port,
                peer=wiring.peer_of(output.component_name, output.field_name, port),
                kilowatt_hours=values[:, position] * (factor * sign),
            )
            declared.by_component.setdefault(output.component_name, []).append(series)
        for component in components:
            declared.class_names[component.component_name] = type(component).__name__
            if component.component_name not in declared.by_component:
                declared.undeclared.append(component.component_name)
        return declared

    def is_declared(self, component_name: str) -> bool:
        """Whether the component declares at least one port."""
        return component_name in self.by_component

    def all_series(self) -> List[PortSeries]:
        """Every declared port of the run, component by component."""
        return [series for ports in self.by_component.values() for series in ports]

    def declared_links(self) -> Dict[Tuple[str, str, str], Tuple[Optional[float], Optional[float]]]:
        """Every transfer between two declared components: ``(sender, receiver, carrier) -> (sent, received)``.

        A receiver's ``IN`` port whose peer is a declared component pairs with that sender's ``OUT`` ports of the
        same carrier whose peer is the receiver. A side that books nothing of the transfer is None: a receiver's
        ``IN`` port with no such ``OUT`` port has ``sent`` None, a sender's ``OUT`` port whose declared peer has no
        ``IN`` port of the same carrier from it has ``received`` None. Both are one-sided bookings, which the
        check fails (owner, 2026-10-02: always fail hard). A booking toward a component that declares no ports,
        an environment node, a store or nobody the wiring names is not a link between declared components and is
        not listed.
        """
        received: Dict[Tuple[str, str, str], float] = {}
        sent: Dict[Tuple[str, str, str], float] = {}
        for component, ports in self.by_component.items():
            for series in ports:
                if series.peer.kind != Peer.COMPONENT or not self.is_declared(series.peer.name):
                    continue
                if series.port.role == lt.EnergyRole.IN:
                    key = (series.peer.name, component, series.port.carrier.value)
                    received[key] = received.get(key, 0.0) + series.annual_kilowatt_hours
                elif series.port.role == lt.EnergyRole.OUT:
                    key = (component, series.peer.name, series.port.carrier.value)
                    sent[key] = sent.get(key, 0.0) + series.annual_kilowatt_hours
        links: Dict[Tuple[str, str, str], Tuple[Optional[float], Optional[float]]] = {
            key: (sent.get(key), total) for key, total in received.items()
        }
        for key, total in sent.items():
            if key not in received:
                links[key] = (total, None)
        return links

    def paired_links(self) -> Dict[Tuple[str, str, str], Tuple[float, float]]:
        """The transfers both sides declare: ``(sender, receiver, carrier) -> (sent kWh, received kWh)``.

        The subset of :meth:`declared_links` with both sides booked; the Sankey draws each of them once.
        """
        return {
            key: (sent, received)
            for key, (sent, received) in self.declared_links().items()
            if sent is not None and received is not None
        }


class Wiring:
    """The connections of a run, read from the components' inputs."""

    def __init__(self, components: Sequence[Component]) -> None:
        """Index every connected input by its own component and by the output it reads."""
        self.order: Dict[str, int] = {component.component_name: number for number, component in enumerate(components)}
        self.sources: Dict[Tuple[str, str], str] = {}
        self.readers: Dict[Tuple[str, str], List[str]] = {}
        self.declared: Dict[str, bool] = {
            component.component_name: any(output.energy_port is not None for output in component.outputs)
            for component in components
        }
        for component in components:
            for component_input in component.inputs:
                source = component_input.src_object_name
                if source is None or component_input.src_field_name is None:
                    continue
                self.sources[(component.component_name, component_input.field_name)] = source
                readers = self.readers.setdefault((source, component_input.src_field_name), [])
                if component.component_name not in readers:
                    readers.append(component.component_name)

    def peer_of(self, component_name: str, field_name: str, port: EnergyPort) -> Peer:
        """The peer of one port (see :mod:`hisim.energy_port` for the rule)."""
        if port.role == lt.EnergyRole.STORED_CHANGE:
            return Peer.store_of(component_name)
        environment = port.default_environment()
        if environment is not None:
            return Peer(Peer.ENVIRONMENT, environment.value)
        if port.peer_input is not None:
            source = self.sources.get((component_name, port.peer_input))
            if source is not None:
                return Peer(Peer.COMPONENT, source)
        watched = port.peer_output
        if watched is None and port.role != lt.EnergyRole.IN:
            watched = field_name
        if watched is not None:
            readers = self.readers.get((component_name, watched), [])
            # a meter reads the same output as the consumer; a reader that declares its ports is the consumer
            readers = sorted(readers, key=lambda name: (not self.declared.get(name, False), self.order.get(name, 0)))
            if readers:
                return Peer(Peer.COMPONENT, readers[0])
        return Peer.unlinked()
