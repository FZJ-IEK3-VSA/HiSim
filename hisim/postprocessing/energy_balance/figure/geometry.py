"""Plane geometry of the Sankey figure: compass directions, axis-aligned boxes and the sides of a trunk.

Every length is in canvas units (u), the layout's length unit: a band's width in u is its drawn kWh divided by the
figure's kWh per unit. y grows upwards, as in matplotlib's data coordinates.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import ClassVar, Mapping, Tuple

from types import MappingProxyType

import numpy as np
from matplotlib.path import Path


@enum.unique
class Direction(str, enum.Enum):
    """A compass direction on the page: east is +x, north is +y."""

    E = "E"
    N = "N"
    W = "W"
    S = "S"

    @property
    def vector(self) -> Tuple[float, float]:
        """The unit vector of the direction, (dx, dy)."""
        return Compass.VECTORS[self]

    def turned_left(self) -> "Direction":
        """The direction a quarter turn counter-clockwise from this one (E -> N)."""
        return Compass.ORDER[(Compass.ORDER.index(self) + 1) % 4]

    def turned_right(self) -> "Direction":
        """The direction a quarter turn clockwise from this one (E -> S)."""
        return Compass.ORDER[(Compass.ORDER.index(self) + 3) % 4]

    def opposite(self) -> "Direction":
        """The direction half a turn from this one (E -> W)."""
        return Compass.ORDER[(Compass.ORDER.index(self) + 2) % 4]

    @property
    def is_horizontal(self) -> bool:
        """Whether the direction runs along x (E or W)."""
        return self in (Direction.E, Direction.W)


class Compass:
    """The constant tables of :class:`Direction`."""

    #: The four directions counter-clockwise, starting east.
    ORDER: ClassVar[Tuple[Direction, ...]] = (Direction.E, Direction.N, Direction.W, Direction.S)
    #: The unit vector of each direction.
    VECTORS: ClassVar[Mapping[Direction, Tuple[float, float]]] = MappingProxyType(
        {Direction.E: (1.0, 0.0), Direction.N: (0.0, 1.0), Direction.W: (-1.0, 0.0), Direction.S: (0.0, -1.0)}
    )


@enum.unique
class Side(str, enum.Enum):
    """A side of a trunk relative to its axis: the tail (upstream end), the head, or a lateral side.

    ``LEFT`` is the lateral side a quarter turn counter-clockwise from the axis (north of an eastward trunk),
    ``RIGHT`` the other one (south of an eastward trunk).
    """

    TAIL = "tail"
    HEAD = "head"
    LEFT = "left"
    RIGHT = "right"

    def outward(self, axis: Direction) -> Direction:
        """Return the compass direction this side faces for a trunk with the given axis.

        Example: the right side of an eastward trunk faces south.
        """
        if self is Side.HEAD:
            return axis
        if self is Side.TAIL:
            return axis.opposite()
        if self is Side.LEFT:
            return axis.turned_left()
        return axis.turned_right()

    @staticmethod
    def facing(compass: Direction, axis: Direction) -> "Side":
        """Return the side of a trunk with the given axis that faces the given compass direction.

        Example: for a southward trunk (a battery hanging below the meter), east is its left side.
        """
        for side in Side:
            if side.outward(axis) is compass:
                return side
        raise ValueError(f"no side of a {axis.value} trunk faces {compass.value}")  # unreachable: four sides


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangle in canvas units, from (x0, y0) to (x1, y1) with x0 <= x1 and y0 <= y1."""

    x0: float
    y0: float
    x1: float
    y1: float

    #: Two boxes that share no more than an edge within this many u do not intersect.
    TOUCH_IN_UNITS: ClassVar[float] = 1e-6

    @staticmethod
    def around(x_a: float, y_a: float, x_b: float, y_b: float) -> "Box":
        """Return the box spanned by two corner points given in any order."""
        return Box(min(x_a, x_b), min(y_a, y_b), max(x_a, x_b), max(y_a, y_b))

    @property
    def width(self) -> float:
        """The extent along x, in u."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """The extent along y, in u."""
        return self.y1 - self.y0

    @property
    def centre(self) -> Tuple[float, float]:
        """The centre point, in u."""
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def intersects(self, other: "Box") -> bool:
        """Return whether the two boxes share an area; boxes that only touch along an edge do not."""
        touch = Box.TOUCH_IN_UNITS
        return self.x0 < other.x1 - touch and other.x0 < self.x1 - touch and self.y0 < other.y1 - touch and other.y0 < self.y1 - touch

    def contains(self, other: "Box") -> bool:
        """Return whether the other box lies completely inside this one (edges included)."""
        touch = Box.TOUCH_IN_UNITS
        return self.x0 <= other.x0 + touch and self.y0 <= other.y0 + touch and other.x1 <= self.x1 + touch and other.y1 <= self.y1 + touch

    def inflated(self, margin: float) -> "Box":
        """Return the box grown by ``margin`` u on every side (shrunk for a negative margin)."""
        return Box(self.x0 - margin, self.y0 - margin, self.x1 + margin, self.y1 + margin)

    def shifted(self, dx: float, dy: float) -> "Box":
        """Return the box moved by (dx, dy) u."""
        return Box(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def union(self, other: "Box") -> "Box":
        """Return the smallest box that holds both boxes."""
        return Box(min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1), max(self.y1, other.y1))

    def overlap_area(self, other: "Box") -> float:
        """Return the area the two boxes share, in u², zero when they are apart."""
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        return max(0.0, width) * max(0.0, height)


class Polygons:
    """Closed polygons as matplotlib paths."""

    @staticmethod
    def closed_path(polygon: np.ndarray) -> Path:
        """Return a closed path through every vertex of a polygon.

        matplotlib's ``Path(vertices, closed=True)`` treats the last vertex as the closing code and drops its position,
        so a box given by its four corners would become a triangle; the first vertex is appended unless the polygon
        already ends where it starts. Example: the four corners of the unit square give a path of five vertices.
        """
        points = np.asarray(polygon, dtype=float)
        if len(points) == 0 or not np.allclose(points[0], points[-1]):
            points = np.vstack([points, points[:1]])
        return Path(points, closed=True)
