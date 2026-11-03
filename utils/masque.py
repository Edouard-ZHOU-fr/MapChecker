"""
Slow-down masque generated from the hotspots (utils/heatmap.py): a separate OSM file in the same format as the
masque_lanelet2.osm edited by hand in VMB, with one area per hotspot:
  4 nodes (lat/lon, local_x, local_y, ele) + a way with type=detection_area, subtype, area=yes, height, slow_down_speed
The area is the smallest rectangle (any orientation) containing the issues of the hotspot, enlarged by margin on each
side. Ids follow the masque files: area k uses nodes 5k+1 ~ 5k+4 and way 5k+5.
"""

import math
from xml.sax.saxutils import quoteattr

import numpy as np

from .config import DEFAULT_CONFIG
from .geometry import fmt_xy

ANGLE_STEP = 0.5  # orientation step (deg) when searching the smallest rectangle
FIT_NODES = 16    # number of nearest map nodes used to compute the lat/lon of a new node


def check_config(cfg):
    """Check / convert the masque section of config.yaml (enabled already decided), raise ValueError on problems"""
    unknown = [k for k in cfg if k not in DEFAULT_CONFIG["masque"]]
    if unknown:
        raise ValueError(f"unknown parameter(s) {', '.join(unknown)}, expected one of "
                         f"{', '.join(DEFAULT_CONFIG['masque'])}")
    cfg = dict(cfg)
    cfg["subtype"] = str(cfg["subtype"] or "").strip()
    if not cfg["subtype"]:
        raise ValueError("subtype must not be empty")
    for key, low in (("slow_down_speed", 0.0), ("height", 0.0), ("margin", 0.0)):
        try:
            cfg[key] = float(cfg[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {cfg[key]!r}") from None
        if cfg[key] < low or (key == "slow_down_speed" and cfg[key] == 0):
            raise ValueError(f"{key} must be {'> 0' if key == 'slow_down_speed' else '>= 0'}, got {cfg[key]:g}")
    return cfg


def min_area_rectangle(pts, margin):
    """
    Smallest rectangle (any orientation) containing the points pts [N, 2] enlarged by margin on each side:
    (corners [4, 2] counterclockwise, (side 1, side 2) m)
    """
    c = pts.mean(axis=0)
    best = None
    for a in np.radians(np.arange(0.0, 90.0, ANGLE_STEP)):
        axes = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])  # columns: u, v (u turned +90°)
        p = (pts - c) @ axes
        lo, hi = p.min(axis=0) - margin, p.max(axis=0) + margin
        area = float(np.prod(hi - lo))
        if best is None or area < best[0] - 1e-9:
            best = (area, axes, lo, hi)
    _, axes, lo, hi = best
    corners = np.array([(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])]) @ axes.T + c
    return corners, tuple(hi - lo)


def latlon(points, nodes):
    """
    lat/lon of local metric points: affine fit of lat/lon on local coordinates over the FIT_NODES nearest map nodes,
    exact to < 1 mm on VMB maps whatever the projection (MGRS etc.), so no projection library is needed.
    Raise ValueError if the map has fewer than 3 nodes with lat/lon
    """
    ref = [n for n in nodes.values() if math.isfinite(n.lat) and math.isfinite(n.lon)
           and math.isfinite(n.x) and math.isfinite(n.y)]
    if len(ref) < 3:
        raise ValueError("the map has fewer than 3 nodes with lat/lon")
    xy = np.array([(n.x, n.y) for n in ref])
    ll = np.array([(n.lat, n.lon) for n in ref])
    result = []
    for q in points:
        near = np.argsort(np.linalg.norm(xy - q, axis=1))[:FIT_NODES]
        coef = np.linalg.lstsq(np.c_[xy[near] - q, np.ones(len(near))], ll[near], rcond=None)[0]
        result.append(coef[-1])  # value of the fit at q
    return np.array(result)


def elevation(points, ctx):
    """ele of each point: that of the nearest node of a vehicle lanelet boundary (any node if there is none), nan if no
    node has ele"""
    ids = {i for ll in ctx.vehicle.values() for i in ll.left_ids + ll.right_ids}
    nodes = [ctx.nodes[i] for i in ids if math.isfinite(ctx.nodes[i].ele)] \
        or [n for n in ctx.nodes.values() if math.isfinite(n.ele)]
    if not nodes:
        return np.full(len(points), math.nan)
    xy = np.array([(n.x, n.y) for n in nodes])
    z = np.array([n.ele for n in nodes])
    return np.array([z[np.argmin(np.linalg.norm(xy - q, axis=1))] for q in points])


def write_masque(path, spots, ctx, cfg):
    """
    spots: issue coordinates of each hotspot ([N, 2] each), in rank order; cfg: result of check_config().
    Returns [(way id, corners [4, 2], (side 1, side 2) m)] of the areas written.
    local_x / local_y are not written when the map itself has none (ctx.map.projected)
    """
    areas = [min_area_rectangle(pts, cfg["margin"]) for pts in spots]
    corners = np.vstack([c for c, _ in areas]).reshape(-1, 2)
    lat_lon, ele = latlon(corners, ctx.nodes), elevation(corners, ctx)

    nodes, ways, result = [], [], []
    for k, (c, size) in enumerate(areas):
        ids = [5 * k + j for j in (1, 2, 3, 4)]
        for j, nid in enumerate(ids):
            i = 4 * k + j
            tags = [] if ctx.map.projected else [("local_x", f"{c[j, 0]:.4f}"), ("local_y", f"{c[j, 1]:.4f}")]
            if math.isfinite(ele[i]):
                tags.append(("ele", f"{ele[i]:.4f}"))
            nodes.append(f'  <node id="{nid}" lat="{lat_lon[i, 0]:.11f}" lon="{lat_lon[i, 1]:.11f}">')
            nodes += [f"    <tag k={quoteattr(t)} v={quoteattr(v)}/>" for t, v in tags]
            nodes.append("  </node>")
        wid = 5 * k + 5
        ways.append(f'  <way id="{wid}">')
        ways += [f'    <nd ref="{nid}"/>' for nid in ids]
        ways += [f"    <tag k={quoteattr(t)} v={quoteattr(v)}/>" for t, v in (
            ("type", "detection_area"), ("subtype", cfg["subtype"]), ("area", "yes"),
            ("height", f"{cfg['height']:g}"), ("slow_down_speed", f"{cfg['slow_down_speed']:g}"))]
        ways.append("  </way>")
        result.append((str(wid), c, size))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(['<?xml version="1.0" encoding="UTF-8"?>', '<osm generator="MapChecker">',
                           '  <MetaInfo format_version="1" map_version="1" validation_version="1"/>',
                           *nodes, *ways, "</osm>", ""]))
    return result


def report_masque(path, spots, ctx, cfg, out):
    """Write the masque of the hotspots spots (see write_masque) and list its areas in the report"""
    if not spots:
        out.info("Masque: no hotspot, not generated")
        return
    try:
        areas = write_masque(path, spots, ctx, cfg)
    except ValueError as e:
        out.info(f"Masque not generated: {e}")
        return
    out.info(f"Masque: {path} ({len(areas)} detection_area, subtype={cfg['subtype']}, "
             f"slow_down_speed={cfg['slow_down_speed']:g}, margin {cfg['margin']:g} m)")
    for k, (wid, corners, size) in enumerate(areas, 1):
        out.info(f"  way {wid}: hotspot #{k}, {max(size):.1f} x {min(size):.1f} m, "
                 f"center {fmt_xy(corners.mean(axis=0))}")
