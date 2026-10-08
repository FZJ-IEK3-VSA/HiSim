"""Display grouping, palette and chart layout geometry shared by every report output.

The cost categories of `timeline.CostCategory` fold onto 8 display groups, so one fixed categorical palette colours
them the same in the HTML report, its SVG charts and the matplotlib PNGs. The group sums themselves are computed by
`views.fold_categories`, which takes `CATEGORY_TO_GROUP` as an argument. The module also holds the neutral chart
colours and the treemap and Sankey layouts both renderers draw, so the two never place the same tile differently. It
imports only `timeline.CostCategory` (pinned by ``tests/test_economics_import_lint.py``).
"""

from __future__ import annotations

import math
import types
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Tuple

from hisim.economics.timeline import CostCategory


def _build_category_to_group(
    display_groups: List[Tuple[str, Tuple[CostCategory, ...]]]
) -> Dict[CostCategory, int]:
    """Invert the display groups into a category-to-group-index map that covers every `CostCategory`.

    A category no group declares fails the import of this module by name, instead of being silently summed and coloured
    as some other group. The result can be handed to `views.fold_categories`, which rejects gaps.

    Args:
        display_groups: The ordered display groups with the categories each declares.

    Returns:
        Every `CostCategory` member mapped to its group index.

    Raises:
        ValueError: If any `CostCategory` member is declared by no group; the message names them.
    """
    declared = {
        category: index for index, (_name, categories) in enumerate(display_groups) for category in categories
    }
    undeclared = [category.value for category in CostCategory if category not in declared]
    if undeclared:
        raise ValueError(
            "Every CostCategory has to belong to exactly one display group, but no group declares "
            f"{', '.join(undeclared)}. Add each of them to a group in "
            "PresentationStyle.DISPLAY_GROUPS -- a category that silently landed in group 0 was "
            "summed, coloured and legended as investment in every report."
        )
    return {category: declared[category] for category in CostCategory}


class PresentationStyle:
    """The display grouping and its categorical palette: ordered groups, light and dark colours, and the category map.

    There are more cost categories than a categorical palette can tell apart, so they are grouped, and the fixed order
    keeps a group's hue and stack position the same in every output. This class labels and colours sums; it never
    decides them.
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

    #: Category -> group-index mapping covering every `CostCategory` member, so it can be handed to
    #: `views.fold_categories` (which rejects gaps). An undeclared category fails the import of this module.
    CATEGORY_TO_GROUP: Dict[CostCategory, int] = _build_category_to_group(DISPLAY_GROUPS)


class ChromeColors:
    """The four neutral, non-data chart colours (surface, ink, muted, grid), keyed by role name.

    Example: ``ChromeColors.LIGHT["surface"]``. The HTML report declares them as CSS custom properties in
    ``reporting/sections.py`` and ``report_plots._Palette`` aliases `LIGHT`, so SVG and PNG charts share one grey. The
    report switches to `DARK` under ``prefers-color-scheme: dark``; a PNG is baked once and always uses `LIGHT`. Both
    maps are read-only proxies, so a renderer cannot change a colour for every later chart.
    """

    LIGHT: Mapping[str, str] = types.MappingProxyType({
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "muted": "#898781",
        "grid": "#e1e0d9",
    })
    DARK: Mapping[str, str] = types.MappingProxyType({
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "muted": "#898781",
        "grid": "#2c2c2a",
    })


class SequentialRamp:
    """A ten-step sequential ramp for the one chart whose series are an ordered quantity.

    The bank benchmark's rate fan shows ten interest rates from 1 % to 10 %; a categorical palette repeats after eight
    hues, so an ordered ramp is used. Steps run light to dark in the report's ink, monotone in lightness only. `LIGHT`
    and `DARK` serve the two colour schemes like `ChromeColors`; both are tuples so no renderer can alter them.
    """

    #: Step 0 (1 %) to step 9 (10 %) on a light surface: muted to ink, monotone in luminance.
    LIGHT: Tuple[str, ...] = (
        "#c6d2e9", "#b0c0e0", "#9bafd6", "#8599cb", "#6f84c0",
        "#5a6faa", "#46588a", "#33436b", "#22304e", "#141d31",
    )
    #: The same ten steps against a dark surface, and in the same direction: step 0 is the tone
    #: nearest the surface and step 9 the one nearest that theme's ink, which there is near-white.
    #: "Further along the ramp" therefore still means "louder" rather than "harder to see".
    DARK: Tuple[str, ...] = (
        "#33445f", "#425573", "#526687", "#63789b", "#7589af",
        "#8b9dc2", "#a2b1d3", "#bac6e2", "#d0d9ee", "#e4eafa",
    )


def squarified_layout(
    values: List[float], x: float, y: float, width: float, height: float
) -> List[Tuple[float, float, float, float]]:
    """Lay out a squarified treemap: one rectangle per value, in the input's order.

    Rows (or columns, along the longer side of the remaining box) are filled so tiles stay close to square, which makes
    areas comparable by eye; the rectangles tile the box exactly. Tiles are near-square only if values come largest
    first; the function does not sort, because the caller's order pairs each rectangle with its label and colour. Both
    the PNG and the SVG report use this layout; the ``squarify`` package is not a dependency.

    Args:
        values: Tile areas in any unit, each positive and finite, ideally largest first. An empty list lays out
            nothing.
        x: Left edge of the box to fill.
        y: Bottom (or top, in the caller's convention) edge of the box.
        width: Box width in the same units as `x`.
        height: Box height.

    Returns:
        One `(x, y, width, height)` per input value, in input order.

    Raises:
        ValueError: If any value is zero, negative or not finite; the message names the index and the value.
    """
    for index, value in enumerate(values):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                f"squarified_layout needs positive, finite areas, but values[{index}] is {value!r}. "
                "A treemap tile is an area: zero has no rectangle, a negative one has no meaning, "
                "and either would be laid out without complaint. Filter or fix the amount at the "
                "call site, where it is known whether it is an empty category or a sign error."
            )
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
    """Return the worst width/height ratio of a candidate treemap row, squarify's quality measure.

    The layout keeps adding tiles to a row while this does not get worse. A zero-area row or tile counts as infinitely
    bad, so it never wins a comparison; `squarified_layout` already refuses such values, so this only guards against a
    `ZeroDivisionError`.
    """
    total = sum(row)
    if total <= 0:
        return float("inf")
    largest, smallest = max(row), min(row)
    if smallest <= 0:
        return float("inf")
    return max(side * side * largest / (total * total), (total * total) / (side * side * smallest))


class SankeyLayout:
    """Shared geometry constants of the Sankey charts, as fractions of the unit square.

    `CURVATURE` is the share of the horizontal gap at which the Bézier control points sit: 0 draws straight trapezoids,
    1 makes ribbons leave and arrive horizontally. Every internal party has its own column, so a transfer between
    actors is an ordinary ribbon between adjacent columns.
    """

    NODE_WIDTH = 0.035
    NODE_GAP = 0.012
    CURVATURE = 0.42
    #: Smallest usable fraction of the unit square a column may be squeezed into by its node gaps;
    #: a diagram with more nodes than gaps fit keeps drawing rather than collapsing to nothing.
    MINIMUM_USABLE_HEIGHT = 0.1
    #: Alternating barycenter passes over the columns. The ordering is almost always stable after two; more
    #: passes cost time and risk a two-cycle that never settles.
    BARYCENTER_SWEEPS = 4
    #: Passes of the adjacent-swap refinement after each barycenter sweep. It stops as soon as a full pass
    #: improves nothing, so the bound only caps a pathological input.
    TRANSPOSE_ROUNDS = 8
    #: Prefix of the virtual (dummy) nodes that give a column-skipping ribbon a corridor to route through.
    #: Every id built here starts with it, so a renderer can assert that it never draws one.
    VIRTUAL_NODE_PREFIX = "__via:"
    #: Length of a net-position stub as a fraction of the horizontal column pitch. Long enough to
    #: read as a flow leaving the face, far too short to be mistaken for a ribbon to a neighbour.
    STUB_LENGTH = 0.28
    #: Height (as a fraction of the unit square) below which a face remainder is float noise
    #: rather than a net position, and no stub is emitted for it.
    MINIMUM_STUB_HEIGHT = 1e-4


@dataclass(frozen=True)
class RibbonSegment:
    """One column-to-column leg of a Sankey ribbon, with the offsets of its two ends.

    A ribbon that skips columns is cut at each intermediate column, where a virtual node reserves its width, and drawn
    as a chain of legs; a ribbon between neighbouring columns has one leg. `out_anchor` is the start's offset above the
    bottom of the source node's right face, `in_anchor` the same for the target node's left face. The ribbon's own ends
    are the first leg's `out_anchor` and the last leg's `in_anchor`.
    """

    source: str
    target: str
    out_anchor: float
    in_anchor: float


@dataclass(frozen=True)
class NetStub:
    """The part of a Sankey node's face that its ribbons do not fill: the node's net position.

    A node is as tall as the larger of its inflow and outflow, so an actor receiving more than it passes on has an
    unfilled outgoing face; the stub shows that net gain (or loss) so both faces are fully tiled. `amount` is the
    absolute imbalance in flow units, `anchor` its offset above the bottom of the face (ribbons stack from the bottom),
    and `is_outgoing` is True when more arrives than leaves.
    """

    node: str
    amount: float
    anchor: float
    is_outgoing: bool


@dataclass(frozen=True)
class SankeyGeometry:
    """The complete Sankey layout both renderers draw: node boxes, the unit scale, ribbon legs and net stubs.

    `boxes` maps a node id to `(x of left edge, y of bottom, height)` in the unit square. `unit_scale` is the height
    one unit of flow occupies, the same in every column, so a ribbon keeps its width end to end; renderers must use it
    rather than derive a per-node scale. `ribbon_segments` gives, per input ribbon and in input order, its chain of
    legs (one per column gap) with end offsets that tile each face in crossing-minimizing order. `boxes` also holds the
    virtual routing nodes (ids start with `SankeyLayout.VIRTUAL_NODE_PREFIX`), which no caller's column list contains,
    so they are never drawn. `net_stubs` closes the faces ribbons do not fill.
    """

    boxes: Dict[str, Tuple[float, float, float]]
    unit_scale: float
    ribbon_segments: List[List[RibbonSegment]] = field(default_factory=list)
    net_stubs: List[NetStub] = field(default_factory=list)


def _place_nodes(
    columns: List[List[str]], values_by_node: Dict[str, float], unit_scale: float
) -> Dict[str, Tuple[float, float, float]]:
    """Turn a column ordering into node rectangles under the given global scale.

    The barycenter sweeps use it to score an ordering by the positions it actually produces. Columns are vertically
    centred.
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
    """Reorder the nodes of each column to reduce ribbon crossings (barycenter heuristic).

    Each node is sorted by the flow-weighted mean height of its partners in the neighbouring column. Sweeps alternate
    left-to-right and right-to-left and stop when a sweep changes nothing or after `SankeyLayout.BARYCENTER_SWEEPS`
    passes. The result is deterministic (ties broken by flow volume and node id), so a re-rendered report is
    byte-identical. A node without partners in the reference column keeps its current height.

    Args:
        columns: The caller's node order per column, left to right.
        ribbons: `(source, target, amount)` triples; same-column links are ignored.
        values_by_node: Node id to flow volume, for the node heights.
        unit_scale: The global unit-to-height scale.

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
                key=lambda node, reference=reference, centers=centers: (
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
    """Count the edge crossings of a layered ordering: inverted pairs within each column gap.

    Two edges of the same gap cross exactly when their endpoints appear in opposite order in the two columns. The
    layout keeps the ordering with the lowest count, since barycenter sweeps can make a picture worse. Every call
    recounts from scratch, which is fine for a dozen nodes; delta scoring per gap would be the upgrade for much larger
    diagrams.
    """
    position = {node: index for nodes in order for index, node in enumerate(nodes)}
    by_gap: Dict[int, List[Tuple[str, str]]] = {}
    for source, target in legs:
        by_gap.setdefault(min(column_of[source], column_of[target]), []).append((source, target))
    total = 0
    for pairs in by_gap.values():
        for first, (source_a, target_a) in enumerate(pairs):
            for source_b, target_b in pairs[first + 1:]:
                if (position[source_a] - position[source_b]) * (
                    position[target_a] - position[target_b]
                ) < 0:
                    total += 1
    return total


def _transposed(
    order: List[List[str]], legs: List[Tuple[str, str]], column_of: Dict[str, int]
) -> List[List[str]]:
    """Swap adjacent nodes while the swap reduces the crossing count (the Sugiyama transpose step).

    Removes the tangles barycenter placement leaves. Columns are visited left to right and pairs bottom to top, and a
    swap is kept only on a strict improvement, so the result is deterministic. Each candidate is scored by a full
    `_crossing_count`, which is fine at report scale.
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
    """Return the flow-weighted mean height of a node's partners in one neighbouring column.

    Weighting by flow puts a node next to its fattest ribbon. A node with no partner in the reference column returns
    its own current height, so it stays where it is.
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
    """Lay out a Sankey diagram: one global scale, crossing-minimized order and per-ribbon anchors.

    Used by both the matplotlib charts and the inline-SVG report so they place every node and ribbon end identically. A
    node's height is `unit_scale` times the larger of its inflow and outflow. Coordinates are fractions of a unit
    square with y growing upward; an SVG renderer flips them.

    - One scale for all columns: the fullest column fills the height after gaps, others are shorter and centred, so
      ribbons keep their width.
    - Corridors: a ribbon that skips columns is cut into one leg per gap, with a virtual node reserving its width in
      each skipped column, so no ribbon crosses a node rectangle.
    - Net stubs: a node face its ribbons do not fill gets a `NetStub` showing the net position.
    - Untangling: nodes are reordered per column by barycenter sweeps and adjacent swaps, and ribbons on each face are
      stacked in the order of their far ends. The caller's column order is only a starting point.

    Args:
        columns: Node ids per column, left to right.
        ribbons: `(source id, target id, amount)` triples; each amount positive and finite, both ids declared in
            `columns`. An empty list places all nodes with height zero and returns `unit_scale` 0.0.

    Returns:
        A `SankeyGeometry` whose `ribbon_segments` align with `ribbons` by index. A declared node without flow gets
            height zero.

    Raises:
        ValueError: If a ribbon amount is zero, negative or not finite, or a ribbon names a node no column declares;
            the message names the ribbon.
    """
    for index, (source, target, amount) in enumerate(ribbons):
        if not math.isfinite(amount) or amount <= 0.0:
            raise ValueError(
                f"Sankey ribbon {index} ({source!r} -> {target!r}) carries {amount!r}. A ribbon "
                "amount has to be positive and finite: a Sankey has no sign — direction is the "
                "node pair — and a zero-width ribbon is invisible while still claiming a slot on "
                "both faces it touches. Drop or fix the flow where it is built."
            )
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
        ribbon_segments=ribbon_segments,
        net_stubs=_net_stubs(columns, incoming, outgoing, unit_scale),
    )


def _route_through_corridors(
    columns: List[List[str]], ribbons: List[Tuple[str, str, float]]
) -> Tuple[List[List[str]], List[Tuple[str, str, float]], List[List[int]]]:
    """Cut column-skipping ribbons into neighbour-to-neighbour legs through virtual nodes (Sugiyama dummy nodes).

    Each skipped column gets a virtual node as wide as the ribbon, which takes part in the ordering and the column's
    space, so no leg can overlap a node rectangle. Virtual ids are built from the ribbon index and the column, so the
    result is deterministic; they are appended to the intermediate column in ribbon order.

    Args:
        columns: The caller's columns, left to right; read for membership and column index.
        ribbons: `(source, target, amount)` triples in the caller's order.

    Returns:
        `(columns including the virtual nodes, legs, leg indices per ribbon)`.

    Raises:
        ValueError: If a ribbon names a node that appears in no column; the message names the node and the ribbon.
    """
    column_of = {node: index for index, nodes in enumerate(columns) for node in nodes}
    routed = [list(nodes) for nodes in columns]
    segments: List[Tuple[str, str, float]] = []
    legs_of_ribbon: List[List[int]] = []
    for index, (source, target, amount) in enumerate(ribbons):
        source_column, target_column = column_of.get(source), column_of.get(target)
        if source_column is None or target_column is None:
            missing = source if source_column is None else target
            raise ValueError(
                f"Sankey ribbon {index} ({source!r} -> {target!r}) names {missing!r}, which no "
                "column declares. Such a ribbon has nowhere to be drawn, so every renderer used "
                "to drop it while its amount still made the node it left taller than the ribbons "
                "that tile it. List the node in a column, or do not emit the flow."
            )
        if abs(target_column - source_column) <= 1:
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
    """Return the net-position stubs of internal nodes (nodes with flow on both faces).

    A first-column source or last-column sink has one empty face by definition and gets no stub. Stubs come in column
    order, then the caller's node order, so the list is deterministic.
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
    """Return each ribbon's two end offsets above the bottom of their node faces.

    Ribbons leaving a node are stacked by the height of their targets, ribbons arriving by the height of their sources,
    so a ribbon going up stays above one going down instead of crossing it. Stacking uses the global scale, so ribbons
    tile each face exactly. The anchors align with `ribbons` by index, so a renderer may draw flows in any order. Every
    node is in `boxes`, because unknown nodes were refused earlier.
    """
    def centre(node: str) -> float:
        """Vertical middle of a node, the key both stacking orders sort on."""
        _x, y, height = boxes[node]
        return y + height / 2.0

    anchors: List[Tuple[float, float]] = [(0.0, 0.0)] * len(ribbons)
    for is_outgoing in (True, False):
        by_node: Dict[str, List[int]] = {}
        for index, (source, target, _amount) in enumerate(ribbons):
            by_node.setdefault(source if is_outgoing else target, []).append(index)
        for indices in by_node.values():
            offset = 0.0
            for index in sorted(
                indices,
                key=lambda i, is_outgoing=is_outgoing: (
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
    """Return the display-group index of a cost category.

    Never raises for a valid `CostCategory`, since the map covers the whole enum. The index also indexes
    `GROUP_COLORS_LIGHT`, `GROUP_COLORS_DARK` and `DISPLAY_GROUPS`, keeping colour, label and stack order together.
    """
    return PresentationStyle.CATEGORY_TO_GROUP[category]


def group_name(index: int) -> str:
    """Return the label of a display group, for legends, axis labels and table headers.

    Takes the index `group_of` returns; an index outside the eight groups raises.
    """
    return PresentationStyle.DISPLAY_GROUPS[index][0]
