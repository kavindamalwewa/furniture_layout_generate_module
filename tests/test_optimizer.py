import unittest

from furniture_layout.geometry import blocks_opening, inside_room, rectangles_overlap
from furniture_layout.models import FurnitureItem, Layout, Opening, Placement, Room
from furniture_layout.optimizer import LayoutOptimizer, NoValidLayoutError
from furniture_layout.service import generate_layouts, select_layout


class LayoutOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(
            "room-1", 5.0, 4.0,
            openings=(Opening("door", "south", 0.25, 0.9, 0.9),),
        )
        self.items = [
            FurnitureItem("sofa", "Sofa", "seating", 2.0, 0.9, wall_preferred=True),
            FurnitureItem("table", "Table", "table", 1.0, 0.6),
            FurnitureItem("chair", "Chair", "seating", 0.7, 0.7, quantity=2),
        ]

    def test_generates_ranked_collision_free_layouts(self):
        layouts = LayoutOptimizer(attempts_per_layout=60).generate(self.room, self.items, 3, seed=7)
        self.assertEqual(3, len(layouts))
        self.assertTrue(layouts[0].recommended)
        self.assertGreaterEqual(layouts[0].score, layouts[-1].score)
        for layout in layouts:
            for index, placement in enumerate(layout.placements):
                self.assertTrue(inside_room(placement, self.room))
                self.assertFalse(any(blocks_opening(placement, opening, self.room) for opening in self.room.openings))
                self.assertFalse(any(rectangles_overlap(placement, other) for other in layout.placements[index + 1:]))

    def test_generation_is_deterministic_for_seed(self):
        optimizer = LayoutOptimizer(attempts_per_layout=40)
        first = optimizer.generate(self.room, self.items, 2, seed=99)
        second = optimizer.generate(self.room, self.items, 2, seed=99)
        self.assertEqual([x.to_dict() for x in first], [x.to_dict() for x in second])

    def test_impossible_required_item_reports_failure(self):
        huge = FurnitureItem("bed", "Bed", "bed", 8.0, 8.0)
        with self.assertRaises(NoValidLayoutError):
            LayoutOptimizer(attempts_per_layout=5).generate(self.room, [huge])

    def test_service_and_manual_override(self):
        result = generate_layouts({
            "room": {"id": "r", "width": 4, "length": 4},
            "furniture": [{"id": "desk", "name": "Desk", "category": "desk", "width": 1.2, "length": 0.6}],
            "layout_count": 2,
            "seed": 1,
        })
        self.assertEqual(result["layouts"][0]["id"], result["recommended_layout_id"])
        layouts = [Layout("a", "r", [], 90, recommended=True), Layout("b", "r", [], 80)]
        selected = select_layout(layouts, "b")
        self.assertEqual("b", selected.id)
        self.assertTrue(layouts[1].selected)
        self.assertFalse(layouts[0].selected)


if __name__ == "__main__":
    unittest.main()

