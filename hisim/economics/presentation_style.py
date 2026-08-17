"""Display grouping and the categorical palette, shared by every report output (W4.7).

The 16+ cost categories of `timeline.CostCategory` fold onto 8 display groups so a fixed
categorical palette covers them and a group keeps its hue across the HTML report, the SVG
charts and the matplotlib PNGs. That mapping is the *one* piece of the seam-4 contract the spec
leaves on the presentation side (§2.4, stated exception): the grouping is a display concept —
the group **sums** are not, and come from `views.fold_categories` / `views.fold_category_matrix`,
into which presentation passes `CATEGORY_TO_GROUP`.

This module exists so `report_plots.py` no longer imports `reporting.py` (and through it the
engine) just to learn what colour "Energy" is. It imports nothing but `timeline.CostCategory`,
which makes it importable from either side of the seam without dragging anything along; the
import-lint in `tests/test_economics_import_lint.py` pins that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from hisim.economics.timeline import CostCategory


def _build_category_to_group(
    display_groups: List[Tuple[str, Tuple[CostCategory, ...]]]
) -> Dict[CostCategory, int]:
    """Total category -> group-index mapping; categories no group declares land in group 0.

    Inverts the group definitions into a lookup and, crucially, makes it *total*: it iterates over
    the whole `CostCategory` enum rather than only over the declared members, so a category added
    later still has an entry. That is what lets the result be handed to `views.fold_categories`,
    which rejects gaps on purpose, without a future enum member breaking every report.
    """
    declared = {
        category: index for index, (_name, categories) in enumerate(display_groups) for category in categories
    }
    return {category: declared.get(category, 0) for category in CostCategory}


class PresentationStyle:
    """The display grouping and its categorical palette (W4.7).

    Holds the three constants that make every chart in every report output look like one system: the
    ordered display groups, the light and dark colour ramps, and the total category-to-group map.
    Grouping is needed because there are more cost categories than a categorical palette can
    distinguish, and *fixed* ordering is needed because a group that changes hue or stack position
    between the HTML report, its inline SVGs and the matplotlib PNGs makes the three impossible to
    read side by side.

    A reviewer should note the boundary this class sits on: it decides how sums are *labelled and
    coloured*, never what the sums are. `views.fold_categories` computes the folded values and takes
    `CATEGORY_TO_GROUP` as an argument, so a grouping change can never alter a number.
    """

    #: (group label, member categories) in the fixed order every chart stacks and legends them.
    DISPLAY_GROUPS: List[Tuple[str, Tuple[CostCategory, ...]]] = [
        ("Investment & financing", (CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL,
                                    CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL,
                                    CostCategory.LOAN_DISBURSEMENT)),
        ("Feed-in revenue", (CostCategory.FEED_IN_REVENUE,)),
        ("Residual value & anyway credit", (CostCategory.RESIDUAL_VALUE, CostCategory.ANYWAY_COST_CREDIT)),
        ("Subsidies", (CostCategory.SUBSIDY,)),
        ("Replacements", (CostCategory.REPLACEMENT, CostCategory.REPLACEMENT_RESERVE)),
        ("Energy", (CostCategory.ENERGY_WORKING, CostCategory.ENERGY_STANDING,
                    CostCategory.ENERGY_CAPACITY_CHARGE)),
        ("CO2", (CostCategory.ENERGY_CO2_PRICE, CostCategory.CO2_DAMAGE)),
        ("Maintenance & operation", (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION,
                                     CostCategory.MODERNIZATION_LEVY)),
    ]

    #: Light-mode categorical slots 1..8 of the dataviz reference palette, in fixed order (never
    #: cycled). The dark-mode set is the same hues at the contrast the dark surface needs.
    GROUP_COLORS_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
    GROUP_COLORS_DARK = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"]

    #: **Total** category -> group-index mapping — every `CostCategory` member has an entry, so it
    #: can be handed to `views.fold_categories` (which rejects gaps on purpose) without a category
    #: added later blowing up a report. A category no group declares lands in group 0, the same
    #: fallback `group_of` has always had.
    CATEGORY_TO_GROUP: Dict[CostCategory, int] = _build_category_to_group(DISPLAY_GROUPS)


def squarified_layout(
    values: List[float], x: float, y: float, width: float, height: float
) -> List[Tuple[float, float, float, float]]:
    """The classic squarified-treemap layout: one rectangle per value, in the input's order.

    Lays descending areas out in rows (or columns, whichever the remaining rectangle is wider
    in) so that the tiles stay as close to square as possible, which is what makes areas
    comparable by eye at all. The returned rectangles tile the given box exactly, so a treemap's
    "areas sum to the total" invariant survives the layout.

    It lives in this module — with the display grouping and the palette rather than with either
    renderer — for the same reason those do: the matplotlib PNG and the inline-SVG report both
    draw the treemap, and two copies of a layout would eventually place the same tiles
    differently. It needs no imports at all, which is what keeps this module importable from
    either side of the seam. The `squarify` PyPI package implements the same algorithm and is
    deliberately not depended on (visualization spec §3, V8).

    Args:
        values: Tile areas in any unit; non-positive values are the caller's to filter out.
        x: Left edge of the box to fill.
        y: Bottom (or top — the caller's coordinate convention) edge of the box.
        width: Box width in the same units as `x`.
        height: Box height.

    Returns:
        One `(x, y, width, height)` per input value, in input order.
    """
    total = sum(values) or 1.0
    scaled = [value * width * height / total for value in values]
    rectangles: List[Tuple[float, float, float, float]] = []
    remaining = list(scaled)
    origin_x, origin_y, box_w, box_h = x, y, width, height
    while remaining:
        row: List[float] = [remaining[0]]
        rest = remaining[1:]
        side = min(box_w, box_h) or 1.0
        while rest and _worst_aspect(row + [rest[0]], side) <= _worst_aspect(row, side):
            row.append(rest[0])
            rest = rest[1:]
        row_area = sum(row)
        if box_w >= box_h:
            row_width = row_area / box_h if box_h else 0.0
            offset = origin_y
            for area in row:
                tile_height = area / row_width if row_width else 0.0
                rectangles.append((origin_x, offset, row_width, tile_height))
                offset += tile_height
            origin_x += row_width
            box_w = max(box_w - row_width, 0.0)
        else:
            row_height = row_area / box_w if box_w else 0.0
            offset = origin_x
            for area in row:
                tile_width = area / row_height if row_height else 0.0
                rectangles.append((offset, origin_y, tile_width, row_height))
                offset += tile_width
            origin_y += row_height
            box_h = max(box_h - row_height, 0.0)
        remaining = rest
    return rectangles


def _worst_aspect(row: List[float], side: float) -> float:
    """Worst width/height ratio of a candidate treemap row — squarify's quality measure.

    The algorithm keeps adding tiles to a row while this does not get worse and closes the row
    the moment it does. A zero-area row is reported as infinitely bad so that it can never win a
    comparison and stall the layout.
    """
    total = sum(row)
    if total <= 0:
        return float("inf")
    largest, smallest = max(row), min(row)
    return max(side * side * largest / (total * total), (total * total) / (side * side * smallest))


class SankeyLayout:
    """Shared geometry of the hand-drawn Sankeys (V1, V10, V11, V12).

    All values are fractions of the unit square the diagram is laid out in, so the same numbers
    work at any figure or viewBox size. `CURVATURE` is the share of the horizontal gap the Bézier
    control points sit at — 0 would draw straight trapezoids, 1 makes every ribbon leave and
    arrive perfectly horizontally, and the value here is the compromise that keeps crossing
    ribbons distinguishable. It sits beside the palette because both renderers read it and a
    ribbon that curves differently in the PNG than in the report would look like a different
    chart.

    There is deliberately no same-column geometry any more: owner decision Q23 gave every
    internal party its own column, so an inter-actor transfer is an ordinary ribbon between two
    adjacent columns and the looping band the levy used to be drawn as — with its own bulge
    constant and its own width correction — is gone from both renderers.
    """

    NODE_WIDTH = 0.035
    NODE_GAP = 0.012
    CURVATURE = 0.42
    #: Smallest usable fraction of the unit square a column may be squeezed into by its node gaps;
    #: a diagram with more nodes than gaps fit keeps drawing rather than collapsing to nothing.
    MINIMUM_USABLE_HEIGHT = 0.1
    #: Alternating barycenter passes over the columns (Q19). Four is the usual stopping point for
    #: this heuristic: the ordering is almost always stable after two, and more passes trade run
    #: time for nothing while risking a two-cycle that never settles.
    BARYCENTER_SWEEPS = 4
    #: Passes of the adjacent-swap refinement after each barycenter sweep (Q29 R7). It stops as
    #: soon as a full pass improves nothing, so the bound only caps a pathological input.
    TRANSPOSE_ROUNDS = 8
    #: Prefix of the virtual (dummy) nodes that give a column-skipping ribbon a corridor to route
    #: through (Q29 R7). Renderers never draw a node they were not handed, and every id built here
    #: starts with this, so a renderer can also assert on it.
    VIRTUAL_NODE_PREFIX = "__via:"
    #: Length of a net-position stub as a fraction of the horizontal column pitch. Long enough to
    #: read as a flow leaving the face, far too short to be mistaken for a ribbon to a neighbour.
    STUB_LENGTH = 0.28
    #: Height (as a fraction of the unit square) below which a face remainder is float noise
    #: rather than a net position, and no stub is emitted for it.
    MINIMUM_STUB_HEIGHT = 1e-4


@dataclass(frozen=True)
class RibbonSegment:
    """One column-to-column leg of a ribbon, with the offsets of its two ends (Q29 R7).

    A ribbon that skips a column is no longer drawn as one long curve past whatever happens to sit
    in between; it is cut at every intermediate column, where a virtual node reserves exactly its
    width, and drawn as a chain of these legs. A ribbon between neighbouring columns has a single
    leg, which is the shape every ribbon used to have.

    `out_anchor` is the offset of the leg's start above the bottom of the source node's right
    face, `in_anchor` the same for the target node's left face — the same convention as
    `SankeyGeometry.ribbon_anchors`, which is now the first and last leg of the chain.
    """

    source: str
    target: str
    out_anchor: float
    in_anchor: float


@dataclass(frozen=True)
class NetStub:
    """The unmatched remainder of a node's face: its net position, drawn (Q29 R7).

    A node is drawn as tall as the larger of what enters and what leaves it, so an actor who
    receives more than it passes on has an outgoing face that its ribbons do not fill. That
    remainder is not nothing: it is precisely the actor's net gain, and leaving it blank was the
    defect — a third of the landlord's block was untiled and unexplained. Rendered as a short stub
    off the deficient face it makes both faces tile at 100 %.

    `amount` is the absolute imbalance in flow units (the renderer formats and signs it),
    `anchor` its offset above the bottom of that face — always the top of the tiled part, since
    ribbons stack from the bottom — and `is_outgoing` says which face is short: True when more
    arrives than leaves (a net gain), False when the node pays out more than it takes in.
    """

    node: str
    amount: float
    anchor: float
    is_outgoing: bool


@dataclass(frozen=True)
class SankeyGeometry:
    """Where every Sankey node sits, how wide a unit is, and where each ribbon attaches.

    The complete layout both Sankey renderers consume, so that a renderer only has to draw. `boxes`
    maps a node id to `(x of its left edge, y of its bottom, height)` in the unit square;
    `unit_scale` is the height one unit of flow occupies — *the same number in every column*, which
    is what makes a ribbon keep its width from end to end (visualization spec rule 2.7); and
    `ribbon_anchors` gives, per input ribbon and in the input's order, the offsets of its two ends
    above the bottom of their node, so the ribbons on a face tile it exactly in the crossing-
    minimizing order (Q19).

    Handing the scale out rather than letting each renderer re-derive it from a node's height is
    the whole point: dividing a node's height by what it carries reproduces a per-node scale, and
    a middle column that carries each unit twice then gets a different one from its neighbours —
    which is exactly the defect this replaced.

    `ribbon_segments` is the routed form of the same ribbons (Q29 R7): one leg per column gap, so
    a renderer draws a chain rather than one curve across whatever lies between. `ribbon_anchors`
    remains the (first out, last in) pair of each chain. `boxes` also contains the virtual nodes
    the routing introduced — their ids start with `SankeyLayout.VIRTUAL_NODE_PREFIX` and they are
    deliberately *not* in any caller's column list, so a renderer that draws the columns it was
    handed never draws them. `net_stubs` closes the faces that ribbons do not fill.
    """

    boxes: Dict[str, Tuple[float, float, float]]
    unit_scale: float
    ribbon_anchors: List[Tuple[float, float]]
    ribbon_segments: List[List[RibbonSegment]] = field(default_factory=list)
    net_stubs: List[NetStub] = field(default_factory=list)


def _place_nodes(
    columns: List[List[str]], values_by_node: Dict[str, float], unit_scale: float
) -> Dict[str, Tuple[float, float, float]]:
    """Turns a column ordering into node rectangles under a given global scale.

    Factored out of `sankey_node_boxes` because the barycenter sweeps need the *positions* an
    ordering produces in order to score the next one, and re-deriving them by hand would let the
    heuristic optimize a layout that is not the one drawn. Columns are vertically centred, so a
    column carrying less than the fullest one sits in the middle rather than sinking to the floor.
    """
    boxes: Dict[str, Tuple[float, float, float]] = {}
    column_count = max(len(columns), 1)
    for index, nodes in enumerate(columns):
        if not nodes:
            continue
        x = index * (1.0 - SankeyLayout.NODE_WIDTH) / max(column_count - 1, 1)
        gaps = SankeyLayout.NODE_GAP * max(len(nodes) - 1, 0)
        column_height = unit_scale * sum(values_by_node.get(node, 0.0) for node in nodes) + gaps
        y = max((1.0 - column_height) / 2.0, 0.0)
        for node in nodes:
            height = unit_scale * values_by_node.get(node, 0.0)
            boxes[node] = (x, y, height)
            y += height + SankeyLayout.NODE_GAP
    return boxes


def _barycenter_order(
    columns: List[List[str]],
    ribbons: List[Tuple[str, str, float]],
    values_by_node: Dict[str, float],
    unit_scale: float,
) -> List[List[str]]:
    """Reorders the nodes of each column to reduce ribbon crossings (Q19).

    The standard barycenter heuristic of layered graph drawing: a node wants to sit at the
    flow-weighted mean height of the nodes it is connected to in the neighbouring column, so
    sorting each column by that value pulls connected pairs level with each other and untangles
    the ribbons between them. Sweeps alternate left-to-right and right-to-left, because a single
    direction only ever tidies one side of each column, and stop as soon as a full sweep changes
    nothing or after `SankeyLayout.BARYCENTER_SWEEPS` passes.

    Determinism is a requirement, not a nicety — a report re-rendered from the same result must
    be byte-identical, so the sort key ends in the flow volume and the node id and nothing here
    consults a hash order or a random seed. A node with no link into the reference column keeps
    its current height as its barycenter, which leaves it where it was instead of collecting all
    unconnected nodes at the floor.

    Args:
        columns: The caller's node order per column, left to right.
        ribbons: `(source, target, amount)` triples; same-column links are ignored, since they
            connect two nodes whose relative order this heuristic is deciding.
        values_by_node: Node id -> flow volume, for the node heights the sweeps score against.
        unit_scale: The global unit-to-height scale, so the sweeps see the drawn geometry.

    Returns:
        A new list of columns with the nodes reordered; the input is not mutated.
    """
    order = [list(nodes) for nodes in columns]
    column_of = {node: index for index, nodes in enumerate(order) for node in nodes}
    links: Dict[str, List[Tuple[str, float]]] = {}
    for source, target, amount in ribbons:
        if column_of.get(source) is None or column_of.get(target) is None:
            continue
        if column_of[source] == column_of[target]:
            continue
        links.setdefault(source, []).append((target, amount))
        links.setdefault(target, []).append((source, amount))
    legs = [
        (source, target)
        for source, target, _amount in ribbons
        if column_of.get(source) is not None
        and column_of.get(target) is not None
        and column_of[source] != column_of[target]
    ]
    best = [list(nodes) for nodes in order]
    best_score = _crossing_count(best, legs, column_of)
    for sweep in range(SankeyLayout.BARYCENTER_SWEEPS):
        centers = {
            node: y + height / 2.0
            for node, (_x, y, height) in _place_nodes(order, values_by_node, unit_scale).items()
        }
        left_to_right = sweep % 2 == 0
        indices = range(1, len(order)) if left_to_right else range(len(order) - 2, -1, -1)
        changed = False
        for index in indices:
            reference = index - 1 if left_to_right else index + 1
            reordered = sorted(
                order[index],
                key=lambda node: (
                    _barycenter_of(node, reference, links, column_of, centers),
                    -values_by_node.get(node, 0.0),
                    node,
                ),
            )
            if reordered != order[index]:
                order[index] = reordered
                changed = True
        order = _transposed(order, legs, column_of)
        score = _crossing_count(order, legs, column_of)
        if score < best_score:
            best_score, best = score, [list(nodes) for nodes in order]
        if not changed and order == best:
            break
    return best


def _crossing_count(
    order: List[List[str]], legs: List[Tuple[str, str]], column_of: Dict[str, int]
) -> int:
    """Edge crossings of a layered ordering: inverted pairs within each column gap (Q29 R7).

    The exact count for a layered drawing, not a proxy: two edges of the same gap cross precisely
    when their endpoints appear in opposite order in the two columns, which is a comparison of
    positions and needs no geometry. It is what the ordering is now *scored* by — barycenter
    sweeps are a heuristic and can make a picture worse, as run 1's rented view showed, so the
    layout keeps the best-scoring pass rather than the last one.
    """
    position = {node: index for nodes in order for index, node in enumerate(nodes)}
    by_gap: Dict[int, List[Tuple[str, str]]] = {}
    for source, target in legs:
        by_gap.setdefault(min(column_of[source], column_of[target]), []).append((source, target))
    total = 0
    for pairs in by_gap.values():
        for first in range(len(pairs)):
            for second in range(first + 1, len(pairs)):
                source_a, target_a = pairs[first]
                source_b, target_b = pairs[second]
                if (position[source_a] - position[source_b]) * (
                    position[target_a] - position[target_b]
                ) < 0:
                    total += 1
    return total


def _transposed(
    order: List[List[str]], legs: List[Tuple[str, str]], column_of: Dict[str, int]
) -> List[List[str]]:
    """The transpose step of the Sugiyama method: adjacent swaps while they reduce crossings.

    Barycenter placement gets the columns roughly right and then stops improving; swapping
    neighbours and keeping the swap only when the exact crossing count falls is what removes the
    last tangles, and it is the standard companion pass. Deterministic: columns are visited left
    to right, pairs bottom to top, and a swap is kept only on a strict improvement, so equal-cost
    alternatives never flip a re-render.
    """
    current = [list(nodes) for nodes in order]
    score = _crossing_count(current, legs, column_of)
    for _round in range(SankeyLayout.TRANSPOSE_ROUNDS):
        improved = False
        for index, nodes in enumerate(current):
            for position in range(len(nodes) - 1):
                candidate = [list(column) for column in current]
                candidate[index][position], candidate[index][position + 1] = (
                    candidate[index][position + 1],
                    candidate[index][position],
                )
                candidate_score = _crossing_count(candidate, legs, column_of)
                if candidate_score < score:
                    current, score, improved = candidate, candidate_score, True
        if not improved:
            break
    return current


def _barycenter_of(
    node: str,
    reference_column: int,
    links: Dict[str, List[Tuple[str, float]]],
    column_of: Dict[str, int],
    centers: Dict[str, float],
) -> float:
    """Flow-weighted mean height of a node's partners in one neighbouring column.

    The score the barycenter sweeps sort on. Weighting by flow rather than counting partners is
    what makes the heuristic follow the picture: a node hanging off one fat ribbon and three
    hairlines belongs next to the fat one. A node with no partner in the reference column falls
    back to its own current height, which is the "leave it alone" answer rather than an arbitrary
    zero that would sink every unconnected node to the floor of the column.
    """
    weighted, total = 0.0, 0.0
    for partner, amount in links.get(node, []):
        if column_of.get(partner) == reference_column and amount > 0.0:
            weighted += amount * centers.get(partner, 0.0)
            total += amount
    return weighted / total if total else centers.get(node, 0.0)


def sankey_node_boxes(
    columns: List[List[str]], ribbons: List[Tuple[str, str, float]]
) -> SankeyGeometry:
    """Lays a Sankey out: one global scale, crossing-minimized order, per-ribbon anchors.

    The shared layout of the whole Sankey family (V1, V10, V11, V12), used by the matplotlib
    companions and by the inline-SVG report alike — both draw the same diagram, so both have to
    place the same node and the same ribbon end in the same spot. A node's height is `unit_scale`
    times the larger of what flows into it and what flows out of it, which makes a pass-through
    node exactly as tall as what crosses it.

    **One scale, all columns** (visualization spec rule 2.7). The scale is chosen so the *fullest*
    column exactly fills the unit square once its inter-node gaps are taken out, and every other
    column is then shorter — vertically centred, so the diagram stays balanced. Scaling each
    column independently to fill the height, which this function used to do, is what made ribbons
    change width in flight: a column carrying each unit twice (every actor in V1 is both a payer
    and a payee) got roughly half the scale of its neighbours, so the same flow arrived narrower
    than it left.

    **Corridors** (Q29 R7). A ribbon that skips a column is cut into one leg per column gap, with
    a virtual node reserving its width in every column it passes — the dummy nodes of the Sugiyama
    method. They take part in the ordering and in the space a column claims, which is what turns
    "the ribbon happens to miss the block" into "the ribbon has somewhere to go"; the invariant it
    buys is that no ribbon path intersects any node rectangle, and it is tested as one.

    **Net stubs** (Q29 R7). A node is as tall as the larger of its two faces, so an internal node
    whose inflow and outflow differ has a face its ribbons cannot fill. That remainder is the
    node's net position and is handed to the renderers as a `NetStub` to draw and label, which is
    what makes both faces of every node tile at 100 %.

    **Untangling** (Q19) is two steps, both standard and both here rather than in a renderer.
    Nodes are reordered per column by barycenter sweeps (`_barycenter_order`), and the ribbons on
    each node face are then stacked in the order of their *far* ends, so two correctly ordered
    columns are not re-tangled by the ribbons between them. The caller's column lists are read for
    membership only; their order is a starting point, not a constraint.

    Coordinates are fractions of a unit square with y growing upward; a renderer whose y grows
    downward (SVG) flips them itself.

    Args:
        columns: Node ids per column, left to right. The order within a column is the sweep's
            starting point.
        ribbons: `(source id, target id, amount)` triples; only the amounts are read here.
            Amounts are expected non-negative — a Sankey ribbon has no sign, and the callers
            encode direction in the node pair.

    Returns:
        A `SankeyGeometry` whose `ribbon_anchors` are index-aligned with `ribbons`. A node named
        in `columns` but carrying no flow gets height zero: under one global scale "no flow" is
        genuinely no height, and inventing a share for it would re-introduce a second scale
        through the back door.
    """
    routed_columns, segments, legs_of_ribbon = _route_through_corridors(columns, ribbons)
    outgoing: Dict[str, float] = {}
    incoming: Dict[str, float] = {}
    for source, target, amount in segments:
        outgoing[source] = outgoing.get(source, 0.0) + amount
        incoming[target] = incoming.get(target, 0.0) + amount
    values_by_node = {
        node: max(outgoing.get(node, 0.0), incoming.get(node, 0.0))
        for nodes in routed_columns for node in nodes
    }
    candidates = [
        max(1.0 - SankeyLayout.NODE_GAP * max(len(nodes) - 1, 0), SankeyLayout.MINIMUM_USABLE_HEIGHT)
        / sum(values_by_node.get(node, 0.0) for node in nodes)
        for nodes in routed_columns
        if sum(values_by_node.get(node, 0.0) for node in nodes) > 0.0
    ]
    unit_scale = min(candidates) if candidates else 0.0
    order = _barycenter_order(routed_columns, segments, values_by_node, unit_scale)
    boxes = _place_nodes(order, values_by_node, unit_scale)
    anchors = _ribbon_anchors(segments, boxes, unit_scale)
    ribbon_segments = [
        [
            RibbonSegment(
                source=segments[leg][0],
                target=segments[leg][1],
                out_anchor=anchors[leg][0],
                in_anchor=anchors[leg][1],
            )
            for leg in legs
        ]
        for legs in legs_of_ribbon
    ]
    return SankeyGeometry(
        boxes=boxes,
        unit_scale=unit_scale,
        ribbon_anchors=[
            (chain[0].out_anchor, chain[-1].in_anchor) if chain else (0.0, 0.0)
            for chain in ribbon_segments
        ],
        ribbon_segments=ribbon_segments,
        net_stubs=_net_stubs(columns, incoming, outgoing, unit_scale),
    )


def _route_through_corridors(
    columns: List[List[str]], ribbons: List[Tuple[str, str, float]]
) -> Tuple[List[List[str]], List[Tuple[str, str, float]], List[List[int]]]:
    """Cuts column-skipping ribbons into legs through virtual nodes (Q29 R7, Sugiyama).

    The layered-graph-drawing answer to a ribbon that crosses a column it has no business in: give
    it a *dummy node* in every column it skips, sized exactly as wide as the ribbon, and let that
    node take part in the ordering and in the space the column reserves. The ribbon is then a
    chain of ordinary neighbour-to-neighbour legs, and since a leg only ever occupies the gap
    between two adjacent columns it cannot overlap a node rectangle — the invariant that used to
    be violated by four source-to-landlord ribbons crossing straight through the tenant's block.

    Virtual ids are built from the ribbon's index and the column, so the expansion is a pure
    function of the input and a re-render is byte-identical. They are appended to the intermediate
    column in ribbon order; where they end up vertically is the barycenter sweeps' business.

    Args:
        columns: The caller's columns, left to right; read for membership and column index.
        ribbons: `(source, target, amount)` triples in the caller's order.

    Returns:
        `(columns including the virtual nodes, legs, leg indices per ribbon)`. A ribbon naming a
        node no column declares keeps its single leg, which the renderers skip as they always did.
    """
    column_of = {node: index for index, nodes in enumerate(columns) for node in nodes}
    routed = [list(nodes) for nodes in columns]
    segments: List[Tuple[str, str, float]] = []
    legs_of_ribbon: List[List[int]] = []
    for index, (source, target, amount) in enumerate(ribbons):
        source_column, target_column = column_of.get(source), column_of.get(target)
        if source_column is None or target_column is None or abs(target_column - source_column) <= 1:
            legs_of_ribbon.append([len(segments)])
            segments.append((source, target, amount))
            continue
        step = 1 if target_column > source_column else -1
        chain = [source]
        for column in range(source_column + step, target_column, step):
            virtual = f"{SankeyLayout.VIRTUAL_NODE_PREFIX}{index}:{column}"
            routed[column].append(virtual)
            chain.append(virtual)
        chain.append(target)
        legs = []
        for leg_source, leg_target in zip(chain, chain[1:]):
            legs.append(len(segments))
            segments.append((leg_source, leg_target, amount))
        legs_of_ribbon.append(legs)
    return routed, segments, legs_of_ribbon


def _net_stubs(
    columns: List[List[str]],
    incoming: Dict[str, float],
    outgoing: Dict[str, float],
    unit_scale: float,
) -> List[NetStub]:
    """The face remainders of internal nodes, as stubs to draw (Q29 R7).

    Only *internal* nodes qualify — a node with flow on both faces. A first-column source or a
    last-column sink has one empty face by definition, and closing that with a stub would draw a
    payment nobody makes; the imbalance that needs explaining is the one inside the picture, where
    a party keeps part of what it receives.

    Emitted in column order, then in the caller's node order, so the list is deterministic and a
    golden diff of the stubs reads top-to-bottom like the diagram.
    """
    stubs: List[NetStub] = []
    for nodes in columns:
        for node in nodes:
            arrives, leaves = incoming.get(node, 0.0), outgoing.get(node, 0.0)
            if arrives <= 0.0 or leaves <= 0.0:
                continue
            imbalance = abs(arrives - leaves)
            if imbalance * unit_scale < SankeyLayout.MINIMUM_STUB_HEIGHT:
                continue
            is_outgoing = arrives > leaves
            stubs.append(
                NetStub(
                    node=node,
                    amount=imbalance,
                    anchor=(leaves if is_outgoing else arrives) * unit_scale,
                    is_outgoing=is_outgoing,
                )
            )
    return stubs


def _ribbon_anchors(
    ribbons: List[Tuple[str, str, float]],
    boxes: Dict[str, Tuple[float, float, float]],
    unit_scale: float,
) -> List[Tuple[float, float]]:
    """Offsets of each ribbon's two ends above the bottom of their node face (Q19).

    The second untangling step. Once the columns are ordered, the ribbons leaving a node are
    stacked in the order of the heights their *targets* sit at, and the ribbons arriving at a node
    in the order of the heights their *sources* sit at — so a ribbon going up stays above one
    going down instead of the two crossing inside the gap between the columns. The stacking is
    cumulative and uses the global scale, which is what makes the ribbons tile the face exactly.

    Returns the anchors index-aligned with `ribbons`, so a renderer can iterate the flows in its
    own order (colour, credit-versus-cost) without disturbing the geometry. A ribbon naming a node
    the layout does not know gets `(0.0, 0.0)`; renderers skip those anyway.
    """
    def centre(node: str) -> float:
        """Vertical middle of a node, the key both stacking orders sort on."""
        x, y, height = boxes.get(node, (0.0, 0.0, 0.0))
        return y + height / 2.0

    anchors: List[Tuple[float, float]] = [(0.0, 0.0)] * len(ribbons)
    for is_outgoing in (True, False):
        by_node: Dict[str, List[int]] = {}
        for index, (source, target, _amount) in enumerate(ribbons):
            if source not in boxes or target not in boxes:
                continue
            by_node.setdefault(source if is_outgoing else target, []).append(index)
        for node, indices in by_node.items():
            offset = 0.0
            for index in sorted(
                indices,
                key=lambda i: (
                    centre(ribbons[i][1] if is_outgoing else ribbons[i][0]),
                    -ribbons[i][2],
                    ribbons[i][1] if is_outgoing else ribbons[i][0],
                ),
            ):
                out_anchor, in_anchor = anchors[index]
                anchors[index] = (offset, in_anchor) if is_outgoing else (out_anchor, offset)
                offset += ribbons[index][2] * unit_scale
    return anchors


def group_of(category: CostCategory) -> int:
    """Display-group index of a cost category.

    The accessor the HTML report and the matplotlib companions use to pick a stack segment and its
    colour for a category. It never raises for a valid `CostCategory`, because the underlying map is
    total; the index doubles as the index into `GROUP_COLORS_LIGHT`/`GROUP_COLORS_DARK` and into
    `DISPLAY_GROUPS`, which is what keeps colour, label and stacking order in lockstep.
    """
    return PresentationStyle.CATEGORY_TO_GROUP[category]


def group_name(index: int) -> str:
    """Label of a display group.

    The inverse-direction accessor, used for legends, axis labels and table headers so a group's
    human-readable name is written in exactly one place. Takes the index `group_of` returns; an
    index outside the eight defined groups is a programming error and raises.
    """
    return PresentationStyle.DISPLAY_GROUPS[index][0]
