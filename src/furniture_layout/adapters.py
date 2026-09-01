from __future__ import annotations

import math
from typing import Any

from .geometry import polygon_area
from .models import Point


WALL_ALIASES = {
    "north": "north",
    "top": "north",
    "east": "east",
    "right": "east",
    "south": "south",
    "bottom": "south",
    "west": "west",
    "left": "west",
}


def normalize_wall(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = WALL_ALIASES.get(str(value).strip().lower())
    if normalized is None:
        raise ValueError(f"Unsupported opening wall: {value}")
    return normalized


def _finite_positive(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return number


def _pixel_point_to_room(point: Point, bbox: dict[str, float], scale: float) -> Point:
    return Point(
        (point.x - bbox["x"]) * scale,
        (bbox["y"] + bbox["height"] - point.y) * scale,
    )


def _raw_point(value: Any) -> Point:
    if isinstance(value, dict):
        return Point(float(value["x"]), float(value["y"]))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return Point(float(value[0]), float(value[1]))
    raise ValueError("Polygon points must be [x, y] pairs or {x, y} objects")


def _box(data: dict[str, Any]) -> tuple[float, float, float, float]:
    x1 = float(data["x1"])
    y1 = float(data["y1"])
    x2 = float(data["x2"])
    y2 = float(data["y2"])
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _distance_to_interval(value: float, start: float, end: float) -> float:
    return max(start - value, value - end, 0.0)


def _snap_segment_to_polygon(
    first: Point,
    second: Point,
    polygon: tuple[Point, ...],
) -> tuple[Point, Point]:
    horizontal = abs(first.y - second.y) <= abs(first.x - second.x)
    midpoint = Point((first.x + second.x) / 2, (first.y + second.y) / 2)
    opening_width = math.hypot(second.x - first.x, second.y - first.y)
    candidates: list[tuple[float, Point, Point]] = []
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge_horizontal = abs(start.y - end.y) <= 1e-6
        edge_vertical = abs(start.x - end.x) <= 1e-6
        if horizontal and edge_horizontal:
            low, high = sorted((start.x, end.x))
            distance = abs(midpoint.y - start.y) + _distance_to_interval(midpoint.x, low, high)
            center = max(low, min(high, midpoint.x))
            half = min(opening_width, high - low) / 2
            center = max(low + half, min(high - half, center))
            candidates.append((distance, Point(center - half, start.y), Point(center + half, start.y)))
        elif not horizontal and edge_vertical:
            low, high = sorted((start.y, end.y))
            distance = abs(midpoint.x - start.x) + _distance_to_interval(midpoint.y, low, high)
            center = max(low, min(high, midpoint.y))
            half = min(opening_width, high - low) / 2
            center = max(low + half, min(high - half, center))
            candidates.append((distance, Point(start.x, center - half), Point(start.x, center + half)))
    if not candidates:
        raise ValueError("Detected opening could not be snapped to an axis-aligned room boundary")
    _, snapped_first, snapped_second = min(candidates, key=lambda candidate: candidate[0])
    return snapped_first, snapped_second


def _opening_from_detector_box(
    data: dict[str, Any],
    kind: str,
    index: int,
    bbox: dict[str, float],
    scale: float,
    polygon: tuple[Point, ...],
    room_width: float,
    room_length: float,
) -> dict[str, Any]:
    x1, y1, x2, y2 = _box(data)
    horizontal = (x2 - x1) >= (y2 - y1)
    if horizontal:
        center_y = (y1 + y2) / 2
        first_px, second_px = Point(x1, center_y), Point(x2, center_y)
    else:
        center_x = (x1 + x2) / 2
        first_px, second_px = Point(center_x, y1), Point(center_x, y2)
    first = _pixel_point_to_room(first_px, bbox, scale)
    second = _pixel_point_to_room(second_px, bbox, scale)
    clearance = float(data.get("clearance_m", 0.9 if kind == "door" else 0.35))
    if not math.isfinite(clearance) or clearance < 0:
        raise ValueError("Opening clearance must be a finite non-negative number")

    if polygon:
        first, second = _snap_segment_to_polygon(first, second, polygon)
        width = math.hypot(second.x - first.x, second.y - first.y)
        return {
            "id": str(data.get("id", f"{kind}-{index}")),
            "kind": kind,
            "wall": None,
            "offset": 0.0,
            "width": width,
            "clearance": clearance,
            "segment": [[first.x, first.y], [second.x, second.y]],
        }

    midpoint = Point((first.x + second.x) / 2, (first.y + second.y) / 2)
    if horizontal:
        wall = "south" if midpoint.y <= room_length - midpoint.y else "north"
        offset = max(0.0, min(first.x, second.x))
        width = min(abs(second.x - first.x), room_width - offset)
    else:
        wall = "west" if midpoint.x <= room_width - midpoint.x else "east"
        offset = max(0.0, min(first.y, second.y))
        width = min(abs(second.y - first.y), room_length - offset)
    return {
        "id": str(data.get("id", f"{kind}-{index}")),
        "kind": kind,
        "wall": wall,
        "offset": offset,
        "width": width,
        "clearance": clearance,
    }


def _default_program(room_type: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = room_type.strip().lower().replace(" ", "_").replace("-", "_")
    if "bedroom" in normalized or normalized == "bed":
        return (
            [
                {"id": "bed", "name": "Queen bed", "category": "bed", "width": 1.6, "length": 2.0, "wall_preferred": True, "clearance": 0.3},
                {"id": "wardrobe", "name": "Wardrobe", "category": "storage", "width": 1.5, "length": 0.6, "wall_preferred": True, "clearance": 0.6},
                {"id": "nightstand", "name": "Nightstand", "category": "storage", "width": 0.45, "length": 0.4, "quantity": 2, "clearance": 0.1},
                {"id": "study-table", "name": "Study table", "category": "desk", "width": 1.2, "length": 0.6, "required": False, "wall_preferred": True, "clearance": 0.25},
            ],
            [
                {"source_id": "nightstand", "target_id": "bed", "kind": "near", "min_distance": 0.05, "max_distance": 0.55},
            ],
        )
    if "living" in normalized or "lounge" in normalized:
        return (
            [
                {"id": "sofa", "name": "Three-seat sofa", "category": "seating", "width": 2.2, "length": 0.9, "wall_preferred": True, "clearance": 0.25},
                {"id": "tv-unit", "name": "TV unit", "category": "storage", "width": 1.6, "length": 0.45, "wall_preferred": True, "clearance": 0.2},
                {"id": "coffee-table", "name": "Coffee table", "category": "table", "width": 1.1, "length": 0.6, "clearance": 0.2},
            ],
            [
                {"source_id": "coffee-table", "target_id": "sofa", "kind": "near", "min_distance": 0.3, "max_distance": 0.9},
                {"source_id": "tv-unit", "target_id": "sofa", "kind": "opposite", "min_distance": 1.2, "max_distance": 3.5, "weight": 1.2},
            ],
        )
    if "study" in normalized or "office" in normalized:
        return (
            [
                {"id": "desk", "name": "Study desk", "category": "desk", "width": 1.4, "length": 0.65, "wall_preferred": True, "clearance": 0.25},
                {"id": "office-chair", "name": "Office chair", "category": "seating", "width": 0.65, "length": 0.65, "clearance": 0.25},
                {"id": "bookcase", "name": "Bookcase", "category": "storage", "width": 1.0, "length": 0.35, "wall_preferred": True, "clearance": 0.2},
            ],
            [],
        )
    raise ValueError(
        f"No default furniture program exists for room type '{room_type}'. Provide a furniture array."
    )


def detector_payload_to_metric(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Normalize the detector's global-pixel room JSON to the public metric schema."""
    bbox_data = data.get("bbox_px")
    if not isinstance(bbox_data, dict):
        raise ValueError("Detector payload requires bbox_px")
    bbox = {
        "x": float(bbox_data["x"]),
        "y": float(bbox_data["y"]),
        "width": _finite_positive(bbox_data["width"], "bbox_px.width"),
        "height": _finite_positive(bbox_data["height"], "bbox_px.height"),
    }
    scale = _finite_positive(data.get("scale_m_per_px"), "scale_m_per_px")
    room_width = bbox["width"] * scale
    room_length = bbox["height"] * scale
    warnings: list[str] = []

    raw_polygon = data.get("room_polygon_px", data.get("polygon_px", []))
    polygon = tuple(
        _pixel_point_to_room(_raw_point(point), bbox, scale)
        for point in raw_polygon
    )
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon = polygon[:-1]
    if not polygon:
        warnings.append(
            "room_polygon_px is missing; generation used the rectangular bbox fallback and must be user-verified"
        )

    reported_area = data.get("room_area_m2")
    calculated_area = polygon_area(polygon) if polygon else room_width * room_length
    if reported_area is not None:
        reported = _finite_positive(reported_area, "room_area_m2")
        difference = abs(calculated_area - reported) / reported
        if difference > 0.10:
            warnings.append(
                f"reported room area ({reported:.2f} m2) differs from usable geometry ({calculated_area:.2f} m2) by {difference:.0%}"
            )

    openings: list[dict[str, Any]] = []
    for kind, key in (("door", "doors"), ("window", "windows")):
        for index, opening in enumerate(data.get(key, [])):
            if not polygon:
                x1, y1, x2, y2 = _box(opening)
                horizontal = (x2 - x1) >= (y2 - y1)
                if horizontal:
                    midpoint = (y1 + y2) / 2
                    boundary_distance = min(
                        abs(midpoint - bbox["y"]),
                        abs(midpoint - (bbox["y"] + bbox["height"])),
                    )
                    thickness = y2 - y1
                else:
                    midpoint = (x1 + x2) / 2
                    boundary_distance = min(
                        abs(midpoint - bbox["x"]),
                        abs(midpoint - (bbox["x"] + bbox["width"])),
                    )
                    thickness = x2 - x1
                if boundary_distance > max(12.0, thickness * 3):
                    warnings.append(
                        f"{kind}-{index} is not near the bbox boundary; room_polygon_px is required to attach it correctly"
                    )
            openings.append(
                _opening_from_detector_box(
                    opening,
                    kind,
                    index,
                    bbox,
                    scale,
                    polygon,
                    room_width,
                    room_length,
                )
            )

    if any("hinge" not in door or "swing" not in door for door in data.get("doors", [])):
        warnings.append(
            "door hinge/swing metadata is missing; a conservative rectangular approach zone was used"
        )

    obstacles = []
    for index, obstacle in enumerate(data.get("fixed_objects", [])):
        x1, y1, x2, y2 = _box(obstacle)
        lower_left = _pixel_point_to_room(Point(x1, y2), bbox, scale)
        obstacles.append({
            "id": str(obstacle.get("id", f"fixed-{index}")),
            "x": lower_left.x,
            "y": lower_left.y,
            "width": (x2 - x1) * scale,
            "length": (y2 - y1) * scale,
            "clearance": float(obstacle.get("clearance_m", 0.0)),
        })

    room_type = str(data.get("room_type", data.get("room_name", "generic")))
    if data.get("furniture"):
        furniture = list(data["furniture"])
        relations = list(data.get("relations", []))
    else:
        furniture, default_relations = _default_program(room_type)
        relations = list(data.get("relations", default_relations))
        if data.get("layouts"):
            warnings.append(
                "existing pixel-based layouts were ignored; candidates were regenerated from the metric furniture catalogue"
            )

    metric_payload: dict[str, Any] = {
        "room": {
            "id": str(data.get("room_id", data.get("id", "detected-room"))),
            "room_type": room_type,
            "width": room_width,
            "length": room_length,
            "polygon": [[point.x, point.y] for point in polygon],
            "openings": openings,
            "obstacles": obstacles,
        },
        "furniture": furniture,
        "relations": relations,
        "layout_count": int(data.get("layout_count", 5)),
        "grid_size": float(data.get("grid_size", 0.25)),
        "attempts_per_layout": int(data.get("attempts_per_layout", 120)),
        "diversity_distance": float(data.get("diversity_distance", 0.5)),
        "seed": data.get("seed"),
    }
    source_transform = {
        "source_units": "px",
        "source_origin": "image_top_left",
        "bbox_px": bbox,
        "m_per_px": scale,
        "scale_source": data.get("scale_source", "unknown"),
    }
    return metric_payload, warnings, source_transform
