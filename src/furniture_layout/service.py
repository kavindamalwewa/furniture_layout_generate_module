from __future__ import annotations

from .adapters import detector_payload_to_metric, normalize_wall
from .geometry import room_area
from .models import (
    FurnitureItem,
    FurnitureRelation,
    Layout,
    Obstacle,
    Opening,
    Point,
    Room,
)
from .optimizer import LayoutOptimizer


def _room_from_dict(data: dict) -> Room:
    polygon = tuple(Point(float(point[0]), float(point[1])) for point in data.get("polygon", []))
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon = polygon[:-1]
    openings = []
    for opening in data.get("openings", []):
        segment = tuple(
            Point(float(point[0]), float(point[1]))
            for point in opening.get("segment", [])
        )
        openings.append(Opening(
            kind=str(opening["kind"]).lower(),
            wall=normalize_wall(opening.get("wall")),
            offset=float(opening.get("offset", 0.0)),
            width=float(opening["width"]),
            clearance=float(opening.get("clearance", 0.8)),
            id=str(opening["id"]) if opening.get("id") is not None else None,
            segment=segment,
        ))
    obstacles = tuple(
        Obstacle(
            id=str(obstacle["id"]),
            x=float(obstacle["x"]),
            y=float(obstacle["y"]),
            width=float(obstacle["width"]),
            length=float(obstacle["length"]),
            clearance=float(obstacle.get("clearance", 0.0)),
        )
        for obstacle in data.get("obstacles", [])
    )
    return Room(
        id=str(data["id"]),
        width=float(data["width"]),
        length=float(data["length"]),
        room_type=str(data.get("room_type", "generic")),
        openings=tuple(openings),
        polygon=polygon,
        obstacles=obstacles,
    )


def _furniture_from_dict(data: dict) -> FurnitureItem:
    return FurnitureItem(
        id=str(data["id"]),
        name=str(data["name"]),
        category=str(data["category"]),
        width=float(data["width"]),
        length=float(data["length"]),
        quantity=int(data.get("quantity", 1)),
        required=bool(data.get("required", True)),
        wall_preferred=bool(data.get("wall_preferred", False)),
        clearance=float(data.get("clearance", 0.15)),
    )


def _relation_from_dict(data: dict) -> FurnitureRelation:
    return FurnitureRelation(
        source_id=str(data.get("source_id", data.get("source"))),
        target_id=str(data.get("target_id", data.get("target"))),
        kind=str(data.get("kind", "near")).lower(),
        min_distance=float(data.get("min_distance", 0.0)),
        max_distance=float(data.get("max_distance", 1.0)),
        weight=float(data.get("weight", 1.0)),
    )


def _room_to_dict(room: Room) -> dict:
    return {
        "id": room.id,
        "room_type": room.room_type,
        "width": round(room.width, 4),
        "length": round(room.length, 4),
        "area_m2": round(room_area(room), 4),
        "polygon": [[round(point.x, 4), round(point.y, 4)] for point in room.polygon],
        "openings": [
            {
                "id": opening.id,
                "kind": opening.kind,
                "wall": opening.wall,
                "offset": round(opening.offset, 4),
                "width": round(opening.width, 4),
                "clearance": round(opening.clearance, 4),
                "segment": [
                    [round(point.x, 4), round(point.y, 4)]
                    for point in opening.segment
                ],
            }
            for opening in room.openings
        ],
        "obstacles": [
            {
                "id": obstacle.id,
                "x": round(obstacle.x, 4),
                "y": round(obstacle.y, 4),
                "width": round(obstacle.width, 4),
                "length": round(obstacle.length, 4),
                "clearance": round(obstacle.clearance, 4),
            }
            for obstacle in room.obstacles
        ],
    }


def _furniture_to_dict(item: FurnitureItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "width": item.width,
        "length": item.length,
        "quantity": item.quantity,
        "required": item.required,
        "wall_preferred": item.wall_preferred,
        "clearance": item.clearance,
    }


def generate_layouts(payload: dict) -> dict:
    """JSON-friendly integration boundary for a web backend."""
    if not isinstance(payload, dict):
        raise TypeError("Layout payload must be a JSON object")
    warnings: list[str] = []
    source_transform = payload.get("source_transform")
    normalized = payload
    if "room" not in payload:
        normalized, warnings, source_transform = detector_payload_to_metric(payload)

    room = _room_from_dict(normalized["room"])
    furniture = [_furniture_from_dict(item) for item in normalized.get("furniture", [])]
    if not furniture:
        raise ValueError("At least one furniture item is required")
    relations = (
        [_relation_from_dict(item) for item in normalized.get("relations", [])]
        if "relations" in normalized
        else None
    )
    optimizer = LayoutOptimizer(
        grid_size=float(normalized.get("grid_size", 0.25)),
        attempts_per_layout=int(normalized.get("attempts_per_layout", 120)),
        diversity_distance=float(normalized.get("diversity_distance", 0.5)),
    )
    layouts = optimizer.generate(
        room,
        furniture,
        count=int(normalized.get("layout_count", 5)),
        seed=normalized.get("seed"),
        relations=relations,
    )
    result = {
        "schema_version": 2,
        "units": "m",
        "coordinate_system": {"origin": "room_south_west", "x_axis": "east", "y_axis": "north"},
        "room_id": room.id,
        "room": _room_to_dict(room),
        "furniture_catalog": [_furniture_to_dict(item) for item in furniture],
        "recommended_layout_id": layouts[0].id,
        "layouts": [layout.to_dict() for layout in layouts],
        "warnings": warnings,
    }
    if source_transform is not None:
        result["source_transform"] = source_transform
    return result


def select_layout(layouts: list[Layout], layout_id: str) -> Layout:
    """Select the recommendation or explicitly override it with another layout."""
    selected = next((layout for layout in layouts if layout.id == layout_id), None)
    if selected is None:
        raise ValueError(f"Unknown layout id: {layout_id}")
    for layout in layouts:
        layout.selected = layout is selected
    return selected
