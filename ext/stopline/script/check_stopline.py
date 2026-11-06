"""
Extension check stopline: stop lines are straight lines of exactly 2 points, and traffic_sign / road_marking
regulatory elements reference their stop line correctly

A stop line is a way with type=stop_line, referenced as ref_line by traffic lights, traffic signs or right_of_way
regulatory elements, or as refers by road_marking regulatory elements; the vehicle stops in front of it.

Geometry of every stop line: more than 2 points is a polyline: WARNING, with how far its intermediate points are from
the straight line between its two ends (0 m means they are only redundant points on the line). Fewer than 2 points, or
nodes missing from the map: ERROR.

Structure of the regulatory elements using a stop line (vm-02-02, vm-03-14, as checked by autoware_lanelet2_map_validator):
  traffic_sign  refers the sign (type=traffic_sign, a stop_sign for Autoware's stop_line module), ref_line the stop line
                (type=stop_line); the stop_line module stops at the ref_line
  road_marking  refers exactly one line, the stop line (type=stop_line), and no ref_line; Autoware only uses it as a
                stop line when the line has type=stop_line

See ext/stopline/README.md for details. Run alone:
  python3 check_lanelet_map.py --only-ext stopline
"""

import numpy as np

from utils.geometry import arc_length, fmt_xy, points_in_polygon
from utils.lanelet_map import by_id

# no parameter besides enable / ignore (handled by utils/ext.py); defined so that a misspelled parameter in the
# config raises an error instead of being silently ignored
DEFAULT_CONFIG = {}

STOP_LINE_REG_ELEMS = ("traffic_sign", "road_marking")  # subtypes of the regulatory elements whose structure is checked


def reg_elem_users(ctx):
    """{regulatory element id: [ids of the lanelets referencing it]}"""
    result = {}
    for ll in sorted(ctx.lanelets.values(), key=by_id):
        for rid in ll.reg_elems:
            result.setdefault(rid, []).append(ll.id)
    return result


def stop_line_refs(re_):
    """Ids of the ways a regulatory element uses as stop line: its ref_line, and the refers of a road_marking"""
    return re_.refs("ref_line") + (re_.refs("refers") if re_.subtype == "road_marking" else [])


def stop_line_users(ctx):
    """{stop line way id: [ids of the lanelets referencing a regulatory element that uses it as stop line]}"""
    result = {}
    users = reg_elem_users(ctx)
    for re_ in sorted(ctx.reg_elems.values(), key=by_id):
        for wid in stop_line_refs(re_):
            for lid in users.get(re_.id, ()):
                ids = result.setdefault(wid, [])
                if lid not in ids:
                    ids.append(lid)
    for ids in result.values():
        ids.sort(key=int)
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


def check_geometry(ctx, used_by, out):
    """Checks that every stop line (way with type=stop_line) is a straight line of 2 points; returns how many were checked"""
    lines = sorted((w for w in ctx.ways.values() if w.type == "stop_line"), key=by_id)
    if ctx.only_check_aoi:
        inside = [w for w in lines if not w.missing and inside_aoi(w, ctx.aois)]
        out.info(f"AOI: {len(inside)} of {len(lines)} stop lines fully inside (only these are checked)")
        lines = inside
    if not lines:
        out.info("No stop line (way with type=stop_line) in the map")
        return 0

    n_polylines = n_invalid = 0
    ignored = out.ignored
    for w in lines:
        ids = (w.id, *used_by.get(w.id, ()))
        where = (f"lanelet {', '.join(used_by[w.id])}" if w.id in used_by
                 else "not referenced by any regulatory element")
        finite = w.points[np.isfinite(w.points).all(axis=1)]
        if w.missing:
            n_invalid += bool(out.issue("missing_nodes",
                                        f"stop_line {w.id}: references node(s) {' '.join(w.missing)} missing "
                                        f"from the map | {where}", ids, pos=finite[0] if len(finite) else None))
        elif len(w.node_ids) < 2:
            n_invalid += bool(out.issue("too_few_points",
                                        f"stop_line {w.id}: only {len(w.node_ids)} point(s), a stop line needs 2 | "
                                        f"{where}", ids, pos=finite[0] if len(finite) else None))
        elif len(w.node_ids) > 2:
            dev = deviation(w.points)
            k = int(np.argmax(dev))
            n_polylines += bool(out.issue("polyline",
                                          f"stop_line {w.id}: {len(w.node_ids)} points (polyline), should be a "
                                          f"straight line of 2 points; node {w.node_ids[k]} is {dev[k]:.2f} m off the "
                                          f"line between its ends (length {arc_length(w.points)[-1]:.1f} m) | {where} "
                                          f"at {fmt_xy(w.points[k])}", ids, pos=w.points[k]))

    unused = [w.id for w in lines if w.id not in used_by]
    ignored = out.ignored - ignored
    out.info(f"{len(lines)} stop lines checked: {n_polylines} polylines, {n_invalid} invalid"
             + (f", {ignored} ignored" if ignored else ""))
    if unused:
        out.info(f"stop lines not referenced by any regulatory element: {' '.join(unused)}")
    return len(lines)


def role_ways(ctx, re_, role, issues):
    """Ways of the regulatory element in role; members that are not ways of the map are added to issues"""
    result = []
    for ref in re_.refs(role):
        if ref in ctx.ways:
            result.append(ctx.ways[ref])
        else:
            issues.append(("member_not_way", f"{role} {ref} is not a way of the map"))
    return result


def type_of(way):
    return way.type or "(none)"


def traffic_sign_issues(ctx, re_):
    """
    Issues of a traffic_sign regulatory element [(issue name, text)]: refers the sign (type=traffic_sign), ref_line the
    stop line (type=stop_line). Autoware's stop_line module handles the traffic signs with a stop_sign and stops at
    their ref_line; a traffic sign of another subtype is not required to have a stop line
    """
    issues = []
    refers, ref_lines = role_ways(ctx, re_, "refers", issues), role_ways(ctx, re_, "ref_line", issues)
    signs = [w for w in refers if w.type == "traffic_sign"]
    misplaced = [w for w in refers if w.type == "stop_line"]  # stop lines put in refers instead of ref_line
    if not signs and len(refers) == len(re_.refs("refers")):  # a member missing from the map is already reported
        issues.append(("sign_missing", "no traffic sign (refers way with type=traffic_sign), Autoware does not "
                                       "recognize it as a stop sign"))
    stop_sign = not signs or any(w.subtype == "stop_sign" for w in signs)
    if stop_sign and not re_.refs("ref_line"):
        text = "no stop line (ref_line): Autoware's stop_line module stops at the ref_line, the vehicle does not stop " \
               "at this sign"
        if misplaced:
            text += f"; stop line way {misplaced[0].id} is in role refers, change its role to ref_line"
        issues.append(("sign_no_stop_line", text))
    elif misplaced:
        issues.append(("sign_stop_line_in_refers", f"stop line way {' '.join(w.id for w in misplaced)} is in role "
                                                   f"refers, a stop line belongs in ref_line"))
    for w in refers:
        if w.type not in ("traffic_sign", "stop_line"):
            issues.append(("sign_refers_type", f"refers way {w.id} has type={type_of(w)}, not traffic_sign"))
    for w in ref_lines:
        if w.type != "stop_line":
            issues.append(("sign_ref_line_type", f"ref_line way {w.id} has type={type_of(w)}, not stop_line"))
    return issues


def road_marking_issues(ctx, re_):
    """
    Issues of a road_marking regulatory element [(issue name, text)]: refers exactly one line, the stop line
    (type=stop_line), and no ref_line. Autoware only uses a road_marking as a stop line (e.g. the intersection module)
    when its line has type=stop_line
    """
    issues = []
    lines = role_ways(ctx, re_, "refers", issues)
    n = len(re_.refs("refers"))
    if n != 1:
        issues.append(("marking_refers_count", f"{n} refers, a road_marking must reference exactly one line (its "
                                               f"stop line)"))
    for w in lines:
        if w.type != "stop_line":
            issues.append(("marking_line_type", f"refers way {w.id} has type={type_of(w)}, not stop_line: Autoware "
                                                f"does not use it as a stop line"))
    if re_.refs("ref_line"):
        issues.append(("marking_ref_line", f"ref_line {' '.join(re_.refs('ref_line'))}: a road_marking references "
                                           f"its stop line with refers only, its ref_line is not used"))
    return issues


def locate(ctx, re_):
    """
    Position of an issue of the regulatory element: middle of its stop line (also one in a wrong role), else of its
    first line; None if none
    """
    ways = [m[2] for m in re_.members if m[0] == "way"]
    stop_lines = [w for w in ways if w in ctx.ways and ctx.ways[w].type == "stop_line"]
    for wid in stop_line_refs(re_) + stop_lines + ways:
        if wid in ctx.ways:
            pts = ctx.ways[wid].points
            pts = pts[np.isfinite(pts).all(axis=1)]
            if len(pts):
                return pts.mean(axis=0)
    return None


def check_reg_elems(ctx, out):
    """
    Checks the structure of the traffic_sign / road_marking regulatory elements; returns how many were checked.
    With only_check_AOI, only those whose lines are all fully inside the AOI are checked
    """
    regs = sorted((r for r in ctx.reg_elems.values() if r.subtype in STOP_LINE_REG_ELEMS), key=by_id)
    if ctx.only_check_aoi:
        def inside(re_):
            ways = [ctx.ways[m[2]] for m in re_.members if m[0] == "way" and m[2] in ctx.ways]
            return bool(ways) and all(not w.missing and inside_aoi(w, ctx.aois) for w in ways)
        selected = [r for r in regs if inside(r)]
        out.info(f"AOI: {len(selected)} of {len(regs)} traffic_sign / road_marking regulatory elements fully inside "
                 f"(only these are checked)")
        regs = selected
    if not regs:
        return 0

    users = reg_elem_users(ctx)
    warnings, errors, ignored = out.warnings, out.errors, out.ignored
    for re_ in regs:
        issues = (traffic_sign_issues if re_.subtype == "traffic_sign" else road_marking_issues)(ctx, re_)
        if not issues:
            continue
        lanelets = users.get(re_.id, [])
        ways = [m[2] for m in re_.members if m[0] == "way"]
        ids = (re_.id, *ways, *lanelets)
        pos = locate(ctx, re_)
        where = f"lanelet {', '.join(lanelets)}" if lanelets else "not referenced by any lanelet"
        where += f" at {fmt_xy(pos)}" if pos is not None else ""
        for issue, text in issues:
            out.issue(issue, f"{re_.subtype} {re_.id}: {text} | {where}", ids, pos=pos)

    count = {s: sum(1 for r in regs if r.subtype == s) for s in STOP_LINE_REG_ELEMS}
    ignored = out.ignored - ignored
    out.info(f"{len(regs)} regulatory elements checked ({count['traffic_sign']} traffic_sign, "
             f"{count['road_marking']} road_marking): {out.warnings - warnings} WARNING, {out.errors - errors} ERROR"
             + (f", {ignored} ignored" if ignored else ""))
    return len(regs)


def check(ctx, cfg, out):
    """
    Checks every stop line (way with type=stop_line) of the map, then the traffic_sign / road_marking regulatory
    elements. The ids of an issue are the stop line or the regulatory element with its ways, and the lanelets
    referencing them, so it can be ignored by any of them
    """
    out.header(f"Ext {out.category}: stop lines are straight lines of 2 points, traffic_sign / road_marking "
               f"regulatory elements reference them correctly")
    n_lines = check_geometry(ctx, stop_line_users(ctx), out)
    n_regs = check_reg_elems(ctx, out)
    if out.total == 0 and (n_lines or n_regs):
        done = ([f"all {n_lines} stop lines are straight lines of 2 points"] if n_lines else []) \
            + ([f"all {n_regs} traffic_sign / road_marking regulatory elements reference their stop line correctly"]
               if n_regs else [])
        text = " and ".join(done)
        out.ok(text[0].upper() + text[1:] + (" (except the ignored ones)" if out.ignored else ""))
