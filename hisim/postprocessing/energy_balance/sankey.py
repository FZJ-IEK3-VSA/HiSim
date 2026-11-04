"""The yearly energy flows of a run as one connected Sankey graph, with one filter per carrier (hisim-energy-sankeys-9uoo.9, .18).

The graph follows ``roadmap/complete_energy_sankeys_spec.md`` §4 (owner decisions D1, D2, D4, Q-J). Its nodes are
of the kinds of :class:`NodeKind`; every edge carries one carrier, a yearly value in kWh (always drawn positive: a
negative total turns the edge round) and the port it came from:

* **transfer**: a producer's ``PRODUCTION`` port to a consumer's ``CONSUMPTION`` port, drawn once, at the value the receiver books;
  when the two sides disagree beyond the tolerance, the difference goes to or from ``unaccounted`` at the sender. A
  port whose peer declares no ports is drawn to or from the grey node "undeclared: <component>", so the graph also
  shows how much of the house the balance covers.
* **metered**: a metered port whose peer is a meter, ``CONSUMPTION`` from the meter and ``PRODUCTION`` to it.
* **meter boundary**: what a meter publishes, its import from "grid import" or the carrier's supply node and its
  export to "grid export" (:mod:`.meters`). A meter's gap between what it publishes and what it reads is its
  residual, treated like a component's.
* **boundary**: a ``CONSUMPTION`` port from its boundary node ("natural gas supply", "ambient heat", "sun") or a ``PRODUCTION``
  port to one.
* **loss**: a ``LOSS`` port to its own loss node, "<component> · <output>", which carries the total on to its
  boundary node, "outdoors" unless the port names another.
* **storage**: a ``STORED_CHANGE`` port to the component's storage node, "<component> · stored", or from it when
  the component gave up more than it stored.
* **residual**: a component's or a meter's residual to or from the red ``unaccounted`` node, drawn only where it is
  beyond the balance's tolerance, the same tolerance that fails a run (owner, 2026-10-20). A residual within the
  tolerance is not drawn; the report lists every residual.

So every declared component node and every meter that fails its balance carries as much out as in over all
carriers once its red residual edge is drawn, and one that closes does so up to its within-tolerance residual.
``overall`` is the one connected graph in which energy changes carrier along the chain (D2); each carrier's view
keeps the edges of that carrier only, so a converting node there shows one side, and a converter's residual, which
has no carrier, appears only in ``overall``. Links are coloured by carrier with the colour-blind-safe Okabe-Ito
palette; the renderer is plotly (D4).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.check import ComponentBalance, LinkBalance
from hisim.postprocessing.energy_balance.meters import MeterBalance
from hisim.postprocessing.energy_balance.ports import DeclaredPorts, Peer, PeerKind, PortSeries


@enum.unique
class NodeKind(str, enum.Enum):
    """The kinds of nodes of the Sankey graph (complete Sankey spec, §4.1), each drawn in its own colour."""

    COMPONENT = "component"
    METER = "meter"
    BOUNDARY = "boundary"
    LOSS = "loss"
    STORAGE = "storage"
    UNACCOUNTED = "unaccounted"
    UNDECLARED = "undeclared"
    UNLINKED = "unlinked"


@enum.unique
class EdgeKind(str, enum.Enum):
    """The kinds of edges of the Sankey graph (complete Sankey spec, §4.2)."""

    TRANSFER = "transfer"
    METERED = "metered"
    METER_BOUNDARY = "meter_boundary"
    BOUNDARY = "boundary"
    LOSS = "loss"
    STORAGE = "storage"
    RESIDUAL = "residual"
    UNLINKED = "unlinked"


@dataclass
class SankeyLink:
    """One yearly flow, in kWh, always positive.

    Attributes:
        source: The node the energy leaves.
        target: The node it enters.
        kilowatt_hours: The flow over the run, in kWh.
        carrier: The carrier's value, or None for a residual of a component that converts between carriers.
        label: The port or the relation the flow comes from, shown on hover.
        kind: What kind of edge it is.
    """

    source: str
    target: str
    kilowatt_hours: float
    carrier: Optional[str]
    label: str
    kind: EdgeKind


class SankeyNodes:
    """Names of the nodes that are no component and no port's peer."""

    UNACCOUNTED: ClassVar[str] = "unaccounted"
    UNDECLARED_PREFIX: ClassVar[str] = "undeclared: "

    @staticmethod
    def loss_of(series: PortSeries) -> str:
        """The loss node of one ``LOSS`` port, for example "CondensingGasBoiler · CombustionHeatLoss"."""
        return f"{series.component}{Peer.NODE_SEPARATOR}{series.channel}"


class SankeyBuilder:
    """Builds the yearly edges of a run, and the kind of every node, from its declared ports, balances and meters."""

    #: Links below this many kWh over the run are left out.
    SMALLEST_LINK_IN_KILOWATT_HOUR: ClassVar[float] = 1e-9

    #: The edge kind of a port's edge, by the kind of its peer.
    EDGE_OF_PEER: ClassVar[Mapping[PeerKind, EdgeKind]] = {
        PeerKind.COMPONENT: EdgeKind.TRANSFER,
        PeerKind.METER: EdgeKind.METERED,
        PeerKind.BOUNDARY: EdgeKind.BOUNDARY,
        PeerKind.STORE: EdgeKind.STORAGE,
        PeerKind.UNLINKED: EdgeKind.UNLINKED,
    }
    #: The node kind of a peer that is no component.
    NODE_OF_PEER: ClassVar[Mapping[PeerKind, NodeKind]] = {
        PeerKind.METER: NodeKind.METER,
        PeerKind.BOUNDARY: NodeKind.BOUNDARY,
        PeerKind.STORE: NodeKind.STORAGE,
        PeerKind.UNLINKED: NodeKind.UNLINKED,
    }

    def __init__(
        self,
        declared: DeclaredPorts,
        balances: Sequence[ComponentBalance],
        meters: Sequence[MeterBalance] = (),
        transfers: Sequence[LinkBalance] = (),
    ) -> None:
        """Build from the ports, the balances, the meter balances and the checked transfers of one run.

        Args:
            declared: The declared ports of the run.
            balances: Every declared component's balance; one that does not close draws its residual.
            meters: Every meter's balance; one that does not agree draws its residual.
            transfers: Every transfer between declared components; one whose two sides disagree beyond the
                tolerance draws their difference.
        """
        self.declared = declared
        self.balances = list(balances)
        self.meters = list(meters)
        self.transfers = list(transfers)
        self.links: List[SankeyLink] = []
        self.kinds: Dict[str, NodeKind] = {}

    def node(self, name: str, kind: NodeKind) -> str:
        """Register a node with its kind and return its name."""
        self.kinds.setdefault(name, kind)
        return name

    def node_of(self, peer: Peer) -> str:
        """The node a peer is drawn as, registered with its kind."""
        if peer.kind == PeerKind.COMPONENT:
            if self.declared.is_declared(peer.name):
                return self.node(peer.name, NodeKind.COMPONENT)
            return self.node(SankeyNodes.UNDECLARED_PREFIX + peer.name, NodeKind.UNDECLARED)
        return self.node(peer.name, self.NODE_OF_PEER[peer.kind])

    def add(
        self, source: str, target: str, kilowatt_hours: float, carrier: Optional[str], label: str, kind: EdgeKind
    ) -> None:
        """Add an edge, turned round when its value is negative; one below the smallest link is left out."""
        if abs(kilowatt_hours) < self.SMALLEST_LINK_IN_KILOWATT_HOUR:
            return
        if kilowatt_hours < 0:
            source, target, kilowatt_hours = target, source, -kilowatt_hours
        self.links.append(SankeyLink(source, target, kilowatt_hours, carrier, label, kind))

    def build(self) -> List[SankeyLink]:
        """All yearly edges of the run; :attr:`kinds` then holds the kind of every node they name."""
        self.links = []
        self.kinds = {}
        paired = self.declared.paired_links()
        for component, ports in self.declared.by_component.items():
            self.node(component, NodeKind.COMPONENT)
            for series in ports:
                self.add_port(component, series, paired)
        for meter in self.meters:
            self.add_meter(meter)
        for transfer in self.transfers:
            # the receiver's value is drawn; a difference beyond the tolerance closes the sender's node through
            # unaccounted, either way round, and one within it is not drawn
            if transfer.closes or transfer.one_sided:
                continue
            self.add(
                transfer.sender,
                self.node(SankeyNodes.UNACCOUNTED, NodeKind.UNACCOUNTED),
                transfer.difference,
                transfer.carrier,
                f"{transfer.sender} -> {transfer.receiver}: sent minus received",
                EdgeKind.RESIDUAL,
            )
        for balance in self.balances:
            if balance.closes:
                continue
            self.add(
                balance.component,
                self.node(SankeyNodes.UNACCOUNTED, NodeKind.UNACCOUNTED),
                balance.residual,
                balance.single_carrier,
                f"{balance.component}: residual of its balance",
                EdgeKind.RESIDUAL,
            )
        return self.links

    def add_meter(self, meter: MeterBalance) -> None:
        """Add the edges of one meter: its published import and export, and a gap beyond the tolerance.

        The ports it reads draw their own edges to and from it (:meth:`add_port`).
        """
        name = self.node(meter.meter, NodeKind.METER)
        exchange = meter.reading.exchange
        carrier = exchange.carrier.value
        imported = float(meter.reading.import_kilowatt_hours.sum())
        source = self.node(exchange.import_node(), NodeKind.BOUNDARY)
        self.add(source, name, imported, carrier, f"{name}.{exchange.import_output}", EdgeKind.METER_BOUNDARY)
        if exchange.export_output is not None:
            exported = float(meter.reading.export_kilowatt_hours.sum())
            target = self.node(exchange.export_node(), NodeKind.BOUNDARY)
            self.add(name, target, exported, carrier, f"{name}.{exchange.export_output}", EdgeKind.METER_BOUNDARY)
        if meter.agrees:
            return
        self.add(
            name,
            self.node(SankeyNodes.UNACCOUNTED, NodeKind.UNACCOUNTED),
            meter.residual,
            carrier,
            f"{name}: what it publishes minus what it reads",
            EdgeKind.RESIDUAL,
        )

    def add_port(
        self, component: str, series: PortSeries, paired: Mapping[Tuple[str, str, str], Tuple[float, float]]
    ) -> None:
        """Add the edges of one port: to or from its peer, through its own loss node for a loss.

        Args:
            component: The port's component.
            series: The port.
            paired: The transfers both sides book; their sender's ``PRODUCTION`` port draws nothing, the receiver does.
        """
        carrier = series.port.carrier.value
        total = series.annual_kilowatt_hours
        role = series.port.role
        label = series.name
        edge = self.EDGE_OF_PEER[series.peer.kind]
        if role == lt.EnergyRole.CONSUMPTION:
            self.add(self.node_of(series.peer), component, total, carrier, label, edge)
        elif role == lt.EnergyRole.STORED_CHANGE:
            self.add(component, self.node_of(series.peer), total, carrier, label, EdgeKind.STORAGE)
        elif role == lt.EnergyRole.LOSS:
            loss = self.node(SankeyNodes.loss_of(series), NodeKind.LOSS)
            self.add(component, loss, total, carrier, label, EdgeKind.LOSS)
            self.add(loss, self.node_of(series.peer), total, carrier, label, EdgeKind.LOSS)
        elif (component, series.peer.name, carrier) not in paired:
            self.add(component, self.node_of(series.peer), total, carrier, label, edge)
        # a PRODUCTION port of a paired transfer is drawn once, at the receiver's value, by the receiver's CONSUMPTION port


class SankeyDiagram:
    """One diagram's nodes and links as the JSON and plotly take them, and the views of the graph."""

    #: The colour of each node kind.
    COLOURS: ClassVar[Mapping[NodeKind, str]] = {
        NodeKind.COMPONENT: "#4a6fa5",
        NodeKind.METER: "#4d4d4d",
        NodeKind.BOUNDARY: "#3c9d5d",
        NodeKind.LOSS: "#e08a2c",
        NodeKind.STORAGE: "#c49a2c",
        NodeKind.UNACCOUNTED: "#d62728",
        NodeKind.UNDECLARED: "#c8c8c8",
        NodeKind.UNLINKED: "#7f7f7f",
    }

    #: The colour of each carrier's links, from the colour-blind-safe Okabe-Ito palette (hisim-energy-sankeys-9uoo.18). The
    #: bought fuels and district heat share one colour: their supply nodes tell them apart.
    CARRIER_COLOURS: ClassVar[Mapping[lt.EnergyBalanceCarrier, str]] = {
        lt.EnergyBalanceCarrier.ELECTRICITY: "#0072B2",
        lt.EnergyBalanceCarrier.NATURAL_GAS: "#CC79A7",
        lt.EnergyBalanceCarrier.HEATING_OIL: "#CC79A7",
        lt.EnergyBalanceCarrier.PELLETS: "#CC79A7",
        lt.EnergyBalanceCarrier.WOOD_CHIPS: "#CC79A7",
        lt.EnergyBalanceCarrier.HYDROGEN: "#CC79A7",
        lt.EnergyBalanceCarrier.DIESEL: "#CC79A7",
        lt.EnergyBalanceCarrier.DISTRICT_HEAT: "#CC79A7",
        lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT: "#D55E00",
        lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT: "#E69F00",
        lt.EnergyBalanceCarrier.ROOM_HEAT: "#000000",
        lt.EnergyBalanceCarrier.COOLING: "#56B4E9",
        lt.EnergyBalanceCarrier.AMBIENT_HEAT: "#009E73",
        lt.EnergyBalanceCarrier.SOLAR: "#F0E442",
    }
    #: The colour of a link without a carrier: a converter's residual.
    NO_CARRIER_COLOUR: ClassVar[str] = "#999999"
    #: The opacity of a link, so that crossing links stay readable.
    LINK_OPACITY: ClassVar[float] = 0.45
    #: The name of the connected all-carrier diagram.
    OVERALL: ClassVar[str] = "overall"

    @classmethod
    def link_colour(cls, carrier: Optional[str]) -> str:
        """The plotly ``rgba(...)`` colour of a link of ``carrier``; None, a converter's residual, is grey.

        Example: ``link_colour("electricity")`` is ``"rgba(0,114,178,0.45)"``.
        """
        colour = cls.NO_CARRIER_COLOUR if carrier is None else cls.CARRIER_COLOURS[lt.EnergyBalanceCarrier(carrier)]
        red, green, blue = (int(colour[position : position + 2], 16) for position in (1, 3, 5))
        return f"rgba({red},{green},{blue},{cls.LINK_OPACITY})"

    @classmethod
    def data(cls, links: Sequence[SankeyLink], kinds: Mapping[str, NodeKind]) -> Dict[str, Any]:
        """``{"nodes": [...], "links": [...]}`` with links referring to nodes by index.

        Args:
            links: The edges to draw.
            kinds: The kind of every node the edges name.
        """
        nodes: List[str] = []
        positions: Dict[str, int] = {}
        for link in links:
            for node in (link.source, link.target):
                if node not in positions:
                    positions[node] = len(nodes)
                    nodes.append(node)
        return {
            "nodes": [{"name": node, "kind": kinds[node].value} for node in nodes],
            "links": [
                {
                    "source": positions[link.source],
                    "target": positions[link.target],
                    "value_kwh": link.kilowatt_hours,
                    "carrier": link.carrier,
                    "kind": link.kind.value,
                    "label": link.label,
                }
                for link in links
            ],
        }

    @classmethod
    def views(cls, links: Sequence[SankeyLink], kinds: Mapping[str, NodeKind]) -> Dict[str, Dict[str, Any]]:
        """``overall``, the one connected graph (D2), then one filter per carrier that has a link.

        A carrier's filter keeps the edges of that carrier; a converter's residual, which has no carrier, is only
        in ``overall``.

        Args:
            links: Every edge of the run.
            kinds: The kind of every node the edges name.
        """
        diagrams: Dict[str, Dict[str, Any]] = {cls.OVERALL: cls.data(links, kinds)}
        for carrier in sorted({link.carrier for link in links if link.carrier is not None}):
            diagrams[carrier] = cls.data([link for link in links if link.carrier == carrier], kinds)
        return diagrams

    @classmethod
    def title(cls, diagram_name: str) -> str:
        """The title of one diagram, with the carrier in words.

        Example: ``domestic_hot_water_heat`` is titled "Energy flows over the run: domestic hot water
        heat (kWh)", ``overall`` "Energy flows over the run: all carriers (kWh)".
        """
        words = "all carriers" if diagram_name == cls.OVERALL else diagram_name.replace("_", " ")
        return f"Energy flows over the run: {words} (kWh)"

    @classmethod
    def node_labels(cls, diagram: Dict[str, Any]) -> List[str]:
        """Each node's name with the energy through it over the run, in whole kWh.

        A node's energy is the larger of what flows in and what flows out, as plotly sizes the
        node; it is written into the label so that a printed diagram, which shows no hover text,
        still carries the numbers. Example: ``MoreAdvancedHeatPumpHPLib<br>15,252 kWh``.
        """
        inflow = [0.0] * len(diagram["nodes"])
        outflow = [0.0] * len(diagram["nodes"])
        for link in diagram["links"]:
            outflow[link["source"]] += link["value_kwh"]
            inflow[link["target"]] += link["value_kwh"]
        return [
            f"{node['name']}<br>{max(inflow[index], outflow[index]):,.0f} kWh"
            for index, node in enumerate(diagram["nodes"])
        ]

    @classmethod
    def figure(cls, diagram: Dict[str, Any], title: str) -> Any:
        """The plotly figure of one diagram: nodes coloured by kind, links by carrier."""
        import plotly.graph_objects as go  # pylint: disable=import-outside-toplevel

        nodes = diagram["nodes"]
        links = diagram["links"]
        figure = go.Figure(
            go.Sankey(
                valueformat=",.1f",
                valuesuffix=" kWh",
                node={
                    "label": cls.node_labels(diagram),
                    "color": [cls.COLOURS[NodeKind(node["kind"])] for node in nodes],
                    "pad": 18,
                },
                link={
                    "source": [link["source"] for link in links],
                    "target": [link["target"] for link in links],
                    "value": [link["value_kwh"] for link in links],
                    "label": [link["label"] for link in links],
                    "color": [cls.link_colour(link["carrier"]) for link in links],
                },
            )
        )
        figure.update_layout(title_text=title, font_size=12)
        return figure
