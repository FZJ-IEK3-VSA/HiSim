"""The drawn graph of the Sankey figure: drawn nodes with their flows, their roles, the main chain and the trees.

A drawn node is a component, meter or undeclared node of the ``overall`` view; every other node (a boundary, a
loss, a storage, the unaccounted node) becomes a terminal flow of the drawn node it touches. A link is a flow
between two drawn nodes. The role of a node (meter, generator, buffer, building, ...) follows from its kind and
the carriers of its flows only, never from its class; the layout reads roles, not names.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from types import MappingProxyType

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs, ViewLink
from hisim.postprocessing.energy_balance.sankey import EdgeKind, NodeKind


@enum.unique
class FlowKind(str, enum.Enum):
    """What is at the other end of a drawn flow."""

    LINK = "link"
    BOUNDARY = "boundary"
    LOSS = "loss"
    STORAGE = "storage"
    RESIDUAL = "residual"
    CUT = "cut"


@enum.unique
class Role(str, enum.Enum):
    """The role of a drawn node in the figure, decided from its kind and the carriers of its flows."""

    ELECTRICITY_METER = "electricity_meter"
    FUEL_METER = "fuel_meter"
    PV = "pv"
    SOLAR_COLLECTOR = "solar_collector"
    VEHICLE_STORAGE = "vehicle_storage"
    VEHICLE = "vehicle"
    ELECTRIC_STORAGE = "electric_storage"
    HOUSEHOLD = "household"
    COOLER = "cooler"
    HOT_WATER_TANK = "hot_water_tank"
    BUFFER = "buffer"
    BUILDING = "building"
    DISTRIBUTION = "distribution"
    GENERATOR = "generator"
    CONVERTER = "converter"


@dataclass(frozen=True)
class Flow:
    """One flow of a drawn node, as that node sees it.

    Attributes:
        flow_id: Unique in the figure: "<node>|<label>".
        node: The drawn node the flow belongs to.
        label: The port label of the view's edge (``Building.HeatReceivedInWatt``); empty for a synthetic flow.
        value_kwh: Signed energy over the run in kWh: positive into the node, negative out of it.
        carrier: The carrier's value, None for a residual.
        kind: What is at the other end.
        other_end: The node at the other end: a drawn node for a link, else the boundary, loss or storage node.
        link_id: The index of the link in :attr:`DrawnGraph.links` for a link, else None.
        synthetic: Whether the flow was made by the figure (the solar cut, a residual that closes a node).
    """

    flow_id: str
    node: str
    label: str
    value_kwh: float
    carrier: Optional[str]
    kind: FlowKind
    other_end: str
    link_id: Optional[int] = None
    synthetic: bool = False

    @property
    def is_input(self) -> bool:
        """Whether energy enters the node through this flow."""
        return self.value_kwh > 0

    @property
    def magnitude_kwh(self) -> float:
        """The unsigned energy of the flow, in kWh."""
        return abs(self.value_kwh)


@dataclass(frozen=True)
class Link:
    """A flow between two drawn nodes, in kWh over the run (positive), with its port label and carrier."""

    link_id: int
    source: str
    target: str
    value_kwh: float
    carrier: Optional[str]
    label: str


@dataclass(frozen=True)
class DrawnNode:
    """A node drawn as one trunk: its name, view kind, component class (empty for a meter) and its flows."""

    name: str
    kind: str
    class_name: str
    flows: Tuple[Flow, ...]

    def flows_where(self, *, is_input: bool, kind: Optional[FlowKind] = None, carrier: Optional[str] = None) -> List[Flow]:
        """Return the node's flows of one direction, optionally only of one kind and one carrier, in order."""
        return [flow for flow in self.flows if flow.is_input == is_input and (kind is None or flow.kind is kind) and (carrier is None or flow.carrier == carrier)]

    @property
    def throughput_kwh(self) -> float:
        """The larger of the node's summed inputs and summed outputs, in kWh."""
        return max(sum(f.value_kwh for f in self.flows if f.is_input), -sum(f.value_kwh for f in self.flows if not f.is_input))


@dataclass(frozen=True)
class TreeEdge:
    """How a node hangs in its tree: placed next to ``anchor`` through the link ``link_id``.

    ``upstream`` is False when the anchor is the link's source (the placed node is its child downstream) and True
    when the placed node is the source (the PV placed in front of the meter it feeds).
    """

    anchor: str
    placed: str
    link_id: int
    upstream: bool = False


@dataclass(frozen=True)
class Tree:
    """One tree of the figure: its root and its edges in placement order (every anchor placed before its child)."""

    root: str
    edges: Tuple[TreeEdge, ...]


@dataclass(frozen=True)
class DrawnGraph:
    """The drawn graph of a run: nodes in view order, links, roles, the main chain and the trees.

    Attributes:
        nodes: The drawn nodes in the order the view lists them.
        links: The links between drawn nodes.
        roles: The role of every drawn node.
        chain: The main chain from its root to the building (or along the largest outputs without a building).
        trees: The trees; the first holds the chain.
        residual_kwh_of: The imbalance a synthetic residual closes, by node.
    """

    nodes: Tuple[DrawnNode, ...]
    links: Tuple[Link, ...]
    roles: Mapping[str, Role] = field(default_factory=dict)
    chain: Tuple[str, ...] = ()
    trees: Tuple[Tree, ...] = ()
    residual_kwh_of: Mapping[str, float] = field(default_factory=dict)

    def node(self, name: str) -> DrawnNode:
        """Return the drawn node of that name."""
        return next(node for node in self.nodes if node.name == name)


class GraphBuilder:
    """Builds the drawn graph of a run from its ``overall`` view (folding, the solar cut, empty flows, residuals)."""

    #: Node kinds drawn as trunks.
    DRAWN_KINDS: ClassVar[Tuple[str, ...]] = (NodeKind.COMPONENT.value, NodeKind.METER.value, NodeKind.UNDECLARED.value)
    #: The flow kind of a terminal flow, by the kind of the node at its other end.
    TERMINAL_KIND_OF_NODE: ClassVar[Mapping[str, FlowKind]] = MappingProxyType(
        {
            NodeKind.LOSS.value: FlowKind.LOSS,
            NodeKind.STORAGE.value: FlowKind.STORAGE,
            NodeKind.UNACCOUNTED.value: FlowKind.RESIDUAL,
            NodeKind.BOUNDARY.value: FlowKind.BOUNDARY,
            NodeKind.UNLINKED.value: FlowKind.BOUNDARY,
        }
    )
    #: A flow below this many kWh is left out (as the plotly Sankey's empty flow).
    OMIT_BELOW_KWH: ClassVar[float] = 0.001
    #: A node whose flows miss by more than this many kWh is closed by a residual flow.
    RESIDUAL_KWH: ClassVar[float] = 0.5
    #: The heat carriers: what a generator produces and a distribution passes on.
    HEAT_CARRIERS: ClassVar[Tuple[str, ...]] = (
        lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT.value,
        lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT.value,
        lt.EnergyBalanceCarrier.ROOM_HEAT.value,
    )

    @staticmethod
    def build(inputs: SankeyInputs) -> DrawnGraph:
        """Return the drawn graph of a run with its roles, chain and trees.

        Args:
            inputs: The run's inputs; only the view and the component classes are read.

        Returns:
            The drawn graph, every node closed within ``RESIDUAL_KWH`` (by a synthetic residual if needed).
        """
        kind_of = {node.name: node.kind for node in inputs.nodes}
        drawn = [node.name for node in inputs.nodes if node.kind in GraphBuilder.DRAWN_KINDS]
        links, flows_of = GraphBuilder.fold(inputs.links, kind_of, drawn)
        nodes = []
        residuals: Dict[str, float] = {}
        for name in drawn:
            flows = GraphBuilder.cut_solar(flows_of[name])
            flows, residual = GraphBuilder.close(name, flows)
            if residual:
                residuals[name] = residual
            nodes.append(DrawnNode(name, kind_of[name], inputs.class_of.get(name, ""), tuple(flows)))
        graph = DrawnGraph(tuple(nodes), tuple(links), residual_kwh_of=residuals)
        roles = {node.name: Roles.of(node) for node in graph.nodes}
        graph = replace(graph, roles=roles)
        chain = Chains.main_chain(graph)
        graph = replace(graph, chain=chain)
        return replace(graph, trees=Trees.build(graph))

    @staticmethod
    def fold(edges: Sequence[ViewLink], kind_of: Mapping[str, str], drawn: Sequence[str]) -> Tuple[List[Link], Dict[str, List[Flow]]]:
        """Fold the view's edges into links between drawn nodes and terminal flows of drawn nodes.

        An edge between two drawn nodes becomes a link and a flow at each end; an edge with one drawn end a
        terminal flow of that node, its kind from the other end's node kind (a loss, a storage, a boundary, the
        unaccounted node); an edge with no drawn end (a loss node's edge to the outdoors) is dropped. An edge below
        ``OMIT_BELOW_KWH`` is left out at both ends.

        Returns:
            The links, and the flows of every drawn node in view order.
        """
        drawn_set = set(drawn)
        flows_of: Dict[str, List[Flow]] = {name: [] for name in drawn}
        links: List[Link] = []
        for edge in edges:
            if edge.value_kwh < GraphBuilder.OMIT_BELOW_KWH:
                continue
            source_drawn, target_drawn = edge.source in drawn_set, edge.target in drawn_set
            residual = edge.kind == EdgeKind.RESIDUAL.value
            if source_drawn and target_drawn:
                link = Link(len(links), edge.source, edge.target, edge.value_kwh, edge.carrier, edge.label)
                links.append(link)
                GraphBuilder.append(flows_of, edge.source, edge, -edge.value_kwh, FlowKind.LINK, edge.target, link.link_id)
                GraphBuilder.append(flows_of, edge.target, edge, edge.value_kwh, FlowKind.LINK, edge.source, link.link_id)
            elif source_drawn:
                kind = FlowKind.RESIDUAL if residual else GraphBuilder.TERMINAL_KIND_OF_NODE.get(kind_of.get(edge.target, ""), FlowKind.BOUNDARY)
                GraphBuilder.append(flows_of, edge.source, edge, -edge.value_kwh, kind, edge.target, None)
            elif target_drawn:
                kind = FlowKind.RESIDUAL if residual else GraphBuilder.TERMINAL_KIND_OF_NODE.get(kind_of.get(edge.source, ""), FlowKind.BOUNDARY)
                GraphBuilder.append(flows_of, edge.target, edge, edge.value_kwh, kind, edge.source, None)
        return links, flows_of

    @staticmethod
    def append(flows_of: Dict[str, List[Flow]], node: str, edge: ViewLink, value_kwh: float, kind: FlowKind, other: str, link_id: Optional[int]) -> None:
        """Append one flow to a node's list, giving it an id unique within the node ("<node>|<label>", then "#2")."""
        base = f"{node}|{edge.label}"
        taken = {flow.flow_id for flow in flows_of[node]}
        flow_id, number = base, 2
        while flow_id in taken:
            flow_id, number = f"{base}#{number}", number + 1
        flows_of[node].append(Flow(flow_id, node, edge.label, value_kwh, edge.carrier, kind, other, link_id))

    @staticmethod
    def cut_solar(flows: Sequence[Flow]) -> List[Flow]:
        """Replace a PV's sunlight and solar losses by one unlabelled input of their difference (the solar cut).

        A node that takes ``SOLAR`` and produces ``ELECTRICITY`` (a PV system) would otherwise be drawn with its
        sunlight, about five times its electricity, as the widest band of the figure. A node that takes sunlight
        and produces heat (a collector) keeps both. Example: 126,925 kWh of sun in, 100,609 kWh of solar loss out
        becomes one cut input of 26,315 kWh.
        """
        solar = lt.EnergyBalanceCarrier.SOLAR.value
        # A collector's pump loss is electricity too; only electricity delivered to a drawn node makes a PV.
        produces_electricity = any(not f.is_input and f.kind is FlowKind.LINK and f.carrier == lt.EnergyBalanceCarrier.ELECTRICITY.value for f in flows)
        solar_terminals = [f for f in flows if f.carrier == solar and f.kind is not FlowKind.LINK]
        if not produces_electricity or not any(f.is_input for f in solar_terminals):
            return list(flows)
        net_kwh = sum(f.value_kwh for f in solar_terminals)
        kept = [f for f in flows if f not in solar_terminals]
        node = flows[0].node
        cut = Flow(f"{node}|cut", node, "", net_kwh, solar, FlowKind.CUT, "sun", synthetic=True)
        return [cut, *kept] if net_kwh > GraphBuilder.OMIT_BELOW_KWH else kept

    @staticmethod
    def close(node: str, flows: Sequence[Flow]) -> Tuple[List[Flow], float]:
        """Close a node whose flows miss by more than ``RESIDUAL_KWH`` with a synthetic residual flow.

        A node that already has a residual flow, or whose flows balance, is returned as it is. Example: an
        undeclared heat distribution that receives 10,930 kWh and gives 10,950 kWh gets a 20 kWh residual input.

        Returns:
            The flows, with the residual appended when one was needed, and the residual's signed kWh (0 if none).
        """
        imbalance_kwh = sum(f.value_kwh for f in flows)
        if abs(imbalance_kwh) <= GraphBuilder.RESIDUAL_KWH or any(f.kind is FlowKind.RESIDUAL for f in flows):
            return list(flows), 0.0
        residual = Flow(f"{node}|residual", node, "", -imbalance_kwh, None, FlowKind.RESIDUAL, "unaccounted", synthetic=True)
        return [*flows, residual], -imbalance_kwh


class Roles:
    """Decides the role of a drawn node from its kind and the carriers of its flows."""

    @staticmethod
    def of(node: DrawnNode) -> Role:
        """Return the role of a node: the first row of the role table whose criterion matches.

        Example: a meter with electricity is the ``ELECTRICITY_METER``; a component with a storage flow of hot-water
        heat is the ``HOT_WATER_TANK``; anything no row matches is a ``CONVERTER``.
        """
        carrier = lt.EnergyBalanceCarrier
        heat = set(GraphBuilder.HEAT_CARRIERS)
        outs = [f for f in node.flows if not f.is_input]
        consumes = {f.carrier for f in node.flows if f.is_input}
        link_out = {f.carrier for f in outs if f.kind is FlowKind.LINK}
        link_in = {f.carrier for f in node.flows if f.is_input and f.kind is FlowKind.LINK}
        stored = {f.carrier for f in node.flows if f.kind is FlowKind.STORAGE}
        boundary_out = {f.carrier for f in outs if f.kind is FlowKind.BOUNDARY}
        is_meter = node.kind == NodeKind.METER.value
        space, dhw = carrier.SPACE_HEATING_HEAT.value, carrier.DOMESTIC_HOT_WATER_HEAT.value
        criteria = (
            (Role.ELECTRICITY_METER, is_meter and any(f.carrier == carrier.ELECTRICITY.value for f in node.flows)),
            (Role.FUEL_METER, is_meter),
            (Role.PV, carrier.SOLAR.value in consumes and carrier.ELECTRICITY.value in link_out),
            (Role.SOLAR_COLLECTOR, carrier.SOLAR.value in consumes and bool(link_out & heat)),
            (Role.VEHICLE_STORAGE, carrier.VEHICLE_ELECTRICITY.value in {f.carrier for f in outs}),
            (Role.VEHICLE, carrier.VEHICLE_ELECTRICITY.value in consumes),
            (Role.ELECTRIC_STORAGE, bool(stored) and Roles.exchanges_electricity_with_one_node(node)),
            (Role.HOUSEHOLD, carrier.ELECTRICITY.value in consumes and carrier.ROOM_HEAT.value in link_out),
            (Role.COOLER, carrier.COOLING.value in link_in and carrier.AMBIENT_HEAT.value in boundary_out),
            (Role.HOT_WATER_TANK, dhw in stored or dhw in boundary_out),
            (Role.BUFFER, space in stored and space in link_in and space in link_out),
            (Role.BUILDING, bool(link_in & {space, carrier.ROOM_HEAT.value}) and not link_out & heat),
            (Role.DISTRIBUTION, not stored and any(c in heat and c in link_out for c in link_in)),
            (Role.GENERATOR, bool(link_out & heat) and any(c not in heat for c in consumes)),
        )
        return next((role for role, matches in criteria if matches), Role.CONVERTER)

    @staticmethod
    def exchanges_electricity_with_one_node(node: DrawnNode) -> bool:
        """Return whether a node takes electricity from and gives electricity to one and the same drawn node."""
        electricity = lt.EnergyBalanceCarrier.ELECTRICITY.value
        sources = {f.other_end for f in node.flows if f.is_input and f.kind is FlowKind.LINK and f.carrier == electricity}
        targets = {f.other_end for f in node.flows if not f.is_input and f.kind is FlowKind.LINK and f.carrier == electricity}
        return bool(sources & targets)


class Chains:
    """Finds the main chain: the row from the heat chain's meter to the building."""

    #: The carrier preference of the upstream walk: space heating first, then room heat, then any heat.
    UPSTREAM_PREFERENCE: ClassVar[Tuple[str, ...]] = (
        lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT.value,
        lt.EnergyBalanceCarrier.ROOM_HEAT.value,
    )

    @staticmethod
    def carrier_rank(carrier: Optional[str]) -> int:
        """Return the rank of a carrier in the upstream walk: 0 space heating, 1 room heat, 2 other heat, 3 the rest."""
        if carrier in Chains.UPSTREAM_PREFERENCE:
            return Chains.UPSTREAM_PREFERENCE.index(carrier)
        return 2 if carrier in GraphBuilder.HEAT_CARRIERS else 3

    @staticmethod
    def main_chain(graph: DrawnGraph) -> Tuple[str, ...]:
        """Return the main chain from its root to its end.

        With a building: start at the building with the largest heat input and walk upstream, always along the
        input link of the best carrier rank and, within it, the largest value, until a meter or a node without an
        input link. Without a building: start at the meter with the largest throughput (else the node with the
        largest throughput) and follow the largest output link. Example: GasMeter -> GenericBoiler ->
        SimpleHotWaterStorage -> HeatDistributionSystem -> Building. An empty graph has an empty chain.
        """
        if not graph.nodes:
            return ()
        buildings = [n for n in graph.nodes if graph.roles[n.name] is Role.BUILDING]
        if buildings:
            start = max(buildings, key=lambda n: (Chains.heat_input_kwh(n), n.name))
            walk = [start.name]
            while graph.node(walk[-1]).kind != NodeKind.METER.value:
                candidates = [f for f in graph.node(walk[-1]).flows_where(is_input=True, kind=FlowKind.LINK) if f.other_end not in walk]
                if not candidates:
                    break
                best = min(candidates, key=lambda f: (Chains.carrier_rank(f.carrier), -f.magnitude_kwh, f.other_end))
                walk.append(best.other_end)
            return tuple(reversed(walk))
        meters = [n for n in graph.nodes if n.kind == NodeKind.METER.value] or list(graph.nodes)
        start = max(meters, key=lambda n: (n.throughput_kwh, n.name))
        walk = [start.name]
        while True:
            candidates = [f for f in graph.node(walk[-1]).flows_where(is_input=False, kind=FlowKind.LINK) if f.other_end not in walk]
            if not candidates:
                return tuple(walk)
            walk.append(max(candidates, key=lambda f: (f.magnitude_kwh, f.other_end)).other_end)

    @staticmethod
    def heat_input_kwh(node: DrawnNode) -> float:
        """Return the heat a node receives through links (space heating and room heat), in kWh."""
        return sum(f.value_kwh for f in node.flows_where(is_input=True, kind=FlowKind.LINK) if f.carrier in GraphBuilder.HEAT_CARRIERS)


class Trees:
    """Hangs every drawn node into a tree: the chain's tree first, then further trees."""

    #: Roles that never hang from an input link; they are roots placed upstream of or beside what they feed.
    ROOT_ROLES: ClassVar[Tuple[Role, ...]] = (Role.PV, Role.SOLAR_COLLECTOR)

    @staticmethod
    def build(graph: DrawnGraph) -> Tuple[Tree, ...]:
        """Return the trees of the graph in placement order.

        The chain is the first tree's spine. Every other node hangs from the source of its largest input link once
        that source hangs somewhere (ties: carrier rank, then name); a PV, and any other node without an input link
        but a collector, hangs upstream in front of the node its largest output feeds. Nodes left over start a further tree: a meter first (largest throughput), else the
        node with the largest throughput.
        """
        trees: List[Tree] = []
        attached: List[str] = []
        root = graph.chain[0] if graph.chain else None
        while len(attached) < len(graph.nodes):
            if root is None:
                root = Trees.next_root(graph, attached)
            edges = Trees.grow(graph, root, attached)
            trees.append(Tree(root, tuple(edges)))
            root = None
        return tuple(trees)

    @staticmethod
    def grow(graph: DrawnGraph, root: str, attached: List[str]) -> List[TreeEdge]:
        """Attach the root (with the chain when the root is the chain's) and everything that hangs from it."""
        edges: List[TreeEdge] = []
        attached.append(root)
        if graph.chain and root == graph.chain[0]:
            for anchor, placed in zip(graph.chain, graph.chain[1:]):
                link = min(
                    (
                        graph.links[f.link_id]
                        for f in graph.node(placed).flows_where(is_input=True, kind=FlowKind.LINK)
                        if f.other_end == anchor and f.link_id is not None
                    ),
                    key=lambda candidate: (Chains.carrier_rank(candidate.carrier), -candidate.value_kwh),
                )
                edges.append(TreeEdge(anchor, placed, link.link_id))
                attached.append(placed)
        changed = True
        while changed:
            changed = False
            for node in graph.nodes:
                if node.name in attached:
                    continue
                edge = Trees.hanging_edge(graph, node, attached)
                if edge is not None:
                    edges.append(edge)
                    attached.append(node.name)
                    changed = True
        return edges

    @staticmethod
    def hanging_edge(graph: DrawnGraph, node: DrawnNode, attached: Sequence[str]) -> Optional[TreeEdge]:
        """Return the edge by which a node hangs from an attached node now, or None when it cannot hang yet."""
        role = graph.roles[node.name]
        is_source = not node.flows_where(is_input=True, kind=FlowKind.LINK)
        if role is Role.PV or (is_source and role is not Role.SOLAR_COLLECTOR):
            outputs = node.flows_where(is_input=False, kind=FlowKind.LINK)
            if outputs:
                best = max(outputs, key=lambda f: (f.magnitude_kwh, f.other_end))
                if best.other_end in attached and best.link_id is not None:
                    return TreeEdge(best.other_end, node.name, best.link_id, upstream=True)
            return None
        if role in Trees.ROOT_ROLES:
            return None
        inputs = node.flows_where(is_input=True, kind=FlowKind.LINK)
        if not inputs:
            return None
        best = min(inputs, key=lambda f: (-f.magnitude_kwh, Chains.carrier_rank(f.carrier), f.other_end))
        if best.other_end in attached and best.link_id is not None:
            return TreeEdge(best.other_end, node.name, best.link_id)
        return None

    @staticmethod
    def next_root(graph: DrawnGraph, attached: Sequence[str]) -> str:
        """Return the root of the next further tree among the nodes not attached yet.

        A meter comes first, then a node whose input links all come from attached nodes or that has none, then any
        node; within each group the largest throughput wins (then the name).
        """
        free = [n for n in graph.nodes if n.name not in attached]
        meters = [n for n in free if n.kind == NodeKind.METER.value]
        if meters:
            return max(meters, key=lambda n: (n.throughput_kwh, n.name)).name
        sources = [n for n in free if all(f.other_end in attached for f in n.flows_where(is_input=True, kind=FlowKind.LINK))]
        return max(sources or free, key=lambda n: (n.throughput_kwh, n.name)).name
