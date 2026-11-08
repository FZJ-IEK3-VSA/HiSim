"""The connectors of the Sankey figure: the bands that join a cross-link's two stubs.

A *stub* is a cross-link's short flow at each end, drawn by the library; the source stub's arrowhead is cut flat and
the target stub's notch filled, and the connector, an orthogonal polyline with rounded corners, runs between the two
flat ends. A route is tried as a straight line, an L or a Z, then searched on a grid with a cost per bend; it is
*clear* when its band keeps ``clearance`` away from every patch, connector and label of other nodes (the patches of
its own two nodes it may touch, with a slightly narrowed band, as two bands stacked on one side do).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Optional, Sequence, Tuple

import numpy as np
from matplotlib.transforms import Bbox

from hisim.postprocessing.energy_balance.figure.geometry import Box, Compass, Direction, Polygons
from hisim.postprocessing.energy_balance.figure.parameters import LayoutConstants

Point = Tuple[float, float]


@dataclass(frozen=True)
class Port:
    """A band's end: the point and the direction the band travels there."""

    point: Point
    direction: Direction


@dataclass(frozen=True)
class Obstacle:
    """A closed polygon the router must avoid, its owner key and the nodes it belongs to."""

    key: str
    polygon: np.ndarray
    nodes: Tuple[str, ...]

    @staticmethod
    def of_box(key: str, box: Box, nodes: Tuple[str, ...] = ()) -> "Obstacle":
        """Return a box as an obstacle polygon."""
        return Obstacle(key, np.array([(box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)]), nodes)


@dataclass(frozen=True)
class RouteRequest:
    """One link to route: its id, both ports, its width, the two nodes it joins and how far its start reaches back.

    ``reach`` is how far the start port lies behind the source stub's arrow tip, in u (the arrowhead is cut flat only
    once the route is found, so the check must not count the head against the band).
    """

    link_id: int
    start: Port
    end: Port
    width: float
    nodes: Tuple[str, str]
    reach: float = 0.0


@dataclass(frozen=True)
class Route:
    """The route of one link: its corner points, band width, shape class and corner radii, in u.

    ``shape`` is "straight", "L", "Z", "routed" (grid search), "pinned" (turns from a pin file) or "failed" (no clear
    route; the stubs then keep their arrowhead and notch).
    """

    link_id: int
    points: Tuple[Point, ...]
    width: float
    shape: str
    radii: Tuple[float, ...]


class Router:
    """Routes a link between two ports around obstacles."""

    #: Where a Z route turns, as fractions of the distance along its direction, tried in this order.
    Z_SPLITS: ClassVar[Tuple[float, ...]] = (0.5, 0.3, 0.7, 0.15, 0.85)
    #: The grid search gives up after this many expanded states.
    MAX_EXPANSIONS: ClassVar[int] = 400_000
    #: The length trimmed from each end of a route before it is checked, in u: the band reaches into its stubs.
    END_TRIM: ClassVar[float] = 0.3
    #: The share of its width a band keeps when it is checked against its own two nodes' patches.
    OWN_WIDTH_SHARE: ClassVar[float] = 0.8

    @staticmethod
    def shapes(start: Port, end: Port, lead: float) -> List[Tuple[str, List[Point]]]:
        """Return the simple shapes that join two ports, in the order they are tried: straight, L, then Z.

        Straight: the ports face each other on one line. L: they are perpendicular and the lines through them meet at
        least ``lead`` in front of both. Z: they travel the same way, offset sideways, with room for two turns.
        Example: from (0, 0) travelling east to (10, 2) travelling east gives the Z [(0, 0), (5, 0), (5, 2), (10, 2)].
        """
        (x0, y0), (x1, y1) = start.point, end.point
        d0, d1 = start.direction, end.direction
        result: List[Tuple[str, List[Point]]] = []
        horizontal = d0.is_horizontal
        along = (x1 - x0) * d0.vector[0] + (y1 - y0) * d0.vector[1]
        if d0 is d1:
            sideways = (y1 - y0) if horizontal else (x1 - x0)
            if abs(sideways) < 1e-6 and along > 0:
                result.append(("straight", [start.point, end.point]))
            elif along >= 2 * lead:
                for split in Router.Z_SPLITS:
                    turn = along * split
                    if turn < lead or along - turn < lead:
                        continue
                    if horizontal:
                        xm = x0 + d0.vector[0] * turn
                        result.append(("Z", [start.point, (xm, y0), (xm, y1), end.point]))
                    else:
                        ym = y0 + d0.vector[1] * turn
                        result.append(("Z", [start.point, (x0, ym), (x1, ym), end.point]))
        elif d0.is_horizontal != d1.is_horizontal:
            corner = (x1, y0) if horizontal else (x0, y1)
            before = (corner[0] - x0) * d0.vector[0] + (corner[1] - y0) * d0.vector[1]
            after = (x1 - corner[0]) * d1.vector[0] + (y1 - corner[1]) * d1.vector[1]
            if before >= lead and after >= lead:
                result.append(("L", [start.point, corner, end.point]))
        return result

    @staticmethod
    def segment_boxes(points: Sequence[Point], width: float, trim: float = 0.0, start_trim: Optional[float] = None) -> List[Box]:
        """Return the boxes of a route's straight segments as a band of ``width``, its ends trimmed by ``trim``.

        ``start_trim`` replaces ``trim`` at the start when given.

        A segment's box reaches ``width / 2`` beyond each inner corner, so the corner square is covered. Example: the L
        [(0, 0), (4, 0), (4, 4)] of width 1 gives (0, -0.5)-(4.5, 0.5) and (3.5, -0.5)-(4.5, 4).
        """
        boxes = []
        half = width / 2
        last = len(points) - 2
        for k, ((x_a, y_a), (x_b, y_b)) in enumerate(zip(points, points[1:])):
            start_cut = (trim if start_trim is None else start_trim) if k == 0 else -half
            end_cut = trim if k == last else -half
            length = math.hypot(x_b - x_a, y_b - y_a)
            if length <= start_cut + end_cut + 1e-9:
                continue
            ux, uy = (x_b - x_a) / length, (y_b - y_a) / length
            ax_, ay_ = x_a + ux * start_cut, y_a + uy * start_cut
            bx_, by_ = x_b - ux * end_cut, y_b - uy * end_cut
            if abs(uy) < 1e-9:
                boxes.append(Box.around(ax_, ay_ - half, bx_, by_ + half))
            else:
                boxes.append(Box.around(ax_ - half, ay_, bx_ + half, by_))
        return boxes

    @staticmethod
    def may_touch(obstacle: Obstacle, request: RouteRequest) -> bool:
        """Return whether a route may run right beside an obstacle: a patch or band of one of its own two nodes."""
        return bool(set(obstacle.nodes) & set(request.nodes))

    @staticmethod
    def is_clear(points: Sequence[Point], request: RouteRequest, obstacles: Sequence[Obstacle], clearance: float) -> bool:
        """Return whether a route's band, ends trimmed, keeps ``clearance`` from every obstacle it may not touch.

        Against its own nodes' patches the band is checked narrowed to ``OWN_WIDTH_SHARE`` of its width without the
        clearance, so a band stacked beside its own tree connection passes.
        """
        start_trim = Router.END_TRIM + request.reach
        own_boxes = Router.segment_boxes(points, request.width * Router.OWN_WIDTH_SHARE, Router.END_TRIM, start_trim)
        other_boxes = Router.segment_boxes(points, request.width + 2 * clearance, Router.END_TRIM, start_trim)
        for obstacle in obstacles:
            path = Polygons.closed_path(obstacle.polygon)
            boxes = own_boxes if Router.may_touch(obstacle, request) else other_boxes
            for box in boxes:
                if path.intersects_bbox(Bbox.from_extents(box.x0, box.y0, box.x1, box.y1), filled=True):
                    return False
        return True

    @staticmethod
    def corner_radii(points: Sequence[Point], radius: float, min_radius: float) -> Optional[Tuple[float, ...]]:
        """Return the radius of every inner corner: ``radius``, reduced to the room the adjacent segments leave.

        A corner may use a whole end segment but only half of a segment it shares with another corner. Example: a Z
        with a 1 u middle segment allows 0.5 u at each corner. Returns None when a corner has less room than
        ``min_radius``.
        """
        radii = []
        count = len(points)
        for k in range(1, count - 1):
            before = Router.distance(points[k - 1], points[k]) / (1 if k == 1 else 2)
            after = Router.distance(points[k], points[k + 1]) / (1 if k == count - 2 else 2)
            room = min(before, after, radius)
            if room < min_radius - 1e-9:
                return None
            radii.append(room)
        return tuple(radii)

    @staticmethod
    def is_orthogonal(points: Sequence[Point]) -> bool:
        """Return whether a polyline has at least two points and every segment is horizontal or vertical."""
        for (x_a, y_a), (x_b, y_b) in zip(points, points[1:]):
            if abs(x_a - x_b) > 1e-9 and abs(y_a - y_b) > 1e-9:
                return False
        return len(points) >= 2

    @staticmethod
    def distance(a: Point, b: Point) -> float:
        """Return the length of an orthogonal segment, in u."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def simplify(points: Sequence[Point]) -> List[Point]:
        """Return a polyline without repeated points and without points in the middle of a straight run."""
        cleaned: List[Point] = []
        for point in points:
            if cleaned and Router.distance(cleaned[-1], point) < 1e-9:
                continue
            if len(cleaned) >= 2:
                (xa, ya), (xb, yb) = cleaned[-2], cleaned[-1]
                if (abs(xa - xb) < 1e-9 and abs(xb - point[0]) < 1e-9) or (abs(ya - yb) < 1e-9 and abs(yb - point[1]) < 1e-9):
                    cleaned[-1] = point
                    continue
            cleaned.append(point)
        return cleaned

    @staticmethod
    def route(request: RouteRequest, obstacles: Sequence[Obstacle], bounds: Box, constants: LayoutConstants) -> Route:
        """Route one link: the first clear simple shape, else a grid search, else a failed route.

        The corner radius is the style's, at least half the band's width plus a little so the inner edge of a corner
        does not fold over, reduced to the room the segments leave.
        """
        lead = max(constants.lead, request.reach + request.width * 0.4 + 2 * constants.grid)
        radius = max(constants.connector_radius, request.width / 2 + 0.1)
        for shape, points in Router.shapes(request.start, request.end, lead):
            radii = Router.corner_radii(points, radius, constants.min_radius)
            if radii is not None and Router.is_clear(points, request, obstacles, constants.clearance):
                return Route(request.link_id, tuple(points), request.width, shape, radii)
        found = GridSearch.search(request, obstacles, bounds, lead, constants)
        if found is not None:
            found = Router.remove_jogs(found, request, obstacles, (2 * constants.min_radius, constants.clearance * 0.5))
        # A narrow band may turn tighter than the style's minimum: its inner edge folds only below half its width,
        # and a grid route through a narrow corridor can keep a one-cell jog.
        min_radius = min(constants.min_radius, max(request.width / 2 + 0.05, constants.grid / 2))
        if found is not None and Router.is_orthogonal(found):
            radii = Router.corner_radii(found, radius, min_radius)
            if radii is not None and Router.is_clear(found, request, obstacles, constants.clearance * 0.5):
                return Route(request.link_id, tuple(found), request.width, "routed", radii)
        return Route(request.link_id, (request.start.point, request.end.point), request.width, "failed", ())

    @staticmethod
    def remove_jogs(points: Sequence[Point], request: RouteRequest, obstacles: Sequence[Obstacle], limits: Tuple[float, float]) -> List[Point]:
        """Return a route without inner segments shorter than ``limits[0]`` where the band stays clear without them.

        A grid route can step one cell aside, a jog too short for two rounded corners. The run before the jog is moved
        onto the line of the run after it (or the other way round), which removes the jog and keeps every segment
        orthogonal; the change is kept only when the band keeps ``limits[1]`` from the obstacles. The end runs touch
        the ports and are never moved. Example: [(0, 0), (0, 2), (5, 2), (5, 2.25), (9, 2.25), (9, 6)] becomes
        [(0, 0), (0, 2.25), (9, 2.25), (9, 6)] when that is clear.
        """
        min_length, clearance = limits
        result = list(points)
        changed = True
        while changed:
            changed = False
            for k in range(2, len(result) - 3):
                if Router.distance(result[k], result[k + 1]) >= min_length:
                    continue
                horizontal_jog = abs(result[k][1] - result[k + 1][1]) < 1e-9
                for candidate in (Router.shift_run(result, k, horizontal_jog, before=True), Router.shift_run(result, k, horizontal_jog, before=False)):
                    simplified = Router.simplify(candidate)
                    if Router.is_orthogonal(simplified) and Router.is_clear(simplified, request, obstacles, clearance):
                        result, changed = simplified, True
                        break
                if changed:
                    break
        return result

    @staticmethod
    def shift_run(points: Sequence[Point], k: int, horizontal_jog: bool, before: bool) -> List[Point]:
        """Return the route with the run before the jog k -> k + 1 moved onto the run after it (or the run after onto the one before)."""
        result = list(points)
        if before:
            source, targets = result[k + 1], (k - 1, k)
        else:
            source, targets = result[k], (k + 1, k + 2)
        for index in targets:
            x, y = result[index]
            result[index] = (source[0], y) if horizontal_jog else (x, source[1])
        return result

    @staticmethod
    def route_points(start: Port, end: Port, turns: Sequence[object]) -> List[Point]:
        """Return the corner points of a pinned route from its turns (the prototype's ``route_points``).

        The route leaves ``start`` along its direction. Each turn is where the current segment ends, on that
        segment's axis: a number is absolute, a string "+1.5"/"-1.5" relative to the segment's start. After the turns,
        one more segment lines the route up with the end if needed. Example: from (0, 0) east to (5, 3) north with
        turns [] gives [(0, 0), (5, 0), (5, 3)].

        Raises:
            ValueError: If a segment is not orthogonal or the route does not leave or arrive along its ports.
        """
        points = [start.point]
        axis = 0 if start.direction.is_horizontal else 1
        for value in turns:
            point = list(points[-1])
            point[axis] = point[axis] + float(str(value)) if isinstance(value, str) else float(str(value))
            points.append((point[0], point[1]))
            axis = 1 - axis
        end_axis = 0 if end.direction.is_horizontal else 1
        if axis != end_axis:
            point = list(points[-1])
            point[axis] = end.point[axis]
            points.append((point[0], point[1]))
        points.append(end.point)
        cleaned = Router.simplify(points)
        if not Router.is_orthogonal(cleaned):
            raise ValueError(f"the turns {list(turns)} give a segment that is not orthogonal")
        first = (cleaned[1][0] - cleaned[0][0]) * start.direction.vector[0] + (cleaned[1][1] - cleaned[0][1]) * start.direction.vector[1]
        last = (cleaned[-1][0] - cleaned[-2][0]) * end.direction.vector[0] + (cleaned[-1][1] - cleaned[-2][1]) * end.direction.vector[1]
        if first <= 0 or last <= 0:
            raise ValueError(f"the turns {list(turns)} do not leave the source along its stub or reach the target along its stub")
        return cleaned


class GridSearch:
    """A* over a square grid with orthogonal moves and a cost per bend, aligned with the route's start."""

    @staticmethod
    def raster(polygon: np.ndarray, origin: Point, size: Tuple[int, int], step: float) -> np.ndarray:
        """Return the grid cells whose centre lies inside a polygon, as a boolean array indexed [i, j] (x, y)."""
        mask = np.zeros(size, dtype=bool)
        x0, y0 = polygon[:, 0].min(), polygon[:, 1].min()
        x1, y1 = polygon[:, 0].max(), polygon[:, 1].max()
        i0, i1 = max(0, int((x0 - origin[0]) / step)), min(size[0] - 1, int((x1 - origin[0]) / step) + 1)
        j0, j1 = max(0, int((y0 - origin[1]) / step)), min(size[1] - 1, int((y1 - origin[1]) / step) + 1)
        if i1 < i0 or j1 < j0:
            return mask
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="ij")
        centres = np.column_stack([origin[0] + ii.ravel() * step, origin[1] + jj.ravel() * step])
        inside = Polygons.closed_path(polygon).contains_points(centres)
        mask[i0 : i1 + 1, j0 : j1 + 1] = inside.reshape(ii.shape)
        return mask

    @staticmethod
    def dilate(mask: np.ndarray, cells: int) -> np.ndarray:
        """Return a boolean grid grown by ``cells`` cells in every direction (a square neighbourhood)."""
        result = mask.copy()
        for _ in range(max(0, cells)):
            grown = result.copy()
            grown[1:, :] |= result[:-1, :]
            grown[:-1, :] |= result[1:, :]
            grown[:, 1:] |= result[:, :-1]
            grown[:, :-1] |= result[:, 1:]
            result = grown
        return result

    @staticmethod
    def search(request: RouteRequest, obstacles: Sequence[Obstacle], bounds: Box, lead: float, constants: LayoutConstants) -> Optional[List[Point]]:
        """Return the corner points of the cheapest grid route between the two ports, or None.

        The route leaves the start port along its direction for ``lead`` u, crosses the grid (cost: length plus
        ``bend_cost`` per turn; a cell is blocked when the band centred on it would come closer than ``clearance`` to
        an obstacle, or overlap a patch of its own nodes) and arrives ``lead`` u in front of the end port, travelling
        into it. The last run is then shifted onto the end port's line, which keeps every segment orthogonal.
        """
        step = constants.grid
        d0, d1 = request.start.direction, request.end.direction
        start = (request.start.point[0] + d0.vector[0] * lead, request.start.point[1] + d0.vector[1] * lead)
        goal = (request.end.point[0] - d1.vector[0] * lead, request.end.point[1] - d1.vector[1] * lead)
        size = (int((bounds.x1 - bounds.x0) / step) + 2, int((bounds.y1 - bounds.y0) / step) + 2)
        origin = (start[0] - step * round((start[0] - bounds.x0) / step), start[1] - step * round((start[1] - bounds.y0) / step))
        own = np.zeros(size, dtype=bool)
        other = np.zeros(size, dtype=bool)
        for obstacle in obstacles:
            mask = GridSearch.raster(obstacle.polygon, origin, size, step)
            if Router.may_touch(obstacle, request):
                own |= mask
            else:
                other |= mask
        blocked = GridSearch.dilate(other, math.ceil((request.width / 2 + constants.clearance) / step))
        blocked |= GridSearch.dilate(own, math.ceil(request.width * 0.4 / step))

        def cell(point: Point) -> Tuple[int, int]:
            return int(round((point[0] - origin[0]) / step)), int(round((point[1] - origin[1]) / step))

        start_cell, goal_cell = cell(start), cell(goal)
        if not all(0 <= c[0] < size[0] and 0 <= c[1] < size[1] for c in (start_cell, goal_cell)):
            return None
        blocked[start_cell] = False
        blocked[goal_cell] = False
        path = GridSearch.astar(blocked, start_cell, goal_cell, (Compass.ORDER.index(d0), Compass.ORDER.index(d1)), constants.bend_cost / step)
        if path is None:
            return None
        corners = [(origin[0] + i * step, origin[1] + j * step) for i, j in Router.simplify([(float(i), float(j)) for i, j in path])]
        return GridSearch.attach_ends(request, corners)

    @staticmethod
    def astar(blocked: np.ndarray, start: Tuple[int, int], goal: Tuple[int, int], headings: Tuple[int, int], bend_steps: float) -> Optional[List[Tuple[int, int]]]:
        """Return the cheapest cell path from start to goal, or None.

        A state is a cell and a heading (an index into the counter-clockwise compass order); a move goes one cell on,
        straight (cost 1) or after a quarter turn (cost 1 plus ``bend_steps``); arriving with a heading other than the
        last of ``headings`` costs one more bend. The heuristic is the Manhattan distance, so the path is optimal.
        """
        first_heading, last_heading = headings
        vectors = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        best: Dict[Tuple[int, int, int], float] = {(start[0], start[1], first_heading): 0.0}
        came: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
        frontier = [(float(abs(goal[0] - start[0]) + abs(goal[1] - start[1])), 0.0, (start[0], start[1], first_heading))]
        expansions = 0
        size = blocked.shape
        while frontier and expansions < Router.MAX_EXPANSIONS:
            _, cost, state = heapq.heappop(frontier)
            if cost > best.get(state, float("inf")) + 1e-12:
                continue
            expansions += 1
            at_goal = (state[0], state[1]) == goal
            if at_goal and state[2] == last_heading:
                path = [state]
                while path[-1] in came:
                    path.append(came[path[-1]])
                return [(i, j) for i, j, _ in reversed(path)]
            candidates = [((state[0], state[1], last_heading), cost + bend_steps * (2 if (state[2] - last_heading) % 4 == 2 else 1))] if at_goal else []
            for turn, extra in ((0, 0.0), (1, bend_steps), (3, bend_steps)):
                heading = (state[2] + turn) % 4
                i, j = state[0] + vectors[heading][0], state[1] + vectors[heading][1]
                if 0 <= i < size[0] and 0 <= j < size[1] and (not blocked[i, j] or (i, j) == goal):
                    candidates.append(((i, j, heading), cost + 1.0 + extra))
            for nxt, new_cost in candidates:
                if new_cost < best.get(nxt, float("inf")) - 1e-12:
                    best[nxt] = new_cost
                    came[nxt] = state
                    heapq.heappush(frontier, (new_cost + abs(goal[0] - nxt[0]) + abs(goal[1] - nxt[1]), new_cost, nxt))
        return None

    @staticmethod
    def attach_ends(request: RouteRequest, corners: List[Point]) -> List[Point]:
        """Join a grid path to the exact ports: prepend the start port, move the last run onto the end port's line.

        The grid is aligned with the start, so only the end can be off its port's line by up to half a cell; the last
        run (or the end of the last run) is moved onto the line, which keeps every segment orthogonal.
        """
        end, d1 = request.end.point, request.end.direction
        points = [request.start.point, *corners]
        if len(points) >= 3:
            (xa, ya), (xb, yb) = points[-2], points[-1]
            last_horizontal = abs(xa - xb) > 1e-9 >= abs(ya - yb)
            if last_horizontal == d1.is_horizontal:
                if last_horizontal:
                    points[-2], points[-1] = (xa, end[1]), (xb, end[1])
                else:
                    points[-2], points[-1] = (end[0], ya), (end[0], yb)
            elif d1.is_horizontal:
                points[-1] = (xb, end[1])
            else:
                points[-1] = (end[0], yb)
        points.append(end)
        return Router.simplify(points)


class Bands:
    """The outline of a connector band: its centre line with rounded corners, offset by half its width."""

    #: Points sampled along a rounded corner.
    CORNER_SAMPLES: ClassVar[int] = 16

    @staticmethod
    def rounded_centre_line(points: Sequence[Point], radii: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
        """Return a dense centre line of an orthogonal route with rounded corners and its unit tangents.

        Each inner corner k is replaced by a quarter circle of radius ``radii[k - 1]``. Example: [(0, 0), (2, 0), (2, 2)]
        with radius 1 runs straight to (1, 0), turns through a quarter circle around (1, 1) and ends at (2, 2).
        """
        array = [np.array(p, dtype=float) for p in points]

        def unit(vector: np.ndarray) -> np.ndarray:
            return vector / float(np.hypot(*vector))

        line, tangents = [array[0]], [unit(array[1] - array[0])]
        for k in range(1, len(array) - 1):
            before, after = unit(array[k] - array[k - 1]), unit(array[k + 1] - array[k])
            radius = radii[k - 1] if k - 1 < len(radii) else 0.0
            centre = array[k] - before * radius + after * radius
            for theta in np.linspace(0.0, math.pi / 2, Bands.CORNER_SAMPLES):
                line.append(centre + radius * (math.cos(theta) * (-after) + math.sin(theta) * before))
                tangents.append(math.sin(theta) * after + math.cos(theta) * before)
        line.append(array[-1])
        tangents.append(unit(array[-1] - array[-2]))
        return np.array(line), np.array(tangents)

    @staticmethod
    def outline(line: np.ndarray, tangents: np.ndarray, width: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return the closed outline polygon of a band of ``width`` along a centre line, and its two sides."""
        normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
        left, right = line + normals * width / 2, line - normals * width / 2
        return np.vstack([left, right[::-1]]), left, right
