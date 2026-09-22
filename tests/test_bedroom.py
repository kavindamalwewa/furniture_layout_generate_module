import math
import unittest
from unittest import mock

from furniture_layout import living_room, polygon_engine
from furniture_layout.bedroom import COMPONENT_MAX, WALL_GAP, generate_bedroom_layouts, has_bed, is_bedroom_type
from furniture_layout.living_room import _polygon_walls
from furniture_layout.polygon_engine import (
    _point_segment_distance, generate_polygon_layouts, rectangle_ring, rings_overlap, validate_layout,
)
from furniture_layout.project import legacy_layouts_to_project


# The room from the reported screenshot: a 3.6 m x 2.1 m bedroom with windows on
# the top and left walls and a door in the bottom-right corner.
SMALL_LEGACY = {
    "room_id": "room-2", "room_name": "Bedroom", "room_type": "bedroom", "room_area_m2": 7.5,
    "scale_m_per_px": 0.0105, "scale_source": "door",
    "bbox_px": {"x": 97, "y": 107, "width": 343, "height": 198},
    "doors": [{"x1": 381, "y1": 308, "x2": 429, "y2": 314}],
    "windows": [{"x1": 159, "y1": 99, "x2": 261, "y2": 105}, {"x1": 89, "y1": 151, "x2": 95, "y2": 252}],
    "layouts": [{"layout_id": "1", "score": 70, "furniture": [
        {"name": "Bed", "x1": 180, "y1": 162, "x2": 368, "y2": 241},
        {"name": "Nightstand", "x1": 125, "y1": 149, "x2": 177, "y2": 172},
        {"name": "Study Table", "x1": 381, "y1": 107, "x2": 411, "y2": 210},
        {"name": "Wardrobe", "x1": 207, "y1": 271, "x2": 326, "y2": 300},
    ]}],
}


def ui_request(legacy=SMALL_LEGACY, **overrides):
    """The request web/layouts-only.html sends after importing a legacy layout JSON."""
    project = legacy_layouts_to_project(legacy)
    room = project["rooms"][0]
    request = {
        "roomId": room["id"], "roomType": room["type"], "polygon": room["polygon"], "scale": project["scale"],
        "openings": project["openings"], "fixedObstacles": [], "count": 6, "seed": 43, "excludeLayouts": [],
        "furniture": [{"id": item[0], "quantity": item[2], "width": item[3], "depth": item[4],
                       "required": item[5], "allowedRotations": [0, 90]} for item in room["furniture"]],
    }
    request.update(overrides)
    return request


def large_request(**overrides):
    """A 4.5 m x 3.8 m master bedroom: window centred on the top wall, door bottom-left."""
    request = {
        "requestId": "br", "roomId": "bedroom-1", "roomType": "bedroom", "inputRevision": 3,
        "polygon": {"outer": [[0, 0], [450, 0], [450, 380], [0, 380]], "holes": []},
        "scale": {"confirmed": True, "metersPerPixel": 0.01},
        "openings": [
            {"id": "door-1", "kind": "door", "span": [[30, 380], [120, 380]], "clearance": 0.9},
            {"id": "win-1", "kind": "window", "span": [[150, 0], [310, 0]]},
        ],
        "fixedObstacles": [],
        "furniture": [
            {"id": "bed", "width": 1.6, "depth": 2.0, "required": True},
            # Listed once per copy, as the legacy import does.
            {"id": "nightstand", "width": 0.45, "depth": 0.4, "required": True},
            {"id": "nightstand", "width": 0.45, "depth": 0.4, "required": True},
            {"id": "wardrobe", "width": 1.5, "depth": 0.6, "required": True},
            {"id": "study-table", "width": 1.2, "depth": 0.6, "required": True},
            {"id": "chair", "width": 0.5, "depth": 0.5, "required": True},
        ],
        "count": 6, "seed": 7,
    }
    request.update(overrides)
    return request


def ring_of(placement):
    return rectangle_ring(placement["x"], placement["y"], placement["width"], placement["depth"], placement["rotation"])


def centroid(placement):
    ring = ring_of(placement)
    return sum(p[0] for p in ring) / 4, sum(p[1] for p in ring) / 4


def piece(layout, catalog_id):
    return next(p for p in layout["placements"] if p["catalogId"] == catalog_id)


def back_wall(placement, polygon):
    """``(wall index, gap)`` for the wall behind a placement's back edge."""
    ring = ring_of(placement)
    middle = ((ring[0][0] + ring[1][0]) / 2, (ring[0][1] + ring[1][1]) / 2)
    walls = _polygon_walls(polygon)
    wall = min(walls, key=lambda item: _point_segment_distance(middle, item["a"], item["b"]))
    return wall["index"], _point_segment_distance(middle, wall["a"], wall["b"])


def window_gap_m(placement, request):
    ring = ring_of(placement)
    edges = list(zip(ring, ring[1:] + ring[:1]))
    units = 1 / request["scale"]["metersPerPixel"]
    gaps = []
    for opening in request["openings"]:
        if opening["kind"] != "window":
            continue
        a, b = opening["span"]
        gaps.append(min([_point_segment_distance(tuple(p), a, b) for p in ring]
                        + [_point_segment_distance(tuple(end), p, q) for end in (a, b) for p, q in edges]) / units)
    return min(gaps)


class BedroomDispatchTests(unittest.TestCase):
    def test_bedrooms_use_the_constructive_engine(self):
        self.assertEqual("constructive-bedroom", generate_polygon_layouts(ui_request())["layouts"][0]["scoreKind"])
        master = generate_polygon_layouts(large_request(roomType="Master Bedroom"))
        self.assertEqual("constructive-bedroom", master["layouts"][0]["scoreKind"])
        inferred = large_request()
        del inferred["roomType"]  # a bed alone marks the request as a bedroom
        self.assertEqual(generate_polygon_layouts(large_request())["layouts"],
                         generate_polygon_layouts(inferred)["layouts"])

    def test_rooms_without_a_bed_keep_the_generic_sampler(self):
        study = {"roomId": "s", "roomType": "bedroom", "scale": {"confirmed": True}, "seed": 4, "count": 2,
                 "polygon": {"outer": [[0, 0], [4, 0], [4, 3], [0, 3]], "holes": []},
                 "furniture": [{"id": "desk", "width": 1.2, "depth": 0.6, "required": True}]}
        first, second = generate_polygon_layouts(study), generate_polygon_layouts(study)
        self.assertEqual("heuristic", first["layouts"][0]["scoreKind"])
        self.assertEqual(first["layouts"], second["layouts"])

    def test_bedrooms_are_scored_by_bedroom_code_alone(self):
        living = {
            "roomId": "lr", "roomType": "living-room", "seed": 5, "count": 2,
            "polygon": {"outer": [[0, 0], [600, 0], [600, 400], [0, 400]], "holes": []},
            "scale": {"confirmed": True, "metersPerPixel": 0.01},
            "openings": [{"id": "door-1", "kind": "door", "span": [[0, 120], [0, 220]], "clearance": 0.9}],
            "furniture": [{"id": "sofa", "width": 2.2, "depth": 0.9}, {"id": "tv-unit", "width": 1.6, "depth": 0.4},
                          {"id": "coffee-table", "width": 1.1, "depth": 0.6}],
        }
        living_layout = generate_polygon_layouts(living)["layouts"][0]
        # The living room keeps its own score components, and no max table, so the page shows it as before.
        self.assertEqual({"door_access", "open_space", "spacing", "sofa_wall", "coffee_table_center",
                          "functional_relationships"}, set(living_layout["scoreComponents"]))
        self.assertNotIn("scoreComponentMax", living_layout)

        # With the living-room scorer disabled, bedrooms still score: they never call it.
        refuse = AssertionError("the living-room scorer must not be used for a bedroom")
        with mock.patch.object(polygon_engine, "_layout_score_components", side_effect=refuse), \
                mock.patch.object(living_room, "_layout_score_components", side_effect=refuse):
            bedroom_layout = generate_polygon_layouts(large_request())["layouts"][0]
        self.assertEqual({"door_access", "open_space", "spacing", "table_near_wall", "nightstand_near_bed"},
                         set(bedroom_layout["scoreComponents"]))
        self.assertEqual(set(COMPONENT_MAX), set(bedroom_layout["scoreComponents"]))

    def test_room_type_and_bed_detection(self):
        self.assertTrue(is_bedroom_type("master-bedroom"))
        self.assertTrue(is_bedroom_type("Guest_Bedroom"))
        self.assertFalse(is_bedroom_type("living-room"))
        self.assertTrue(has_bed([{"id": "queen-bed"}]))
        self.assertFalse(has_bed([{"id": "bedside-table"}, {"id": "wardrobe"}]))


class BedroomRuleTests(unittest.TestCase):
    def setUp(self):
        self.cases = [(ui_request(), generate_polygon_layouts(ui_request())["layouts"]),
                      (large_request(), generate_polygon_layouts(large_request())["layouts"])]

    def test_six_complete_layouts_that_revalidate(self):
        for request, layouts in self.cases:
            self.assertEqual(6, len(layouts))
            expected = {spec["id"] for spec in request["furniture"]}
            doors = [opening for opening in request["openings"] if opening["kind"] == "door"]
            for layout in layouts:
                self.assertEqual(expected, {p["catalogId"] for p in layout["placements"]})
                verdict = validate_layout({**request, "openings": doors, "placements": layout["placements"]})
                self.assertTrue(verdict["valid"], verdict["violations"])

    def test_bed_sits_by_a_window(self):
        for request, layouts in self.cases:
            for layout in layouts:
                self.assertLessEqual(window_gap_m(piece(layout, "bed"), request), 1.0)
                self.assertNotIn("bed_far_from_window", layout["penalties"])
            self.assertLessEqual(window_gap_m(piece(layouts[0], "bed"), request), 0.3)

    def test_furniture_hugs_the_walls_with_the_study_table_on_another_wall(self):
        for request, layouts in self.cases:
            units = 1 / request["scale"]["metersPerPixel"]
            for layout in layouts:
                for placement in layout["placements"]:
                    if placement["catalogId"] == "chair":
                        continue
                    _wall, gap = back_wall(placement, request["polygon"])
                    self.assertLessEqual(gap / units, WALL_GAP + 0.01, placement["id"])
                    if placement["catalogId"] == "bed":   # headboard (short side) on the wall
                        self.assertLessEqual(placement["width"], placement["depth"])
                    else:                                  # long side along the wall
                        self.assertGreaterEqual(placement["width"], placement["depth"])
                bed_wall = back_wall(piece(layout, "bed"), request["polygon"])[0]
                self.assertNotEqual(bed_wall, back_wall(piece(layout, "study-table"), request["polygon"])[0])
                self.assertNotIn("desk_on_bed_wall", layout["penalties"])
                # Flush against a wall that is not the bed's earns the whole table score.
                self.assertEqual(COMPONENT_MAX["table_near_wall"], layout["scoreComponents"]["table_near_wall"])

    def test_the_middle_of_the_room_stays_open(self):
        for request, layouts in self.cases:
            xs = [p[0] for p in request["polygon"]["outer"]]
            ys = [p[1] for p in request["polygon"]["outer"]]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            middle = rectangle_ring(min(xs) + width * .3, min(ys) + height * .3, width * .4, height * .4)
            for layout in layouts:
                for placement in layout["placements"]:
                    if placement["catalogId"] not in {"bed", "chair"}:
                        self.assertFalse(rings_overlap(ring_of(placement), middle), placement["id"])
            self.assertGreaterEqual(layouts[0]["scoreComponents"]["open_space"], 0.75 * COMPONENT_MAX["open_space"])

    def test_nothing_enters_the_door_approach(self):
        small, large = ui_request(), large_request()
        zones = [(small, next(o for o in small["openings"] if o["kind"] == "door")["keepClearPolygon"]),
                 # built on the room side of the bottom wall, 0.1 m wider than the door each side
                 (large, [[20, 380], [130, 380], [130, 290], [20, 290]])]
        for (request, layouts), (_request, zone) in zip(self.cases, zones):
            for layout in layouts:
                for placement in layout["placements"]:
                    self.assertFalse(rings_overlap(ring_of(placement), zone), placement["id"])

    def test_nightstands_flank_the_bed_head_and_the_chair_faces_the_desk(self):
        request, layouts = self.cases[1]
        units = 1 / request["scale"]["metersPerPixel"]
        for layout in layouts:
            bed = piece(layout, "bed")
            stands = [p for p in layout["placements"] if p["catalogId"] == "nightstand"]
            self.assertEqual(["nightstand-1", "nightstand-2"], sorted(p["id"] for p in stands))
            for stand in stands:
                self.assertEqual(back_wall(bed, request["polygon"])[0], back_wall(stand, request["polygon"])[0])
                gap = min(_point_segment_distance(tuple(p), a, b) for p in ring_of(stand)
                          for a, b in zip(ring_of(bed), ring_of(bed)[1:] + ring_of(bed)[:1]))
                self.assertLessEqual(gap / units, 0.05)
            desk, chair = piece(layout, "study-table"), piece(layout, "chair")
            self.assertEqual(180, (chair["rotation"] - desk["rotation"]) % 360)
            self.assertLessEqual(math.dist(centroid(desk), centroid(chair)) / units, 0.3 + 0.25 + 0.1)

    def test_alternatives_are_different_arrangements(self):
        for request, layouts in self.cases:
            units = 1 / request["scale"]["metersPerPixel"]

            def walls(layout):
                return {p["id"]: back_wall(p, request["polygon"])[0]
                        for p in layout["placements"] if p["catalogId"] != "chair"}

            for index, first in enumerate(layouts):
                for second in layouts[index + 1:]:
                    bed_moved = math.dist(centroid(piece(first, "bed")), centroid(piece(second, "bed"))) / units
                    self.assertTrue(bed_moved >= 0.3 or walls(first) != walls(second))

    def test_scores_are_consistent(self):
        for request, layouts in self.cases:
            for layout in layouts:
                components = layout["scoreComponents"]
                self.assertEqual(set(COMPONENT_MAX), set(components))
                self.assertEqual(COMPONENT_MAX, layout["scoreComponentMax"])
                self.assertTrue(all(0 <= components[key] <= COMPONENT_MAX[key] for key in components))
                expected = max(0.0, sum(components.values()) - sum(layout["penalties"].values()))
                self.assertAlmostEqual(expected, layout["score"], places=1)
                self.assertEqual("valid" if not layout["penalties"] else "review", layout["validity"])
            # Beds right by a window rank first; among those the best score leads.
            by_window = [layout for layout in layouts if window_gap_m(piece(layout, "bed"), request) <= 0.3]
            self.assertIn(layouts[0], by_window)
            self.assertEqual(max(layout["score"] for layout in by_window), layouts[0]["score"])


class BedroomGenerationTests(unittest.TestCase):
    def test_generation_is_deterministic_and_regeneration_shows_new_layouts(self):
        first = generate_bedroom_layouts(large_request())
        self.assertEqual(first["layouts"], generate_bedroom_layouts(large_request())["layouts"])
        self.assertTrue(all(layout["inputRevision"] == 3 for layout in first["layouts"]))
        second = generate_bedroom_layouts(large_request(seed=8, excludeLayouts=first["layouts"]))

        def signatures(layouts):
            return {tuple(sorted((p["catalogId"], round(p["x"], 1), round(p["y"], 1), round(p["rotation"]))
                                 for p in layout["placements"])) for layout in layouts}
        self.assertEqual(6, len(second["layouts"]))
        self.assertFalse(signatures(first["layouts"]) & signatures(second["layouts"]))

    def test_a_bed_that_cannot_reach_a_window_is_penalised_not_blocked(self):
        # The only window sits between two doors whose approaches keep the bed away.
        request = large_request(
            polygon={"outer": [[0, 0], [600, 0], [600, 400], [0, 400]], "holes": []},
            openings=[{"id": "d1", "kind": "door", "span": [[600, 20], [600, 140]], "clearance": 1.2},
                      {"id": "d2", "kind": "door", "span": [[600, 260], [600, 380]], "clearance": 1.2},
                      {"id": "w1", "kind": "window", "span": [[600, 160], [600, 240]]}])
        layouts = generate_bedroom_layouts(request)["layouts"]
        self.assertTrue(layouts)
        top = layouts[0]
        self.assertIn("bed_far_from_window", top["penalties"])
        self.assertEqual("review", top["validity"])
        # The penalty grows with distance, so the bed is still pulled towards the window.
        by_gap = sorted(layouts, key=lambda layout: window_gap_m(piece(layout, "bed"), request))
        penalties = [layout["penalties"]["bed_far_from_window"] for layout in by_gap]
        self.assertEqual(sorted(penalties), penalties)
        self.assertLess(penalties[0], penalties[-1])

    def test_windowless_room_does_not_penalise_the_bed(self):
        request = large_request(openings=[{"id": "door-1", "kind": "door", "span": [[30, 380], [120, 380]]}])
        for layout in generate_bedroom_layouts(request)["layouts"]:
            self.assertNotIn("bed_far_from_window", layout["penalties"])

    def test_table_score_is_full_when_no_table_is_requested(self):
        furniture = [spec for spec in large_request()["furniture"] if spec["id"] not in {"study-table", "chair"}]
        for layout in generate_bedroom_layouts(large_request(furniture=furniture))["layouts"]:
            self.assertEqual(COMPONENT_MAX["table_near_wall"], layout["scoreComponents"]["table_near_wall"])

    def test_nightstands_beside_the_bed_score_in_full_and_a_missing_one_scores_zero(self):
        for layout in generate_bedroom_layouts(large_request())["layouts"]:
            self.assertEqual(COMPONENT_MAX["nightstand_near_bed"], layout["scoreComponents"]["nightstand_near_bed"])
        # A 1.8 m x 2.4 m room: the bed fills its only usable wall, leaving no room beside its head.
        narrow = large_request(polygon={"outer": [[0, 0], [180, 0], [180, 240], [0, 240]], "holes": []},
                               openings=[], furniture=[{"id": "bed", "width": 1.6, "depth": 2.0, "required": True},
                                                       {"id": "nightstand", "width": 0.45, "depth": 0.4}])
        top = generate_bedroom_layouts(narrow)["layouts"][0]
        self.assertEqual(0.0, top["scoreComponents"]["nightstand_near_bed"])
        self.assertIn("nightstand_missing", top["penalties"])

    def test_a_room_smaller_than_the_bed_reports_an_actionable_reason(self):
        tiny = large_request(polygon={"outer": [[0, 0], [150, 0], [150, 150], [0, 150]], "holes": []})
        result = generate_bedroom_layouts(tiny)
        self.assertEqual([], result["layouts"])
        self.assertEqual("no-feasible-layout", result["reasons"][0]["code"])
        self.assertIn("bed", result["reasons"][0]["message"])
        self.assertTrue(result["reasons"][0]["suggestions"])

    def test_a_request_without_a_bed_reports_a_reason(self):
        result = generate_bedroom_layouts(large_request(furniture=[{"id": "wardrobe", "width": 1.5, "depth": 0.6}]))
        self.assertEqual([], result["layouts"])
        self.assertIn("needs a bed", result["reasons"][0]["message"])


if __name__ == "__main__":
    unittest.main()
