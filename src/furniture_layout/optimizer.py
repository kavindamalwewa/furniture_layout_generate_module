from __future__ import annotations

import hashlib
import math
import random
from dataclasses import replace

from .geometry import (
    blocks_opening,
    inside_room,
    opening_clearance_rect,
    overlap_area,
    rectangle_distance,
    rectangles_overlap,
    wall_distance,
)
from .models import FurnitureItem, Layout, Placement, Room


class NoValidLayoutError(RuntimeError):
    pass


class LayoutOptimizer:
    """Generate deterministic, scored alternatives for a rectangular room."""

    def __init__(self, grid_size: float = 0.25, attempts_per_layout: int = 120) -> None:
        if grid_size <= 0 or attempts_per_layout < 1:
            raise ValueError("grid_size and attempts_per_layout must be positive")
        self.grid_size = grid_size
        self.attempts_per_layout = attempts_per_layout

    def generate(
        self,
        room: Room,
        furniture: list[FurnitureItem],
        count: int = 5,
        seed: int | None = None,
    ) -> list[Layout]:
        if count < 1:
            raise ValueError("count must be at least 1")
        expanded = [(item, instance) for item in furniture for instance in range(1, item.quantity + 1)]
        expanded.sort(key=lambda pair: pair[0].width * pair[0].length, reverse=True)
        if seed is None:
            signature = f"{room.id}:{room.width}:{room.length}:" + ",".join(i.id for i, _ in expanded)
            seed = int(hashlib.sha256(signature.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        unique: dict[tuple, Layout] = {}

        for _ in range(max(count * self.attempts_per_layout, self.attempts_per_layout)):
            placements: list[Placement] = []
            valid = True
            for item, instance in expanded:
                candidates = self._candidates(room, item, instance, rng)
                candidate = next((p for p in candidates if self._valid(p, item, room, placements)), None)
                if candidate is None:
                    if item.required:
                        valid = False
                        break
                    continue
                placements.append(candidate)
            if not valid:
                continue
            key = tuple(sorted((p.furniture_id, p.instance, round(p.x, 3), round(p.y, 3), p.rotation) for p in placements))
            if key not in unique:
                breakdown = self._score(room, expanded, placements)
                unique[key] = Layout("", room.id, placements, round(sum(breakdown.values()), 2), breakdown)
            if len(unique) >= count * 4:
                break

        if not unique:
            raise NoValidLayoutError("No collision-free layout fits the room and opening constraints")

        layouts = sorted(unique.values(), key=lambda layout: layout.score, reverse=True)[:count]
        for index, layout in enumerate(layouts, start=1):
            layout.id = f"{room.id}-layout-{index}"
            layout.recommended = index == 1
        return layouts

    def _candidates(
        self, room: Room, item: FurnitureItem, instance: int, rng: random.Random
    ) -> list[Placement]:
        result: list[Placement] = []
        rotations = [(item.width, item.length, 0)]
        if not math.isclose(item.width, item.length):
            rotations.append((item.length, item.width, 90))
        for width, length, rotation in rotations:
            max_x = room.width - width
            max_y = room.length - length
            if max_x < 0 or max_y < 0:
                continue
            xs = self._axis_values(max_x)
            ys = self._axis_values(max_y)
            for x in xs:
                for y in ys:
                    result.append(Placement(item.id, instance, x, y, width, length, rotation))
        rng.shuffle(result)
        if item.wall_preferred:
            result.sort(key=lambda p: wall_distance(p, room) + rng.random() * 0.05)
        return result

    def _axis_values(self, maximum: float) -> list[float]:
        steps = int(maximum / self.grid_size)
        values = [round(i * self.grid_size, 6) for i in range(steps + 1)]
        if not values or not math.isclose(values[-1], maximum):
            values.append(round(maximum, 6))
        return values

    @staticmethod
    def _valid(p: Placement, item: FurnitureItem, room: Room, placed: list[Placement]) -> bool:
        return (
            inside_room(p, room)
            and not any(blocks_opening(p, opening, room) for opening in room.openings)
            and not any(rectangles_overlap(p, other, item.clearance) for other in placed)
        )

    @staticmethod
    def _score(
        room: Room,
        expanded: list[tuple[FurnitureItem, int]],
        placements: list[Placement],
    ) -> dict[str, float]:
        room_area = room.width * room.length
        used_area = sum(p.width * p.length for p in placements)
        density = used_area / room_area
        target_density = 0.35
        space_use = max(0.0, 10.0 * (1.0 - abs(density - target_density) / target_density))

        item_by_id = {item.id: item for item, _ in expanded}
        wall_items = [p for p in placements if item_by_id[p.furniture_id].wall_preferred]
        wall_alignment = 10.0 if not wall_items else 10.0 * sum(
            max(0.0, 1.0 - wall_distance(p, room) / 0.75) for p in wall_items
        ) / len(wall_items)

        if len(placements) < 2:
            furniture_spacing = 15.0
        else:
            gaps = [
                rectangle_distance(a, b)
                for index, a in enumerate(placements)
                for b in placements[index + 1 :]
            ]
            furniture_spacing = 15.0 * sum(min(1.0, gap / 0.75) for gap in gaps) / len(gaps)

        central_zone = Placement(
            "central-zone", 0,
            room.width * 0.3, room.length * 0.3,
            room.width * 0.4, room.length * 0.4, 0,
        )
        central_area = central_zone.width * central_zone.length
        blocked_center = min(central_area, sum(overlap_area(p, central_zone) for p in placements))
        central_open_space = 20.0 * (1.0 - blocked_center / central_area)

        doors = [opening for opening in room.openings if opening.kind == "door"]
        if not doors or not placements:
            door_clearance = 20.0
        else:
            door_zones = [
                Placement("door-zone", i, *opening_clearance_rect(opening, room), 0)
                for i, opening in enumerate(doors)
            ]
            nearest_gap = min(rectangle_distance(p, zone) for p in placements for zone in door_zones)
            door_clearance = 20.0 * min(1.0, nearest_gap / 1.0)

        windows = [opening for opening in room.openings if opening.kind == "window"]
        light_items = [
            p for p in placements
            if item_by_id[p.furniture_id].category in {"table", "desk"}
        ]
        if not windows or not light_items:
            window_proximity = 15.0
        else:
            window_zones = [
                Placement("window-zone", i, *opening_clearance_rect(opening, room), 0)
                for i, opening in enumerate(windows)
            ]
            distances = [min(rectangle_distance(p, zone) for zone in window_zones) for p in light_items]
            window_proximity = 15.0 * sum(max(0.0, 1.0 - distance / 2.0) for distance in distances) / len(distances)

        completeness = 10.0 * len(placements) / max(len(expanded), 1)
        return {
            "door_clearance": round(door_clearance, 2),
            "central_open_space": round(central_open_space, 2),
            "window_proximity": round(window_proximity, 2),
            "furniture_spacing": round(furniture_spacing, 2),
            "space_utilization": round(space_use, 2),
            "wall_alignment": round(wall_alignment, 2),
            "completeness": round(completeness, 2),
        }
