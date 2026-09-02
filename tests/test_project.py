import json
import tempfile
import unittest
from pathlib import Path

from furniture_layout.polygon_engine import _layout_score_components, _semantic_verdict, footprint_inside, generate_polygon_layouts, rectangle_ring, validate_layout
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
