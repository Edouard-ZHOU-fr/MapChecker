"""
Extension check stopline: a stop line must be a straight line made of exactly 2 points

A stop line is a way with type=stop_line, referenced as ref_line by traffic lights, traffic signs or right_of_way
regulatory elements; the vehicle stops in front of it. A stop line with more than 2 points is a polyline: WARNING,
with how far its intermediate points are from the straight line between its two ends (0 m means they are only
redundant points on the line). Fewer than 2 points, or nodes missing from the map: ERROR.

See ext/stopline/README.md for details. Run alone:
  python3 check_lanelet_map.py --only-ext stopline
"""

import numpy as np

from utils.geometry import arc_length, fmt_xy, points_in_polygon
from utils.lanelet_map import by_id

# no parameter besides enable / ignore (handled by utils/ext.py); defined so that a misspelled parameter in the
# config raises an error instead of being silently ignored
DEFAULT_CONFIG = {}


def stop_line_users(ctx):
    """{stop line way id: [ids of the lanelets referencing a regulatory element that uses it as ref_line]}"""
    result = {}
    for ll in sorted(ctx.lanelets.values(), key=by_id):
        for rid in ll.reg_elems:
            if rid in ctx.reg_elems:
                for wid in ctx.reg_elems[rid].refs("ref_line"):
                    users = result.setdefault(wid, [])
                    if ll.id not in users:
                        users.append(ll.id)
    return result


def deviation(points):
    """
    Distance of each point to the straight line through the first and last points (to the first point if they
    coincide)
    """
    a, d = points[0], points[-1] - points[0]
    length = np.linalg.norm(d)
    if length == 0:
        return np.linalg.norm(points - a, axis=1)
    return np.abs(d[0] * (points[:, 1] - a[1]) - d[1] * (points[:, 0] - a[0])) / length


def inside_aoi(way, aois):
    """All points of the way are inside the AOI polygons"""
    inside = np.zeros(len(way.points), dtype=bool)
    for poly in aois:
        inside |= points_in_polygon(way.points, poly)
    return bool(inside.all())


def check(ctx, cfg, out):
    """
    Checks every stop line (way with type=stop_line) of the map.
    The ids of an issue are the stop line itself and the lanelets referencing it, so it can be ignored by either.
    With only_check_AOI, only stop lines fully inside the AOI are checked
    """
    out.header(f"Ext {out.category}: stop lines must be straight lines of exactly 2 points")
    lines = sorted((w for w in ctx.ways.values() if w.type == "stop_line"), key=by_id)
    if ctx.only_check_aoi:
        inside = [w for w in lines if not w.missing and inside_aoi(w, ctx.aois)]
        out.info(f"AOI: {len(inside)} of {len(lines)} stop lines fully inside (only these are checked)")
        lines = inside
    if not lines:
        out.info("No stop line (way with type=stop_line) in the map")
        return

    used_by = stop_line_users(ctx)
    for w in lines:
        ids = (w.id, *used_by.get(w.id, ()))
        where = (f"lanelet {', '.join(used_by[w.id])}" if w.id in used_by
                 else "not referenced by any regulatory element")
        finite = w.points[np.isfinite(w.points).all(axis=1)]
        if w.missing:
            out.error(f"stop_line {w.id}: references node(s) {' '.join(w.missing)} missing from the map | {where}",
                      ids, pos=finite[0] if len(finite) else None)
        elif len(w.node_ids) < 2:
            out.error(f"stop_line {w.id}: only {len(w.node_ids)} point(s), a stop line needs 2 | {where}",
                      ids, pos=finite[0] if len(finite) else None)
        elif len(w.node_ids) > 2:
            dev = deviation(w.points)
            k = int(np.argmax(dev))
            out.warn(f"stop_line {w.id}: {len(w.node_ids)} points (polyline), should be a straight line of 2 "
                     f"points; node {w.node_ids[k]} is {dev[k]:.2f} m off the line between its ends "
                     f"(length {arc_length(w.points)[-1]:.1f} m) | {where} at {fmt_xy(w.points[k])}",
                     ids, pos=w.points[k])

    unused = [w.id for w in lines if w.id not in used_by]
    out.info(f"{len(lines)} stop lines checked: {out.warnings} polylines, {out.errors} invalid"
             + (f", {out.ignored} ignored" if out.ignored else ""))
    if unused:
        out.info(f"stop lines not referenced by any regulatory element: {' '.join(unused)}")
    if out.warnings == 0 and out.errors == 0:
        out.ok(f"All {len(lines)} stop lines are straight lines of 2 points" if not out.ignored
               else "All stop lines except the ignored ones are straight lines of 2 points")
