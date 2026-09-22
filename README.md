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

A single sampler cannot satisfy bedroom, living room, kitchen, and bathroom rules at once, so `POST /api/layouts/generate` first decides the room type, then dispatches to the engine that matches it. The type comes from the request `roomType` field; if it is absent, a request that asks for both a sofa and a TV is treated as a living room, and one that asks for a bed as a bedroom. Anything without a dedicated engine (including a bedroom request with no bed) falls back to the generic deterministic grid sampler.

**Living room** (`furniture_layout.living_room`) constructs the layout in a fixed order instead of sampling it. Every living-room rule is a **preference that shapes the ranking and the score, never a filter that blocks a result** — so the engine returns layouts whenever the sofa and TV physically fit:

1. Place the sofa flush against the longest wall that contains no door. A sofa may sit under a window; wall-hugging pieces always put their long side along the wall regardless of how the source drawing was oriented.
2. Place the TV unit on a wall outside every door approach zone, preferring a window-free wall that faces the sofa. If no window-free wall is available it goes on a windowed wall; if no wall faces the sofa it goes on the nearest one.
3. Place the coffee table on the sofa-to-TV line, trying a few offsets and both orientations. If nothing stays clear it is left out.

Each unmet preference (`sofa_not_longest`, `tv_window_wall`, `tv_not_facing`, `near_door`, `coffee_missing`) subtracts points and is listed under `penalties`; the layout's `validity` is `"review"` when any penalty applied. Every `(sofa wall, TV wall)` pairing is explored. The best rule-compliant result stays first, while the remaining results favour unused sofa walls, TV walls, wall pairings, and facing/perpendicular TV rules instead of returning six small nudges of one arrangement. Results are validated and scored with the shared helpers, so the response contract matches the generic engine (`scoreKind` is `constructive-living-room`); the only arrangement never shipped is furniture overlapping furniture. Layouts with all three pieces are preferred over sofa+TV-only ones. Only the sofa, TV, and coffee table are arranged; other requested items are reported under `assumptions`. `no-feasible-layout` is returned only when the room is genuinely smaller than the sofa or TV at the confirmed scale.

**Bedroom** (`furniture_layout.bedroom`) also constructs its layouts rather than sampling them, following modern bedroom planning. Every piece stands flush against a wall, so the middle of the room stays open:

1. The bed's headboard goes against a wall as close to a window as the room allows: centred under it, beside it, or with the bed tucked into the corner and running alongside the window wall. A headboard that only partly covers a window scores below one centred on it. The bed never enters a door approach.
2. Nightstands flank the head of the bed on the same wall (a bed tucked into a corner keeps one).
3. The wardrobe and other tall storage stand against a wall, off the windows, with 0.6 m clear in front to open the doors.
4. The study table goes against a different wall from the bed, as close to daylight as possible, with 0.6 m in front; a chair is pulled up to it. Dressers and other items also go against a wall.

Bedrooms are scored by their own code in `furniture_layout.bedroom`; the living room keeps its scorer in `polygon_engine._layout_score_components`, which the bedroom engine never calls. The bedroom score is out of 100:

| Component | Points | Measured as |
| --- | --- | --- |
| `door_access` | 20 | half the clear distance from the furniture to the door approach (0.45 m for full marks), half the share of pieces a walkway from the door reaches |
| `open_space` | 25 | free floor in the middle of the room, the largest clear square, and the share of floor no walkway is cut off from |
| `spacing` | 25 | the clear floor each piece needs — beside and at the foot of the bed, in front of storage and desks, behind a desk chair — half as the average and half as the tightest spot |
| `table_near_wall` | 15 | study and dressing tables flush against a wall, halved for a study table that shares the bed's wall, zero when a requested one found no spot |
| `nightstand_near_bed` | 15 | each nightstand within 0.1 m of the bed on its headboard wall, zero when a requested one found no spot |

Gaps between pairs of pieces are deliberately not measured, because a nightstand beside the bed and a chair at its desk are meant to touch. Bedroom layouts return `scoreComponentMax`, which the web page uses to show each component as a percentage; living-room layouts keep their original components and card.

Which six arrangements are shown is decided separately, by a planning rank (`_RANK_WEIGHTS`) that also weighs the rules the card does not show, such as how close the bed is to a window and how much daylight the study table gets. The six chosen layouts are then handed over in score order. Broken preferences subtract points and set `validity` to `"review"`: `bed_far_from_window` (more than 1 m from every window, growing with distance), `desk_on_bed_wall`, `storage_covers_window`, `blocked_access` (a piece no walkway reaches), `dead_floor` (at least 1 m² cut off, such as behind a bed that runs wall to wall), and `<item>_missing` for a required piece that found no clear spot. Layouts with the bed right by a window rank first, and the six results are different arrangements (a new bed position, or a piece on a different wall) rather than small nudges of one. Repeated furniture ids are merged into one quantity, because the legacy import lists each copy separately.

## API actions

Project/import/calibration/generation/validation/save/export actions are available under `/api`. Review helpers preserve bounded wall offsets, ambiguous openings, shared doorway room IDs, and separate sealed extraction versus traversable navigation policies. Server JSON files are stored under `output/projects`.

Post the polygon request to `POST /api/layouts/generate`. For a living room or a bedroom the constructive engines above run; otherwise the algorithm samples deterministic grid candidates, rejects footprints outside concave polygons or inside holes, rejects collisions with furniture/fixed obstacles/opening keep-clear regions, checks portal circulation, removes duplicate layouts, scores feasible alternatives, and returns the best options in descending score order.

The web page is stamped with a build ID each time it is served. If a browser tab still runs an older copy of the page (for example after the server was updated), `POST /api/layouts/generate` answers `409` and the page asks you to press Ctrl+F5 instead of showing results with the wrong score labels. Requests that do not come from a page served by this server, such as API clients, are never affected.

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
