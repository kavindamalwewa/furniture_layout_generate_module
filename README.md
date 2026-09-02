# FloorPlanAI 2D Room and Furniture Workspace

This repository now includes a dependency-free 2D review workspace and deterministic polygon-aware furniture layout engine. The original rectangular integration remains backward compatible.

## Implemented workflow

Open a floor-plan image, import or draw reviewed room geometry, confirm real scale with two points, select a stable room ID, configure furniture, generate distinct layouts, apply and edit a placement, validate, then save or export JSON/PNG. The SVG view uses original, orientation-corrected image coordinates; pointer input is transformed through the inverse SVG view matrix.

Bedroom and Living Room include illustrative catalog presets. Other types use editable custom furniture. Dimensions and clearances are prototype preferences, not building-code claims. Geometry or scale revisions mark dependent layouts stale.

The canonical project JSON stores image dimensions and preprocessing transforms, polygon outer rings and holes, walls, shared openings, zones, fixed obstacles, metric furniture data, rotations/front directions, validation results, score components, seed, and input revision for later 3D consumers.

Room polygons, opening spans, and obstacles are stored in original-image pixels. Furniture catalogue dimensions, `grid`, and opening `clearance` are supplied in metres. A confirmed `metersPerPixel` scale joins those coordinate systems. Generated placements retain `x`, `y`, `width`, and `depth` for drawing over the source image and also include `positionMeters`, `widthMeters`, `depthMeters`, `heightMeters`, and `rotation` for the later 3D stage. See `examples/polygon_layout_request.json` for the generation contract.

## What it covers

- Room, door, window, furniture, placement, and layout data models
- Room-boundary, furniture-collision, and opening-clearance rules
- 0/90-degree rotation and support for furniture quantities
- Multiple deterministic layout alternatives
- Scores for accessibility, space use, wall alignment, distribution, and completeness
- Highest-score recommendation and manual selection override
- JSON integration boundary suitable for an API/backend

Coordinates use metres. `(0, 0)` is the room's south-west corner. Opening `offset` is measured from the west end of a north/south wall, or the south end of an east/west wall.

## Windows / VS Code setup

```powershell
python -m pip install -e .
python -m furniture_layout.web
```

Open `http://127.0.0.1:8090`.

No external package is required for the 2D workspace. Detection is optional. To inspect a trusted YOLO checkpoint, install Ultralytics and configure a server-side path (the browser cannot choose model paths):

```powershell
python -m pip install ultralytics
$env:FLOORPLAN_MODEL_PATH = "C:\path\to\trusted\best.pt"
python -m furniture_layout.web
```

The service reads `model.names` after loading. A corrupt or incompatible checkpoint produces an explicit error; no fallback model or fabricated detection result is used. Detection boxes remain evidence only: Room boxes never become polygons or artificial walls.

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

## Room-type layout engines

A single sampler cannot satisfy bedroom, living room, kitchen, and bathroom rules at once, so `POST /api/layouts/generate` first decides the room type, then dispatches to the engine that matches it. The type comes from the request `roomType` field; if it is absent, a request that asks for both a sofa and a TV is treated as a living room. Anything without a dedicated engine falls back to the generic deterministic grid sampler.

**Living room** (`furniture_layout.living_room`) constructs the layout in a fixed order instead of sampling it. Every living-room rule is a **preference that shapes the ranking and the score, never a filter that blocks a result** — so the engine returns layouts whenever the sofa and TV physically fit:

1. Place the sofa flush against the longest wall, in the widest stretch left clear of door approaches. A sofa may sit under a window; wall-hugging pieces always put their long side along the wall regardless of how the source drawing was oriented.
2. Place the TV unit on a wall, preferring one with no window that faces the sofa. If no window-free wall is available it goes on a windowed wall; if no wall faces the sofa it goes on the nearest one.
3. Place the coffee table on the sofa-to-TV line, trying a few offsets and both orientations. If nothing stays clear it is left out.

Each unmet preference (`sofa_not_longest`, `tv_window_wall`, `tv_not_facing`, `near_door`, `coffee_missing`) subtracts points and is listed under `penalties`; the layout's `validity` is `"review"` when any penalty applied. Every `(sofa wall, TV wall)` pairing is tried, ranked so the ones that satisfy the most rules come first, then a few offset/orientation tweaks per pairing. Results are validated and scored with the shared helpers, so the response contract matches the generic engine (`scoreKind` is `constructive-living-room`); the only arrangement never shipped is furniture overlapping furniture. Layouts with all three pieces are preferred over sofa+TV-only ones. Only the sofa, TV, and coffee table are arranged; other requested items are reported under `assumptions`. `no-feasible-layout` is returned only when the room is genuinely smaller than the sofa or TV at the confirmed scale.

## API actions

Project/import/calibration/generation/validation/save/export actions are available under `/api`. Review helpers preserve bounded wall offsets, ambiguous openings, shared doorway room IDs, and separate sealed extraction versus traversable navigation policies. Server JSON files are stored under `output/projects`.

Post the polygon request to `POST /api/layouts/generate`. For a living room the constructive engine above runs; otherwise the algorithm samples deterministic grid candidates, rejects footprints outside concave polygons or inside holes, rejects collisions with furniture/fixed obstacles/opening keep-clear regions, checks portal circulation, removes duplicate layouts, scores feasible alternatives, and returns the best options in descending score order.

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

or `npm test`.

## Required reference inputs

The requested real inputs are not currently in this repository. Place them without renaming at:

- `examples/legacy/room_0_layouts.json`
- `examples/legacy/room_3_layouts.json`
- `examples/reference/original-floor-plan.<png|jpg>`
- `examples/reference/detector-output.json` (and an optional rendered screenshot)
- `examples/reference/snapped-wall.png`
- Configure the trusted checkpoint with `FLOORPLAN_MODEL_PATH`; do not commit it unless your project policy explicitly permits large model files.

Until an original image plus registration/preprocessing transforms and reviewed polygons are supplied, legacy coordinates are shown only as unregistered candidates and cannot be marked validated. Reported legacy areas are preserved but are not treated as verified measurements.

## Backend integration

Call `furniture_layout.generate_layouts(payload)` with the same JSON structure as `examples/living_room.json`. The response contains `recommended_layout_id` and a descending, scored `layouts` array. Store each placement using the SRS `FurnitureLayout` fields: furniture/layout IDs, `position_x`, `position_y`, and `rotation`.

This module intentionally stops at 2D layout generation. Room detection supplies its input, while the selected result is passed to the separate 3D rendering engine.
