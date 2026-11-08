"""One drawing of the Sankey figure from given library parameters: build, place the trees, connect, label, check.

The library places every connected sub-diagram implicitly (a child's connecting tip on its parent's) and puts every
unconnected sub-diagram at the origin. A *round* builds the diagram from the parameters, moves each further tree so
its member with an ``at`` lands there, routes the connectors, places the labels and checks for overlaps; the
findings come back with the drawing. This module draws the parameters of a complete pin file in one round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hisim.postprocessing.energy_balance.figure.connectors import Bands, Obstacle, Port, Route, RouteRequest, Router
from hisim.postprocessing.energy_balance.figure.drawing import DrawnDiagram, SankeyBuild, SankeyBuildError
from hisim.postprocessing.energy_balance.figure.geometry import Box
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, FlowKind, GraphBuilder
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs
from hisim.postprocessing.energy_balance.figure.labels import LabelPlacer, Overlap, OverlapCheck, PlacedLabel, Shape, TextMeasure
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants, Parameters
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle
from hisim.postprocessing.energy_balance.figure.texts import FigureTexts, TextContext


@dataclass(frozen=True)
class Fault:
    """A layout fault: its kind and a sentence that says what failed."""

    kind: str
    message: str


@dataclass
class Drawing:
    """One round's drawing: the figure and axes, the drawn sub-diagrams, routes, connector outlines and labels."""

    figure: Any
    axes: Any
    drawn: Dict[str, DrawnDiagram]
    routes: Dict[int, Route] = field(default_factory=dict)
    connector_outlines: Dict[int, np.ndarray] = field(default_factory=dict)
    labels: List[PlacedLabel] = field(default_factory=list)
    shapes: List[Shape] = field(default_factory=list)


@dataclass
class Layout:
    """The result of the search: the parameters, the last round's drawing, its findings and faults.

    Attributes:
        title: The figure's title.
        graph: The drawn graph.
        parameters: The library's parameters of the last round.
        drawing: The last round's drawing (None when there is nothing to draw).
        overlaps: The findings left after the last round.
        faults: The faults (the findings left included).
        canvas: The axes limits (x0, x1, y0, y1), in u.
        caption: The caption lines.
        rounds: How many rounds the search ran.
    """

    title: str
    graph: DrawnGraph
    parameters: Parameters
    drawing: Optional[Drawing]
    overlaps: List[Overlap]
    faults: List[Fault]
    canvas: Tuple[float, float, float, float]
    caption: List[str] = field(default_factory=list)
    rounds: int = 0

    @property
    def nothing_to_draw(self) -> bool:
        """Whether the view has no drawn node (a run that declares no energy port); such a run gets no figure."""
        return not self.graph.nodes


class TreePlacement:
    """Moves the further trees of a drawing to their position (the library puts unconnected sub-diagrams at the origin)."""

    @staticmethod
    def members(parameters: Parameters) -> List[List[str]]:
        """Return the sub-diagrams of each tree, in order: a root starts a tree, every child follows its prior's."""
        trees: List[List[str]] = []
        for diagram in parameters.diagrams:
            if diagram.prior is None:
                trees.append([diagram.component])
            else:
                next(tree for tree in trees if diagram.prior in tree).append(diagram.component)
        return trees

    @staticmethod
    def box_of(drawn: Mapping[str, DrawnDiagram], names: Sequence[str]) -> Box:
        """Return the box around the drawn patches of some sub-diagrams."""
        x0, y0, x1, y1 = SankeyBuild.extent([polygon for name in names for polygon in SankeyBuild.polygons(drawn[name].patch)])
        return Box(x0, y0, x1, y1)

    @staticmethod
    def place_pinned(parameters: Parameters, drawn: Dict[str, DrawnDiagram], tree: Sequence[str]) -> None:
        """Move a pinned tree so the trunk centre of its member with an ``at`` lands there (the prototype's ``place_trees``)."""
        anchor = next((name for name in tree if parameters.diagram(name).at is not None), None)
        if anchor is None:
            return
        at = parameters.diagram(anchor).at
        assert at is not None  # found by its at
        dx, dy = at[0] - drawn[anchor].centre[0], at[1] - drawn[anchor].centre[1]
        for name in tree:
            drawn[name].move(dx, dy)

    @staticmethod
    def place_pinned_trees(parameters: Parameters, drawn: Dict[str, DrawnDiagram]) -> None:
        """Move every tree of a drawing whose member carries an ``at`` so that member's trunk centre lands there."""
        for tree in TreePlacement.members(parameters):
            TreePlacement.place_pinned(parameters, drawn, tree)


class Connecting:
    """Flattens the solar cut and the parallel seams, and routes and draws the connectors of a drawing."""

    @staticmethod
    def flatten_cuts(graph: DrawnGraph, parameters: Parameters, drawn: Mapping[str, DrawnDiagram], constants: LayoutConstants) -> None:
        """Fill the notch of every solar cut, so the PV's trunk starts with a flat tail."""
        for diagram in parameters.diagrams:
            kinds = {f.flow_id: f.kind for f in graph.node(diagram.component).flows}
            for params in diagram.flows:
                if kinds.get(params.flow_id) is FlowKind.CUT:
                    picture = drawn[diagram.component]
                    width = abs(params.drawn_kwh) / parameters.kwh_per_unit
                    SankeyBuild.flatten_input_notch(picture.patch, picture.tip(params.flow_id), picture.direction(params.flow_id), width, constants.pitch)

    @staticmethod
    def flatten_parallel(parameters: Parameters, drawn: Mapping[str, DrawnDiagram], routes: Mapping[int, Route], constants: LayoutConstants) -> None:
        """Make a tree connection meet its child square where a routed connector runs beside it into the same child.

        A generator's second input from the same meter is a connector right under the tree connection; the
        connection's arrowhead is cut flat and the child's notch moved back by the shoulder, so the two bands enter
        side by side with one seam.
        """
        for connector in parameters.connectors:
            if connector.parallel or routes.get(connector.link_id) is None or routes[connector.link_id].shape == "failed":
                continue
            source, target = connector.source_flow.split("|", 1)[0], connector.target_flow.split("|", 1)[0]
            child = parameters.diagram(target)
            if child.prior != source or child.prior_flow is None or child.connect is None:
                continue
            out = parameters.diagram(source).flow(child.prior_flow)
            width = abs(out.drawn_kwh) / parameters.kwh_per_unit
            direction = drawn[source].direction(child.prior_flow)
            SankeyBuild.flatten_output_head(drawn[source].patch, drawn[source].tip(child.prior_flow), direction, width, constants)
            SankeyBuild.flatten_input_notch(
                drawn[target].patch, drawn[target].tip(child.connect), direction, width, constants.pitch, back=constants.shoulder * constants.pitch
            )

    @staticmethod
    def ports(parameters: Parameters, drawn: Mapping[str, DrawnDiagram], link_id: int, constants: LayoutConstants) -> Tuple[Port, Port, float]:
        """Return the two ports of a connector, the flat ends of its stubs reaching into them by the overlap, and its width."""
        connector = next(c for c in parameters.connectors if c.link_id == link_id)
        source, target = connector.source_flow.split("|", 1)[0], connector.target_flow.split("|", 1)[0]
        width = abs(parameters.diagram(source).flow(connector.source_flow).drawn_kwh) / parameters.kwh_per_unit
        d_source, d_target = drawn[source].direction(connector.source_flow), drawn[target].direction(connector.target_flow)
        start = drawn[source].tip(connector.source_flow) - np.array(d_source.vector) * ((constants.shoulder + width / 2) * constants.pitch + constants.connector_overlap)
        # A parallel connector stops at the notch's outer edge and enters the notch with an arrowhead of its own.
        overlap = 0.0 if connector.parallel else constants.connector_overlap
        end = drawn[target].tip(connector.target_flow) - np.array(d_target.vector) * (width / 2 * constants.pitch - overlap)
        return Port((float(start[0]), float(start[1])), d_source), Port((float(end[0]), float(end[1])), d_target), width

    @staticmethod
    def route_all(parameters: Parameters, drawing: Drawing, constants: LayoutConstants) -> None:
        """Route every connector, the widest first, around the patches and the connectors placed before it.

        A pinned connector takes its turns. A routed connector cuts its source stub's arrowhead flat and fills its
        target stub's notch (a parallel one keeps the notch and enters it with an arrowhead); a failed one leaves both.
        """
        obstacles = [Obstacle(f"diagram:{name}", polygon, (name,)) for name, picture in drawing.drawn.items() for polygon in SankeyBuild.polygons(picture.patch)]
        bounds = Box(*SankeyBuild.extent([o.polygon for o in obstacles])) if obstacles else Box(0, 0, 1, 1)
        bounds = bounds.inflated(3.0)
        for connector in parameters.connectors:
            source, target = connector.source_flow.split("|", 1)[0], connector.target_flow.split("|", 1)[0]
            start, end, width = Connecting.ports(parameters, drawing.drawn, connector.link_id, constants)
            reach = (constants.shoulder + width / 2) * constants.pitch + constants.connector_overlap
            request = RouteRequest(connector.link_id, start, end, width, (source, target), reach)
            if connector.turns is not None:
                try:
                    points = Router.route_points(start, end, connector.turns)
                    radii = Router.corner_radii(points, max(constants.connector_radius, width / 2 + 0.1), constants.min_radius) or tuple(0.0 for _ in points[2:])
                    route = Route(connector.link_id, tuple(points), width, "pinned", radii)
                except ValueError:
                    route = Route(connector.link_id, (start.point, end.point), width, "failed", ())
            else:
                route = Router.route(request, obstacles, bounds, constants)
            drawing.routes[connector.link_id] = route
            if route.shape == "failed":
                continue
            line, tangents = Bands.rounded_centre_line(route.points, route.radii)
            outline, _, _ = Bands.outline(line, tangents, width)
            drawing.connector_outlines[connector.link_id] = outline
            obstacles.append(Obstacle(f"connector:{connector.link_id}", outline, (source, target)))
            SankeyBuild.flatten_output_head(drawing.drawn[source].patch, drawing.drawn[source].tip(connector.source_flow), start.direction, width, constants)
            if not connector.parallel:
                SankeyBuild.flatten_input_notch(drawing.drawn[target].patch, drawing.drawn[target].tip(connector.target_flow), end.direction, width, constants.pitch)


class PlacementSearch:
    """Draws one round from given library parameters and checks it."""

    @staticmethod
    def draw(inputs: SankeyInputs, parameters: Parameters, constants: Optional[LayoutConstants] = None, style: Optional[SankeyStyle] = None) -> Layout:
        """Return the drawing of a run's overall view from complete library parameters (a pin file), with its faults.

        Args:
            inputs: The run's inputs.
            parameters: The library parameters of every sub-diagram and connector.
            constants: The layout constants (the defaults when omitted).
            style: The presentation table (the one beside the module when omitted).
        """
        constants = constants or LayoutConstants()
        style = style or SankeyStyle.load()
        graph = GraphBuilder.build(inputs)
        title = f"{inputs.title}: energy flows over the run"
        if not graph.nodes:
            return Layout(title, graph, Parameters(1.0, 0.0, [], []), None, [], [], (0.0, 10.0, 0.0, 5.0))
        layout = PlacementSearch.round(TextContext(graph, inputs, style, TextMeasure(constants), constants), parameters)
        layout.title, layout.rounds = title, 1
        if layout.overlaps:
            layout.faults.append(Fault("overlap", f"{len(layout.overlaps)} overlaps left"))
        inflated = any(f.inflated for d in layout.parameters.diagrams for f in d.flows)
        layout.caption = FigureTexts.caption(graph, inputs, layout.parameters.kwh_per_unit, inflated, constants)
        return layout

    @staticmethod
    def round(context: TextContext, parameters: Parameters) -> Layout:
        """Build, place, connect, label and check one round; return its layout with the findings."""
        graph, constants = context.graph, context.constants
        figure = Figure(figsize=(10, 10), dpi=constants.dpi)
        FigureCanvasAgg(figure)
        axes = figure.add_axes((0.0, 0.0, 1.0, 1.0))
        axes.set_aspect("equal")
        axes.axis("off")
        try:
            drawn = SankeyBuild.build(parameters, constants, axes)
        except SankeyBuildError as error:
            return Layout("", graph, parameters, None, [], [Fault("library", str(error))], (0.0, 10.0, 0.0, 5.0))
        TreePlacement.place_pinned_trees(parameters, drawn)
        drawing = Drawing(figure, axes, drawn)
        Connecting.flatten_cuts(graph, parameters, drawn, constants)
        Connecting.route_all(parameters, drawing, constants)
        Connecting.flatten_parallel(parameters, drawn, drawing.routes, constants)
        patches = [Shape.of(f"diagram:{name}", polygon, (name,)) for name, picture in drawn.items() for polygon in SankeyBuild.polygons(picture.patch)]
        connectors = [Shape.of(f"connector:{k}", outline, ()) for k, outline in sorted(drawing.connector_outlines.items())]
        drawing.shapes = patches + connectors
        drawing.labels = LabelPlacer.place(FigureTexts.label_specs(context, parameters, drawn, drawing.routes), drawing.shapes)
        connected = [(d.component, d.prior) for d in parameters.diagrams if d.prior is not None]
        findings = OverlapCheck.labels(drawing.labels, drawing.shapes) + OverlapCheck.bands(patches, connected)
        findings += OverlapCheck.connectors(PlacementSearch.trimmed_connectors(drawing, constants), patches + connectors)
        findings.sort(key=lambda o: (o.kind, o.first, o.second))
        faults = [Fault("route", f"no clear route for link {graph.links[k].label!r}") for k, r in sorted(drawing.routes.items()) if r.shape == "failed"]
        return Layout("", graph, parameters, drawing, findings, faults, PlacementSearch.canvas(drawing, parameters, constants))

    @staticmethod
    def trimmed_connectors(drawing: Drawing, constants: LayoutConstants) -> List[Shape]:
        """Return every routed connector's outline narrowed and with its ends trimmed, for the crossing check."""
        result = []
        for link_id, route in sorted(drawing.routes.items()):
            if route.shape == "failed":
                continue
            line, tangents = Bands.rounded_centre_line(route.points, route.radii)
            lengths = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
            keep = (lengths > Router.END_TRIM + constants.connector_overlap) & (lengths < lengths[-1] - Router.END_TRIM - constants.connector_overlap)
            if keep.sum() >= 2:
                outline, _, _ = Bands.outline(line[keep], tangents[keep], route.width * Router.OWN_WIDTH_SHARE)
                result.append(Shape.of(f"connector:{link_id}", outline, ()))
        return result

    @staticmethod
    def canvas(drawing: Drawing, parameters: Parameters, constants: LayoutConstants) -> Tuple[float, float, float, float]:
        """Return the axes limits: a pin file's, else the box around every patch, connector and label plus the margin."""
        if parameters.canvas is not None:
            return parameters.canvas
        boxes = [shape.box for shape in drawing.shapes] + [label.box for label in drawing.labels]
        if not boxes:
            return 0.0, 10.0, 0.0, 5.0
        box = boxes[0]
        for other in boxes[1:]:
            box = box.union(other)
        box = box.inflated(constants.canvas_margin)
        return box.x0, box.x1, box.y0, box.y1
