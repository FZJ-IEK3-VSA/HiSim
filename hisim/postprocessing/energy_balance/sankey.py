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
(spec §4.8, owner amendments of 2026-11-06, beads hisim-sankey-readability-8e20 and hisim-sankey-layout-48b8):
``overall`` starts a component that converts sunlight at what it makes of it, an exchange both ways between two nodes
is drawn as its net, flows below 1 Wh are left out, flows below a share of the view's largest flow merge into "other"
nodes, and every node is labelled on one line with a display name and its energy. :class:`SankeyLayout` places the
nodes in explicit columns, sources left and sinks right, with a label line of room around every node.
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

    The JSON keeps the whole graph; only the drawing changes it, in this order (owner amendments of 2026-11-06, beads
    hisim-sankey-readability-8e20 and hisim-sankey-layout-48b8):

    1. ``overall`` nets a component's solar losses against the sunlight it receives (:meth:`net_solar_losses`): the
       PV system starts at its electricity, and the split of the sunlight is drawn in the ``solar`` view only.
    2. An exchange both ways between two nodes on one carrier is drawn as its net (:meth:`net_two_way`), so a battery
       that gives back to the meter part of what it took from it draws no ring.
    3. A flow below :attr:`EMPTY_FLOW_IN_KILOWATT_HOUR` is left out (:meth:`drop_empty`), and a node left with no
       flow goes with it. A view with nothing left is not drawn.
    4. Small nodes, through which less than :attr:`SMALL_FLOW_SHARE` of the view's largest flow passes, merge into
       "other" nodes, one per sink (:meth:`merge_small`); the hover text lists what each stands for. An "other" node
       that stands for a single node keeps that node's name and colour, since merging one node gains nothing.
    5. Every node is labelled on one line, "name · energy" (:meth:`label`), with a display name (:meth:`display_name`).
    6. :class:`SankeyLayout` places every node in an explicit column, with room for its label (:meth:`figure`).

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
    #: The figure's margins around the plot: room for the title on top, little elsewhere, since the labels of the
    #: first column are right of their nodes and those of the last column left of theirs.
    MARGIN_IN_PIXELS: ClassVar[Mapping[str, int]] = MappingProxyType({"t": 60, "b": 30, "l": 20, "r": 20})
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
        """The drawing of one view of ``energy_sankey.json``, made readable in the first five steps of the class.

        Args:
            diagram: One view as :meth:`SankeyDiagram.data` writes it.
            view: The view's name; ``overall`` also nets the solar losses.
        """
        links, kinds = cls.links_of(diagram)
        if view == SankeyDiagram.OVERALL:
            links = cls.net_solar_losses(links, kinds)
        links = cls.net_two_way(links)
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

    @classmethod
    def net_two_way(cls, links: Sequence[SankeyLink]) -> List[SankeyLink]:
        """Draw an exchange both ways between two nodes on one carrier as one link of its net, the way the larger goes.

        A battery takes 3,488 kWh from the meter over the year and gives 2,541 kWh back. Drawn as two links, the
        pair is a cycle, which plotly draws as a ring around both nodes; drawn as its net, "ElectricityMeter →
        Battery" carries 947 kWh, what the battery's losses cost beyond what it gave up from its store, and its hover
        text lists both flows. Links between two nodes one way only are kept as they are, parallel ones too; flows of
        different carriers are never netted against each other, since they are different energies.

        Args:
            links: The links of one view.

        Returns:
            The links, every two-way exchange replaced by one link at the place of its first link; an exchange that
            nets to zero is a link of 0 kWh, which :meth:`drop_empty` leaves out.
        """
        totals: Dict[Tuple[str, str, Optional[str]], float] = {}
        for link in links:
            key = (link.source, link.target, link.carrier)
            totals[key] = totals.get(key, 0.0) + link.kilowatt_hours
        netted: List[SankeyLink] = []
        done: Set[Tuple[FrozenSet[str], Optional[str]]] = set()
        for link in links:
            if (link.target, link.source, link.carrier) not in totals:
                netted.append(link)
                continue
            pair = (frozenset((link.source, link.target)), link.carrier)
            if pair in done:
                continue
            done.add(pair)
            there = totals[(link.source, link.target, link.carrier)]
            back = totals[(link.target, link.source, link.carrier)]
            source, target = (link.source, link.target) if there >= back else (link.target, link.source)
            parts = [part for part in links if (frozenset((part.source, part.target)), part.carrier) == pair]
            label = "net of both ways:<br>" + "<br>".join(
                f"{part.label}: {cls.amount(part.kilowatt_hours)} kWh{'' if part.source == source else ' (back)'}" for part in parts
            )
            netted.append(SankeyLink(source, target, abs(there - back), link.carrier, label, link.kind))
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
        """The plotly figure of the drawing: nodes where :class:`SankeyLayout` puts them, coloured by kind, links by
        carrier, hover text on both, and the figure as tall as the layout needs.

        plotly takes the positions with ``arrangement="snap"``: it keeps them, and a node the reader drags snaps to a
        column and keeps the label gap to its neighbours.
        """
        import plotly.graph_objects as go  # pylint: disable=import-outside-toplevel

        layout = SankeyLayout.of(self.links, {node.name: node.label for node in self.nodes})
        positions = {node.name: index for index, node in enumerate(self.nodes)}
        figure = go.Figure(
            go.Sankey(
                valueformat=",.1f",
                valuesuffix=" kWh",
                arrangement="snap",
                node={
                    "label": [node.label for node in self.nodes],
                    "color": [node.colour for node in self.nodes],
                    "customdata": [node.hover for node in self.nodes],
                    "hovertemplate": "%{customdata}<br>%{value:,.1f} kWh<extra></extra>",
                    "pad": SankeyLayout.LABEL_GAP_IN_PIXELS,
                    "thickness": SankeyLayout.NODE_THICKNESS_IN_PIXELS,
                    "x": [layout.x[node.name] for node in self.nodes],
                    "y": [layout.y[node.name] for node in self.nodes],
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
        margin = self.MARGIN_IN_PIXELS
        figure.update_layout(
            title_text=title,
            font_size=SankeyLayout.FONT_SIZE_IN_PIXELS,
            height=round(layout.height_in_pixels + margin["t"] + margin["b"]),
            width=round(layout.width_in_pixels + margin["l"] + margin["r"]),
            margin=dict(margin),
        )
        return figure


@dataclass(frozen=True)
class SankeyLayout:
    """Where the drawing puts each node: its column, its height and the room for its label beside it.

    plotly places a label beside its node, right of it but in the last column left of it, and has no collision
    handling, so the labels of nodes a few pixels high overlap where plotly stacks them closer than a line, and a long
    label runs into the next column (spec §4.8, step 6, owner amendment of 2026-11-06, bead hisim-sankey-layout-48b8).
    The layout therefore decides every node's position itself and hands it to plotly as ``node.x``/``node.y``:

    1. **Columns** (:meth:`columns`): a node that only gives is in the first column (the boundaries energy enters
       from, a store giving up energy), a node that only takes in the last (the boundaries energy leaves to, a store
       taking energy), every other node in the column of its longest path from a node of the first column; a link
       that would close a cycle (:meth:`back_links`) does not count.
    2. **Order in a column** (:meth:`orders`): by energy, largest on top, then sorted again by the energy-weighted
       mean height of the nodes each node is linked to, column by column forth and back, so that links cross less.
    3. **Heights** (:meth:`stack`): every node as tall as its energy, the fullest column's energy
       :attr:`NODE_HEIGHT_IN_PIXELS` tall, the nodes of a column at least :attr:`LABEL_GAP_IN_PIXELS` apart, one
       label line, and each column centred. The plot is as tall as the column that needs most room
       (:meth:`plot_height_in_pixels`), so a column of many small nodes makes the figure taller instead of stacking
       their labels on each other.
    4. **Widths** (:meth:`column_centres`): the gap after a column holds its longest label, the last gap the
       longest labels of both its columns, since the last column's labels face left. The plot is as wide as that
       needs, at least :attr:`SMALLEST_PLOT_WIDTH_IN_PIXELS`, so a long label widens the figure instead of running
       over the next column.

    Example: ``SankeyLayout.of(drawing.links, labels)`` lays out a drawing; ``layout.x["grid import"]`` is near 0,
    the first column, and ``layout.x["grid export"]`` near 1, the last.

    Attributes:
        column_of: The column of every node, 0 the first.
        column_nodes: The nodes of each column, top first.
        x: Every node's horizontal centre, 0 at the left to 1, as plotly's ``node.x`` takes it.
        y: Every node's vertical centre, 0 at the top to 1, as plotly's ``node.y`` takes it.
        height_in_pixels: The height of the plot area the positions are computed for.
        width_in_pixels: The width of the plot area the positions are computed for.
    """

    column_of: Mapping[str, int]
    column_nodes: Tuple[Tuple[str, ...], ...]
    x: Mapping[str, float]
    y: Mapping[str, float]
    height_in_pixels: float
    width_in_pixels: float

    #: The font size of the labels.
    FONT_SIZE_IN_PIXELS: ClassVar[int] = 12
    #: The width of a label's character, as a share of the font size: 0.55 is a little more than plotly's sans-serif
    #: font needs on average, so that an estimated label is never shorter than the drawn one.
    CHARACTER_WIDTH_SHARE: ClassVar[float] = 0.55
    #: The distance between a node and its label, a little more than plotly leaves.
    LABEL_OFFSET_IN_PIXELS: ClassVar[float] = 8.0
    #: The least distance between the nodes of a column: one label line at :attr:`FONT_SIZE_IN_PIXELS` and a little
    #: air. plotly's ``node.pad``, so that plotly keeps the same gap when it snaps a dragged node.
    LABEL_GAP_IN_PIXELS: ClassVar[float] = 20.0
    #: The width of a node, plotly's ``node.thickness``.
    NODE_THICKNESS_IN_PIXELS: ClassVar[float] = 20.0
    #: The height of the nodes of the column with most energy, gaps not counted.
    NODE_HEIGHT_IN_PIXELS: ClassVar[float] = 560.0
    #: The least width of the plot: a view with few short labels is spread over it.
    SMALLEST_PLOT_WIDTH_IN_PIXELS: ClassVar[float] = 1200.0
    #: How often the columns are sorted forth and back by the heights of their neighbours.
    SWEEPS: ClassVar[int] = 4
    #: The bounds of a vertical position: plotly ignores a position of exactly 0.
    FIRST_POSITION: ClassVar[float] = 0.001
    LAST_POSITION: ClassVar[float] = 0.999

    @classmethod
    def of(cls, links: Sequence[SankeyLink], labels: Mapping[str, str]) -> "SankeyLayout":
        """The layout of a drawing's links, in the four steps of the class.

        Args:
            links: The links the drawing draws; every node they name gets a position.
            labels: The label of every node, whose length sets the room beside it.
        """
        columns = cls.columns(links)
        throughputs = SankeyDrawing.throughputs(links)
        count = max(columns.values(), default=0) + 1
        energy_per_column = [0.0] * count
        for node, column in columns.items():
            energy_per_column[column] += throughputs[node]
        fullest = max(energy_per_column, default=0.0)
        pixels_per_kilowatt_hour = cls.NODE_HEIGHT_IN_PIXELS / fullest if fullest > 0.0 else 0.0
        heights = {node: kilowatt_hours * pixels_per_kilowatt_hour for node, kilowatt_hours in throughputs.items()}
        orders = cls.orders(links, columns, heights)
        plot_height = cls.plot_height_in_pixels(orders, heights, cls.LABEL_GAP_IN_PIXELS)
        y: Dict[str, float] = {}
        for column_nodes in orders:
            centres = cls.stack([heights[node] for node in column_nodes], cls.LABEL_GAP_IN_PIXELS, plot_height)
            for node, centre in zip(column_nodes, centres):
                y[node] = min(max(centre / plot_height, cls.FIRST_POSITION), cls.LAST_POSITION)
        label_widths = [max((cls.label_width_in_pixels(labels[node]) for node in column_nodes), default=0.0) for column_nodes in orders]
        centres, plot_width = cls.column_centres(label_widths)
        x = {node: centres[column] / plot_width for node, column in columns.items()}
        return cls(
            MappingProxyType(columns),
            tuple(tuple(column_nodes) for column_nodes in orders),
            MappingProxyType(x),
            MappingProxyType(y),
            plot_height,
            plot_width,
        )

    @staticmethod
    def back_links(links: Sequence[SankeyLink]) -> FrozenSet[Tuple[str, str]]:
        """The links that close a cycle, as (source, target): what a depth-first walk from the sources finds going back.

        The walk starts at the nodes without inflow, in the order the links name them, then at any node not yet
        reached (a cycle no source leads to), and follows each node's links in their order. Example: of
        "grid → Meter", "Meter → Battery" and "Battery → Meter", the last closes the cycle and is the back link.
        """
        successors: Dict[str, List[str]] = {}
        nodes: List[str] = []
        takes: Set[str] = set()
        for link in links:
            for node in (link.source, link.target):
                if node not in successors:
                    successors[node] = []
                    nodes.append(node)
            successors[link.source].append(link.target)
            takes.add(link.target)
        on_path: Set[str] = set()
        visited: Set[str] = set()
        back: Set[Tuple[str, str]] = set()
        for start in [node for node in nodes if node not in takes] + nodes:
            if start in visited:
                continue
            visited.add(start)
            on_path.add(start)
            path = [(start, iter(successors[start]))]
            while path:
                node, children = path[-1]
                for child in children:
                    if child in on_path:
                        back.add((node, child))
                    elif child not in visited:
                        visited.add(child)
                        on_path.add(child)
                        path.append((child, iter(successors[child])))
                        break
                else:
                    on_path.discard(node)
                    path.pop()
        return frozenset(back)

    @classmethod
    def columns(cls, links: Sequence[SankeyLink]) -> Dict[str, int]:
        """The column of every node: sources first, sinks last, every other node by its longest path from a source.

        A node that only gives is in column 0, a node that only takes in the last column, the column of the longest
        path; every other node is in the column of the number of links on its longest path from column 0, back
        links (:meth:`back_links`) left out. Example: "grid import → Meter → HeatPump → Building → Building · loss →
        outdoors" and "Meter → grid export" put grid import in 0, the meter in 1, the heat pump in 2, the building
        in 3, its loss node in 4 and both outdoors and grid export in 5.

        Returns:
            The column of every node, in the order the links name them.
        """
        back = cls.back_links(links)
        forward = [link for link in links if (link.source, link.target) not in back]
        depth: Dict[str, int] = {}
        for link in links:
            depth.setdefault(link.source, 0)
            depth.setdefault(link.target, 0)
        inflows = {node: 0 for node in depth}
        for link in forward:
            inflows[link.target] += 1
        ready = [node for node in depth if inflows[node] == 0]
        while ready:
            node = ready.pop(0)
            for link in forward:
                if link.source != node:
                    continue
                depth[link.target] = max(depth[link.target], depth[node] + 1)
                inflows[link.target] -= 1
                if inflows[link.target] == 0:
                    ready.append(link.target)
        last = max(depth.values(), default=0)
        gives = {link.source for link in links}
        takes = {link.target for link in links}
        columns: Dict[str, int] = {}
        for node, node_depth in depth.items():
            if node not in gives:
                columns[node] = last
            elif node not in takes:
                columns[node] = 0
            else:
                columns[node] = node_depth
        return columns

    @classmethod
    def orders(cls, links: Sequence[SankeyLink], columns: Mapping[str, int], heights: Mapping[str, float]) -> List[List[str]]:
        """The nodes of each column, top first: by height, then by the weighted mean height of their neighbours.

        First every column is sorted by its nodes' heights, the tallest on top. Then, :attr:`SWEEPS` times, the
        columns are sorted from the second to the last by the mean centre of each node's neighbours in the columns
        before it, weighted by the energy of the links to them, and from the last but one to the first by those in
        the columns after it. A node without such a neighbour keeps its centre; equal keys keep their order. Example:
        a source S2 of 110 kWh above a source S1 of 100 kWh, S1 feeding the sink Ta (100 kWh) and S2 the sinks Tc
        (60 kWh) and Tb (50 kWh): sorted by height the sinks are Ta, Tc, Tb, so Ta's link would cross both others;
        sorted by their neighbours they are Tc, Tb, Ta, and no link crosses.

        Args:
            links: The links of the drawing.
            columns: The column of every node (:meth:`columns`).
            heights: Every node's height in pixels.
        """
        count = max(columns.values(), default=0) + 1
        orders: List[List[str]] = [[] for _ in range(count)]
        for node, column in columns.items():
            orders[column].append(node)
        for column_nodes in orders:
            column_nodes.sort(key=lambda node: -heights[node])
        gap = cls.LABEL_GAP_IN_PIXELS
        for _ in range(cls.SWEEPS):
            for column, before in [(column, True) for column in range(1, count)] + [(column, False) for column in range(count - 2, -1, -1)]:
                plot_height = cls.plot_height_in_pixels(orders, heights, gap)
                centres: Dict[str, float] = {}
                for column_nodes in orders:
                    centres.update(zip(column_nodes, cls.stack([heights[node] for node in column_nodes], gap, plot_height)))
                weighted: Dict[str, float] = {}
                weights: Dict[str, float] = {}
                for link in links:
                    for node, neighbour in ((link.target, link.source), (link.source, link.target)):
                        if columns[node] != column or (columns[neighbour] < column) != before or columns[neighbour] == column:
                            continue
                        weighted[node] = weighted.get(node, 0.0) + link.kilowatt_hours * centres[neighbour]
                        weights[node] = weights.get(node, 0.0) + link.kilowatt_hours
                keys = {
                    node: weighted[node] / weights[node] if weights.get(node, 0.0) > 0.0 else centres[node]
                    for node in orders[column]
                }
                orders[column].sort(key=keys.__getitem__)
        return orders

    @staticmethod
    def stack(heights: Sequence[float], gap_in_pixels: float, plot_height_in_pixels: float) -> List[float]:
        """The vertical centres of nodes stacked from the top, ``gap_in_pixels`` apart, the column centred in the plot.

        Example: heights 100 and 20 with a gap of 20 in a plot 200 high take 140 pixels, start 30 pixels down and
        have their centres at 80 and 160.
        """
        total = sum(heights) + gap_in_pixels * max(len(heights) - 1, 0)
        top = (plot_height_in_pixels - total) / 2.0
        centres: List[float] = []
        for height in heights:
            centres.append(top + height / 2.0)
            top += height + gap_in_pixels
        return centres

    @staticmethod
    def plot_height_in_pixels(orders: Sequence[Sequence[str]], heights: Mapping[str, float], gap_in_pixels: float) -> float:
        """The height the fullest column needs: its nodes' heights and a gap between each two of them.

        Example: a column of nodes 560, 1 and 1 pixel high with a gap of 20 needs 602 pixels, a column of twelve
        nodes of 1 pixel 232: the plot is 602 high.
        """
        return max(
            (sum(heights[node] for node in column) + gap_in_pixels * max(len(column) - 1, 0) for column in orders),
            default=0.0,
        )

    @classmethod
    def label_width_in_pixels(cls, label: str) -> float:
        """The width a label takes, estimated from its characters: :attr:`CHARACTER_WIDTH_SHARE` of the font size each.

        Example: "grid import · 1,805 kWh", 23 characters at 12 pixels, is estimated at 151.8 pixels.
        """
        return len(label) * cls.CHARACTER_WIDTH_SHARE * cls.FONT_SIZE_IN_PIXELS

    @classmethod
    def column_centres(cls, label_widths: Sequence[float]) -> Tuple[List[float], float]:
        """The horizontal centre of each column in pixels, and the width of the plot, so that every label fits.

        A column's labels are right of its nodes, the last column's left of them. So the gap after a column holds a
        node, the label offset and the column's longest label; the gap before the last column holds the longest
        labels of both its columns. A plot narrower than :attr:`SMALLEST_PLOT_WIDTH_IN_PIXELS` is widened to it, every
        gap in proportion. Example: with labels of 100, 200 and 50 pixels, nodes 20 wide and an offset of 8, the
        centres are 10, 138 and 424 in a plot 434 wide, which is then widened to 1200.

        Args:
            label_widths: The width of each column's longest label, in pixels.

        Returns:
            The centres, the first at half a node from the left edge and the last at half a node from the right, and
            the plot's width; a single column is one node wide, since there is no gap to widen.
        """
        thickness = cls.NODE_THICKNESS_IN_PIXELS
        offset = cls.LABEL_OFFSET_IN_PIXELS
        centres = [thickness / 2.0]
        last = len(label_widths) - 1
        for column in range(1, last + 1):
            gap = thickness + offset + label_widths[column - 1]
            if column == last:
                gap += offset + label_widths[last]
            centres.append(centres[-1] + gap)
        width = centres[-1] + thickness / 2.0
        smallest = cls.SMALLEST_PLOT_WIDTH_IN_PIXELS
        if thickness < width < smallest:
            stretch = (smallest - thickness) / (width - thickness)
            centres = [thickness / 2.0 + (centre - thickness / 2.0) * stretch for centre in centres]
            width = smallest
        return centres, width
