from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal


Wall = Literal["north", "east", "south", "west"]
RelationKind = Literal["near", "opposite"]


def _all_finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def _cross(a: "Point", b: "Point", c: "Point") -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(point: "Point", start: "Point", end: "Point") -> bool:
    return (
        abs(_cross(start, end, point)) <= 1e-9
        and min(start.x, end.x) - 1e-9 <= point.x <= max(start.x, end.x) + 1e-9
        and min(start.y, end.y) - 1e-9 <= point.y <= max(start.y, end.y) + 1e-9
    )


def _segments_intersect(a1: "Point", a2: "Point", b1: "Point", b2: "Point") -> bool:
    c1, c2 = _cross(a1, a2, b1), _cross(a1, a2, b2)
    c3, c4 = _cross(b1, b2, a1), _cross(b1, b2, a2)
    if ((c1 > 1e-9 and c2 < -1e-9) or (c1 < -1e-9 and c2 > 1e-9)) and (
        (c3 > 1e-9 and c4 < -1e-9) or (c3 < -1e-9 and c4 > 1e-9)
    ):
        return True
    return any(
        abs(cross) <= 1e-9 and _on_segment(point, start, end)
        for cross, point, start, end in (
            (c1, b1, a1, a2), (c2, b2, a1, a2),
            (c3, a1, b1, b2), (c4, a2, b1, b2),
        )
    )


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Opening:
    """A door or window measured along one wall from its west/south origin."""

    kind: Literal["door", "window"]
    wall: Wall | None
    offset: float
    width: float
    clearance: float = 0.8
    id: str | None = None
    segment: tuple[Point, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("door", "window"):
            raise ValueError(f"Unsupported opening kind: {self.kind}")
        if self.wall not in ("north", "east", "south", "west", None):
            raise ValueError(f"Unsupported opening wall: {self.wall}")
        if not _all_finite(self.offset, self.width, self.clearance) or self.offset < 0 or self.width <= 0 or self.clearance < 0:
            raise ValueError("Opening offset/width/clearance values are invalid")
        if self.segment:
            if len(self.segment) != 2 or self.segment[0] == self.segment[1]:
                raise ValueError("Opening segment must contain two distinct points")
            start, end = self.segment
            if abs(start.x - end.x) > 1e-9 and abs(start.y - end.y) > 1e-9:
                raise ValueError("Only horizontal or vertical opening segments are supported")
        elif self.wall is None:
            raise ValueError("Opening requires either a wall or a boundary segment")


@dataclass(frozen=True, slots=True)
class Obstacle:
    """A fixed rectangular object or unusable zone, in room-local metres."""

    id: str
    x: float
    y: float
    width: float
    length: float
    clearance: float = 0.0

    def __post_init__(self) -> None:
        if not _all_finite(self.x, self.y, self.width, self.length, self.clearance) or self.x < 0 or self.y < 0 or self.width <= 0 or self.length <= 0 or self.clearance < 0:
            raise ValueError("Obstacle position, dimensions, or clearance are invalid")


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    width: float
    length: float
    room_type: str = "generic"
    openings: tuple[Opening, ...] = ()
    polygon: tuple[Point, ...] = ()
    obstacles: tuple[Obstacle, ...] = ()

    def __post_init__(self) -> None:
        if not _all_finite(self.width, self.length) or self.width <= 0 or self.length <= 0:
            raise ValueError("Room dimensions must be positive")
        for opening in self.openings:
            if opening.segment:
                for point in opening.segment:
                    if not (-1e-9 <= point.x <= self.width + 1e-9 and -1e-9 <= point.y <= self.length + 1e-9):
                        raise ValueError("Opening segment lies outside the room bounds")
                continue
            assert opening.wall is not None
            wall_length = self.width if opening.wall in ("north", "south") else self.length
            if opening.offset + opening.width > wall_length + 1e-9:
                raise ValueError(f"Opening extends beyond the {opening.wall} wall")
        if self.polygon:
            if len(self.polygon) < 3:
                raise ValueError("Room polygon must contain at least three points")
            for point in self.polygon:
                if not (-1e-9 <= point.x <= self.width + 1e-9 and -1e-9 <= point.y <= self.length + 1e-9):
                    raise ValueError("Room polygon point lies outside the room bounds")
            edge_count = len(self.polygon)
            for first_index in range(edge_count):
                first_start = self.polygon[first_index]
                first_end = self.polygon[(first_index + 1) % edge_count]
                if first_start == first_end:
                    raise ValueError("Room polygon contains a zero-length edge")
                for second_index in range(first_index + 1, edge_count):
                    if second_index in (first_index, (first_index + 1) % edge_count):
                        continue
                    if first_index == 0 and second_index == edge_count - 1:
                        continue
                    second_start = self.polygon[second_index]
                    second_end = self.polygon[(second_index + 1) % edge_count]
                    if _segments_intersect(first_start, first_end, second_start, second_end):
                        raise ValueError("Room polygon must be simple and non-self-intersecting")
            area = abs(sum(
                point.x * self.polygon[(index + 1) % len(self.polygon)].y
                - self.polygon[(index + 1) % len(self.polygon)].x * point.y
                for index, point in enumerate(self.polygon)
            )) / 2
            if area <= 1e-9:
                raise ValueError("Room polygon must have a positive area")
        for obstacle in self.obstacles:
            if obstacle.x + obstacle.width > self.width + 1e-9 or obstacle.y + obstacle.length > self.length + 1e-9:
                raise ValueError(f"Obstacle {obstacle.id} extends beyond the room bounds")


@dataclass(frozen=True, slots=True)
class FurnitureItem:
    id: str
    name: str
    category: str
    width: float
    length: float
    quantity: int = 1
    required: bool = True
    wall_preferred: bool = False
    clearance: float = 0.15

    def __post_init__(self) -> None:
        if not _all_finite(self.width, self.length, self.clearance) or self.width <= 0 or self.length <= 0 or self.quantity < 1 or self.clearance < 0:
            raise ValueError("Furniture dimensions, quantity, or clearance are invalid")


@dataclass(frozen=True, slots=True)
class FurnitureRelation:
    """A preferred functional relationship between two furniture types."""

    source_id: str
    target_id: str
    kind: RelationKind = "near"
    min_distance: float = 0.0
    max_distance: float = 1.0
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.source_id or not self.target_id or self.source_id == self.target_id:
            raise ValueError("Furniture relation requires two different furniture IDs")
        if self.kind not in ("near", "opposite"):
            raise ValueError(f"Unsupported furniture relation: {self.kind}")
        if not _all_finite(self.min_distance, self.max_distance, self.weight) or self.min_distance < 0 or self.max_distance < self.min_distance or self.weight <= 0:
            raise ValueError("Furniture relation distance or weight is invalid")


@dataclass(frozen=True, slots=True)
class Placement:
    furniture_id: str
    instance: int
    x: float
    y: float
    width: float
    length: float
    rotation: int

    @property
    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.length / 2)


@dataclass(slots=True)
class Layout:
    id: str
    room_id: str
    placements: list[Placement]
    score: float
    score_breakdown: dict[str, float] = field(default_factory=dict)
    recommended: bool = False
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "room_id": self.room_id,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "recommended": self.recommended,
            "selected": self.selected,
            "placements": [
                {
                    "furniture_id": p.furniture_id,
                    "instance": p.instance,
                    "position_x": round(p.x, 3),
                    "position_y": round(p.y, 3),
                    "width": p.width,
                    "length": p.length,
                    "rotation": p.rotation,
                }
                for p in self.placements
            ],
        }
