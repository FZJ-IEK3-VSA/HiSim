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
* **boundary**: a ``CONSUMPTION`` port from its boundary node ("natural gas supply", "ambient heat source", "sun") or a
  ``PRODUCTION`` port to one ("ambient heat sink", "driving").
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

``energy_sankey.json`` keeps every edge of every view. :class:`SankeyDrawing` decides what a view's HTML draws
(spec §4.8, owner amendment of 2026-11-06, bead hisim-sankey-readability-8e20): ``overall`` starts a component that
converts sunlight at what it makes of it, flows below 1 Wh are left out, flows below a share of the view's largest
flow merge into "other" nodes, and every node is labelled on one line with a display name and its energy.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

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
    """One diagram's nodes and links as ``energy_sankey.json`` keeps them, and the views of the graph.

    The JSON keeps every edge; :class:`SankeyDrawing` decides what the HTML of a view draws.
    """

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
    #: bought fuels and district heat share one colour: their supply nodes tell them apart. Okabe-Ito has eight colours, so
    #: the carriers beyond them take colours of Paul Tol's colour-blind-safe muted palette: driving olive, the vehicle
    #: electricity a car battery gives its car indigo, a darker blue than the electricity it was charged with, and the
    #: electricity of a wind turbine the run does not model teal.
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
        lt.EnergyBalanceCarrier.DRIVING: "#999933",
        lt.EnergyBalanceCarrier.VEHICLE_ELECTRICITY: "#332288",
        lt.EnergyBalanceCarrier.WIND_ELECTRICITY: "#44AA99",
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


@dataclass(frozen=True)
class MergedNode:
    """An "other" node of a drawing: the small nodes it stands for and how many flows it carries on.

    Attributes:
        name: The node's name in the drawing, unique in it, for example "other → heat loss to outdoors".
        members: The names of the nodes merged into it, in the order of the view.
        flows: How many of their flows reach the rest of the view on its sink side: the flows it gives on, or, for a
            node that gives nothing on, the flows it takes in.
    """

    name: str
    members: Tuple[str, ...]
    flows: int


@dataclass(frozen=True)
class DrawnNode:
    """One node as plotly draws it.

    Attributes:
        name: The node's name in the drawing, the key its links use.
        label: The one-line label beside the node, for example "PVSystem · module and inverter loss · 1,234 kWh".
        hover: The hover text: the node's full name, or the nodes an "other" node stands for.
        colour: The node's colour, by its kind.
    """

    name: str
    label: str
    hover: str
    colour: str


class SankeyDrawing:
    """What the HTML of one view draws: the view's links made readable, its nodes labelled (spec §4.8).

    The JSON keeps the whole graph; only the drawing changes it, in this order (owner amendment of 2026-11-06, bead
    hisim-sankey-readability-8e20):

    1. ``overall`` nets a component's solar losses against the sunlight it receives (:meth:`net_solar_losses`): the
       PV system starts at its electricity, and the split of the sunlight is drawn in the ``solar`` view only.
    2. A flow below :attr:`EMPTY_FLOW_IN_KILOWATT_HOUR` is left out (:meth:`drop_empty`), and a node left with no
       flow goes with it. A view with nothing left is not drawn.
    3. Small nodes, through which less than :attr:`SMALL_FLOW_SHARE` of the view's largest flow passes, merge into
       "other" nodes, one per sink (:meth:`merge_small`); the hover text lists what each stands for. An "other" node
       that stands for a single node keeps that node's name and colour, since merging one node gains nothing.
    4. Every node is labelled on one line, "name · energy" (:meth:`label`), with a display name (:meth:`display_name`).

    Example: ``SankeyDrawing.of(diagrams["overall"], "overall").figure("…")`` is the plotly figure of ``overall``.
    """

    #: A flow below this is left out of the drawing: float noise or a device that did not run. 1 Wh, so that the
    #: flows of a short test run are still drawn.
    EMPTY_FLOW_IN_KILOWATT_HOUR: ClassVar[float] = 0.001
    #: A node through which less than this share of the view's largest flow passes merges into an "other" node.
    SMALL_FLOW_SHARE: ClassVar[float] = 0.005
    #: The kinds of nodes that are never merged: where energy enters or leaves the house, the meters, and every node
    #: that shows a fault (a residual, an undeclared component, an unlinked port).
    ALWAYS_DRAWN: ClassVar[FrozenSet[NodeKind]] = frozenset(
        {NodeKind.BOUNDARY, NodeKind.METER, NodeKind.UNACCOUNTED, NodeKind.UNDECLARED, NodeKind.UNLINKED}
    )
    #: The name of the "other" node in front of a sink is this prefix and the sink's name.
    OTHER_PREFIX: ClassVar[str] = "other → "
    #: The "other" node of small nodes that only give energy to the rest of the view (a storage giving up a little).
    OTHER_SOURCES: ClassVar[str] = "other (sources)"
    #: The "other" node of small nodes that only take energy from the rest of the view.
    OTHER_SINKS: ClassVar[str] = "other (sinks)"
    #: The colour of an "other" node.
    OTHER_COLOUR: ClassVar[str] = "#b0b0b0"
    #: The unit suffixes an output name ends with, longest first; a display name leaves them out.
    UNIT_SUFFIXES: ClassVar[Tuple[str, ...]] = (
        "InKilogramPerSecond",
        "InKilowattHour",
        "InWattHour",
        "InKilowatt",
        "InWatt",
    )
    #: Display names of outputs whose automatic name reads poorly, by output name.
    DISPLAY_NAMES: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "ApplianceElectricityNotReleasedIndoorsInWatt": "appliance energy leaving the house",
            "TransmissionLossOpaqueInWatt": "opaque transmission loss",
            "TransmissionLossWindowsAndDoorsInWatt": "window and door transmission loss",
        }
    )
    #: The words of a CamelCase name: an acronym ("DHW"), a capitalised or lower-case word, or a number.
    WORD_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

    def __init__(self, links: Sequence[SankeyLink], kinds: Mapping[str, NodeKind], merged: Sequence[MergedNode]) -> None:
        """Label and colour the nodes of links that are already made readable; :meth:`of` makes them so.

        Args:
            links: The links to draw.
            kinds: The kind of every node of the view; an "other" node has none.
            merged: The "other" nodes among the links' nodes.
        """
        self.links = list(links)
        others = {node.name: node for node in merged}
        throughputs = self.throughputs(self.links)
        self.nodes: List[DrawnNode] = []
        for name, kilowatt_hours in throughputs.items():
            if name in others and len(others[name].members) == 1:
                # an "other" node that stands for one node is drawn where that node was: it keeps its name
                (member,) = others[name].members
                display = self.display_name(member, kinds[member])
                hover = member
                colour = SankeyDiagram.COLOURS[kinds[member]]
            elif name in others:
                node = others[name]
                display = f"other ({node.flows} flow{'' if node.flows == 1 else 's'})"
                hover = display + ":<br>" + "<br>".join(self.display_name(member, kinds[member]) for member in node.members)
                colour = self.OTHER_COLOUR
            else:
                display = self.display_name(name, kinds[name])
                hover = name
                colour = SankeyDiagram.COLOURS[kinds[name]]
            self.nodes.append(DrawnNode(name, self.label(display, kilowatt_hours), hover, colour))

    @classmethod
    def of(cls, diagram: Mapping[str, Any], view: str) -> "SankeyDrawing":
        """The drawing of one view of ``energy_sankey.json``, made readable in the four steps of the class.

        Args:
            diagram: One view as :meth:`SankeyDiagram.data` writes it.
            view: The view's name; ``overall`` also nets the solar losses.
        """
        links, kinds = cls.links_of(diagram)
        if view == SankeyDiagram.OVERALL:
            links = cls.net_solar_losses(links, kinds)
        links = cls.drop_empty(links, cls.EMPTY_FLOW_IN_KILOWATT_HOUR)
        largest = max((link.kilowatt_hours for link in links), default=0.0)
        links, merged = cls.merge_small(links, kinds, cls.SMALL_FLOW_SHARE * largest)
        return cls(links, kinds, merged)

    @staticmethod
    def links_of(diagram: Mapping[str, Any]) -> Tuple[List[SankeyLink], Dict[str, NodeKind]]:
        """The links of one view of the JSON, naming their nodes, and the kind of every node.

        The inverse of :meth:`SankeyDiagram.data`: a link ``{"source": 0, "target": 1, ...}`` becomes a
        :class:`SankeyLink` from the first node's name to the second's.
        """
        names = [node["name"] for node in diagram["nodes"]]
        kinds = {node["name"]: NodeKind(node["kind"]) for node in diagram["nodes"]}
        links = [
            SankeyLink(
                names[link["source"]],
                names[link["target"]],
                link["value_kwh"],
                link["carrier"],
                link["label"],
                EdgeKind(link["kind"]),
            )
            for link in diagram["links"]
        ]
        return links, kinds

    @staticmethod
    def net_solar_losses(links: Sequence[SankeyLink], kinds: Mapping[str, NodeKind]) -> List[SankeyLink]:
        """Net every component's solar losses against the sunlight it receives, so it starts at what it makes of it.

        A PV system takes 126,925 kWh of sunlight and loses 100,610 kWh of it in its modules and inverter: the drawing
        leaves out its loss node and draws "sun → PVSystem" at 26,315 kWh, its electricity, instead of letting the
        unconverted sunlight fill three quarters of ``overall``. A solar collector's ``CollectorHeatLoss`` is netted
        the same way. Every other link is kept as it is; the ``solar`` view, which draws the split, does not call this.

        Args:
            links: The links of one view.
            kinds: The kind of every node: the link from a component to its loss node is told from the loss node's
                link onward by the kind of its source.

        Returns:
            The links without the solar losses, the sunlight into each component that lost some reduced in proportion.
        """
        solar = lt.EnergyBalanceCarrier.SOLAR.value
        lost: Dict[str, float] = {}
        kept: List[SankeyLink] = []
        for link in links:
            if link.carrier == solar and link.kind == EdgeKind.LOSS:
                if kinds[link.source] != NodeKind.LOSS:
                    lost[link.source] = lost.get(link.source, 0.0) + link.kilowatt_hours
                continue
            kept.append(link)
        received: Dict[str, float] = {}
        for link in kept:
            if link.carrier == solar and link.target in lost:
                received[link.target] = received.get(link.target, 0.0) + link.kilowatt_hours
        netted: List[SankeyLink] = []
        for link in kept:
            if link.carrier == solar and link.target in lost:
                share = 1.0 - lost[link.target] / received[link.target]
                if share <= 0.0:
                    continue
                link = replace(
                    link,
                    kilowatt_hours=link.kilowatt_hours * share,
                    label=f"{link.label}, net of {link.target}'s solar losses ({lost[link.target]:,.1f} kWh)",
                )
            netted.append(link)
        return netted

    @staticmethod
    def drop_empty(links: Sequence[SankeyLink], smallest_kilowatt_hours: float) -> List[SankeyLink]:
        """The links of at least ``smallest_kilowatt_hours``; a node all of whose links are dropped is not drawn.

        Example: the 2.4e-7 kWh of cooling a heat pump's hot-water circuit books over a year is dropped, and with it
        the ``cooling`` view, which has no other link.
        """
        return [link for link in links if link.kilowatt_hours >= smallest_kilowatt_hours]

    @staticmethod
    def throughputs(links: Sequence[SankeyLink]) -> Dict[str, float]:
        """The energy through each node, the larger of what flows in and what flows out, as plotly sizes it.

        The nodes are in the order the links first name them. Example: a battery charged with 3,505 kWh that gives
        2,557 kWh back has a throughput of 3,505 kWh.
        """
        inflow: Dict[str, float] = {}
        outflow: Dict[str, float] = {}
        for link in links:
            for node in (link.source, link.target):
                inflow.setdefault(node, 0.0)
                outflow.setdefault(node, 0.0)
            outflow[link.source] += link.kilowatt_hours
            inflow[link.target] += link.kilowatt_hours
        return {node: max(into, outflow[node]) for node, into in inflow.items()}

    @staticmethod
    def small_groups(links: Sequence[SankeyLink], small: Set[str]) -> List[List[str]]:
        """The small nodes in groups that links between small nodes connect, each in the order the links name them.

        Example: a building's loss node and nothing else small around it is a group of its own; a small battery, its
        two loss nodes and its storage node are one group.
        """
        parent = {node: node for node in small}

        def root(node: str) -> str:
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for link in links:
            if link.source in small and link.target in small:
                parent[root(link.source)] = root(link.target)
        groups: Dict[str, List[str]] = {}
        for link in links:
            for node in (link.source, link.target):
                if node in small and node not in groups.get(root(node), []):
                    groups.setdefault(root(node), []).append(node)
        return list(groups.values())

    @classmethod
    def merge_small(
        cls, links: Sequence[SankeyLink], kinds: Mapping[str, NodeKind], threshold_kilowatt_hours: float
    ) -> Tuple[List[SankeyLink], List[MergedNode]]:
        """Merge every node with less than ``threshold_kilowatt_hours`` through it into an "other" node, one per sink.

        A small node is a component, a loss node or a storage node with a throughput below the threshold
        (:attr:`ALWAYS_DRAWN` kinds never are). The small nodes form groups (:meth:`small_groups`), and each group
        goes into one "other" node: "other → <sink>" in front of the node the group gives most to, ":attr:`OTHER_SOURCES`"
        for a group that only gives, ":attr:`OTHER_SINKS`" for one that only takes. Groups of the same sink share it.
        The links inside a group vanish; the links between a group and the rest are redrawn to or from its "other"
        node, one link per pair of nodes and carrier, with the merged links listed in its hover text. A group linked
        to nothing else is left as it is.

        Example: with a threshold of 132 kWh, the building's 47 kWh of gains lost through its windows reach "heat loss
        to outdoors" through "other → heat loss to outdoors" instead of their own loss node, and the 19, 11, 2 and
        2 kWh the building, the battery and the two tanks give up from their stores come from one "other (sources)".

        Args:
            links: The links of one view.
            kinds: The kind of every node they name.
            threshold_kilowatt_hours: A node with less than this through it is small.

        Returns:
            The links to draw, and the "other" nodes among their nodes.
        """
        throughputs = cls.throughputs(links)
        small = {
            node
            for node, kilowatt_hours in throughputs.items()
            if kilowatt_hours < threshold_kilowatt_hours and kinds[node] not in cls.ALWAYS_DRAWN
        }
        other_of: Dict[str, str] = {}
        members: Dict[str, List[str]] = {}
        flows: Dict[str, int] = {}
        for group in cls.small_groups(links, small):
            outs = [link for link in links if link.source in group and link.target not in small]
            ins = [link for link in links if link.target in group and link.source not in small]
            if not outs and not ins:
                continue
            if not outs:
                other = cls.OTHER_SINKS
            elif not ins:
                other = cls.OTHER_SOURCES
            else:
                given: Dict[str, float] = {}
                for link in outs:
                    given[link.target] = given.get(link.target, 0.0) + link.kilowatt_hours
                other = cls.OTHER_PREFIX + max(given, key=given.__getitem__)
            for node in group:
                other_of[node] = other
            members.setdefault(other, []).extend(group)
            flows[other] = flows.get(other, 0) + len(outs or ins)
        drawn: List[Any] = []
        merged: Dict[Tuple[str, str, Optional[str]], List[SankeyLink]] = {}
        for link in links:
            if link.source not in other_of and link.target not in other_of:
                drawn.append(link)
                continue
            source = other_of.get(link.source, link.source)
            target = other_of.get(link.target, link.target)
            if source == target:
                continue
            key = (source, target, link.carrier)
            if key not in merged:
                merged[key] = []
                drawn.append(key)
            merged[key].append(link)
        result: List[SankeyLink] = []
        for entry in drawn:
            if isinstance(entry, SankeyLink):
                result.append(entry)
                continue
            parts = merged[entry]
            label = parts[0].label
            if len(parts) > 1:
                label = "<br>".join(f"{part.label}: {cls.amount(part.kilowatt_hours)} kWh" for part in parts)
            result.append(
                SankeyLink(entry[0], entry[1], sum(part.kilowatt_hours for part in parts), entry[2], label, parts[0].kind)
            )
        others = [MergedNode(name, tuple(nodes), flows[name]) for name, nodes in members.items()]
        return result, others

    @classmethod
    def words(cls, output_name: str) -> str:
        """An output's name in words: unit suffix left out, CamelCase split, words in lower case but acronyms.

        Examples: ``ModuleAndInverterLossInWatt`` is "module and inverter loss", ``ThermalPowerConsumptionDHW``
        "thermal power consumption DHW", ``stored`` "stored"; :attr:`DISPLAY_NAMES` overrides a poor result, so
        ``TransmissionLossOpaqueInWatt`` is "opaque transmission loss".
        """
        if output_name in cls.DISPLAY_NAMES:
            return cls.DISPLAY_NAMES[output_name]
        stem = output_name
        for suffix in cls.UNIT_SUFFIXES:
            if stem.endswith(suffix) and len(stem) > len(suffix):
                stem = stem[: -len(suffix)]
                break
        tokens = cls.WORD_PATTERN.findall(stem)
        if not tokens:
            return output_name
        return " ".join(token if len(token) > 1 and token.isupper() else token.lower() for token in tokens)

    @classmethod
    def display_name(cls, name: str, kind: NodeKind) -> str:
        """The name a node is drawn with: a loss node's port in words, every other node's name as it is.

        Components keep the name the energy system gives them (spec §4.1). Example: the loss node
        "PVSystem · ModuleAndInverterLossInWatt" is drawn as "PVSystem · module and inverter loss", "Battery · stored"
        and "grid import" as they are.
        """
        if kind != NodeKind.LOSS:
            return name
        component, separator, output_name = name.rpartition(Peer.NODE_SEPARATOR)
        if not separator:
            return name
        return f"{component}{separator}{cls.words(output_name)}"

    @staticmethod
    def amount(kilowatt_hours: float) -> str:
        """An energy as a label writes it: whole kWh with thousands separators from 10 kWh on, else two digits.

        Examples: 1830.2 is "1,830", 11.1 "11", 1.74 "1.7", 0.025 "0.025"; a short run's small flows keep a value.
        """
        return f"{kilowatt_hours:,.0f}" if kilowatt_hours >= 10.0 else f"{kilowatt_hours:.2g}"

    @classmethod
    def label(cls, display: str, kilowatt_hours: float) -> str:
        """A node's one-line label: its display name and the energy through it (:meth:`amount`).

        A printed diagram shows no hover text, so the label carries the number. One line, so that a node a few pixels
        high takes as little room beside it as it can. Examples: ``label("grid import", 1830.2)`` is
        "grid import · 1,830 kWh", ``label("Tank · stored", 0.25)`` "Tank · stored · 0.25 kWh".
        """
        return f"{display}{Peer.NODE_SEPARATOR}{cls.amount(kilowatt_hours)} kWh"

    def figure(self, title: str) -> Any:
        """The plotly figure of the drawing: nodes coloured by kind, links by carrier, hover text on both."""
        import plotly.graph_objects as go  # pylint: disable=import-outside-toplevel

        positions = {node.name: index for index, node in enumerate(self.nodes)}
        figure = go.Figure(
            go.Sankey(
                valueformat=",.1f",
                valuesuffix=" kWh",
                node={
                    "label": [node.label for node in self.nodes],
                    "color": [node.colour for node in self.nodes],
                    "customdata": [node.hover for node in self.nodes],
                    "hovertemplate": "%{customdata}<br>%{value:,.1f} kWh<extra></extra>",
                    "pad": 18,
                },
                link={
                    "source": [positions[link.source] for link in self.links],
                    "target": [positions[link.target] for link in self.links],
                    "value": [link.kilowatt_hours for link in self.links],
                    "label": [link.label for link in self.links],
                    "color": [SankeyDiagram.link_colour(link.carrier) for link in self.links],
                },
            )
        )
        figure.update_layout(title_text=title, font_size=12)
        return figure
