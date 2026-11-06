"""How a view of the energy Sankey is drawn: solar losses netted, two-way exchanges netted, empty flows dropped, small
nodes merged, one-line labels, and every node placed in an explicit column with room for its label.

Spec ``roadmap/complete_energy_sankeys_spec.md`` §4.8 (owner amendments of 2026-11-06, beads
hisim-sankey-readability-8e20 and hisim-sankey-layout-48b8). Every function of :class:`SankeyDrawing` and
:class:`SankeyLayout` is tested on hand-built links whose numbers can be read off the test; ``energy_sankey.json`` is
never changed by the drawing.
"""

from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pytest

from hisim.postprocessing.energy_balance.sankey import (
    DrawnNode,
    EdgeKind,
    MergedNode,
    NodeKind,
    SankeyDiagram,
    SankeyDrawing,
    SankeyLayout,
    SankeyLink,
)


class K:
    """Short names of the carriers and node kinds the hand-built links use."""

    SOLAR: ClassVar[str] = "solar"
    ELECTRICITY: ClassVar[str] = "electricity"
    ROOM_HEAT: ClassVar[str] = "room_heat"
    B: ClassVar[NodeKind] = NodeKind.BOUNDARY
    C: ClassVar[NodeKind] = NodeKind.COMPONENT
    L: ClassVar[NodeKind] = NodeKind.LOSS
    S: ClassVar[NodeKind] = NodeKind.STORAGE
    M: ClassVar[NodeKind] = NodeKind.METER


def link(
    source: str, target: str, kilowatt_hours: float = 1.0, carrier: Optional[str] = K.ELECTRICITY, kind: EdgeKind = EdgeKind.TRANSFER, label: str = ""
) -> SankeyLink:
    """One link; its label is "source -> target" unless given."""
    return SankeyLink(source, target, kilowatt_hours, carrier, label or f"{source} -> {target}", kind)


def pairs(links: List[SankeyLink]) -> Dict[Tuple[str, str], float]:
    """The links by (source, target)."""
    return {(item.source, item.target): item.kilowatt_hours for item in links}


def pv_house() -> Tuple[List[SankeyLink], Dict[str, NodeKind]]:
    """A PV system that turns 100 kWh of sunlight into 20 kWh, losing 80 kWh, beside 10 kWh of window gains."""
    links = [
        link("sun", "PV", 100.0, K.SOLAR, EdgeKind.BOUNDARY),
        link("PV", "PV · ModuleLossInWatt", 80.0, K.SOLAR, EdgeKind.LOSS),
        link("PV · ModuleLossInWatt", "outdoors", 80.0, K.SOLAR, EdgeKind.LOSS),
        link("PV", "Meter", 20.0, K.ELECTRICITY, EdgeKind.METERED),
        link("sun", "Building", 10.0, K.SOLAR, EdgeKind.BOUNDARY),
        link("Building", "Building · VentilationLossInWatt", 10.0, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Building · VentilationLossInWatt", "outdoors", 10.0, K.ROOM_HEAT, EdgeKind.LOSS),
    ]
    kinds = {"sun": K.B, "PV": K.C, "PV · ModuleLossInWatt": K.L, "outdoors": K.B, "Meter": K.M, "Building": K.C, "Building · VentilationLossInWatt": K.L}
    return links, kinds


@pytest.mark.base
def test_the_overall_drawing_starts_a_pv_system_at_its_electricity() -> None:
    """The PV's 80 kWh solar loss and its loss node go; sun -> PV carries the 20 kWh it converts; the rest stays."""
    links, kinds = pv_house()
    netted = SankeyDrawing.net_solar_losses(links, kinds)
    assert pairs(netted) == {
        ("sun", "PV"): pytest.approx(20.0),
        ("PV", "Meter"): 20.0,
        ("sun", "Building"): 10.0,
        ("Building", "Building · VentilationLossInWatt"): 10.0,
        ("Building · VentilationLossInWatt", "outdoors"): 10.0,
    }
    assert netted[0].label == "sun -> PV, net of PV's solar losses (80.0 kWh)"


@pytest.mark.base
def test_a_component_that_loses_all_its_sunlight_is_not_drawn_from_the_sun() -> None:
    """A collector that loses all 5 kWh it receives draws no link from the sun."""
    links = [
        link("sun", "Collector", 5.0, K.SOLAR, EdgeKind.BOUNDARY),
        link("Collector", "Collector · CollectorHeatLoss", 5.0, K.SOLAR, EdgeKind.LOSS),
        link("Collector · CollectorHeatLoss", "outdoors", 5.0, K.SOLAR, EdgeKind.LOSS),
    ]
    kinds = {"sun": K.B, "Collector": K.C, "Collector · CollectorHeatLoss": K.L, "outdoors": K.B}
    assert not SankeyDrawing.net_solar_losses(links, kinds)


def battery_ring() -> Tuple[List[SankeyLink], Dict[str, NodeKind]]:
    """A battery charged with 3,488.5 kWh from the meter that gives 2,541.2 kWh back, 11.2 kWh from its store, and
    loses 958.5 kWh; the meter takes 1,000 kWh from the grid."""
    links = [
        link("grid import", "Meter", 1000.0, kind=EdgeKind.METER_BOUNDARY),
        link("Meter", "Battery", 3488.5, kind=EdgeKind.METERED, label="Battery.ChargingPower"),
        link("Battery", "Meter", 2541.2, kind=EdgeKind.METERED, label="Battery.DischargingPower"),
        link("Battery · stored", "Battery", 11.2, kind=EdgeKind.STORAGE),
        link("Battery", "Battery · CellLossInWatt", 958.5, kind=EdgeKind.LOSS),
        link("Battery · CellLossInWatt", "outdoors", 958.5, kind=EdgeKind.LOSS),
    ]
    kinds = {"grid import": K.B, "Meter": K.M, "Battery": K.C, "Battery · stored": K.S, "Battery · CellLossInWatt": K.L, "outdoors": K.B}
    return links, kinds


@pytest.mark.base
def test_an_exchange_both_ways_is_drawn_as_its_net_with_both_flows_in_the_hover_text() -> None:
    """Meter -> Battery 3,488.5 and Battery -> Meter 2,541.2 kWh become one Meter -> Battery link of 947.3 kWh, at the
    place of the first; the rest stays as it is."""
    links, _ = battery_ring()
    netted = SankeyDrawing.net_two_way(links)
    assert [(item.source, item.target) for item in netted] == [
        ("grid import", "Meter"),
        ("Meter", "Battery"),
        ("Battery · stored", "Battery"),
        ("Battery", "Battery · CellLossInWatt"),
        ("Battery · CellLossInWatt", "outdoors"),
    ]
    assert (netted[1].source, netted[1].target, netted[1].carrier, netted[1].kind) == ("Meter", "Battery", K.ELECTRICITY, EdgeKind.METERED)
    assert netted[1].kilowatt_hours == pytest.approx(947.3)
    assert netted[1].label == "net of both ways:<br>Battery.ChargingPower: 3,488 kWh<br>Battery.DischargingPower: 2,541 kWh (back)"


@pytest.mark.base
def test_the_net_of_an_exchange_goes_the_way_of_the_larger_flow_summed_over_parallel_links() -> None:
    """A -> B 2 + 3 kWh against B -> A 8 kWh is B -> A 3 kWh; a link of 0 kWh stands for an exchange that nets to zero."""
    links = [link("A", "B", 2.0, label="a1"), link("B", "A", 8.0, label="b"), link("A", "B", 3.0, label="a2"), link("C", "D", 4.0), link("D", "C", 4.0)]
    netted = SankeyDrawing.net_two_way(links)
    assert [(item.source, item.target, item.kilowatt_hours) for item in netted] == [("B", "A", 3.0), ("C", "D", 0.0)]
    assert netted[0].label == "net of both ways:<br>a1: 2 kWh (back)<br>b: 8 kWh<br>a2: 3 kWh (back)"


@pytest.mark.base
def test_one_way_links_and_flows_of_different_carriers_are_not_netted() -> None:
    """Two parallel links one way stay two links; heat one way and electricity the other are different energies."""
    links = [link("A", "B", 2.0), link("A", "B", 3.0), link("B", "A", 1.0, K.ROOM_HEAT)]
    assert SankeyDrawing.net_two_way(links) == links


@pytest.mark.base
def test_links_below_the_smallest_flow_are_dropped() -> None:
    """A 2.4e-7 kWh link goes, a 1 Wh link stays."""
    links = [link("A", "B", 2.4e-7), link("A", "C", 0.001), link("A", "D", 5.0)]
    assert pairs(SankeyDrawing.drop_empty(links, 0.001)) == {("A", "C"): 0.001, ("A", "D"): 5.0}


@pytest.mark.base
def test_a_node_is_as_large_as_the_larger_of_its_in_and_outflow() -> None:
    """The battery takes 3,505 kWh and gives 2,557 kWh: 3,505 kWh; nodes in the order the links name them."""
    links = [link("Meter", "Battery", 3505.0), link("Battery", "Meter", 2557.0), link("Battery", "loss", 948.0)]
    assert SankeyDrawing.throughputs(links) == {"Meter": 3505.0, "Battery": 3505.0, "loss": 948.0}


@pytest.mark.base
def test_small_nodes_linked_to_each_other_form_one_group() -> None:
    """A small battery with its loss and storage nodes is one group, a lone loss node another."""
    links = [
        link("Meter", "Bat"),
        link("Bat", "Bat · Loss"),
        link("Bat · Loss", "outdoors"),
        link("Bat", "Bat · stored"),
        link("House", "House · Loss"),
        link("House · Loss", "outdoors"),
    ]
    small = {"Bat", "Bat · Loss", "Bat · stored", "House · Loss"}
    assert SankeyDrawing.small_groups(links, small) == [["Bat", "Bat · Loss", "Bat · stored"], ["House · Loss"]]


def house_with_small_flows() -> Tuple[List[SankeyLink], Dict[str, NodeKind]]:
    """A house whose largest flow is 1,000 kWh, with small losses, small stores, a small grid import and a residual.

    At a threshold of 5 kWh: two loss nodes of 2 and 1 kWh reach "outdoors", two stores give 3 and 0.5 kWh, the grid
    gives 2 kWh (a boundary, never merged) and the heater has a 0.2 kWh residual (unaccounted, never merged).
    """
    links = [
        link("Meter", "Heater", 1000.0, K.ELECTRICITY, EdgeKind.METERED),
        link("grid import", "Meter", 2.0, K.ELECTRICITY, EdgeKind.METER_BOUNDARY),
        link("Heater", "Building", 800.0, K.ROOM_HEAT),
        link("Heater", "Heater · CasingLossInWatt", 197.8, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Heater · CasingLossInWatt", "outdoors", 197.8, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Heater", "Heater · PumpLossInWatt", 2.0, K.ELECTRICITY, EdgeKind.LOSS),
        link("Heater · PumpLossInWatt", "outdoors", 2.0, K.ELECTRICITY, EdgeKind.LOSS),
        link("Building", "Building · GainsLostInWatt", 1.0, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Building · GainsLostInWatt", "outdoors", 1.0, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Building", "Building · VentilationLossInWatt", 802.0, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Building · VentilationLossInWatt", "outdoors", 802.0, K.ROOM_HEAT, EdgeKind.LOSS),
        link("Building · stored", "Building", 3.0, K.ROOM_HEAT, EdgeKind.STORAGE),
        link("Heater · stored", "Heater", 0.5, K.ROOM_HEAT, EdgeKind.STORAGE),
        link("Heater", "unaccounted", 0.2, None, EdgeKind.RESIDUAL),
    ]
    kinds = {
        "Meter": K.M,
        "Heater": K.C,
        "grid import": K.B,
        "Building": K.C,
        "Heater · CasingLossInWatt": K.L,
        "outdoors": K.B,
        "Heater · PumpLossInWatt": K.L,
        "Building · GainsLostInWatt": K.L,
        "Building · VentilationLossInWatt": K.L,
        "Building · stored": K.S,
        "Heater · stored": K.S,
        "unaccounted": NodeKind.UNACCOUNTED,
    }
    return links, kinds


@pytest.mark.base
def test_small_nodes_merge_into_one_other_node_per_sink() -> None:
    """The two small loss nodes reach outdoors through one "other" node, the two stores come from one source node.

    Per carrier one link: the heater's pump loss (electricity) and the building's lost gains (room heat) go into
    "other → outdoors" on links of their own and leave it on one link per carrier. The grid import and the residual
    stay as they are, though both are below the threshold.
    """
    links, kinds = house_with_small_flows()
    drawn, merged = SankeyDrawing.merge_small(links, kinds, 5.0)
    assert pairs(drawn) == {
        ("Meter", "Heater"): 1000.0,
        ("grid import", "Meter"): 2.0,
        ("Heater", "Building"): 800.0,
        ("Heater", "Heater · CasingLossInWatt"): 197.8,
        ("Heater · CasingLossInWatt", "outdoors"): 197.8,
        ("Heater", "other → outdoors"): 2.0,
        ("other → outdoors", "outdoors"): 1.0,
        ("Building", "other → outdoors"): 1.0,
        ("Building", "Building · VentilationLossInWatt"): 802.0,
        ("Building · VentilationLossInWatt", "outdoors"): 802.0,
        ("other (sources)", "Building"): 3.0,
        ("other (sources)", "Heater"): 0.5,
        ("Heater", "unaccounted"): 0.2,
    }
    into_outdoors = [item for item in drawn if item.source == "other → outdoors"]
    assert [(item.carrier, item.kilowatt_hours) for item in into_outdoors] == [(K.ELECTRICITY, 2.0), (K.ROOM_HEAT, 1.0)]
    assert merged == [
        MergedNode("other → outdoors", ("Heater · PumpLossInWatt", "Building · GainsLostInWatt"), 2),
        MergedNode("other (sources)", ("Building · stored", "Heater · stored"), 2),
    ]


@pytest.mark.base
def test_merged_links_list_what_they_merge_in_their_hover_text() -> None:
    """Two small stores of one component give to it on one link, whose label lists both with their energy."""
    links = [link("Grid", "Tank", 100.0), link("Tank · stored A", "Tank", 0.5, label="Tank.A"), link("Tank · stored B", "Tank", 0.25, label="Tank.B")]
    kinds = {"Grid": K.B, "Tank": K.C, "Tank · stored A": K.S, "Tank · stored B": K.S}
    drawn, merged = SankeyDrawing.merge_small(links, kinds, 1.0)
    assert drawn[1] == SankeyLink("other (sources)", "Tank", 0.75, K.ELECTRICITY, "Tank.A: 0.5 kWh<br>Tank.B: 0.25 kWh", EdgeKind.TRANSFER)
    assert merged == [MergedNode("other (sources)", ("Tank · stored A", "Tank · stored B"), 2)]


@pytest.mark.base
def test_a_small_group_that_only_takes_goes_to_one_sink_node_and_one_linked_to_nothing_stays() -> None:
    """A small store that only takes is merged into "other (sinks)"; two small components linked only to each other
    are drawn as they are, since there is nothing to merge them into."""
    links = [link("Grid", "Tank", 100.0), link("Tank", "Tank · stored", 99.0), link("Tank", "Tank · Loss", 1.0), link("A", "B", 0.5)]
    kinds = {"Grid": K.B, "Tank": K.C, "Tank · stored": K.S, "Tank · Loss": K.L, "A": K.C, "B": K.C}
    drawn, merged = SankeyDrawing.merge_small(links, kinds, 2.0)
    assert pairs(drawn) == {("Grid", "Tank"): 100.0, ("Tank", "Tank · stored"): 99.0, ("Tank", "other (sinks)"): 1.0, ("A", "B"): 0.5}
    assert merged == [MergedNode("other (sinks)", ("Tank · Loss",), 1)]


@pytest.mark.base
def test_an_output_name_is_drawn_in_words() -> None:
    """Unit suffix left out, CamelCase split, words in lower case, acronyms kept; the overrides win."""
    assert SankeyDrawing.words("ModuleAndInverterLossInWatt") == "module and inverter loss"
    assert SankeyDrawing.words("StoredEnergyIncreaseInWattHour") == "stored energy increase"
    assert SankeyDrawing.words("HydrogenFlowInKilogramPerSecond") == "hydrogen flow"
    assert SankeyDrawing.words("ThermalPowerConsumptionDHW") == "thermal power consumption DHW"
    assert SankeyDrawing.words("DHWStorageLoss") == "DHW storage loss"
    assert SankeyDrawing.words("CombustionHeatLoss") == "combustion heat loss"
    assert SankeyDrawing.words("stored") == "stored"
    assert SankeyDrawing.words("InWatt") == "in watt"
    assert SankeyDrawing.words("TransmissionLossOpaqueInWatt") == "opaque transmission loss"
    assert set(SankeyDrawing.DISPLAY_NAMES) == {
        "ApplianceElectricityNotReleasedIndoorsInWatt",
        "TransmissionLossOpaqueInWatt",
        "TransmissionLossWindowsAndDoorsInWatt",
    }


@pytest.mark.base
def test_a_loss_node_shows_its_port_in_words_and_every_other_node_its_name() -> None:
    """Components keep the name they were given; only a loss node's port part changes."""
    assert SankeyDrawing.display_name("PVSystem · ModuleAndInverterLossInWatt", K.L) == "PVSystem · module and inverter loss"
    assert SankeyDrawing.display_name("MoreAdvancedHeatPumpHPLib", K.C) == "MoreAdvancedHeatPumpHPLib"
    assert SankeyDrawing.display_name("Battery · stored", K.S) == "Battery · stored"
    assert SankeyDrawing.display_name("LossWithoutSeparator", K.L) == "LossWithoutSeparator"


@pytest.mark.base
def test_an_amount_is_whole_kilowatt_hours_from_ten_on_and_two_digits_below() -> None:
    """Large values read like the report; a short run's small values keep two significant digits."""
    assert [SankeyDrawing.amount(value) for value in (126924.6, 10.0, 9.96, 1.74, 0.025)] == ["126,925", "10", "10", "1.7", "0.025"]


@pytest.mark.base
def test_a_label_is_one_line_with_whole_kilowatt_hours_from_ten_on() -> None:
    """A printed diagram has no hover text, so the label carries the energy; below 10 kWh two significant digits."""
    assert SankeyDrawing.label("grid import", 1830.2) == "grid import · 1,830 kWh"
    assert SankeyDrawing.label("Battery · stored", 11.1) == "Battery · stored · 11 kWh"
    assert SankeyDrawing.label("Tank · stored", 1.74) == "Tank · stored · 1.7 kWh"
    assert SankeyDrawing.label("Pump", 0.025) == "Pump · 0.025 kWh"
    assert "<br>" not in SankeyDrawing.label("a", 1.0)


def diagram_of(links: List[SankeyLink], kinds: Dict[str, NodeKind]) -> Dict[str, Any]:
    """The links as ``energy_sankey.json`` writes them."""
    return SankeyDiagram.data(links, kinds)


@pytest.mark.base
def test_links_read_back_from_the_json_are_the_links_written() -> None:
    """:meth:`SankeyDrawing.links_of` is the inverse of :meth:`SankeyDiagram.data`."""
    links, kinds = house_with_small_flows()
    assert SankeyDrawing.links_of(diagram_of(links, kinds)) == (links, kinds)


@pytest.mark.base
def test_only_the_overall_drawing_nets_the_solar_losses() -> None:
    """``overall`` starts the PV at its electricity; the ``solar`` view keeps the split; both label each node once."""
    links, kinds = pv_house()
    overall = SankeyDrawing.of(diagram_of(links, kinds), "overall")
    assert "PV · ModuleLossInWatt" not in {node.name for node in overall.nodes}
    assert [node.label for node in overall.nodes] == [
        "sun · 30 kWh",
        "PV · 20 kWh",
        "Meter · 20 kWh",
        "Building · 10 kWh",
        "Building · ventilation loss · 10 kWh",
        "outdoors · 10 kWh",
    ]
    solar = SankeyDrawing.of(diagram_of(links, kinds), "solar")
    assert pairs(solar.links)[("PV", "PV · ModuleLossInWatt")] == 80.0


@pytest.mark.base
def test_a_drawing_merges_below_half_a_percent_of_its_largest_flow_and_labels_the_other_node() -> None:
    """Largest flow 1,000 kWh: the threshold is 5 kWh; the "other" nodes are labelled and list what they stand for."""
    links, kinds = house_with_small_flows()
    drawing = SankeyDrawing.of(diagram_of(links, kinds), "electricity")
    nodes = {node.name: node for node in drawing.nodes}
    assert nodes["other → outdoors"].label == "other (2 flows) · 3 kWh"
    assert nodes["other → outdoors"].hover == "other (2 flows):<br>Heater · pump loss<br>Building · gains lost"
    assert nodes["other → outdoors"].colour == SankeyDrawing.OTHER_COLOUR
    assert nodes["other (sources)"].label == "other (2 flows) · 3.5 kWh"
    assert nodes["Heater · CasingLossInWatt"].hover == "Heater · CasingLossInWatt"
    assert nodes["Heater · CasingLossInWatt"].label == "Heater · casing loss · 198 kWh"
    assert nodes["Heater · CasingLossInWatt"].colour == SankeyDiagram.COLOURS[K.L]


@pytest.mark.base
def test_an_other_node_that_stands_for_one_node_keeps_its_name_and_colour() -> None:
    """The tank's 0.2 kWh loss is the only small node toward "other (sinks)": it is labelled as the loss node."""
    links = [link("Grid", "Tank", 100.0), link("Tank", "Tank · stored", 99.8), link("Tank", "Tank · LossInWatt", 0.2)]
    kinds = {"Grid": K.B, "Tank": K.C, "Tank · stored": K.S, "Tank · LossInWatt": K.L}
    nodes = {node.name: node for node in SankeyDrawing.of(diagram_of(links, kinds), "electricity").nodes}
    assert nodes["other (sinks)"] == DrawnNode("other (sinks)", "Tank · loss · 0.2 kWh", "Tank · LossInWatt", SankeyDiagram.COLOURS[K.L])


@pytest.mark.base
def test_the_figure_draws_the_labels_and_the_links_of_the_drawing() -> None:
    """One plotly Sankey trace whose nodes are the drawing's labels and whose links refer to them by position."""
    links, kinds = pv_house()
    drawing = SankeyDrawing.of(diagram_of(links, kinds), "overall")
    trace = drawing.figure("t").data[0]
    assert list(trace.node.label) == [node.label for node in drawing.nodes]
    assert list(trace.link.value) == [item.kilowatt_hours for item in drawing.links]
    names = [node.name for node in drawing.nodes]
    assert [(names[source], names[target]) for source, target in zip(trace.link.source, trace.link.target)] == [(item.source, item.target) for item in drawing.links]


@pytest.mark.base
def test_the_figure_places_the_nodes_where_the_layout_puts_them_and_is_as_large_as_it_needs() -> None:
    """node.x/node.y are the layout's, plotly snaps with the label gap as its pad, and the figure is the plot and its
    margins."""
    links, kinds = house_with_small_flows()
    drawing = SankeyDrawing.of(diagram_of(links, kinds), "electricity")
    layout = SankeyLayout.of(drawing.links, {node.name: node.label for node in drawing.nodes})
    figure = drawing.figure("t")
    trace = figure.data[0]
    assert trace.arrangement == "snap"
    assert list(trace.node.x) == [layout.x[node.name] for node in drawing.nodes]
    assert list(trace.node.y) == [layout.y[node.name] for node in drawing.nodes]
    assert trace.node.pad == SankeyLayout.LABEL_GAP_IN_PIXELS
    assert figure.layout.height == round(layout.height_in_pixels + 90)
    assert figure.layout.width == round(layout.width_in_pixels + 40)


@pytest.mark.base
def test_a_drawing_draws_a_two_way_exchange_once_so_the_battery_draws_no_ring() -> None:
    """The battery's charge and discharge are one link of their net; its store is a node of its own."""
    links, kinds = battery_ring()
    drawing = SankeyDrawing.of(diagram_of(links, kinds), "electricity")
    assert ("Battery", "Meter") not in pairs(drawing.links)
    assert pairs(drawing.links)[("Meter", "Battery")] == pytest.approx(947.3)
    assert SankeyLayout.back_links(drawing.links) == frozenset()


@pytest.mark.base
def test_the_link_that_closes_a_cycle_is_the_back_link() -> None:
    """The walk from the grid goes Meter -> Battery and finds Battery -> Meter going back; a cycle without a source is
    walked from its first node."""
    links = [link("grid", "Meter"), link("Meter", "Battery"), link("Battery", "Meter"), link("Battery", "loss")]
    assert SankeyLayout.back_links(links) == frozenset({("Battery", "Meter")})
    assert SankeyLayout.back_links([link("A", "B"), link("B", "A")]) == frozenset({("B", "A")})
    assert SankeyLayout.back_links([link("A", "B"), link("A", "C"), link("B", "C")]) == frozenset()


@pytest.mark.base
def test_sources_are_first_sinks_last_and_every_other_node_in_the_column_of_its_longest_path() -> None:
    """grid import 0, meter 1, heat pump 2, building 3, its loss node 4; outdoors and grid export, both sinks, 5."""
    links = [
        link("grid import", "Meter"),
        link("Meter", "HeatPump"),
        link("HeatPump", "Building"),
        link("Building", "Building · loss"),
        link("Building · loss", "outdoors"),
        link("Meter", "grid export"),
    ]
    assert SankeyLayout.columns(links) == {
        "grid import": 0,
        "Meter": 1,
        "HeatPump": 2,
        "Building": 3,
        "Building · loss": 4,
        "outdoors": 5,
        "grid export": 5,
    }


@pytest.mark.base
def test_a_store_is_a_source_when_it_gives_and_a_sink_when_it_takes_and_a_back_link_does_not_count() -> None:
    """The tank's store gives up energy: first column; the battery's store takes some: last column. The battery's
    discharge back to the meter closes a cycle and does not push the meter behind the battery."""
    links = [
        link("grid", "Meter"),
        link("Meter", "Battery"),
        link("Battery", "Meter"),
        link("Battery", "Battery · stored"),
        link("Tank · stored", "Tank"),
        link("Meter", "Tank"),
        link("Tank", "taps"),
    ]
    assert SankeyLayout.columns(links) == {
        "grid": 0,
        "Meter": 1,
        "Battery": 2,
        "Battery · stored": 3,
        "Tank · stored": 0,
        "Tank": 2,
        "taps": 3,
    }


@pytest.mark.base
def test_a_column_is_sorted_by_height_then_by_the_heights_of_its_neighbours() -> None:
    """By height the sinks would be Ta, Tc, Tb, and Ta's link from the lower source would cross both others; by their
    neighbours they are Tc, Tb, Ta (Tc and Tb share a neighbour and keep their order), and the sources stay."""
    links = [link("S1", "Ta", 100.0), link("S2", "Tc", 60.0), link("S2", "Tb", 50.0)]
    columns = {"S1": 0, "Ta": 1, "S2": 0, "Tc": 1, "Tb": 1}
    heights = {"S1": 100.0, "Ta": 100.0, "S2": 110.0, "Tc": 60.0, "Tb": 50.0}
    assert SankeyLayout.orders(links, columns, heights) == [["S2", "S1"], ["Tc", "Tb", "Ta"]]


@pytest.mark.base
def test_nodes_are_stacked_a_gap_apart_and_the_column_is_centred() -> None:
    """Heights 100 and 20, gap 20, plot 200: the column takes 140 pixels, starts 30 down, centres at 80 and 160."""
    assert SankeyLayout.stack([100.0, 20.0], 20.0, 200.0) == [80.0, 160.0]
    assert not SankeyLayout.stack([], 20.0, 200.0)


@pytest.mark.base
def test_the_plot_is_as_tall_as_the_column_that_needs_most_room() -> None:
    """Nodes of 560, 1 and 1 pixels need 602 with two gaps of 20; twelve nodes of 1 pixel 232."""
    heights = {"big": 560.0, "a": 1.0, "b": 1.0, **{f"n{index}": 1.0 for index in range(12)}}
    assert SankeyLayout.plot_height_in_pixels([["big", "a", "b"], [f"n{index}" for index in range(12)]], heights, 20.0) == 602.0
    assert SankeyLayout.plot_height_in_pixels([[f"n{index}" for index in range(12)]], heights, 20.0) == 232.0


@pytest.mark.base
def test_a_label_is_estimated_at_its_characters_times_a_share_of_the_font_size() -> None:
    """23 characters at 12 pixels and a share of 0.55: 151.8 pixels."""
    assert SankeyLayout.label_width_in_pixels("grid import · 1,805 kWh") == pytest.approx(151.8)


@pytest.mark.base
def test_the_gap_after_a_column_holds_its_longest_label_and_the_last_gap_both_columns_labels() -> None:
    """Labels of 500, 600 and 400 pixels: centres 10, 538 and 1574 in a plot 1584 wide; labels of 100, 200 and 50:
    centres 10, 138 and 424 in 434 pixels, spread over the smallest width of 1200."""
    assert SankeyLayout.column_centres([500.0, 600.0, 400.0]) == ([10.0, 538.0, 1574.0], 1584.0)
    centres, width = SankeyLayout.column_centres([100.0, 200.0, 50.0])
    assert width == 1200.0
    assert centres == [10.0, pytest.approx(10.0 + 128.0 * 1180.0 / 414.0), pytest.approx(1190.0)]
    assert SankeyLayout.column_centres([0.0]) == ([10.0], 20.0)


@pytest.mark.base
def test_the_layout_gives_every_label_a_line_of_its_own() -> None:
    """In every column the centres are at least a label gap apart, sources are on the left, sinks on the right."""
    links, kinds = house_with_small_flows()
    drawing = SankeyDrawing.of(diagram_of(links, kinds), "electricity")
    layout = SankeyLayout.of(drawing.links, {node.name: node.label for node in drawing.nodes})
    assert set(layout.x) == set(layout.y) == {node.name for node in drawing.nodes}
    for column_nodes in layout.column_nodes:
        centres = [layout.y[node] * layout.height_in_pixels for node in column_nodes]
        assert all(lower - upper >= SankeyLayout.LABEL_GAP_IN_PIXELS - 1e-9 for upper, lower in zip(centres, centres[1:]))
    assert layout.x["grid import"] == min(layout.x.values())
    assert layout.x["outdoors"] == max(layout.x.values())
    assert all(0.0 < value < 1.0 for value in list(layout.x.values()) + list(layout.y.values()))
    assert layout.column_of["Heater"] == 2
