from __future__ import annotations

import math
from typing import Any

from .project import ProjectValidationError


def bounded_snap_segments(segments: list[dict[str, Any]], tolerance_px: float = 4.0) -> list[dict[str, Any]]:
    """Snap only nearby endpoints; preserve offsets larger than the configured review tolerance."""
    if tolerance_px < 0 or tolerance_px > 20:
        raise ProjectValidationError("Snapping tolerance must be between 0 and 20 original-image pixels")
    result = [{**s, "a": list(s["a"]), "b": list(s["b"])} for s in segments]
    endpoints = [(s, key) for s in result for key in ("a", "b")]
    for i, (segment, key) in enumerate(endpoints):
        point = segment[key]
        neighbours = [other[other_key] for other, other_key in endpoints[i+1:]
                      if math.dist(point, other[other_key]) <= tolerance_px]
        if neighbours:
            cluster = [point, *neighbours]
            target = [sum(p[0] for p in cluster)/len(cluster), sum(p[1] for p in cluster)/len(cluster)]
            if math.dist(point, target) <= tolerance_px:
                segment[key] = target
    return result


def associate_openings(openings: list[dict[str, Any]], walls: list[dict[str, Any]], rooms: list[dict[str, Any]], tolerance_px: float = 8.0) -> list[dict[str, Any]]:
    """Associate to nearby real walls and preserve ambiguous/unmatched detections for review."""
    result=[]
    for opening in openings:
        center=[(opening["span"][0][0]+opening["span"][1][0])/2,(opening["span"][0][1]+opening["span"][1][1])/2]
        distances=[]
        for wall in walls:
            a,b=wall["segment"];vx,vy=b[0]-a[0],b[1]-a[1];den=vx*vx+vy*vy
            t=0 if den==0 else max(0,min(1,((center[0]-a[0])*vx+(center[1]-a[1])*vy)/den))
            distances.append((math.dist(center,[a[0]+t*vx,a[1]+t*vy]),wall["id"]))
        near=sorted((d,w) for d,w in distances if d<=tolerance_px)
        adjacent=[room["id"] for room in rooms if room.get("openingIds") and opening.get("id") in room["openingIds"]]
        status="reviewed" if len(near)==1 else "ambiguous" if near else "unmatched"
        result.append({**opening,"wallId":near[0][1] if len(near)==1 else None,"candidateWallIds":[w for _,w in near],
                       "adjacentRoomIds":adjacent[:2],"reviewStatus":status})
    return result


def extraction_contract(detections: dict[str, Any]) -> dict[str, Any]:
    """Validate structured detection input without pretending boxes are room polygons."""
    if detections.get("coordinateSpace") != "original-image":
        raise ProjectValidationError("Detections must be transformed into original-image coordinates")
    return {"wallEvidence": detections.get("walls", []), "openingEvidence": detections.get("doors", []) + detections.get("windows", []),
            "roomHints": detections.get("rooms", []), "barrierMaskPolicy": "bridge-confirmed-openings-for-extraction-only",
            "navigationPolicy": "use-unsealed-openings", "warnings": ["Room boxes are association hints, not walls or polygons"],
            "requiresReview": True}
