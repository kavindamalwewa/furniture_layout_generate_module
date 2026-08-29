from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Wall = Literal["north", "east", "south", "west"]


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Opening:
    """A door or window measured along one wall from its west/south origin."""

    kind: Literal["door", "window"]
    wall: Wall
    offset: float
    width: float
    clearance: float = 0.8

    def __post_init__(self) -> None:
        if self.offset < 0 or self.width <= 0 or self.clearance < 0:
            raise ValueError("Opening offset/width/clearance values are invalid")


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    width: float
    length: float
    room_type: str = "generic"
    openings: tuple[Opening, ...] = ()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.length <= 0:
            raise ValueError("Room dimensions must be positive")
        for opening in self.openings:
            wall_length = self.width if opening.wall in ("north", "south") else self.length
            if opening.offset + opening.width > wall_length + 1e-9:
                raise ValueError(f"Opening extends beyond the {opening.wall} wall")


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
        if self.width <= 0 or self.length <= 0 or self.quantity < 1 or self.clearance < 0:
            raise ValueError("Furniture dimensions, quantity, or clearance are invalid")


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

