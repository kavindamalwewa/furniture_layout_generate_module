from __future__ import annotations

import hashlib
import math
import random
import time
from collections import deque
from typing import Any

from .project import ProjectValidationError, polygon_bbox, signed_area, validate_ring


EPS = 1e-8


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if point_on_segment(point, a, b):
            return True
        if (a[1] > y) != (b[1] > y):
            cross_x = (b[0]-a[0]) * (y-a[1]) / (b[1]-a[1]) + a[0]
            if x < cross_x:
                inside = not inside
    return inside


def point_on_segment(p: tuple[float, float], a: list[float], b: list[float]) -> bool:
    cross = (p[0]-a[0])*(b[1]-a[1]) - (p[1]-a[1])*(b[0]-a[0])
    return abs(cross) <= EPS and min(a[0], b[0])-EPS <= p[0] <= max(a[0], b[0])+EPS and min(a[1], b[1])-EPS <= p[1] <= max(a[1], b[1])+EPS


def segments_intersect(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
    def orient(p, q, r): return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    values = orient(a,b,c), orient(a,b,d), orient(c,d,a), orient(c,d,b)
    if values[0]*values[1] < -EPS and values[2]*values[3] < -EPS:
        return True
    return any(abs(v) <= EPS and point_on_segment(tuple(p), q, r) for v,p,q,r in
               ((values[0],c,a,b),(values[1],d,a,b),(values[2],a,c,d),(values[3],b,c,d)))


def point_in_polygon(point: tuple[float, float], polygon: dict[str, Any]) -> bool:
    return point_in_ring(point, polygon["outer"]) and not any(point_in_ring(point, h) for h in polygon.get("holes", []))


def rectangle_ring(x: float, y: float, width: float, depth: float, rotation: float = 0) -> list[list[float]]:
    angle = math.radians(rotation)
    c, s = math.cos(angle), math.sin(angle)
    return [[x + px*c - py*s, y + px*s + py*c] for px,py in ((0,0),(width,0),(width,depth),(0,depth))]


def rings_overlap(a: list[list[float]], b: list[list[float]]) -> bool:
    if any(point_in_ring(tuple(p), b) for p in a) or any(point_in_ring(tuple(p), a) for p in b):
        return True
    return any(segments_intersect(a1,a2,b1,b2) for a1,a2 in zip(a,a[1:]+a[:1]) for b1,b2 in zip(b,b[1:]+b[:1]))


def footprint_inside(ring: list[list[float]], polygon: dict[str, Any]) -> bool:
    # Vertices alone are insufficient for concave rooms: reject all boundary crossings too.
    if not all(point_in_polygon(tuple(p), polygon) for p in ring):
        return False
    for edge_a, edge_b in zip(ring, ring[1:]+ring[:1]):
        for boundary in [polygon["outer"], *polygon.get("holes", [])]:
            for a,b in zip(boundary, boundary[1:]+boundary[:1]):
                if segments_intersect(edge_a, edge_b, a, b) and not (point_on_segment(tuple(edge_a),a,b) and point_on_segment(tuple(edge_b),a,b)):
                    # Permit touching at a furniture vertex; midpoint catches excursions.
                    midpoint=((edge_a[0]+edge_b[0])/2,(edge_a[1]+edge_b[1])/2)
                    if not point_in_polygon(midpoint, polygon): return False
    return True


def _footprint(placement: dict[str, Any]) -> list[list[float]]:
    return placement.get("footprint") or rectangle_ring(float(placement["x"]), float(placement["y"]),
        float(placement["width"]), float(placement.get("depth", placement.get("length"))), float(placement.get("rotation", 0)))


def _point_segment_distance(point: tuple[float, float], a: list[float], b: list[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    t = 0.0 if denominator <= EPS else max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def _centre(placement: dict[str, Any]) -> tuple[float, float]:
    ring = _footprint(placement)
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


def _nearest_wall(point: tuple[float, float], polygon: dict[str, Any]) -> tuple[int, float]:
    walls = list(zip(polygon["outer"], polygon["outer"][1:] + polygon["outer"][:1]))
    distances = [_point_segment_distance(point, a, b) for a, b in walls]
    index = min(range(len(distances)), key=distances.__getitem__)
    return index, distances[index]


def _ring_distance(a: list[list[float]], b: list[list[float]]) -> float:
    if rings_overlap(a, b): return 0.0
    values = [_point_segment_distance(tuple(p), x, y) for p in a for x, y in zip(b, b[1:] + b[:1])]
    values += [_point_segment_distance(tuple(p), x, y) for p in b for x, y in zip(a, a[1:] + a[:1])]
    return min(values, default=0.0)


def _layout_score_components(request: dict[str, Any], placements: list[dict[str, Any]], units_per_meter: float,
                             functional_score: float) -> dict[str, float]:
    polygon=request["polygon"]; footprints=[_footprint(p) for p in placements]
    doors=[_opening_keep_clear(o,units_per_meter) for o in request.get("openings",[]) if o.get("kind")=="door"]
    doors=[door for door in doors if door]
    nearest=min((_ring_distance(ring,door) for ring in footprints for door in doors),default=.9*units_per_meter)/units_per_meter
    door_access=20*min(1,nearest/.9)
    gaps=[_ring_distance(a,b)/units_per_meter for i,a in enumerate(footprints) for b in footprints[i+1:]]
    def gap_quality(gap: float) -> float:
        if gap < .45:return max(0,gap/.45)
        if gap <= 1.8:return 1.0
        return max(.35,1-(gap-1.8)/4)
    spacing=15 if not gaps else 15*sum(gap_quality(gap) for gap in gaps)/len(gaps)
    box=polygon_bbox(polygon); centre=(box["x"]+box["width"]/2,box["y"]+box["height"]/2)
    centre_zone=rectangle_ring(box["x"]+box["width"]*.3,box["y"]+box["height"]*.3,box["width"]*.4,box["height"]*.4)
    blockers=[p for p in placements if "coffee" not in str(p.get("catalogId","")).lower()]
    blocked=sum(rings_overlap(_footprint(p),centre_zone) for p in blockers)
    open_space=20*(1-blocked/max(len(blockers),1))
    sofa_wall=15.0; sofas=[p for p in placements if any(k in str(p.get("catalogId","")).lower() for k in ("sofa","couch"))]
    if sofas:
        walls=list(zip(polygon["outer"],polygon["outer"][1:]+polygon["outer"][:1]));lengths=[math.dist(a,b) for a,b in walls]
        wall_index,_=_nearest_wall(_centre(sofas[0]),polygon)
        edge_gap=min(_point_segment_distance(tuple(vertex),*walls[wall_index]) for vertex in _footprint(sofas[0]))/units_per_meter
        sofa_wall=15*max(0,1-edge_gap)*(1 if lengths[wall_index]>=max(lengths)*.9 else .55)
    coffee_center=10.0; tables=[p for p in placements if "coffee" in str(p.get("catalogId","")).lower()]
    if tables:
        cx,cy=_centre(tables[0]);distance=math.hypot(cx-centre[0],cy-centre[1]);radius=max(math.hypot(box["width"],box["height"])/2,EPS)
        coffee_center=10*max(0,1-distance/radius)
    return {"door_access":round(door_access,2),"open_space":round(open_space,2),"spacing":round(spacing,2),
            "sofa_wall":round(sofa_wall,2),"coffee_table_center":round(coffee_center,2),
            "functional_relationships":round(functional_score,2)}


def _semantic_verdict(request: dict[str, Any], placements: list[dict[str, Any]], units_per_meter: float) -> tuple[bool, list[str], float]:
    """Hard real-world living-room rules plus a functional score."""
    polygon = request["polygon"]
    by_kind = {"tv": [], "sofa": [], "coffee": []}
    for placement in placements:
        item_id = str(placement.get("catalogId", "")).lower().replace("_", "-")
        if "tv" in item_id: by_kind["tv"].append(placement)
        if "sofa" in item_id or "couch" in item_id: by_kind["sofa"].append(placement)
        if "coffee" in item_id: by_kind["coffee"].append(placement)
    reasons: list[str] = []
    functional_score = 20.0

    window_walls = set()
    for opening in request.get("openings", []):
        if opening.get("kind") == "window" and opening.get("span"):
            midpoint = ((opening["span"][0][0] + opening["span"][1][0]) / 2,
                        (opening["span"][0][1] + opening["span"][1][1]) / 2)
            window_walls.add(_nearest_wall(midpoint, polygon)[0])

    for tv in by_kind["tv"]:
        wall_index, wall_gap = _nearest_wall(_centre(tv), polygon)
        allowed_gap = max(float(tv["width"]), float(tv["depth"])) / 2 + .3 * units_per_meter
        if wall_gap > allowed_gap:
            reasons.append("TV unit must be placed against a wall")
        if wall_index in window_walls:
            reasons.append("TV unit supporting wall must not contain a window")

    if by_kind["tv"] and by_kind["sofa"]:
        tv, sofa = by_kind["tv"][0], by_kind["sofa"][0]
        tx, ty = _centre(tv); sx, sy = _centre(sofa)
        distance = math.hypot(tx - sx, ty - sy) / units_per_meter
        angle = math.radians(float(sofa.get("rotation", 0)))
        front = (-math.sin(angle), math.cos(angle))
        length = max(math.hypot(tx - sx, ty - sy), EPS)
        alignment = abs(((tx - sx) * front[0] + (ty - sy) * front[1]) / length)
        distance_quality=max(0.0,1.0-min(abs(distance-2.7)/3.0,1.0))
        coffee_quality=1.0
        if by_kind["coffee"]:
            cx, cy = _centre(by_kind["coffee"][0]); vx, vy = tx-sx, ty-sy
            squared=max(vx*vx+vy*vy,EPS); progress=((cx-sx)*vx+(cy-sy)*vy)/squared
            perpendicular=abs(vx*(sy-cy)-(sx-cx)*vy)/math.sqrt(squared)/units_per_meter
            coffee_quality=(1.0-min(perpendicular/.8,1.0)) if .2<=progress<=.8 else .2
        functional_score=max(0.0,20.0*alignment*distance_quality*(.35+.65*coffee_quality))
    return not reasons, reasons, round(functional_score, 2)


def _opening_keep_clear(opening: dict[str, Any], units_per_meter: float = 1.0) -> list[list[float]] | None:
    if opening.get("keepClearPolygon"):
        return opening["keepClearPolygon"]
    span = opening.get("span")
    if not span: return None
    a,b = span
    depth=float(opening.get("clearance", .8))*units_per_meter; dx=b[0]-a[0]; dy=b[1]-a[1]; length=math.hypot(dx,dy)
    if length <= EPS: return None
    nx,ny=-dy/length*depth,dx/length*depth
    return [a,b,[b[0]+nx,b[1]+ny],[a[0]+nx,a[1]+ny]]


def _route_exists(polygon: dict[str, Any], blockers: list[list[list[float]]], portals: list[list[float]], grid: float=.2) -> bool:
    if len(portals) < 2: return True
    box=polygon_bbox(polygon); cols=max(1,math.ceil(box["width"]/grid)); rows=max(1,math.ceil(box["height"]/grid))
    if cols*rows > 100_000: grid*=math.sqrt(cols*rows/100_000); cols=max(1,math.ceil(box["width"]/grid)); rows=max(1,math.ceil(box["height"]/grid))
    def cell(p): return (max(0,min(cols-1,int((p[0]-box['x'])/grid))),max(0,min(rows-1,int((p[1]-box['y'])/grid))))
    def center(c): return (box['x']+(c[0]+.5)*grid,box['y']+(c[1]+.5)*grid)
    def free(c):
        p=center(c); return point_in_polygon(p,polygon) and not any(point_in_ring(p,b) for b in blockers)
    start=cell(portals[0]); targets={cell(p) for p in portals[1:]}; queue=deque([start]); visited={start}
    while queue:
        c=queue.popleft(); targets.discard(c)
        for n in ((c[0]+1,c[1]),(c[0]-1,c[1]),(c[0],c[1]+1),(c[0],c[1]-1)):
            if 0<=n[0]<cols and 0<=n[1]<rows and n not in visited and free(n): visited.add(n);queue.append(n)
    return not targets


def validate_layout(request: dict[str, Any]) -> dict[str, Any]:
    polygon=request.get("polygon")
    if not polygon: raise ProjectValidationError("Confirmed room polygon is required")
    validate_ring(polygon.get("outer")); [validate_ring(h) for h in polygon.get("holes",[])]
    meters_per_unit=float(request.get("scale",{}).get("metersPerPixel",1.0))
    if meters_per_unit <= 0: raise ProjectValidationError("scale.metersPerPixel must be positive")
    units_per_meter=1.0/meters_per_unit
    violations=[]; assumptions=[]; footprints=[]
    obstacles=[o.get("polygon",o) for o in request.get("fixedObstacles",[])]
    openings=request.get("openings",[])
    for opening in openings:
        if opening.get("kind")=="door" and not opening.get("swing"):
            assumptions.append(f"Door {opening.get('id','unknown')} uses a conservative keep-clear area because swing is unknown")
    for i,p in enumerate(request.get("placements",[])):
        try: ring=_footprint(p)
        except (KeyError,TypeError,ValueError): violations.append({"code":"invalid-placement","placementId":p.get("id"),"message":"Placement dimensions and coordinates must be finite"});continue
        if not footprint_inside(ring,polygon): violations.append({"code":"outside-usable-polygon","placementId":p.get("id"),"message":"The complete rotated footprint must stay inside the usable polygon and outside holes"})
        for obstacle in obstacles:
            if rings_overlap(ring,obstacle): violations.append({"code":"fixed-obstacle-collision","placementId":p.get("id"),"message":"Placement collides with a fixed obstacle"})
        for opening in openings:
            keep=_opening_keep_clear(opening,units_per_meter)
            if keep and rings_overlap(ring,keep): violations.append({"code":"opening-clearance","placementId":p.get("id"),"openingId":opening.get("id"),"message":"Placement blocks an opening approach or swing area"})
        if p.get("wallAttached") and (not p.get("supportWallId") or p.get("supportKind") in {"virtual-zone","window"}):
            violations.append({"code":"invalid-wall-support","placementId":p.get("id"),"message":"Wall-mounted furniture requires a real reviewed supporting wall"})
        for prior_id,prior in footprints:
            if rings_overlap(ring,prior): violations.append({"code":"furniture-overlap","placementId":p.get("id"),"otherPlacementId":prior_id,"message":"Furniture footprints overlap"})
        footprints.append((p.get("id",str(i)),ring))
    portals=[o["portalPoint"] for o in openings if o.get("kind")=="door" and o.get("portalPoint")]
    portals += [p["accessPoint"] for p in request.get("placements",[]) if p.get("required",True) and p.get("accessPoint")]
    if portals and not _route_exists(polygon,[r for _,r in footprints]+obstacles,portals,float(request.get("routeGrid",.2))*units_per_meter):
        violations.append({"code":"blocked-route","message":"Furniture leaves no clearance-aware route between required portals"})
    return {"valid":not violations,"validity":"valid" if not violations else "invalid","violations":violations,"assumptions":assumptions}


def generate_polygon_layouts(request: dict[str, Any]) -> dict[str, Any]:
    if not request.get("scale",{}).get("confirmed"):
        raise ProjectValidationError("A confirmed two-point scale is required before layout generation")
    polygon=request.get("polygon")
    if not polygon: raise ProjectValidationError("A reviewed room polygon is required before layout generation")
    revision=int(request.get("inputRevision",0)); seed=int(request.get("seed",0)); budget_ms=min(10_000,max(50,int(request.get("searchBudgetMs",1500))))
    meters_per_unit=float(request.get("scale",{}).get("metersPerPixel",1.0))
    if meters_per_unit <= 0: raise ProjectValidationError("scale.metersPerPixel must be positive")
    units_per_meter=1.0/meters_per_unit
    count=min(6,max(1,int(request.get("count",6)))); grid=max(.1,float(request.get("grid",.25)))*units_per_meter; rng=random.Random(seed); box=polygon_bbox(polygon)
    specs=[]
    for spec in request.get("furniture",[]):
        quantity=int(spec.get("quantity",1))
        for instance in range(quantity): specs.append((spec,instance+1))
    specs.sort(key=lambda pair:float(pair[0]["width"])*float(pair[0]["depth"]),reverse=True)
    candidates=[]; start=time.monotonic(); explored=0
    def positions(spec):
        rotations=spec.get("allowedRotations",[0,90]); result=[]
        xs=[box['x']+i*grid for i in range(int(box['width']/grid)+1)]; ys=[box['y']+i*grid for i in range(int(box['height']/grid)+1)]
        for rot in rotations:
            for x in xs:
                for y in ys: result.append((x,y,float(rot)))
        rng.shuffle(result); return result
    pools=[positions(s) for s,_ in specs]
    attempts=max(300,count*300)
    for _ in range(attempts):
        if (time.monotonic()-start)*1000>=budget_ms: break
        placements=[]
        for (spec,instance),pool in zip(specs,pools):
            chosen=None
            offset=rng.randrange(len(pool)) if pool else 0
            trial_pool=(pool[offset:]+pool[:offset])[:min(len(pool),200)]
            if "coffee" in str(spec.get("id","")).lower():
                sofa=next((p for p in placements if any(k in str(p["catalogId"]).lower() for k in ("sofa","couch"))),None)
                tv=next((p for p in placements if "tv" in str(p["catalogId"]).lower()),None)
                if sofa and tv:
                    sx,sy=_centre(sofa);tx,ty=_centre(tv);target=((sx+tx)/2,(sy+ty)/2)
                    trial_pool=sorted(pool,key=lambda candidate:math.hypot(candidate[0]-target[0],candidate[1]-target[1]))[:min(len(pool),300)]
            for x,y,rotation in trial_pool:
                width_m=float(spec['width']);depth_m=float(spec['depth']);width=width_m*units_per_meter;depth=depth_m*units_per_meter
                explored+=1; placement={"id":f"{spec['id']}-{instance}","catalogId":spec['id'],"x":x,"y":y,"width":width,"depth":depth,"widthMeters":width_m,"depthMeters":depth_m,"heightMeters":spec.get("height"),"rotation":rotation,"frontDirection":rotation,"locked":bool(spec.get("locked",False)),"required":bool(spec.get("required",True)),"wallAttached":bool(spec.get("wallAttached",False)),"supportWallId":spec.get("supportWallId"),"supportKind":spec.get("supportKind"),"accessPoint":spec.get("accessPoint"),"positionMeters":{"x":(x-box['x'])*meters_per_unit,"y":(y-box['y'])*meters_per_unit}}
                verdict=validate_layout({**request,"placements":placements+[placement]})
                if not any(v["code"] not in {"blocked-route"} for v in verdict["violations"]): chosen=placement;break
            if chosen: placements.append(chosen)
            elif spec.get("required",True): placements=[];break
        if len(placements) < sum(1 for s,_ in specs if s.get("required",True)): continue
        verdict=validate_layout({**request,"placements":placements})
        if not verdict["valid"]: continue
        semantic_valid, semantic_reasons, functional_score = _semantic_verdict(request, placements, units_per_meter)
        if not semantic_valid: continue
        signature=tuple(sorted((p['catalogId'],round(p['x']/grid),round(p['y']/grid),round(p['rotation'])%360,round(p['width'],2),round(p['depth'],2)) for p in placements))
        if any(c[0]==signature for c in candidates): continue
        components=_layout_score_components(request,placements,units_per_meter,functional_score)
        candidates.append((signature,{"id":f"layout-{len(candidates)+1}","roomId":request.get("roomId"),"placements":placements,"validity":"valid","violations":[],"assumptions":verdict['assumptions'],"score":round(sum(components.values()),2),"scoreComponents":components,"scoreKind":"heuristic","seed":seed,"inputRevision":revision}))
        if len(candidates)>=count: break
    layouts=[c[1] for c in sorted(candidates,key=lambda c:c[1]['score'],reverse=True)]
    return {"requestId":request.get("requestId"),"inputRevision":revision,"layouts":layouts,"partial":(time.monotonic()-start)*1000>=budget_ms,"exploredCandidates":explored,"reasons":[] if layouts else [{"code":"no-feasible-layout","message":"Required furniture does not fit without violating geometry, openings, obstacles, or circulation.","suggestions":["Review room geometry and scale","Reduce required quantities or choose smaller real dimensions","Move or unlock fixed placements"]}]}
