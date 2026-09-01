from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from .service import generate_layouts


COLORS = {
    "seating": "#8b5cf6",
    "table": "#f59e0b",
    "storage": "#06b6d4",
    "bed": "#6366f1",
    "desk": "#10b981",
}


def _svg(room: dict, layout: dict, furniture: dict[str, dict]) -> str:
    scale = min(420 / float(room["width"]), 280 / float(room["length"]))
    width = float(room["width"]) * scale
    height = float(room["length"]) * scale
    shapes: list[str] = []
    polygon = room.get("polygon", [])
    if polygon:
        points = " ".join(
            f"{float(point[0]) * scale:.1f},{height - float(point[1]) * scale:.1f}"
            for point in polygon
        )
        boundary = f'<polygon points="{points}" fill="#f8fafc" stroke="#334155" stroke-width="4"/>'
    else:
        boundary = f'<rect width="{width:.1f}" height="{height:.1f}" fill="#f8fafc" stroke="#334155" stroke-width="4"/>'
    for obstacle in room.get("obstacles", []):
        x = float(obstacle["x"]) * scale
        y = height - (float(obstacle["y"]) + float(obstacle["length"])) * scale
        shapes.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{float(obstacle["width"]) * scale:.1f}" '
            f'height="{float(obstacle["length"]) * scale:.1f}" fill="#ef444455" stroke="#dc2626"/>'
        )
    for placement in layout["placements"]:
        item = furniture[placement["furniture_id"]]
        x = placement["position_x"] * scale
        y = height - (placement["position_y"] + placement["length"]) * scale
        w = placement["width"] * scale
        h = placement["length"] * scale
        color = COLORS.get(item["category"], "#64748b")
        label = html.escape(item["name"])
        shapes.append(
            f'<g><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="5" fill="{color}" fill-opacity=".32" stroke="{color}" stroke-width="2">'
            f'<title>{label} - rotation {placement["rotation"]} degrees</title></rect>'
            f'<text x="{x + w / 2:.1f}" y="{y + h / 2:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle">{html.escape(item["name"][:14])}</text></g>'
        )
    for opening in room.get("openings", []):
        if opening.get("segment"):
            first, second = opening["segment"]
            x1, y1 = float(first[0]) * scale, height - float(first[1]) * scale
            x2, y2 = float(second[0]) * scale, height - float(second[1]) * scale
        else:
            offset = float(opening["offset"]) * scale
            span = float(opening["width"]) * scale
            wall = opening["wall"]
            if wall == "south":
                x1, y1, x2, y2 = offset, height, offset + span, height
            elif wall == "north":
                x1, y1, x2, y2 = offset, 0, offset + span, 0
            elif wall == "west":
                x1, y1, x2, y2 = 0, height - offset, 0, height - offset - span
            else:
                x1, y1, x2, y2 = width, height - offset, width, height - offset - span
        color = "#22c55e" if opening["kind"] == "door" else "#38bdf8"
        shapes.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="7"><title>{opening["kind"]}</title></line>')
    return (
        f'<svg viewBox="-12 -12 {width + 24:.1f} {height + 24:.1f}" role="img" '
        f'aria-label="{html.escape(layout["id"])}">'
        + boundary + "".join(shapes) + "</svg>"
    )


def build_report(payload: dict, result: dict) -> str:
    room = payload["room"]
    furniture = {item["id"]: item for item in payload["furniture"]}
    cards = []
    for index, layout in enumerate(result["layouts"]):
        badge = '<span class="recommended">Recommended</span>' if layout["recommended"] else ""
        breakdown = "".join(
            f'<li><span>{html.escape(name.replace("_", " ").title())}</span><b>{value:.2f}</b></li>'
            for name, value in layout["score_breakdown"].items()
        )
        cards.append(
            f'<article class="card"><header><h2>Layout {chr(65 + index)}</h2>{badge}'
            f'<strong class="score">{layout["score"]:.2f}<small>/100</small></strong></header>'
            f'{_svg(room, layout, furniture)}<ul>{breakdown}</ul>'
            f'<p class="id">{html.escape(layout["id"])}</p></article>'
        )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sparkshift Layout Module Test</title><style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#080b10;color:#e5e7eb}}
body{{max-width:1450px;margin:auto;padding:32px}} h1{{color:#2dd4bf;margin-bottom:4px}} .summary{{color:#94a3b8;margin:0 0 28px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(370px,1fr));gap:20px}} .card{{background:#15191f;border:1px solid #303741;border-radius:18px;padding:18px}}
.card:first-child{{border-color:#2dd4bf;box-shadow:0 0 24px #14b8a633}} header{{display:flex;align-items:center;gap:12px}} h2{{margin:0;flex:1}}
.recommended{{background:#0f766e;color:#ccfbf1;border-radius:999px;padding:6px 10px;font-size:12px}} .score{{font-size:28px;color:#2dd4bf}} .score small{{font-size:11px;color:#94a3b8}}
svg{{display:block;width:100%;height:280px;margin:14px 0;background:#0f141a;border-radius:12px}} svg text{{font-size:10px;fill:#0f172a;font-weight:700}}
ul{{list-style:none;padding:0;margin:0}} li{{display:flex;justify-content:space-between;border-bottom:1px solid #282f38;padding:7px 0;color:#aeb8c6}} li b{{color:#e5e7eb}}
.id{{font:12px ui-monospace,monospace;color:#64748b;margin-bottom:0}} code{{color:#5eead4}}
</style></head><body><h1>Furniture Layout Module Test</h1>
<p class="summary">Room: <b>{html.escape(room['id'])}</b> ({room['width']}m × {room['length']}m) · Generated: <b>{len(result['layouts'])} layouts</b> · Recommended: <code>{html.escape(result['recommended_layout_id'])}</code></p>
<main class="grid">{''.join(cards)}</main></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a standalone visual module test report")
    parser.add_argument("input", type=Path, nargs="?", default=Path("examples/living_room.json"))
    parser.add_argument("-o", "--output", type=Path, default=Path("output/layout-test-report.html"))
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = generate_layouts(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(payload, result), encoding="utf-8")
    print(f"Generated {len(result['layouts'])} layouts")
    print(f"Recommended: {result['recommended_layout_id']} ({result['layouts'][0]['score']}/100)")
    print(f"Visual report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
