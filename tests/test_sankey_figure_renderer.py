"""Direct tests of the Sankey figure's renderer.

Covered: geometry, parameters, drawing, connectors, labels, texts, the one-round drawing, the files, the pin file,
``draw_overall``, the redraw command and the ``PLOT_SANKEY`` wiring.

The small cases are built by hand; the full cases are the synthetic heat pump house and the boiler fed twice of
``tests/sankey_test_systems.py``, drawn from their hand-written pin files (no recorded run is committed, and the
automatic layout comes in Sankey PR 5).
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, cast

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hisim import cli
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.executor import SimulationParametersReader
from hisim.postprocessing.energy_balance.figure import FigureFiles, SankeyInputs, draw_overall
from hisim.postprocessing.energy_balance.figure.connectors import Bands, GridSearch, Obstacle, Port, Route, RouteRequest, Router
from hisim.postprocessing.energy_balance.figure.drawing import SankeyBuild, SankeyBuildError
from hisim.postprocessing.energy_balance.figure.geometry import Box, Direction, Polygons, Side
from hisim.postprocessing.energy_balance.figure.graph import FlowKind
from hisim.postprocessing.energy_balance.figure.labels import (
    Candidate,
    Candidates,
    LabelPlacer,
    LabelSpec,
    Overlap,
    OverlapCheck,
    PlacedLabel,
    Shape,
    TextBlock,
    TextMeasure,
)
from hisim.postprocessing.energy_balance.figure.parameters import ConnectorParams, DiagramParams, FlowParams, LabelPin, LayoutConstants, Parameters, Scale
from hisim.postprocessing.energy_balance.figure.pins import PinError, PinFile
from hisim.postprocessing.energy_balance.figure.render import LayoutFile, SankeyFigure
from hisim.postprocessing.energy_balance.figure.search import Connecting, Drawing, Fault, Layout, PlacementSearch, TreePlacement
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle
from hisim.postprocessing.energy_balance.figure.texts import FigureTexts, TextContext
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.postprocessingoptions import PostProcessingOptions
from tests.sankey_test_systems import SankeySystems

pytestmark = pytest.mark.base

CONSTANTS = LayoutConstants()


@pytest.fixture(name="layout", scope="module")
def fixture_layout(tmp_path_factory: pytest.TempPathFactory) -> Layout:
    """The heat pump house drawn from its hand-written pin file."""
    pins = SankeySystems.pin_file(SankeySystems.heat_pump_house_pins(), tmp_path_factory.mktemp("house"))
    return PinFile.draw(SankeySystems.heat_pump_house(), pins, CONSTANTS)


@pytest.fixture(name="context", scope="module")
def fixture_context(layout: Layout) -> TextContext:
    """The text context of the house."""
    return TextContext(layout.graph, SankeySystems.heat_pump_house(), SankeyStyle.load(), TextMeasure(CONSTANTS), CONSTANTS)


def new_axes() -> Any:
    """Return fresh axes on an Agg figure."""
    figure = Figure()
    FigureCanvasAgg(figure)
    return figure.add_subplot()


def flow(flow_id: str, value: float, orientation: int, side: Side = Side.TAIL) -> FlowParams:
    """Return a flow of the given value and orientation with a 1 u path."""
    return FlowParams(flow_id, value, value, orientation, 1.0, side)


def two_flow_diagram() -> DiagramParams:
    """Return a one-node sub-diagram N with an input a and an output b of 1 kWh at 1 kWh/u."""
    return DiagramParams("N", None, None, None, Direction.E, 0, None, 3.0, (0.0, 0.0), "#888888", "N", [flow("a", 1.0, 0), flow("b", -1.0, 0)])


def square(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Return the four corners of a box as a polygon."""
    return np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def request(start: Port, end: Port, width: float = 0.5, reach: float = 0.0) -> RouteRequest:
    """Return a route request between two ports for the nodes A and B."""
    return RouteRequest(0, start, end, width, ("A", "B"), reach)


# geometry


def test_direction_and_side() -> None:
    """Turning left from east gives north; the right side of an eastward trunk faces south; east is a southward trunk's left."""
    assert Direction.E.turned_left() is Direction.N and Direction.E.turned_right() is Direction.S and Direction.N.opposite() is Direction.S
    assert Direction.W.vector == (-1.0, 0.0) and Direction.W.is_horizontal
    assert Side.RIGHT.outward(Direction.E) is Direction.S
    assert Side.facing(Direction.E, Direction.S) is Side.LEFT


def test_box_operations() -> None:
    """Touching boxes do not intersect; contained, inflated, shifted, united and overlapping boxes as expected."""
    a, b = Box(0, 0, 2, 2), Box(2, 0, 3, 1)
    assert not a.intersects(b) and a.intersects(Box(1, 1, 3, 3))
    assert a.contains(Box(0.5, 0.5, 1, 1)) and a.inflated(1) == Box(-1, -1, 3, 3) and a.shifted(1, 2) == Box(1, 2, 3, 4)
    assert a.union(b) == Box(0, 0, 3, 2) and a.overlap_area(Box(1, 1, 3, 3)) == 1.0 and Box.around(2, 3, 0, 1) == Box(0, 1, 2, 3)
    assert (a.width, a.height, a.centre) == (2, 2, (1.0, 1.0))


def test_closed_path_keeps_every_corner() -> None:
    """A box's four corners give a closed path through all four (matplotlib would drop the last one)."""
    path = Polygons.closed_path(np.array([(0, 0), (2, 0), (2, 2), (0, 2)]))
    assert np.asarray(path.vertices).shape[0] == 5
    assert path.contains_point((1.9, 1.9)) and path.contains_point((0.1, 1.9))


# parameters


@pytest.mark.parametrize("widest_kwh, expected", [(26315.0, 4000.0), (600.0, 100.0), (7.0, 1.0), (1400.0, 200.0), (2700.0, 400.0), (0.0, 1.0)])
def test_scale_series(widest_kwh: float, expected: float) -> None:
    """The scale is the smallest of 1, 2, 4, 5 x 10^n that keeps the widest band within 7 u; the full year lands at 4,000."""
    assert Scale.kwh_per_unit(widest_kwh, 7.0) == expected


def test_scale_limits_and_minimum_band() -> None:
    """A limit of 0 u is refused; 2 px at 30 px/u stand for 267 kWh at 4,000 kWh/u; a small band is drawn at the minimum."""
    with pytest.raises(ValueError):
        Scale.kwh_per_unit(10.0, 0.0)
    assert Scale.min_band_kwh(4000.0, CONSTANTS) == pytest.approx(266.67, abs=0.01)
    assert Scale.drawn_kwh(-5.0, 267.0) == -267.0 and Scale.drawn_kwh(500.0, 267.0) == 500.0


def test_constants_pitch() -> None:
    """The library's pitch at a 110 degree head angle is tan(35 degrees)."""
    assert CONSTANTS.pitch == pytest.approx(0.7002, abs=1e-4)


def test_parameter_containers() -> None:
    """A diagram finds its flows by id and position; parameters find diagrams; an inflated flow says so."""
    inflated = FlowParams("a", 1.0, 5.0, 0, 1.0, Side.TAIL)
    diagram = DiagramParams("N", None, None, None, Direction.E, 0, None, 3.0, (0.0, 0.0), "#888888", "N", [inflated])
    parameters = Parameters(1.0, 5.0, [diagram], [ConnectorParams(0, "l", "N|a", "M|a")])
    assert diagram.flow("a") is inflated and diagram.index("a") == 0 and inflated.inflated
    assert parameters.diagram("N") is diagram and parameters.position("N") == 0


# drawing


def test_build_and_direction_of(layout: Layout) -> None:
    """Every sub-diagram is drawn; the library's angles become page directions; a refused connection names its node."""
    drawn = SankeyBuild.build(layout.parameters, CONSTANTS, new_axes())
    assert set(drawn) == {d.component for d in layout.parameters.diagrams}
    assert SankeyBuild.direction_of(3) is Direction.S and SankeyBuild.direction_of(0.0) is Direction.E
    assert drawn["Battery"].direction("Battery|Battery.ChargingPower") is Direction.S
    broken = Parameters(1.0, 0.0, [DiagramParams("N", None, None, None, Direction.E, 0, None, 3.0, (0.0, 0.0), "#888888", "N", [flow("a", 1.0, 2)])], [])
    with pytest.raises(SankeyBuildError, match="N"):
        SankeyBuild.build(broken, CONSTANTS, new_axes())


def test_drawn_diagram_moves_with_its_tips() -> None:
    """Moving a drawn sub-diagram shifts its patch, tips, centre and label positions together."""
    drawn = SankeyBuild.build(Parameters(1.0, 0.0, [two_flow_diagram()], []), CONSTANTS, new_axes())["N"]
    tip = drawn.tip("b").copy()
    drawn.move(1.0, 2.0)
    assert np.allclose(drawn.tip("b"), tip + np.array([1.0, 2.0])) and drawn.centre == (1.0, 2.0) and drawn.index("b") == 1


def test_flatten_head_and_notch() -> None:
    """A stub's arrowhead is cut flat behind its tip; an input's notch is filled out to its outer corners."""
    drawn = SankeyBuild.build(Parameters(1.0, 0.0, [two_flow_diagram()], []), CONSTANTS, new_axes())["N"]
    tip_b, dip_a = drawn.tip("b"), drawn.tip("a")
    end = SankeyBuild.flatten_output_head(drawn.patch, tip_b, Direction.E, 1.0, CONSTANTS)
    assert end[0] == pytest.approx(tip_b[0] - (CONSTANTS.shoulder + 0.5) * CONSTANTS.pitch)
    assert drawn.patch.get_path().vertices[:, 0].max() <= end[0] + 1e-9
    start = SankeyBuild.flatten_input_notch(drawn.patch, dip_a, Direction.E, 1.0, CONSTANTS.pitch)
    assert start[0] == pytest.approx(dip_a[0] - 0.5 * CONSTANTS.pitch)
    assert np.allclose(SankeyBuild.flatten_input_notch(drawn.patch, np.array([99.0, 99.0]), Direction.E, 1.0, CONSTANTS.pitch), (99.0, 99.0))


def test_polygons_and_extent() -> None:
    """A drawn patch flattens to closed polygons; an extent spans them."""
    drawn = SankeyBuild.build(Parameters(1.0, 0.0, [two_flow_diagram()], []), CONSTANTS, new_axes())["N"]
    polygons = SankeyBuild.polygons(drawn.patch)
    assert polygons and polygons[0].shape[1] == 2
    assert SankeyBuild.extent([np.array([(0, 0), (2, 1)]), np.array([(-1, 3)])]) == (-1.0, 0.0, 2.0, 3.0)


# connectors


def test_shapes() -> None:
    """Facing ports give a straight line, offset ones a Z, perpendicular ones an L; behind the start gives nothing."""
    assert Router.shapes(Port((0.0, 0.0), Direction.E), Port((5.0, 0.0), Direction.E), 1.0)[0] == ("straight", [(0.0, 0.0), (5.0, 0.0)])
    assert Router.shapes(Port((0.0, 0.0), Direction.E), Port((10.0, 2.0), Direction.E), 1.0)[0] == ("Z", [(0.0, 0.0), (5.0, 0.0), (5.0, 2.0), (10.0, 2.0)])
    assert Router.shapes(Port((0.0, 0.0), Direction.E), Port((5.0, 5.0), Direction.N), 1.0) == [("L", [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)])]
    assert not Router.shapes(Port((0.0, 0.0), Direction.E), Port((-5.0, 5.0), Direction.N), 1.0)


def test_segment_boxes() -> None:
    """An L of width 1 gives two boxes reaching into the corner square; trimming shortens both ends."""
    assert Router.segment_boxes([(0, 0), (4, 0), (4, 4)], 1.0) == [Box(0, -0.5, 4.5, 0.5), Box(3.5, -0.5, 4.5, 4)]
    assert Router.segment_boxes([(0, 0), (4, 0)], 1.0, 0.5, 1.0) == [Box(1.0, -0.5, 3.5, 0.5)]


def test_is_clear_and_may_touch() -> None:
    """A route through another node's patch is not clear; beside its own node's patch it is."""
    req = request(Port((0.0, 0.0), Direction.E), Port((10.0, 0.0), Direction.E))
    blocking = Obstacle.of_box("diagram:C", Box(4, -1, 5, 1), ("C",))
    beside = Obstacle.of_box("diagram:A", Box(0, 0.25, 3, 2), ("A",))
    assert not Router.is_clear([(0.0, 0.0), (10.0, 0.0)], req, [blocking], 0.3)
    assert Router.is_clear([(0.0, 0.0), (10.0, 0.0)], req, [beside], 0.3)
    assert Router.may_touch(beside, req) and not Router.may_touch(blocking, req)


def test_corner_radii_and_small_helpers() -> None:
    """A Z with a 1 u middle allows 0.5 u per corner, too little room gives None; orthogonality, length, simplification."""
    assert Router.corner_radii([(0, 0), (5, 0), (5, 1), (10, 1)], 0.64, 0.24) == (0.5, 0.5)
    assert Router.corner_radii([(0, 0), (5, 0), (5, 0.3), (10, 0.3)], 0.64, 0.24) is None
    assert Router.is_orthogonal([(0, 0), (1, 0), (1, 1)]) and not Router.is_orthogonal([(0, 0), (1, 1)])
    assert Router.distance((0, 0), (3, 0)) == 3
    assert Router.simplify([(0, 0), (1, 0), (2, 0), (2, 0), (2, 1)]) == [(0, 0), (2, 0), (2, 1)]


def test_route_takes_a_shape_or_searches() -> None:
    """A clear straight line is taken; with a block in the way the route goes around it, clear of it."""
    bounds = Box(-5, -10, 15, 10)
    req = request(Port((0.0, 0.0), Direction.E), Port((10.0, 0.0), Direction.E))
    assert Router.route(req, [], bounds, CONSTANTS).shape == "straight"
    block = Obstacle.of_box("diagram:C", Box(4, -2, 6, 2), ("C",))
    around = Router.route(req, [block], bounds, CONSTANTS)
    assert around.shape in ("routed", "Z") and Router.is_clear(around.points, req, [block], 0.0)


def test_remove_jogs_and_shift_run() -> None:
    """A one-cell jog is removed when the band stays clear; the run before it moves onto the run after it."""
    points = [(0.0, 0.0), (0.0, 2.0), (5.0, 2.0), (5.0, 2.25), (9.0, 2.25), (9.0, 6.0)]
    req = request(Port((0.0, 0.0), Direction.N), Port((9.0, 6.0), Direction.N), 0.1)
    assert Router.remove_jogs(points, req, [], (0.48, 0.15)) == [(0.0, 0.0), (0.0, 2.25), (9.0, 2.25), (9.0, 6.0)]
    assert Router.shift_run(points, 2, False, before=True)[1:3] == [(0.0, 2.25), (5.0, 2.25)]


def test_route_points() -> None:
    """Pinned turns give orthogonal corners; turns that leave the stub backwards are refused."""
    assert Router.route_points(Port((0.0, 0.0), Direction.E), Port((5.0, 3.0), Direction.N), []) == [(0.0, 0.0), (5.0, 0.0), (5.0, 3.0)]
    assert Router.route_points(Port((0.0, 0.0), Direction.E), Port((8.0, 3.0), Direction.E), ["+4"]) == [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (8.0, 3.0)]
    with pytest.raises(ValueError):
        Router.route_points(Port((0.0, 0.0), Direction.E), Port((5.0, 3.0), Direction.N), [-2.0])


def test_grid_parts() -> None:
    """A polygon rasters onto the cells inside it and dilates; A* finds the bend-free way; ends attach exactly."""
    mask = GridSearch.raster(np.array([(1.0, -1.0), (2.0, -1.0), (2.0, 1.0), (1.0, 1.0)]), (0.0, -2.0), (20, 20), 0.25)
    assert mask[6, 8] and not mask[0, 8]
    grown = GridSearch.dilate(mask, 1)
    assert grown[3, 8] and not mask[3, 8]
    assert GridSearch.astar(np.zeros((10, 10), dtype=bool), (0, 0), (4, 0), (0, 0), 3.0) == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
    joined = GridSearch.attach_ends(request(Port((0.0, 0.0), Direction.E), Port((10.0, 2.1), Direction.E)), [(1.0, 0.0), (5.0, 0.0), (5.0, 2.0), (9.0, 2.0)])
    assert joined[-1] == (10.0, 2.1)
    found = GridSearch.search(
        request(Port((0.0, 0.0), Direction.E), Port((10.0, 0.0), Direction.E)), [Obstacle.of_box("x", Box(4, -2, 6, 2), ("C",))], Box(-5, -10, 15, 10), 1.0, CONSTANTS
    )
    assert found is not None and found[0] == (0.0, 0.0) and found[-1] == (10.0, 0.0)


def test_bands() -> None:
    """[(0, 0), (2, 0), (2, 2)] with radius 1 turns around (1, 1); its outline is the line offset by half the width."""
    line, tangents = Bands.rounded_centre_line([(0, 0), (2, 0), (2, 2)], [1.0])
    assert line[1] == pytest.approx((1.0, 0.0)) and line[-2] == pytest.approx((2.0, 1.0)) and tangents[-1] == pytest.approx((0.0, 1.0))
    outline, left, right = Bands.outline(np.array([(0.0, 0.0), (2.0, 0.0)]), np.array([(1.0, 0.0), (1.0, 0.0)]), 1.0)
    assert left[0] == pytest.approx((0.0, 0.5)) and right[0] == pytest.approx((0.0, -0.5)) and len(outline) == 4


# labels


def test_text_measure() -> None:
    """A wider text measures wider, two lines are twice as high as one, an empty line has no width."""
    measure = TextMeasure(CONSTANTS)
    one, two = measure.size(TextBlock(("Heat",), 9.0)), measure.size(TextBlock(("Heat pump", "x"), 9.0))
    assert two[0] > one[0] and two[1] == pytest.approx(2 * one[1]) and measure.line_width_pt("", 9.0, False) == 0.0


def test_shape_hits_and_crosses() -> None:
    """A box over a shape hits it, a box inside counts, a box beside does not; two overlapping shapes cross."""
    shape = Shape.of("diagram:A", square(0, 0, 4, 2), ("A",))
    assert shape.hits(Box(3, 1, 5, 3)) and shape.hits(Box(1, 0.5, 2, 1)) and not shape.hits(Box(5, 0, 6, 1))
    assert shape.crosses(Shape.of("diagram:B", square(3, 1, 6, 3), ("B",))) and not shape.crosses(Shape.of("diagram:C", square(5, 0, 6, 1), ("C",)))


def test_candidate_helpers() -> None:
    """Anchored boxes follow matplotlib's alignments; a label beside a point is anchored away from it."""
    assert Candidates.anchored((0.0, 0.0), "left", "top", (2.0, 1.0)).box == Box(0.0, -1.0, 2.0, 0.0)
    assert Candidates.away_alignment(Direction.N) == ("center", "bottom")
    assert Candidates.beside_point((0.0, 0.0), Direction.E, (2.0, 1.0), 0.1) == Candidate(Box(0.1, -0.5, 2.1, 0.5), "left")
    assert Candidates.wrapped("Appliance electricity not released indoors", 18) == "Appliance\nelectricity not\nreleased indoors"


def test_terminal_link_and_title_candidates() -> None:
    """A branch's first candidate lies beyond its outer end; a wide band's first inside; a title's at the centre."""
    terminal = Candidates.terminal(((0.0, 2.0), (0.0, 1.0), 0.5), [((2.0, 1.0), ("a",)), ((1.0, 2.0), ("b", "c"))], 0.1)
    assert terminal[0].box == Box(-1.0, 2.1, 1.0, 3.1) and terminal[0].lines == ("a",) and terminal[-1].lines == ("b", "c")
    assert Candidates.link([(0.0, 0.0), (10.0, 0.0)], 2.0, (3.0, 1.0), 0.1)[0].box == Box(3.5, -0.5, 6.5, 0.5)
    title = Candidates.title((3.0, 0.0), Box(0, -1, 6, 1), (2.0, 1.0), 0.1)
    assert title[0].box == Box(2.0, -0.5, 4.0, 0.5) and len(title) == 5


def test_label_placer() -> None:
    """A label avoids a shape by its second candidate; scores, counts and the exempt rule as expected."""
    spec = LabelSpec("flow:x", TextBlock(("x",), 9.0), (Candidate(Box(0, 0, 1, 1), "left"), Candidate(Box(5, 0, 6, 1), "left")), (), 1.0, False, "path:x")
    shapes = [Shape.of("diagram:A", square(0, 0, 2, 2), ("A",))]
    assert LabelPlacer.score(spec.candidates[0], spec.candidates[0], spec, shapes, []) == 1000.0
    assert LabelPlacer.best(spec, shapes, []).box == Box(5, 0, 6, 1)
    assert LabelPlacer.place([spec], shapes)[0].box == Box(5, 0, 6, 1)
    assert LabelPlacer.count(PlacedLabel("flow:x", spec.block, Box(0, 0, 1, 1), "left", False, "", ()), shapes, []) == 1


def test_overlap_check() -> None:
    """Labels over labels or shapes, crossing patches (but a prior and its child) and crossing connectors are found."""
    block = TextBlock(("x",), 9.0)
    labels = [PlacedLabel("flow:a", block, Box(0, 0, 2, 1), "left", False, "", ()), PlacedLabel("flow:b", block, Box(1, 0, 3, 1), "left", False, "", ())]
    found = OverlapCheck.labels(labels, [Shape.of("diagram:C", square(2.5, 0, 4, 1), ("C",))])
    assert found == [Overlap("label/label", "flow:a", "flow:b"), Overlap("label/band", "flow:b", "diagram:C")]
    patches = [Shape.of("diagram:A", square(0, 0, 2, 2), ("A",)), Shape.of("diagram:B", square(1, 1, 3, 3), ("B",)), Shape.of("diagram:C", square(1, 0, 3, 1), ("C",))]
    assert OverlapCheck.bands(patches, [("B", "A")]) == [Overlap("band/band", "diagram:A", "diagram:C")]  # B touches C only
    assert OverlapCheck.connectors([Shape.of("connector:0", square(0.5, 0.5, 5, 0.8), ())], patches[:1]) == [Overlap("connector/band", "connector:0", "diagram:A")]


# texts


def test_title_value_owner_reason(layout: Layout, context: TextContext) -> None:
    """Titles carry metric lines; values carry † when inflated; a residual of a clean node has no reason."""
    block = FigureTexts.title_block(layout.graph, "HeatPump", "Heat pump", context.inputs, CONSTANTS)
    assert block.lines == ("Heat pump", "SPF 4.00 · ambient 75 %") and block.bold_lines == 1
    small = layout.parameters.diagram("Battery").flow("Battery|Battery.Stored")
    assert FigureTexts.value_text(small).endswith(" †")
    stored = next(f for f in layout.graph.node("Battery").flows if f.kind is FlowKind.STORAGE)
    assert FigureTexts.owner_class(stored, {"Battery": "Battery"}) == "Battery"
    assert FigureTexts.residual_reason(layout.graph, context.inputs, "Buffer") == ""
    assert FigureTexts.flow_name(context, stored, small) == "Stored"  # a port without a display name keeps its name


def test_stub_geometry_and_pinned_candidate(layout: Layout) -> None:
    """A loss branch's outer end is its arrow tip and its middle lies half its path back; a pinned label is shifted."""
    assert layout.drawing is not None
    picture = layout.drawing.drawn["Buffer"]
    params = layout.parameters.diagram("Buffer").flow("Buffer|Buffer.StandbyHeatLoss")
    outer, middle, _, away = FigureTexts.stub_geometry(picture, params, CONSTANTS, layout.parameters.kwh_per_unit)
    assert np.allclose(outer, picture.tip(params.flow_id)) and away is Direction.N
    assert outer[1] - middle[1] == pytest.approx(params.pathlength / 2)
    pinned = FlowParams(params.flow_id, params.value_kwh, params.drawn_kwh, params.orientation, params.pathlength, params.side, LabelPin(shift=(1.0, 0.0)))
    candidate = FigureTexts.pinned_flow_candidate(picture, pinned, (2.0, 1.0), CONSTANTS, layout.parameters.kwh_per_unit)
    assert candidate.box.centre[0] == pytest.approx(picture.label_positions[picture.index(params.flow_id)][0] + 1.0)


def test_specs(layout: Layout, context: TextContext) -> None:
    """Terminal, connection, connector and title specs carry their keys, growth moves and candidates."""
    assert layout.drawing is not None
    drawn, parameters = layout.drawing.drawn, layout.parameters
    loss = parameters.diagram("Buffer").flow("Buffer|Buffer.StandbyHeatLoss")
    assert FigureTexts.terminal_spec(context, drawn["Buffer"], loss, parameters.kwh_per_unit).grows == "path:Buffer|Buffer.StandbyHeatLoss"
    connection = FigureTexts.connection_spec(context, parameters, parameters.diagram("Buffer"), drawn)
    assert connection is not None and connection.key == "flow:HeatPump|Buffer.loop"
    assert FigureTexts.connection_spec(context, parameters, parameters.diagram("PV"), drawn) is None
    link_id, route = next(iter(sorted(layout.drawing.routes.items())))
    assert FigureTexts.connector_spec(context, parameters, link_id, route, drawn).key == f"link:{link_id}"
    block = FigureTexts.link_block(context, "Meter|Battery.ChargingPower", parameters.diagram("Meter").flow("Meter|Battery.ChargingPower"))
    assert block.lines[0] == "Battery charging"
    meter = parameters.diagram("Meter")
    title = FigureTexts.title_spec(context, meter, drawn["Meter"], FigureTexts.trunk_box(meter, drawn["Meter"], parameters.kwh_per_unit), block)
    assert title.exempt == ("diagram:Meter",)
    assert len(FigureTexts.label_specs(context, parameters, drawn, layout.drawing.routes)) > 10


def test_stub_length_with_stubs_and_inside(layout: Layout) -> None:
    """Head outputs are drawn as long as the longest; a connector's label line runs back along its stubs."""
    assert layout.drawing is not None
    meter = layout.parameters.diagram("Meter")
    head = meter.flow("Meter|HeatPump.ElectricalInputPowerSH")
    assert FigureTexts.stub_length(meter, head) == head.pathlength
    connector = layout.parameters.connectors[0]
    route = layout.drawing.routes[connector.link_id]
    assert len(FigureTexts.with_stubs(layout.parameters, connector, route, layout.drawing.drawn)) >= len(route.points)
    x, y = route.points[0]
    on_line = Candidate(Box(x - 0.1, y - 0.1, x + 0.1, y + 0.1), "center")
    assert FigureTexts.inside(on_line, Route(0, ((x - 1, y), (x + 1, y)), 0.1, "L", ()))


def test_trunk_box_and_caption(layout: Layout, context: TextContext) -> None:
    """A trunk box is centred on its trunk; the caption states the period, the scale, the verdict and the † note."""
    assert layout.drawing is not None
    box = FigureTexts.trunk_box(layout.parameters.diagram("Meter"), layout.drawing.drawn["Meter"], layout.parameters.kwh_per_unit)
    assert box.centre == pytest.approx(layout.drawing.drawn["Meter"].centre)
    caption = FigureTexts.caption(layout.graph, context.inputs, 40.0, True, CONSTANTS)
    assert "365 days" in caption[0] and "1 unit = 40 kWh" in caption[0] and "†" in caption[0]


# one round from pin parameters


def test_the_house_draws_from_its_pin_file_with_no_fault(layout: Layout) -> None:
    """The heat pump house drawn from its hand-written pin file has no fault and no overlap, in one round."""
    assert not layout.faults and not layout.overlaps and layout.rounds == 1 and not layout.nothing_to_draw
    assert layout.drawing is not None and {r.shape for r in layout.drawing.routes.values()} == {"pinned"}


def test_draw_with_nothing_to_draw() -> None:
    """A view without drawn nodes gives an empty layout and no figure."""
    layout = PlacementSearch.draw(SankeySystems.nothing_to_draw(), Parameters(1.0, 0.0, [], []))
    assert layout.nothing_to_draw and not layout.faults and layout.drawing is None


def test_draw_reports_overlaps_and_a_refused_build_as_faults(layout: Layout) -> None:
    """A label pinned onto a band gives an overlap fault; a connection the library refuses a library fault."""
    pins = SankeySystems.heat_pump_house_pins()
    pins["diagrams"][1]["flows"][4]["label"] = [-8.0, -1.0]  # the heat pump electricity's label onto the meter's trunk
    graph, style = layout.graph, SankeyStyle.load()
    crowded = PlacementSearch.draw(SankeySystems.heat_pump_house(), PinFile.parameters(pins, graph, style, CONSTANTS))
    assert crowded.overlaps and crowded.faults[-1] == Fault("overlap", f"{len(crowded.overlaps)} overlaps left")
    broken = PinFile.parameters(SankeySystems.heat_pump_house_pins(), graph, style, CONSTANTS)
    broken.diagrams[2].flows[1].orientation = 2  # an orientation the library does not know
    refused = PlacementSearch.draw(SankeySystems.heat_pump_house(), broken)
    assert refused.drawing is None and refused.faults[0].kind == "library"


def test_tree_placement(layout: Layout) -> None:
    """The house is one tree; boxes span patches; an ``at`` on any member moves its whole tree so it lands there."""
    assert layout.drawing is not None
    parameters, drawn = layout.parameters, layout.drawing.drawn
    assert TreePlacement.members(parameters) == [[d.component for d in parameters.diagrams]]
    box = TreePlacement.box_of(drawn, ["Meter"])
    assert box.width > 0 and box.height > 0
    pv_before = drawn["PV"].centre
    meter = parameters.diagram("Meter")
    keep = meter.at
    meter.at = (100.0, 50.0)
    TreePlacement.place_pinned(parameters, drawn, [d.component for d in parameters.diagrams])
    assert drawn["Meter"].centre == pytest.approx((100.0, 50.0)) and drawn["PV"].centre != pv_before
    meter.at = (0.0, 0.0)
    TreePlacement.place_pinned_trees(parameters, drawn)
    assert drawn["Meter"].centre == pytest.approx((0.0, 0.0))
    meter.at = keep


def test_further_tree_and_parallel_arrow(tmp_path: Path) -> None:
    """The boiler fed twice: the electricity meter's tree lands at its ``at``, the second fuel link is its own arrow.

    The hot-water fuel connector is ``parallel``: it keeps the boiler's notch and is drawn with its own arrowhead
    (the layout file records it); the figure has no fault.
    """
    inputs = SankeySystems.boiler_fed_twice()
    layout = PinFile.draw(inputs, SankeySystems.pin_file(SankeySystems.boiler_fed_twice_pins(), tmp_path), CONSTANTS)
    assert layout.drawing is not None and not layout.faults and not layout.overlaps
    assert layout.drawing.drawn["E"].centre == pytest.approx((0.0, -9.0))
    connector = next(c for c in layout.parameters.connectors if c.label == "B.EnergyDemandDhw")
    assert connector.parallel and layout.drawing.routes[connector.link_id].shape == "pinned"
    assert json.loads(LayoutFile.dumps(layout))["connectors"][0]["parallel"]


def test_connecting_ports_flattening_and_route_all(layout: Layout, context: TextContext) -> None:
    """A connector's ports lie behind its stubs' tips; routing a fresh build gives every connector a route and outline."""
    assert layout.drawing is not None
    connector = layout.parameters.connectors[0]
    start, end, width = Connecting.ports(layout.parameters, layout.drawing.drawn, connector.link_id, CONSTANTS)
    assert width > 0 and start.direction in Direction and end.direction in Direction
    axes = new_axes()
    drawing = Drawing(axes.figure, axes, SankeyBuild.build(layout.parameters, CONSTANTS, axes))
    Connecting.flatten_cuts(layout.graph, layout.parameters, drawing.drawn, CONSTANTS)
    Connecting.route_all(layout.parameters, drawing, CONSTANTS)
    Connecting.flatten_parallel(layout.parameters, drawing.drawn, drawing.routes, CONSTANTS)
    assert set(drawing.routes) == {c.link_id for c in layout.parameters.connectors} == set(drawing.connector_outlines)
    again = PlacementSearch.round(context, layout.parameters)
    assert again.drawing is not None and not again.overlaps


def test_canvas_and_trimmed_connectors(layout: Layout) -> None:
    """The canvas holds every shape and label; every routed connector gets a trimmed outline for the crossing check."""
    assert layout.drawing is not None
    x0, x1, y0, y1 = PlacementSearch.canvas(layout.drawing, layout.parameters, CONSTANTS)
    assert all(x0 <= s.box.x0 and s.box.x1 <= x1 and y0 <= s.box.y0 and s.box.y1 <= y1 for s in layout.drawing.shapes)
    assert len(PlacementSearch.trimmed_connectors(layout.drawing, CONSTANTS)) == len(layout.drawing.connector_outlines)


# render


def test_residual_box_caption_wrap_and_arrowhead() -> None:
    """A residual box runs back from the outer end; a long caption wraps; an arrowhead is as deep as the target's notch."""
    corners = SankeyFigure.residual_box((5.0, 0.0), (1.0, 0.0), 1.0, 2.0)
    assert corners[0] == pytest.approx((5.0, 0.58)) and corners[1] == pytest.approx((3.0, 0.58))
    assert len(SankeyFigure.wrap_caption(["word " * 100], 1500.0)) == 3
    head = SankeyFigure.arrowhead((5.0, 0.0), 1.0, 0.0, 1.0, CONSTANTS)
    assert head[0] == pytest.approx((5.0, 0.5)) and head[1] == pytest.approx((5.0 + 0.5 * CONSTANTS.pitch, 0.0)) and head[2] == pytest.approx((5.0, -0.5))


def test_write_and_layout_file(tmp_path: Path, layout: Layout) -> None:
    """The figure is written as PNG, PDF, SVG and the layout file, which records chain, diagrams and connectors."""
    (tmp_path / "overall.layout_error.json").write_text("{}", encoding="utf-8")
    written = SankeyFigure.write(layout, tmp_path, CONSTANTS)
    assert sorted(path.name for path in written) == ["overall.layout.json", "overall.pdf", "overall.png", "overall.svg"]
    assert not (tmp_path / "overall.layout_error.json").exists()  # a clean figure removes a stale fault file
    document = json.loads(LayoutFile.dumps(layout))
    assert document["chain"] == ["Meter", "HeatPump", "Buffer", "Building"] and document["diagrams"][0]["component"] == "PV"
    assert LayoutFile.rounded(-0.00001) == 0.0 and LayoutFile.rounded(1.234567) == 1.2346


def test_fault_file(tmp_path: Path) -> None:
    """Faults and overlaps become sentences in ``overall.layout_error.json``."""
    layout = Layout(
        "t",
        cast(Any, SimpleNamespace(nodes=())),
        Parameters(1.0, 0.0, [], []),
        None,
        [Overlap("label/band", "a", "b")],
        [Fault("overlap", "1 overlaps left")],
        (0, 1, 0, 1),
    )
    assert SankeyFigure.fault_sentences(layout) == ["overlap: 1 overlaps left"]
    assert SankeyFigure.overlap_sentences(layout) == ["label/band a <> b"]
    path = SankeyFigure.write_faults(tmp_path / "x", ["overlap: 1 overlaps left"], ["label/band a <> b"])
    assert json.loads(path.read_text(encoding="utf-8")) == {"faults": ["overlap: 1 overlaps left"], "overlaps": ["label/band a <> b"]}


def test_finish_and_mark_residuals(tmp_path: Path) -> None:
    """Finishing sizes the figure at 30 px/u; a residual gets its hatched mark."""
    kinds = {"A": "component", "B": "component", "src": "boundary", "out": "boundary"}
    edges = [
        ("src", "A", 100.0, "space_heating_heat", "boundary", "A.in"),
        ("A", "B", 80.0, "space_heating_heat", "transfer", "B.in"),
        ("B", "out", 80.0, "room_heat", "boundary", "B.out"),
    ]
    pins: Dict[str, Any] = {
        "schema": "hisim.sankey_pins/1",
        "diagrams": [
            {
                "component": "A",
                "trunklength": 3.0,
                "flows": [
                    {"link": "A.in", "orientation": 0, "pathlength": 1.0},
                    {"residual": True, "orientation": -1, "pathlength": 1.0},
                    {"link": "B.in", "orientation": 0, "pathlength": 2.0},
                ],
            },
            {
                "component": "B",
                "prior": "A",
                "connect": "B.in",
                "trunklength": 3.0,
                "flows": [{"link": "B.in", "orientation": 0, "pathlength": 0.5}, {"link": "B.out", "orientation": 0, "pathlength": 1.0}],
            },
        ],
    }
    layout = PinFile.draw(SankeySystems.inputs(kinds, edges), SankeySystems.pin_file(pins, tmp_path), CONSTANTS)
    assert layout.drawing is not None
    patches = len(layout.drawing.axes.patches)
    SankeyFigure.mark_residuals(layout, layout.drawing.axes, CONSTANTS)
    assert len(layout.drawing.axes.patches) == patches + 1
    figure = SankeyFigure.finish(layout, CONSTANTS)
    assert figure.get_size_inches()[0] == pytest.approx((layout.canvas[1] - layout.canvas[0]) * CONSTANTS.px_per_unit / CONSTANTS.dpi)


# pins


def test_pin_helpers() -> None:
    """A flow entry's id and label pin are read from the prototype's keys; an entry without a link is refused."""
    assert PinFile.flow_id("N", {"link": "N.x"}) == "N|N.x" and PinFile.flow_id("N", {"residual": True}) == "N|residual"
    assert PinFile.flow_id("N", {"cut": True}) == "N|cut"
    with pytest.raises(PinError):
        PinFile.flow_id("N", {"orientation": 0})
    pin = PinFile.label_pin({"label": [1, 2], "inside": True, "on_bands": ["M"]})
    assert pin is not None and pin.shift == (1.0, 2.0) and pin.inside and pin.exempt == ("M",)
    assert PinFile.label_pin({"orientation": 0}) is None


def test_pin_parameters_diagram_and_connector(layout: Layout) -> None:
    """A pin document gives the scale from the run, its diagrams and connectors; unknown names and schemas are refused."""
    document, graph, style = SankeySystems.heat_pump_house_pins(), layout.graph, SankeyStyle.load()
    parameters = PinFile.parameters(document, graph, style, CONSTANTS)
    assert parameters.pinned and parameters.kwh_per_unit == 40.0 and parameters.diagram("Meter").prior == "PV" and parameters.canvas is None
    meter = PinFile.diagram(document["diagrams"][1], graph, style, (parameters.kwh_per_unit, parameters.min_band_kwh))
    assert meter.component == "Meter" and meter.at == (0.0, 0.0) and meter.prior_flow == "PV|PV.ElectricityOutput"
    connector = PinFile.connector({"link": "Battery.DischargingPower", "turns": []}, graph)
    assert connector.source_flow == "Battery|Battery.DischargingPower" and not connector.parallel
    assert PinFile.connector({"link": "Battery.DischargingPower", "parallel": True}, graph).parallel
    with pytest.raises(PinError, match="schema"):
        PinFile.parameters({**document, "schema": "x"}, graph, style, CONSTANTS)
    with pytest.raises(PinError, match="nope"):
        PinFile.connector({"link": "nope"}, graph)
    with pytest.raises(PinError, match="Ghost"):
        PinFile.parameters({**document, "diagrams": [{"component": "Ghost"}]}, graph, style, CONSTANTS)


# the entries: draw_overall, draw_run, the command, PLOT_SANKEY


def test_draw_overall(tmp_path: Path) -> None:
    """With a pin file or laid out automatically the figure is written; nothing for a run with nothing to draw."""
    pins = SankeySystems.pin_file(SankeySystems.heat_pump_house_pins(), tmp_path)
    layout, written = draw_overall(SankeySystems.heat_pump_house(), tmp_path / "a", pins)
    assert not layout.faults and len(written) == 4 and layout.parameters.pinned
    layout, written = draw_overall(SankeySystems.heat_pump_house(), tmp_path / "b")
    assert not layout.faults and len(written) == 4 and not layout.parameters.pinned
    layout, nothing = draw_overall(SankeySystems.nothing_to_draw(), tmp_path / "c")
    assert not nothing and layout.nothing_to_draw


def write_result_directory(directory: Path) -> Path:
    """Write the two files a result directory needs for the figure (a meter feeding a household) and return it."""
    sankeys = directory / "energy_sankeys"
    sankeys.mkdir(parents=True)
    view = {
        "overall": {
            "nodes": [{"name": "grid", "kind": "boundary"}, {"name": "M", "kind": "meter"}, {"name": "H", "kind": "component"}, {"name": "out", "kind": "boundary"}],
            "links": [
                {"source": 0, "target": 1, "value_kwh": 3.0, "carrier": "electricity", "kind": "meter_boundary", "label": "M.in"},
                {"source": 1, "target": 2, "value_kwh": 3.0, "carrier": "electricity", "kind": "metered", "label": "H.in"},
                {"source": 2, "target": 3, "value_kwh": 3.0, "carrier": "electricity", "kind": "boundary", "label": "H.out"},
            ],
        }
    }
    (sankeys / "energy_sankey.json").write_text(json.dumps(view), encoding="utf-8")
    balance = {"verdict": "closes", "timesteps": 96, "seconds_per_timestep": 900, "components": [], "undeclared": []}
    (directory / "balance_report.json").write_text(json.dumps(balance), encoding="utf-8")
    return directory


def with_a_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the automatic layout report one fault on top of what it finds."""
    run = PlacementSearch.run

    def faulty(*args: Any, **kwargs: Any) -> Layout:
        layout = run(*args, **kwargs)
        layout.faults.append(Fault("overlap", "1 overlaps left after 12 rounds"))
        return layout

    monkeypatch.setattr(PlacementSearch, "run", faulty)


def test_draw_run_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``draw_run`` draws a clean figure with no fault, lists and writes faults, and turns a missing input into one (D15)."""
    directory = write_result_directory(tmp_path / "run")
    assert SankeyInputs.from_result_directory(directory).title == "run"
    assert not FigureFiles.draw_run(directory) and (directory / "energy_sankeys" / "overall.png").is_file()
    with_a_fault(monkeypatch)
    faults = FigureFiles.draw_run(directory)
    assert faults == ["overlap: 1 overlaps left after 12 rounds"]
    assert json.loads((directory / "energy_sankeys" / "overall.layout_error.json").read_text(encoding="utf-8"))["faults"] == faults
    missing = FigureFiles.draw_run(tmp_path / "empty")
    assert len(missing) == 1 and missing[0].startswith("error: the figure could not be drawn: SankeyInputError")
    assert (tmp_path / "empty" / "energy_sankeys" / "overall.layout_error.json").is_file()


def test_the_command_draws_and_exits_by_its_faults(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """``hisim energy-system sankey`` exits 0 drawn from a clean pin file or laid out, 4 with layout faults, 2 without inputs."""
    directory = write_result_directory(tmp_path / "run")
    pins = {
        "schema": "hisim.sankey_pins/1",
        "diagrams": [
            {
                "component": "M",
                "trunklength": 3.0,
                "flows": [{"link": "M.in", "orientation": 0, "pathlength": 1.0}, {"link": "H.in", "orientation": 0, "pathlength": 3.0}],
            },
            {
                "component": "H",
                "prior": "M",
                "connect": "H.in",
                "trunklength": 3.0,
                "flows": [{"link": "H.in", "orientation": 0, "pathlength": 0.5}, {"link": "H.out", "orientation": 0, "pathlength": 1.0}],
            },
        ],
    }
    pin_path = SankeySystems.pin_file(pins, tmp_path)
    assert cli.main(["energy-system", "sankey", str(directory), "--pins", str(pin_path), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "overall.png").is_file()
    assert cli.main(["energy-system", "sankey", str(directory)]) == 0
    with_a_fault(monkeypatch)
    assert cli.main(["energy-system", "sankey", str(directory)]) == 4
    assert "layout fault (overlap): 1 overlaps left after 12 rounds" in capsys.readouterr().err
    assert cli.main(["energy-system", "sankey", str(tmp_path / "missing")]) == 2


def test_plot_sankey_implies_the_export_and_draws_the_figure(tmp_path: Path) -> None:
    """``PLOT_SANKEY`` writes the balance files and draws the figure after them; without it nothing is drawn."""
    directory = write_result_directory(tmp_path / "run")
    plotting = cast(
        Any, SimpleNamespace(post_processing_options=[PostProcessingOptions.PLOT_SANKEY], simulation_parameters=SimpleNamespace(result_directory=str(directory)))
    )
    quiet = cast(Any, SimpleNamespace(post_processing_options=[], simulation_parameters=SimpleNamespace(result_directory=str(tmp_path / "quiet"))))
    exporting = cast(Any, SimpleNamespace(post_processing_options=[PostProcessingOptions.EXPORT_ENERGY_BALANCE]))
    assert PostProcessor.writes_energy_balance(plotting) and PostProcessor.writes_energy_balance(exporting) and not PostProcessor.writes_energy_balance(quiet)
    PostProcessor.plot_sankey(quiet)
    assert not (tmp_path / "quiet").exists()
    PostProcessor.plot_sankey(plotting)
    assert (directory / "energy_sankeys" / "overall.png").is_file() and not (directory / "energy_sankeys" / "overall.layout_error.json").exists()
    concluded: List[Optional[str]] = []
    balance = cast(Any, SimpleNamespace(conclude=concluded.append))
    PostProcessor.export_sankeys(plotting, balance)
    assert concluded == [str(directory)]


def test_plot_sankey_without_compute_kpis_is_refused_when_the_parameters_are_read() -> None:
    """D14: a parameter set with ``PLOT_SANKEY`` and without ``COMPUTE_KPIS`` is refused with the sentence naming both."""
    with pytest.raises(EnergySystemFormatError, match="PLOT_SANKEY .* requires COMPUTE_KPIS"):
        SimulationParametersReader.build({"post_processing_options": ["PLOT_SANKEY"]}, "x.simulation.yaml")
    SimulationParametersReader.refuse_sankey_without_kpis([PostProcessingOptions.PLOT_SANKEY, PostProcessingOptions.COMPUTE_KPIS], "x")
    SimulationParametersReader.refuse_sankey_without_kpis([], "x")
