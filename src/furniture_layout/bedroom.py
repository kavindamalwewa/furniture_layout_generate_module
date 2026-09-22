"""Room-type-specific layout generation for bedrooms.

The generic sampler scatters pieces anywhere they fit, which is how a bed ends
up floating in the middle of the room with everything else packed around it.
This module *constructs* a modern bedroom instead, in a fixed order:

    1. Put the bed's headboard flush against a wall, as close to a window as the
       room allows: under it, beside it, or tucked into the corner it shares
       with the window wall.  The bed never enters a door approach.
    2. Flank the head of the bed with the nightstands, on the same wall.
    3. Stand the wardrobe (and other tall storage) flush against a wall, off the
       windows, with a clear strip in front to open the doors.
    4. Put the study table flush against a *different* wall from the bed, as
       close to daylight as possible, with room for the chair in front.
    5. Every piece hugs a wall, so the middle of the room stays open.

Each bed position is expanded into a few storage/desk arrangements; every
arrangement is scored on a coarse floor grid (open floor, walkways, bed access)
and the best distinct ones are returned with the same contract as the other
engines.  As in the living room, preferences that cannot be met become score
penalties instead of blocking a result.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

from .living_room import _layout_signature, _place_against_wall, _place_centered, _polygon_walls, _seed_rotate
from .polygon_engine import (
    EPS,
    _point_segment_distance,
    _ring_distance,
    footprint_inside,
    point_in_polygon,
    point_in_ring,
    rectangle_ring,
    rings_overlap,
    segments_intersect,
    validate_layout,
)
from .project import ProjectValidationError, polygon_bbox, validate_ring


# Real-world clearances, in metres.
WALL_GAP = 0.05           # back-to-wall gap for every piece
SIDE_GAP = 0.05           # gap to the side wall when a piece is tucked into a corner
PIECE_GAP = 0.03          # gap between neighbours (touching footprints count as overlapping)
BED_ACCESS = 0.6          # clear strip wanted beside and at the foot of the bed
CHAIR_PULL_OUT = 0.45     # clear floor behind a desk chair to pull it out
SPACING_COMFORT = 0.3     # standing room beyond a piece's own clearance for full spacing marks
DOUBLE_BED = 1.2          # beds at least this wide should be reachable from both sides
BY_WINDOW = 0.3           # a bed this close to a window is under or beside it
NEAR_WINDOW = 1.0         # a bed further than this from every window is penalised
PATH_HALF_WIDTH = 0.3     # walkways must be roughly 0.6 m wide
DEAD_FLOOR = 1.0          # m² of cut-off floor that counts as a wasted pocket rather than slivers
FRONT_CLEARANCE = {"wardrobe": 0.6, "dresser": 0.6, "desk": 0.6, "other": 0.45}

# Search limits.
MAX_BEDS = 16                        # bed positions expanded into full layouts
BRANCH = {"wardrobe": 3, "desk": 3}  # slots tried for the first piece of each role
DIVERSITY_FLOOR = 15.0               # alternatives should trail the best layout by at most this many points
GRID_CELL = 0.1                      # floor-grid resolution in metres, coarsened for large rooms
MAX_CELLS = 1600

# The bedroom score, out of 100 points, as shown on the layout cards.  It is
# measured here by bedroom-specific code; the living room keeps its own scorer
# (``polygon_engine._layout_score_components``), which this module never calls.
COMPONENT_MAX = {
    "door_access": 20.0,          # door approach kept clear, with a walkway from it to every piece
    "open_space": 25.0,           # free floor in the middle, in one large clear area, none of it cut off
    "spacing": 25.0,              # room to use each piece: beside the bed, in front of storage, behind the chair
    "table_near_wall": 15.0,      # study (and dressing) table flush against a wall, not the bed's wall
    "nightstand_near_bed": 15.0,  # nightstands right beside the head of the bed
}

# How the generator ranks candidate arrangements.  This is part of layout
# generation, not of the score above: it also weighs the bedroom planning rules
# that the card does not show, such as how close the bed is to a window.
_RANK_WEIGHTS = {"bed_window": 20.0, "open_center": 20.0, "study_table": 15.0,
                 "bed_access": 15.0, "walkways": 15.0, "door_clearance": 15.0}

# Score deductions, in points, when a bedroom preference cannot be honoured.
_PENALTY = {
    "bed_far_from_window": 15.0,  # plus 3 per metre beyond NEAR_WINDOW, at most 25
    "desk_on_bed_wall": 8.0,
    "storage_covers_window": 10.0,
    "missing": 6.0,          # per required piece that found no clear spot
    "blocked_access": 10.0,  # per bed / storage / desk no walkway reaches
    "dead_floor": 12.0,      # at most; 6 points per m² once DEAD_FLOOR is cut off
}
# Broken bedroom rules (rather than weaker scores): alternatives that keep them rank first.
_RULES = ("desk_on_bed_wall", "storage_covers_window", "blocked_access")

_ROLE_ORDER = {"bed": 0, "nightstand": 1, "wardrobe": 2, "desk": 3, "dresser": 4, "other": 5, "chair": 6}


# --------------------------------------------------------------------------- #
# Request analysis
# --------------------------------------------------------------------------- #
def is_bedroom_type(room_type: str) -> bool:
    return "bedroom" in room_type.lower().replace("-", "").replace("_", "").replace(" ", "")


def has_bed(furniture: list[dict[str, Any]]) -> bool:
    return any(_role(str(spec.get("id", ""))) == "bed" for spec in furniture)


def _role(item_id: str) -> str:
    key = item_id.lower().replace("_", "-").replace(" ", "-")
    if any(word in key for word in ("nightstand", "night-stand", "bedside", "side-table")):
        return "nightstand"
    if "bed" in key:
        return "bed"
    if any(word in key for word in ("wardrobe", "closet", "armoire", "cupboard", "almirah")):
        return "wardrobe"
    if any(word in key for word in ("dresser", "dressing", "vanity", "chest", "drawer")):
        return "dresser"
    if any(word in key for word in ("chair", "stool", "seat")):
        return "chair"
    if any(word in key for word in ("desk", "study", "table", "workstation")):
        return "desk"
    return "other"


def _pieces(furniture: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per physical piece, in placement order (bed first, chairs last).

    Specs sharing an id are merged, because the legacy import lists a repeated
    piece once per copy.  Wall pieces put their long side along the wall; the
    bed does the opposite, with its headboard (the short side) on the wall.
    """
    merged: dict[str, dict[str, Any]] = {}
    for spec in furniture:
        item_id = str(spec.get("id", "")).strip()
        quantity = int(spec.get("quantity", 1))
        if not item_id or quantity < 1:
            continue
        if item_id in merged:
            merged[item_id]["quantity"] += quantity
        else:
            merged[item_id] = {**spec, "id": item_id, "quantity": quantity}
    pieces: list[dict[str, Any]] = []
    for spec in merged.values():
        role = _role(spec["id"])
        a, b = float(spec["width"]), float(spec["depth"])
        along, into = (min(a, b), max(a, b)) if role == "bed" else (max(a, b), min(a, b))
        tall = (role == "wardrobe" or any(word in spec["id"].lower() for word in ("book", "shelf", "cabinet"))
                or float(spec.get("height") or 0) >= 1.2)
        for instance in range(1, spec["quantity"] + 1):
            pieces.append({"id": f"{spec['id']}-{instance}", "spec": spec, "role": role,
                           "along": along, "into": into, "tall": tall,
                           "required": bool(spec.get("required", True))})
    pieces.sort(key=lambda piece: (_ROLE_ORDER[piece["role"]], -piece["along"] * piece["into"], piece["id"]))
    branched: set[str] = set()
    for piece in pieces:
        piece["branch"] = 1 if piece["role"] in branched else BRANCH.get(piece["role"], 1)
        branched.add(piece["role"])
    return pieces


# --------------------------------------------------------------------------- #
# Geometry helpers (pixel units unless a name says metres)
# --------------------------------------------------------------------------- #
def _project(wall: dict[str, Any], point) -> tuple[float, float]:
    """``(offset along the wall from its start, distance into the room)``."""
    dx, dy = point[0] - wall["a"][0], point[1] - wall["a"][1]
    return (dx * wall["direction"][0] + dy * wall["direction"][1],
            dx * wall["normal"][0] + dy * wall["normal"][1])


def _at(wall: dict[str, Any], offset: float) -> list[float]:
    return [wall["a"][0] + wall["direction"][0] * offset, wall["a"][1] + wall["direction"][1] * offset]


def _span_on_wall(span, walls: list[dict[str, Any]]) -> tuple[dict[str, Any], float, float]:
    """The wall an opening sits in and the ``(start, end)`` offsets it covers along it."""
    mid = ((span[0][0] + span[1][0]) / 2, (span[0][1] + span[1][1]) / 2)
    wall = min(walls, key=lambda item: _point_segment_distance(mid, item["a"], item["b"]))
    s0, s1 = sorted(min(max(_project(wall, point)[0], 0.0), wall["length"]) for point in span)
    return wall, s0, s1


def _bbox(ring) -> tuple[float, float, float, float]:
    xs = [point[0] for point in ring]
    ys = [point[1] for point in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _hit(a, b) -> bool:
    """Footprint overlap (touching counts), with a cheap bounding-box reject first."""
    ax0, ay0, ax1, ay1 = _bbox(a)
    bx0, by0, bx1, by1 = _bbox(b)
    if ax1 < bx0 - EPS or bx1 < ax0 - EPS or ay1 < by0 - EPS or by1 < ay0 - EPS:
        return False
    return rings_overlap(a, b)


def _centre(ring) -> tuple[float, float]:
    return sum(point[0] for point in ring) / len(ring), sum(point[1] for point in ring) / len(ring)


def _ring(geom: dict[str, Any]) -> list[list[float]]:
    return rectangle_ring(geom["x"], geom["y"], geom["width"], geom["depth"], geom["rotation"])


def _local_point(geom: dict[str, Any], along: float, into: float) -> tuple[float, float]:
    """A point in a placement's own frame: ``along`` its back edge, ``into`` towards its front."""
    radians = math.radians(geom["rotation"])
    cos, sin = math.cos(radians), math.sin(radians)
    return geom["x"] + cos * along - sin * into, geom["y"] + sin * along + cos * into


def _local_ring(geom: dict[str, Any], a0: float, a1: float, b0: float, b1: float) -> list[list[float]]:
    return [list(_local_point(geom, a, b)) for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]


def _segment_gap(ring, a, b) -> float:
    """Distance from a footprint to a segment (a window); zero when they touch."""
    edges = list(zip(ring, ring[1:] + ring[:1]))
    if point_in_ring(tuple(a), ring) or any(segments_intersect(p, q, a, b) for p, q in edges):
        return 0.0
    return min([_point_segment_distance(tuple(point), a, b) for point in ring]
               + [_point_segment_distance(tuple(end), p, q) for end in (a, b) for p, q in edges])


def _ramp(value: float, full: float, zero: float) -> float:
    """1 up to ``full``, falling linearly to 0 at ``zero``."""
    return max(0.0, min(1.0, (zero - value) / (zero - full)))


def _window_quality(nearest: tuple[float, int] | None) -> float:
    """1 for a bed under or beside a window (or a windowless room), 0 once it is well away."""
    return 1.0 if nearest is None else _ramp(nearest[0], BY_WINDOW, NEAR_WINDOW + 0.2)


def _window_tier(nearest: tuple[float, int] | None) -> int:
    """0: bed by a window (or no windows), 1: near one, 2: away from every window."""
    if nearest is None or nearest[0] <= BY_WINDOW:
        return 0
    return 1 if nearest[0] <= NEAR_WINDOW else 2


# --------------------------------------------------------------------------- #
# Room model
# --------------------------------------------------------------------------- #
class _Room:
    """Everything about the room that stays fixed while candidate layouts are built."""

    def __init__(self, request: dict[str, Any], units_per_meter: float) -> None:
        self.polygon = request["polygon"]
        self.upm = units_per_meter
        self.box = polygon_bbox(self.polygon)
        self.walls = _polygon_walls(self.polygon)
        self.wall = {wall["index"]: wall for wall in self.walls}
        self.windows: dict[int, list[tuple[float, float]]] = {wall["index"]: [] for wall in self.walls}
        self.window_segments: list[tuple[int, list[float], list[float]]] = []
        self.door_zones: list[list[list[float]]] = []
        self.door_points: list[tuple[float, float]] = []
        for opening in request.get("openings", []):
            span = opening.get("span")
            if opening.get("kind") == "window" and span:
                wall, s0, s1 = _span_on_wall(span, self.walls)
                if s1 - s0 > EPS:
                    self.windows[wall["index"]].append((s0, s1))
                    self.window_segments.append((wall["index"], _at(wall, s0), _at(wall, s1)))
            elif opening.get("kind") == "door":
                zone = self._door_zone(opening)
                if zone:
                    self.door_zones.append(zone)
                    self.door_points.append(tuple(opening.get("portalPoint") or _centre(zone)))
        self.obstacles = [obstacle.get("polygon", obstacle) if isinstance(obstacle, dict) else obstacle
                          for obstacle in request.get("fixedObstacles", [])]
        self.centre = (self.box["x"] + self.box["width"] / 2, self.box["y"] + self.box["height"] / 2)
        self.half_diagonal = max(math.hypot(self.box["width"], self.box["height"]) / 2, EPS)
        self.grid = _Grid(self)

    def _door_zone(self, opening: dict[str, Any]) -> list[list[float]] | None:
        """The door's keep-clear area; built on the room side of its wall when not supplied."""
        if opening.get("keepClearPolygon"):
            return [[float(point[0]), float(point[1])] for point in opening["keepClearPolygon"]]
        span = opening.get("span")
        if not span:
            return None
        wall, s0, s1 = _span_on_wall(span, self.walls)
        if s1 - s0 <= EPS:
            return None
        margin = 0.1 * self.upm
        depth = float(opening.get("clearance", 0.9)) * self.upm
        near, far = _at(wall, max(0.0, s0 - margin)), _at(wall, min(wall["length"], s1 + margin))
        nx, ny = wall["normal"][0] * depth, wall["normal"][1] * depth
        return [near, far, [far[0] + nx, far[1] + ny], [near[0] + nx, near[1] + ny]]

    def nearest_window(self, ring) -> tuple[float, int] | None:
        """``(metres to the nearest window, index of its wall)``, or ``None`` without windows."""
        if not self.window_segments:
            return None
        gap, index = min((_segment_gap(ring, a, b), index) for index, a, b in self.window_segments)
        return gap / self.upm, index

    def daylight(self, ring) -> float:
        """1 beside a window, fading to 0 two metres away (and 0 in a windowless room)."""
        nearest = self.nearest_window(ring)
        return 0.0 if nearest is None else _ramp(nearest[0], 0.3, 2.3)

    def fits(self, state: _State, ring, skip_zone_of: str | None = None) -> bool:
        """Inside the room and clear of furniture, doors, obstacles, and other pieces' front clearances."""
        return (not any(_hit(ring, item["ring"]) for item in state.items)
                and not any(_hit(ring, item["zone"]) for item in state.items
                            if item["zone"] and item["id"] != skip_zone_of)
                and not any(_hit(ring, zone) for zone in self.door_zones)
                and not any(_hit(ring, obstacle) for obstacle in self.obstacles)
                and footprint_inside(ring, self.polygon))

    def zone_free(self, state: _State, zone) -> bool:
        """A front clearance must lie inside the room with no furniture standing in it."""
        return (not any(_hit(zone, item["ring"]) for item in state.items)
                and not any(_hit(zone, obstacle) for obstacle in self.obstacles)
                and footprint_inside(zone, self.polygon))

    def covered_windows(self, item: dict[str, Any]) -> list[tuple[float, float]]:
        """The windows on the item's wall that its along-wall extent overlaps."""
        if item["wall"] is None:
            return []
        start, end = item["start"], item["start"] + item["geom"]["width"]
        return [(s0, s1) for s0, s1 in self.windows[item["wall"]] if s0 < end - EPS and s1 > start + EPS]

    def covers_window(self, item: dict[str, Any]) -> bool:
        return bool(self.covered_windows(item))

    def headboard_alignment(self, bed: dict[str, Any]) -> float:
        """1 unless the headboard half-covers a window: a bed under a window is centred on it."""
        middle = bed["start"] + bed["geom"]["width"] / 2
        for s0, s1 in self.covered_windows(bed):
            if abs((s0 + s1) / 2 - middle) > max(0.15 * self.upm, 0.1 * (s1 - s0)):
                return 0.85
        return 1.0


class _Grid:
    """Coarse floor grid used to judge open floor and walkways."""

    def __init__(self, room: _Room) -> None:
        box, self.upm = room.box, room.upm
        area_m2 = box["width"] * box["height"] / room.upm / room.upm
        self.cell_m = max(GRID_CELL, math.sqrt(area_m2 / MAX_CELLS))
        self.cell = self.cell_m * room.upm
        self.min_clear = max(1, math.ceil(PATH_HALF_WIDTH / self.cell_m - 1e-9))
        self.x0, self.y0 = box["x"], box["y"]
        self.cols = max(1, math.ceil(box["width"] / self.cell - 1e-9))
        self.rows = max(1, math.ceil(box["height"] / self.cell - 1e-9))
        self.centres = [(self.x0 + (col + .5) * self.cell, self.y0 + (row + .5) * self.cell)
                        for row in range(self.rows) for col in range(self.cols)]
        self.inside = [point_in_polygon(point, room.polygon)
                       and not any(point_in_ring(point, obstacle) for obstacle in room.obstacles)
                       for point in self.centres]
        x0, x1 = self.x0 + box["width"] * .25, self.x0 + box["width"] * .75
        y0, y1 = self.y0 + box["height"] * .25, self.y0 + box["height"] * .75
        self.central = [index for index, (x, y) in enumerate(self.centres)
                        if self.inside[index] and x0 <= x <= x1 and y0 <= y <= y1]
        cells = [(row, col) for row in range(self.rows) for col in range(self.cols)]
        self.sides = [[r * self.cols + c for r, c in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1))
                       if 0 <= r < self.rows and 0 <= c < self.cols] for row, col in cells]
        self.neighbours = [[r * self.cols + c for r in (row - 1, row, row + 1) for c in (col - 1, col, col + 1)
                            if (r, c) != (row, col) and 0 <= r < self.rows and 0 <= c < self.cols]
                           for row, col in cells]

    def _span(self, low: float, high: float, origin: float, count: int) -> range:
        """Indices of the cells whose centres lie within ``[low, high]``."""
        first = math.ceil((low - origin) / self.cell - .5 - 1e-9)
        last = math.floor((high - origin) / self.cell - .5 + 1e-9)
        return range(max(0, first), min(count - 1, last) + 1)

    def blocked(self, rings) -> list[bool]:
        """Cells outside the room or under a footprint."""
        blocked = [not inside for inside in self.inside]
        for ring in rings:
            x0, y0, x1, y1 = _bbox(ring)
            # An axis-aligned footprint is its bounding box, so no point test is needed.
            square = all(abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6 for a, b in zip(ring, ring[1:] + ring[:1]))
            for row in self._span(y0, y1, self.y0, self.rows):
                for col in self._span(x0, x1, self.x0, self.cols):
                    index = row * self.cols + col
                    if not blocked[index] and (square or point_in_ring(self.centres[index], ring)):
                        blocked[index] = True
        return blocked

    def clearance(self, blocked: list[bool]) -> list[int]:
        """Chessboard distance, in cells, from every cell to the nearest blocked cell or grid edge."""
        cols, rows = self.cols, self.rows
        dist = [0 if cell else cols + rows for cell in blocked]
        for row in range(rows):
            for col in range(cols):
                index = row * cols + col
                if dist[index]:
                    up = index - cols
                    dist[index] = 1 + min(dist[index] - 1,
                                          dist[index - 1] if col else 0,
                                          dist[up] if row else 0,
                                          dist[up - 1] if row and col else 0,
                                          dist[up + 1] if row and col + 1 < cols else 0)
        for row in range(rows - 1, -1, -1):
            for col in range(cols - 1, -1, -1):
                index = row * cols + col
                if dist[index]:
                    down, below = index + cols, row + 1 < rows
                    dist[index] = 1 + min(dist[index] - 1,
                                          dist[index + 1] if col + 1 < cols else 0,
                                          dist[down] if below else 0,
                                          dist[down + 1] if below and col + 1 < cols else 0,
                                          dist[down - 1] if below and col else 0)
        return dist

    def nearest(self, point, mask: list[bool], radius_m: float) -> int | None:
        """The ``mask`` cell closest to ``point`` within ``radius_m``."""
        radius = radius_m * self.upm
        best, best_gap = None, radius + EPS
        for row in self._span(point[1] - radius, point[1] + radius, self.y0, self.rows):
            for col in self._span(point[0] - radius, point[0] + radius, self.x0, self.cols):
                index = row * self.cols + col
                if mask[index]:
                    gap = math.hypot(self.centres[index][0] - point[0], self.centres[index][1] - point[1])
                    if gap < best_gap:
                        best, best_gap = index, gap
        return best

    def around(self, marked: list[bool], blocked: list[bool], steps: int) -> list[bool]:
        """Free cells within ``steps`` moves of a marked cell (arm's reach of a walkway)."""
        near = list(marked)
        frontier = [index for index, value in enumerate(marked) if value]
        for _ in range(steps):
            grown = []
            for index in frontier:
                for other in self.neighbours[index]:
                    if not near[other] and not blocked[other]:
                        near[other] = True
                        grown.append(other)
            frontier = grown
        return near

    def reachable(self, walkable: list[bool], starts: list[int | None]) -> list[bool]:
        """Cells reachable from any start through walkable cells."""
        seen = [False] * len(walkable)
        queue: deque[int] = deque()
        for start in starts:
            if start is not None and not seen[start]:
                seen[start] = True
                queue.append(start)
        while queue:
            for index in self.sides[queue.popleft()]:
                if walkable[index] and not seen[index]:
                    seen[index] = True
                    queue.append(index)
        return seen


class _State:
    """A partial layout: placed items (bed first) and the pieces that found no spot."""

    __slots__ = ("items", "missing")

    def __init__(self, items: tuple = (), missing: tuple = ()) -> None:
        self.items = items
        self.missing = missing

    def plus(self, item: dict[str, Any]) -> _State:
        return _State(self.items + (item,), self.missing)

    def skip(self, piece: dict[str, Any]) -> _State:
        return _State(self.items, self.missing + (piece,))

    @property
    def bed(self) -> dict[str, Any] | None:
        return self.items[0] if self.items and self.items[0]["role"] == "bed" else None


_EMPTY = _State()


def _item(piece: dict[str, Any], geom: dict[str, Any], wall: int | None = None, start: float | None = None,
          zone: list[list[float]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": piece["id"], "piece": piece, "role": piece["role"], "geom": geom, "ring": _ring(geom),
            "wall": wall, "start": start, "zone": zone, **extra}


# --------------------------------------------------------------------------- #
# Candidate slots
# --------------------------------------------------------------------------- #
def _on_wall(wall: dict[str, Any], piece: dict[str, Any], start: float, units_per_meter: float) -> dict[str, Any]:
    """``piece`` flush against ``wall``, its along-wall extent beginning ``start`` from the wall's start."""
    shift = start + piece["along"] * units_per_meter / 2 - wall["length"] / 2
    return _place_against_wall(wall, piece["along"], piece["into"], units_per_meter, shift, WALL_GAP)


def _slot_starts(wall: dict[str, Any], along: float, gap: float, raw: list[float], tolerance: float) -> list[float]:
    """Clamp candidate start offsets onto the wall and drop near-duplicates.

    Both corners and the centre are always tried; ``raw`` adds offsets beside
    other pieces and openings, or centred on windows.
    """
    low, high = gap, wall["length"] - along - gap
    if high < low - EPS:
        return []
    high = max(high, low)
    starts: list[float] = []
    for start in (low, high, (low + high) / 2, *raw):
        start = min(max(start, low), high)
        if all(abs(start - kept) > tolerance for kept in starts):
            starts.append(start)
    return starts


def _beside(intervals: list[tuple[float, float]], along: float, gap: float) -> list[float]:
    """Start offsets that put a piece just before or just after each interval."""
    return [start for s0, s1 in intervals for start in (s0 - along - gap, s1 + gap)]


def _intervals(wall: dict[str, Any], rings, reach: float) -> list[tuple[float, float]]:
    """Along-wall extents of the rings that come within ``reach`` of the wall."""
    intervals = []
    for ring in rings:
        offsets = [_project(wall, point) for point in ring]
        along = [offset[0] for offset in offsets]
        depth = [offset[1] for offset in offsets]
        if min(depth) > reach or max(depth) < -EPS or max(along) < 0 or min(along) > wall["length"]:
            continue
        intervals.append((min(along), max(along)))
    return intervals


def _pick(options: list[tuple[float, dict[str, Any]]], width: int) -> list[tuple[float, dict[str, Any]]]:
    """The ``width`` best options, taking the best on each wall before any second choice."""
    ranked = sorted(options, key=lambda option: option[0], reverse=True)
    first, rest, walls = [], [], set()
    for option in ranked:
        (rest if option[1]["wall"] in walls else first).append(option)
        walls.add(option[1]["wall"])
    return (first + rest)[:width]


def _bed_zones(geom: dict[str, Any], head: float, reach: float) -> list[list[list[float]]]:
    """The strips beside and at the foot of the bed that should stay walkable."""
    width, depth = geom["width"], geom["depth"]
    head = min(head, depth / 2)
    return [_local_ring(geom, -reach, 0.0, head, depth),
            _local_ring(geom, width, width + reach, head, depth),
            _local_ring(geom, 0.0, width, depth, depth + reach)]


def _bed_options(room: _Room, piece: dict[str, Any],
                 headroom: tuple[float, float]) -> list[tuple[float, dict[str, Any]]]:
    """Every door-clear headboard position, scored for window proximity and open floor.

    ``headroom`` is the ``(along, into)`` size of the largest nightstand: it adds
    positions that leave exactly enough room for one between bed and corner.
    """
    upm = room.upm
    along, into = piece["along"] * upm, piece["into"] * upm
    gap, tolerance = SIDE_GAP * upm, 0.02 * upm
    tuck = (headroom[0] + PIECE_GAP) * upm if headroom[0] else 0.0
    head = (headroom[1] + PIECE_GAP) * upm if headroom[1] else 0.3 * upm
    options = []
    for wall in room.walls:
        windows = room.windows[wall["index"]]
        raw = [(s0 + s1) / 2 - along / 2 for s0, s1 in windows]
        raw += _beside(windows, along, gap) + _beside(_intervals(wall, room.door_zones, into), along, gap)
        if tuck:
            raw += [gap + tuck, wall["length"] - along - gap - tuck]
        for start in _slot_starts(wall, along, gap, raw, tolerance):
            geom = _on_wall(wall, piece, start, upm)
            ring = _ring(geom)
            if not room.fits(_EMPTY, ring):
                continue
            item = _item(piece, geom, wall=wall["index"], start=start, zones=_bed_zones(geom, head, BED_ACCESS * upm))
            nearest = room.nearest_window(ring)
            near = _window_quality(nearest) * room.headboard_alignment(item)
            away = 0.0 if nearest is None else max(0.0, nearest[0] - NEAR_WINDOW)  # keeps far beds as close as they can be
            cx, cy = _centre(ring)
            spread = math.hypot(cx - room.centre[0], cy - room.centre[1]) / room.half_diagonal
            options.append((3.0 * near - 0.5 * away + spread, item))
    return options


def _nightstand_options(room: _Room, state: _State, piece: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    """Beside the head of the bed on the bed wall, the side with more free wall first."""
    bed, upm = state.bed, room.upm
    wall = room.wall[bed["wall"]]
    along, gap = piece["along"] * upm, PIECE_GAP * upm
    before = bed["start"] - along - gap
    after = bed["start"] + bed["geom"]["width"] + gap
    options = []
    for start, free in ((before, before), (after, wall["length"] - after - along)):
        if free < -EPS:
            continue
        geom = _on_wall(wall, piece, start, upm)
        if room.fits(state, _ring(geom)):
            options.append((free, _item(piece, geom, wall=wall["index"], start=start)))
    return options


def _chair_options(room: _Room, state: _State, piece: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    """Pulled up to the front of the next desk that has no chair yet."""
    seated = {item.get("desk") for item in state.items if item["role"] == "chair"}
    desk = next((item for item in state.items if item["role"] == "desk" and item["id"] not in seated), None)
    if desk is None:
        return []
    upm = room.upm
    normal = room.wall[desk["wall"]]["normal"]
    front = desk["geom"]["front_center"]
    offset = PIECE_GAP * upm + piece["into"] * upm / 2
    centre = (front[0] + normal[0] * offset, front[1] + normal[1] * offset)
    geom = _place_centered(centre, piece["along"], piece["into"], upm, (desk["geom"]["rotation"] + 180) % 360)
    if not room.fits(state, _ring(geom), skip_zone_of=desk["id"]):
        return []
    return [(0.0, _item(piece, geom, desk=desk["id"]))]


def _wall_score(room: _Room, state: _State, item: dict[str, Any]) -> float:
    """Local preference used to choose which wall slots are worth expanding."""
    ring, wall, upm = item["ring"], room.wall[item["wall"]], room.upm
    cx, cy = _centre(ring)
    score = math.hypot(cx - room.centre[0], cy - room.centre[1]) / room.half_diagonal
    tucked = (SIDE_GAP + 0.02) * upm
    if item["start"] <= tucked or item["start"] + item["geom"]["width"] >= wall["length"] - tucked:
        score += 0.6      # corner pieces read as built-in and leave the widest open floor
    if item["piece"]["tall"] and room.covers_window(item):
        score -= 6.0
    bed = state.bed
    if item["role"] == "desk":
        if bed is not None and item["wall"] == bed["wall"]:
            score -= 4.0
        score += 2.5 * room.daylight(ring)
    if bed is not None:
        score -= 2.5 * sum(1 for zone in bed["zones"] if _hit(ring, zone))
    if room.door_zones:
        gap = min(_ring_distance(ring, zone) for zone in room.door_zones) / upm
        score -= max(0.0, 0.3 - gap) / 0.3
    return score


def _wall_options(room: _Room, state: _State, piece: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    """Every flush-to-wall slot whose front clearance stays free, scored locally."""
    upm = room.upm
    along, into = piece["along"] * upm, piece["into"] * upm
    clearance = FRONT_CLEARANCE.get(piece["role"], FRONT_CLEARANCE["other"]) * upm
    gap, tolerance = SIDE_GAP * upm, 0.02 * upm
    neighbours = [item["ring"] for item in state.items] + room.door_zones + room.obstacles
    if state.bed is not None:
        neighbours += state.bed["zones"]
    options = []
    for wall in room.walls:
        windows = room.windows[wall["index"]]
        raw = _beside(_intervals(wall, neighbours, into + clearance), along, PIECE_GAP * upm)
        raw += _beside(windows, along, gap)
        if piece["role"] == "desk":
            raw += [(s0 + s1) / 2 - along / 2 for s0, s1 in windows]
        for start in _slot_starts(wall, along, gap, raw, tolerance):
            geom = _on_wall(wall, piece, start, upm)
            ring = _ring(geom)
            if not room.fits(state, ring):
                continue
            zone = _local_ring(geom, 0.0, geom["width"], geom["depth"], geom["depth"] + clearance)
            if not room.zone_free(state, zone):
                continue
            item = _item(piece, geom, wall=wall["index"], start=start, zone=zone)
            options.append((_wall_score(room, state, item), item))
    return options


def _options(room: _Room, state: _State, piece: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    if piece["role"] == "nightstand" and state.bed is not None:
        return _nightstand_options(room, state, piece)
    if piece["role"] == "chair":
        seated = _chair_options(room, state, piece)
        if seated:
            return seated
    return _wall_options(room, state, piece)


def _expand(room: _Room, state: _State, pieces: list[dict[str, Any]]) -> list[_State]:
    """Depth-first: the best slot for each piece, branching on the first wardrobe and desk."""
    if not pieces:
        return [state]
    piece, rest = pieces[0], pieces[1:]
    options = _options(room, state, piece)
    if not options:
        return _expand(room, state.skip(piece), rest)
    leaves: list[_State] = []
    for _score, item in _pick(options, piece["branch"]):
        leaves.extend(_expand(room, state.plus(item), rest))
    return leaves


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _clear_run(room: _Room, others, origin, direction, limit: float = 1.0) -> float:
    """Metres of free floor from ``origin`` in ``direction``, capped at ``limit``."""
    step = 0.05
    for k in range(1, int(round(limit / step)) + 1):
        distance = k * step * room.upm
        point = (origin[0] + direction[0] * distance, origin[1] + direction[1] * distance)
        if (not point_in_polygon(point, room.polygon)
                or any(box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3] and point_in_ring(point, ring)
                       for ring, box in others)):
            return (k - 1) * step
    return limit


def _walkways(room: _Room, state: _State, blocked: list[bool], clearance: list[int]) -> tuple[float, int, float]:
    """``(share of destinations reached, destinations missed, dead floor in m²)``.

    Walkways are paths about 0.6 m wide from the door.  Destinations are a side
    or the foot of the bed, and the standing spot in front of every wardrobe,
    dresser, desk (behind its chair), or other piece.  Dead floor is free floor
    out of arm's reach of every walkway, such as a strip cut off by a bed that
    runs from wall to wall.
    """
    grid, upm = room.grid, room.upm
    walkable = [value >= grid.min_clear for value in clearance]
    if room.door_points:
        starts = [grid.nearest(point, walkable, 0.6) for point in room.door_points]
    else:
        starts = [max(range(len(clearance)), key=clearance.__getitem__)]
    reached = grid.reachable(walkable, starts)
    usable = grid.around(reached, blocked, grid.min_clear)
    dead = sum(1 for index, cell in enumerate(blocked) if not cell and not usable[index]) * grid.cell_m ** 2
    step = 0.35 * upm
    chairs = {item["desk"]: item for item in state.items if item.get("desk")}
    targets = []
    for item in state.items:
        geom = item["geom"]
        width, depth = geom["width"], geom["depth"]
        if item["role"] == "bed":
            targets.append([_local_point(geom, -step, depth * .6), _local_point(geom, width + step, depth * .6),
                            _local_point(geom, width / 2, depth + step)])
        elif item["zone"] is not None:
            seat = chairs.get(item["id"])
            reach = depth + step + (seat["geom"]["depth"] + PIECE_GAP * upm if seat else 0.0)
            targets.append([_local_point(geom, width / 2, reach)])
    hits = sum(1 for points in targets if any(grid.nearest(point, reached, 0.35) is not None for point in points))
    return (hits / len(targets) if targets else 1.0), len(targets) - hits, dead


def _bed_access(room: _Room, state: _State) -> float:
    """Clear floor along the bed's long sides (both for a double) and at its foot."""
    bed = state.bed
    others = [(item["ring"], _bbox(item["ring"])) for item in state.items if item is not bed]
    geom = bed["geom"]
    width, depth = geom["width"], geom["depth"]
    radians = math.radians(geom["rotation"])
    along = (math.cos(radians), math.sin(radians))
    back = (-along[0], -along[1])
    front = (-along[1], along[0])
    sides = sorted((min(_clear_run(room, others, _local_point(geom, 0.0, depth * f), back) for f in (.55, .85)),
                    min(_clear_run(room, others, _local_point(geom, width, depth * f), along) for f in (.55, .85))),
                   reverse=True)
    foot = min(_clear_run(room, others, _local_point(geom, width * f, depth), front) for f in (.3, .7))
    need = 2 if bed["piece"]["along"] >= DOUBLE_BED else 1
    side_quality = sum(min(1.0, side / BED_ACCESS) for side in sides[:need]) / need
    return 0.75 * side_quality + 0.25 * min(1.0, foot / BED_ACCESS)


def _bed_note(room: _Room, bed: dict[str, Any], nearest: tuple[float, int] | None) -> str:
    wall, upm = room.wall[bed["wall"]], room.upm
    start, end = bed["start"], bed["start"] + bed["geom"]["width"]
    tucked = (SIDE_GAP + 0.02) * upm
    cornered = start <= tucked or end >= wall["length"] - tucked
    if nearest is None:
        return "Bed headboard against a solid wall (the room has no window)"
    if room.covers_window(bed):
        return ("Bed headboard centred under the window" if room.headboard_alignment(bed) == 1.0
                else "Bed headboard partly under the window")
    gap, window_wall = nearest
    if gap <= BY_WINDOW:
        if window_wall == wall["index"]:
            return "Bed headboard right beside the window"
        return "Bed tucked into the corner, running alongside the window" if cornered \
            else "Bed running alongside the window"
    if gap <= NEAR_WINDOW:
        return "Bed placed near the window"
    return "Bed placed away from the windows (no wall near a window could take it clear of the door)"


def _spacing(room: _Room, state: _State, bed_access: float) -> float:
    """Room to use every piece, averaged over the bed, storage, desks, and desk chairs.

    Pieces that belong together (a nightstand beside the bed, a chair at its desk)
    are meant to stand close, so gaps between pairs of pieces are not measured.
    What counts is the clear floor each piece needs: beside and at the foot of the
    bed, in front of storage and desks, and behind a desk chair to pull it out.
    Half the mark is the average and half the tightest of those spots, so one
    cramped piece shows up instead of being averaged away.
    """
    seats = {item["desk"]: item["id"] for item in state.items if item.get("desk")}
    checks = [bed_access]
    for item in state.items:
        geom = item["geom"]
        radians = math.radians(geom["rotation"])
        front = (-math.sin(radians), math.cos(radians))
        if item.get("desk"):                     # a desk chair: room behind it to pull it out
            edge, direction, need, skip = 0.0, (-front[0], -front[1]), CHAIR_PULL_OUT, {item["id"]}
        elif item["zone"] is not None:           # storage, a desk, ...: room to stand in front
            edge, direction = geom["depth"], front
            need = FRONT_CLEARANCE.get(item["role"], FRONT_CLEARANCE["other"]) + SPACING_COMFORT
            skip = {item["id"], seats.get(item["id"])}
        else:
            continue
        others = [(other["ring"], _bbox(other["ring"])) for other in state.items if other["id"] not in skip]
        run = min(_clear_run(room, others, _local_point(geom, geom["width"] * f, edge), direction, need)
                  for f in (.25, .5, .75))
        checks.append(min(1.0, run / need))
    return 0.5 * sum(checks) / len(checks) + 0.5 * min(checks)


def _table_near_wall(room: _Room, state: _State) -> float:
    """Study and dressing tables flush against a wall; a study table on a different wall from the bed."""
    values = []
    for item in state.items:
        if item["role"] not in ("desk", "dresser"):
            continue
        back = _local_point(item["geom"], item["geom"]["width"] / 2, 0.0)
        wall = min(room.walls, key=lambda candidate: _point_segment_distance(back, candidate["a"], candidate["b"]))
        value = _ramp(_point_segment_distance(back, wall["a"], wall["b"]) / room.upm, WALL_GAP + 0.05, 0.5)
        if item["role"] == "desk" and wall["index"] == state.bed["wall"]:
            value *= 0.5
        values.append(value)
    values += [0.0 for piece in state.missing if piece["required"] and piece["role"] in ("desk", "dresser")]
    return sum(values) / len(values) if values else 1.0


def _nightstand_near_bed(room: _Room, state: _State) -> float:
    """Nightstands right beside the head of the bed, on its headboard wall."""
    bed = state.bed
    values = []
    for item in state.items:
        if item["role"] == "nightstand":
            value = _ramp(_ring_distance(item["ring"], bed["ring"]) / room.upm, 0.1, 0.6)
            values.append(value if item["wall"] == bed["wall"] else 0.5 * value)
    values += [0.0 for piece in state.missing if piece["required"] and piece["role"] == "nightstand"]
    return sum(values) / len(values) if values else 1.0


def _evaluate(room: _Room, state: _State) -> tuple[dict[str, float], dict[str, float], list[str], float]:
    """``(score components, penalties, notes, planning rank)`` for a finished layout.

    Both measures come from the same floor-grid measurements.  The score
    components (``COMPONENT_MAX``) are the bedroom scoring shown on the cards.
    The planning rank (``_RANK_WEIGHTS``) is what the generator uses to choose
    which arrangements to show; it also weighs how close the bed is to a window
    and how much daylight the study table gets.
    """
    upm, grid = room.upm, room.grid
    items, bed = state.items, state.bed
    rings = [item["ring"] for item in items]
    blocked = grid.blocked(rings)
    clearance = grid.clearance(blocked)
    penalties: dict[str, float] = {}

    nearest = room.nearest_window(bed["ring"])
    bed_window = _window_quality(nearest) * room.headboard_alignment(bed)
    if _window_tier(nearest) == 2:
        penalties["bed_far_from_window"] = round(min(25.0, _PENALTY["bed_far_from_window"]
                                                     + 3.0 * (nearest[0] - NEAR_WINDOW)), 2)

    # Walkways from the door; floor that no walkway reaches is wasted.
    reached, missed, dead = _walkways(room, state, blocked, clearance)
    free = sum(1 for cell in blocked if not cell) * grid.cell_m ** 2
    usable = max(0.0, 1.0 - dead / free) if free > EPS else 1.0
    walkways = 0.6 * reached + 0.4 * usable
    if missed:
        penalties["blocked_access"] = _PENALTY["blocked_access"] * missed
    if dead >= DEAD_FLOOR:
        penalties["dead_floor"] = round(min(_PENALTY["dead_floor"], 6.0 * dead), 2)

    # Open floor: how much of the middle is free, and the largest clear square anywhere.
    central_free = (sum(1 for index in grid.central if not blocked[index]) / len(grid.central)
                    if grid.central else 1.0)
    square = max(0, 2 * max(clearance, default=0) - 1) * grid.cell_m
    target = min(1.5, 0.5 * min(room.box["width"], room.box["height"]) / upm)
    square_share = min(1.0, square / max(target, EPS))
    open_center = 0.5 * central_free + 0.5 * square_share

    # Study table on a different wall from the bed, as close to daylight as possible
    # (full marks when the room has no study table to place).
    entries: list[tuple[float, float]] = []
    for item in items:
        if item["role"] == "desk" and item["wall"] is not None:
            entries.append((3.0, 0.0 if item["wall"] == bed["wall"] else 1.0))
            if room.window_segments:
                entries.append((2.0, room.daylight(item["ring"])))
    entries += [(5.0, 0.0) for piece in state.missing if piece["required"] and piece["role"] == "desk"]
    study_table = (sum(weight * value for weight, value in entries) / sum(weight for weight, _ in entries)
                   if entries else 1.0)

    if any(item["role"] == "desk" and item["wall"] == bed["wall"] for item in items):
        penalties["desk_on_bed_wall"] = _PENALTY["desk_on_bed_wall"]
    if any(item["piece"]["tall"] and room.covers_window(item) for item in items):
        penalties["storage_covers_window"] = _PENALTY["storage_covers_window"]
    for piece in state.missing:
        if piece["required"]:
            key = f"{piece['spec']['id']}_missing"
            penalties[key] = penalties.get(key, 0.0) + _PENALTY["missing"]

    door_clearance = 1.0
    if room.door_zones:
        gap = min(_ring_distance(ring, zone) for ring in rings for zone in room.door_zones) / upm
        door_clearance = min(1.0, gap / 0.45)
    bed_access = _bed_access(room, state)
    penalty = sum(penalties.values())

    # The bedroom score shown on the cards.
    shares = {
        # nothing crowding the door approach, and a walkway from the door to every piece
        "door_access": 0.5 * door_clearance + 0.5 * reached,
        # a free middle, one large clear area, and no floor cut off from the walkways
        "open_space": 0.4 * central_free + 0.3 * square_share + 0.3 * usable,
        "spacing": _spacing(room, state, bed_access),
        "table_near_wall": _table_near_wall(room, state),
        "nightstand_near_bed": _nightstand_near_bed(room, state),
    }
    components = {key: round(COMPONENT_MAX[key] * value, 2) for key, value in shares.items()}

    # The planning rank used to choose which arrangements are shown.
    planning = {"bed_window": bed_window, "open_center": open_center, "study_table": study_table,
                "bed_access": bed_access, "walkways": walkways, "door_clearance": door_clearance}
    rank = round(max(0.0, sum(round(_RANK_WEIGHTS[key] * value, 2) for key, value in planning.items()) - penalty), 2)

    notes = _notes(room, state, nearest, central_free)
    if missed:
        notes.append(f"{missed} piece(s) can only be reached by squeezing past other furniture")
    if dead >= DEAD_FLOOR:
        notes.append(f"{dead:.1f} m² of floor is cut off from the walkways")
    return components, penalties, notes, rank


def _notes(room: _Room, state: _State, nearest: tuple[float, int] | None, central_free: float) -> list[str]:
    bed = state.bed
    notes = [_bed_note(room, bed, nearest)]
    stands = [item for item in state.items if item["role"] == "nightstand"]
    if len(stands) >= 2:
        notes.append("Nightstands on both sides of the bed head")
    elif stands:
        notes.append("Nightstand beside the bed head")
    for item in state.items:
        name = item["piece"]["spec"]["id"].replace("-", " ").replace("_", " ").capitalize()
        if item["role"] == "desk" and item["wall"] is not None:
            if item["wall"] == bed["wall"]:
                notes.append(f"{name} shares the bed wall (no other wall had room)")
            else:
                light = " by the window" if room.daylight(item["ring"]) >= 0.8 else ""
                notes.append(f"{name} flush against a different wall from the bed{light}")
        elif item["role"] in ("wardrobe", "dresser") and item["wall"] is not None:
            where = ("covers a window (no other wall had room)" if item["piece"]["tall"] and room.covers_window(item)
                     else "flush against a wall")
            notes.append(f"{name} {where}, {FRONT_CLEARANCE[item['role']]:.1f} m clear in front")
        elif item["role"] == "chair" and item.get("desk"):
            notes.append(f"{name} pulled up to the desk")
    for piece in state.missing:
        notes.append(f"{piece['id']} left out: no wall space stayed clear of the door, the bed, and the other pieces")
    notes.append(f"{round(central_free * 100)}% of the room centre left open")
    return notes


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _placement(item: dict[str, Any], box: dict[str, float], meters_per_unit: float) -> dict[str, Any]:
    piece, geom = item["piece"], item["geom"]
    spec = piece["spec"]
    return {
        "id": piece["id"], "catalogId": spec["id"],
        "x": geom["x"], "y": geom["y"], "width": geom["width"], "depth": geom["depth"],
        "widthMeters": round(piece["along"], 4), "depthMeters": round(piece["into"], 4),
        "heightMeters": spec.get("height"),
        "rotation": geom["rotation"], "frontDirection": geom["rotation"],
        "locked": False, "required": piece["required"],
        "wallAttached": bool(spec.get("wallAttached", False)),
        "supportWallId": spec.get("supportWallId"), "supportKind": spec.get("supportKind"),
        "accessPoint": spec.get("accessPoint"),
        "positionMeters": {"x": (geom["x"] - box["x"]) * meters_per_unit,
                           "y": (geom["y"] - box["y"]) * meters_per_unit},
    }


def _diversity_keys(room: _Room, state: _State) -> dict[str, Any]:
    def walls(*roles: str) -> tuple:
        return tuple(sorted(item["wall"] for item in state.items if item["role"] in roles and item["wall"] is not None))

    bed = state.bed
    bed_slot = (bed["wall"], round(bed["start"] / room.upm / 0.4))
    return {"bedWall": bed["wall"], "bedSlot": bed_slot,
            "deskWall": walls("desk"), "storageWall": walls("wardrobe", "dresser"),
            # Sliding a piece along its wall is a nudge; a new arrangement moves the
            # bed or puts some piece on a different wall.
            "arrangement": (bed_slot, tuple(sorted((item["id"], item["wall"]) for item in state.items[1:]
                                                   if item["wall"] is not None)))}


def _diverse_layouts(layouts: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Best layout first, then the strongest alternatives that change the arrangement.

    Which arrangements are shown is decided by the planning rank, not by the
    bedroom score on the cards.  An alternative must be a different arrangement
    from every layout already chosen; a nudge of a shown arrangement is used only
    when nothing different ranks within twice ``DIVERSITY_FLOOR`` of the best.
    Complete layouts come first, then those with the bed right by a window
    (before merely near one), then those breaking no rule; among equals, a new
    bed wall beats a new bed position, which beats a new desk wall, then a new
    storage wall.  The chosen layouts are then handed over in score order.
    """
    def standing(entry: dict[str, Any]) -> tuple:
        return (entry["_complete"], -entry["_tier"], -sum(rule in entry["penalties"] for rule in _RULES))

    ranked = sorted(layouts, key=lambda entry: (*standing(entry), entry["_rank"]), reverse=True)
    if not ranked:
        return []
    selected = [ranked.pop(0)]
    best = selected[0]["_rank"]
    seen = {key: {value} for key, value in selected[0]["_keys"].items()}
    while ranked and len(selected) < count:
        distinct = [entry for entry in ranked if entry["_keys"]["arrangement"] not in seen["arrangement"]]
        pool = ([entry for entry in distinct if entry["_rank"] >= best - DIVERSITY_FLOOR]
                or [entry for entry in distinct if entry["_rank"] >= best - 2 * DIVERSITY_FLOOR]
                or [entry for entry in ranked if entry["_rank"] >= best - DIVERSITY_FLOOR]
                or distinct or ranked)
        chosen = max(pool, key=lambda entry: (
            *standing(entry),
            *(entry["_keys"][key] not in seen[key] for key in ("bedWall", "bedSlot", "deskWall", "storageWall")),
            entry["_rank"]))
        ranked = [entry for entry in ranked if entry is not chosen]
        selected.append(chosen)
        for key, value in chosen["_keys"].items():
            seen[key].add(value)
    selected.sort(key=lambda entry: (*standing(entry), entry["score"]), reverse=True)
    for entry in selected:
        del entry["_complete"], entry["_tier"], entry["_keys"], entry["_rank"]
    return selected


def generate_bedroom_layouts(request: dict[str, Any]) -> dict[str, Any]:
    polygon = request.get("polygon")
    if not polygon:
        raise ProjectValidationError("A reviewed room polygon is required before layout generation")
    validate_ring(polygon.get("outer"))
    for index, hole in enumerate(polygon.get("holes", [])):
        validate_ring(hole, f"polygon.holes[{index}]")
    if not request.get("scale", {}).get("confirmed"):
        raise ProjectValidationError("A confirmed two-point scale is required before layout generation")
    meters_per_unit = float(request.get("scale", {}).get("metersPerPixel", 1.0))
    if meters_per_unit <= 0:
        raise ProjectValidationError("scale.metersPerPixel must be positive")

    units_per_meter = 1.0 / meters_per_unit
    revision = int(request.get("inputRevision", 0))
    seed = int(request.get("seed", 0))
    count = min(6, max(1, int(request.get("count", 6))))

    pieces = _pieces(request.get("furniture", []))
    bed_piece = next((piece for piece in pieces if piece["role"] == "bed"), None)
    if bed_piece is None:
        return _no_layouts(request, revision, "A bedroom layout needs a bed")
    room = _Room(request, units_per_meter)
    if len(room.walls) < 3:
        raise ProjectValidationError("Room polygon must have at least three non-degenerate walls")
    rest = [piece for piece in pieces if piece is not bed_piece]
    stands = [piece for piece in rest if piece["role"] == "nightstand"]
    headroom = (max((piece["along"] for piece in stands), default=0.0),
                max((piece["into"] for piece in stands), default=0.0))
    beds = _pick(_bed_options(room, bed_piece, headroom), MAX_BEDS)
    if not beds:
        return _no_layouts(request, revision, "The bed does not fit against any wall outside the door approach")

    doors_only = [opening for opening in request.get("openings", []) if opening.get("kind") == "door"]
    excluded = {_layout_signature(entry.get("placements", [])) for entry in request.get("excludeLayouts", [])}
    seen: set[tuple] = set()
    layouts: list[dict[str, Any]] = []
    explored = repeats = 0
    for _score, bed in _seed_rotate(beds, seed):
        for state in _expand(room, _State((bed,)), rest):
            explored += 1
            placements = [_placement(item, room.box, meters_per_unit) for item in state.items]
            signature = _layout_signature(placements)
            if signature in excluded:
                repeats += 1
                continue
            if signature in seen:
                continue
            seen.add(signature)
            # Every candidate is re-checked by the shared validator before it ships.
            verdict = validate_layout({**request, "openings": doors_only, "placements": placements})
            if any(violation["code"] != "blocked-route" for violation in verdict["violations"]):
                continue
            components, penalties, notes, rank = _evaluate(room, state)
            layouts.append({
                "id": "layout", "roomId": request.get("roomId"), "placements": placements,
                "validity": "valid" if not penalties else "review",
                "violations": [], "assumptions": notes + verdict["assumptions"],
                "score": round(max(0.0, sum(components.values()) - sum(penalties.values())), 2),
                "scoreComponents": components, "scoreComponentMax": dict(COMPONENT_MAX),
                "penalties": penalties, "scoreKind": "constructive-bedroom",
                "seed": seed, "inputRevision": revision,
                "_complete": not any(piece["required"] for piece in state.missing),
                "_tier": _window_tier(room.nearest_window(state.bed["ring"])),
                "_keys": _diversity_keys(room, state), "_rank": rank,
            })

    selected = _diverse_layouts(layouts, count)
    for position, layout in enumerate(selected, start=1):
        layout["id"] = f"layout-{position}"
    if selected:
        return {"requestId": request.get("requestId"), "inputRevision": revision, "layouts": selected,
                "partial": False, "exploredCandidates": explored, "reasons": []}
    if repeats:
        return _no_layouts(request, revision, "Every arrangement that fits this room has already been shown",
                           explored, code="no-new-layout")
    return _no_layouts(request, revision, "No arrangement kept the furniture clear of the doors and each other",
                       explored)


def _no_layouts(request: dict[str, Any], revision: int, reason: str, explored: int = 0,
                code: str = "no-feasible-layout") -> dict[str, Any]:
    return {
        "requestId": request.get("requestId"), "inputRevision": revision, "layouts": [],
        "partial": False, "exploredCandidates": explored,
        "reasons": [{
            "code": code,
            "message": f"No new bedroom layout could be built. {reason}." if code == "no-new-layout"
            else f"No bedroom layout could be built. {reason}.",
            "suggestions": [
                "Check the confirmed two-point scale (metres per pixel)",
                "Confirm the room polygon is the room, not the whole plan",
                "Reduce the bed or wardrobe dimensions, or mark optional pieces as not required",
            ],
        }],
    }
