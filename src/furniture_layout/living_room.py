"""Room-type-specific layout generation for living rooms.

The generic sampler in :mod:`polygon_engine` treats every room the same, which
makes the rules for different room types collide.  This module owns the living
room only: it *constructs* a layout in a fixed order instead of sampling it.

    1. Place the sofa flush against the longest wall, in the widest run of it
       left clear by the door approaches.
    2. Place the TV unit on a window-free wall, preferring one that faces the
       sofa (a perpendicular clear wall only when nothing faces it).
    3. Place the coffee table on the line between the sofa and the TV, trying a
       few offsets and orientations; leave it out if none stays clear.
    4. Keep every required piece out of the door approach zones and the walls.

A handful of deterministic variants (which wall, how far along it, coffee-table
orientation) produce the requested number of alternatives, which are then scored
and validated with the shared helpers so downstream consumers see the same
contract as the generic engine.
"""

from __future__ import annotations

import math
from typing import Any

from .polygon_engine import (
    EPS,
    _layout_score_components,
    _opening_keep_clear,
    _point_segment_distance,
    _semantic_verdict,
    footprint_inside,
    point_in_polygon,
    rectangle_ring,
    rings_overlap,
    validate_layout,
)
from .project import ProjectValidationError, polygon_bbox, validate_ring


LIVING_ROOM_TYPES = {
    "living-room", "living_room", "livingroom", "living",
    "lounge", "family-room", "sitting-room", "drawing-room",
}

# Real-world circulation gaps, in metres.
WALL_GAP = 0.05           # sofa / TV back-to-wall gap
FACING_DOT = -0.35        # normals this opposed count as two walls "facing" each other


# --------------------------------------------------------------------------- #
# Room analysis
# --------------------------------------------------------------------------- #
def infer_room_type(furniture: list[dict[str, Any]]) -> str:
    """Classify a request as a living room only when a sofa *and* a TV are asked for."""
    ids = " ".join(str(spec.get("id", "")).lower() for spec in furniture)
    has_sofa = "sofa" in ids or "couch" in ids
    has_tv = "tv" in ids or "television" in ids
    return "living-room" if has_sofa and has_tv else ""


def _catalog_lookup(furniture: list[dict[str, Any]], *keywords: str) -> dict[str, Any] | None:
    for spec in furniture:
        item_id = str(spec.get("id", "")).lower().replace("_", "-")
        if any(keyword in item_id for keyword in keywords):
            return spec
    return None


def _polygon_walls(polygon: dict[str, Any]) -> list[dict[str, Any]]:
    """Outer-ring edges with a unit direction and an inward-pointing unit normal."""
    outer = polygon["outer"]
    box = polygon_bbox(polygon)
    probe = max(box["width"], box["height"]) * 1e-4 + EPS
    walls: list[dict[str, Any]] = []
    for index, (a, b) in enumerate(zip(outer, outer[1:] + outer[:1])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < EPS:
            continue
        direction = (dx / length, dy / length)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        left = (-direction[1], direction[0])
        inward = left if point_in_polygon((mid[0] + left[0] * probe, mid[1] + left[1] * probe), polygon) \
            else (direction[1], -direction[0])
        walls.append({"index": index, "a": a, "b": b, "mid": mid,
                      "direction": direction, "normal": inward, "length": length})
    return walls


def _opening_walls(openings: list[dict[str, Any]], walls: list[dict[str, Any]], kind: str) -> set[int]:
    marked: set[int] = set()
    for opening in openings:
        if opening.get("kind") != kind or not opening.get("span"):
            continue
        span = opening["span"]
        point = ((span[0][0] + span[1][0]) / 2, (span[0][1] + span[1][1]) / 2)
        nearest = min(walls, key=lambda wall: _point_segment_distance(point, wall["a"], wall["b"]))
        marked.add(nearest["index"])
    return marked


def _facing(a: dict[str, Any], b: dict[str, Any]) -> float:
    return a["normal"][0] * b["normal"][0] + a["normal"][1] * b["normal"][1]


def _along_shift(wall: dict[str, Any], anchor: tuple[float, float]) -> float:
    return ((anchor[0] - wall["mid"][0]) * wall["direction"][0]
            + (anchor[1] - wall["mid"][1]) * wall["direction"][1])


def _blocked_intervals(wall: dict[str, Any], openings: list[dict[str, Any]],
                       units_per_meter: float, margin_m: float = 0.10) -> list[tuple[float, float]]:
    """Offsets along the wall (0..length) that an opening keep-clear zone rules out."""
    blocked: list[tuple[float, float]] = []
    half = wall["length"] / 2
    for opening in openings:
        span = opening.get("span")
        if not span:
            continue
        mid = ((span[0][0] + span[1][0]) / 2, (span[0][1] + span[1][1]) / 2)
        if _point_segment_distance(mid, wall["a"], wall["b"]) > 0.25 * units_per_meter:
            continue
        offsets = sorted(_along_shift(wall, tuple(point)) + half for point in span)
        margin = margin_m * units_per_meter
        blocked.append((max(0.0, offsets[0] - margin), min(wall["length"], offsets[1] + margin)))
    return blocked


def _free_slots(length: float, blocked: list[tuple[float, float]]) -> list[tuple[float, float]]:
    slots: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in sorted(blocked):
        if start > cursor:
            slots.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < length:
        slots.append((cursor, length))
    return slots


def _wall_candidates(walls: list[dict[str, Any]], window_walls: set[int], door_walls: set[int],
                     sofa_len_px: float, tv_len_px: float) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every ``(sofa_wall, tv_wall)`` pairing, best first.

    A wall containing a door is a hard exclusion for the sofa. Other living-room
    rules are preferences used for ordering. Priority, most important first:

    1. the sofa wall is long enough for the sofa;
    2. the TV wall is long enough for the TV;
    3. the sofa wall is (within 10 % of) the longest wall;
    4. the TV wall carries no window;
    5. the TV wall faces the sofa;
    6. the TV wall carries no door;
    """
    longest = max(wall["length"] for wall in walls)
    ranked: list[tuple[tuple, dict[str, Any], dict[str, Any]]] = []
    for sofa_wall in walls:
        if sofa_wall["index"] in door_walls:
            continue
        for tv_wall in walls:
            if tv_wall["index"] == sofa_wall["index"]:
                continue
            facing = _facing(tv_wall, sofa_wall)
            key = (
                0 if sofa_wall["length"] + EPS >= sofa_len_px else 1,
                0 if tv_wall["length"] + EPS >= tv_len_px else 1,
                0 if sofa_wall["length"] >= 0.9 * longest else 1,
                0 if tv_wall["index"] not in window_walls else 1,
                0 if facing <= FACING_DOT else 1,
                0 if tv_wall["index"] not in door_walls else 1,
                round(facing, 4),
                -round(sofa_wall["length"], 3),
            )
            ranked.append((key, sofa_wall, tv_wall))
    ranked.sort(key=lambda item: item[0])
    return [(sofa_wall, tv_wall) for _key, sofa_wall, tv_wall in ranked]


def _sofa_slot(wall: dict[str, Any], doors: list[dict[str, Any]], sofa_len_px: float,
               units_per_meter: float) -> tuple[tuple[float, float] | None, bool]:
    """``(slot, clear_of_doors)``: the along-wall run to seat the sofa in.

    The widest door-clear run wins; if none is wide enough the whole wall is
    returned (the sofa may then clip a door approach, which is penalised, not
    rejected).  ``slot`` is ``None`` only when the wall is shorter than the sofa.
    """
    fitting = [slot for slot in _free_slots(wall["length"], _blocked_intervals(wall, doors, units_per_meter))
               if slot[1] - slot[0] >= sofa_len_px - EPS]
    if fitting:
        return max(fitting, key=lambda slot: slot[1] - slot[0]), True
    if wall["length"] + EPS >= sofa_len_px:
        return (0.0, wall["length"]), False
    return None, False


# --------------------------------------------------------------------------- #
# Placement primitives (metres in, pixel geometry out)
# --------------------------------------------------------------------------- #
def _place_against_wall(wall: dict[str, Any], width_m: float, depth_m: float,
                        units_per_meter: float, shift: float, gap_m: float) -> dict[str, Any]:
    width = width_m * units_per_meter
    depth = depth_m * units_per_meter
    theta = math.degrees(math.atan2(-wall["normal"][0], wall["normal"][1]))
    radians = math.radians(theta)
    along = (math.cos(radians), math.sin(radians))
    gap = gap_m * units_per_meter
    back = (wall["mid"][0] + shift * wall["direction"][0] + gap * wall["normal"][0],
            wall["mid"][1] + shift * wall["direction"][1] + gap * wall["normal"][1])
    return {
        "x": back[0] - width / 2 * along[0],
        "y": back[1] - width / 2 * along[1],
        "width": width, "depth": depth, "rotation": round(theta) % 360,
        "front_center": (back[0] + depth * wall["normal"][0], back[1] + depth * wall["normal"][1]),
    }


def _place_centered(center: tuple[float, float], width_m: float, depth_m: float,
                    units_per_meter: float, theta: float) -> dict[str, Any]:
    width = width_m * units_per_meter
    depth = depth_m * units_per_meter
    radians = math.radians(theta)
    along = (math.cos(radians), math.sin(radians))
    perp = (-math.sin(radians), math.cos(radians))
    return {
        "x": center[0] - width / 2 * along[0] - depth / 2 * perp[0],
        "y": center[1] - width / 2 * along[1] - depth / 2 * perp[1],
        "width": width, "depth": depth, "rotation": round(theta) % 360,
    }


def _placement(spec: dict[str, Any], geom: dict[str, Any], width_m: float, depth_m: float,
               box: dict[str, float], meters_per_unit: float) -> dict[str, Any]:
    return {
        "id": f"{spec['id']}-1", "catalogId": spec["id"],
        "x": geom["x"], "y": geom["y"], "width": geom["width"], "depth": geom["depth"],
        "widthMeters": round(width_m, 4), "depthMeters": round(depth_m, 4),
        "heightMeters": spec.get("height"),
        "rotation": geom["rotation"], "frontDirection": geom["rotation"],
        "locked": False, "required": bool(spec.get("required", True)),
        "wallAttached": bool(spec.get("wallAttached", False)),
        "supportWallId": spec.get("supportWallId"), "supportKind": spec.get("supportKind"),
        "accessPoint": spec.get("accessPoint"),
        "positionMeters": {"x": (geom["x"] - box["x"]) * meters_per_unit,
                           "y": (geom["y"] - box["y"]) * meters_per_unit},
    }


def _coffee_candidates(sofa_geom: dict[str, Any], tv_geom: dict[str, Any], params: dict[str, Any],
                       width_m: float, depth_m: float, units_per_meter: float):
    """Yield coffee-table geoms on the sofa-to-TV line at a few offsets and orientations."""
    sofa_front, tv_front = sofa_geom["front_center"], tv_geom["front_center"]
    axis = (tv_front[0] - sofa_front[0], tv_front[1] - sofa_front[1])
    nudge = float(params["coffee_bias"])
    turned = (sofa_geom["rotation"] + 90) % 360
    orientations = [sofa_geom["rotation"], turned] if params["coffee_along_sofa"] else [turned, sofa_geom["rotation"]]
    for fraction in (0.5 + nudge, 0.45, 0.4, 0.55, 0.6, 0.35):
        center = (sofa_front[0] + axis[0] * fraction, sofa_front[1] + axis[1] * fraction)
        for theta in orientations:
            yield _place_centered(center, width_m, depth_m, units_per_meter, theta)


# --------------------------------------------------------------------------- #
# Constructive core
# --------------------------------------------------------------------------- #
# Tried in order for each wall pairing; the seed rotates the order.
_TWEAKS = [
    {"sofa_shift": sofa_shift, "tv_shift": tv_shift, "coffee_bias": coffee_bias,
     "coffee_along_sofa": along}
    for sofa_shift in (0.0, .3, -.3, .6, -.6)
    for tv_shift in (0.0, .35, -.35, .7, -.7)
    for coffee_bias in (0.0, .06, -.06, .12, -.12)
    for along in (True, False)
]

# Score deductions, in points, when a living-room preference cannot be honoured.
# The rule still shapes the ranking; it just no longer blocks a result.
_PENALTY = {
    "sofa_not_longest": 5.0,     # sofa could not take the longest wall
    "tv_window_wall": 20.0,      # strongly prefer a clear side wall over covering a window
    "tv_not_facing": 9.0,        # the TV wall does not face the sofa
    "near_door": 12.0,           # a piece sits in a door approach
    "coffee_missing": 6.0,       # no clear spot for the coffee table
    "overhang": 15.0,            # a piece is larger than its wall / the room
}


def _long_short(spec: dict[str, Any]) -> tuple[float, float]:
    """(along-wall, into-room) metres — the long side always runs along the wall."""
    a, b = float(spec["width"]), float(spec["depth"])
    return max(a, b), min(a, b)


def _build(request: dict[str, Any], sofa_wall: dict[str, Any], tv_wall: dict[str, Any],
           tweak: dict[str, Any], window_walls: set[int], door_walls: set[int], longest_len: float,
           units_per_meter: float, meters_per_unit: float, box: dict[str, float]) -> dict[str, Any] | None:
    """Build one layout for a wall pairing. Preferences that cannot be met become
    score penalties, so this returns ``None`` only when the sofa or the TV cannot
    be seated inside the room on the given walls at all."""
    furniture = request.get("furniture", [])
    sofa_spec = _catalog_lookup(furniture, "sofa", "couch")
    tv_spec = _catalog_lookup(furniture, "tv", "television")
    coffee_spec = _catalog_lookup(furniture, "coffee")

    doors = [opening for opening in request.get("openings", []) if opening.get("kind") == "door"]
    door_zones = [zone for zone in (_opening_keep_clear(opening, units_per_meter) for opening in doors) if zone]

    def ring_of(geom: dict[str, Any]) -> list[list[float]]:
        return rectangle_ring(geom["x"], geom["y"], geom["width"], geom["depth"], geom["rotation"])

    def inside(ring: list[list[float]]) -> bool:
        return footprint_inside(ring, request["polygon"])

    def hits_door(ring: list[list[float]]) -> bool:
        return any(rings_overlap(ring, zone) for zone in door_zones)

    sofa_along, sofa_into = _long_short(sofa_spec)
    tv_along, tv_into = _long_short(tv_spec)
    penalties: dict[str, float] = {}
    assumptions: list[str] = []

    # 1. Sofa flush to its wall, in the widest run clear of door approaches.
    slot, door_clear = _sofa_slot(sofa_wall, doors, sofa_along * units_per_meter, units_per_meter)
    if slot is None:
        return None
    slot_center = (slot[0] + slot[1]) / 2 - sofa_wall["length"] / 2
    slack = max(0.0, (slot[1] - slot[0] - sofa_along * units_per_meter) / 2)
    sofa_geom = _place_against_wall(sofa_wall, sofa_along, sofa_into, units_per_meter,
                                    slot_center + tweak["sofa_shift"] * slack, WALL_GAP)
    sofa_ring = ring_of(sofa_geom)
    if not inside(sofa_ring):
        return None
    if hits_door(sofa_ring):
        penalties["near_door"] = _PENALTY["near_door"]
        assumptions.append("Sofa clips a door approach (no clear wall run was long enough)")
    if sofa_wall["length"] >= 0.9 * longest_len:
        assumptions.append("Sofa placed against the longest wall")
    else:
        penalties["sofa_not_longest"] = _PENALTY["sofa_not_longest"]
        assumptions.append("Sofa placed on a shorter wall (the longest wall could not take it)")

    # 2. TV flush to its wall, centred on the sofa's line of sight.
    tv_free = tv_wall["length"] - tv_along * units_per_meter
    tv_shift = _along_shift(tv_wall, sofa_geom["front_center"])
    tv_shift += tweak["tv_shift"] * max(tv_free, 0.0) / 2
    tv_shift = max(-tv_free / 2, min(tv_free / 2, tv_shift)) if tv_free > 0 else 0.0
    tv_geom = _place_against_wall(tv_wall, tv_along, tv_into, units_per_meter, tv_shift, WALL_GAP)
    tv_ring = ring_of(tv_geom)
    if not inside(tv_ring):
        return None
    # Door access is a hard safety constraint for the TV. A door wall remains
    # usable only when the unit can sit completely outside its approach zone.
    if hits_door(tv_ring):
        return None
    facing = _facing(tv_wall, sofa_wall)
    if tv_wall["index"] in window_walls:
        penalties["tv_window_wall"] = _PENALTY["tv_window_wall"]
        assumptions.append("TV unit is on a wall with a window (no window-free wall was available)")
    elif facing <= FACING_DOT:
        assumptions.append("TV unit placed on a window-free wall facing the sofa")
    else:
        assumptions.append("TV unit placed on a window-free wall")
    if facing > FACING_DOT:
        penalties["tv_not_facing"] = _PENALTY["tv_not_facing"]
        assumptions.append("TV unit does not directly face the sofa (no facing wall was available)")
    placements = [_placement(sofa_spec, sofa_geom, sofa_along, sofa_into, box, meters_per_unit),
                  _placement(tv_spec, tv_geom, tv_along, tv_into, box, meters_per_unit)]
    if rings_overlap(sofa_ring, tv_ring):
        return None
    complete = True

    # 3. Coffee table on the sofa-to-TV line; a few offsets and both orientations
    #    are tried, and it is left out (penalised) rather than failing the layout.
    if coffee_spec:
        coffee_w, coffee_d = float(coffee_spec["width"]), float(coffee_spec["depth"])
        placed = next(
            (geom for geom in _coffee_candidates(sofa_geom, tv_geom, tweak, coffee_w, coffee_d, units_per_meter)
             if inside(ring_of(geom)) and not hits_door(ring_of(geom))
             and not any(rings_overlap(ring_of(geom), other) for other in (sofa_ring, tv_ring))),
            None)
        if placed is not None:
            placements.append(_placement(coffee_spec, placed, coffee_w, coffee_d, box, meters_per_unit))
            assumptions.append("Coffee table centred between the sofa and the TV unit")
        else:
            complete = False
            penalties["coffee_missing"] = _PENALTY["coffee_missing"]
            assumptions.append("Coffee table left out: no clear space remained between the sofa and TV")

    handled = {sofa_spec["id"], tv_spec["id"]} | ({coffee_spec["id"]} if coffee_spec else set())
    deferred = sorted({str(spec.get("id")) for spec in furniture if str(spec.get("id")) not in handled})
    if deferred:
        assumptions.append("Living-room mode arranges the sofa, TV, and coffee table only; "
                           f"not placed: {', '.join(deferred)}")
    return {"placements": placements, "assumptions": assumptions,
            "penalties": penalties, "complete": complete}


def _seed_rotate(items: list[Any], seed: int) -> list[Any]:
    offset = seed % len(items) if items else 0
    return items[offset:] + items[:offset]


def _layout_signature(placements: list[dict[str, Any]]) -> tuple:
    return tuple(sorted((str(p.get("catalogId", "")), round(float(p["x"]), 1),
                         round(float(p["y"]), 1), round(float(p.get("rotation", 0))))
                        for p in placements))


def _diverse_layouts(layouts: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Keep the best layout first, then favour genuinely different wall rules.

    Position-only variations of one arrangement used to occupy every result.
    The greedy diversity bonus makes unseen sofa walls the strongest alternative,
    followed by unseen TV walls, wall pairings, and facing/perpendicular relations.
    Score remains the tie-breaker, so the best example of each rule wins.
    """
    remaining = sorted(
        layouts,
        key=lambda entry: (
            "tv_window_wall" not in entry["penalties"],
            "tv_not_facing" not in entry["penalties"],
            "near_door" not in entry["penalties"],
            entry["score"],
        ),
        reverse=True,
    )
    if not remaining:
        return []
    selected = [remaining.pop(0)]
    sofa_walls = {selected[0]["_sofaWallIndex"]}
    tv_walls = {selected[0]["_tvWallIndex"]}
    pairs = {(selected[0]["_sofaWallIndex"], selected[0]["_tvWallIndex"])}
    relations = {selected[0]["_tvRule"]}
    while remaining and len(selected) < count:
        def priority(entry: dict[str, Any]) -> tuple:
            pair = (entry["_sofaWallIndex"], entry["_tvWallIndex"])
            return (
                entry["_sofaWallIndex"] not in sofa_walls,
                entry["_tvWallIndex"] not in tv_walls,
                pair not in pairs,
                entry["_tvRule"] not in relations,
                entry["score"],
            )
        chosen = max(remaining, key=priority)
        remaining.remove(chosen)
        selected.append(chosen)
        sofa_walls.add(chosen["_sofaWallIndex"])
        tv_walls.add(chosen["_tvWallIndex"])
        pairs.add((chosen["_sofaWallIndex"], chosen["_tvWallIndex"]))
        relations.add(chosen["_tvRule"])
    for entry in selected:
        entry.pop("_sofaWallIndex", None)
        entry.pop("_tvWallIndex", None)
        entry.pop("_tvRule", None)
    return selected


def generate_living_room_layouts(request: dict[str, Any]) -> dict[str, Any]:
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

    furniture = request.get("furniture", [])
    sofa_spec = _catalog_lookup(furniture, "sofa", "couch")
    tv_spec = _catalog_lookup(furniture, "tv", "television")
    if not sofa_spec or not tv_spec:
        return _no_layouts(request, revision, "A living-room layout needs at least a sofa and a TV unit")

    box = polygon_bbox(polygon)
    walls = _polygon_walls(polygon)
    if len(walls) < 3:
        raise ProjectValidationError("Room polygon must have at least three non-degenerate walls")
    openings = request.get("openings", [])
    window_walls = _opening_walls(openings, walls, "window")
    door_walls = _opening_walls(openings, walls, "door")
    doors_only = [opening for opening in openings if opening.get("kind") == "door"]
    longest_len = max(wall["length"] for wall in walls)

    sofa_len_px = max(float(sofa_spec["width"]), float(sofa_spec["depth"])) * units_per_meter
    tv_len_px = max(float(tv_spec["width"]), float(tv_spec["depth"])) * units_per_meter
    candidates = _wall_candidates(walls, window_walls, door_walls, sofa_len_px, tv_len_px)
    if not candidates:
        return _no_layouts(request, revision, "The sofa requires a wall without a door")

    full: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    excluded = {_layout_signature(entry.get("placements", [])) for entry in request.get("excludeLayouts", [])}
    explored = 0

    # Explore every wall pair. Keep only a bounded number of variants per pair;
    # final selection below values different placement rules over tiny nudges.
    for sofa_wall, tv_wall in candidates:
        accepted_for_pair = 0
        for tweak in _seed_rotate(_TWEAKS, seed):
            explored += 1
            built = _build(request, sofa_wall, tv_wall, tweak, window_walls, door_walls,
                           longest_len, units_per_meter, meters_per_unit, box)
            if built is None:
                continue
            placements = built["placements"]
            # A furniture-on-furniture collision is the one thing we never ship.
            verdict = validate_layout({**request, "openings": doors_only, "placements": placements})
            if any(v["code"] == "furniture-overlap" for v in verdict["violations"]):
                continue
            signature = _layout_signature(placements)
            if signature in seen or signature in excluded:
                continue
            seen.add(signature)
            _ok, _why, functional = _semantic_verdict(request, placements, units_per_meter)
            components = _layout_score_components(request, placements, units_per_meter, functional)
            penalty = sum(built["penalties"].values())
            layout = {
                "id": "layout", "roomId": request.get("roomId"), "placements": placements,
                "validity": "valid" if not built["penalties"] else "review",
                "violations": [], "assumptions": built["assumptions"] + verdict["assumptions"],
                "score": round(max(0.0, sum(components.values()) - penalty), 2),
                "scoreComponents": components, "penalties": built["penalties"],
                "scoreKind": "constructive-living-room", "seed": seed, "inputRevision": revision,
                "_sofaWallIndex": sofa_wall["index"], "_tvWallIndex": tv_wall["index"],
                "_tvRule": "facing" if _facing(tv_wall, sofa_wall) <= FACING_DOT else "perpendicular",
            }
            (full if built["complete"] else partial).append(layout)
            accepted_for_pair += 1
            if accepted_for_pair >= max(8, count * 2):
                break

    layouts = _diverse_layouts(full or partial, count)
    for position, layout in enumerate(layouts, start=1):
        layout["id"] = f"layout-{position}"
    if layouts:
        return {"requestId": request.get("requestId"), "inputRevision": revision, "layouts": layouts,
                "partial": False, "exploredCandidates": explored, "reasons": []}
    return _no_layouts(request, revision,
                       "The room is smaller than the sofa or the TV unit at the confirmed scale",
                       explored)


def _no_layouts(request: dict[str, Any], revision: int, reason: str, explored: int = 0) -> dict[str, Any]:
    return {
        "requestId": request.get("requestId"), "inputRevision": revision, "layouts": [],
        "partial": False, "exploredCandidates": explored,
        "reasons": [{
            "code": "no-feasible-layout",
            "message": f"No living-room layout could be built. {reason}.",
            "suggestions": [
                "Check the confirmed two-point scale (metres per pixel)",
                "Confirm the room polygon is the room, not the whole plan",
                "Reduce the sofa or TV unit dimensions",
            ],
        }],
    }
