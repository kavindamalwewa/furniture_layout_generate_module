# Sparkshift Furniture Layout Generation Module

A dependency-free Python engine that generates multiple rule-based furniture arrangements for a detected rectangular room, scores them, and identifies the recommended layout.

## What it covers

- Room, door, window, furniture, placement, and layout data models
- Room-boundary, furniture-collision, and opening-clearance rules
- 0/90-degree rotation and support for furniture quantities
- Multiple deterministic layout alternatives
- Scores for accessibility, space use, wall alignment, distribution, and completeness
- Highest-score recommendation and manual selection override
- JSON integration boundary suitable for an API/backend

Coordinates use metres. `(0, 0)` is the room's south-west corner. Opening `offset` is measured from the west end of a north/south wall, or the south end of an east/west wall.

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

Then open `http://127.0.0.1:8080`. The interface calls the real optimizer, supports room-type selection, regeneration with a new seed, layout selection, visual placement previews, and score comparison.

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
