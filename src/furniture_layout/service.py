from __future__ import annotations

from .models import FurnitureItem, Layout, Opening, Room
from .optimizer import LayoutOptimizer


def _room_from_dict(data: dict) -> Room:
    return Room(
        id=str(data["id"]),
        width=float(data["width"]),
        length=float(data["length"]),
        room_type=str(data.get("room_type", "generic")),
        openings=tuple(Opening(**opening) for opening in data.get("openings", [])),
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


def generate_layouts(payload: dict) -> dict:
    """JSON-friendly integration boundary for a web backend."""
    room = _room_from_dict(payload["room"])
    furniture = [_furniture_from_dict(item) for item in payload.get("furniture", [])]
    optimizer = LayoutOptimizer(
        grid_size=float(payload.get("grid_size", 0.25)),
        attempts_per_layout=int(payload.get("attempts_per_layout", 120)),
    )
    layouts = optimizer.generate(
        room,
        furniture,
        count=int(payload.get("layout_count", 5)),
        seed=payload.get("seed"),
    )
    return {
        "room_id": room.id,
        "recommended_layout_id": layouts[0].id,
        "layouts": [layout.to_dict() for layout in layouts],
    }


def select_layout(layouts: list[Layout], layout_id: str) -> Layout:
    """Select the recommendation or explicitly override it with another layout."""
    selected = next((layout for layout in layouts if layout.id == layout_id), None)
    if selected is None:
        raise ValueError(f"Unknown layout id: {layout_id}")
    for layout in layouts:
        layout.selected = layout is selected
    return selected

