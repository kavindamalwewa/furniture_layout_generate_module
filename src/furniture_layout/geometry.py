from __future__ import annotations

from .models import Opening, Placement, Room


EPSILON = 1e-9


def inside_room(p: Placement, room: Room) -> bool:
    return (
        p.x >= -EPSILON
        and p.y >= -EPSILON
        and p.x + p.width <= room.width + EPSILON
        and p.y + p.length <= room.length + EPSILON
    )


def rectangles_overlap(a: Placement, b: Placement, gap: float = 0.0) -> bool:
    return not (
        a.x + a.width + gap <= b.x + EPSILON
        or b.x + b.width + gap <= a.x + EPSILON
        or a.y + a.length + gap <= b.y + EPSILON
        or b.y + b.length + gap <= a.y + EPSILON
    )


def opening_clearance_rect(opening: Opening, room: Room) -> tuple[float, float, float, float]:
    start = max(0.0, opening.offset - opening.clearance / 2)
    span = opening.width + opening.clearance
    depth = opening.clearance
    if opening.wall == "south":
        return start, 0.0, min(span, room.width - start), depth
    if opening.wall == "north":
        return start, max(0.0, room.length - depth), min(span, room.width - start), depth
    if opening.wall == "west":
        return 0.0, start, depth, min(span, room.length - start)
    return max(0.0, room.width - depth), start, depth, min(span, room.length - start)


def blocks_opening(p: Placement, opening: Opening, room: Room) -> bool:
    x, y, width, length = opening_clearance_rect(opening, room)
    zone = Placement("opening", 0, x, y, width, length, 0)
    return rectangles_overlap(p, zone)


def wall_distance(p: Placement, room: Room) -> float:
    return min(p.x, p.y, room.width - p.x - p.width, room.length - p.y - p.length)

