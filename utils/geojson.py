"""
GeoJSON output of the issues of a run (json.enabled, --json): map_check_YYYYmmdd_HHMMSS.geojson next to the log.

One Point feature per printed WARNING / ERROR, at its lon/lat (the same position as in the heatmap), with its code,
severity, message, related ids and a key identifying the issue across runs and map versions. It is meant for programs
(comparing two versions of a map, CI, statistics, merging with other validators) and opens as a layer in JOSM / QGIS.
The run itself (map, date, checks, options, counts) is described in the foreign member "mapchecker" of the
FeatureCollection (RFC 7946, section 6.1), which GIS tools ignore.
"""

import json
import math
import time

import numpy as np

from .heatmap import issue_position
from .masque import latlon

FORMAT = 1  # version of the content of this file, increased when fields are renamed or removed

# Element types of the related ids, in the order an id is looked up in the map (lanelets first: in maps made with VMB /
# JOSM, ids are unique across element types)
ID_TYPES = ("lanelet", "regulatory_element", "way", "node")


def id_types(ids, ctx):
    """Related ids of an issue by element type {type: [ids]}, only the types present; "unknown" for ids not in the map"""
    lookup = {"lanelet": ctx.lanelets, "regulatory_element": ctx.reg_elems, "way": ctx.ways, "node": ctx.nodes}
    result = {}
    for i in dict.fromkeys(str(i) for i in ids):
        kind = next((t for t in ID_TYPES if i in lookup[t]), "unknown")
        result.setdefault(kind, []).append(i)
    return result


def _by_number(i):
    try:
        return 0, int(i), i
    except ValueError:
        return 1, 0, i


def issue_key(code, ids, text):
    """
    Identity of an issue across runs and map versions: its code and the sorted ids of the related elements, e.g.
    right_of_way.missing:12494/19041; the message when there is no id. Positions, areas and p are left out since they
    change with small edits of the map. Several issues of a run can share a key (e.g. two problems of the same lanelet)
    """
    ids = sorted({str(i) for i in ids}, key=_by_number)
    return f"{code}:{'/'.join(ids)}" if ids else f"{code}:{text}"


def _number(value, digits):
    return round(float(value), digits) if value is not None and math.isfinite(value) else None


def features(out, ctx, radius):
    """
    GeoJSON features of the issues printed by out (utils.printer.Printer), in the order they were reported.
    radius: as heatmap.radius, see utils.heatmap.issue_position.
    Returns (features, note): note explains why lon/lat are missing, None if they could be computed
    """
    middles = {}
    positions = []
    for r in out.located:
        pos = issue_position(r, ctx.lanelets, radius, middles)
        positions.append(pos if pos is not None and np.isfinite(pos).all() else None)
    located = [k for k, pos in enumerate(positions) if pos is not None]
    lonlat, note = {}, None
    if located:
        try:
            lat_lon = latlon(np.array([positions[k] for k in located]), ctx.nodes)
            lonlat = {k: (lon, lat) for k, (lat, lon) in zip(located, lat_lon)}
        except ValueError as e:
            note = f"no lon/lat: {e}"

    result = []
    for k, r in enumerate(out.located):
        pos = positions[k]
        geometry = None
        if k in lonlat:
            geometry = {"type": "Point", "coordinates": [_number(lonlat[k][0], 8), _number(lonlat[k][1], 8)]}
        result.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "key": issue_key(r.code, r.ids, r.text),
                "code": r.code,
                "severity": r.severity,
                "check": r.category,
                "title": out.codes.title(r.code),
                "message": r.text,
                "ids": id_types(r.ids, ctx),
                "x": _number(pos[0], 2) if pos is not None else None,  # local metric coordinates, as in the log
                "y": _number(pos[1], 2) if pos is not None else None,
                "p": _number(r.p, 3),
            },
        })
    return result, note


def write_geojson(path, out, ctx, run_info, radius):
    """
    Writes the issues printed by out as a GeoJSON FeatureCollection to path.
    run_info: description of the run (map, checks, options), completed here with the date, the map metadata and the
    counts; radius: see features(). Returns (number of issues, number of them without lon/lat, note of features())
    """
    feats, note = features(out, ctx, radius)
    info = {
        "format": FORMAT,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        **run_info,
        "map_meta": ctx.map.meta,
        "summary": {
            "issues": len(feats),
            "warnings": sum(out.count.values()),
            "errors": sum(out.errors.values()),
            "by_code": dict(out.by_code),
            "ignored": {c: n for c, n in out.ignored.items() if n},
            "not_reported": dict(out.disabled),  # codes whose severity is off
        },
    }
    if note:
        info["note"] = note
    collection = {"type": "FeatureCollection", "mapchecker": info, "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")
    return len(feats), sum(1 for feat in feats if feat["geometry"] is None), note


def report_geojson(path, out, ctx, run_info, radius):
    """Writes the GeoJSON output (write_geojson) and notes it in the report"""
    n, unplaced, note = write_geojson(path, out, ctx, run_info, radius)
    out.info(f"GeoJSON: {path} ({n} issues" + (f", {unplaced} without position" if unplaced else "")
             + (f"; {note}" if note else "") + ")")
