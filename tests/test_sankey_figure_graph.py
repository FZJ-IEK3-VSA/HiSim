"""Direct tests of the Sankey figure's inputs, drawn graph, roles, chain, trees, metrics, style and command.

Every test builds a small synthetic view (``tests/sankey_test_systems.py``), so the numbers can be read off the test;
the figures of the real twins are laid out in the golden jobs.
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import json

import pytest

from hisim.postprocessing.energy_balance.figure.graph import Chains, DrawnGraph, DrawnNode, Flow, FlowKind, GraphBuilder, Link, Role, Roles, TreeEdge, Trees
from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputError, SankeyInputs, ViewLink, ViewNode
from hisim.postprocessing.energy_balance.figure.metrics import Metrics
from hisim.postprocessing.energy_balance.figure.style import NodeStyle, SankeyStyle
from hisim import cli
from tests.sankey_test_systems import Edge, SankeySystems


def make_node(name: str, flows: Sequence[Tuple[str, float, Optional[str], FlowKind, str]], kind: str = "component") -> DrawnNode:
    """Return a drawn node with flows given as (label, signed kWh, carrier, kind, other end)."""
    return DrawnNode(name, kind, "", tuple(Flow(f"{name}|{label}", name, label, value, carrier, flow_kind, other) for label, value, carrier, flow_kind, other in flows))


@pytest.fixture(name="house")
def fixture_house() -> DrawnGraph:
    """The drawn graph of the hand-built heat pump house."""
    return GraphBuilder.build(SankeySystems.heat_pump_house())


@pytest.mark.base
def test_inputs_parse_view_names_nodes() -> None:
    """The view's node indices are resolved to names; a wrong index would attach flows to the wrong node."""
    nodes, links = SankeyInputs.parse_view(
        {
            "nodes": [{"name": "a", "kind": "meter"}, {"name": "b", "kind": "component"}],
            "links": [{"source": 0, "target": 1, "value_kwh": 2, "carrier": "electricity", "kind": "metered", "label": "b.x"}],
        }
    )
    assert nodes[1] == ViewNode("b", "component")
    assert links[0] == ViewLink("a", "b", 2.0, "electricity", "metered", "b.x")
    with pytest.raises(SankeyInputError):
        SankeyInputs.parse_view({"nodes": [], "links": [{"source": 0, "target": 1, "value_kwh": 1, "kind": "x", "label": "l"}]})


@pytest.mark.base
def test_inputs_classes_include_undeclared() -> None:
    """An undeclared component's class is filed under its prefixed node name, so its style is found."""
    classes = SankeyInputs.classes_from_balance({"components": [{"component": "A", "class": "K"}], "undeclared": [{"component": "H", "class": "HeatDistribution"}]})
    assert classes == {"A": "K", "undeclared: H": "HeatDistribution"}


@pytest.mark.base
def test_inputs_flat_kpis_keeps_numbers_only() -> None:
    """Only numeric KPIs are kept, the first of a repeated name wins; a string value would break the caption's format."""
    document = {"B1": {"General": {"Rate": {"value": 70.5}, "Name": {"value": "x"}}}, "B2": {"General": {"Rate": {"value": 1.0}}}}
    assert SankeyInputs.flat_kpis(document) == {"Rate": 70.5}


@pytest.mark.base
def test_inputs_peak_powers_from_realized() -> None:
    """The PV's kWp comes from the realized ``power_in_watt``; a component without one is left out."""
    realized = {"components": {"PVSystem": {"config": {"power_in_watt": 22272.28}}, "Building": {"config": {"floor": 1}}}}
    assert SankeyInputs.peak_powers(realized) == {"PVSystem": 22272.28}


@pytest.mark.base
def test_inputs_kpi_by_prefix() -> None:
    """A KPI is found by the start of its name (the component suffix varies); a missing one is None, never 0."""
    inputs = SankeySystems.inputs({}, [], kpis={"Total energy from grid (ElectricityMeter)": 5.0})
    assert inputs.kpi("Total energy from grid") == 5.0
    assert inputs.kpi("Total energy to grid") is None


@pytest.mark.base
def test_inputs_from_result_directory(tmp_path: Path) -> None:
    """A directory with the view and the balance report is read, its run hours from steps times step length; a directory without them is refused."""
    (tmp_path / "energy_sankey.json").write_text(json.dumps({"overall": {"nodes": [{"name": "M", "kind": "meter"}], "links": []}}), encoding="utf-8")
    (tmp_path / "balance_report.json").write_text(json.dumps({"timesteps": 96, "seconds_per_timestep": 900, "verdict": "closes", "components": []}), encoding="utf-8")
    inputs = SankeyInputs.from_result_directory(tmp_path)
    assert inputs.run_hours == 24.0
    assert inputs.nodes == (ViewNode("M", "meter"),)
    with pytest.raises(SankeyInputError):
        SankeyInputs.from_result_directory(tmp_path / "missing")


@pytest.mark.base
def test_fold_turns_edges_into_links_and_terminal_flows() -> None:
    """A loss edge becomes a terminal output.

    The loss node's edge to the outdoors is dropped, a link appears at both ends with opposite signs, and an empty
    edge is left out.
    """
    kinds = {"A": "component", "B": "component", "A · L": "loss", "outdoors": "boundary"}
    edges = [
        ViewLink("A", "B", 10.0, "electricity", "transfer", "B.in"),
        ViewLink("A", "A · L", 2.0, "electricity", "loss", "A.L"),
        ViewLink("A · L", "outdoors", 2.0, "electricity", "loss", "A.L"),
        ViewLink("A", "B", 0.0001, "electricity", "transfer", "B.tiny"),
    ]
    links, flows = GraphBuilder.fold(edges, kinds, ["A", "B"])
    assert [link.label for link in links] == ["B.in"]
    assert [(f.value_kwh, f.kind) for f in flows["A"]] == [(-10.0, FlowKind.LINK), (-2.0, FlowKind.LOSS)]
    assert [(f.value_kwh, f.kind) for f in flows["B"]] == [(10.0, FlowKind.LINK)]


@pytest.mark.base
def test_append_makes_flow_ids_unique() -> None:
    """Two edges with one label at one node get distinct ids; equal ids would place one flow twice."""
    flows: Dict[str, List[Flow]] = {"A": []}
    edge = ViewLink("A", "x", 1.0, "electricity", "loss", "A.L")
    GraphBuilder.append(flows, "A", edge, -1.0, FlowKind.LOSS, "x", None)
    GraphBuilder.append(flows, "A", edge, -1.0, FlowKind.LOSS, "x", None)
    assert [f.flow_id for f in flows["A"]] == ["A|A.L", "A|A.L#2"]


@pytest.mark.base
def test_cut_solar_nets_the_pv_sunlight() -> None:
    """A PV's sunlight and solar loss become one cut input of their difference; a collector keeps its sunlight."""
    pv = make_node(
        "PV",
        [
            ("PV.Sun", 500.0, "solar", FlowKind.BOUNDARY, "sun"),
            ("PV.Loss", -400.0, "solar", FlowKind.LOSS, "PV · Loss"),
            ("PV.Out", -100.0, "electricity", FlowKind.LINK, "M"),
        ],
    )
    cut = GraphBuilder.cut_solar(pv.flows)
    assert [(f.kind, f.value_kwh) for f in cut] == [(FlowKind.CUT, 100.0), (FlowKind.LINK, -100.0)]
    collector = make_node(
        "C",
        [
            ("C.Sun", 50.0, "solar", FlowKind.BOUNDARY, "sun"),
            ("C.Pump", -1.0, "electricity", FlowKind.LOSS, "C · P"),
            ("C.Heat", -49.0, "domestic_hot_water_heat", FlowKind.LINK, "T"),
        ],
    )
    assert GraphBuilder.cut_solar(collector.flows) == list(collector.flows)


@pytest.mark.base
def test_close_adds_a_residual_only_beyond_the_tolerance() -> None:
    """A node that misses by 20 kWh gets a 20 kWh residual gain; one that misses by 0.1 kWh gets none."""
    node = make_node("H", [("in", 100.0, "space_heating_heat", FlowKind.LINK, "A"), ("out", -120.0, "space_heating_heat", FlowKind.LINK, "B")])
    flows, residual = GraphBuilder.close("H", node.flows)
    assert residual == pytest.approx(20.0)
    assert flows[-1].kind is FlowKind.RESIDUAL and flows[-1].value_kwh == pytest.approx(20.0)
    near = make_node("H", [("in", 100.0, "space_heating_heat", FlowKind.LINK, "A"), ("out", -100.1, "space_heating_heat", FlowKind.LINK, "B")])
    assert GraphBuilder.close("H", near.flows) == (list(near.flows), 0.0)


@pytest.mark.base
def test_build_gives_roles_chain_and_trees(house: DrawnGraph) -> None:
    """The heat pump house gets the expected roles, the chain meter -> heat pump -> buffer -> building, and one tree with the PV hanging upstream of the meter."""
    assert house.roles == {
        "PV": Role.PV,
        "Meter": Role.ELECTRICITY_METER,
        "HeatPump": Role.GENERATOR,
        "Buffer": Role.BUFFER,
        "Building": Role.BUILDING,
        "Household": Role.HOUSEHOLD,
        "Battery": Role.ELECTRIC_STORAGE,
    }
    assert house.chain == ("Meter", "HeatPump", "Buffer", "Building")
    assert len(house.trees) == 1
    assert TreeEdge("Meter", "PV", 0, upstream=True) in house.trees[0].edges


@pytest.mark.base
@pytest.mark.parametrize(
    "flows, kind, role",
    [
        ([("g", 5.0, "natural_gas", FlowKind.BOUNDARY, "supply")], "meter", Role.FUEL_METER),
        ([("v", -5.0, "vehicle_electricity", FlowKind.LINK, "Car")], "component", Role.VEHICLE_STORAGE),
        ([("v", 5.0, "vehicle_electricity", FlowKind.LINK, "CB")], "component", Role.VEHICLE),
        ([("c", 5.0, "cooling", FlowKind.LINK, "B"), ("a", -6.0, "ambient_heat", FlowKind.BOUNDARY, "sink")], "component", Role.COOLER),
        ([("t", -5.0, "domestic_hot_water_heat", FlowKind.BOUNDARY, "taps")], "component", Role.HOT_WATER_TANK),
        ([("i", 5.0, "space_heating_heat", FlowKind.LINK, "A"), ("o", -5.0, "space_heating_heat", FlowKind.LINK, "B")], "undeclared", Role.DISTRIBUTION),
        ([("i", 5.0, "hydrogen", FlowKind.LINK, "A"), ("o", -5.0, "electricity", FlowKind.LINK, "B")], "component", Role.CONVERTER),
        ([("s", 5.0, "solar", FlowKind.BOUNDARY, "sun"), ("h", -2.0, "domestic_hot_water_heat", FlowKind.LINK, "T")], "component", Role.SOLAR_COLLECTOR),
        (
            [("s", 5.0, "solar", FlowKind.BOUNDARY, "sun"), ("l", -2.0, "room_heat", FlowKind.LOSS, "x"), ("i", 1.0, "room_heat", FlowKind.LINK, "H")],
            "component",
            Role.BUILDING,
        ),
    ],
)
def test_roles_of_minimal_nodes(flows: List[Tuple[str, float, Optional[str], FlowKind, str]], kind: str, role: Role) -> None:
    """Each role of the table follows from a minimal node; a building with sunlight and room-heat losses is no collector."""
    assert Roles.of(make_node("N", flows, kind)) is role


@pytest.mark.base
def test_exchanges_electricity_with_one_node() -> None:
    """A battery charges from and discharges to the same meter; a node feeding another node does not exchange."""
    battery = make_node("B", [("c", 5.0, "electricity", FlowKind.LINK, "M"), ("d", -4.0, "electricity", FlowKind.LINK, "M")])
    feeder = make_node("F", [("c", 5.0, "electricity", FlowKind.LINK, "M"), ("d", -4.0, "electricity", FlowKind.LINK, "X")])
    assert Roles.exchanges_electricity_with_one_node(battery)
    assert not Roles.exchanges_electricity_with_one_node(feeder)


@pytest.mark.base
def test_carrier_rank_orders_the_upstream_walk() -> None:
    """Space heating ranks before room heat, before other heat, before anything else."""
    assert [Chains.carrier_rank(c) for c in ("space_heating_heat", "room_heat", "domestic_hot_water_heat", "electricity", None)] == [0, 1, 2, 3, 3]


@pytest.mark.base
def test_heat_input_counts_heat_links_only() -> None:
    """A building's heat input is its heat links; its sunlight (a boundary) does not count."""
    node = make_node(
        "B", [("h", 10.0, "space_heating_heat", FlowKind.LINK, "D"), ("r", 4.0, "room_heat", FlowKind.LINK, "H"), ("s", 9.0, "solar", FlowKind.BOUNDARY, "sun")]
    )
    assert Chains.heat_input_kwh(node) == 14.0


@pytest.mark.base
def test_main_chain_without_building_follows_the_largest_output() -> None:
    """Without a building the chain starts at the meter with the largest throughput and follows the largest outputs."""
    kinds = {"grid import": "boundary", "M": "meter", "E": "component", "H": "meter", "export": "boundary"}
    edges: List[Edge] = [
        ("grid import", "M", 10.0, "electricity", "meter_boundary", "M.in"),
        ("M", "E", 10.0, "electricity", "metered", "E.in"),
        ("E", "H", 7.0, "hydrogen", "metered", "H.in"),
        ("H", "export", 7.0, "hydrogen", "meter_boundary", "H.out"),
    ]
    graph = GraphBuilder.build(SankeySystems.inputs(kinds, edges))
    assert Chains.main_chain(graph) == ("M", "E", "H")


@pytest.mark.base
def test_trees_hang_nodes_from_their_largest_input(house: DrawnGraph) -> None:
    """The household and the battery hang from the meter (their largest input), not from each other."""
    edges = {edge.placed: edge for edge in house.trees[0].edges}
    assert edges["Household"].anchor == "Meter"
    assert edges["Battery"].anchor == "Meter"
    assert not edges["Battery"].upstream


@pytest.mark.base
def test_hanging_edge_waits_for_an_unattached_source(house: DrawnGraph) -> None:
    """A node whose largest input comes from a node not attached yet cannot hang yet."""
    assert Trees.hanging_edge(house, house.node("Battery"), ["Building"]) is None
    assert Trees.hanging_edge(house, house.node("Battery"), ["Meter"]) is not None


@pytest.mark.base
def test_next_root_prefers_a_meter(house: DrawnGraph) -> None:
    """Of the free nodes the meter starts the next tree, whatever the others' throughput."""
    assert Trees.next_root(house, ["HeatPump", "Buffer", "Building"]) == "Meter"


@pytest.mark.base
def test_grow_attaches_the_chain_first(house: DrawnGraph) -> None:
    """Growing from the chain root attaches the whole chain in order before any side node."""
    attached: List[str] = []
    edges = Trees.grow(house, "Meter", attached)
    assert [edge.placed for edge in edges[:3]] == ["HeatPump", "Buffer", "Building"]
    assert sorted(attached) == sorted(node.name for node in house.nodes)


@pytest.mark.base
def test_build_trees_starts_further_trees() -> None:
    """A meter not linked to the chain starts a tree of its own."""
    kinds = {
        "gas": "boundary",
        "G": "meter",
        "B": "component",
        "Bld": "component",
        "E": "meter",
        "H": "component",
        "grid": "boundary",
        "out": "boundary",
        "o2": "boundary",
    }
    edges: List[Edge] = [
        ("gas", "G", 10.0, "natural_gas", "meter_boundary", "G.in"),
        ("G", "B", 10.0, "natural_gas", "metered", "B.in"),
        ("B", "Bld", 10.0, "space_heating_heat", "transfer", "Bld.in"),
        ("Bld", "out", 10.0, "room_heat", "boundary", "Bld.out"),
        ("grid", "E", 3.0, "electricity", "meter_boundary", "E.in"),
        ("E", "H", 3.0, "electricity", "metered", "H.in"),
        ("H", "o2", 3.0, "electricity", "boundary", "H.out"),
    ]
    graph = GraphBuilder.build(SankeySystems.inputs(kinds, edges))
    assert [tree.root for tree in Trees.build(graph)] == ["G", "E"]


@pytest.mark.base
def test_format_kwh() -> None:
    """Energies are MWh from 10 MWh, whole kWh from 10 kWh, one decimal below; the sign is dropped."""
    assert [Metrics.format_kwh(v) for v in (26315.1, 1805.1, -1.84)] == ["26.3 MWh", "1,805 kWh", "1.8 kWh"]


@pytest.mark.base
def test_ratio_and_per_year() -> None:
    """A ratio over zero is None (the metric is left out); a week's 100 kWh is 5,214 kWh per year."""
    assert Metrics.ratio(1.0, 0.0) is None
    assert Metrics.ratio(3.0, 2.0) == 1.5
    assert Metrics.per_year(100.0, 168.0) == pytest.approx(5214.29, abs=0.01)
    with pytest.raises(ValueError):
        Metrics.per_year(1.0, 0.0)


@pytest.mark.base
def test_sums_split_a_generator(house: DrawnGraph) -> None:
    """The heat pump's heat out, electricity in and ambient heat in are separated."""
    assert Metrics.sums(house.node("HeatPump")) == (160.0, 40.0, 120.0, 0.0, 0.0)


@pytest.mark.base
def test_node_lines(house: DrawnGraph) -> None:
    """Each role's metric line is computed from the drawn flows; the household has none.

    The heat pump shows SPF 4.00 and 75 % ambient, the PV 10 kWp and 10 kWh/kWp, the battery an 86 % round trip
    (25 kWh out of 30 kWh charged less 1 kWh stored), the building 1 kWh/(m²·a).
    """
    inputs = SankeySystems.heat_pump_house()
    assert Metrics.node_lines(house, house.node("HeatPump"), inputs) == ["SPF 4.00 · ambient 75 %"]
    assert Metrics.node_lines(house, house.node("PV"), inputs) == ["10.0 kWp", "10 kWh/kWp·a"]
    assert Metrics.node_lines(house, house.node("Battery"), inputs) == ["round trip 86 %"]
    assert Metrics.node_lines(house, house.node("Building"), inputs) == ["heat to the rooms 1 kWh/(m²·a)"]
    assert not Metrics.node_lines(house, house.node("Household"), inputs)


@pytest.mark.base
def test_pv_and_building_lines_need_their_inputs(house: DrawnGraph) -> None:
    """Without a realized peak power or a floor area the line is left out, never estimated."""
    bare = SankeySystems.inputs({}, [])
    assert not Metrics.pv_lines(house.node("PV"), bare)
    assert not Metrics.building_lines(house.node("Building"), bare)


@pytest.mark.base
def test_generator_collector_storage_cooler_lines() -> None:
    """A boiler's efficiency, a trivial electric heater (none), a collector's efficiency and a cooler's EER."""
    boiler = make_node(
        "B", [("g", 100.0, "natural_gas", FlowKind.LINK, "G"), ("h", -95.0, "space_heating_heat", FlowKind.LINK, "S"), ("l", -5.0, "natural_gas", FlowKind.LOSS, "x")]
    )
    heater = make_node("E", [("e", 10.0, "electricity", FlowKind.LINK, "M"), ("h", -10.0, "space_heating_heat", FlowKind.LINK, "S")])
    collector = make_node("C", [("s", 40.0, "solar", FlowKind.BOUNDARY, "sun"), ("h", -10.0, "domestic_hot_water_heat", FlowKind.LINK, "T")])
    cooler = make_node("A", [("c", 30.0, "cooling", FlowKind.LINK, "B"), ("e", 10.0, "electricity", FlowKind.LINK, "M")])
    battery = make_node("Bat", [("c", 10.0, "electricity", FlowKind.LINK, "M"), ("d", -8.0, "electricity", FlowKind.LINK, "M")])
    assert Metrics.generator_line(boiler) == "efficiency 95.0 %"
    assert Metrics.generator_line(heater) is None
    assert Metrics.collector_line(collector) == "collector efficiency 25 %"
    assert Metrics.cooler_line(cooler) == "EER 3.0"
    assert Metrics.storage_line(battery) == "round trip 80 %"


@pytest.mark.base
def test_results_line(house: DrawnGraph) -> None:
    """The caption's results name the KPIs the run has and the heat delivered to the rooms; missing KPIs are left out."""
    inputs = SankeySystems.inputs({}, [], kpis={"Total energy from grid (M)": 50.0, "Self-sufficiency rate of electricity": 70.0})
    assert Metrics.results_line(house, inputs) == "Run results: grid import 50 kWh · self-sufficiency 70.0 % · heat delivered to the rooms 150 kWh."


@pytest.mark.base
def test_style_from_document_and_lookups() -> None:
    """A class entry wins, a meter falls back to its role's title, a port name may differ by direction."""
    style = SankeyStyle.from_document(
        {
            "kinds": {"Battery": {"title": "Battery", "color": "#90be6d"}},
            "port_names": {"Stored": {"in": "released", "out": "stored"}, "Battery.ChargingPower": "Battery charging"},
            "role_colors": {"electricity_meter": "#7aa6d8"},
            "role_titles": {"electricity_meter": "Electricity meter"},
        }
    )
    assert style.node_style("x", "Battery", Role.ELECTRIC_STORAGE) == NodeStyle("Battery", "#90be6d")
    assert style.node_style("grid-ElectricityMeter", "", Role.ELECTRICITY_METER) == NodeStyle("Electricity meter", "#7aa6d8")
    released = Flow("b|x", "b", "b.Stored", 2.0, "electricity", FlowKind.STORAGE, "b · stored")
    assert style.flow_name(released) == "released"
    charge = Flow("m|c", "m", "bat-Battery.ChargingPower", -2.0, "electricity", FlowKind.LINK, "bat-Battery")
    assert style.flow_name(charge, "Battery") == "Battery charging"
    with pytest.raises(ValueError):
        SankeyStyle.from_document({"kinds": {"X": {"title": "t"}}})


@pytest.mark.base
def test_style_load_reads_the_shipped_table() -> None:
    """The shipped style table loads and names the heat pump; a broken file would stop every figure."""
    assert SankeyStyle.load().kinds["MoreAdvancedHeatPumpHPLib"].title == "Heat pump"


@pytest.mark.base
def test_link_dataclass_and_flow_properties() -> None:
    """A flow's direction and magnitude follow its sign; a drawn node's throughput is the larger side."""
    node = make_node("N", [("i", 5.0, "electricity", FlowKind.LINK, "A"), ("o", -3.0, "electricity", FlowKind.LINK, "B")])
    assert node.flows[0].is_input and not node.flows[1].is_input
    assert node.flows[1].magnitude_kwh == 3.0
    assert node.throughput_kwh == 5.0
    assert Link(0, "A", "N", 5.0, "electricity", "N.i").source == "A"


@pytest.mark.base
@pytest.mark.parametrize(
    "factory, chain, roles",
    [
        (
            SankeySystems.boiler_fed_twice,
            ("G", "B", "Bld"),
            {"G": Role.FUEL_METER, "B": Role.GENERATOR, "T": Role.HOT_WATER_TANK, "E": Role.ELECTRICITY_METER, "H": Role.HOUSEHOLD},
        ),
        (SankeySystems.storage_without_discharge, ("M", "H"), {"Bat": Role.CONVERTER}),
        (SankeySystems.no_building, ("M", "Elz", "HM"), {"T": Role.CONVERTER, "HM": Role.FUEL_METER}),
        (SankeySystems.several_generators, ("E", "HP", "Buf", "Bld"), {"Boiler": Role.GENERATOR, "Buf": Role.BUFFER}),
        (SankeySystems.solar_collector_with_pump, ("G", "B", "Bld"), {"C": Role.SOLAR_COLLECTOR, "T": Role.HOT_WATER_TANK}),
        (SankeySystems.undeclared_with_gap, ("G", "B", "undeclared: D", "Bld"), {"undeclared: D": Role.DISTRIBUTION}),
        (SankeySystems.car, ("M", "H"), {"CB": Role.VEHICLE_STORAGE, "Car": Role.VEHICLE}),
    ],
)
def test_synthetic_systems_roles_and_chains(factory: Callable[[], SankeyInputs], chain: Tuple[str, ...], roles: Dict[str, Role]) -> None:
    """Each synthetic case gets its chain and the roles its rule needs; a wrong criterion would misplace the node."""
    graph = GraphBuilder.build(factory())
    assert graph.chain == chain
    for name, role in roles.items():
        assert graph.roles[name] is role


@pytest.mark.base
def test_trees_of_the_synthetic_systems() -> None:
    """Further trees, roots and upstream sources come out as the rules say.

    The fuel system's electricity meter starts a further tree; the collector is a root of its own; the car hangs from
    its battery; the source upstream of the electrolyzer's meter hangs in front of it.
    """
    assert [tree.root for tree in GraphBuilder.build(SankeySystems.boiler_fed_twice()).trees] == ["G", "E"]
    assert "C" in [tree.root for tree in GraphBuilder.build(SankeySystems.solar_collector_with_pump()).trees]
    car = {edge.placed: edge.anchor for tree in GraphBuilder.build(SankeySystems.car()).trees for edge in tree.edges}
    assert car["Car"] == "CB" and car["CB"] == "M"
    upstream = [edge for tree in GraphBuilder.build(SankeySystems.no_building()).trees for edge in tree.edges if edge.upstream]
    assert [(edge.anchor, edge.placed) for edge in upstream] == [("M", "T")]


@pytest.mark.base
def test_undeclared_gap_is_closed_by_a_residual() -> None:
    """The undeclared distribution that gives 0.6 kWh more than it receives gets a 0.6 kWh residual gain."""
    graph = GraphBuilder.build(SankeySystems.undeclared_with_gap())
    assert graph.residual_kwh_of == {"undeclared: D": pytest.approx(0.6)}


@pytest.mark.base
def test_graph_lines() -> None:
    """The command's description names the chain, every node's role and parent, and the residuals."""
    lines = cli.SankeyCommand.graph_lines(GraphBuilder.build(SankeySystems.undeclared_with_gap()))
    assert lines[0] == "chain: G > B > undeclared: D > Bld"
    assert any(line.startswith("B ") and "generator" in line and "parent G" in line for line in lines)
    assert lines[-1] == "residual of undeclared: D: 0.6 kWh"
    assert cli.SankeyCommand.graph_lines(GraphBuilder.build(SankeySystems.nothing_to_draw())) == ["the overall view has no component, meter or undeclared node to draw"]


@pytest.mark.base
def test_sankey_command(tmp_path: Path) -> None:
    """``hisim energy-system sankey`` reads a result directory and exits 0; a directory without inputs exits 2."""
    view = {
        "overall": {
            "nodes": [{"name": "grid", "kind": "boundary"}, {"name": "M", "kind": "meter"}],
            "links": [{"source": 0, "target": 1, "value_kwh": 1.0, "carrier": "electricity", "kind": "meter_boundary", "label": "M.in"}],
        }
    }
    (tmp_path / "energy_sankey.json").write_text(json.dumps(view), encoding="utf-8")
    (tmp_path / "balance_report.json").write_text(json.dumps({"timesteps": 4, "seconds_per_timestep": 900, "verdict": "closes"}), encoding="utf-8")
    assert cli.main(["energy-system", "sankey", str(tmp_path)]) == 0
    assert cli.main(["energy-system", "sankey", str(tmp_path / "missing")]) == 2
