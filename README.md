# Sparkshift Furniture Layout Generation Module

A dependency-free Python engine that converts detector pixel output to a metric scene, generates multiple rule-based furniture arrangements for rectangular or polygonal rooms, scores them, and identifies the recommended layout.

## What it covers

- Room polygons, fixed obstacles, doors, windows, furniture, placement, and layout data models
- Polygon-boundary, symmetric furniture-clearance, obstacle, and opening-clearance rules
- 0/90-degree rotation and support for furniture quantities
- Multiple deterministic layout alternatives
- Functional relations such as bed/nightstand, sofa/coffee-table, and sofa/TV
- Semantic deduplication and layout-diversity selection
- Scores for accessibility, functional fit, space use, wall alignment, distribution, and completeness
- Highest-score recommendation and manual selection override
- Legacy metric JSON plus a detector pixel-JSON adapter

Coordinates use metres. `(0, 0)` is the room's south-west corner. Opening `offset` is measured from the west end of a north/south wall, or the south end of an east/west wall.

When `room.polygon` is present, it is authoritative and the width/length rectangle is only its local bounding coordinate frame. Furniture must fit completely inside that polygon.

## Run

```powershell
python -m pip install -e .
sparkshift-layout examples/living_room.json -o generated-layouts.json
```

Without installing the package:

```powershell
$env:PYTHONPATH = "src"
python -m furniture_layout.cli examples/living_room.json
```

## Visual standalone test

This generates a local HTML test report only; it is not the production web application.

```powershell
$env:PYTHONPATH = "src"
python -m furniture_layout.demo
```

Open `output/layout-test-report.html` in a browser to compare the six generated layouts, inspect furniture placement, and review every score component.

## Interactive sample web interface

Run the dependency-free local test server:

```powershell
$env:PYTHONPATH = "src"
python -m furniture_layout.web
```

Then open `http://127.0.0.1:8090`. The interface calls the real optimizer, supports room-type selection, regeneration with a new seed, layout selection, visual placement previews, and score comparison.

If Node.js/npm is installed, the same server can be started with:

```powershell
npm run dev
```

Run the automated module tests with `npm test`.

## Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Backend integration

Call `furniture_layout.generate_layouts(payload)` with the same JSON structure as `examples/living_room.json`. The response contains `recommended_layout_id` and a descending, scored `layouts` array. Store each placement using the SRS `FurnitureLayout` fields: furniture/layout IDs, `position_x`, `position_y`, and `rotation`.

This module intentionally stops at 2D layout generation. Room detection supplies its input, while the selected result is passed to the separate 3D rendering engine.

## Detector-output integration

`generate_layouts()` and the CLI also accept the detector's room-level pixel JSON directly. Required detector fields are:

```json
{
  "room_id": 0,
  "room_type": "Bedroom",
  "scale_m_per_px": 0.019096,
  "bbox_px": {"x": 127, "y": 58, "width": 328, "height": 191},
  "room_polygon_px": [[127, 58], [455, 58], [455, 249], [127, 249]],
  "doors": [{"x1": 399, "y1": 249, "x2": 446, "y2": 256}],
  "windows": [{"x1": 186, "y1": 48, "x2": 284, "y2": 59}]
}
```

Pixel coordinates are converted exactly once to room-local metres and the image Y axis is flipped. If `room_polygon_px` is missing, the adapter uses `bbox_px` only as a compatibility fallback and returns a warning. Do not silently approve that fallback for an irregular room.

Bedroom, living-room, and study-room detector payloads receive authoritative metric furniture defaults when no `furniture` array is supplied. Existing pixel furniture layouts are ignored rather than reused as furniture dimensions.

The additive v2 response retains all original layout fields and adds:

- `schema_version` and `units`
- `coordinate_system`
- `warnings`
- `source_transform` for detector inputs

Optional metric-room fields include `polygon`, `obstacles`, opening `segment`, and top-level `relations`. Relations support `near` and `opposite` constraints with minimum/maximum distances in metres.
