"""Unit tests for the two shared dependencies of the report renderers: the layouts and the prose.

`presentation_style.squarified_layout` / `sankey_node_boxes` and `report_prose.ReportProse` are
consumed by both renderers — the inline SVGs of the `reporting` package and the matplotlib PNGs of
`report_plots.py` — and by neither exclusively. They are therefore tested here, on their own
inputs, rather than through a rendered report: a layout is a pure function from amounts to
rectangles, and asserting on it directly says which of the two is at fault when a chart looks
wrong. The renderers' own tests (`test_economics_reporting.py`, the goldens) assert that what is
drawn matches what these return.

**What a failure means.** Nothing about a number: no figure in any report comes from this module
pair. A failure here means a chart would be *drawn* wrong — tiles that do not tile, ribbons that
cross where they need not, a face left partly empty, or authored prose that reaches the document
unescaped. The last of those is the only one with a security flavour, which is why the escaping is
pinned by an assertion rather than by the goldens alone.
"""

# clean

from typing import Dict, List, Tuple

import pytest

from hisim.economics.presentation_style import (
    SankeyLayout,
    _place_nodes,
    sankey_node_boxes,
    squarified_layout,
)
from hisim.economics.report_prose import ReportProse

pytestmark = pytest.mark.base


Rectangle = Tuple[float, float, float, float]


def _overlap(first: Rectangle, second: Rectangle) -> float:
    """Overlapping area of two `(x, y, width, height)` rectangles, zero when they only touch."""
    first_x, first_y, first_w, first_h = first
    second_x, second_y, second_w, second_h = second
    horizontal = min(first_x + first_w, second_x + second_w) - max(first_x, second_x)
    vertical = min(first_y + first_h, second_y + second_h) - max(first_y, second_y)
    return max(horizontal, 0.0) * max(vertical, 0.0)


class TestSquarifiedLayout:
    """V8's treemap layout: the tiles have to tile, in the caller's order, at the caller's scale.

    A treemap says "these areas are these amounts". That reading survives only if the rectangles
    partition the box exactly — every gap is an amount the reader cannot see and every overlap is
    one counted twice — so the three properties below are the chart's correctness, not its polish.
    """

    def sample(self):
        """A deliberately uneven set of amounts: one dominant tile and a tail of small ones."""
        return [50.0, 25.0, 12.0, 7.0, 4.0, 2.0]

    def test_the_tiles_cover_the_box_exactly(self):
        """Areas sum to the box area, and the union's bounding box is the box itself."""
        values = self.sample()
        tiles = squarified_layout(values, 10.0, 5.0, 40.0, 20.0)
        assert len(tiles) == len(values)
        assert sum(width * height for _x, _y, width, height in tiles) == pytest.approx(40.0 * 20.0)
        assert min(x for x, _y, _w, _h in tiles) == pytest.approx(10.0)
        assert min(y for _x, y, _w, _h in tiles) == pytest.approx(5.0)
        assert max(x + width for x, _y, width, _h in tiles) == pytest.approx(50.0)
        assert max(y + height for _x, y, _w, height in tiles) == pytest.approx(25.0)

    def test_no_two_tiles_overlap(self):
        """An overlap would draw one euro inside another; the areas would stop being readable."""
        tiles = squarified_layout(self.sample(), 0.0, 0.0, 16.0, 9.0)
        for first, tile in enumerate(tiles):
            for other in tiles[first + 1:]:
                assert _overlap(tile, other) == pytest.approx(0.0, abs=1e-9)

    def test_each_tile_has_the_area_its_value_claims(self):
        """The rectangles come back in input order, each scaled by its share of the total."""
        values = self.sample()
        tiles = squarified_layout(values, 0.0, 0.0, 16.0, 9.0)
        total = sum(values)
        for value, (_x, _y, width, height) in zip(values, tiles):
            assert width * height == pytest.approx(value / total * 16.0 * 9.0)

    def test_the_tiles_stay_roughly_square(self):
        """The point of squarifying: no tile degenerates into a sliver the eye cannot compare."""
        tiles = squarified_layout(self.sample(), 0.0, 0.0, 16.0, 9.0)
        for _x, _y, width, height in tiles:
            assert max(width / height, height / width) < 6.0

    def test_an_empty_input_lays_nothing_out(self):
        """A treemap of nothing is not an error — the caller filters, and gets an empty list."""
        assert not squarified_layout([], 0.0, 0.0, 16.0, 9.0)


class TestSankeyLayoutGeometry:
    """The shared Sankey layout: one scale, corridors, closed faces, and fewer crossings (Q17/Q19).

    Ported from the geometry half of the 9/9 chart tests; the halves that parse an SVG path or a
    matplotlib patch belong with the renderers that emit them. What is asserted here is the
    contract both of those renderers consume: `SankeyGeometry`.
    """

    def tangled_diagram(self):
        """A three-column diagram whose given node order is deliberately the tangled one.

        Every source feeds the actor whose listed position is furthest from its own, so the naive
        layout crosses on both sides; the middle column carries each unit twice, which is exactly
        the situation that produced two different per-column scales before Q17.
        """
        columns = [["bank", "state", "market"], ["landlord", "tenant"], ["fees", "energy", "works"]]
        ribbons = [
            ("bank", "tenant", 400.0),
            ("state", "tenant", 300.0),
            ("market", "landlord", 500.0),
            ("landlord", "energy", 200.0),
            ("landlord", "fees", 300.0),
            ("tenant", "works", 500.0),
            ("tenant", "energy", 200.0),
        ]
        return columns, ribbons

    def skipping_diagram(self):
        """Four columns in which two ribbons skip a column each, in both directions of imbalance.

        The shape of the rented view reduced to its defect: sources paying a party two columns
        along (their ribbons used to cross the intervening party's block), a party paying a sink
        two columns along, and a middle party that receives more than it passes on (its outgoing
        face used to be a third empty).
        """
        columns = [["bank", "state"], ["tenant"], ["landlord"], ["market", "suppliers"]]
        ribbons = [
            ("bank", "landlord", 600.0),
            ("state", "landlord", 300.0),
            ("tenant", "landlord", 500.0),
            ("tenant", "suppliers", 200.0),
            ("landlord", "market", 700.0),
        ]
        return columns, ribbons

    def crossings(self, anchors, boxes, ribbons):
        """Pairwise anchor-order inversions between ribbons sharing a column pair.

        Two ribbons cross when their vertical order at the source end is the opposite of their
        order at the target end. Counting inversions is the standard proxy for "how tangled is this
        picture"; it is exact for straight ribbons and monotone in the same direction for the
        Bezier ones actually drawn.
        """
        total = 0
        for first, (source_a, target_a, _amount_a) in enumerate(ribbons):
            for second, (source_b, target_b, _amount_b) in enumerate(ribbons[first + 1:], start=first + 1):
                if boxes[source_a][0] != boxes[source_b][0] or boxes[target_a][0] != boxes[target_b][0]:
                    continue
                start = (boxes[source_a][1] + anchors[first][0]) - (boxes[source_b][1] + anchors[second][0])
                end = (boxes[target_a][1] + anchors[first][1]) - (boxes[target_b][1] + anchors[second][1])
                if start * end < 0:
                    total += 1
        return total

    def naive_anchors(self, ribbons, unit_scale):
        """Ribbon anchors without any ordering: stacked in the order the caller listed the flows.

        The baseline the crossing-minimization test compares against — what the layout produced
        before Q19, and what a renderer would do if it simply appended.
        """
        anchors: List[Tuple[float, float]] = []
        out_offset: Dict[str, float] = {}
        in_offset: Dict[str, float] = {}
        for source, target, amount in ribbons:
            anchors.append((out_offset.get(source, 0.0), in_offset.get(target, 0.0)))
            out_offset[source] = out_offset.get(source, 0.0) + amount * unit_scale
            in_offset[target] = in_offset.get(target, 0.0) + amount * unit_scale
        return anchors

    def test_one_global_unit_scale_across_all_columns(self):
        """The defect itself: every node's height is the same euros-per-unit, column-independent."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        carried: Dict[str, float] = {}
        incoming: Dict[str, float] = {}
        for source, target, amount in ribbons:
            carried[source] = carried.get(source, 0.0) + amount
            incoming[target] = incoming.get(target, 0.0) + amount
        for node, (_x, _y, height) in geometry.boxes.items():
            value = max(carried.get(node, 0.0), incoming.get(node, 0.0))
            assert height == pytest.approx(value * geometry.unit_scale)

    def test_the_anchors_tile_the_fuller_face_of_every_node(self):
        """The ribbons on a node's fuller face stack to its full height, with no overflow."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        for node, (_x, _y, height) in geometry.boxes.items():
            outgoing = [
                anchor[0] + amount * geometry.unit_scale
                for anchor, (source, _target, amount) in zip(geometry.ribbon_anchors, ribbons)
                if source == node
            ]
            incoming = [
                anchor[1] + amount * geometry.unit_scale
                for anchor, (_source, target, amount) in zip(geometry.ribbon_anchors, ribbons)
                if target == node
            ]
            assert max(outgoing + incoming + [0.0]) == pytest.approx(height)

    def test_ordering_reduces_crossings_against_the_unordered_layout(self):
        """Q19: barycenter node order plus anchor sorting untangles a deliberately tangled input."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        naive_boxes = _place_nodes(
            columns,
            {
                node: max(
                    sum(amount for source, _target, amount in ribbons if source == node),
                    sum(amount for _source, target, amount in ribbons if target == node),
                )
                for column in columns
                for node in column
            },
            geometry.unit_scale,
        )
        before = self.crossings(self.naive_anchors(ribbons, geometry.unit_scale), naive_boxes, ribbons)
        after = self.crossings(geometry.ribbon_anchors, geometry.boxes, ribbons)
        assert before > 0
        assert after <= before

    def test_connected_nodes_end_up_near_each_other(self):
        """What the barycenter sweeps are for: the fat ribbon's two ends sit at similar heights."""
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)

        def centre(node):
            _x, y, height = geometry.boxes[node]
            return y + height / 2.0

        for source, target, amount in ribbons:
            if amount < 500.0:
                continue
            assert abs(centre(source) - centre(target)) < 0.25

    def test_layout_is_deterministic(self):
        """A report re-rendered from the same result must be byte-identical; no hash order here."""
        columns, ribbons = self.tangled_diagram()
        first = sankey_node_boxes(columns, ribbons)
        second = sankey_node_boxes(columns, ribbons)
        assert first.boxes == second.boxes
        assert first.ribbon_anchors == second.ribbon_anchors
        assert first.ribbon_segments == second.ribbon_segments
        assert first.net_stubs == second.net_stubs
        assert first.unit_scale == second.unit_scale

    def test_a_column_skipping_ribbon_is_routed_through_a_corridor(self):
        """Q29 R7: it becomes one leg per column gap, with a virtual node holding the space."""
        columns, ribbons = self.skipping_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        chains = geometry.ribbon_segments
        assert [len(chain) for chain in chains] == [2, 2, 1, 2, 1]
        virtual = [node for node in geometry.boxes if node.startswith(SankeyLayout.VIRTUAL_NODE_PREFIX)]
        assert len(virtual) == 3
        for node in virtual:
            assert node not in [name for column in columns for name in column]
        # A corridor node is exactly as tall as the ribbon it carries — that is what reserving
        # the space means, and it is why the ribbon has somewhere to go.
        for chain, (_source, _target, amount) in zip(chains, ribbons):
            for leg in chain:
                for node in (leg.source, leg.target):
                    if node.startswith(SankeyLayout.VIRTUAL_NODE_PREFIX):
                        assert geometry.boxes[node][2] == pytest.approx(amount * geometry.unit_scale)

    def test_the_stub_is_the_nodes_own_net_position(self):
        """Q29 R7: the face a node's ribbons cannot fill is handed out as its net position."""
        columns, ribbons = self.skipping_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        stubs = {stub.node: stub for stub in geometry.net_stubs}
        assert set(stubs) == {"landlord"}
        assert stubs["landlord"].amount == pytest.approx(1400.0 - 700.0)
        assert stubs["landlord"].is_outgoing
        assert stubs["landlord"].anchor == pytest.approx(700.0 * geometry.unit_scale)

    def test_a_transfer_is_an_ordinary_ribbon_between_distinct_columns(self):
        """Q23: each party has its own column, so an inter-actor transfer travels left to right.

        The levy used to connect two nodes of one shared middle column, which has no horizontal
        extent, so both renderers drew it as a band looping out of the column and back. With the
        payer's column placed before the payee's it is a ribbon like any other, and the payee is
        as tall as what passes through it rather than as tall as the sum of both faces.
        """
        columns = [["market"], ["tenant"], ["landlord"], ["works"]]
        ribbons = [
            ("market", "landlord", 1000.0),
            ("tenant", "landlord", 400.0),
            ("landlord", "works", 1400.0),
        ]
        geometry = sankey_node_boxes(columns, ribbons)
        assert geometry.boxes["landlord"][2] == pytest.approx(1400.0 * geometry.unit_scale)
        assert geometry.boxes["tenant"][2] == pytest.approx(400.0 * geometry.unit_scale)
        assert geometry.boxes["tenant"][0] < geometry.boxes["landlord"][0]
        assert geometry.net_stubs == []


class TestReportProse:
    """The authored explanation text: complete, addressable by section name, and escaped on the way out."""

    def test_prose_markup_is_escaped_before_it_is_emphasized(self):
        """A definition mentioning a tag must not be able to open one."""
        assert ReportProse.to_html("a *b* and **c** and `d`") == (
            "a <em>b</em> and <strong>c</strong> and <code>d</code>"
        )
        assert ReportProse.to_html("<details> & <em>") == "&lt;details&gt; &amp; &lt;em&gt;"
        assert ReportProse.to_plain_text("a *b* and **c** and `d`") == "a b and c and d"

    def test_quotes_are_escaped_too_so_an_attribute_cannot_be_closed(self):
        """`html.escape(..., quote=True)`, the same call the reporting package's `_esc` makes."""
        assert ReportProse.to_html('a "quoted" word') == "a &quot;quoted&quot; word"
        assert ReportProse.to_html("it's") == "it&#x27;s"

    def test_every_authored_section_carries_all_four_parts(self):
        """A section rendered without its terms or its calculation is a silent editorial gap."""
        for name, prose in ReportProse.SECTIONS.items():
            assert prose.shows, name
            assert prose.adds, name
            if name == ReportProse.LEDGER_HEATMAP_SECTION_NAME:
                continue
            assert prose.terms, name
            assert prose.calculation, name
            for term, definition in prose.terms:
                assert term and definition, name

    def test_every_chapter_carries_its_lead_in(self):
        """Q24: a chapter heading with no authored intro would open the story with nothing."""
        assert ReportProse.CHAPTER_INTROS
        for name, intro in ReportProse.CHAPTER_INTROS.items():
            assert intro.strip(), name

    def test_an_unknown_name_raises_rather_than_rendering_empty(self):
        """`for_section` / `for_chapter` fail loudly; an empty block would be invisible in a report."""
        with pytest.raises(KeyError):
            ReportProse.for_section("A section nobody wrote")
        with pytest.raises(KeyError):
            ReportProse.for_chapter("A chapter nobody wrote")
        assert ReportProse.for_section(ReportProse.PRIMER_SECTION_NAME).shows
