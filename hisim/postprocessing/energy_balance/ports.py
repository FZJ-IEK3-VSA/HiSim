"""The declared energy ports of a finished run, as kWh per step, and who is on the other side of each (hisim-energy-sankeys-9uoo.2).

:class:`DeclaredPorts` reads the result table once: every output that declares an
:class:`~hisim.energy_port.EnergyPort` becomes a :class:`PortSeries`, its column converted to kWh per step
(a power with the timestep) and turned round when the port is declared ``negated``; the sign of its role is
applied by the check (:mod:`.check`). A component that declares what it meters
(:meth:`hisim.component.Component.metered_exchanges`) is a *meter*, a node of its own (owner decision Q-J of
``roadmap/complete_energy_sankeys_spec.md``). Any other component none of whose outputs declares a port is
*undeclared*, unless it is *exempt* (:class:`Exemptions`): a controller decides, it moves no energy (owner decision
D1). The peer of each port comes from the wiring the simulator left on the components' inputs
(:attr:`~hisim.component.ComponentInput.src_object_name`), skipping exempt components (:class:`Wiring`): a metered
port of electricity or a bought carrier goes to the meter that reads it, directly or through exempt components, or
to a component that reads it. A port with no peer falls back to its carrier's boundary default
(:meth:`hisim.energy_port.EnergyPort.default_boundary`), such as "natural gas supply" or "outdoors", or to nobody, which
leaves the port *unlinked*. A metered port whose peer is no meter and no component is *unmetered*
(:meth:`DeclaredPorts.unmetered`).

:meth:`DeclaredPorts.declared_links` pairs a sender's ``PRODUCTION`` port with its receiver's ``CONSUMPTION`` port where both
describe the same transfer, and lists a transfer between two declared components that only one side books with
the other side None: the check compares the totals of a pair and fails a one-sided booking, the Sankey draws a
pair once (:meth:`DeclaredPorts.paired_links`). A booking toward a component that declares no ports at all, a
meter, a boundary node or nobody stays one-sided without failing.

Beside the ports, :class:`DeclaredPorts` reads what each meter publishes (:class:`MeterReading`, from
:meth:`hisim.component.Component.metered_exchanges`), which :mod:`.meters` checks against the ports it reads.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Type

import numpy as np
import pandas as pd

from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentKindNotDeclaredError,
    ComponentKinds,
    ComponentOutput,
    L1ControllerComponent,
    L2ControllerComponent,
    MeterComponent,
)
from hisim.component_wrapper import ComponentWrapper
from hisim.config.channels import PortTypeCompatibility
from hisim.energy_port import EnergyPort, MeteredExchange


@enum.unique
class PeerKind(str, enum.Enum):

    """What the other side of a port is (complete Sankey spec, §4.1 and §4.3).

    ``COMPONENT`` is another component, declared or not; ``METER`` a meter that reads the port (owner decision Q-J);
    ``BOUNDARY`` a node where energy enters or leaves the house ("natural gas supply", "ambient heat", "outdoors");
    ``STORE`` the component's own store, the other side of its storage change; ``UNLINKED`` nobody, a port whose
    wiring names no meter and no component that moves energy and whose carrier has no default.
    """

    COMPONENT = "component"
    METER = "meter"
    BOUNDARY = "boundary"
    STORE = "store"
    UNLINKED = "unlinked"


@dataclass(frozen=True)
class Peer:
    """The other side of a port: a component, a meter, a boundary node, the component's store, or nobody."""

    kind: PeerKind
    name: str

    #: Joins a component's name and a port's name into the name of its loss or storage node.
    NODE_SEPARATOR: ClassVar[str] = " · "

    @classmethod
    def store_of(cls, component_name: str) -> "Peer":
        """The component's own store, the other side of its storage change, named "<component> · stored"."""
        return cls(PeerKind.STORE, f"{component_name}{cls.NODE_SEPARATOR}stored")

    @classmethod
    def meter(cls, meter_name: str) -> "Peer":
        """A meter that reads the port, drawn under its component name, for example "ElectricityMeter"."""
        return cls(PeerKind.METER, meter_name)

    @classmethod
    def unlinked(cls) -> "Peer":
        """A port whose peer neither the wiring nor its carrier names."""
        return cls(PeerKind.UNLINKED, "unlinked")


class Exemptions:
    """Which components are never a node or a peer of the energy balance, and why (owner decisions D1 and Q-J).

    A component is exempt by its kind, an L1 or L2 controller (:meth:`hisim.component.ComponentKinds.of`): a
    controller decides, it moves no energy. A meter is a node when it declares what it meters
    (:meth:`hisim.component.Component.metered_exchanges`); one that declares nothing is exempt by its kind, since the
    balance cannot read it. Any other component is exempt when its class states a reason in
    :attr:`hisim.component.Component.ENERGY_PORTS_EXEMPT_BECAUSE`. An exempt component is listed in the report with
    its reason, never as undeclared, and the wiring skips it when it looks for a port's peer.
    """

    #: The reason of every kind that is exempt as a whole (read-only); a meter's applies only when it meters nothing.
    REASON_OF_KIND: ClassVar[Mapping[Type[Component], str]] = MappingProxyType(
        {
            L1ControllerComponent: "an L1 controller decides for its device, it moves no energy",
            L2ControllerComponent: "an L2 controller coordinates devices, it moves no energy",
            MeterComponent: "a meter that declares no metered exchange publishes nothing the balance can check",
        }
    )

    @classmethod
    def reason(cls, component: Any) -> Optional[str]:
        """Why ``component`` is exempt from the energy balance, or None when it is not.

        For example, the energy management system is exempt because "an L2 controller coordinates devices, it moves
        no energy". A component whose class inherits no kind (a test double) is exempt only by its own attribute. A
        meter that declares a metered exchange is not asked: it is a node (:meth:`DeclaredPorts.collect`).

        Args:
            component: A component of the run.

        Returns:
            The reason, or None for a component that must declare its ports.
        """
        try:
            kind: Optional[Type[Component]] = ComponentKinds.of(type(component))
        except ComponentKindNotDeclaredError:
            kind = None
        if kind is not None and kind in cls.REASON_OF_KIND:
            return cls.REASON_OF_KIND[kind]
        stated = getattr(type(component), "ENERGY_PORTS_EXEMPT_BECAUSE", None)
        return stated if isinstance(stated, str) else None


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
            "peer_kind": self.peer.kind.value,
            "total_kwh": self.annual_kilowatt_hours,
        }

    @property
    def name(self) -> str:
        """``component.output``, as the report and the error messages name a port."""
        return f"{self.component}.{self.output.field_name}"


@dataclass
class MeterReading:
    """What one meter publishes of one carrier per step, in kWh: its import from and export to the boundary.

    Attributes:
        meter: The meter's component name.
        building: The meter's building (:attr:`hisim.config.ComponentID.building`), None in a run without buildings.
        exchange: What the meter declares it publishes.
        import_kilowatt_hours: The import per step, in kWh.
        export_kilowatt_hours: The export per step, in kWh; zeros for a carrier the meter counts only bought.
    """

    meter: str
    building: Optional[str]
    exchange: MeteredExchange
    import_kilowatt_hours: np.ndarray
    export_kilowatt_hours: np.ndarray


@dataclass
class DeclaredPorts:
    """Every declared port of a run, by component; the meters; the components that declare none, exempt or not."""

    by_component: Dict[str, List[PortSeries]] = field(default_factory=dict)
    class_names: Dict[str, str] = field(default_factory=dict)
    undeclared: List[str] = field(default_factory=list)
    #: Every exempt component with its reason, in the order of the run.
    exempt: Dict[str, str] = field(default_factory=dict)
    #: What each meter of the run publishes, one reading per metered exchange.
    meters: List[MeterReading] = field(default_factory=list)
    index: Optional[pd.Index] = None

    @staticmethod
    def exchanges_of(component: Any) -> Tuple[MeteredExchange, ...]:
        """What ``component`` declares it meters (:meth:`hisim.component.Component.metered_exchanges`); empty when not.

        A stand-in without the method, as the tests build them, meters nothing.
        """
        declare = getattr(component, "metered_exchanges", None)
        return tuple(declare()) if callable(declare) else ()

    @classmethod
    def collect(
        cls,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        wrapped_components: Sequence[ComponentWrapper],
        seconds_per_timestep: float,
    ) -> "DeclaredPorts":
        """Read the declared ports and the meters from the result table, whose columns are ``all_outputs`` in order.

        Raises:
            ValueError: If the table does not have one column per output, if an exempt component or a meter declares a
                port (a controller or meter that moves energy contradicts its kind), or if a meter declares a metered
                exchange on an output it does not have.
        """
        if results.shape[1] != len(all_outputs):
            raise ValueError(f"The result table has {results.shape[1]} columns for {len(all_outputs)} outputs.")
        components = [wrapped.my_component for wrapped in wrapped_components]
        metered_carriers = {
            component.component_name: frozenset(exchange.carrier for exchange in exchanges)
            for component in components
            if (exchanges := cls.exchanges_of(component))
        }
        exempt = {
            component.component_name: reason
            for component in components
            if component.component_name not in metered_carriers and (reason := Exemptions.reason(component)) is not None
        }
        wiring = Wiring(components, exempt, metered_carriers)
        declared = cls(index=results.index, exempt=exempt)
        columns = [column for column, output in enumerate(all_outputs) if output.energy_port is not None]
        # only the declared columns are copied: the check costs what the ports are, not the whole table
        values = results.iloc[:, columns].to_numpy(dtype=float) if columns else np.empty((len(results), 0))
        for position, column in enumerate(columns):
            output = all_outputs[column]
            port = output.energy_port
            if port is None:  # pragma: no cover - the columns are the declared ones
                continue
            if output.component_name in exempt:
                raise ValueError(
                    f"{output.component_name} declares the energy port {output.field_name}, but it is exempt from the "
                    f"energy balance: {exempt[output.component_name]}."
                )
            if output.component_name in metered_carriers:
                raise ValueError(
                    f"{output.component_name} declares the energy port {output.field_name}, but it is a meter: it "
                    f"publishes what it meters (metered_exchanges) and moves no energy of its own."
                )
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
            name = component.component_name
            if name not in declared.by_component and name not in exempt and name not in metered_carriers:
                declared.undeclared.append(name)
        declared.meters = cls.read_meters(results, all_outputs, components, seconds_per_timestep)
        return declared

    @staticmethod
    def read_meters(
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        components: Sequence[Any],
        seconds_per_timestep: float,
    ) -> List[MeterReading]:
        """What every component that declares :meth:`~hisim.component.Component.metered_exchanges` publishes, per step.

        Raises:
            ValueError: If a declared exchange names an output the component does not have.
        """
        position_of = {
            (output.component_name, output.field_name): column for column, output in enumerate(all_outputs)
        }
        readings: List[MeterReading] = []
        for component in components:
            for exchange in DeclaredPorts.exchanges_of(component):
                series: List[np.ndarray] = []
                building: Optional[str] = None
                for field_name in (exchange.import_output, exchange.export_output):
                    if field_name is None:
                        series.append(np.zeros(len(results)))
                        continue
                    column = position_of.get((component.component_name, field_name))
                    if column is None:
                        raise ValueError(
                            f"{component.component_name} declares that it meters {exchange.carrier.value} on its "
                            f"output {field_name}, which it does not have."
                        )
                    output = all_outputs[column]
                    building = output.component_id.building
                    factor = EnergyPort.kilowatt_hours_per_step(output.unit, seconds_per_timestep)
                    series.append(results.iloc[:, column].to_numpy(dtype=float) * factor)
                readings.append(MeterReading(component.component_name, building, exchange, series[0], series[1]))
        return readings

    def is_declared(self, component_name: str) -> bool:
        """Whether the component declares at least one port."""
        return component_name in self.by_component

    def all_series(self) -> List[PortSeries]:
        """Every declared port of the run, component by component."""
        return [series for ports in self.by_component.values() for series in ports]

    def unlinked(self) -> List[PortSeries]:
        """Every port without a peer: its wiring names no meter or component moving energy, its carrier no default.

        Reported, not yet a failure; the acceptance PR of the complete Sankey spec makes it one (§4.3).
        """
        return [series for series in self.all_series() if series.peer.kind == PeerKind.UNLINKED]

    def unmetered(self) -> List[PortSeries]:
        """Every metered port (:attr:`~hisim.energy_port.EnergyPort.is_metered`) whose flow passes no meter.

        A port of electricity or a bought carrier whose peer is neither a meter nor a component: a boiler's gas port
        that only the supply node feeds, or an electricity port nothing reads (owner decision Q-J: every bought
        carrier's flow passes a meter). A port whose peer is a component hands its flow on, and that component's
        ports are metered in turn.
        """
        return [
            series
            for series in self.all_series()
            if series.port.is_metered and series.peer.kind not in (PeerKind.METER, PeerKind.COMPONENT)
        ]

    def declared_links(self) -> Dict[Tuple[str, str, str], Tuple[Optional[float], Optional[float]]]:
        """Every transfer between two declared components: ``(sender, receiver, carrier) -> (sent, received)``.

        A receiver's ``CONSUMPTION`` port whose peer is a declared component pairs with that sender's ``PRODUCTION`` ports of the
        same carrier whose peer is the receiver. A side that books nothing of the transfer is None: a receiver's
        ``CONSUMPTION`` port with no such ``PRODUCTION`` port has ``sent`` None, a sender's ``PRODUCTION`` port whose declared peer has no
        ``CONSUMPTION`` port of the same carrier from it has ``received`` None. Both are one-sided bookings, which the
        check fails (owner, 2026-10-02: always fail hard). A booking toward a component that declares no ports,
        a meter, a boundary node, a store or nobody is not a link between declared components and is not listed.
        """
        received: Dict[Tuple[str, str, str], float] = {}
        sent: Dict[Tuple[str, str, str], float] = {}
        for component, ports in self.by_component.items():
            for series in ports:
                if series.peer.kind != PeerKind.COMPONENT or not self.is_declared(series.peer.name):
                    continue
                if series.port.role == lt.EnergyRole.CONSUMPTION:
                    key = (series.peer.name, component, series.port.carrier.value)
                    received[key] = received.get(key, 0.0) + series.annual_kilowatt_hours
                elif series.port.role == lt.EnergyRole.PRODUCTION:
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
    """The connections of a run, read from the components' inputs, and the peer each port finds through them."""

    def __init__(
        self,
        components: Sequence[Component],
        exempt: Optional[Mapping[str, str]] = None,
        metered_carriers: Optional[Mapping[str, FrozenSet[lt.EnergyBalanceCarrier]]] = None,
    ) -> None:
        """Index every connected input by its own component and by the output it reads, and every output's load type.

        Args:
            components: The components of the run, in the order of the run.
            exempt: The exempt components by name (:class:`Exemptions`); the wiring never names one as a peer.
            metered_carriers: The meters by name, each with the carriers it meters; a meter is the peer only of a
                metered port of one of its carriers.
        """
        self.order: Dict[str, int] = {component.component_name: number for number, component in enumerate(components)}
        self.exempt: Mapping[str, str] = exempt or {}
        self.metered_carriers: Mapping[str, FrozenSet[lt.EnergyBalanceCarrier]] = metered_carriers or {}
        self.sources: Dict[Tuple[str, str], str] = {}
        self.readers: Dict[Tuple[str, str], List[str]] = {}
        self.load_types: Dict[Tuple[str, str], lt.LoadTypes] = {}
        self.outputs: Dict[str, List[str]] = {}
        self.declared: Dict[str, bool] = {
            component.component_name: any(output.energy_port is not None for output in component.outputs)
            for component in components
        }
        for component in components:
            self.outputs[component.component_name] = [output.field_name for output in component.outputs]
            for output in component.outputs:
                self.load_types[(component.component_name, output.field_name)] = output.load_type
            for component_input in component.inputs:
                source = component_input.src_object_name
                if source is None or component_input.src_field_name is None:
                    continue
                self.sources[(component.component_name, component_input.field_name)] = source
                readers = self.readers.setdefault((source, component_input.src_field_name), [])
                if component.component_name not in readers:
                    readers.append(component.component_name)

    def meters(self, name: str, carrier: lt.EnergyBalanceCarrier) -> bool:
        """Whether the component ``name`` is a meter of ``carrier``."""
        return carrier in self.metered_carriers.get(name, frozenset())

    def moves_energy(self, name: str) -> bool:
        """Whether the component ``name`` can be the peer of a port that is not metered: neither exempt nor a meter."""
        return name not in self.exempt and name not in self.metered_carriers

    def component_peer(self, component_name: str, field_name: str, port: EnergyPort) -> Optional[str]:
        """The component the wiring names as the peer of a port that is not metered; None when there is none.

        ``peer_input`` names the component feeding one of the port's own inputs; otherwise the peer reads
        ``peer_output``, or the port's own output for a ``PRODUCTION`` port. Exempt components and meters are skipped.
        Among several readers, one that declares ports is taken first, then the first in the order of the run.
        """
        if not port.names_a_component:
            return None
        if port.peer_input is not None:
            source = self.sources.get((component_name, port.peer_input))
            return source if source is not None and self.moves_energy(source) else None
        watched = port.peer_output if port.peer_output is not None else field_name
        readers = [name for name in self.readers.get((component_name, watched), []) if self.moves_energy(name)]
        readers.sort(key=lambda name: (not self.declared.get(name, False), self.order.get(name, 0)))
        return readers[0] if readers else None

    @staticmethod
    def load_types_agree(first: Optional[lt.LoadTypes], second: Optional[lt.LoadTypes]) -> bool:
        """Whether two outputs can carry the same flow: either is unknown, or the port types agree.

        The rule is HiSim's one spelling of it (:meth:`hisim.config.channels.PortTypeCompatibility.load_types_agree`),
        equal load types or the ``ANY`` wildcard; only an unknown load type, an output the wiring has no record of,
        is added here.
        """
        return first is None or second is None or PortTypeCompatibility.load_types_agree(first, second)

    def metered_peer(self, component_name: str, field_name: str, port: EnergyPort) -> Optional[Peer]:
        """The peer of a metered port (:attr:`~hisim.energy_port.EnergyPort.is_metered`); None when nothing reads it.

        ``peer_input`` names the component feeding one of the port's own inputs. Otherwise the port watches
        ``peer_output`` or its own output, whatever its role, and its peer is the first that reads it:

        1. a component that reads it directly and is not exempt: a meter of the port's carrier first, then a
           component that declares ports, then the first in the order of the run;
        2. else a meter of the port's carrier reached through exempt components, breadth first. An exempt component
           passes the flow on through its outputs of the same load type; for example, the energy manager reads a heat
           pump's electricity and gives its sum with the other devices' to the electricity meter. What an exempt
           component hands to anything but a meter is a decision, not the flow, so only a meter is taken there.

        Args:
            component_name: The port's component.
            field_name: The port's output.
            port: The metered port.

        Returns:
            The meter or the component, or None.
        """
        carrier = port.carrier
        if port.peer_input is not None:
            source = self.sources.get((component_name, port.peer_input))
            if source is not None and self.meters(source, carrier):
                return Peer.meter(source)
            if source is not None and self.moves_energy(source):
                return Peer(PeerKind.COMPONENT, source)
            return None
        watched = port.peer_output if port.peer_output is not None else field_name
        direct = self.readers.get((component_name, watched), [])
        candidates = [name for name in direct if self.meters(name, carrier) or self.moves_energy(name)]
        candidates.sort(
            key=lambda name: (
                not self.meters(name, carrier),
                not self.declared.get(name, False),
                self.order.get(name, 0),
            )
        )
        if candidates:
            first = candidates[0]
            return Peer.meter(first) if self.meters(first, carrier) else Peer(PeerKind.COMPONENT, first)
        load_type = self.load_types.get((component_name, watched))
        queue = [name for name in direct if name in self.exempt]
        visited = set(queue)
        while queue:
            relay = queue.pop(0)
            for relay_output in self.outputs.get(relay, []):
                if not self.load_types_agree(load_type, self.load_types.get((relay, relay_output))):
                    continue
                for reader in self.readers.get((relay, relay_output), []):
                    if self.meters(reader, carrier):
                        return Peer.meter(reader)
                    if reader in self.exempt and reader not in visited:
                        visited.add(reader)
                        queue.append(reader)
        return None

    def peer_of(self, component_name: str, field_name: str, port: EnergyPort) -> Peer:
        """The peer of one port (complete Sankey spec, §4.3; :mod:`hisim.energy_port` states the rule).

        A storage change goes to the component's own store. A metered port goes to the meter or component that reads
        it (:meth:`metered_peer`), any other port to the component the wiring names (:meth:`component_peer`). A port
        with no such peer takes its carrier's boundary default, or nobody (unlinked). For example, a heat pump's
        electricity port, which the energy manager reads and passes on summed to the electricity meter, goes to the
        meter "ElectricityMeter"; a boiler's gas port that no meter reads comes from "natural gas supply".

        Args:
            component_name: The port's component.
            field_name: The port's output.
            port: The port.

        Returns:
            The peer.
        """
        if port.role == lt.EnergyRole.STORED_CHANGE:
            return Peer.store_of(component_name)
        if port.is_metered:
            metered = self.metered_peer(component_name, field_name, port)
            if metered is not None:
                return metered
        else:
            source = self.component_peer(component_name, field_name, port)
            if source is not None:
                return Peer(PeerKind.COMPONENT, source)
        boundary = port.default_boundary()
        if boundary is not None:
            return Peer(PeerKind.BOUNDARY, boundary)
        return Peer.unlinked()
