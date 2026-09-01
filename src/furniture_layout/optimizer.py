from __future__ import annotations

import hashlib
import math
import random

from .geometry import (
    blocks_obstacle,
    blocks_opening,
    inside_room,
    opening_clearance_rect,
    overlap_area,
    polygon_rectangle_intersection_area,
    rectangle_distance,
    rectangles_overlap,
    room_area,
    room_polygon,
    wall_distance,
)
from .models import FurnitureItem, FurnitureRelation, Layout, Placement, Room


class NoValidLayoutError(RuntimeError):
    pass


class LayoutOptimizer:
    """Generate deterministic, scored alternatives from metric room geometry."""

    def __init__(
        self,
        grid_size: float = 0.25,
        attempts_per_layout: int = 120,
        diversity_distance: float = 0.5,
    ) -> None:
        if grid_size <= 0 or attempts_per_layout < 1 or diversity_distance < 0:
            raise ValueError("grid_size/attempts_per_layout must be positive and diversity_distance non-negative")
        self.grid_size = grid_size
        self.attempts_per_layout = attempts_per_layout
        self.diversity_distance = diversity_distance

    def generate(
        self,
        room: Room,
        furniture: list[FurnitureItem],
        count: int = 5,
        seed: int | None = None,
        relations: list[FurnitureRelation] | tuple[FurnitureRelation, ...] | None = None,
    ) -> list[Layout]:
        if count < 1:
            raise ValueError("count must be at least 1")
        expanded = [(item, instance) for item in furniture for instance in range(1, item.quantity + 1)]
        expanded.sort(key=lambda pair: pair[0].width * pair[0].length, reverse=True)
        item_by_id = {item.id: item for item in furniture}
        relation_list = tuple(self._infer_relations(furniture) if relations is None else relations)
        unknown_ids = {
            furniture_id
            for relation in relation_list
            for furniture_id in (relation.source_id, relation.target_id)
            if furniture_id not in item_by_id
        }
        if unknown_ids:
            raise ValueError(f"Furniture relations reference unknown IDs: {', '.join(sorted(unknown_ids))}")

        if seed is None:
            signature = f"{room.id}:{room.width}:{room.length}:" + ",".join(i.id for i, _ in expanded)
            seed = int(hashlib.sha256(signature.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        unique: dict[tuple, Layout] = {}

        maximum_attempts = max(count * self.attempts_per_layout, self.attempts_per_layout)
        for _ in range(maximum_attempts):
            placements: list[Placement] = []
            valid = True
            for item, instance in expanded:
                candidates = self._candidates(room, item, instance, rng, placements, relation_list)
                candidate = next(
                    (
                        placement
                        for placement in candidates
                        if self._valid(placement, item, room, placements, item_by_id)
                    ),
                    None,
                )
                if candidate is None:
                    if item.required:
                        valid = False
                        break
                    continue
                placements.append(candidate)
            if not valid:
                continue
            key = self._layout_key(placements)
            if key not in unique:
                breakdown = self._score(room, expanded, placements, relation_list)
                unique[key] = Layout("", room.id, placements, round(sum(breakdown.values()), 2), breakdown)
            if len(unique) >= count * 10:
                break

        if not unique:
            raise NoValidLayoutError("No collision-free layout fits the room, polygon, obstacles, and opening constraints")

        ranked = sorted(unique.values(), key=lambda layout: layout.score, reverse=True)
        layouts = self._select_diverse(ranked, count)
        for index, layout in enumerate(layouts, start=1):
            layout.id = f"{room.id}-layout-{index}"
            layout.recommended = index == 1
        return layouts

    def _candidates(
        self,
        room: Room,
        item: FurnitureItem,
        instance: int,
        rng: random.Random,
        placed: list[Placement],
        relations: tuple[FurnitureRelation, ...],
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
            for x in self._axis_values(max_x):
                for y in self._axis_values(max_y):
                    result.append(Placement(item.id, instance, x, y, width, length, rotation))

        jitter = {id(candidate): rng.random() for candidate in result}
        result.sort(
            key=lambda candidate: (
                self._candidate_relation_cost(candidate, placed, relations),
                wall_distance(candidate, room) if item.wall_preferred else 0.0,
                jitter[id(candidate)],
            )
        )
        return result

    def _axis_values(self, maximum: float) -> list[float]:
        steps = int(maximum / self.grid_size)
        values = [round(i * self.grid_size, 6) for i in range(steps + 1)]
        if not values or not math.isclose(values[-1], maximum):
            values.append(round(maximum, 6))
        return values

    @staticmethod
    def _valid(
        p: Placement,
        item: FurnitureItem,
        room: Room,
        placed: list[Placement],
        item_by_id: dict[str, FurnitureItem] | None = None,
    ) -> bool:
        item_by_id = item_by_id or {}
        return (
            inside_room(p, room)
            and not any(blocks_opening(p, opening, room) for opening in room.openings)
            and not any(blocks_obstacle(p, obstacle, item.clearance) for obstacle in room.obstacles)
            and not any(
                rectangles_overlap(
                    p,
                    other,
                    max(item.clearance, item_by_id.get(other.furniture_id, item).clearance),
                )
                for other in placed
            )
        )

    @staticmethod
    def _relation_quality(
        relation: FurnitureRelation, source: Placement, target: Placement
    ) -> float:
        distance = rectangle_distance(source, target)
        if relation.min_distance <= distance <= relation.max_distance:
            distance_score = 1.0
        elif distance < relation.min_distance:
            tolerance = max(relation.min_distance, 0.25)
            distance_score = max(0.0, 1.0 - (relation.min_distance - distance) / tolerance)
        else:
            tolerance = max(relation.max_distance, 0.75)
            distance_score = max(0.0, 1.0 - (distance - relation.max_distance) / tolerance)

        if relation.kind == "near":
            return distance_score

        delta_x = abs(source.center.x - target.center.x)
        delta_y = abs(source.center.y - target.center.y)
        separation = max(delta_x, delta_y)
        alignment_error = min(delta_x, delta_y)
        alignment_scale = max(0.5, min(source.width + target.width, source.length + target.length) / 2)
        alignment_score = max(0.0, 1.0 - alignment_error / alignment_scale)
        separation_score = min(1.0, separation / max(relation.min_distance, 0.5))
        return 0.55 * distance_score + 0.35 * alignment_score + 0.10 * separation_score

    @classmethod
    def _candidate_relation_cost(
        cls,
        candidate: Placement,
        placed: list[Placement],
        relations: tuple[FurnitureRelation, ...],
    ) -> float:
        costs: list[tuple[float, float]] = []
        for relation in relations:
            if relation.source_id == candidate.furniture_id:
                others = [p for p in placed if p.furniture_id == relation.target_id]
                costs.extend((1.0 - cls._relation_quality(relation, candidate, other), relation.weight) for other in others)
            elif relation.target_id == candidate.furniture_id:
                others = [p for p in placed if p.furniture_id == relation.source_id]
                costs.extend((1.0 - cls._relation_quality(relation, other, candidate), relation.weight) for other in others)
        if not costs:
            return 0.0
        return sum(cost * weight for cost, weight in costs) / sum(weight for _, weight in costs)

    @classmethod
    def _functional_score(
        cls,
        placements: list[Placement],
        relations: tuple[FurnitureRelation, ...],
    ) -> float:
        if not relations:
            return 15.0
        weighted_scores: list[tuple[float, float]] = []
        for relation in relations:
            sources = [p for p in placements if p.furniture_id == relation.source_id]
            targets = [p for p in placements if p.furniture_id == relation.target_id]
            if not sources or not targets:
                continue
            for source in sources:
                best = max(cls._relation_quality(relation, source, target) for target in targets)
                weighted_scores.append((best, relation.weight))
        if not weighted_scores:
            return 15.0
        quality = sum(score * weight for score, weight in weighted_scores) / sum(
            weight for _, weight in weighted_scores
        )
        return 15.0 * quality

    @classmethod
    def _score(
        cls,
        room: Room,
        expanded: list[tuple[FurnitureItem, int]],
        placements: list[Placement],
        relations: tuple[FurnitureRelation, ...] = (),
    ) -> dict[str, float]:
        usable_area = room_area(room)
        used_area = sum(p.width * p.length for p in placements)
        density = used_area / usable_area
        target_density = 0.35
        space_use = max(0.0, 10.0 * (1.0 - abs(density - target_density) / target_density))

        item_by_id = {item.id: item for item, _ in expanded}
        wall_items = [p for p in placements if item_by_id[p.furniture_id].wall_preferred]
        wall_alignment = 10.0 if not wall_items else 10.0 * sum(
            max(0.0, 1.0 - wall_distance(p, room) / 0.75) for p in wall_items
        ) / len(wall_items)

        related_pairs = {
            frozenset((relation.source_id, relation.target_id))
            for relation in relations
        }
        gaps = [
            rectangle_distance(a, b)
            for index, a in enumerate(placements)
            for b in placements[index + 1 :]
            if frozenset((a.furniture_id, b.furniture_id)) not in related_pairs
        ]
        furniture_spacing = 10.0 if not gaps else 10.0 * sum(
            min(1.0, gap / 0.75) for gap in gaps
        ) / len(gaps)

        central_zone = Placement(
            "central-zone",
            0,
            room.width * 0.3,
            room.length * 0.3,
            room.width * 0.4,
            room.length * 0.4,
            0,
        )
        central_area = polygon_rectangle_intersection_area(
            room_polygon(room),
            central_zone.x,
            central_zone.y,
            central_zone.width,
            central_zone.length,
        )
        blocked_center = min(central_area, sum(overlap_area(p, central_zone) for p in placements))
        central_open_space = 15.0 if central_area <= 1e-9 else 15.0 * (1.0 - blocked_center / central_area)

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
            window_proximity = 10.0
        else:
            window_zones = [
                Placement("window-zone", i, *opening_clearance_rect(opening, room), 0)
                for i, opening in enumerate(windows)
            ]
            distances = [min(rectangle_distance(p, zone) for zone in window_zones) for p in light_items]
            window_proximity = 10.0 * sum(
                max(0.0, 1.0 - distance / 2.0) for distance in distances
            ) / len(distances)

        functional_relationships = cls._functional_score(placements, relations)
        completeness = 10.0 * len(placements) / max(len(expanded), 1)
        return {
            "door_clearance": round(door_clearance, 2),
            "central_open_space": round(central_open_space, 2),
            "window_proximity": round(window_proximity, 2),
            "furniture_spacing": round(furniture_spacing, 2),
            "functional_relationships": round(functional_relationships, 2),
            "space_utilization": round(space_use, 2),
            "wall_alignment": round(wall_alignment, 2),
            "completeness": round(completeness, 2),
        }

    @staticmethod
    def _layout_key(placements: list[Placement]) -> tuple:
        """Canonicalize physical geometry, ignoring interchangeable instance labels."""
        return tuple(sorted(
            (
                placement.furniture_id,
                round(placement.x, 3),
                round(placement.y, 3),
                round(placement.width, 3),
                round(placement.length, 3),
            )
            for placement in placements
        ))

    @staticmethod
    def _layout_distance(first: Layout, second: Layout) -> float:
        first_items = sorted(
            (p.furniture_id, p.center.x, p.center.y, p.width, p.length) for p in first.placements
        )
        second_items = sorted(
            (p.furniture_id, p.center.x, p.center.y, p.width, p.length) for p in second.placements
        )
        if len(first_items) != len(second_items) or [x[0] for x in first_items] != [x[0] for x in second_items]:
            return math.inf
        distances = [
            math.hypot(a[1] - b[1], a[2] - b[2]) + (0.25 if a[3:] != b[3:] else 0.0)
            for a, b in zip(first_items, second_items)
        ]
        return sum(distances) / max(len(distances), 1)

    def _select_diverse(self, ranked: list[Layout], count: int) -> list[Layout]:
        selected: list[Layout] = []
        skipped: list[Layout] = []
        for layout in ranked:
            if not selected or all(
                self._layout_distance(layout, existing) >= self.diversity_distance
                for existing in selected
            ):
                selected.append(layout)
                if len(selected) == count:
                    return selected
            else:
                skipped.append(layout)
        selected.extend(skipped[: max(0, count - len(selected))])
        return sorted(selected[:count], key=lambda layout: layout.score, reverse=True)

    @staticmethod
    def _infer_relations(furniture: list[FurnitureItem]) -> list[FurnitureRelation]:
        searchable = {
            item.id: f"{item.id} {item.name} {item.category}".lower().replace("_", "-")
            for item in furniture
        }

        def first_matching(*tokens: str) -> str | None:
            return next(
                (item_id for item_id, text in searchable.items() if any(token in text for token in tokens)),
                None,
            )

        relations: list[FurnitureRelation] = []
        coffee = first_matching("coffee")
        sofa = first_matching("sofa", "couch")
        if coffee and sofa:
            relations.append(FurnitureRelation(coffee, sofa, "near", 0.3, 0.9, 1.0))
        nightstand = first_matching("nightstand", "bedside")
        bed = first_matching(" bed", "bed ", "bed-")
        if nightstand and bed:
            relations.append(FurnitureRelation(nightstand, bed, "near", 0.05, 0.55, 1.0))
        television = first_matching("tv", "television")
        if television and sofa:
            relations.append(FurnitureRelation(television, sofa, "opposite", 1.2, 3.5, 1.2))
        return relations
