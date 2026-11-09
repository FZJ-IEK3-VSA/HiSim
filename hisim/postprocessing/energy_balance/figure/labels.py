"""Labels of the Sankey figure: text sizes, candidate positions, greedy placement with repair, and the overlap check.

Every label is a box in canvas units. Text sizes come from ``matplotlib.textpath.TextPath`` at the label's font
size in DejaVu Sans, converted to u through the figure's fixed pixels per unit, so they need no canvas and are the
same on every machine. A *shape* is a drawn band: a sub-diagram's patch or a connector's outline, as a closed path.
A label tries its candidates in order and takes the first with the lowest score: 1,000 per overlap with a placed
label or a shape it is not drawn inside on purpose, plus its distance from the first candidate.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field, replace
from typing import ClassVar, Dict, List, Sequence, Tuple

import numpy as np
from matplotlib.font_manager import FontProperties
from matplotlib.path import Path
from matplotlib.textpath import TextPath
from matplotlib.transforms import Bbox

from hisim.postprocessing.energy_balance.figure.geometry import Box, Direction, Polygons
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants

Point = Tuple[float, float]


@dataclass(frozen=True)
class TextBlock:
    """Lines of text drawn as one label: the font size in pt, how many leading lines are bold, and the colour."""

    lines: Tuple[str, ...]
    font_pt: float
    bold_lines: int = 0
    color: str = "#1a1a1a"


@dataclass
class TextMeasure:
    """Measures text blocks in canvas units; caches each line's width, since a round measures the same texts often."""

    constants: LayoutConstants
    cache: Dict[Tuple[str, float, bool], float] = field(default_factory=dict)

    #: The font of every text of the figure, matplotlib's bundled default.
    FONT_FAMILY: ClassVar[str] = "DejaVu Sans"
    #: The height of one line as a multiple of the font size (matplotlib's line spacing 1.2 plus the descent).
    LINE_HEIGHT_FACTOR: ClassVar[float] = 1.25
    #: Added to every measured width, in u: the rendered glyphs reach up to about 0.05 u past the path's advance.
    WIDTH_PAD_IN_UNITS: ClassVar[float] = 0.08

    def line_width_pt(self, text: str, font_pt: float, bold: bool) -> float:
        """Return the advance width of one line of text, in pt."""
        key = (text, font_pt, bold)
        if key not in self.cache:
            prop = FontProperties(family=TextMeasure.FONT_FAMILY, weight="bold" if bold else "normal")
            extents = TextPath((0, 0), text, size=font_pt, prop=prop).get_extents() if text.strip() else None
            self.cache[key] = float(extents.x1) if extents is not None else 0.0
        return self.cache[key]

    def size(self, block: TextBlock) -> Tuple[float, float]:
        """Return the (width, height) of a text block, in u."""
        units_per_pt = self.constants.dpi / 72.0 / self.constants.px_per_unit
        widths = [self.line_width_pt(line, block.font_pt, k < block.bold_lines) for k, line in enumerate(block.lines)]
        height = len(block.lines) * block.font_pt * TextMeasure.LINE_HEIGHT_FACTOR
        return max(widths, default=0.0) * units_per_pt + TextMeasure.WIDTH_PAD_IN_UNITS, height * units_per_pt


@dataclass(frozen=True)
class Shape:
    """A drawn band as a closed path: a sub-diagram's patch or a connector's outline, with its owner key and nodes."""

    key: str
    path: Path
    box: Box
    nodes: Tuple[str, ...]

    @staticmethod
    def of(key: str, polygon: np.ndarray, nodes: Tuple[str, ...]) -> "Shape":
        """Return the shape of a closed polygon."""
        box = Box(float(polygon[:, 0].min()), float(polygon[:, 1].min()), float(polygon[:, 0].max()), float(polygon[:, 1].max()))
        return Shape(key, Polygons.closed_path(polygon), box, nodes)

    def hits(self, box: Box) -> bool:
        """Return whether a box shares an area with the shape (a box inside the shape counts)."""
        if not box.intersects(self.box):
            return False
        return bool(self.path.intersects_bbox(Bbox.from_extents(box.x0, box.y0, box.x1, box.y1), filled=True))

    def crosses(self, other: "Shape") -> bool:
        """Return whether two shapes share an area (one inside the other counts)."""
        if not self.box.intersects(other.box):
            return False
        return bool(self.path.intersects_path(other.path, filled=True))


@dataclass(frozen=True)
class Candidate:
    """One possible position of a label: its box, the alignment of its lines, and its own lines if any.

    ``lines`` holds the text when it differs from the label's own (the name wrapped to two lines); empty means the
    label's lines.
    """

    box: Box
    align: str
    lines: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LabelSpec:
    """A label to place: its key, text, candidates, the shapes it may overlap and its order weight.

    Attributes:
        key: "flow:<flow id>", "link:<link id>" or "title:<node>".
        block: The text.
        candidates: The positions to try, the preferred first.
        exempt: Keys of shapes the label may lie on (its own sub-diagram for a title, its band for an inside label).
        weight: Labels of wider bands are placed first.
        inside: Whether the first candidate lies inside a band (drawn bold in the block's colour there).
        grows: What the search lengthens when the label still overlaps: "path:<flow id>" or "trunk:<node>", or "".
    """

    key: str
    block: TextBlock
    candidates: Tuple[Candidate, ...]
    exempt: Tuple[str, ...]
    weight: float
    inside: bool
    grows: str


@dataclass(frozen=True)
class PlacedLabel:
    """A label at its chosen position."""

    key: str
    block: TextBlock
    box: Box
    align: str
    inside: bool
    grows: str
    exempt: Tuple[str, ...]


class Candidates:
    """The candidate positions of each kind of label."""

    @staticmethod
    def anchored(point: Point, ha: str, va: str, size: Tuple[float, float]) -> Candidate:
        """Return the box of a label anchored at a point with matplotlib's alignments.

        Example: anchored at (0, 0) with ha left and va top, a 2 x 1 label spans (0, -1) to (2, 0).
        """
        width, height = size
        x0 = {"left": point[0], "right": point[0] - width}.get(ha, point[0] - width / 2)
        y0 = {"bottom": point[1], "top": point[1] - height}.get(va, point[1] - height / 2)
        return Candidate(Box(x0, y0, x0 + width, y0 + height), ha if ha in ("left", "right") else "center")

    @staticmethod
    def away_alignment(away: Direction) -> Tuple[str, str]:
        """Return the alignment that anchors a label on the side of a point facing ``away``: east -> (left, center)."""
        return {Direction.E: ("left", "center"), Direction.W: ("right", "center"), Direction.N: ("center", "bottom"), Direction.S: ("center", "top")}[away]

    @staticmethod
    def beside_point(point: Point, toward: Direction, size: Tuple[float, float], gap: float) -> Candidate:
        """Return the box of a label ``gap`` u from a point in the given direction, anchored away from it."""
        x, y = point
        moved = (x + toward.vector[0] * gap, y + toward.vector[1] * gap)
        ha, va = Candidates.away_alignment(toward)
        return Candidates.anchored(moved, ha, va, size)

    @staticmethod
    def terminal(stub: Tuple[Point, Point, float], variants: Sequence[Tuple[Tuple[float, float], Tuple[str, ...]]], gap: float) -> List[Candidate]:
        """Return the candidates of a terminal branch's label: beyond its outer end, beside its stub, beside its end.

        ``stub`` is (outer end, stub middle, band width): the outer end is the arrow's tip for an output and the
        notch's outer edge for an input. The list is repeated for each text variant given as (size, lines): the plain
        name, then the name wrapped.
        """
        outer, middle, width = stub
        dx, dy = outer[0] - middle[0], outer[1] - middle[1]
        away = (Direction.E if dx > 0 else Direction.W) if abs(dx) >= abs(dy) else (Direction.N if dy > 0 else Direction.S)
        half = width / 2
        result: List[Candidate] = []
        for size, lines in variants:
            variant = [Candidates.beside_point(outer, away, size, gap)]
            for side in (away.turned_left(), away.turned_right()):
                variant.append(Candidates.beside_point((middle[0] + side.vector[0] * half, middle[1] + side.vector[1] * half), side, size, gap))
            for side in (away.turned_left(), away.turned_right()):
                variant.append(Candidates.beside_point((outer[0] + side.vector[0] * half, outer[1] + side.vector[1] * half), side, size, gap))
            result.extend(replace(candidate, lines=lines) for candidate in variant)
        return result

    @staticmethod
    def link(points: Sequence[Point], width: float, size: Tuple[float, float], gap: float) -> List[Candidate]:
        """Return the candidates of a band's label, the preferred first.

        First inside the band on its longest horizontal segment when the band is at least as wide as the label is
        high and the segment long enough, then above, below, left and right of each segment's middle and beside its
        quarter and tenth points, from the longest segment to the shortest.
        """
        result: List[Candidate] = []
        segments = sorted(zip(points, points[1:]), key=lambda s: -(abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1])))
        label_width, label_height = size
        for (x_a, y_a), (x_b, y_b) in segments:
            if abs(y_a - y_b) < 1e-9 and abs(x_a - x_b) >= label_width + 2 * gap and width >= label_height + gap:
                cx = (x_a + x_b) / 2
                result.append(Candidate(Box(cx - label_width / 2, y_a - label_height / 2, cx + label_width / 2, y_a + label_height / 2), "center"))
                break
        for (x_a, y_a), (x_b, y_b) in segments:
            horizontal = abs(y_a - y_b) < 1e-9
            order = (Direction.N, Direction.S, Direction.W, Direction.E) if horizontal else (Direction.E, Direction.W, Direction.N, Direction.S)
            for fraction, sides in ((0.5, order), (0.25, order[:2]), (0.75, order[:2]), (0.1, order[:2]), (0.9, order[:2])):
                point = (x_a + (x_b - x_a) * fraction, y_a + (y_b - y_a) * fraction)
                for side in sides:
                    anchor = (point[0] + side.vector[0] * width / 2, point[1] + side.vector[1] * width / 2)
                    result.append(Candidates.beside_point(anchor, side, size, gap))
        return result

    @staticmethod
    def title(centre: Point, trunk: Box, size: Tuple[float, float], gap: float) -> List[Candidate]:
        """Return the candidates of a title block: at the trunk centre, then beside each of the trunk's four edges."""
        cx, cy = centre
        width, height = size
        result = [Candidate(Box(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2), "center")]
        result.append(Candidates.beside_point((trunk.x1, cy), Direction.E, size, gap))
        result.append(Candidates.beside_point((trunk.x0, cy), Direction.W, size, gap))
        result.append(Candidates.beside_point((cx, trunk.y1), Direction.N, size, gap))
        result.append(Candidates.beside_point((cx, trunk.y0), Direction.S, size, gap))
        return result

    @staticmethod
    def wrapped(name: str, width_in_characters: int) -> str:
        """Return a label name wrapped to lines of at most the given characters (a name already on lines is kept)."""
        if "\n" in name:
            return name
        return "\n".join(textwrap.wrap(name, width_in_characters)) or name


@dataclass(frozen=True)
class Overlap:
    """One overlap finding: its kind ("label/label", "label/band", "band/band" or "connector/band") and the two owners."""

    kind: str
    first: str
    second: str


class LabelPlacer:
    """Places labels greedily by weight, then repairs the ones that still overlap."""

    #: The score of one overlap.
    OVERLAP_SCORE: ClassVar[float] = 1000.0
    #: How many repair passes follow the greedy pass.
    REPAIR_PASSES: ClassVar[int] = 2

    @staticmethod
    def score(candidate: Candidate, first: Candidate, spec: LabelSpec, shapes: Sequence[Shape], placed: Sequence[PlacedLabel]) -> float:
        """Return a candidate's score: 1,000 per overlap with a placed label or a non-exempt shape, plus its distance in u."""
        overlaps = sum(1 for shape in shapes if shape.key not in spec.exempt and shape.hits(candidate.box))
        overlaps += sum(1 for label in placed if label.key != spec.key and candidate.box.intersects(label.box))
        (x_a, y_a), (x_b, y_b) = candidate.box.centre, first.box.centre
        return LabelPlacer.OVERLAP_SCORE * overlaps + abs(x_a - x_b) + abs(y_a - y_b)

    @staticmethod
    def best(spec: LabelSpec, shapes: Sequence[Shape], placed: Sequence[PlacedLabel]) -> PlacedLabel:
        """Return the label at its best candidate (the first of the lowest score); outside its band an inside label is dark."""
        first = spec.candidates[0]
        chosen = min(spec.candidates, key=lambda c: LabelPlacer.score(c, first, spec, shapes, placed))
        inside = spec.inside and chosen is first
        lines = chosen.lines or spec.block.lines
        if inside or not spec.inside:
            block = TextBlock(lines, spec.block.font_pt, spec.block.bold_lines, spec.block.color)
        else:
            block = TextBlock(lines, spec.block.font_pt, 0, "#1a1a1a")
        exempt = spec.exempt if inside or spec.key.startswith("title:") else ()
        return PlacedLabel(spec.key, block, chosen.box, chosen.align, inside, spec.grows, exempt)

    @staticmethod
    def place(specs: Sequence[LabelSpec], shapes: Sequence[Shape]) -> List[PlacedLabel]:
        """Place every label: titles first, then by weight descending; then re-place overlapping labels twice."""
        ordered = sorted(specs, key=lambda s: (0 if s.key.startswith("title:") else 1, -s.weight, s.key))
        placed: List[PlacedLabel] = []
        for spec in ordered:
            placed.append(LabelPlacer.best(spec, shapes, placed))
        for _ in range(LabelPlacer.REPAIR_PASSES):
            for index, spec in enumerate(ordered):
                if len(spec.candidates) == 1 or LabelPlacer.count(placed[index], shapes, placed) == 0:
                    continue
                placed[index] = LabelPlacer.best(spec, shapes, [p for k, p in enumerate(placed) if k != index])
        return placed

    @staticmethod
    def count(label: PlacedLabel, shapes: Sequence[Shape], placed: Sequence[PlacedLabel]) -> int:
        """Return how many shapes and other labels a placed label overlaps."""
        overlaps = sum(1 for shape in shapes if shape.key not in label.exempt and shape.hits(label.box))
        return overlaps + sum(1 for other in placed if other.key != label.key and label.box.intersects(other.box))


class OverlapCheck:
    """The figure's overlap findings: labels over labels or bands, bands of different sub-diagrams, connectors over bands."""

    @staticmethod
    def labels(placed: Sequence[PlacedLabel], shapes: Sequence[Shape]) -> List[Overlap]:
        """Return every label/label and label/band overlap, each pair once, in label order."""
        result = []
        for index, label in enumerate(placed):
            for other in placed[index + 1 :]:
                if label.box.intersects(other.box):
                    result.append(Overlap("label/label", label.key, other.key))
            for shape in shapes:
                if shape.key not in label.exempt and shape.hits(label.box):
                    result.append(Overlap("label/band", label.key, shape.key))
        return result

    @staticmethod
    def bands(patches: Sequence[Shape], connected: Sequence[Tuple[str, str]]) -> List[Overlap]:
        """Return every pair of sub-diagram patches that share an area, but a prior and its child (they meet at a tip)."""
        joined = {frozenset(pair) for pair in connected}
        result = []
        for index, first in enumerate(patches):
            for second in patches[index + 1 :]:
                if frozenset((first.nodes[0], second.nodes[0])) in joined:
                    continue
                if first.crosses(second):
                    result.append(Overlap("band/band", first.key, second.key))
        return result

    @staticmethod
    def connectors(trimmed: Sequence[Shape], shapes: Sequence[Shape]) -> List[Overlap]:
        """Return every connector (ends trimmed, narrowed) that crosses a patch or another connector."""
        result = []
        for connector in trimmed:
            for shape in shapes:
                if shape.key != connector.key and connector.crosses(shape):
                    result.append(Overlap("connector/band", connector.key, shape.key))
        return result
