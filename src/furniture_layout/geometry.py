from __future__ import annotations

import math

from .models import Obstacle, Opening, Placement, Point, Room


EPSILON = 1e-9


def room_polygon(room: Room) -> tuple[Point, ...]:
    if room.polygon:
        return room.polygon
    return (
        Point(0.0, 0.0),
        Point(room.width, 0.0),
        Point(room.width, room.length),
        Point(0.0, room.length),
    )


def polygon_area(points: tuple[Point, ...]) -> float:
    return abs(sum(
        point.x * points[(index + 1) % len(points)].y
        - points[(index + 1) % len(points)].x * point.y
        for index, point in enumerate(points)
    )) / 2


def room_area(room: Room) -> float:
    return polygon_area(room_polygon(room))


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    return (
        abs(_cross(start, end, point)) <= EPSILON
        and min(start.x, end.x) - EPSILON <= point.x <= max(start.x, end.x) + EPSILON
        and min(start.y, end.y) - EPSILON <= point.y <= max(start.y, end.y) + EPSILON
    )


def point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    """Return True for points inside or on the boundary of a simple polygon."""
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _point_on_segment(point, start, end):
            return True
        if (start.y > point.y) != (end.y > point.y):
            boundary_x = (end.x - start.x) * (point.y - start.y) / (end.y - start.y) + start.x
            if point.x < boundary_x:
                inside = not inside
    return inside


def _properly_intersects(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    """Detect a boundary crossing while allowing collinear/touching edges."""
    c1 = _cross(a1, a2, b1)
    c2 = _cross(a1, a2, b2)
    c3 = _cross(b1, b2, a1)
    c4 = _cross(b1, b2, a2)
    return (
        ((c1 > EPSILON and c2 < -EPSILON) or (c1 < -EPSILON and c2 > EPSILON))
        and ((c3 > EPSILON and c4 < -EPSILON) or (c3 < -EPSILON and c4 > EPSILON))
    )


def _placement_points(p: Placement) -> tuple[Point, Point, Point, Point]:
    return (
        Point(p.x, p.y),
        Point(p.x + p.width, p.y),
        Point(p.x + p.width, p.y + p.length),
        Point(p.x, p.y + p.length),
    )


def inside_room(p: Placement, room: Room) -> bool:
    if not (
        p.x >= -EPSILON
        and p.y >= -EPSILON
        and p.x + p.width <= room.width + EPSILON
        and p.y + p.length <= room.length + EPSILON
    ):
        return False
    if not room.polygon:
        return True

    polygon = room.polygon
    rectangle = _placement_points(p)
    if not all(point_in_polygon(point, polygon) for point in rectangle):
        return False

    rectangle_edges = tuple(
        (point, rectangle[(index + 1) % len(rectangle)])
        for index, point in enumerate(rectangle)
    )
    polygon_edges = tuple(
        (point, polygon[(index + 1) % len(polygon)])
        for index, point in enumerate(polygon)
    )
    if any(
        _properly_intersects(r1, r2, p1, p2)
        for r1, r2 in rectangle_edges
        for p1, p2 in polygon_edges
    ):
        return False

    # A concave notch can cross a rectangle even when all four corners are in
    # the room. A boundary vertex strictly inside the footprint catches it.
    return not any(
        p.x + EPSILON < point.x < p.x + p.width - EPSILON
        and p.y + EPSILON < point.y < p.y + p.length - EPSILON
        for point in polygon
    )


def rectangles_overlap(a: Placement, b: Placement, gap: float = 0.0) -> bool:
    return not (
        a.x + a.width + gap <= b.x + EPSILON
        or b.x + b.width + gap <= a.x + EPSILON
        or a.y + a.length + gap <= b.y + EPSILON
        or b.y + b.length + gap <= a.y + EPSILON
    )


def opening_clearance_rect(opening: Opening, room: Room) -> tuple[float, float, float, float]:
    if opening.segment:
        first, second = opening.segment
        depth = opening.clearance
        side = opening.clearance / 2
        midpoint = Point((first.x + second.x) / 2, (first.y + second.y) / 2)
        polygon = room_polygon(room)
        probe = min(0.05, max(depth / 2, 0.01))
        if abs(first.y - second.y) <= EPSILON:
            start = max(0.0, min(first.x, second.x) - side)
            end = min(room.width, max(first.x, second.x) + side)
            north_inside = point_in_polygon(Point(midpoint.x, midpoint.y + probe), polygon)
            south_inside = point_in_polygon(Point(midpoint.x, midpoint.y - probe), polygon)
            if north_inside and not south_inside:
                y = midpoint.y
            elif south_inside and not north_inside:
                y = midpoint.y - depth
            else:
                y = midpoint.y - depth if opening.wall == "north" else midpoint.y
            y = max(0.0, min(y, room.length - depth))
            return start, y, max(0.0, end - start), min(depth, room.length)

        start = max(0.0, min(first.y, second.y) - side)
        end = min(room.length, max(first.y, second.y) + side)
        east_inside = point_in_polygon(Point(midpoint.x + probe, midpoint.y), polygon)
        west_inside = point_in_polygon(Point(midpoint.x - probe, midpoint.y), polygon)
        if east_inside and not west_inside:
            x = midpoint.x
        elif west_inside and not east_inside:
            x = midpoint.x - depth
        else:
            x = midpoint.x - depth if opening.wall == "east" else midpoint.x
        x = max(0.0, min(x, room.width - depth))
        return x, start, min(depth, room.width), max(0.0, end - start)

    assert opening.wall is not None
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


def blocks_obstacle(p: Placement, obstacle: Obstacle, gap: float = 0.0) -> bool:
    zone = Placement(obstacle.id, 0, obstacle.x, obstacle.y, obstacle.width, obstacle.length, 0)
    return rectangles_overlap(p, zone, max(gap, obstacle.clearance))


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    if abs(dx) <= EPSILON and abs(dy) <= EPSILON:
        return math.hypot(point.x - start.x, point.y - start.y)
    ratio = ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    projection = Point(start.x + ratio * dx, start.y + ratio * dy)
    return math.hypot(point.x - projection.x, point.y - projection.y)


def _segment_distance(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    if _properly_intersects(a1, a2, b1, b2) or any(
        _point_on_segment(point, start, end)
        for point, start, end in (
            (a1, b1, b2), (a2, b1, b2), (b1, a1, a2), (b2, a1, a2)
        )
    ):
        return 0.0
    return min(
        _point_segment_distance(a1, b1, b2),
        _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2),
        _point_segment_distance(b2, a1, a2),
    )


def wall_distance(p: Placement, room: Room) -> float:
    if not room.polygon:
        return min(p.x, p.y, room.width - p.x - p.width, room.length - p.y - p.length)
    rectangle = _placement_points(p)
    boundary = room_polygon(room)
    return min(
        _segment_distance(
            rectangle[index], rectangle[(index + 1) % len(rectangle)],
            boundary[boundary_index], boundary[(boundary_index + 1) % len(boundary)],
        )
        for index in range(len(rectangle))
        for boundary_index in range(len(boundary))
    )


def rectangle_distance(a: Placement, b: Placement) -> float:
    """Return the shortest edge-to-edge distance between two rectangles."""
    dx = max(a.x - (b.x + b.width), b.x - (a.x + a.width), 0.0)
    dy = max(a.y - (b.y + b.length), b.y - (a.y + a.length), 0.0)
    return math.hypot(dx, dy)


def overlap_area(a: Placement, b: Placement) -> float:
    width = max(0.0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    length = max(0.0, min(a.y + a.length, b.y + b.length) - max(a.y, b.y))
    return width * length


def polygon_rectangle_intersection_area(
    polygon: tuple[Point, ...], x: float, y: float, width: float, length: float
) -> float:
    """Integrate vertical slices to intersect a simple polygon and rectangle."""
    min_x, max_x = x, x + width
    min_y, max_y = y, y + length
    critical_x = {min_x, max_x}
    for point in polygon:
        if min_x < point.x < max_x:
            critical_x.add(point.x)
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if abs(end.y - start.y) <= EPSILON:
            continue
        for boundary_y in (min_y, max_y):
            ratio = (boundary_y - start.y) / (end.y - start.y)
            if EPSILON < ratio < 1 - EPSILON:
                crossing_x = start.x + ratio * (end.x - start.x)
                if min_x < crossing_x < max_x:
                    critical_x.add(crossing_x)

    area = 0.0
    ordered_x = sorted(critical_x)
    for left, right in zip(ordered_x, ordered_x[1:]):
        if right - left <= EPSILON:
            continue
        sample_x = (left + right) / 2
        crossings: list[float] = []
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            if (start.x <= sample_x < end.x) or (end.x <= sample_x < start.x):
                ratio = (sample_x - start.x) / (end.x - start.x)
                crossings.append(start.y + ratio * (end.y - start.y))
        crossings.sort()
        covered_y = 0.0
        for lower, upper in zip(crossings[0::2], crossings[1::2]):
            covered_y += max(0.0, min(upper, max_y) - max(lower, min_y))
        area += (right - left) * covered_y
    return max(0.0, min(area, width * length))
