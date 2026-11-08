"""The matplotlib.sankey parameters of the figure: one entry per sub-diagram and per connector, and how they are built.

A *sub-diagram* is one ``Sankey.add`` call: the trunk of one drawn node with its flows. Its parameters are the ones a
hand layout of the prototype held: the prior sub-diagram and the link that connects it (none for a root), the ``at``
position of a tree member (any member, its whole tree moves), the trunk length, the title offset, the colour, and per flow its orientation
(0 along the axis, 1 and -1 to the frame's two sides) and path length, in the order the library stacks them. A
*connector* joins the two stubs of a cross-link. The search (``search.py``) changes the parameters through
:class:`Adjustments` and rebuilds them each round.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.postprocessing.energy_balance.figure.geometry import Direction, Side
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, DrawnNode, Flow, FlowKind, Role, Tree, TreeEdge
from hisim.postprocessing.energy_balance.figure.orientation import Orientation


@dataclass(frozen=True)
class LayoutConstants:
    """The lengths of the layout in canvas units (u), the library's style constants and the search's bounds.

    The pixel scale is fixed (``px_per_unit``), so text and bands keep their proportions in every figure and the
    figure grows with its canvas.
    """

    widest_band_in_units: float = 7.0
    px_per_unit: float = 30.0
    dpi: float = 110.0
    min_band_px: float = 2.0
    gap: float = 0.56
    radius: float = 0.24
    shoulder: float = 0.096
    offset: float = 0.192
    head_angle: float = 110.0
    tolerance: float = 1e-6
    title_pad: float = 0.6
    min_trunk: float = 3.0
    stub: float = 1.3
    link_stub: float = 0.48
    #: How much longer the stubs of a parallel second are per narrower second inside it (room for a route and a label).
    second_gap: float = 1.5
    tail_stub: float = 0.8
    cut_stub: float = 0.56
    min_connection: float = 2.0
    node_margin: float = 1.0
    tree_margin: float = 1.5
    slide: float = 0.5
    route_slide: float = 2.0
    grow: float = 0.5
    max_rounds: int = 12
    grid: float = 0.25
    bend_cost: float = 3.0
    clearance: float = 0.3
    connector_radius: float = 0.64
    min_radius: float = 0.24
    connector_overlap: float = 0.096
    lead: float = 0.6
    canvas_margin: float = 1.0
    font_pt: float = 9.0
    title_pt: float = 11.0
    label_gap: float = 0.12
    max_title_width: float = 6.0

    @property
    def pitch(self) -> float:
        """The library's head pitch: the length of an arrowhead or notch per unit of half band width."""
        return math.tan(math.pi * (1 - self.head_angle / 180.0) / 2.0)


class Scale:
    """The figure's one scale: kWh per canvas unit of band width."""

    #: The mantissas of the scale series 1, 2, 4, 5 x 10^n; the 4 puts the full-year building-sizer systems at 4,000.
    MANTISSAS: ClassVar[Tuple[float, ...]] = (1.0, 2.0, 4.0, 5.0)

    @staticmethod
    def kwh_per_unit(widest_kwh: float, widest_band_in_units: float) -> float:
        """Return the smallest value of the series 1, 2, 4, 5 x 10^n for which the widest band is at most the limit.

        Example: a widest band of 26,315 kWh with a limit of 7 u needs at least 3,759 kWh/u, so the scale is
        4,000 kWh/u; a widest band of 600 kWh gives 100 kWh/u.

        Raises:
            ValueError: If the limit is not positive.
        """
        if widest_band_in_units <= 0:
            raise ValueError(f"the widest band must be allowed a positive width, not {widest_band_in_units} u")
        if widest_kwh <= 0:
            return 1.0
        exponent = math.floor(math.log10(widest_kwh / widest_band_in_units)) - 1
        while True:
            for mantissa in Scale.MANTISSAS:
                value = mantissa * 10.0**exponent
                if widest_kwh / value <= widest_band_in_units + 1e-12:
                    return value
            exponent += 1

    @staticmethod
    def min_band_kwh(kwh_per_unit: float, constants: LayoutConstants) -> float:
        """Return the energy the minimum drawn band width stands for, in kWh (2 px at 30 px/u and 4,000 kWh/u: 267 kWh)."""
        return constants.min_band_px / constants.px_per_unit * kwh_per_unit

    @staticmethod
    def drawn_kwh(value_kwh: float, min_band_kwh: float) -> float:
        """Return the signed energy a band is drawn with: at least the minimum band, the sign kept.

        Example: -5 kWh with a minimum of 267 kWh is drawn as -267 kWh; both ends of a link get the same value, so the
        library's connection still matches.
        """
        return math.copysign(max(abs(value_kwh), min_band_kwh), value_kwh)


@dataclass(frozen=True)
class LabelPin:
    """A label placed by hand: its shift from the library's label position, its alignment and its style.

    Attributes:
        shift: The shift from the library's position (a flow's) or the segment point (a connector's), in u.
        ha: The horizontal alignment, or "" for the rule that anchors a label away from its arrow.
        va: The vertical alignment, or "" for the rule.
        inside: Whether the label is written inside its band (bold, ``color``).
        color: The colour of an inside label.
        exempt: The sub-diagrams an inside label may lie on.
        segment: A connector label's segment index.
        side: A connector label's side of its segment (above, below, left, right).
        at: Where along the segment, as a fraction.
    """

    shift: Tuple[float, float] = (0.0, 0.0)
    ha: str = ""
    va: str = ""
    inside: bool = False
    color: str = "white"
    exempt: Tuple[str, ...] = ()
    segment: int = 0
    side: str = ""
    at: float = 0.5


@dataclass
class FlowParams:
    """One flow of a sub-diagram as the library gets it.

    Attributes:
        flow_id: The graph's flow id ("<node>|<label>", "<node>|cut", "<node>|residual").
        value_kwh: The flow's energy over the run, signed (+ in, - out), in kWh.
        drawn_kwh: The signed energy the band is drawn with (at least the minimum band).
        orientation: The library's orientation: 0 along the axis, 1 to the frame's top, -1 to its bottom.
        pathlength: The straight length after the bend (lateral) or from the trunk (tail and head), in u.
        side: The side of the trunk the flow attaches to.
        label: A hand-placed label, or None for a computed one.
        name: A display name that replaces the style's, "" for the style's.
    """

    flow_id: str
    value_kwh: float
    drawn_kwh: float
    orientation: int
    pathlength: float
    side: Side
    label: Optional[LabelPin] = None
    name: str = ""

    @property
    def inflated(self) -> bool:
        """Whether the band is drawn wider than its value (the minimum band)."""
        return abs(self.drawn_kwh) > abs(self.value_kwh) + 1e-9


@dataclass
class DiagramParams:
    """One sub-diagram: the node, its connection, position, trunk, title and flows in the library's order.

    Attributes:
        component: The drawn node's name.
        prior: The node it hangs from, None for a root.
        connect: The id of its own flow that connects it to the prior, None for a root.
        prior_flow: The id of the prior's flow it connects to, None for a root.
        axis: The page direction of the trunk's main flow.
        rotation: The library's rotation of a root, in degrees (connected sub-diagrams take their frame from the link).
        at: Where the anchor of a further tree goes (its trunk centre), in u; None for the first tree or until placed.
        trunklength: The trunk's length, in u.
        title_offset: The title's shift from the trunk centre, in u.
        color: The fill colour.
        title: The title text (lines separated by newlines).
        flows: The flows in the order the library stacks them.
    """

    component: str
    prior: Optional[str]
    connect: Optional[str]
    prior_flow: Optional[str]
    axis: Direction
    rotation: int
    at: Optional[Tuple[float, float]]
    trunklength: float
    title_offset: Tuple[float, float]
    color: str
    title: str
    flows: List[FlowParams] = field(default_factory=list)

    def flow(self, flow_id: str) -> FlowParams:
        """Return the flow of that id."""
        return next(f for f in self.flows if f.flow_id == flow_id)

    def index(self, flow_id: str) -> int:
        """Return the position of a flow in the library's list."""
        return next(k for k, f in enumerate(self.flows) if f.flow_id == flow_id)


@dataclass
class ConnectorParams:
    """One cross-link drawn as a connector: the link, its two flow ids, its pinned turns and label, if any.

    ``parallel`` marks a second link between the same two nodes: its connector keeps the target's notch and enters it
    with an arrowhead, so it reads as an arrow of its own.
    """

    link_id: int
    label: str
    source_flow: str
    target_flow: str
    turns: Optional[List[object]] = None
    label_pin: Optional[LabelPin] = None
    parallel: bool = False


@dataclass
class Parameters:
    """Everything the library and the connectors need for one build.

    Attributes:
        kwh_per_unit: The scale, in kWh per u of band width.
        min_band_kwh: The energy drawn at the minimum band width, in kWh.
        diagrams: The sub-diagrams in the order they are added (a prior before its children).
        connectors: The cross-links, the widest first.
        canvas: The axes limits (x0, x1, y0, y1) a pin file fixes, None to compute them.
        pinned: Whether the parameters come from a complete pin file (no search moves).
    """

    kwh_per_unit: float
    min_band_kwh: float
    diagrams: List[DiagramParams]
    connectors: List[ConnectorParams]
    canvas: Optional[Tuple[float, float, float, float]] = None
    pinned: bool = False

    def diagram(self, component: str) -> DiagramParams:
        """Return the sub-diagram of a node."""
        return next(d for d in self.diagrams if d.component == component)

    def position(self, component: str) -> int:
        """Return the index of a node's sub-diagram in the order they are added."""
        return next(k for k, d in enumerate(self.diagrams) if d.component == component)


@dataclass
class Adjustments:
    """What the search has added to the computed parameters, in u; all start empty.

    Attributes:
        pathlength: Extra path length of a flow, by flow id (a terminal branch, or a tree connection's output).
        trunk: Extra trunk length, by node.
        tree_shift: The shift of a further tree from its computed position, by root, in u.
    """

    pathlength: Dict[str, float] = field(default_factory=dict)
    trunk: Dict[str, float] = field(default_factory=dict)
    tree_shift: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    @staticmethod
    def add(table: Dict[str, float], key: str, amount: float) -> None:
        """Add an amount to one entry of a table, starting from 0."""
        table[key] = table.get(key, 0.0) + amount

    def shift(self, root: str, dx: float, dy: float) -> None:
        """Move a further tree by (dx, dy) u more."""
        x, y = self.tree_shift.get(root, (0.0, 0.0))
        self.tree_shift[root] = (x + dx, y + dy)

    def signature(self) -> Tuple[object, ...]:
        """Return a value that changes whenever an adjustment does (the search stops on an unchanged round)."""
        return (tuple(sorted(self.pathlength.items())), tuple(sorted(self.trunk.items())), tuple(sorted(self.tree_shift.items())))


class FrameRule:
    """The library's frame: how a side becomes an orientation, and the connecting orientation of a child."""

    @staticmethod
    def up(axis: Direction) -> Direction:
        """Return the page direction of a frame's top side (orientation 1): E -> N, S -> E, W -> S, N -> W."""
        return axis.turned_left()

    @staticmethod
    def orientation(side: Side) -> int:
        """Return the library's orientation of a side: 0 for the tail and the head, 1 for the left (top), -1 for the right."""
        return {Side.TAIL: 0, Side.HEAD: 0, Side.LEFT: 1, Side.RIGHT: -1}[side]

    @staticmethod
    def direction(axis: Direction, orientation: int) -> Direction:
        """Return the page direction an output of the given orientation leaves a trunk of the given axis in."""
        if orientation == 0:
            return axis
        return axis.turned_left() if orientation == 1 else axis.turned_right()

    @staticmethod
    def connecting_orientation(axis: Direction, parent_direction: Direction) -> Optional[int]:
        """Return the orientation of a child's connecting input for its wanted axis and the parent's output direction.

        0 when the axis continues the parent's band, 1 when the band enters the frame's top, -1 when it enters the
        bottom; None when the axis runs against the band (impossible). Examples: the battery below the meter (band S,
        axis S) 0; the household below the meter (band S, axis E) 1; the tank above the heat pump (band N, axis E) -1.
        """
        if axis is parent_direction:
            return 0
        if FrameRule.up(axis) is parent_direction.opposite():
            return 1
        if FrameRule.up(axis) is parent_direction:
            return -1
        return None

    @staticmethod
    def root_rotation(axis: Direction) -> int:
        """Return the library's rotation in degrees that turns a root's eastward frame to the given axis."""
        return {Direction.E: 0, Direction.N: 90, Direction.W: 180, Direction.S: -90}[axis]


class SideOrder:
    """Emits a sub-diagram's flow list so the library stacks every side in the wanted order."""

    @staticmethod
    def emit(flows: Sequence[FlowParams], wanted: Sequence[str]) -> List[FlowParams]:
        """Return the flows in the library's list order for the wanted order of each side.

        ``wanted`` lists every flow id, each side's flows nearest the trunk first (for the tail and the head: the
        frame's top first). The library stacks the top side (orientation 1) and the tail and head in list order and the
        bottom side (orientation -1) in reversed list order, so the bottom groups are reversed; the groups follow each
        other in the order tail, top, head, bottom. Example: bottom outputs wanted [battery, household] are emitted
        [household, battery], so the battery sits nearest the trunk.
        """
        by_id = {f.flow_id: f for f in flows}
        groups: Dict[Tuple[int, bool], List[FlowParams]] = {}
        for flow_id in wanted:
            flow = by_id[flow_id]
            groups.setdefault((flow.orientation, flow.value_kwh > 0), []).append(flow)
        result: List[FlowParams] = []
        for key in ((0, True), (1, True), (1, False), (0, False), (-1, True), (-1, False)):
            group = groups.get(key, [])
            result.extend(reversed(group) if key[0] == -1 else group)
        return result


@dataclass(frozen=True)
class TextSizes:
    """The text widths the parameters need, in u: every node's title block and every link's label."""

    title_width: Mapping[str, float]
    link_label_width: Mapping[int, float]


class ParameterBuilder:
    """Builds the library's parameters from the drawn graph, the orientation table and the search's adjustments."""

    #: Slot rank of children on one lateral side, nearest the trunk first: the battery nearest, the household farthest.
    SLOT_RANK: ClassVar[Mapping[Role, int]] = {Role.ELECTRIC_STORAGE: 0, Role.VEHICLE_STORAGE: 1, Role.COOLER: 2, Role.HOUSEHOLD: 3}

    @staticmethod
    def sub_diagram_order(graph: DrawnGraph) -> List[TreeEdge]:
        """Return the library's sub-diagram order as edges (anchor, placed), with the roots as edges anchored on "".

        A source hung upstream of its meter (the PV) becomes the tree's first sub-diagram and the meter hangs from it,
        as the prototype's layouts did; every other node follows its anchor.
        """
        order: List[TreeEdge] = []
        for tree in graph.trees:
            order.extend(ParameterBuilder.tree_order(tree))
        return order

    @staticmethod
    def tree_order(tree: Tree) -> List[TreeEdge]:
        """Return one tree's sub-diagrams as edges (prior, child, link) in the order the library needs them."""
        upstream = [edge for edge in tree.edges if edge.upstream and edge.anchor == tree.root]
        result: List[TreeEdge] = []
        if upstream:
            source = upstream[0]
            result.append(TreeEdge("", source.placed, -1))
            result.append(TreeEdge(source.placed, tree.root, source.link_id))
        else:
            result.append(TreeEdge("", tree.root, -1))
        result.extend(edge for edge in tree.edges if edge not in upstream)
        return result

    @staticmethod
    def build(graph: DrawnGraph, sizes: TextSizes, adjustments: Adjustments, constants: LayoutConstants, styles: Mapping[str, Tuple[str, str]]) -> "Parameters":
        """Return the parameters of every sub-diagram and connector for the current adjustments.

        Args:
            graph: The drawn graph with its roles, chain and trees.
            sizes: The text widths of the titles and link labels, in u.
            adjustments: The search's extras so far.
            constants: The layout constants.
            styles: The (title, colour) of every node.
        """
        widest = max((f.magnitude_kwh for node in graph.nodes for f in node.flows), default=1.0)
        kwh_per_unit = Scale.kwh_per_unit(widest, constants.widest_band_in_units)
        min_band = Scale.min_band_kwh(kwh_per_unit, constants)
        diagrams: List[DiagramParams] = []
        axes: Dict[str, Direction] = {}
        tree_links = set()
        for edge in ParameterBuilder.sub_diagram_order(graph):
            node = graph.node(edge.placed)
            prior_flow = connect = None
            if edge.anchor:
                tree_links.add(edge.link_id)
                connect = next(f.flow_id for f in node.flows if f.link_id == edge.link_id)
                prior_flow = next(f.flow_id for f in graph.node(edge.anchor).flows if f.link_id == edge.link_id)
            diagram = ParameterBuilder.diagram(graph, node, (edge.anchor or None, connect, prior_flow), diagrams, axes, (sizes, adjustments, constants, min_band, styles))
            axes[node.name] = diagram.axis
            diagrams.append(diagram)
        seconds = Orientation.parallel_seconds(graph)
        connectors = [
            ConnectorParams(
                link.link_id,
                link.label,
                ParameterBuilder.flow_of(graph, link.source, link.link_id),
                ParameterBuilder.flow_of(graph, link.target, link.link_id),
                parallel=link.link_id in seconds,
            )
            for link in sorted(graph.links, key=lambda link: (-link.value_kwh, link.link_id))
            if link.link_id not in tree_links
        ]
        return Parameters(kwh_per_unit, min_band, diagrams, connectors)

    @staticmethod
    def flow_of(graph: DrawnGraph, node: str, link_id: int) -> str:
        """Return the id of a node's end of a link."""
        return next(f.flow_id for f in graph.node(node).flows if f.link_id == link_id)

    @staticmethod
    def diagram(
        graph: DrawnGraph,
        node: DrawnNode,
        connection: Tuple[Optional[str], Optional[str], Optional[str]],
        placed: Sequence[DiagramParams],
        axes: Mapping[str, Direction],
        context: Tuple[TextSizes, Adjustments, LayoutConstants, float, Mapping[str, Tuple[str, str]]],
    ) -> DiagramParams:
        """Return one sub-diagram's parameters: its axis from its connection, its flows' orientations, order and lengths."""
        sizes, adjustments, constants, min_band, styles = context
        prior, connect, prior_flow = connection
        role = graph.roles[node.name]
        sides = Orientation.sides(graph, node, connect)
        axis, connecting = ParameterBuilder.axis_and_connection(graph, node.name, prior, prior_flow, placed, axes, sides.get(connect or ""))
        flows: List[FlowParams] = []
        for flow in node.flows:
            side = sides[flow.flow_id]
            orientation = connecting if flow.flow_id == connect else ParameterBuilder.flow_orientation(side, flow.is_input)
            flows.append(
                FlowParams(
                    flow.flow_id,
                    flow.value_kwh,
                    Scale.drawn_kwh(flow.value_kwh, min_band),
                    orientation,
                    ParameterBuilder.pathlength(graph, flow, side, connect, sizes, constants) + adjustments.pathlength.get(flow.flow_id, 0.0),
                    side,
                )
            )
        wanted = ParameterBuilder.wanted_order(graph, node, flows, connect)
        title, color = styles[node.name]
        # The library's straight trunk is its length less a gap at each end, and the prior's arrowhead reaches into the
        # tail by the notch depth of the widest tail input; the title, centred, needs that room on both sides.
        kwh_per_unit = min_band * constants.px_per_unit / constants.min_band_px
        notch = max((abs(f.drawn_kwh) / kwh_per_unit / 2 * constants.pitch for f in flows if f.orientation == 0 and f.value_kwh > 0), default=0.0)
        title_room = sizes.title_width.get(node.name, 0.0) + 2 * (constants.title_pad + constants.gap + notch)
        trunk = max(constants.min_trunk, title_room) + adjustments.trunk.get(node.name, 0.0)
        del role
        return DiagramParams(
            node.name,
            prior,
            connect,
            prior_flow,
            axis,
            FrameRule.root_rotation(axis) if prior is None else 0,
            None,
            trunk,
            (0.0, 0.0),
            color,
            title,
            SideOrder.emit(flows, wanted),
        )

    @staticmethod
    def axis_and_connection(
        graph: DrawnGraph,
        name: str,
        prior: Optional[str],
        prior_flow: Optional[str],
        placed: Sequence[DiagramParams],
        axes: Mapping[str, Direction],
        connect_side: Optional[Side],
    ) -> Tuple[Direction, int]:
        """Return a sub-diagram's axis and the orientation of its connecting input.

        A root takes its role's nominal axis. A child entering at its tail continues the parent's band (orientation 0);
        a child entering a lateral side keeps its role's nominal axis and enters the frame's top or bottom by the
        frame rule. An impossible combination falls back to the continuation.
        """
        nominal = Orientation.nominal_axis(graph.roles[name])
        if prior is None or prior_flow is None:
            return nominal, 0
        parent = next(d for d in placed if d.component == prior)
        band = FrameRule.direction(axes[prior], parent.flow(prior_flow).orientation)
        if connect_side in (None, Side.TAIL):
            return band, 0
        orientation = FrameRule.connecting_orientation(nominal, band)
        return (band, 0) if orientation is None else (nominal, orientation)

    @staticmethod
    def flow_orientation(side: Side, is_input: bool) -> int:
        """Return a flow's orientation; an output on the tail or an input on the head goes to the frame's bottom or top.

        The library draws every orientation-0 input at the tail and every orientation-0 output at the head, so the
        two impossible combinations are moved to a lateral side.
        """
        if side is Side.TAIL and not is_input:
            return -1
        if side is Side.HEAD and is_input:
            return 1
        return FrameRule.orientation(side)

    @staticmethod
    def pathlength(graph: DrawnGraph, flow: Flow, side: Side, connect: Optional[str], sizes: TextSizes, constants: LayoutConstants) -> float:
        """Return a flow's initial path length.

        A tree connection's output at the parent is the connection length: its label's width plus a pad at each end, at
        least ``min_connection``; the child's connecting input is ``tail_stub`` at a tail, ``link_stub`` on a side; a
        cross-link's stub is ``link_stub``; a boundary supply at a tail ``tail_stub``; the solar cut ``cut_stub``;
        every other terminal branch ``stub``.
        """
        if flow.kind is FlowKind.CUT:
            return constants.cut_stub
        if flow.flow_id == connect:
            return constants.tail_stub if side is Side.TAIL else constants.link_stub
        if flow.kind is FlowKind.LINK:
            child_edge = next((e for tree in graph.trees for e in tree.edges if e.link_id == flow.link_id), None)
            is_parent_end = child_edge is not None and (
                (not child_edge.upstream and child_edge.anchor == flow.node) or (child_edge.upstream and child_edge.placed == flow.node)
            )
            if is_parent_end and flow.link_id is not None:
                return max(constants.min_connection, sizes.link_label_width.get(flow.link_id, 0.0) + 2 * constants.title_pad)
            return constants.link_stub + ParameterBuilder.seconds_inside(graph, flow) * constants.second_gap
        if flow.is_input and side is Side.TAIL:
            return constants.tail_stub
        return constants.stub

    @staticmethod
    def seconds_inside(graph: DrawnGraph, flow: Flow) -> int:
        """Return how many narrower parallel seconds between the same two nodes run inside this flow's link.

        Seconds into one child nest: the widest is routed first and runs outermost. Each narrower second inside it
        needs room for its route and its label, so the outer one's stubs grow by ``second_gap`` per second inside.
        Example: the heat pump's hot-water electricity (26 kWh) has its heating rod's electricity (1.6 kWh) inside: 1.
        """
        seconds = Orientation.parallel_seconds(graph)
        if flow.link_id not in seconds:
            return 0
        link = graph.links[flow.link_id]
        return sum(
            1
            for other in graph.links
            if other.link_id in seconds
            and (other.source, other.target) == (link.source, link.target)
            and (other.value_kwh, other.link_id) < (link.value_kwh, link.link_id)
        )

    @staticmethod
    def wanted_order(graph: DrawnGraph, node: DrawnNode, flows: Sequence[FlowParams], connect: Optional[str]) -> List[str]:
        """Return every flow id of a node, each side's flows in the wanted order (nearest the trunk or topmost first).

        The connecting flow first, then links by the slot rank of their other end and by value, then terminal flows
        by value, the residual last. A second parallel link (``Orientation.parallel_seconds``) goes farthest from the trunk at both
        ends.
        Cross-link inputs on a lateral side go the other way round, smallest nearest the
        trunk: the source stacks them largest first from its frame's top, and the connectors nest without crossing only
        when the largest enters farthest from the trunk (the household's two heat flows into the building).
        """
        by_id = {f.flow_id: f for f in node.flows}
        seconds = Orientation.parallel_seconds(graph)

        def key(params: FlowParams) -> Tuple[int, int, float, str]:
            flow = by_id[params.flow_id]
            if params.flow_id == connect:
                return 0, 0, 0.0, params.flow_id
            if flow.kind is FlowKind.LINK and flow.link_id in seconds:
                # A second parallel link sits farthest from the trunk at both ends: it leaves its source beyond the
                # source's other bands on that side and enters its target from the side its connector comes from.
                return 4, 0, 0.0, params.flow_id
            if flow.kind is FlowKind.LINK:
                rank = ParameterBuilder.SLOT_RANK.get(graph.roles.get(flow.other_end, Role.CONVERTER), len(ParameterBuilder.SLOT_RANK))
                ascending = flow.is_input and params.orientation != 0
                return 1, rank, flow.magnitude_kwh if ascending else -flow.magnitude_kwh, params.flow_id
            return (3 if flow.kind is FlowKind.RESIDUAL else 2), 0, -flow.magnitude_kwh, params.flow_id

        return [f.flow_id for f in sorted(flows, key=key)]
