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

**Refusals are part of the contract.** Both layouts and both prose renderers raise on input they
cannot draw or cannot render, rather than producing a picture or a paragraph that is quietly
wrong, and each of those refusals is asserted here — a zero-area tile, a signless ribbon, a
ribbon naming a node no column declares, an unbalanced emphasis marker. The prose case is also
asserted over the *whole* authored corpus, in both renderers, so a stray asterisk typed into a
definition fails in CI rather than being printed to a reader as punctuation.
"""

# clean

import dataclasses
from typing import Any, Dict, List, Tuple

import pytest

from hisim.economics.presentation_style import (
    ChromeColors,
    SankeyGeometry,
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

    @pytest.mark.parametrize(
        "values, offender",
        [
            ([50.0, 0.0, 12.0], 1),
            ([50.0, -25.0], 1),
            ([float("inf"), 3.0], 0),
            ([3.0, float("nan")], 1),
        ],
    )
    def test_a_value_that_is_not_a_positive_area_is_refused(self, values, offender):
        """A zero tile has no rectangle and a negative one eats into its row — both raise.

        The tiling arithmetic would not notice either: a zero area comes back as a zero-height
        rectangle the reader cannot see, and a negative one shortens the row around it, so the
        remaining tiles silently stop meaning what their areas claim. The message names the index
        so the caller can find the amount it did not filter.
        """
        with pytest.raises(ValueError, match=rf"values\[{offender}\]"):
            squarified_layout(values, 0.0, 0.0, 16.0, 9.0)


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

    def anchors(self, geometry: SankeyGeometry) -> List[Tuple[float, float]]:
        """The two ends of every ribbon: the first leg's `out_anchor`, the last leg's `in_anchor`.

        `SankeyGeometry` publishes the routed chain and nothing else, so the pair a crossing count
        or a face tiling needs is read off the chain rather than from a second field holding a
        projection of it — a field that could disagree with the legs it was derived from.
        """
        return [(chain[0].out_anchor, chain[-1].in_anchor) for chain in geometry.ribbon_segments]

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
        anchors = self.anchors(geometry)
        for node, (_x, _y, height) in geometry.boxes.items():
            outgoing = [
                anchor[0] + amount * geometry.unit_scale
                for anchor, (source, _target, amount) in zip(anchors, ribbons)
                if source == node
            ]
            incoming = [
                anchor[1] + amount * geometry.unit_scale
                for anchor, (_source, target, amount) in zip(anchors, ribbons)
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
        after = self.crossings(self.anchors(geometry), geometry.boxes, ribbons)
        # The fixture is tangled on both sides on purpose: four crossings before the sweeps, and
        # the two steps together remove all four. Both halves of that matter. `<=` passes even
        # when nothing is reordered at all, and a strict `<` still passes with the barycenter
        # sweeps disabled, because sorting the anchors alone gets the count down to two — only
        # "no crossings left" fails when either step is taken out.
        assert before == 4
        assert after < before
        assert after == 0

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

    def test_the_scale_is_the_tightest_column_and_nothing_leaves_the_unit_square(self):
        """One scale, chosen by the column that fits least comfortably, and no overflow.

        `unit_scale` is the smallest of the per-column candidates — a column's usable height (the
        unit square minus its inter-node gaps, floored at `MINIMUM_USABLE_HEIGHT`) divided by what
        it carries — because the *binding* column is the one that decides how tall a unit may be.
        Taking any other candidate would let that column overflow the square, which is the
        assertion in the second half: the fullest column spans exactly [0, 1] and every node of
        every column sits inside it.
        """
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        values = {
            node: max(
                sum(amount for source, _target, amount in ribbons if source == node),
                sum(amount for _source, target, amount in ribbons if target == node),
            )
            for column in columns
            for node in column
        }
        assert geometry.unit_scale == pytest.approx(min(
            max(1.0 - SankeyLayout.NODE_GAP * (len(nodes) - 1), SankeyLayout.MINIMUM_USABLE_HEIGHT)
            / sum(values[node] for node in nodes)
            for nodes in columns
        ))
        for node, (_x, y, height) in geometry.boxes.items():
            assert y >= -1e-9, node
            assert y + height <= 1.0 + 1e-9, node
        spans = [
            (min(geometry.boxes[node][1] for node in nodes),
             max(geometry.boxes[node][1] + geometry.boxes[node][2] for node in nodes))
            for nodes in columns
        ]
        assert any(
            bottom == pytest.approx(0.0, abs=1e-9) and top == pytest.approx(1.0, abs=1e-9)
            for bottom, top in spans
        )

    def test_the_ribbons_on_a_face_are_stacked_by_their_far_ends_height(self):
        """The second untangling step: a face's ribbons are ordered by where they are going.

        Stacking a node's outgoing ribbons in the order of their targets' heights (and its
        incoming ones by their sources') is what stops two correctly ordered columns from being
        re-tangled by the ribbons between them. Asserted as monotonicity rather than as a literal
        anchor list, because the property is the ordering and not the arithmetic: read the anchors
        of one face in ascending order and the far ends they lead to must ascend too.
        """
        columns, ribbons = self.tangled_diagram()
        geometry = sankey_node_boxes(columns, ribbons)
        anchors = self.anchors(geometry)

        def centre(node):
            """Vertical middle of a node — the height `_ribbon_anchors` sorts a face on."""
            _x, y, height = geometry.boxes[node]
            return y + height / 2.0

        faces_checked = 0
        for is_outgoing in (True, False):
            face = 0 if is_outgoing else 1
            by_node: Dict[str, List[int]] = {}
            for index, (source, target, _amount) in enumerate(ribbons):
                by_node.setdefault(source if is_outgoing else target, []).append(index)
            for node, indices in by_node.items():
                if len(indices) < 2:
                    continue
                faces_checked += 1
                stacked = sorted(indices, key=lambda index, face=face: anchors[index][face])
                far_ends = [
                    centre(ribbons[index][1] if is_outgoing else ribbons[index][0])
                    for index in stacked
                ]
                assert far_ends == sorted(far_ends), (node, is_outgoing)
        assert faces_checked >= 4

    def test_no_leg_passes_through_a_node_it_does_not_end_at(self):
        """The invariant the corridors buy: a leg's horizontal span touches only its own two ends.

        Every leg runs from the right face of a node in one column to the left face of a node in
        the *next* column, so its x-extent is the open gap between two adjacent columns. Nothing
        else can be in that gap: every node of the source column ends exactly where the leg starts
        and every node of the target column starts exactly where it ends, and there is no third
        column x in between. That is what makes "no ribbon path intersects a node rectangle" a
        property of the layout rather than of the caller's luck — and it is the property the four
        source-to-landlord ribbons of the rented view used to violate.
        """
        for columns, ribbons in (self.skipping_diagram(), self.tangled_diagram()):
            geometry = sankey_node_boxes(columns, ribbons)
            column_positions = sorted({x for x, _y, _height in geometry.boxes.values()})
            for chain in geometry.ribbon_segments:
                for leg in chain:
                    source_x = geometry.boxes[leg.source][0]
                    target_x = geometry.boxes[leg.target][0]
                    left = min(source_x, target_x) + SankeyLayout.NODE_WIDTH
                    right = max(source_x, target_x)
                    assert left < right, leg
                    assert not [x for x in column_positions if left <= x < right], leg
                    for node, (x, _y, _height) in geometry.boxes.items():
                        if node in (leg.source, leg.target):
                            continue
                        assert not (x < right and x + SankeyLayout.NODE_WIDTH > left), (leg, node)

    @pytest.mark.parametrize("amount", [0.0, -400.0, float("inf"), float("nan")])
    def test_a_ribbon_that_is_not_a_positive_flow_is_refused(self, amount):
        """A Sankey has no sign — direction is the node pair — and a zero ribbon is invisible.

        Both would still be laid out: the amount is added to both faces it touches, so the nodes
        grow around a ribbon the reader either cannot see or would read backwards.
        """
        with pytest.raises(ValueError, match="Sankey ribbon 1"):
            sankey_node_boxes([["a"], ["b"]], [("a", "b", 100.0), ("a", "b", amount)])

    def test_a_ribbon_naming_an_undeclared_node_is_refused(self):
        """It used to be dropped by the renderers while still inflating the node it left.

        The ribbon had no column to be drawn in, so nothing drew it — but its amount still counted
        into its source's outgoing total, and the source was therefore drawn taller than the
        ribbons that tile it, for a reason nothing in the picture stated.
        """
        with pytest.raises(ValueError, match="'ghost'"):
            sankey_node_boxes([["a"], ["b"]], [("a", "b", 100.0), ("a", "ghost", 50.0)])
        with pytest.raises(ValueError, match="'ghost'"):
            sankey_node_boxes([["a"], ["b"]], [("ghost", "b", 50.0)])


class TestChromeColors:
    """The four neutral chrome roles: the same roles in both modes, and not rewritable at runtime."""

    def test_both_modes_define_exactly_the_same_four_roles(self):
        """A role defined in one mode and missing in the other is a KeyError in dark mode only."""
        assert set(ChromeColors.LIGHT) == {"surface", "ink", "muted", "grid"}
        assert set(ChromeColors.DARK) == set(ChromeColors.LIGHT)

    def test_a_palette_cannot_be_rewritten_in_place(self):
        """A renderer that assigned a role would recolour every chart drawn after it, silently."""
        palette: Any = ChromeColors.LIGHT
        with pytest.raises(TypeError):
            palette["surface"] = "#000000"
        assert ChromeColors.LIGHT["surface"] == "#fcfcfb"


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

    def authored_strings(self):
        """Every authored string in the module, named by where it was found.

        Read off the dataclass fields rather than listed by hand, so a fifth part added to
        `SectionProse` is covered the moment it exists instead of the day somebody remembers to
        extend this helper.
        """
        found = []
        for name, prose in ReportProse.SECTIONS.items():
            for section_field in dataclasses.fields(prose):
                value = getattr(prose, section_field.name)
                if isinstance(value, str):
                    found.append((f"{name}.{section_field.name}", value))
                    continue
                for item in value:
                    parts = [item] if isinstance(item, str) else list(item)
                    found.extend((f"{name}.{section_field.name}", part) for part in parts)
        found.extend((f"chapter {name}", intro) for name, intro in ReportProse.CHAPTER_INTROS.items())
        return found

    def test_every_authored_string_renders_in_both_renderers(self):
        """The corpus test: a stray asterisk anywhere in the module fails here rather than in a report.

        Both renderers refuse markup they cannot render, so an unbalanced or nested marker typed
        into a definition raises. Running both over every field of every section and every chapter
        intro is what turns that refusal into a CI failure at the moment the text is edited,
        instead of a traceback the first time somebody renders the report — or, worse, a raw
        asterisk printed to a reader as punctuation.
        """
        strings = self.authored_strings()
        assert len(strings) > 400
        for where, text in strings:
            assert ReportProse.to_html(text), where
            assert ReportProse.to_plain_text(text), where

    @pytest.mark.parametrize("text", ["**a*b**", "value *", "a ` b", "**bold *and* more**"])
    def test_markup_that_cannot_be_rendered_is_refused_by_both_renderers(self, text):
        """Nested or unbalanced markers raise, in HTML and in plain text alike.

        `**a*b**` renders as neither strong nor emphasis; `value *` is a marker the author did not
        close. Either way what is left over would be printed raw, and the plain-text renderer used
        to strip it silently — so the caption and the HTML disagreed about what the prose said.
        """
        with pytest.raises(ValueError):
            ReportProse.to_html(text)
        with pytest.raises(ValueError):
            ReportProse.to_plain_text(text)

    def test_a_marker_inside_a_code_span_is_literal(self):
        """Code spans are substituted first, so backticks protect what is between them."""
        assert ReportProse.to_html("`*a*`") == "<code>*a*</code>"
        assert ReportProse.to_plain_text("`*a*`") == "*a*"

    def test_an_unknown_name_raises_rather_than_rendering_empty(self):
        """`for_section` / `for_chapter` fail loudly; an empty block would be invisible in a report."""
        with pytest.raises(KeyError):
            ReportProse.for_section("A section nobody wrote")
        with pytest.raises(KeyError):
            ReportProse.for_chapter("A chapter nobody wrote")
        assert ReportProse.for_section(ReportProse.PRIMER_SECTION_NAME).shows
