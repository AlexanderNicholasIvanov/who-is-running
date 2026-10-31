"""State and county outlines for the results map, as ready-to-draw SVG paths.

The source is us-atlas (https://github.com/topojson/us-atlas, ISC license),
the Census Bureau's 1:10,000,000 cartographic boundaries already projected to
Albers USA in a 975x610 frame, with Alaska and Hawaii inset. This module
decodes its TopoJSON once into SVG path strings, so the page needs no map
library. The output is committed under ``web/geo/``; rerun ``build-geo``
only to change the shapes.

    web/geo/states.json          {"viewBox": [...], "shapes": {"06": {"name", "d"}}}
    web/geo/counties/<ST>.json   the same for one state's counties, plus its outline
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from who_is_running.districts import STATE_FIPS
from who_is_running.http import Fetcher

ATLAS = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-albers-10m.json"

Point = tuple[float, float]
Ring = list[Point]


def decode_arcs(topology: dict[str, Any]) -> list[Ring]:
    """Undo TopoJSON's quantization and delta encoding."""
    (sx, sy), (tx, ty) = topology["transform"]["scale"], topology["transform"]["translate"]
    arcs = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def rings_of(geometry: dict[str, Any], arcs: list[Ring]) -> list[Ring]:
    """Every ring (outer and holes) of a Polygon or MultiPolygon."""
    polygons = [geometry["arcs"]] if geometry["type"] == "Polygon" else geometry["arcs"]
    rings = []
    for polygon in polygons:
        for ring_arcs in polygon:
            ring: Ring = []
            for index in ring_arcs:
                arc = arcs[index] if index >= 0 else arcs[~index][::-1]
                ring.extend(arc if not ring else arc[1:])  # arcs share their end points
            rings.append(ring)
    return rings


def svg_path(rings: Iterable[Ring], digits: int) -> str:
    """Rings as an SVG path in relative coordinates (shorter than absolute)."""
    scale = 10**digits
    parts = []
    for ring in rings:
        points = [(round(x * scale), round(y * scale)) for x, y in ring]
        (x0, y0), out = points[0], []
        px, py = x0, y0
        for x, y in points[1:]:
            if (x, y) != (px, py):
                out.append(f"{_num(x - px, digits)} {_num(y - py, digits)}")
                px, py = x, y
        if len(out) >= 2:
            parts.append(f"M{_num(x0, digits)} {_num(y0, digits)}l{' '.join(out)}z")
    return "".join(parts)


def _num(value: int, digits: int) -> str:
    """An integer count of 10**-digits units, written as a short decimal ("-0.5", "12")."""
    text = f"{value / 10**digits:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def bounds(rings: Iterable[Ring]) -> list[float]:
    """SVG viewBox [x, y, width, height] around the rings, with a small margin."""
    xs, ys = zip(*(p for ring in rings for p in ring), strict=True)
    pad = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.02
    x, y = min(xs) - pad, min(ys) - pad
    return [round(x, 2), round(y, 2), round(max(xs) + pad - x, 2), round(max(ys) + pad - y, 2)]


def build(topology: dict[str, Any], out_dir: Path) -> int:
    """Write the shape files. Returns how many county files were written."""
    arcs = decode_arcs(topology)
    states = topology["objects"]["states"]["geometries"]
    counties = topology["objects"]["counties"]["geometries"]

    state_rings = {g["id"]: rings_of(g, arcs) for g in states}
    _write(
        out_dir / "states.json",
        {
            "viewBox": [0, 0, 975, 610],
            "shapes": {
                g["id"]: {
                    "name": g["properties"]["name"],
                    "abbr": STATE_FIPS.get(g["id"], ""),
                    "d": svg_path(state_rings[g["id"]], 1),
                }
                for g in states
            },
        },
    )

    written = 0
    for state_fips, abbr in STATE_FIPS.items():
        if state_fips not in state_rings:
            continue  # Puerto Rico isn't in the atlas
        mine = [g for g in counties if g["id"][:2] == state_fips]
        _write(
            out_dir / "counties" / f"{abbr}.json",
            {
                "viewBox": bounds(state_rings[state_fips]),
                "outline": svg_path(state_rings[state_fips], 2),
                "shapes": {
                    g["id"]: {"name": g["properties"]["name"], "d": svg_path(rings_of(g, arcs), 2)}
                    for g in mine
                },
            },
        )
        written += 1
    return written


def fetch_and_build(fetch: Fetcher, out_dir: Path) -> int:
    return build(json.loads(fetch(ATLAS)), out_dir)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
