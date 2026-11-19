"""Building the matplotlib.sankey diagram from the parameters, and the edits the figure makes to what it drew.

The library draws every sub-diagram as one patch; the figure reads back each flow's tip, its direction and the
library's label position, moves further trees to their ``at`` position (the library puts every unconnected
sub-diagram at the origin), flattens the arrowhead of a stub where a connector continues it, and fills the notch of
an input where a connector ends or the solar cut starts. The edits follow the prototype's ``draw_sankey.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, Tuple

import numpy as np
from matplotlib.path import Path
from matplotlib.sankey import Sankey

from hisim.postprocessing.energy_balance.figure.geometry import Direction
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants, Parameters

Point = Tuple[float, float]


class SankeyBuildError(ValueError):
    """The library refused a sub-diagram (a connection that does not match, a flow below its tolerance)."""


@dataclass
class DrawnDiagram:
    """One sub-diagram as the library drew it: its patch, each flow's tip, direction and label position.

    Attributes:
        component: The drawn node.
        patch: The library's patch (edited in place by the flattening and the tree moves).
        tips: The tip of every flow in list order: an output's arrow tip, an input's notch point, in u.
        angles: The library's direction of every flow in quarter turns (0 east, 1 north, 2 west, 3 south), the
            direction the band travels at its tip; None for a flow the library did not draw.
        centre: The trunk's centre (the library's text position), in u.
        label_positions: The library's label position of every flow, in u.
        flow_ids: The flow ids in list order.
    """

    component: str
    patch: Any
    tips: np.ndarray
    angles: List[Any]
    centre: Point
    label_positions: List[Point]
    flow_ids: List[str] = field(default_factory=list)

    def index(self, flow_id: str) -> int:
        """Return a flow's position in the list."""
        return self.flow_ids.index(flow_id)

    def tip(self, flow_id: str) -> np.ndarray:
        """Return a flow's tip."""
        return np.array(self.tips[self.index(flow_id)], dtype=float)

    def direction(self, flow_id: str) -> Direction:
        """Return the page direction a flow's band travels at its tip."""
        return SankeyBuild.direction_of(self.angles[self.index(flow_id)])

    def move(self, dx: float, dy: float) -> None:
        """Shift the sub-diagram, its tips and label positions by (dx, dy) u."""
        path = self.patch.get_path()
        self.patch.set_path(Path(path.vertices + np.array([dx, dy]), path.codes))
        self.tips = self.tips + np.array([dx, dy])
        self.centre = (self.centre[0] + dx, self.centre[1] + dy)
        self.label_positions = [(x + dx, y + dy) for x, y in self.label_positions]


class SankeyBuild:
    """Builds the library's diagram and edits the drawn patches."""

    #: The page direction of each of the library's quarter-turn angles.
    DIRECTIONS: ClassVar[Mapping[int, Direction]] = {0: Direction.E, 1: Direction.N, 2: Direction.W, 3: Direction.S}
    #: The outline colour and width of every sub-diagram.
    OUTLINE: ClassVar[str] = "#404040"
    OUTLINE_WIDTH: ClassVar[float] = 0.6

    @staticmethod
    def direction_of(angle: float) -> Direction:
        """Return the page direction of one of the library's angles (quarter turns, 0 east)."""
        return SankeyBuild.DIRECTIONS[int(round(angle)) % 4]

    @staticmethod
    def build(parameters: Parameters, constants: LayoutConstants, axes: Any) -> Dict[str, DrawnDiagram]:
        """Draw every sub-diagram on the axes and return them by node, in the order they were added.

        Each sub-diagram is one ``Sankey.add`` with the parameters' flows, orientations, path lengths and trunk length,
        empty labels, and ``prior``/``connect`` from its connection (a root's ``rotation`` from its axis); then
        ``finish()``. The library's labels stay empty: the figure places its own.

        Raises:
            SankeyBuildError: If the library refuses a sub-diagram, naming it.
        """
        sankey = Sankey(
            ax=axes,
            scale=1.0 / parameters.kwh_per_unit,
            unit=None,
            format="",
            tolerance=constants.tolerance,
            gap=constants.gap,
            radius=constants.radius,
            shoulder=constants.shoulder,
            offset=constants.offset,
            head_angle=constants.head_angle,
        )
        index: Dict[str, int] = {}
        for number, diagram in enumerate(parameters.diagrams):
            options: Dict[str, Any] = {"rotation": diagram.rotation}
            if diagram.prior is not None and diagram.connect is not None and diagram.prior_flow is not None:
                prior = parameters.diagram(diagram.prior)
                options = {"prior": index[diagram.prior], "connect": (prior.index(diagram.prior_flow), diagram.index(diagram.connect))}
            try:
                sankey.add(
                    patchlabel="",
                    flows=[f.drawn_kwh for f in diagram.flows],
                    orientations=[f.orientation for f in diagram.flows],
                    pathlengths=[f.pathlength for f in diagram.flows],
                    labels=[""] * len(diagram.flows),
                    trunklength=diagram.trunklength,
                    facecolor=diagram.color,
                    edgecolor=SankeyBuild.OUTLINE,
                    linewidth=SankeyBuild.OUTLINE_WIDTH,
                    **options,
                )
            except ValueError as error:
                raise SankeyBuildError(f"the library refused the sub-diagram of {diagram.component}: {error}") from error
            index[diagram.component] = number
        drawn = sankey.finish()
        result: Dict[str, DrawnDiagram] = {}
        for diagram, library in zip(parameters.diagrams, drawn):
            result[diagram.component] = DrawnDiagram(
                diagram.component,
                library.patch,
                np.array(library.tips, dtype=float),
                list(library.angles),
                tuple(library.text.get_position()),
                [tuple(text.get_position()) for text in library.texts],
                [f.flow_id for f in diagram.flows],
            )
            library.text.set_visible(False)
        return result

    @staticmethod
    def flatten_output_head(patch: Any, tip: np.ndarray, direction: Direction, width: float, constants: LayoutConstants) -> np.ndarray:
        """Cut a stub output's arrowhead off its sub-diagram's outline and return the flat end's centre.

        The head's tip and shoulders are moved onto the head's base line, so the outline ends square where the connector
        band starts. Example: an eastward output of width 1 with its tip at (5, 0) ends flat at x = 5 - 0.596 * pitch.
        """
        d = np.array(direction.vector)
        n = np.array([-d[1], d[0]])
        tipheight = (constants.shoulder + width / 2) * constants.pitch
        path = patch.get_path()
        vertices = path.vertices.copy()
        for k, vertex in enumerate(vertices):
            along, lateral = (vertex - tip) @ d, (vertex - tip) @ n
            if -tipheight - 1e-6 <= along <= 1e-6 and abs(lateral) <= width / 2 + constants.shoulder + 1e-6:
                vertices[k] = tip - d * tipheight + n * max(-width / 2, min(width / 2, lateral))
        patch.set_path(Path(vertices, path.codes))
        return np.asarray(tip - d * tipheight, dtype=float)

    @staticmethod
    def flatten_input_notch(patch: Any, dip: np.ndarray, direction: Direction, width: float, pitch: float, back: float = 0.0) -> np.ndarray:
        """Fill an input's notch in its sub-diagram's outline and return the flat end's centre.

        The notch's inner point (the library's tip of an input) moves out to the line of its two outer corners; with
        ``back`` > 0 the corners and the point move a further ``back`` upstream, so the input meets a flattened
        arrowhead. Returns the dip itself when the outline has no vertex there (nothing to fill).
        """
        d = np.array(direction.vector)
        n = np.array([-d[1], d[0]])
        depth = (width / 2) * pitch
        path = patch.get_path()
        vertices = path.vertices.copy()
        hits = set(int(k) for k in np.where(np.hypot(*(vertices - dip).T) < 1e-6)[0])
        if not hits:
            return dip
        for k, vertex in enumerate(vertices):
            along, lateral = (vertex - dip) @ d, (vertex - dip) @ n
            if k in hits or (abs(along + depth) < 1e-6 and abs(abs(lateral) - width / 2) < 1e-6):
                vertices[k] = dip - d * (depth + back) + n * (0.0 if k in hits else lateral)
        patch.set_path(Path(vertices, path.codes))
        return np.asarray(dip - d * (depth + back), dtype=float)

    @staticmethod
    def polygons(patch: Any) -> List[np.ndarray]:
        """Return the drawn outline of a patch as closed polygons, its curves flattened, in u."""
        return [np.asarray(polygon) for polygon in patch.get_path().to_polygons(closed_only=True) if len(polygon) >= 3]

    @staticmethod
    def extent(points: Sequence[np.ndarray]) -> Tuple[float, float, float, float]:
        """Return the (x0, y0, x1, y1) box around a set of point arrays."""
        stacked = np.vstack(points)
        return float(stacked[:, 0].min()), float(stacked[:, 1].min()), float(stacked[:, 0].max()), float(stacked[:, 1].max())
