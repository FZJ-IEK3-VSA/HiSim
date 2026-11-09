"""Direct tests of the Sankey figure's automatic layout.

Covered: the orientation table with its context rules and the second link to the same node (``orientation``), the
frame rule, the side orders, the adjustments and the parameter builder (``parameters``), the tree placement, the
repair moves and the search (``search``), and the acceptance rules of the matplotlib Sankey spec on the synthetic
systems of ``tests/sankey_test_systems.py``: every system lays out with no fault and no overlap (§9.2), also with
every flow 25 % larger, and the layout file is the same twice in one process and in a second process (§9.3).
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, List

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from hisim.postprocessing.energy_balance.figure import SankeyInputs
from hisim.postprocessing.energy_balance.figure.connectors import GridSearch
from hisim.postprocessing.energy_balance.figure.drawing import SankeyBuild
from hisim.postprocessing.energy_balance.figure.geometry import Box, Direction, Side
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, FlowKind, GraphBuilder, Role
from hisim.postprocessing.energy_balance.figure.labels import Overlap, TextMeasure
from hisim.postprocessing.energy_balance.figure.orientation import Orientation
from hisim.postprocessing.energy_balance.figure.parameters import (
    Adjustments,
    ConnectorParams,
    DiagramParams,
    FlowParams,
    FrameRule,
    LayoutConstants,
    ParameterBuilder,
    Parameters,
    SideOrder,
    TextSizes,
)
from hisim.postprocessing.energy_balance.figure.render import LayoutFile
from hisim.postprocessing.energy_balance.figure.search import Connecting, Drawing, Layout, PlacementSearch, Repair, TreePlacement
from hisim.postprocessing.energy_balance.figure.style import SankeyStyle
from hisim.postprocessing.energy_balance.figure.texts import FigureTexts, TextContext
from tests.sankey_test_systems import SankeySystems

pytestmark = pytest.mark.base

CONSTANTS = LayoutConstants()

#: Every synthetic system with something to draw.
SYSTEMS: List[Callable[[], SankeyInputs]] = [
    SankeySystems.heat_pump_house,
    SankeySystems.boiler_fed_twice,
    SankeySystems.storage_without_discharge,
    SankeySystems.no_building,
    SankeySystems.several_generators,
    SankeySystems.solar_collector_with_pump,
    SankeySystems.undeclared_with_gap,
    SankeySystems.car,
    SankeySystems.summer_tank,
    SankeySystems.hot_water_only,
]


@pytest.fixture(name="house", scope="module")
def fixture_house() -> DrawnGraph:
    """The drawn graph of the synthetic heat pump house."""
    return GraphBuilder.build(SankeySystems.heat_pump_house())


@pytest.fixture(name="parameters", scope="module")
def fixture_parameters(house: DrawnGraph) -> Parameters:
    """The house's parameters without adjustments or text."""
    styles = {node.name: (node.name, "#888888") for node in house.nodes}
    return ParameterBuilder.build(house, TextSizes({}, {}), Adjustments(), CONSTANTS, styles)


@pytest.fixture(name="layout", scope="module")
def fixture_layout() -> Layout:
    """The automatic layout of the house."""
    return PlacementSearch.run(SankeySystems.heat_pump_house())


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


def scaled(inputs: SankeyInputs, factor: float) -> SankeyInputs:
    """Return the inputs with every edge's energy multiplied by a factor."""
    return dataclasses.replace(inputs, links=tuple(dataclasses.replace(link, value_kwh=link.value_kwh * factor) for link in inputs.links))


# orientation


def test_nominal_axis() -> None:
    """Batteries are drawn downwards, everything else eastwards."""
    assert Orientation.nominal_axis(Role.ELECTRIC_STORAGE) is Direction.S
    assert Orientation.nominal_axis(Role.BUILDING) is Direction.E


def test_to_side_turns_compass_letters() -> None:
    """For a southward battery east is its left side; "tail" stays the tail."""
    assert Orientation.to_side("E", Direction.S) is Side.LEFT
    assert Orientation.to_side("tail", Direction.S) is Side.TAIL


def test_chain_side(house: DrawnGraph) -> None:
    """The link to the chain's next node takes the head, the one from the previous the tail; others get None."""
    heat_pump = house.node("HeatPump")
    to_buffer = next(f for f in heat_pump.flows if f.other_end == "Buffer")
    from_meter = next(f for f in heat_pump.flows if f.other_end == "Meter")
    ambient = next(f for f in heat_pump.flows if f.kind is FlowKind.BOUNDARY)
    assert Orientation.chain_side(house, heat_pump, to_buffer) == "head"
    assert Orientation.chain_side(house, heat_pump, from_meter) == "tail"
    assert Orientation.chain_side(house, heat_pump, ambient) is None


def test_sides_of_the_meter(house: DrawnGraph) -> None:
    """The meter takes the PV at its tail, grid import and export north, the heat pump at its head, household and battery south.

    A wrong side would cross the chain with the household's band.
    """
    sides = Orientation.sides(house, house.node("Meter"), None)
    assert sides["Meter|PV.ElectricityOutput"] is Side.TAIL
    assert sides["Meter|Meter.ElectricityFromGridInWatt"] is Side.LEFT
    assert sides["Meter|Meter.ElectricityToGridInWatt"] is Side.LEFT
    assert sides["Meter|HeatPump.ElectricalInputPowerSH"] is Side.HEAD
    assert sides["Meter|Household.ElectricalPowerConsumption"] is Side.RIGHT
    assert sides["Meter|Battery.DischargingPower"] is Side.RIGHT


def test_rule_and_context_rules(house: DrawnGraph) -> None:
    """The building's losses go head, north by rank; a buffer's loss goes north, its storage change south."""
    building, buffer = house.node("Building"), house.node("Buffer")
    opaque = next(f for f in building.flows if f.label == "Building.Opaque")
    vent = next(f for f in building.flows if f.label == "Building.Vent")
    assert Orientation.rule(house, Role.BUILDING, opaque, 0) == "head"
    assert Orientation.rule(house, Role.BUILDING, vent, 1) == "N"
    loss = next(f for f in buffer.flows if f.kind is FlowKind.LOSS)
    stored = next(f for f in buffer.flows if f.kind is FlowKind.STORAGE)
    assert Orientation.rule(house, Role.BUFFER, loss, 0) == "N"
    assert Orientation.rule(house, Role.BUFFER, stored, 0) == "S"
    assert Orientation.context_rule(Role.ELECTRIC_STORAGE, loss, 1) == "head"
    assert Orientation.context_rule(Role.BUFFER, loss, 0) is None


def test_meter_rule(house: DrawnGraph) -> None:
    """Grid export goes north from the chain root's meter; a household link has no special side (None)."""
    meter = house.node("Meter")
    export = next(f for f in meter.flows if f.label == "Meter.ElectricityToGridInWatt")
    household = next(f for f in meter.flows if f.other_end == "Household")
    assert Orientation.meter_rule(house, export) == "N"
    assert Orientation.meter_rule(house, household) is None


def test_parallel_seconds_and_their_sides(house: DrawnGraph) -> None:
    """The hot-water fuel beside the tree connection to the same boiler is a second link (D24).

    It is drawn from the meter's frame bottom into the boiler's frame bottom; the house has no second link.
    """
    graph = GraphBuilder.build(SankeySystems.boiler_fed_twice())
    dhw = next(link.link_id for link in graph.links if link.label == "B.EnergyDemandDhw")
    assert Orientation.parallel_seconds(graph) == {dhw}
    assert Orientation.sides(graph, graph.node("G"), None)["G|B.EnergyDemandDhw"] is Side.RIGHT
    assert Orientation.sides(graph, graph.node("B"), "B|B.EnergyDemandSh")["B|B.EnergyDemandDhw"] is Side.RIGHT
    assert not Orientation.parallel_seconds(house)


def test_tree_side_and_tank_rule() -> None:
    """A tank hung from its collector takes it at the tail; the generator's heat keeps the south side.

    Without these rules a second heat source stacks beside the tree connection at the tail, where the tree
    connection's arrow encloses it and no route reaches it (the summer weeks of the solar-thermal twins).
    """
    graph = GraphBuilder.build(SankeySystems.summer_tank())
    tank = graph.node("T")
    from_collector = next(f for f in tank.flows if f.other_end == "S")
    from_boiler = next(f for f in tank.flows if f.other_end == "B")
    assert Orientation.tree_side(graph, Role.HOT_WATER_TANK, from_collector) == "tail"
    assert Orientation.tree_side(graph, Role.HOT_WATER_TANK, from_boiler) == "S"
    assert Orientation.tree_side(graph, Role.HOUSEHOLD, from_boiler) == "N"
    assert Orientation.tank_rule(graph, from_boiler) == "S" and Orientation.tank_rule(graph, from_collector) is None
    assert Orientation.sides(graph, tank, from_collector.flow_id)[from_boiler.flow_id] is Side.RIGHT
    # On the chain the boiler's heat holds the tank's tail, so the collector's heat comes in from the north.
    chain = GraphBuilder.build(SankeySystems.hot_water_only())
    tank = chain.node("T")
    from_boiler = next(f for f in tank.flows if f.other_end == "B")
    from_collector = next(f for f in tank.flows if f.other_end == "S")
    assert Orientation.sides(chain, tank, from_boiler.flow_id)[from_collector.flow_id] is Side.LEFT


# parameters


def test_frame_rule() -> None:
    """Sides become orientations; the connecting orientation follows the hand layouts and refuses an opposed axis."""
    assert FrameRule.up(Direction.S) is Direction.E
    assert [FrameRule.orientation(side) for side in (Side.TAIL, Side.HEAD, Side.LEFT, Side.RIGHT)] == [0, 0, 1, -1]
    assert FrameRule.direction(Direction.E, -1) is Direction.S and FrameRule.direction(Direction.S, 1) is Direction.E
    assert FrameRule.connecting_orientation(Direction.S, Direction.S) == 0  # the battery below the meter
    assert FrameRule.connecting_orientation(Direction.E, Direction.S) == 1  # the household below the meter
    assert FrameRule.connecting_orientation(Direction.E, Direction.N) == -1  # the tank above the heat pump
    assert FrameRule.connecting_orientation(Direction.W, Direction.E) is None
    assert FrameRule.root_rotation(Direction.S) == -90


def test_side_order_emits_the_library_order() -> None:
    """Bottom groups are reversed, top, tail and head groups keep the wanted order."""
    flows = [flow("a", -1.0, -1), flow("b", -1.0, -1), flow("c", 1.0, 1), flow("d", 1.0, 1), flow("e", 1.0, 0), flow("f", -1.0, 0)]
    emitted = SideOrder.emit(flows, ["a", "b", "c", "d", "e", "f"])
    assert [f.flow_id for f in emitted] == ["e", "c", "d", "f", "b", "a"]


def test_side_order_read_back_from_the_library() -> None:
    """The wanted flow of each of the four groups is drawn nearest the trunk (or topmost) by the library."""
    flows = [flow("top_in_near", 1.0, 1), flow("top_in_far", 0.5, 1), flow("bottom_out_near", -1.0, -1), flow("bottom_out_far", -0.5, -1)]
    flows += [flow("tail_top", 1.0, 0), flow("tail_low", 0.5, 0), flow("head_top", -1.0, 0), flow("head_low", -1.0, 0)]
    emitted = SideOrder.emit(flows, [f.flow_id for f in flows])
    diagram = DiagramParams("N", None, None, None, Direction.E, 0, None, 4.0, (0.0, 0.0), "#888888", "N", emitted)
    drawn = SankeyBuild.build(Parameters(1.0, 0.0, [diagram], []), CONSTANTS, new_axes())["N"]
    assert abs(drawn.tip("top_in_near")[0]) < abs(drawn.tip("top_in_far")[0])
    assert abs(drawn.tip("bottom_out_near")[0]) < abs(drawn.tip("bottom_out_far")[0])
    assert drawn.tip("tail_top")[1] > drawn.tip("tail_low")[1]
    assert drawn.tip("head_top")[1] > drawn.tip("head_low")[1]


def test_adjustments() -> None:
    """Adjustments add up, shift trees and change their signature."""
    adjustments = Adjustments()
    before = adjustments.signature()
    Adjustments.add(adjustments.pathlength, "f", 0.5)
    Adjustments.add(adjustments.pathlength, "f", 0.5)
    adjustments.shift("T", 0.0, -1.0)
    adjustments.shift("T", 0.0, -1.0)
    assert adjustments.pathlength == {"f": 1.0} and adjustments.tree_shift == {"T": (0.0, -2.0)}
    assert adjustments.signature() != before


def test_sub_diagram_order_puts_the_pv_first(house: DrawnGraph) -> None:
    """The PV hung upstream of the meter is the first sub-diagram and the meter hangs from it."""
    order = ParameterBuilder.sub_diagram_order(house)
    assert order[0].placed == "PV" and order[0].anchor == ""
    assert (order[1].anchor, order[1].placed) == ("PV", "Meter")
    assert ParameterBuilder.tree_order(house.trees[0])[0].placed == "PV"


def test_build_gives_the_hand_layouts_frames(parameters: Parameters) -> None:
    """The battery points south, the household is horizontal fed from the frame's top, the chain continues east."""
    battery, household, heat_pump = parameters.diagram("Battery"), parameters.diagram("Household"), parameters.diagram("HeatPump")
    assert battery.axis is Direction.S and battery.flow(str(battery.connect)).orientation == 0
    assert household.axis is Direction.E and household.flow(str(household.connect)).orientation == 1
    assert heat_pump.prior == "Meter" and heat_pump.axis is Direction.E
    assert [c.label for c in parameters.connectors] == ["Building.HeatingByDevices", "Battery.DischargingPower"]


def test_build_orders_the_meter_bottom_side(parameters: Parameters) -> None:
    """On the meter's bottom side the battery is nearest the trunk, the household farthest (reversed in the list)."""
    meter = parameters.diagram("Meter")
    bottom_outputs = [f.flow_id for f in meter.flows if f.orientation == -1 and f.value_kwh < 0]
    assert bottom_outputs == ["Meter|Household.ElectricalPowerConsumption", "Meter|Battery.ChargingPower"]


def test_flow_of_and_axis_and_connection(house: DrawnGraph, parameters: Parameters) -> None:
    """A link's end is found at either node; a root takes its nominal axis, a lateral child its frame-rule orientation."""
    assert ParameterBuilder.flow_of(house, "Meter", 0) == "Meter|PV.ElectricityOutput"
    assert ParameterBuilder.axis_and_connection(house, "PV", None, None, [], {}, None) == (Direction.E, 0)
    axes = {d.component: d.axis for d in parameters.diagrams}
    household_axis = ParameterBuilder.axis_and_connection(house, "Household", "Meter", "Meter|Household.ElectricalPowerConsumption", parameters.diagrams, axes, Side.LEFT)
    assert household_axis == (Direction.E, 1)


def test_diagram_builds_one_sub_diagram(house: DrawnGraph, parameters: Parameters) -> None:
    """One sub-diagram gets every flow of its node, its trunk at least the minimum."""
    styles = {node.name: (node.name, "#888888") for node in house.nodes}
    axes = {d.component: d.axis for d in parameters.diagrams}
    diagram = ParameterBuilder.diagram(house, house.node("PV"), (None, None, None), [], axes, (TextSizes({}, {}), Adjustments(), CONSTANTS, 1.0, styles))
    assert len(diagram.flows) == len(house.node("PV").flows) and diagram.trunklength >= CONSTANTS.min_trunk


def test_flow_orientation_moves_impossible_sides() -> None:
    """An output on the tail goes to the bottom, an input on the head to the top; the rest follow the frame."""
    assert ParameterBuilder.flow_orientation(Side.TAIL, False) == -1
    assert ParameterBuilder.flow_orientation(Side.HEAD, True) == 1
    assert ParameterBuilder.flow_orientation(Side.LEFT, True) == 1


def test_pathlength(house: DrawnGraph) -> None:
    """The cut is short, a tree connection's output as long as its label plus pads, a cross-link a stub, a loss a branch."""
    pv, meter, buffer = house.node("PV"), house.node("Meter"), house.node("Buffer")
    sizes = TextSizes({}, {3: 6.0})
    cut = next(f for f in pv.flows if f.flow_id.endswith("|cut"))
    to_heat_pump = next(f for f in meter.flows if f.other_end == "HeatPump")
    discharge = next(f for f in meter.flows if f.label == "Battery.DischargingPower")
    loss = next(f for f in buffer.flows if f.label == "Buffer.StandbyHeatLoss")
    assert ParameterBuilder.pathlength(house, cut, Side.TAIL, None, sizes, CONSTANTS) == CONSTANTS.cut_stub
    expected = max(CONSTANTS.min_connection, sizes.link_label_width.get(to_heat_pump.link_id or 0, 0.0) + 2 * CONSTANTS.title_pad)
    assert ParameterBuilder.pathlength(house, to_heat_pump, Side.HEAD, None, sizes, CONSTANTS) == expected
    assert ParameterBuilder.pathlength(house, discharge, Side.RIGHT, None, sizes, CONSTANTS) == CONSTANTS.link_stub
    assert ParameterBuilder.pathlength(house, loss, Side.LEFT, None, sizes, CONSTANTS) == CONSTANTS.stub


def test_wanted_order(house: DrawnGraph, parameters: Parameters) -> None:
    """The connection comes first; links follow their slot rank (battery before household)."""
    meter = parameters.diagram("Meter")
    order = ParameterBuilder.wanted_order(house, house.node("Meter"), meter.flows, meter.connect)
    assert order[0] == "Meter|PV.ElectricityOutput"
    assert order.index("Meter|Battery.ChargingPower") < order.index("Meter|Household.ElectricalPowerConsumption")
    assert ParameterBuilder.SLOT_RANK[Role.ELECTRIC_STORAGE] < ParameterBuilder.SLOT_RANK[Role.HOUSEHOLD]


# search


def test_tree_placement(layout: Layout) -> None:
    """The house is one tree; a collector slides up, anything else down; the first tree needs no quadrant shift."""
    assert layout.drawing is not None
    assert TreePlacement.members(layout.parameters) == [[d.component for d in layout.parameters.diagrams]]
    box = TreePlacement.box_of(layout.drawing.drawn, ["Meter"])
    assert TreePlacement.slide_direction(layout.graph, "Meter") is Direction.S
    assert TreePlacement.quadrant_shift(layout.graph, "Meter", box, None, layout.drawing.drawn, "Meter", CONSTANTS) == (0.0, 0.0)


def test_tree_placement_moves_a_further_tree() -> None:
    """The boiler fed twice: the electricity meter's tree is moved below the chain, its top a tree margin under it."""
    layout = PlacementSearch.run(SankeySystems.boiler_fed_twice())
    assert layout.drawing is not None
    chain = TreePlacement.box_of(layout.drawing.drawn, ["G", "B", "Bld", "T"])
    further = TreePlacement.box_of(layout.drawing.drawn, ["E", "H"])
    assert further.y1 <= chain.y0 - CONSTANTS.tree_margin + 1e-6
    assert layout.parameters.diagram("E").at is not None


def test_place_moves_a_collector_above(context: TextContext) -> None:
    """A solar collector's tree goes above the chain (its quadrant), every other further tree below."""
    layout = PlacementSearch.run(SankeySystems.solar_collector_with_pump())
    assert layout.drawing is not None and not layout.faults
    roots = [d.component for d in layout.parameters.diagrams if d.prior is None]
    collector = next(name for name in roots if layout.graph.roles[name] is Role.SOLAR_COLLECTOR)
    assert TreePlacement.slide_direction(layout.graph, collector) is Direction.N
    del context


def test_connecting_route_all_with_labels(layout: Layout, context: TextContext) -> None:
    """Routing a fresh build around the reserved label spots gives every connector a route and an outline.

    A connector that finds its route only by ignoring the spots is listed as blind, so the repair can open a corridor.
    """
    axes = new_axes()
    parameters = layout.parameters
    drawing = Drawing(axes.figure, axes, SankeyBuild.build(parameters, CONSTANTS, axes))
    TreePlacement.place(layout.graph, parameters, drawing.drawn, Adjustments(), CONSTANTS)
    reserved = PlacementSearch.reserved_labels(context, parameters, drawing.drawn)
    assert reserved and all(label.key.startswith("reserve:") for label in reserved)
    Connecting.route_all(parameters, drawing, CONSTANTS, reserved)
    assert set(drawing.routes) == {c.link_id for c in parameters.connectors} == set(drawing.connector_outlines)
    assert set(drawing.blind) <= set(drawing.routes)  # a route found only by ignoring the label spots is listed


def test_repair_helpers(layout: Layout) -> None:
    """Paths climb to the root; projections and clearing distances are measured along a direction."""
    assert Repair.chain_to_root(layout.parameters, "Battery") == ["Battery", "Meter", "PV"]
    assert Repair.projection(Box(0, 0, 2, 4), Direction.S) == (-4.0, 0.0)
    assert Repair.clearing_distance(Box(0, -1, 1, 1), Box(0, 0, 1, 2), Direction.S, 1.0) == 4.0
    assert Repair.clearing_distance(Box(0, 5, 1, 6), Box(0, 0, 1, 2), Direction.S, 1.0) == 0.0


def test_repair_moves(layout: Layout) -> None:
    """A band overlap of two siblings lengthens the later one's connection; a label grows its path, sliding no tree."""
    assert layout.drawing is not None
    adjustments = Adjustments()
    Repair.band_move(layout.graph, layout.parameters, layout.drawing.drawn, ("Household", "Battery"), adjustments, CONSTANTS)
    assert sum(adjustments.pathlength.values()) >= CONSTANTS.grow
    adjustments = Adjustments()
    Repair.apply(layout.graph, layout.parameters, layout.drawing, [Overlap("label/band", "flow:Buffer|Buffer.StandbyHeatLoss", "diagram:X")], adjustments, CONSTANTS)
    assert "Buffer|Buffer.StandbyHeatLoss" in adjustments.pathlength and not adjustments.tree_shift


def test_cornered_connectors_move_the_label(layout: Layout) -> None:
    """A blind connector between two nodes of the chain's tree is cornered: no slide helps, the label on it moves."""
    assert layout.drawing is not None
    discharge = next(c for c in layout.parameters.connectors if c.label == "Battery.DischargingPower")
    keep = list(layout.drawing.blind)
    layout.drawing.blind[:] = [discharge.link_id]
    assert Repair.cornered(layout.parameters, layout.drawing, discharge.link_id)
    layout.drawing.blind[:] = []
    assert not Repair.cornered(layout.parameters, layout.drawing, discharge.link_id)
    layout.drawing.blind[:] = keep


def test_outer_sibling() -> None:
    """Of two parallel connectors into one child the one routed first (the wider) runs outside the other."""
    wide = ConnectorParams(1, "B.a", "G|B.a", "B|B.a", parallel=True)
    narrow = ConnectorParams(2, "B.b", "G|B.b", "B|B.b", parallel=True)
    other = ConnectorParams(3, "H.c", "G|H.c", "H|H.c")
    parameters = Parameters(1.0, 0.0, [], [wide, narrow, other])
    assert FigureTexts.outer_sibling(parameters, narrow) is wide
    assert FigureTexts.outer_sibling(parameters, wide) is None and FigureTexts.outer_sibling(parameters, other) is None


def test_seconds_inside_space_the_parallel_arrows() -> None:
    """Of two seconds into one boiler the wider runs outside and its stubs grow by one gap; the narrower's do not.

    The layout of that system has no fault: the narrower second's route and label fit between the two.
    """
    kinds = {"gas": "boundary", "out": "boundary", "G": "meter", "B": "component", "Bld": "component"}
    edges = [
        ("gas", "G", 14.0, "natural_gas", "meter_boundary", "G.in"),
        ("G", "B", 10.0, "natural_gas", "metered", "B.Sh"),
        ("G", "B", 3.0, "natural_gas", "metered", "B.Dhw"),
        ("G", "B", 1.0, "natural_gas", "metered", "B.Rod"),
        ("B", "Bld", 14.0, "space_heating_heat", "transfer", "Bld.in"),
        ("Bld", "out", 14.0, "room_heat", "boundary", "Bld.out"),
    ]
    inputs = SankeySystems.inputs(kinds, edges)
    graph = GraphBuilder.build(inputs)
    flows = {f.label: f for f in graph.node("G").flows}
    assert ParameterBuilder.seconds_inside(graph, flows["B.Dhw"]) == 1
    assert ParameterBuilder.seconds_inside(graph, flows["B.Rod"]) == 0 and ParameterBuilder.seconds_inside(graph, flows["B.Sh"]) == 0
    assert ParameterBuilder.pathlength(graph, flows["B.Dhw"], Side.RIGHT, None, TextSizes({}, {}), CONSTANTS) == CONSTANTS.link_stub + CONSTANTS.second_gap
    layout = PlacementSearch.run(inputs)
    assert not layout.faults and not layout.overlaps


def test_raster_covers_a_hairline() -> None:
    """A polygon narrower than a cell, whose outline holds no cell centre, still covers the cells it passes through."""
    hairline = np.array([(0.05, 0.0), (0.1, 0.0), (0.1, 2.0), (0.05, 2.0)])
    mask = GridSearch.raster(hairline, (-1.0, -1.0), (20, 20), 0.25)
    assert mask[4, 4:13].all() and mask.sum() >= 9


def test_dilate_is_a_square() -> None:
    """Growing one cell by one cell blocks its eight neighbours, diagonals included."""
    mask = np.zeros((5, 5), dtype=bool)
    mask[2, 2] = True
    grown = GridSearch.dilate(mask, 1)
    assert grown[1:4, 1:4].all() and grown.sum() == 9


def test_search_signatures_and_sizes(layout: Layout, context: TextContext) -> None:
    """Label signatures change with positions; text sizes cover every node and link."""
    assert layout.drawing is not None
    assert PlacementSearch.label_signature(layout.drawing.labels)
    sizes = PlacementSearch.text_sizes(context, {n.name: (n.name, "#888888") for n in layout.graph.nodes})
    assert set(sizes.title_width) == {n.name for n in layout.graph.nodes} and set(sizes.link_label_width) == {link.link_id for link in layout.graph.links}


def test_round_with_adjustments(context: TextContext, layout: Layout) -> None:
    """A round with the search's adjustments and the last round's labels draws and routes every connector."""
    again = PlacementSearch.round(context, layout.parameters, Adjustments(), layout.drawing.labels if layout.drawing else [])
    assert again.drawing is not None and all(r.shape != "failed" for r in again.drawing.routes.values())


def test_the_second_link_is_an_arrow_of_its_own() -> None:
    """A second fuel link to the boiler is drawn as its own connector below the tree connection (D24).

    It is flagged parallel, routed with a gap (not straight beside the first) and enters the boiler's notch with an
    arrowhead; the layout has no fault.
    """
    layout = PlacementSearch.run(SankeySystems.boiler_fed_twice())
    assert layout.drawing is not None and not layout.faults and not layout.overlaps
    connector = next(c for c in layout.parameters.connectors if c.label == "B.EnergyDemandDhw")
    assert connector.parallel and layout.drawing.routes[connector.link_id].shape not in ("straight", "failed")
    meter = layout.parameters.diagram("G")
    assert meter.flow("G|B.EnergyDemandDhw").orientation == -1 and meter.flow("G|B.EnergyDemandSh").orientation == 0
    assert any(c["parallel"] for c in json.loads(LayoutFile.dumps(layout))["connectors"])


# acceptance: §9.2 and §9.3 on the synthetic systems


@pytest.mark.parametrize("system", SYSTEMS, ids=lambda system: system.__name__)
@pytest.mark.parametrize("factor", [1.0, 1.25])
def test_every_synthetic_system_lays_out_cleanly(system: Callable[[], SankeyInputs], factor: float) -> None:
    """Every synthetic system lays out with no fault and no overlap, also with every flow 25 % larger (§9.2)."""
    layout = PlacementSearch.run(scaled(system(), factor))
    assert not layout.faults and not layout.overlaps and not layout.nothing_to_draw
    assert layout.rounds <= CONSTANTS.max_rounds


def test_nothing_to_draw() -> None:
    """A view without drawn nodes gives an empty layout and no figure."""
    layout = PlacementSearch.run(SankeySystems.nothing_to_draw())
    assert layout.nothing_to_draw and not layout.faults and layout.drawing is None


class Determinism:
    """The script a second process runs: lay out the heat pump house and print its layout file."""

    SCRIPT = (
        "from hisim.postprocessing.energy_balance.figure.render import LayoutFile\n"
        "from hisim.postprocessing.energy_balance.figure.search import PlacementSearch\n"
        "from tests.sankey_test_systems import SankeySystems\n"
        "print(LayoutFile.dumps(PlacementSearch.run(SankeySystems.heat_pump_house())), end='')\n"
    )


def test_the_layout_is_deterministic(tmp_path: Path) -> None:
    """The layout file of the house is the same twice in one process and in a second process (§9.3)."""
    first = LayoutFile.dumps(PlacementSearch.run(SankeySystems.heat_pump_house()))
    assert LayoutFile.dumps(PlacementSearch.run(SankeySystems.heat_pump_house())) == first
    repository = Path(__file__).resolve().parent.parent
    environment = {"PYTHONPATH": str(repository), "MPLCONFIGDIR": str(tmp_path), "PYTHONHASHSEED": "12345"}
    second = subprocess.run([sys.executable, "-c", Determinism.SCRIPT], capture_output=True, text=True, check=True, cwd=repository, env=environment)
    assert second.stdout == first
