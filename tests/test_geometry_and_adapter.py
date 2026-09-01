import unittest

from furniture_layout.geometry import blocks_obstacle, inside_room
from furniture_layout.models import (
    FurnitureItem,
    FurnitureRelation,
    Obstacle,
    Opening,
    Placement,
    Point,
    Room,
)
from furniture_layout.optimizer import LayoutOptimizer
from furniture_layout.service import generate_layouts


class PolygonGeometryTests(unittest.TestCase):
    def test_concave_notch_rejects_rectangle_even_when_corners_are_inside(self):
        room = Room(
            "u-room",
            4.0,
            4.0,
            polygon=tuple(Point(*point) for point in (
                (0, 0), (4, 0), (4, 4), (3, 4),
                (3, 1), (1, 1), (1, 4), (0, 4),
            )),
        )
        crossing_notch = Placement("bed", 1, 0.5, 0.5, 3.0, 1.5, 0)
        valid_arm = Placement("desk", 1, 0.0, 1.0, 0.8, 2.0, 0)
        self.assertFalse(inside_room(crossing_notch, room))
        self.assertTrue(inside_room(valid_arm, room))

    def test_self_intersecting_polygon_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-self-intersecting"):
            Room(
                "bad",
                4.0,
                4.0,
                polygon=(Point(0, 0), Point(4, 4), Point(0, 4), Point(4, 0)),
            )

    def test_unknown_opening_wall_is_not_silently_treated_as_east(self):
        with self.assertRaisesRegex(ValueError, "Unsupported opening wall"):
            Opening("door", "up", 0.0, 0.9)

    def test_generation_respects_polygon_and_fixed_obstacles(self):
        room = Room(
            "l-room",
            4.0,
            4.0,
            polygon=(
                Point(0, 0), Point(4, 0), Point(4, 2),
                Point(2, 2), Point(2, 4), Point(0, 4),
            ),
            obstacles=(Obstacle("column", 0.9, 0.9, 0.5, 0.5, 0.1),),
        )
        item = FurnitureItem("chair", "Chair", "seating", 0.7, 0.7, quantity=2)
        layouts = LayoutOptimizer(attempts_per_layout=25).generate(room, [item], 2, seed=3)
        for layout in layouts:
            self.assertTrue(all(inside_room(placement, room) for placement in layout.placements))
            self.assertTrue(all(
                not blocks_obstacle(placement, room.obstacles[0], item.clearance)
                for placement in layout.placements
            ))


class OptimizerRegressionTests(unittest.TestCase):
    def test_pair_clearance_uses_both_items_and_is_order_independent(self):
        room = Room("room", 4.0, 3.0)
        sofa = FurnitureItem("sofa", "Sofa", "seating", 1.0, 1.0, clearance=0.5)
        table = FurnitureItem("table", "Table", "table", 0.5, 0.5, clearance=0.1)
        placed = [Placement("sofa", 1, 0.0, 0.0, 1.0, 1.0, 0)]
        too_close = Placement("table", 1, 1.2, 0.0, 0.5, 0.5, 0)
        self.assertFalse(LayoutOptimizer._valid(
            too_close, table, room, placed, {"sofa": sofa, "table": table}
        ))

    def test_functional_relationships_reward_useful_grouping(self):
        room = Room("living", 6.0, 5.0)
        items = [
            FurnitureItem("sofa", "Sofa", "seating", 2.0, 0.9),
            FurnitureItem("coffee", "Coffee table", "table", 1.0, 0.6),
        ]
        expanded = [(item, 1) for item in items]
        relation = (FurnitureRelation("coffee", "sofa", "near", 0.3, 0.9),)
        grouped = [
            Placement("sofa", 1, 0.0, 0.0, 2.0, 0.9, 0),
            Placement("coffee", 1, 0.5, 1.3, 1.0, 0.6, 0),
        ]
        unrelated = [
            Placement("sofa", 1, 0.0, 0.0, 2.0, 0.9, 0),
            Placement("coffee", 1, 5.0, 4.0, 1.0, 0.6, 0),
        ]
        good = LayoutOptimizer._score(room, expanded, grouped, relation)
        bad = LayoutOptimizer._score(room, expanded, unrelated, relation)
        self.assertGreater(good["functional_relationships"], bad["functional_relationships"])

    def test_layout_key_ignores_interchangeable_instance_numbers(self):
        first = [
            Placement("chair", 1, 0.0, 0.0, 0.5, 0.5, 0),
            Placement("chair", 2, 2.0, 0.0, 0.5, 0.5, 0),
        ]
        swapped = [
            Placement("chair", 2, 0.0, 0.0, 0.5, 0.5, 90),
            Placement("chair", 1, 2.0, 0.0, 0.5, 0.5, 0),
        ]
        self.assertEqual(LayoutOptimizer._layout_key(first), LayoutOptimizer._layout_key(swapped))


class DetectorAdapterTests(unittest.TestCase):
    def test_attached_style_pixel_payload_is_regenerated_from_metric_catalogue(self):
        result = generate_layouts({
            "room_id": 0,
            "room_name": "Primary Bedroom",
            "room_type": "Bedroom",
            "room_area_m2": 18.25,
            "scale_m_per_px": 0.019096478819847107,
            "scale_source": "door",
            "bbox_px": {"x": 127.0, "y": 58.0, "width": 328.0, "height": 191.0},
            "doors": [
                {"x1": 399.29, "y1": 251.31, "x2": 445.62, "y2": 256.66},
            ],
            "windows": [
                {"x1": 185.52, "y1": 47.56, "x2": 284.11, "y2": 58.5},
            ],
            "layouts": [{"layout_id": 1, "furniture": [{"name": "Bed"}]}],
            "layout_count": 2,
            "attempts_per_layout": 25,
            "seed": 8,
        })
        self.assertEqual(2, result["schema_version"])
        self.assertEqual("m", result["units"])
        self.assertEqual("px", result["source_transform"]["source_units"])
        self.assertTrue(any("bbox fallback" in warning for warning in result["warnings"]))
        self.assertTrue(any("ignored" in warning for warning in result["warnings"]))
        bed = next(
            placement
            for placement in result["layouts"][0]["placements"]
            if placement["furniture_id"] == "bed"
        )
        self.assertEqual({1.6, 2.0}, {bed["width"], bed["length"]})
        for layout in result["layouts"]:
            self.assertLessEqual(layout["score"], 100.0)
            self.assertAlmostEqual(layout["score"], round(sum(layout["score_breakdown"].values()), 2))


if __name__ == "__main__":
    unittest.main()
