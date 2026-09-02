from __future__ import annotations

import json
import math
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "2.0"


class ProjectValidationError(ValueError):
    """An actionable error in project geometry or metadata."""


def _number(value: Any, path: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProjectValidationError(f"{path} must be a number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        qualifier = "a positive finite number" if positive else "finite"
        raise ProjectValidationError(f"{path} must be {qualifier}")
    return result


def validate_ring(ring: Any, path: str = "polygon.outer") -> list[list[float]]:
    if not isinstance(ring, list) or len(ring) < 3:
        raise ProjectValidationError(f"{path} must contain at least three vertices")
    result = [[_number(p[0], f"{path}[{i}].x"), _number(p[1], f"{path}[{i}].y")]
              for i, p in enumerate(ring) if isinstance(p, (list, tuple)) and len(p) == 2]
    if len(result) != len(ring):
        raise ProjectValidationError(f"{path} vertices must be [x, y] pairs")
    if abs(signed_area(result)) < 1e-9:
        raise ProjectValidationError(f"{path} has zero area")
    return result


def signed_area(ring: list[list[float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


def polygon_area(polygon: dict[str, Any]) -> float:
    outer = abs(signed_area(validate_ring(polygon.get("outer"))))
    holes = sum(abs(signed_area(validate_ring(r, f"polygon.holes[{i}]")))
                for i, r in enumerate(polygon.get("holes", [])))
    return max(0.0, outer - holes)


def polygon_bbox(polygon: dict[str, Any]) -> dict[str, float]:
    outer = validate_ring(polygon.get("outer"))
    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
    return {"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}


def calibrate_scale(point_a: list[float], point_b: list[float], length: float, unit: str) -> dict[str, Any]:
    a = [_number(point_a[0], "pointA.x"), _number(point_a[1], "pointA.y")]
    b = [_number(point_b[0], "pointB.x"), _number(point_b[1], "pointB.y")]
    factors = {"m": 1.0, "cm": .01, "mm": .001, "ft": .3048, "in": .0254}
    if unit not in factors:
        raise ProjectValidationError("unit must be m, cm, mm, ft, or in")
    distance = math.hypot(b[0] - a[0], b[1] - a[1])
    if distance <= 1e-9:
        raise ProjectValidationError("Calibration points must not be identical")
    known = _number(length, "knownLength", positive=True)
    return {"metersPerPixel": known * factors[unit] / distance, "source": "two-point-measurement",
            "measurementPoints": [a, b], "knownLength": known, "unit": unit,
            "confirmed": True, "revision": 1}


def new_project(image: dict[str, Any] | None = None) -> dict[str, Any]:
    image = image or {}
    return {"schemaVersion": SCHEMA_VERSION, "id": str(uuid.uuid4()),
            "image": {"id": image.get("id"), "name": image.get("name"),
                      "naturalWidth": image.get("naturalWidth"), "naturalHeight": image.get("naturalHeight"),
                      "orientationCorrected": True, "transforms": image.get("transforms", [])},
            "scale": None, "geometryRevision": 0, "walls": [], "openings": [], "rooms": [],
            "zones": [], "fixedObstacles": [], "layouts": [], "selectedRoomId": None,
            "selectedLayoutId": None, "warnings": []}


def validate_project(project: dict[str, Any]) -> dict[str, Any]:
    if project.get("schemaVersion") != SCHEMA_VERSION:
        raise ProjectValidationError(f"Unsupported schemaVersion; expected {SCHEMA_VERSION}")
    ids: set[str] = set()
    for i, room in enumerate(project.get("rooms", [])):
        room_id = str(room.get("id", ""))
        if not room_id or room_id in ids:
            raise ProjectValidationError(f"rooms[{i}].id must be unique and non-empty")
        ids.add(room_id)
        if room.get("polygon") is not None:
            validate_ring(room["polygon"].get("outer"), f"rooms[{i}].polygon.outer")
            for j, hole in enumerate(room["polygon"].get("holes", [])):
                validate_ring(hole, f"rooms[{i}].polygon.holes[{j}]")
    scale = project.get("scale")
    if scale is not None:
        _number(scale.get("metersPerPixel"), "scale.metersPerPixel", positive=True)
    return project


def import_legacy(data: dict[str, Any], source_name: str = "legacy.json") -> dict[str, Any]:
    """Import without inventing image registration, polygons, or facing direction."""
    original = deepcopy(data)
    room_id = str(data.get("room_id", ""))
    if not room_id:
        raise ProjectValidationError("room_id is required")
    scale = _number(data.get("scale_m_per_px"), "scale_m_per_px", positive=True)
    bbox = data.get("bbox_px") or {}
    box = {k: _number(bbox.get(k), f"bbox_px.{k}", positive=k in {"width", "height"})
           for k in ("x", "y", "width", "height")}
    warnings = ["Image registration is unknown; coordinates are shown only in the legacy preview.",
                "Room polygon is missing; extract or draw it before validation.",
                "Facing direction is unknown and was not inferred from legacy wall labels."]
    if data.get("scale_source") == "door":
        warnings.append("Door-derived scale is estimated and requires two-point confirmation.")
    candidates, seen = [], set()
    for index, layout in enumerate(data.get("layouts", [])):
        placements, orientation_unknown = [], False
        for j, item in enumerate(layout.get("furniture", [])):
            x1, y1 = _number(item.get("x1"), f"layouts[{index}].furniture[{j}].x1"), _number(item.get("y1"), f"layouts[{index}].furniture[{j}].y1")
            x2, y2 = _number(item.get("x2"), f"layouts[{index}].furniture[{j}].x2"), _number(item.get("y2"), f"layouts[{index}].furniture[{j}].y2")
            if x2 <= x1 or y2 <= y1:
                raise ProjectValidationError(f"layouts[{index}].furniture[{j}] has non-positive extents")
            stored_w, stored_h = item.get("width_px"), item.get("height_px")
            item_warnings = []
            if stored_w is not None and not math.isclose(_number(stored_w, "width_px"), x2-x1, abs_tol=.01):
                item_warnings.append("Stored width differs from recomputed extent")
            if stored_h is not None and not math.isclose(_number(stored_h, "height_px"), y2-y1, abs_tol=.01):
                item_warnings.append("Stored height differs from recomputed extent")
            orientation_unknown = True
            placements.append({"id": f"legacy-{index}-{j}", "catalogId": str(item.get("name", "item")).lower().replace(" ", "-"),
                "name": item.get("name", "Furniture"), "imageBox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                "widthMeters": (x2-x1)*scale, "depthMeters": (y2-y1)*scale, "rotation": None,
                "frontDirection": None, "locked": False, "source": deepcopy(item), "warnings": item_warnings})
        key = tuple(sorted((p["catalogId"], round(p["imageBox"]["x1"], 2), round(p["imageBox"]["y1"], 2),
                            round(p["imageBox"]["x2"], 2), round(p["imageBox"]["y2"], 2)) for p in placements))
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"id": str(layout.get("layout_id", f"legacy-{index+1}")), "roomId": room_id,
                           "placements": placements, "validity": "unvalidated", "violations": [],
                           "assumptions": ["Furniture facing direction requires review"] if orientation_unknown else [],
                           "score": layout.get("score"), "scoreKind": "legacy-unverified", "source": deepcopy(layout)})
    if any("bed" in p["catalogId"] and (p["widthMeters"] > 3 or p["depthMeters"] > 3)
           for c in candidates for p in c["placements"]):
        warnings.append("Legacy bed dimensions appear unusually large and require review; catalog defaults were not changed.")
    return {"schemaVersion": SCHEMA_VERSION, "kind": "legacy-layout-import", "sourceName": source_name,
            "original": original, "registration": None, "room": {"id": room_id, "name": data.get("room_name", room_id),
            "type": data.get("room_type", "custom"), "reportedAreaM2": data.get("room_area_m2"), "polygon": None,
            "legacyBboxPx": box, "source": "legacy-import", "reviewStatus": "needs-review"},
            "scale": {"metersPerPixel": scale, "source": data.get("scale_source", "legacy"), "confirmed": False, "revision": 0},
            "openings": {"doors": deepcopy(data.get("doors", [])), "windows": deepcopy(data.get("windows", []))},
            "layouts": candidates, "warnings": warnings}


def legacy_layouts_to_project(data: dict[str, Any], source_name: str = "legacy.json") -> dict[str, Any]:
    """Turn a bbox-based model result into a directly usable project."""
    imported = import_legacy(data, source_name)
    box = imported["room"]["legacyBboxPx"]
    x, y, width, height = box["x"], box["y"], box["width"], box["height"]
    room_id, scale = imported["room"]["id"], imported["scale"]["metersPerPixel"]
    polygon = {"outer": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]], "holes": []}

    def opening(raw: dict[str, Any], kind: str, index: int) -> dict[str, Any]:
        x1, y1, x2, y2 = (_number(raw.get(k), f"{kind}s[{index}].{k}") for k in ("x1", "y1", "x2", "y2"))
        span = ([[x1, (y1 + y2) / 2], [x2, (y1 + y2) / 2]] if x2 - x1 >= y2 - y1
                else [[(x1 + x2) / 2, y1], [(x1 + x2) / 2, y2]])
        return {"id": f"{kind}-{index + 1}", "kind": kind, "span": span,
                "adjacentRoomIds": [room_id], "reviewStatus": "model-derived",
                "clearance": .9 if kind == "door" else .35}

    openings = [opening(value, "door", i) for i, value in enumerate(data.get("doors", []))]
    openings += [opening(value, "window", i) for i, value in enumerate(data.get("windows", []))]
    layouts = []
    for candidate in imported["layouts"]:
        placements = []
        for placement in candidate["placements"]:
            image_box = placement["imageBox"]
            placements.append({"id": placement["id"], "catalogId": placement["catalogId"],
                "x": image_box["x1"], "y": image_box["y1"],
                "width": image_box["x2"] - image_box["x1"], "depth": image_box["y2"] - image_box["y1"],
                "widthMeters": placement["widthMeters"], "depthMeters": placement["depthMeters"],
                "heightMeters": None, "rotation": 0, "frontDirection": None,
                "positionMeters": {"x": (image_box["x1"] - x) * scale, "y": (image_box["y1"] - y) * scale},
                "required": True, "locked": False})
        layouts.append({"id": candidate["id"], "roomId": room_id, "placements": placements,
                        "validity": "model-derived", "violations": [], "assumptions": candidate["assumptions"],
                        "score": candidate["score"] or 0, "scoreComponents": {}, "scoreKind": "legacy-unverified",
                        "seed": None, "inputRevision": 0})

    first = layouts[0]["placements"] if layouts else []
    furniture = [[p["catalogId"], p["catalogId"].replace("-", " ").title(), 1,
                  round(p["widthMeters"], 3), round(p["depthMeters"], 3), True] for p in first]
    project = new_project()
    project.update({"scale": {"metersPerPixel": scale, "source": data.get("scale_source", "model-json"),
                              "confirmed": True, "acceptedFromInputJson": True, "revision": 1},
                    "geometryRevision": 1, "openings": openings, "layouts": layouts,
                    "selectedRoomId": room_id, "selectedLayoutId": layouts[0]["id"] if layouts else None,
                    "warnings": ["Room boundary uses the bbox supplied by the input JSON."]})
    project["rooms"] = [{"id": room_id, "name": imported["room"]["name"],
                         "type": str(imported["room"]["type"]).lower(), "polygon": polygon,
                         "source": "input-json-bbox", "reviewStatus": "model-derived",
                         "computedAreaM2": polygon_area(polygon) * scale * scale, "furniture": furniture}]
    return project


@dataclass(slots=True)
class ProjectStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, project: dict[str, Any]) -> Path:
        validate_project(project)
        safe_id = str(project["id"])
        if not safe_id.replace("-", "").isalnum():
            raise ProjectValidationError("Project ID contains unsupported characters")
        path = self.root / f"{safe_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(project, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, project_id: str) -> dict[str, Any]:
        if not project_id.replace("-", "").isalnum():
            raise ProjectValidationError("Invalid project ID")
        path = self.root / f"{project_id}.json"
        if not path.exists():
            raise ProjectValidationError("Project not found")
        return validate_project(json.loads(path.read_text(encoding="utf-8")))
