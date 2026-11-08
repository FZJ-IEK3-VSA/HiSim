"""The texts of the Sankey figure: titles with their metric lines, flow labels and their candidates, and the caption.

The library's own labels are disabled; every label is placed here from what the library drew: a terminal branch's
label beyond its outer end or beside its stub, a tree connection's label inside its band or beside it, a connector's
beside one of its segments, a title at its trunk's centre. A hand-placed label (a pin) has one candidate only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

import numpy as np

from hisim.postprocessing.energy_balance.figure.connectors import Route, Router
from hisim.postprocessing.energy_balance.figure.drawing import DrawnDiagram
from hisim.postprocessing.energy_balance.figure.geometry import Box, Direction
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, Flow, FlowKind
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs
from hisim.postprocessing.energy_balance.figure.labels import Candidate, Candidates, LabelSpec, TextBlock, TextMeasure
from hisim.postprocessing.energy_balance.figure.metrics import Metrics
from hisim.postprocessing.energy_balance.figure.parameters import ConnectorParams, DiagramParams, FlowParams, LabelPin, LayoutConstants, Parameters
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle

Point = Tuple[float, float]


@dataclass(frozen=True)
class TextContext:
    """What the label specs need besides the drawing: the graph, inputs, style, measure and constants."""

    graph: DrawnGraph
    inputs: SankeyInputs
    style: SankeyStyle
    measure: TextMeasure
    constants: LayoutConstants


class FigureTexts:
    """Builds the figure's texts and the label specs from the drawn sub-diagrams."""

    #: Names are wrapped to lines of this many characters in the second set of label candidates.
    WRAP_CHARACTERS: ClassVar[int] = 18
    #: The colours of an error and of normal text.
    ERROR_RED: ClassVar[str] = "#c62828"
    TEXT: ClassVar[str] = "#1a1a1a"
    #: How far above the trunk's centre line a title with metric lines ends, in u (the prototype's 0.06).
    TITLE_LIFT: ClassVar[float] = 0.06

    @staticmethod
    def title_block(graph: DrawnGraph, node: str, title: str, inputs: SankeyInputs, constants: LayoutConstants) -> TextBlock:
        """Return a node's title block: its title lines (bold) and its metric lines."""
        title_lines = tuple(title.split("\n"))
        metric_lines = tuple(Metrics.node_lines(graph, graph.node(node), inputs))
        return TextBlock(title_lines + metric_lines, constants.title_pt, len(title_lines))

    @staticmethod
    def value_text(flow: FlowParams) -> str:
        """Return a flow's value line, with " †" when its band is drawn at the minimum width."""
        return Metrics.format_kwh(flow.value_kwh) + (" †" if flow.inflated else "")

    @staticmethod
    def owner_class(flow: Flow, class_of: Mapping[str, str]) -> str:
        """Return the class of the component that owns a flow's port (named by the label's first part), "" if unknown."""
        return class_of.get(flow.label.split(".", 1)[0], "")

    @staticmethod
    def residual_reason(graph: DrawnGraph, inputs: SankeyInputs, node: str) -> str:
        """Return why a node's residual is an error ("undeclared: no energy ports", "balance does not close"), else ""."""
        if graph.node(node).kind == "undeclared":
            return "undeclared: no energy ports"
        if inputs.verdict_of.get(node) == "does_not_close":
            return "balance does not close"
        return ""

    @staticmethod
    def flow_name(context: TextContext, flow: Flow, params: FlowParams) -> str:
        """Return a flow's display name: the pin's name, else the style's by class and port."""
        return params.name or context.style.flow_name(flow, FigureTexts.owner_class(flow, context.inputs.class_of))

    @staticmethod
    def stub_geometry(drawn: DrawnDiagram, params: FlowParams, constants: LayoutConstants, kwh_per_unit: float) -> Tuple[Point, Point, float, Direction]:
        """Return a branch's outer end, the middle of its straight part, its width and the direction away from the trunk.

        The library's tip of an output is its arrow tip; the tip of an input is its notch's inner point, so the input's
        outer edge lies half its width times the pitch further out.
        """
        width = abs(params.drawn_kwh) / kwh_per_unit
        travel = drawn.direction(params.flow_id)
        away = travel if params.value_kwh < 0 else travel.opposite()
        tip = drawn.tip(params.flow_id)
        outer = tip if params.value_kwh < 0 else tip + np.array(away.vector) * width / 2 * constants.pitch
        middle = outer - np.array(away.vector) * params.pathlength / 2
        return (float(outer[0]), float(outer[1])), (float(middle[0]), float(middle[1])), width, away

    @staticmethod
    def pinned_flow_candidate(drawn: DrawnDiagram, params: FlowParams, size: Tuple[float, float], constants: LayoutConstants, kwh_per_unit: float) -> Candidate:
        """Return the one candidate of a hand-placed flow label, as the prototype placed it.

        The library's label position plus the pin's shift, anchored away from the arrow (or centred for an inside label,
        or the pin's own alignment); an input's label moved out by its notch depth.
        """
        pin = params.label or LabelPin()
        x, y = drawn.label_positions[drawn.index(params.flow_id)]
        x, y = x + pin.shift[0], y + pin.shift[1]
        _, _, width, away = FigureTexts.stub_geometry(drawn, params, constants, kwh_per_unit)
        ha, va = ("center", "center") if pin.inside else Candidates.away_alignment(away)
        if not pin.inside and params.value_kwh > 0:
            x, y = x + away.vector[0] * width / 2 * constants.pitch, y + away.vector[1] * width / 2 * constants.pitch
        return Candidates.anchored((x, y), pin.ha or ha, pin.va or va, size)

    @staticmethod
    def terminal_spec(context: TextContext, drawn: DrawnDiagram, params: FlowParams, kwh_per_unit: float) -> LabelSpec:
        """Return the label of a terminal flow: name and value, red with a reason for a residual of a faulty node."""
        constants = context.constants
        node = drawn.component
        flow = next(f for f in context.graph.node(node).flows if f.flow_id == params.flow_id)
        name = FigureTexts.flow_name(context, flow, params)
        lines: Tuple[str, ...] = (*name.split("\n"), FigureTexts.value_text(params))
        color = FigureTexts.TEXT
        if flow.kind is FlowKind.RESIDUAL:
            reason = FigureTexts.residual_reason(context.graph, context.inputs, node)
            if reason:
                color = FigureTexts.ERROR_RED
                lines = (*name.split("\n"), reason, FigureTexts.value_text(params))
        block = TextBlock(lines, constants.font_pt, 0, color)
        if params.label is not None:
            candidates: List[Candidate] = [FigureTexts.pinned_flow_candidate(drawn, params, context.measure.size(block), constants, kwh_per_unit)]
        else:
            wrapped = TextBlock((*Candidates.wrapped(name, FigureTexts.WRAP_CHARACTERS).split("\n"), *lines[len(name.split("\n")) :]), constants.font_pt, 0, color)
            outer, middle, width, _ = FigureTexts.stub_geometry(drawn, params, constants, kwh_per_unit)
            variants = [(context.measure.size(block), block.lines), (context.measure.size(wrapped), wrapped.lines)]
            candidates = Candidates.terminal((outer, middle, width), variants, constants.label_gap)
        return LabelSpec(f"flow:{params.flow_id}", block, tuple(candidates), (), abs(params.drawn_kwh), False, f"path:{params.flow_id}")

    @staticmethod
    def link_block(context: TextContext, source_flow: str, params: FlowParams) -> TextBlock:
        """Return a link's label: its display name (from the source's end) and its value, white and bold."""
        node = source_flow.split("|", 1)[0]
        flow = next(f for f in context.graph.node(node).flows if f.flow_id == source_flow)
        lines = tuple(FigureTexts.flow_name(context, flow, params).split("\n"))
        color = params.label.color if params.label is not None and params.label.inside else "white"
        return TextBlock((*lines, FigureTexts.value_text(params)), context.constants.font_pt, len(lines) + 1, color)

    @staticmethod
    def connection_spec(context: TextContext, parameters: Parameters, child: DiagramParams, drawn: Mapping[str, DrawnDiagram]) -> Optional[LabelSpec]:
        """Return the label of a tree connection: inside its band when the band is wide enough, else beside it."""
        if child.prior is None or child.prior_flow is None or child.connect is None:
            return None
        prior = parameters.diagram(child.prior)
        out = prior.flow(child.prior_flow)
        block = FigureTexts.link_block(context, child.prior_flow, out)
        tip = drawn[child.prior].tip(child.prior_flow)
        d = np.array(drawn[child.prior].direction(child.prior_flow).vector)
        start, end = tip - d * out.pathlength, tip + d * child.flow(child.connect).pathlength
        points = [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
        width = abs(out.drawn_kwh) / parameters.kwh_per_unit
        size = context.measure.size(block)
        exempt: Tuple[str, ...] = (f"diagram:{child.prior}", f"diagram:{child.component}")
        if out.label is not None:
            candidate = FigureTexts.pinned_flow_candidate(drawn[child.prior], out, size, context.constants, parameters.kwh_per_unit)
            exempt = exempt + tuple(f"diagram:{name}" for name in out.label.exempt)
            if not out.label.inside:
                block = TextBlock(block.lines, block.font_pt, 0, FigureTexts.TEXT)
            return LabelSpec(f"flow:{child.prior_flow}", block, (candidate,), exempt, width, out.label.inside, "")
        candidates = Candidates.link(points, width, size, context.constants.label_gap)
        inside = width >= size[1] + context.constants.label_gap and bool(candidates) and abs(points[0][1] - points[1][1]) < 1e-9
        if not inside:
            block = TextBlock(block.lines, block.font_pt, 0, FigureTexts.TEXT)
        return LabelSpec(f"flow:{child.prior_flow}", block, tuple(candidates), exempt if inside else (), width, inside, f"path:{child.prior_flow}")

    @staticmethod
    def connector_spec(context: TextContext, parameters: Parameters, connector_link: int, route: Route, drawn: Mapping[str, DrawnDiagram]) -> LabelSpec:
        """Return the label of a connector: beside one of its segments (a pin names the segment, side and shift)."""
        params = next(c for c in parameters.connectors if c.link_id == connector_link)
        source = parameters.diagram(params.source_flow.split("|", 1)[0]).flow(params.source_flow)
        block = FigureTexts.link_block(context, params.source_flow, source)
        block = TextBlock(block.lines, block.font_pt, 0, FigureTexts.TEXT)
        size = context.measure.size(block)
        width = route.width
        if params.label_pin is not None and len(route.points) >= 2:
            pin = params.label_pin
            segment = min(pin.segment, len(route.points) - 2)
            (x_a, y_a), (x_b, y_b) = route.points[segment], route.points[segment + 1]
            horizontal = abs(y_b - y_a) < 1e-9
            side = pin.side or ("above" if horizontal else "right")
            gap = width / 2 + 0.12
            offset = {"above": (0.0, gap), "below": (0.0, -gap), "left": (-gap, 0.0), "right": (gap, 0.0)}[side]
            point = (x_a + (x_b - x_a) * pin.at + offset[0] + pin.shift[0], y_a + (y_b - y_a) * pin.at + offset[1] + pin.shift[1])
            ha = {"left": "right", "right": "left"}.get(side, "center")
            va = {"above": "bottom", "below": "top"}.get(side, "center")
            return LabelSpec(f"link:{connector_link}", block, (Candidates.anchored(point, ha, va, size),), (), width, False, "")
        points = FigureTexts.with_stubs(parameters, params, route, drawn)
        candidates = [c for c in Candidates.link(points, width, size, context.constants.label_gap) if not FigureTexts.inside(c, route)]
        # A connector beside the tree connection into the same child (a second input from the same source) gets room
        # by a longer tree connection; any other connector's label has no growth move.
        target = parameters.diagram(params.target_flow.split("|", 1)[0])
        grows = f"path:{target.prior_flow}" if target.prior == params.source_flow.split("|", 1)[0] and target.prior_flow else ""
        outer = FigureTexts.outer_sibling(parameters, params)
        if outer is not None:
            # Two seconds into one child nest: the wider is routed first and runs outside, so the narrower one's label
            # lies between the two; the outer one's stub grows to make room (the heat pump's rod and hot-water power).
            grows = f"path:{outer.source_flow}"
        return LabelSpec(f"link:{connector_link}", block, tuple(candidates), (), width, False, grows)

    @staticmethod
    def outer_sibling(parameters: Parameters, connector: ConnectorParams) -> Optional[ConnectorParams]:
        """Return the parallel connector between the same two nodes that is routed before this one (a wider one), else None.

        Connectors are routed widest first, so of two seconds into one child the wider runs outside the narrower.
        Example: the heat pump's hot-water electricity (26 kWh) runs outside its heating rod's electricity (1.6 kWh).
        """
        if not connector.parallel:
            return None
        ends = (connector.source_flow.split("|", 1)[0], connector.target_flow.split("|", 1)[0])
        siblings = [
            c for c in parameters.connectors if c.parallel and c.link_id != connector.link_id and (c.source_flow.split("|", 1)[0], c.target_flow.split("|", 1)[0]) == ends
        ]
        position = {c.link_id: k for k, c in enumerate(parameters.connectors)}
        earlier = [c for c in siblings if position[c.link_id] < position[connector.link_id]]
        return earlier[0] if earlier else None

    @staticmethod
    def stub_length(diagram: DiagramParams, flow: FlowParams) -> float:
        """Return the drawn length of a stub: the library aligns the tips of all tail inputs and of all head outputs.

        A head output (a tail input) is drawn as long as the longest head output (tail input) of its sub-diagram, so
        a second input from the same meter runs the whole way beside the tree connection; a lateral stub is drawn
        with its own path length.
        """
        if flow.orientation != 0:
            return flow.pathlength
        same = [f.pathlength for f in diagram.flows if f.orientation == 0 and (f.drawn_kwh > 0) == (flow.drawn_kwh > 0)]
        return max(same, default=flow.pathlength)

    @staticmethod
    def with_stubs(parameters: Parameters, params: Any, route: Route, drawn: Mapping[str, DrawnDiagram]) -> List[Point]:
        """Return a connector's route extended back along both stubs to their trunks, for its label's candidates."""
        source, target = params.source_flow.split("|", 1)[0], params.target_flow.split("|", 1)[0]
        source_diagram, target_diagram = parameters.diagram(source), parameters.diagram(target)
        d_source = np.array(drawn[source].direction(params.source_flow).vector)
        d_target = np.array(drawn[target].direction(params.target_flow).vector)
        first = np.array(route.points[0]) - d_source * FigureTexts.stub_length(source_diagram, source_diagram.flow(params.source_flow))
        last = np.array(route.points[-1]) + d_target * FigureTexts.stub_length(target_diagram, target_diagram.flow(params.target_flow))
        points = [(float(first[0]), float(first[1])), *route.points, (float(last[0]), float(last[1]))]
        return Router.simplify(points)

    @staticmethod
    def inside(candidate: Candidate, route: Route) -> bool:
        """Return whether a candidate sits on a route's centre line (an inside candidate, which a connector does not use)."""
        cx, cy = candidate.box.centre
        return any(abs(cy - y_a) < 1e-9 and min(x_a, x_b) <= cx <= max(x_a, x_b) for (x_a, y_a), (x_b, y_b) in zip(route.points, route.points[1:]))

    @staticmethod
    def title_spec(context: TextContext, diagram: DiagramParams, drawn: DrawnDiagram, trunk: Box, title_block: TextBlock) -> LabelSpec:
        """Return the label of a title block: at the trunk's centre (plus a pinned offset), else beside the trunk.

        As in the hand figures, the title lines sit just above the centre line and the metric lines just below it; a
        block without metric lines is centred.
        """
        size = context.measure.size(title_block)
        line_height = size[1] / max(1, len(title_block.lines))
        metric_lines = len(title_block.lines) - title_block.bold_lines
        lift = FigureTexts.TITLE_LIFT + title_block.bold_lines * line_height - size[1] / 2 if metric_lines else 0.0
        centre = (drawn.centre[0] + diagram.title_offset[0], drawn.centre[1] + diagram.title_offset[1] + lift)
        candidates = Candidates.title(centre, trunk, size, context.constants.label_gap)
        if diagram.title_offset != (0.0, 0.0):
            candidates = candidates[:1]
        return LabelSpec(f"title:{diagram.component}", title_block, tuple(candidates), (f"diagram:{diagram.component}",), 1e9, False, f"trunk:{diagram.component}")

    @staticmethod
    def trunk_box(diagram: DiagramParams, drawn: DrawnDiagram, kwh_per_unit: float) -> Box:
        """Return the box of a sub-diagram's trunk: its length along its axis, its inputs' width across, around its centre."""
        across = sum(f.drawn_kwh for f in diagram.flows if f.drawn_kwh > 0) / kwh_per_unit
        half_along, half_across = diagram.trunklength / 2, across / 2
        cx, cy = drawn.centre
        axis = drawn.direction(diagram.connect) if diagram.connect is not None else diagram.axis
        if axis.is_horizontal:
            return Box(cx - half_along, cy - half_across, cx + half_along, cy + half_across)
        return Box(cx - half_across, cy - half_along, cx + half_across, cy + half_along)

    @staticmethod
    def caption(graph: DrawnGraph, inputs: SankeyInputs, kwh_per_unit: float, inflated: bool, constants: LayoutConstants) -> List[str]:
        """Return the caption: data source, period, scale, PV note, balance verdict, the † note and the key results."""
        days = inputs.run_hours / 24
        scale = Metrics.format_kwh(kwh_per_unit) if kwh_per_unit >= 10 else f"{kwh_per_unit:g} kWh"
        first = (
            f"Energy over the run ({days:.0f} days at {inputs.seconds_per_timestep} s steps) from the run's energy balance, overall view. "
            f"Band width: 1 unit = {scale}. Arrows show direction; no signs. "
            "A PV system is drawn from its AC output (its sunlight split stays out). "
            f"Balance verdict of the run: {inputs.verdict or 'unknown'}."
        )
        if inflated:
            first += f" † drawn at the minimum width of {constants.min_band_px:g} px, not to scale."
        lines = [first]
        results = Metrics.results_line(graph, inputs)
        if results:
            lines.append(results)
        return lines

    @staticmethod
    def label_specs(context: TextContext, parameters: Parameters, drawn: Mapping[str, DrawnDiagram], routes: Mapping[int, Route]) -> List[LabelSpec]:
        """Return the label of every title, terminal flow (but the cut), tree connection and routed connector."""
        specs: List[LabelSpec] = []
        kwh = parameters.kwh_per_unit
        for diagram in parameters.diagrams:
            block = FigureTexts.title_block(context.graph, diagram.component, diagram.title, context.inputs, context.constants)
            specs.append(FigureTexts.title_spec(context, diagram, drawn[diagram.component], FigureTexts.trunk_box(diagram, drawn[diagram.component], kwh), block))
            kinds: Dict[str, FlowKind] = {f.flow_id: f.kind for f in context.graph.node(diagram.component).flows}
            for params in diagram.flows:
                if kinds.get(params.flow_id) not in (FlowKind.LINK, FlowKind.CUT):
                    specs.append(FigureTexts.terminal_spec(context, drawn[diagram.component], params, kwh))
            connection = FigureTexts.connection_spec(context, parameters, diagram, drawn)
            if connection is not None:
                specs.append(connection)
        for link_id, route in sorted(routes.items()):
            if route.shape != "failed":
                specs.append(FigureTexts.connector_spec(context, parameters, link_id, route, drawn))
            else:
                connector = next(c for c in parameters.connectors if c.link_id == link_id)
                source = parameters.diagram(connector.source_flow.split("|", 1)[0])
                specs.append(FigureTexts.terminal_spec(context, drawn[source.component], source.flow(connector.source_flow), kwh))
        return specs
