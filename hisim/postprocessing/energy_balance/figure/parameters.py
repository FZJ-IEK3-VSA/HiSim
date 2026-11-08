"""The matplotlib.sankey parameters of the figure: one entry per sub-diagram and per connector.

A *sub-diagram* is one ``Sankey.add`` call: the trunk of one drawn node with its flows. Its parameters are the ones a
hand layout of the prototype held: the prior sub-diagram and the link that connects it (none for a root), the ``at``
position of a tree's anchor, the trunk length, the title offset, the colour, and per flow its orientation (0 along
the axis, 1 and -1 to the frame's two sides) and path length, in the order the library stacks them. A *connector*
joins the two stubs of a cross-link. A complete pin file gives all of them (``pins.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional, Tuple

from hisim.postprocessing.energy_balance.figure.geometry import Direction, Side


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
