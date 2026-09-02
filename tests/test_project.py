import json
import tempfile
import unittest
from pathlib import Path

import math

from furniture_layout.living_room import generate_living_room_layouts, infer_room_type
from furniture_layout.polygon_engine import _layout_score_components, _opening_keep_clear, _semantic_verdict, footprint_inside, generate_polygon_layouts, rectangle_ring, rings_overlap, validate_layout
from furniture_layout.project import ProjectStore, ProjectValidationError, calibrate_scale, import_legacy, legacy_layouts_to_project, new_project
from furniture_layout.extraction import associate_openings, bounded_snap_segments, extraction_contract


def legacy_fixture():
    furniture = [{"name":"Bed","wall":"north","x1":10,"y1":20,"x2":355,"y2":166,"width_px":345,"height_px":146}]
    return {"room_id":"room-3","room_name":"Bedroom","room_type":"bedroom","room_area_m2":18,
            "scale_m_per_px":.01,"scale_source":"door","bbox_px":{"x":0,"y":0,"width":500,"height":400},
            "doors":[],"windows":[],"layouts":[{"layout_id":str(i),"score":80-i,"furniture":furniture} for i in (1,2,4,5)]}


class ProjectTests(unittest.TestCase):
    def test_legacy_preserves_original_deduplicates_and_invents_no_polygon(self):
        source=legacy_fixture(); result=import_legacy(source,"room_3_layouts.json")
        self.assertEqual(source,result["original"]);self.assertIsNone(result["room"]["polygon"]);self.assertIsNone(result["registration"])
        self.assertEqual(1,len(result["layouts"]));self.assertIsNone(result["layouts"][0]["placements"][0]["rotation"])
        self.assertIn("Furniture facing direction requires review",result["layouts"][0]["assumptions"])
        self.assertTrue(any("bed dimensions" in w for w in result["warnings"]))

    def test_invalid_scale_and_zero_calibration_are_actionable(self):
        bad=legacy_fixture();bad["scale_m_per_px"]=0
        with self.assertRaisesRegex(ProjectValidationError,"scale_m_per_px"):import_legacy(bad)
        with self.assertRaisesRegex(ProjectValidationError,"must not be identical"):calibrate_scale([1,1],[1,1],2,"m")
        self.assertAlmostEqual(.01,calibrate_scale([0,0],[100,0],1,"m")["metersPerPixel"])

    def test_project_persistence_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProjectStore(Path(tmp));project=new_project();store.save(project)
            self.assertEqual(project,store.load(project["id"]))

    def test_legacy_layout_file_opens_without_drawing_a_room(self):
        source=legacy_fixture();project=legacy_layouts_to_project(source,"room_0_layouts.json")
        self.assertEqual("room-3",project["selectedRoomId"])
        self.assertTrue(project["scale"]["confirmed"])
        self.assertEqual(4,len(project["rooms"][0]["polygon"]["outer"]))
        self.assertGreater(len(project["layouts"]),0)
        self.assertIn("x",project["layouts"][0]["placements"][0])


class PolygonTests(unittest.TestCase):
    def test_l_room_and_hole_reject_crossing_footprints(self):
        l={"outer":[[0,0],[4,0],[4,2],[2,2],[2,4],[0,4]],"holes":[]}
        self.assertFalse(footprint_inside(rectangle_ring(1.5,1.5,2,2),l))
        holed={"outer":[[0,0],[5,0],[5,5],[0,5]],"holes":[[[2,2],[3,2],[3,3],[2,3]]]}
        self.assertFalse(footprint_inside(rectangle_ring(1.5,1.5,2,2,45),holed))

    def test_obstacle_opening_rotation_and_virtual_wall_are_checked(self):
        polygon={"outer":[[0,0],[6,0],[6,5],[0,5]],"holes":[]}
        base={"polygon":polygon,"openings":[{"id":"door","kind":"door","span":[[0,0],[1,0]],"keepClearPolygon":[[0,0],[1,0],[1,1],[0,1]]}],"fixedObstacles":[{"polygon":[[4,4],[5,4],[5,5],[4,5]]}]}
        placements=[{"id":"p","x":.2,"y":.2,"width":.5,"depth":.5,"rotation":45,"wallAttached":True,"supportKind":"virtual-zone"}]
        codes={v["code"] for v in validate_layout({**base,"placements":placements})["violations"]}
        self.assertIn("opening-clearance",codes);self.assertIn("invalid-wall-support",codes)
        obstacle={"id":"p","x":4.1,"y":4.1,"width":.5,"depth":.5,"rotation":0}
        self.assertIn("fixed-obstacle-collision",{v["code"] for v in validate_layout({**base,"placements":[obstacle]})["violations"]})

    def test_blocked_route_is_rejected(self):
        polygon={"outer":[[0,0],[4,0],[4,4],[0,4]],"holes":[]}
        wall={"id":"barrier","x":0,"y":1.7,"width":4,"depth":.6,"rotation":0,"required":True}
        openings=[{"id":"a","kind":"door","portalPoint":[.5,.5]},{"id":"b","kind":"door","portalPoint":[.5,3.5]}]
        self.assertIn("blocked-route",{v["code"] for v in validate_layout({"polygon":polygon,"placements":[wall],"openings":openings})["violations"]})

    def test_generation_reproducible_stale_revision_and_impossible_reason(self):
        request={"roomId":"r","polygon":{"outer":[[0,0],[5,0],[5,4],[0,4]],"holes":[]},"scale":{"confirmed":True},"inputRevision":7,"seed":12,"count":2,"searchBudgetMs":1000,"furniture":[{"id":"bed","width":1.5,"depth":2,"quantity":1,"required":True,"allowedRotations":[0,90]}]}
        a=generate_polygon_layouts(request);b=generate_polygon_layouts(request)
        self.assertEqual(a["layouts"],b["layouts"]);self.assertTrue(all(x["inputRevision"]==7 for x in a["layouts"]))
        impossible=generate_polygon_layouts({**request,"furniture":[{"id":"huge","width":9,"depth":9,"required":True}]})
        self.assertEqual([],impossible["layouts"]);self.assertEqual("no-feasible-layout",impossible["reasons"][0]["code"])

    def test_pixel_polygon_converts_metric_furniture_and_emits_3d_coordinates(self):
        request={"roomId":"r","polygon":{"outer":[[100,200],[600,200],[600,600],[100,600]],"holes":[]},
                 "scale":{"confirmed":True,"metersPerPixel":.01},"seed":3,"count":1,
                 "furniture":[{"id":"bed","width":1.5,"depth":2.0,"required":True}]}
        placement=generate_polygon_layouts(request)["layouts"][0]["placements"][0]
        self.assertEqual(150.0,placement["width"]);self.assertEqual(200.0,placement["depth"])
        self.assertEqual(1.5,placement["widthMeters"]);self.assertEqual(2.0,placement["depthMeters"])
        self.assertAlmostEqual((placement["x"]-100)*.01,placement["positionMeters"]["x"])

    def test_missing_polygon_and_unconfirmed_scale_stop_generation(self):
        with self.assertRaisesRegex(ProjectValidationError,"confirmed two-point scale"):generate_polygon_layouts({"scale":{"confirmed":False}})
        with self.assertRaisesRegex(ProjectValidationError,"reviewed room polygon"):generate_polygon_layouts({"scale":{"confirmed":True}})

    def test_living_room_tv_faces_sofa_and_uses_window_free_wall(self):
        polygon={"outer":[[0,0],[6,0],[6,5],[0,5]],"holes":[]}
        placements=[{"catalogId":"sofa","x":2,"y":1,"width":2,"depth":.8,"rotation":0},
                    {"catalogId":"tv-unit","x":2.5,"y":4.6,"width":1,"depth":.3,"rotation":0}]
        valid={"polygon":polygon,"openings":[{"kind":"window","span":[[0,0],[2,0]]}]}
        self.assertTrue(_semantic_verdict(valid,placements,1)[0])
        invalid={"polygon":polygon,"openings":[{"kind":"window","span":[[2,5],[4,5]]}]}
        verdict=_semantic_verdict(invalid,placements,1)
        self.assertFalse(verdict[0]);self.assertTrue(any("window" in reason for reason in verdict[1]))

    def test_modern_arrangement_scores_above_central_sofa_and_wall_table(self):
        request={"polygon":{"outer":[[0,0],[6,0],[6,5],[0,5]],"holes":[]},"openings":[]}
        good=[{"catalogId":"sofa","x":2,"y":.1,"width":2,"depth":.8,"rotation":0},
              {"catalogId":"tv-unit","x":2.5,"y":4.6,"width":1,"depth":.3,"rotation":0},
              {"catalogId":"coffee-table","x":2.5,"y":2.3,"width":1,"depth":.5,"rotation":0}]
        bad=[{"catalogId":"sofa","x":2,"y":2,"width":2,"depth":.8,"rotation":0},good[1],
             {"catalogId":"coffee-table","x":.1,"y":2.3,"width":1,"depth":.5,"rotation":0}]
        good_score=sum(_layout_score_components(request,good,1,20).values())
        bad_score=sum(_layout_score_components(request,bad,1,20).values())
        self.assertGreater(good_score,bad_score+20)

    def test_living_strategy_requires_long_wall_sofa_and_centered_coffee_table(self):
        polygon={"outer":[[0,0],[6,0],[6,5],[0,5]],"holes":[]}
        request={"polygon":polygon,"openings":[],"roomStrategy":"living_room"}
        valid=[{"catalogId":"sofa","x":2,"y":0,"width":2,"depth":.8,"rotation":0},
               {"catalogId":"tv-unit","x":2.5,"y":4.6,"width":1,"depth":.3,"rotation":0},
               {"catalogId":"coffee-table","x":2.5,"y":2.3,"width":1,"depth":.5,"rotation":0}]
        self.assertTrue(_semantic_verdict(request,valid,1)[0])
        invalid=[{**valid[0],"x":0,"y":1.5,"rotation":90},valid[1],
                 {**valid[2],"x":.1}]
        verdict=_semantic_verdict(request,invalid,1)
        self.assertFalse(verdict[0]);self.assertTrue(any("Coffee table" in reason or "longest walls" in reason for reason in verdict[1]))

def _centroid(placement):
    ring = rectangle_ring(placement["x"], placement["y"], placement["width"], placement["depth"], placement["rotation"])
    return sum(p[0] for p in ring) / 4, sum(p[1] for p in ring) / 4


class LivingRoomTests(unittest.TestCase):
    def _request(self, **overrides):
        request = {
            "requestId": "lr", "roomId": "living-1", "roomType": "living-room", "inputRevision": 4,
            "polygon": {"outer": [[0, 0], [600, 0], [600, 400], [0, 400]], "holes": []},
            "scale": {"confirmed": True, "metersPerPixel": 0.01},
            "openings": [
                {"id": "door-1", "kind": "door", "span": [[0, 120], [0, 220]], "clearance": 0.9},
                {"id": "win-1", "kind": "window", "span": [[220, 0], [400, 0]], "clearance": 0.35},
            ],
            "fixedObstacles": [],
            "furniture": [
                {"id": "sofa", "width": 2.2, "depth": 0.9, "height": 0.85, "required": True},
                {"id": "tv-unit", "width": 1.6, "depth": 0.4, "height": 0.5, "required": True},
                {"id": "coffee-table", "width": 1.1, "depth": 0.6, "height": 0.45, "required": True},
            ],
            "count": 6, "seed": 5,
        }
        request.update(overrides)
        return request

    def test_polygon_engine_dispatches_living_rooms_to_the_constructive_engine(self):
        request = self._request()
        by_type = generate_polygon_layouts(request)
        del request["roomType"]  # sofa + TV should still be inferred as a living room
        inferred = generate_polygon_layouts(request)
        self.assertEqual("constructive-living-room", by_type["layouts"][0]["scoreKind"])
        self.assertEqual(by_type["layouts"], inferred["layouts"])
        self.assertEqual("", infer_room_type([{"id": "bed"}, {"id": "wardrobe"}]))

    def test_every_layout_places_sofa_tv_and_coffee_and_revalidates(self):
        result = generate_living_room_layouts(self._request())
        self.assertTrue(result["layouts"])
        self.assertLessEqual(len(result["layouts"]), 6)
        doors_only = [o for o in self._request()["openings"] if o["kind"] == "door"]
        for layout in result["layouts"]:
            kinds = {p["catalogId"] for p in layout["placements"]}
            self.assertEqual({"sofa", "tv-unit", "coffee-table"}, kinds)
            verdict = validate_layout({**self._request(), "openings": doors_only, "placements": layout["placements"]})
            self.assertTrue(verdict["valid"], verdict["violations"])

    def test_sofa_takes_a_long_wall_and_tv_faces_it_off_the_window_wall(self):
        layout = generate_living_room_layouts(self._request())["layouts"][0]
        sofa = next(p for p in layout["placements"] if p["catalogId"] == "sofa")
        tv = next(p for p in layout["placements"] if p["catalogId"] == "tv-unit")
        # Sofa and TV sit on opposite 6 m walls (north/south), TV clear of the north window.
        self.assertLess(_centroid(sofa)[1], 100)
        self.assertGreater(_centroid(tv)[1], 300)
        self.assertTrue(_semantic_verdict(self._request(), layout["placements"], 100.0)[0])

    def test_nothing_sits_in_the_door_approach(self):
        layout = generate_living_room_layouts(self._request())["layouts"][0]
        keep_clear = _opening_keep_clear({"kind": "door", "span": [[0, 120], [0, 220]], "clearance": 0.9}, 100.0)
        for placement in layout["placements"]:
            ring = rectangle_ring(placement["x"], placement["y"], placement["width"], placement["depth"], placement["rotation"])
            self.assertFalse(rings_overlap(ring, keep_clear), placement["catalogId"])

    def test_coffee_table_sits_between_the_sofa_and_the_tv(self):
        layout = generate_living_room_layouts(self._request())["layouts"][0]
        sofa, tv, coffee = (next(p for p in layout["placements"] if p["catalogId"] == cid)
                            for cid in ("sofa", "tv-unit", "coffee-table"))
        (sx, sy), (tx, ty), (cx, cy) = _centroid(sofa), _centroid(tv), _centroid(coffee)
        length = math.hypot(tx - sx, ty - sy)
        offline = abs((tx - sx) * (sy - cy) - (sx - cx) * (ty - sy)) / length / 100.0
        progress = ((cx - sx) * (tx - sx) + (cy - sy) * (ty - sy)) / (length * length)
        self.assertLess(offline, 0.6)          # within 0.6 m of the sofa/TV centre line
        self.assertTrue(0.15 < progress < 0.85)

    def test_generation_is_deterministic_for_a_seed(self):
        self.assertEqual(generate_living_room_layouts(self._request())["layouts"],
                         generate_living_room_layouts(self._request())["layouts"])

    def test_regeneration_excludes_the_six_currently_displayed_layouts(self):
        first=generate_living_room_layouts(self._request(seed=5))["layouts"]
        second=generate_living_room_layouts(self._request(seed=6,excludeLayouts=first))["layouts"]
        def signatures(layouts):
            return {tuple(sorted((p["catalogId"],round(p["x"],1),round(p["y"],1),round(p["rotation"]))
                                 for p in layout["placements"])) for layout in layouts}
        self.assertEqual(6,len(first));self.assertEqual(6,len(second))
        self.assertFalse(signatures(first)&signatures(second))

    def test_windows_on_both_long_walls_still_produce_a_valid_layout(self):
        request = self._request(openings=[
            {"id": "door-1", "kind": "door", "span": [[0, 120], [0, 220]], "clearance": 0.9},
            {"id": "win-n", "kind": "window", "span": [[200, 0], [400, 0]], "clearance": 0.35},
            {"id": "win-s", "kind": "window", "span": [[200, 400], [400, 400]], "clearance": 0.35},
        ])
        result = generate_living_room_layouts(request)
        self.assertTrue(result["layouts"])
        top = result["layouts"][0]
        self.assertEqual({"sofa", "tv-unit", "coffee-table"}, {p["catalogId"] for p in top["placements"]})
        doors_only = [o for o in request["openings"] if o["kind"] == "door"]
        self.assertTrue(validate_layout({**request, "openings": doors_only, "placements": top["placements"]})["valid"])
        # The TV never sits on a windowed wall: it stays clear of both y=0 and y=400.
        tv = next(p for p in top["placements"] if p["catalogId"] == "tv-unit")
        self.assertGreater(_centroid(tv)[1], 30)
        self.assertLess(_centroid(tv)[1], 370)

    def test_two_piece_request_without_a_coffee_table_is_accepted(self):
        request = self._request(furniture=[
            {"id": "sofa", "width": 2.2, "depth": 0.9, "required": True},
            {"id": "tv-unit", "width": 1.6, "depth": 0.4, "required": True},
        ])
        top = generate_living_room_layouts(request)["layouts"][0]
        self.assertEqual({"sofa", "tv-unit"}, {p["catalogId"] for p in top["placements"]})

    def test_impossible_room_reports_an_actionable_reason(self):
        tiny = self._request(polygon={"outer": [[0, 0], [180, 0], [180, 160], [0, 160]], "holes": []})
        result = generate_living_room_layouts(tiny)
        self.assertEqual([], result["layouts"])
        self.assertEqual("no-feasible-layout", result["reasons"][0]["code"])
        self.assertTrue(result["reasons"][0]["suggestions"])

    def test_broken_preferences_penalise_the_score_instead_of_blocking(self):
        # A window on every wall: the "TV off a window wall" rule cannot hold.
        walled = self._request(openings=[
            {"id": "door-1", "kind": "door", "span": [[0, 120], [0, 220]], "clearance": 0.9},
            {"id": "w-n", "kind": "window", "span": [[200, 0], [400, 0]]},
            {"id": "w-s", "kind": "window", "span": [[200, 400], [400, 400]]},
            {"id": "w-e", "kind": "window", "span": [[600, 120], [600, 280]]},
            {"id": "w-w", "kind": "window", "span": [[0, 280], [0, 360]]},
        ])
        result = generate_living_room_layouts(walled)
        self.assertTrue(result["layouts"])            # still produces layouts
        top = result["layouts"][0]
        self.assertIn("tv_window_wall", top["penalties"])
        self.assertEqual("review", top["validity"])
        self.assertLess(top["score"], sum(top["scoreComponents"].values()))


class ExtractionTests(unittest.TestCase):
    def test_wall_offset_beyond_tolerance_is_preserved(self):
        segments=[{"id":"a","a":[0,0],"b":[5,0]},{"id":"b","a":[5,6],"b":[8,6]}]
        self.assertEqual([5,0],bounded_snap_segments(segments,4)[0]["b"])

    def test_shared_door_keeps_both_room_ids(self):
        walls=[{"id":"wall","segment":[[0,0],[5,0]]}]
        rooms=[{"id":"a","openingIds":["door"]},{"id":"b","openingIds":["door"]}]
        result=associate_openings([{"id":"door","kind":"door","span":[[2,0],[3,0]]}],walls,rooms)
        self.assertEqual(["a","b"],result[0]["adjacentRoomIds"])

    def test_extraction_and_navigation_masks_are_distinct(self):
        result=extraction_contract({"coordinateSpace":"original-image","rooms":[{"bbox":[0,0,5,5]}]})
        self.assertIn("bridge",result["barrierMaskPolicy"]);self.assertEqual("use-unsealed-openings",result["navigationPolicy"])

if __name__=="__main__":unittest.main()
