"""The placement search of the Sankey figure: build the library's diagram, check it, repair one move per finding.

The library places every connected sub-diagram implicitly (a child's connecting tip on its parent's), so placement is a
search over the parameters: build the diagram from the parameters, move the further trees, route the connectors,
place the labels, check for overlaps; each finding gets one deterministic repair move (a longer branch, a longer tree
connection, a further tree slid away, a longer trunk), and the next round builds again. At most ``max_rounds`` rounds;
a round that changes nothing, or the last round, ends the search with the findings as faults. The parameters of a
complete pin file are drawn in one round without repair moves (``PlacementSearch.draw``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hisim.postprocessing.energy_balance.figure.connectors import Bands, Obstacle, Port, Route, RouteRequest, Router
from hisim.postprocessing.energy_balance.figure.drawing import DrawnDiagram, SankeyBuild, SankeyBuildError
from hisim.postprocessing.energy_balance.figure.geometry import Box, Direction
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, FlowKind, GraphBuilder, Role
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs
from hisim.postprocessing.energy_balance.figure.labels import LabelPlacer, Overlap, OverlapCheck, PlacedLabel, Shape, TextBlock, TextMeasure
from hisim.postprocessing.energy_balance.figure.parameters import Adjustments, LayoutConstants, ParameterBuilder, Parameters, TextSizes
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle
from hisim.postprocessing.energy_balance.figure.texts import FigureTexts, TextContext


@dataclass(frozen=True)
class Fault:
    """A layout fault: its kind and a sentence that says what failed."""

    kind: str
    message: str


@dataclass
class Drawing:
    """One round's drawing: the figure and axes, the drawn sub-diagrams, routes, connector outlines and labels.

    ``blind`` lists the connectors that found a route only by ignoring the label spots; the repair treats them like
    failed routes (it slides a further tree to open a corridor).
    """

    figure: Any
    axes: Any
    drawn: Dict[str, DrawnDiagram]
    routes: Dict[int, Route] = field(default_factory=dict)
    connector_outlines: Dict[int, np.ndarray] = field(default_factory=dict)
    blind: List[int] = field(default_factory=list)
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
    def slide_direction(graph: DrawnGraph, root: str) -> Direction:
        """Return the direction a further tree slides away from the chain in: up for a collector, down for the rest."""
        return Direction.N if graph.roles[root] is Role.SOLAR_COLLECTOR else Direction.S

    @staticmethod
    def place(graph: DrawnGraph, parameters: Parameters, drawn: Dict[str, DrawnDiagram], adjustments: Adjustments, constants: LayoutConstants) -> None:
        """Move every further tree to its quadrant (or its pinned ``at``) and record ``at`` in the parameters.

        A collector goes above and left of the node its heat feeds; any other tree below everything placed so far, its
        left edge at the chain root's; then the search's accumulated shift is added.
        """
        if parameters.pinned:
            TreePlacement.place_pinned_trees(parameters, drawn)
            return
        trees = TreePlacement.members(parameters)
        placed = TreePlacement.box_of(drawn, trees[0]) if trees else None
        chain_root = graph.chain[0] if graph.chain and graph.chain[0] in drawn else (trees[0][0] if trees else "")
        for tree in trees[1:]:
            root = parameters.diagram(tree[0])
            own = TreePlacement.box_of(drawn, tree)
            if root.at is not None:
                dx, dy = root.at[0] - drawn[tree[0]].centre[0], root.at[1] - drawn[tree[0]].centre[1]
            else:
                dx, dy = TreePlacement.quadrant_shift(graph, tree[0], own, placed, drawn, chain_root, constants)
                shift = adjustments.tree_shift.get(tree[0], (0.0, 0.0))
                dx, dy = dx + shift[0], dy + shift[1]
            for name in tree:
                drawn[name].move(dx, dy)
            root.at = drawn[tree[0]].centre
            moved = own.shifted(dx, dy)
            placed = moved if placed is None else placed.union(moved)

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

    @staticmethod
    def quadrant_shift(
        graph: DrawnGraph, root: str, own: Box, placed: Optional[Box], drawn: Mapping[str, DrawnDiagram], chain_root: str, constants: LayoutConstants
    ) -> Tuple[float, float]:
        """Return the shift that puts a further tree at the nearest position of its quadrant."""
        if placed is None:
            return 0.0, 0.0
        if graph.roles[root] is Role.SOLAR_COLLECTOR:
            outputs = graph.node(root).flows_where(is_input=False, kind=FlowKind.LINK)
            target = max(outputs, key=lambda f: (f.magnitude_kwh, f.other_end)).other_end if outputs else ""
            if target in drawn:
                box = TreePlacement.box_of(drawn, [target])
                # A collector that carries the tank it feeds (a summer week) leaves room under the tank for the
                # connector that brings the generator's heat into the tank's south side.
                own_tree = next((t for t in graph.trees if t.root == root), None)
                carried = own_tree is not None and any(target == edge.placed for edge in own_tree.edges)
                room = constants.min_connection if carried else 0.0
                return box.x0 - 2 * constants.min_connection - own.x1, box.y1 + constants.tree_margin + room - own.y0
            return placed.x0 - own.x0, placed.y1 + constants.tree_margin - own.y0
        anchor = TreePlacement.box_of(drawn, [chain_root]).x0 if chain_root in drawn else placed.x0
        return anchor - own.x0, placed.y0 - constants.tree_margin - own.y1


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
    def route_all(parameters: Parameters, drawing: Drawing, constants: LayoutConstants, previous_labels: Sequence[PlacedLabel] = ()) -> None:
        """Route every connector, the widest first, around the patches, the connectors placed and the given labels.

        The labels are the last round's and the reserved spots of the terminal labels (§6.7 rule 7). A pinned connector
        takes its turns. A routed connector cuts its source stub's arrowhead flat and fills its target stub's notch (a
        parallel one keeps the notch and enters it with an arrowhead); a failed one leaves both. A connector that
        finds a route only by ignoring the labels is listed in ``drawing.blind``, so the repair opens a corridor.
        """
        obstacles = [Obstacle(f"diagram:{name}", polygon, (name,)) for name, picture in drawing.drawn.items() for polygon in SankeyBuild.polygons(picture.patch)]
        stubs = {f"flow:{c.source_flow}" for c in parameters.connectors} | {f"flow:{c.target_flow}" for c in parameters.connectors}
        stubs |= {f"reserve:{c.source_flow}" for c in parameters.connectors} | {f"reserve:{c.target_flow}" for c in parameters.connectors}
        for label in previous_labels:
            if label.key not in stubs:  # a failed connector's stub label must not block the next round's route
                obstacles.append(Obstacle.of_box(label.key, label.box))
        bounds = Box(*SankeyBuild.extent([o.polygon for o in obstacles])) if obstacles else Box(0, 0, 1, 1)
        bounds = bounds.inflated(3.0)
        for connector in parameters.connectors:
            source, target = connector.source_flow.split("|", 1)[0], connector.target_flow.split("|", 1)[0]
            start, end, width = Connecting.ports(parameters, drawing.drawn, connector.link_id, constants)
            reach = (constants.shoulder + width / 2) * constants.pitch + constants.connector_overlap
            request = RouteRequest(connector.link_id, start, end, width, (source, target), reach)
            # Its own labels, and the titles of its two nodes: a hairline node's title sits on the stub the route
            # leaves from (a summer week's boiler), and the overlap check still sees a route that crosses it.
            own_labels = (f"link:{connector.link_id}", f"flow:{connector.source_flow}", f"flow:{connector.target_flow}", f"title:{source}", f"title:{target}")
            usable = [o for o in obstacles if o.key not in own_labels]
            if connector.turns is not None:
                try:
                    points = Router.route_points(start, end, connector.turns)
                    radii = Router.corner_radii(points, max(constants.connector_radius, width / 2 + 0.1), constants.min_radius) or tuple(0.0 for _ in points[2:])
                    route = Route(connector.link_id, tuple(points), width, "pinned", radii)
                except ValueError:
                    route = Route(connector.link_id, (start.point, end.point), width, "failed", ())
            else:
                route = Router.route(request, usable, bounds, constants)
                if route.shape == "failed":
                    # Labels can move and routes cannot: without a way around the label spots, the route ignores them,
                    # the labels make room in this round's placement, and the repair opens a corridor.
                    route = Router.route(request, [o for o in usable if o.key.startswith(("diagram:", "connector:"))], bounds, constants)
                    drawing.blind.append(connector.link_id)
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


class Repair:
    """One deterministic repair move per finding."""

    @staticmethod
    def chain_to_root(parameters: Parameters, name: str) -> List[str]:
        """Return a sub-diagram and its priors up to its tree's root, nearest first."""
        path = [name]
        while parameters.diagram(path[-1]).prior is not None:
            path.append(str(parameters.diagram(path[-1]).prior))
        return path

    @staticmethod
    def projection(box: Box, direction: Direction) -> Tuple[float, float]:
        """Return the range a box covers along a direction (its lowest and highest coordinate on that axis)."""
        dx, dy = direction.vector
        values = sorted((box.x0 * dx + box.y0 * dy, box.x1 * dx + box.y1 * dy))
        return values[0], values[1]

    @staticmethod
    def clearing_distance(fixed: Box, moving: Box, direction: Direction, margin: float) -> float:
        """Return how far ``moving`` must travel along ``direction`` to clear ``fixed`` by ``margin`` (at least 0).

        Example: a box spanning y 0..2 moving south past one spanning y -1..1 needs 2 + margin.
        """
        _, fixed_high = Repair.projection(fixed, direction)
        moving_low, _ = Repair.projection(moving, direction)
        return max(0.0, fixed_high - moving_low + margin)

    @staticmethod
    def band_move(
        graph: DrawnGraph, parameters: Parameters, drawn: Mapping[str, DrawnDiagram], pair: Tuple[str, str], adjustments: Adjustments, constants: LayoutConstants
    ) -> None:
        """Separate two sub-diagrams whose bands overlap.

        Of the two, the later one in the build order moves. In another tree than the earlier one, its tree slides away
        from the chain until clear; in the same tree, the tree connection of its branch below the common ancestor is
        lengthened until its subtree clears the other by ``node_margin`` (siblings are staggered in depth).
        """
        first, second = sorted(pair, key=parameters.position)
        path_first, path_second = Repair.chain_to_root(parameters, first), Repair.chain_to_root(parameters, second)
        box_first = TreePlacement.box_of(drawn, [first])
        if path_first[-1] != path_second[-1]:
            root = path_second[-1]
            direction = TreePlacement.slide_direction(graph, root)
            distance = Repair.clearing_distance(box_first, TreePlacement.box_of(drawn, [second]), direction, constants.node_margin)
            adjustments.shift(root, direction.vector[0] * max(distance, constants.slide), direction.vector[1] * max(distance, constants.slide))
            return
        common = next(name for name in path_second if name in path_first)
        branch = path_second[path_second.index(common) - 1] if path_second.index(common) > 0 else second
        child = parameters.diagram(branch)
        if child.prior is None or child.prior_flow is None:
            return
        direction = drawn[child.prior].direction(child.prior_flow)
        subtree = [d.component for d in parameters.diagrams if branch in Repair.chain_to_root(parameters, d.component)]
        distance = Repair.clearing_distance(box_first, TreePlacement.box_of(drawn, subtree), direction, constants.node_margin)
        Adjustments.add(adjustments.pathlength, child.prior_flow, max(distance, constants.grow))

    @staticmethod
    def cornered(parameters: Parameters, drawing: Drawing, link_id: int) -> bool:
        """Whether a connector found its route only by ignoring the labels and no tree slide can open a corridor for it.

        Both its ends are then in the first tree (the chain's), which never slides; the label on it must move instead:
        its branch grows past the connector. Example: in a summer week the household's body heat runs to the building
        right beside the household's appliance loss, whose label lies on that route.
        """
        if link_id not in drawing.blind:
            return False
        connector = next(c for c in parameters.connectors if c.link_id == link_id)
        first = TreePlacement.members(parameters)[0]
        return all(flow_id.split("|", 1)[0] in first for flow_id in (connector.source_flow, connector.target_flow))

    @staticmethod
    def apply(graph: DrawnGraph, parameters: Parameters, drawing: Drawing, findings: Sequence[Overlap], adjustments: Adjustments, constants: LayoutConstants) -> None:
        """Apply one move per finding, in the findings' order; a label or tree moves once per round."""
        moved: set = set()
        labels = {label.key: label for label in drawing.labels}
        for finding in findings:
            if finding.kind == "band/band":
                pair = (finding.first.split(":", 1)[1], finding.second.split(":", 1)[1])
                if frozenset(pair) not in moved:
                    moved.add(frozenset(pair))
                    Repair.band_move(graph, parameters, drawing.drawn, pair, adjustments, constants)
                continue
            if (
                finding.kind == "label/band"
                and finding.second.startswith("connector:")
                and not Repair.cornered(parameters, drawing, int(finding.second.split(":", 1)[1]))
            ):
                continue  # the next round routes the connector around this round's labels
            for key in (finding.first, finding.second) if finding.kind == "label/label" else (finding.first,):
                label = labels.get(key)
                if label is None or not label.grows or key in moved:
                    continue
                moved.add(key)
                kind, target = label.grows.split(":", 1)
                # A path grows by the label's height at least, so a label squeezed between siblings reaches free room
                # within the bounded rounds; a trunk grows by the step.
                amount = max(constants.grow, label.box.height) if kind == "path" else constants.grow
                Adjustments.add(adjustments.pathlength if kind == "path" else adjustments.trunk, target, amount)
                break
        trees = TreePlacement.members(parameters)
        for link_id, route in sorted(drawing.routes.items()):
            if route.shape != "failed" and link_id not in drawing.blind:
                continue
            connector = next(c for c in parameters.connectors if c.link_id == link_id)
            for flow_id in (connector.source_flow, connector.target_flow):
                tree = next(t for t in trees if flow_id.split("|", 1)[0] in t)
                if tree is not trees[0] and tree[0] not in moved:
                    moved.add(tree[0])
                    direction = TreePlacement.slide_direction(graph, tree[0])
                    adjustments.shift(tree[0], direction.vector[0] * constants.route_slide, direction.vector[1] * constants.route_slide)
                    break


class PlacementSearch:
    """Runs the rounds: build, place, connect, label, check, repair."""

    @staticmethod
    def run(inputs: SankeyInputs, constants: Optional[LayoutConstants] = None, style: Optional[SankeyStyle] = None, pinned: Optional[Parameters] = None) -> Layout:
        """Return the layout of a run's overall view, with every fault found.

        Args:
            inputs: The run's inputs.
            constants: The layout constants (the defaults when omitted).
            style: The presentation table (the one beside the module when omitted).
            pinned: Complete parameters from a pin file; drawn in one round without repair moves.
        """
        constants = constants or LayoutConstants()
        style = style or SankeyStyle.load()
        graph = GraphBuilder.build(inputs)
        measure = TextMeasure(constants)
        styles = {n.name: style.node_style(n.name, n.class_name, graph.roles[n.name]) for n in graph.nodes}
        title = f"{inputs.title}: energy flows over the run"
        if not graph.nodes:
            empty = Parameters(1.0, 0.0, [], [])
            return Layout(title, graph, empty, None, [], [], (0.0, 10.0, 0.0, 5.0))
        context = TextContext(graph, inputs, style, measure, constants)
        sizes = PlacementSearch.text_sizes(context, {name: (s.title, s.color) for name, s in styles.items()})
        adjustments = Adjustments()
        previous: List[PlacedLabel] = []
        layout: Optional[Layout] = None
        for number in range(1, (1 if pinned is not None else constants.max_rounds) + 1):
            parameters = (
                pinned if pinned is not None else ParameterBuilder.build(graph, sizes, adjustments, constants, {n: (s.title, s.color) for n, s in styles.items()})
            )
            layout = PlacementSearch.round(context, parameters, adjustments, previous)
            layout.title, layout.rounds = title, number
            if layout.drawing is None or not layout.overlaps and not layout.faults:
                break
            before = (adjustments.signature(), PlacementSearch.label_signature(previous))
            Repair.apply(graph, parameters, layout.drawing, layout.overlaps, adjustments, constants)
            previous = layout.drawing.labels
            if (adjustments.signature(), PlacementSearch.label_signature(previous)) == before:
                break
        assert layout is not None  # the loop runs at least once
        if layout.overlaps:
            layout.faults.append(Fault("overlap", f"{len(layout.overlaps)} overlaps left" + ("" if pinned is not None else f" after {layout.rounds} rounds")))
        inflated = any(f.inflated for d in layout.parameters.diagrams for f in d.flows)
        layout.caption = FigureTexts.caption(graph, inputs, layout.parameters.kwh_per_unit, inflated, constants)
        return layout

    @staticmethod
    def draw(inputs: SankeyInputs, parameters: Parameters, constants: Optional[LayoutConstants] = None, style: Optional[SankeyStyle] = None) -> Layout:
        """Return the drawing of a run's overall view from complete library parameters (a pin file), in one round."""
        return PlacementSearch.run(inputs, constants, style, pinned=parameters)

    @staticmethod
    def label_signature(labels: Sequence[PlacedLabel]) -> Tuple[Tuple[str, float, float], ...]:
        """Return the labels' keys and positions, rounded: a round that moved no label and no parameter changes nothing."""
        return tuple((label.key, round(label.box.x0, 3), round(label.box.y0, 3)) for label in labels)

    @staticmethod
    def text_sizes(context: TextContext, styles: Mapping[str, Tuple[str, str]]) -> TextSizes:
        """Return the widths of every title block and link label, in u (the trunk and connection lengths need them)."""
        graph, constants = context.graph, context.constants
        titles = {n.name: context.measure.size(FigureTexts.title_block(graph, n.name, styles[n.name][0], context.inputs, constants))[0] for n in graph.nodes}
        links = {}
        for link in graph.links:
            flow = next(f for f in graph.node(link.source).flows if f.link_id == link.link_id)
            name = context.style.flow_name(flow, FigureTexts.owner_class(flow, context.inputs.class_of))
            lines = (*name.split("\n"), "00,000 kWh")
            links[link.link_id] = context.measure.size(TextBlock(lines, constants.font_pt, len(lines)))[0]
        return TextSizes(titles, links)

    @staticmethod
    def round(context: TextContext, parameters: Parameters, adjustments: Optional[Adjustments] = None, previous: Sequence[PlacedLabel] = ()) -> Layout:
        """Build, place, connect, label and check one round; return its layout with the findings.

        ``adjustments`` are the search's accumulated moves (none for a pin file), ``previous`` the last round's labels,
        which the router keeps clear.
        """
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
        TreePlacement.place(graph, parameters, drawn, adjustments or Adjustments(), constants)
        drawing = Drawing(figure, axes, drawn)
        Connecting.flatten_cuts(graph, parameters, drawn, constants)
        Connecting.route_all(parameters, drawing, constants, [*previous, *PlacementSearch.reserved_labels(context, parameters, drawn)])
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
    def reserved_labels(context: TextContext, parameters: Parameters, drawn: Mapping[str, DrawnDiagram]) -> List[PlacedLabel]:
        """Return the preferred spot of every terminal flow's label (beyond its tip), for the router to keep clear.

        The router places connectors before the labels; without these spots a connector may hug a branch so closely
        that none of its label's candidates is free (a pump line routed round the gas supply's arrow tip).
        """
        result = []
        for diagram in parameters.diagrams:
            kinds = {f.flow_id: f.kind for f in context.graph.node(diagram.component).flows}
            for params in diagram.flows:
                if kinds.get(params.flow_id) in (FlowKind.LINK, FlowKind.CUT):
                    continue
                spec = FigureTexts.terminal_spec(context, drawn[diagram.component], params, parameters.kwh_per_unit)
                result.append(PlacedLabel(f"reserve:{params.flow_id}", spec.block, spec.candidates[0].box, "center", False, "", ()))
        return result

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
