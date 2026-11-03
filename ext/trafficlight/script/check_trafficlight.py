"""
Extension check trafficlight: traffic light regulatory elements as Autoware (behavior_velocity traffic_light module)
and milla_traffic_light_matching use them

For each regulatory element with subtype=traffic_light referenced by vehicle lanelets:
  - structure: refers (traffic_light ways) and ref_line (stop line; Autoware only uses the first one)
  - the stop line crosses each lanelet using it, otherwise the module cannot place a stop point (no stop at red)
  - from each lanelet using it, at least one light can be recognized: a camera light is only matched when it faces the
    vehicle (linestring drawn left -> right as seen from the stop line) and stands ahead of the stop line. Without a
    recognized light the module receives no signal, treats it as red and the vehicle never passes
  - a light referenced by several regulatory elements: the camera matching keeps only one of them
  - the stop line extended by stop_line_extend_length crosses another lanelet of the same direction that does not use
    this regulatory element: a module of it still active while driving there stops the vehicle there
  - light height above the stop line, missing traffic_light_type / height, light bulbs far from their traffic light
    (check_light_bulbs)

See ext/trafficlight/README.md for details. Run alone:
  python3 check_lanelet_map.py --only-ext trafficlight
"""

import math

import numpy as np

from utils.geometry import centerline, fmt_xy
from utils.lanelet_map import by_id

DEFAULT_CONFIG = {
    "max_facing_angle": 40.0,         # deg, max_angle_range_deg of milla_traffic_light_matching
    "min_forward_distance": 1.0,      # m, light ahead of the stop line along the lane
    "min_height": 1.5,                # m, light center above the stop line
    "max_height": 8.0,
    "check_light_bulbs": True,        # check the light_bulbs ways of the regulatory elements
    "bulb_max_offset": 1.0,           # m, light bulbs from their traffic light
    "stop_line_extend_length": 5.0,   # m, stop_line_extend_length of behavior_velocity_planner
    "same_direction_angle": 45.0,     # deg, lanelets crossed by the extended stop line closer than this to the
                                      #   direction of the lanelets using it are reported
}

LIGHT_TYPES = ("camera", "v2x")  # traffic_light_type read by milla_traffic_light_matching / the V2X handler


def configure(cfg):
    """Convert the parameters to float and check their ranges"""
    if not isinstance(cfg["check_light_bulbs"], bool):
        raise ValueError(f"check_light_bulbs must be true or false, got {cfg['check_light_bulbs']!r}")
    for key in DEFAULT_CONFIG:
        if key == "check_light_bulbs":
            continue
        try:
            cfg[key] = float(cfg[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {cfg[key]!r}") from None
    if not 0.0 < cfg["max_facing_angle"] < 180.0:
        raise ValueError("max_facing_angle must be in (0, 180)")
    if not 0.0 < cfg["same_direction_angle"] < 90.0:
        raise ValueError("same_direction_angle must be in (0, 90)")
    if cfg["stop_line_extend_length"] < 0.0:
        raise ValueError("stop_line_extend_length must be >= 0")
    if cfg["min_height"] >= cfg["max_height"]:
        raise ValueError("min_height must be less than max_height")
    if cfg["bulb_max_offset"] <= 0.0:
        raise ValueError("bulb_max_offset must be > 0")
    return cfg


def wrap_deg(angle):
    return (angle + 180.0) % 360.0 - 180.0


def crossings(poly, a, b):
    """Where the polyline poly [N, 2] crosses the segment a-b: [(index along poly, point, unit direction of poly)]"""
    p, r = poly[:-1], np.diff(poly, axis=0)
    s = b - a
    den = r[:, 0] * s[1] - r[:, 1] * s[0]
    qp = a - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[:, 0] * s[1] - qp[:, 1] * s[0]) / den
        u = (qp[:, 0] * r[:, 1] - qp[:, 1] * r[:, 0]) / den
    hit = np.nonzero((den != 0) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1))[0]
    return [(i + t[i], p[i] + t[i] * r[i], r[i] / np.linalg.norm(r[i])) for i in hit]


def stop_segments(points, extend):
    """Segments of the stop line, each extended by extend at both ends as planning_utils::extendLine() does"""
    result = []
    for a, b in zip(points[:-1], points[1:]):
        d = b - a
        n = np.linalg.norm(d)
        if n > 0:
            result.append((a - extend * d / n, b + extend * d / n))
    return result


def first_crossing(center, segments):
    """First point along the centerline where it crosses one of the segments: (point, unit heading) or None"""
    hits = [h for a, b in segments for h in crossings(center, a, b)]
    if not hits:
        return None
    _, point, heading = min(hits, key=lambda h: h[0])
    return point, heading


def facing_error(way, heading):
    """
    Angle (deg) between the direction milla_traffic_light_matching takes for the light (linestring direction + 90°,
    which is the driving direction when the light is drawn left -> right as seen from the stop line) and the heading
    of the lane; the matching only keeps lights with |error| < max_angle_range_deg
    """
    d = way.points[-1] - way.points[0]
    return wrap_deg(math.degrees(math.atan2(d[1], d[0]) + math.pi / 2 - math.atan2(heading[1], heading[0])))


def light_height(way):
    """height tag of a traffic light way (box height above its nodes), nan if absent or invalid"""
    try:
        return float(way.tags.get("height", "nan"))
    except ValueError:
        return math.nan


def light_center(way):
    """Center of the light box (x, y, z); z is nan without ele"""
    xy = (way.points[0] + way.points[-1]) / 2
    h = light_height(way)
    z = (way.z[0] + way.z[-1]) / 2 + (h / 2 if np.isfinite(h) else 0.0)
    return xy, z


def box_offset(points, z, way):
    """Distance of each point (xy [N, 2], z [N]) to the light box: segment of the way, from its z up to z + height"""
    a, b = way.points[0], way.points[-1]
    d = b - a
    t = np.clip(((points - a) @ d) / max(float(d @ d), 1e-12), 0.0, 1.0)
    horizontal = np.linalg.norm(points - (a + t[:, None] * d), axis=1)
    lo, hi = z_range(way)
    vertical = np.zeros(len(points))
    known = np.isfinite(z)
    if np.isfinite(lo):
        vertical[known] = np.maximum(0.0, np.maximum(lo - z[known], z[known] - hi))
    return np.hypot(horizontal, vertical)


def z_range(way):
    """(lowest, highest) z of the light box: its nodes up to z + height; nan without ele"""
    if not np.isfinite(way.z).any():
        return math.nan, math.nan
    h = light_height(way)
    return float(np.nanmin(way.z)), float(np.nanmax(way.z)) + (h if np.isfinite(h) else 0.0)


def valid_way(ctx, wid):
    """The way exists, has no missing node and at least 2 points"""
    w = ctx.ways.get(wid)
    return w is not None and not w.missing and len(w.node_ids) >= 2


def check(ctx, cfg, out):
    """
    Checks every regulatory element with subtype=traffic_light. The ids of an issue are the regulatory element and
    the lanelets concerned, so it can be ignored by either. With only_check_AOI, only regulatory elements with a
    vehicle lanelet using them fully inside the AOI are checked
    """
    ext = cfg["stop_line_extend_length"]
    out.header(f"Ext {out.category}: traffic lights as used by the Autoware traffic_light module and the camera "
               f"matching (facing <= {cfg['max_facing_angle']:.0f}°, stop line extended by {ext:g} m)")
    regs = {rid: re for rid, re in ctx.reg_elems.items() if re.subtype == "traffic_light"}
    if not regs:
        out.info("No traffic_light regulatory element in the map")
        return

    users = {rid: [] for rid in regs}
    for ll in sorted(ctx.lanelets.values(), key=by_id):
        for rid in ll.reg_elems:
            if rid in users:
                users[rid].append(ll.id)
    lights_of = {rid: set(re.refs("refers")) for rid, re in regs.items()}
    centers = {}

    def center(lid):
        if lid not in centers:
            centers[lid] = centerline(ctx.lanelets[lid], 0.5)
        return centers[lid]

    pedestrian = [rid for rid in regs if users[rid] and not any(l in ctx.vehicle for l in users[rid])]
    checked = [rid for rid in sorted(regs, key=int) if rid not in pedestrian]
    if ctx.only_check_aoi:
        inside = [rid for rid in checked if any(ctx.in_aoi(l) for l in users[rid] if l in ctx.vehicle)]
        out.info(f"AOI: {len(inside)} of {len(checked)} traffic lights used by a vehicle lanelet inside the AOI "
                 f"(only these are checked)")
        checked = inside

    check_shared_lights(ctx, regs, checked, out)
    lanes = set()
    for rid in checked:
        vehicle_users = [l for l in users[rid] if l in ctx.vehicle]
        lanes.update(vehicle_users)
        check_reg_elem(ctx, cfg, out, regs[rid], vehicle_users, center)
        check_other_lanes(ctx, cfg, out, regs[rid], vehicle_users, users, lights_of, center)

    out.info(f"{len(checked)} traffic lights used by {len(lanes)} vehicle lanelets checked: {out.errors} error(s), "
             f"{out.warnings} warning(s)" + (f", {out.ignored} ignored" if out.ignored else ""))
    if pedestrian:
        out.info(f"used only by non-vehicle lanelets (e.g. pedestrian signals of crosswalks), not checked: "
                 f"{' '.join(sorted(pedestrian, key=int))}")
    referenced = set().union(*lights_of.values())
    unused = sorted((w.id for w in ctx.ways.values() if w.type == "traffic_light" and w.id not in referenced), key=int)
    if unused:
        out.info(f"traffic_light ways not referenced by any regulatory element: {' '.join(unused)}")
    if out.warnings == 0 and out.errors == 0:
        out.ok(f"All {len(checked)} traffic lights can be used by Autoware" if not out.ignored
               else "All traffic lights except the ignored ones can be used by Autoware")


def check_shared_lights(ctx, regs, checked, out):
    """A traffic light way referenced by several traffic_light regulatory elements"""
    owners = {}
    for rid in sorted(regs, key=int):
        for wid in regs[rid].refs("refers"):
            owners.setdefault(wid, []).append(rid)
    for wid, rids in sorted(owners.items(), key=lambda kv: int(kv[0])):
        if len(rids) > 1 and any(r in checked for r in rids):
            w = ctx.ways.get(wid)
            pos = light_center(w)[0] if w is not None and valid_way(ctx, wid) else None
            out.error(f"traffic light way {wid} is referenced by traffic_light {', '.join(rids)}: the camera matching "
                      f"keeps a single (light, regulatory element) pair per light, the others never get its state "
                      f"and are treated as red; merge them into one regulatory element", tuple(rids), pos=pos)


def check_reg_elem(ctx, cfg, out, re, lanelets, center):
    """Structure, stop line on each lanelet, recognition of the lights from each lanelet, height and bulbs"""
    ids = (re.id, *lanelets)
    name = f"traffic_light {re.id}"
    lights = [ctx.ways[w] for w in re.refs("refers") if valid_way(ctx, w)]
    for wid in re.refs("refers"):
        if not valid_way(ctx, wid):
            reason = "missing from the map" if wid not in ctx.ways else "with missing nodes or fewer than 2 points"
            out.error(f"{name}: traffic light way {wid} {reason}", ids)
        elif ctx.ways[wid].type != "traffic_light":
            out.warn(f"{name}: refers way {wid} has type={ctx.ways[wid].type or '(none)'}, not traffic_light", ids,
                     pos=light_center(ctx.ways[wid])[0])
    if not re.refs("refers"):
        out.error(f"{name}: no traffic light (refers)", ids)

    ref_lines = re.refs("ref_line")
    stop = ctx.ways[ref_lines[0]] if ref_lines and valid_way(ctx, ref_lines[0]) else None
    stop_pos = stop.points.mean(axis=0) if stop is not None else (light_center(lights[0])[0] if lights else None)
    if not ref_lines:
        out.error(f"{name}: no stop line (ref_line): the traffic_light module is not created, vehicles do not stop at "
                  f"red", ids, pos=stop_pos)
    elif stop is None:
        out.error(f"{name}: stop line way {ref_lines[0]} is missing from the map or invalid", ids, pos=stop_pos)
    else:
        if len(ref_lines) > 1:
            out.warn(f"{name}: {len(ref_lines)} ref_line, only the first one (way {stop.id}) is used as stop line",
                     ids, pos=stop_pos)
        if stop.type != "stop_line":
            out.warn(f"{name}: ref_line way {stop.id} has type={stop.type or '(none)'}, not stop_line", ids,
                     pos=stop_pos)
    if not lanelets:
        out.warn(f"{name}: not referenced by any lanelet, no traffic_light module is ever created for it", ids,
                 pos=stop_pos)

    check_light_tags(cfg, out, name, ids, lights, stop)
    check_bulbs(ctx, cfg, out, re, name, ids, lights)
    if stop is not None and lights:
        check_recognition(ctx, cfg, out, name, re, lanelets, lights, stop, center)


def check_light_tags(cfg, out, name, ids, lights, stop):
    """traffic_light_type, height tag, and height of the light above the stop line"""
    stop_z = float(np.nanmean(stop.z)) if stop is not None and np.isfinite(stop.z).any() else math.nan
    for w in lights:
        pos = light_center(w)[0]
        kind = w.tags.get("traffic_light_type")
        if kind is None:
            out.warn(f"{name}: traffic light way {w.id} has no traffic_light_type: used neither by the camera matching "
                     f"(camera) nor by the V2X handler (v2x); reading it, the camera matching skips the following "
                     f"lights of this regulatory element and the V2X handler all the following traffic lights of the "
                     f"map", ids, pos=pos)
        elif kind not in LIGHT_TYPES:
            out.warn(f"{name}: traffic light way {w.id} has traffic_light_type={kind}, used neither by the camera "
                     f"matching (camera) nor by the V2X handler (v2x)", ids, pos=pos)
        if kind == "camera" and not light_height(w) > 0:
            out.warn(f"{name}: camera traffic light way {w.id} has no valid height tag, the camera matching projects "
                     f"a box of height 0", ids, pos=pos)
        z = light_center(w)[1]
        if np.isfinite(z) and np.isfinite(stop_z) and not cfg["min_height"] <= z - stop_z <= cfg["max_height"]:
            out.warn(f"{name}: traffic light way {w.id} is {z - stop_z:.2f} m above the stop line (expected "
                     f"{cfg['min_height']:g} ~ {cfg['max_height']:g} m), the camera matching looks for it at this "
                     f"height", ids, pos=pos)


def check_bulbs(ctx, cfg, out, re, name, ids, lights):
    """Each light_bulbs way lies on (one of) the traffic lights of the regulatory element (if check_light_bulbs)"""
    if not cfg["check_light_bulbs"] or not lights:
        return
    for bid in re.refs("light_bulbs"):
        bulbs = ctx.ways.get(bid)
        if bulbs is None or not bulbs.node_ids or bulbs.missing:
            out.error(f"{name}: light_bulbs way {bid} is missing from the map or has missing nodes", ids)
            continue
        offsets = {w.id: float(box_offset(bulbs.points, bulbs.z, w).max()) for w in lights}
        wid = min(offsets, key=offsets.get)
        if offsets[wid] > cfg["bulb_max_offset"]:
            heights = ""
            if np.isfinite(bulbs.z).any() and np.isfinite(z_range(ctx.ways[wid])[0]):
                lo, hi = z_range(ctx.ways[wid])
                heights = (f": bulbs z {np.nanmin(bulbs.z):.2f} ~ {np.nanmax(bulbs.z):.2f}, "
                           f"light z {lo:.2f} ~ {hi:.2f}")
            out.warn(f"{name}: light_bulbs way {bid} is up to {offsets[wid]:.2f} m from traffic light way {wid} "
                     f"(max {cfg['bulb_max_offset']:g} m){heights}", ids, pos=bulbs.points.mean(axis=0))


def stop_crossing(ctx, lid, segments, center):
    """
    Where the path along lanelet lid crosses the stop line segments, also looking at its successors and predecessors
    (stop line at the very end / start of the lanelet): (lanelet id, point, unit heading) or None
    """
    succ, pred = ctx.topology
    for cand in [lid, *sorted(succ.get(lid, ()), key=int), *sorted(pred.get(lid, ()), key=int)]:
        if cand in ctx.lanelets:
            hit = first_crossing(center(cand), segments)
            if hit:
                return (cand, *hit)
    return None


def check_recognition(ctx, cfg, out, name, re, lanelets, lights, stop, center):
    """
    From each lanelet: the stop line crosses it (else no stop point) and at least one light can be recognized
    (else no signal, treated as red: the vehicle never passes). Lights that cannot be recognized from some lanelets
    while others can are reported once per light
    """
    ext = cfg["stop_line_extend_length"]
    raw, extended = stop_segments(stop.points, 0.0), stop_segments(stop.points, ext)
    v2x = [w for w in lights if w.tags.get("traffic_light_type") == "v2x"]
    cameras = [w for w in lights if w.tags.get("traffic_light_type") == "camera"]
    unseen = {w.id: [] for w in cameras}  # light -> [(lanelet, reason)] for lanelets it cannot be recognized from
    for lid in lanelets:
        ids = (re.id, lid)
        hit = stop_crossing(ctx, lid, raw, center)
        if hit is None:
            hit = stop_crossing(ctx, lid, extended, center)
            if hit is None:
                out.error(f"{name} | lanelet {lid}: stop line {stop.id} does not cross the lanelet (nor its "
                          f"predecessors / successors), even extended by {ext:g} m: the traffic_light module cannot "
                          f"place a stop point, vehicles on it do not stop at red", ids, pos=stop.points.mean(axis=0))
                continue
            out.warn(f"{name} | lanelet {lid}: stop line {stop.id} only crosses the lanelet once extended by {ext:g} m "
                     f"(stop_line_extend_length) at {fmt_xy(hit[1])}", ids, pos=hit[1])
        _, point, heading = hit

        problems = {}
        for w in cameras:
            reasons = []
            err = facing_error(w, heading)
            if abs(err) > cfg["max_facing_angle"]:
                fix = "; drawn right -> left, reverse its node order" if 180 - abs(err) <= cfg["max_facing_angle"] else ""
                reasons.append(f"faces away, facing error {err:+.0f}° (max {cfg['max_facing_angle']:.0f}°){fix}")
            forward = float((light_center(w)[0] - point) @ heading)
            if forward < cfg["min_forward_distance"]:
                reasons.append(f"{-forward:.1f} m behind the stop line" if forward < 0
                               else f"only {forward:.1f} m ahead of the stop line (min {cfg['min_forward_distance']:g} m)")
            if reasons:
                problems[w.id] = "; ".join(reasons)
        usable = [w.id for w in cameras if w.id not in problems] + [w.id for w in v2x]
        if usable:
            for wid, reason in problems.items():
                unseen[wid].append((lid, reason))
            continue
        detail = ", ".join(f"light {wid}: {reason}" for wid, reason in problems.items()) or (
            "no light with traffic_light_type camera or v2x")
        out.error(f"{name} | lanelet {lid}: no light can be recognized from the lanelet ({detail}): the traffic_light "
                  f"module gets no signal, treats it as red and vehicles on lanelet {lid} never pass the stop line at "
                  f"{fmt_xy(point)}", ids, pos=point)

    for wid, where in unseen.items():
        if where:
            by_reason = {}
            for lid, reason in where:
                by_reason.setdefault(reason, []).append(lid)
            lanes = ", ".join(f"lanelet {', '.join(lids)} ({reason})" for reason, lids in by_reason.items())
            out.warn(f"{name}: traffic light way {wid} cannot be recognized from {lanes}; only the other light(s) of "
                     f"the regulatory element are used there", (re.id, *(lid for lid, _ in where)),
                     pos=light_center(ctx.ways[wid])[0])


def check_other_lanes(ctx, cfg, out, re, lanelets, users, lights_of, center):
    """
    The stop line extended by stop_line_extend_length crosses another lanelet going in the same direction that does
    not use this regulatory element (nor one sharing a light with it). The traffic_light module intersects its
    extended stop line with whatever path the vehicle follows, so while it is active on that lanelet (one of its
    lanelets in the path, or a module left over: modules are only removed when no traffic light is left on the path),
    the vehicle stops there by the lights of this regulatory element
    """
    ref_lines = re.refs("ref_line")
    if not lanelets or not ref_lines or not valid_way(ctx, ref_lines[0]):
        return
    ext = cfg["stop_line_extend_length"]
    stop = ctx.ways[ref_lines[0]]
    raw, extended = stop_segments(stop.points, 0.0), stop_segments(stop.points, ext)
    if not extended:
        return
    headings = [hit[2] for hit in (stop_crossing(ctx, lid, extended, center) for lid in lanelets) if hit]
    if not headings:
        return
    succ, pred = ctx.topology
    related = set(lanelets)
    for lid in lanelets:
        related |= succ.get(lid, set()) | pred.get(lid, set())
    same_lights = {rid for rid, lights in lights_of.items() if lights & lights_of[re.id]}
    min_cos = math.cos(math.radians(cfg["same_direction_angle"]))

    for other in ctx.vehicle_near(np.vstack([p for seg in extended for p in seg])):
        if other.id in related or other.reg_elems & same_lights:
            continue
        hit = first_crossing(center(other.id), extended)
        if hit is None or max(float(hit[1] @ h) for h in headings) < min_cos:
            continue
        point = hit[0]
        how = "the stop line itself" if first_crossing(center(other.id), raw) else f"once extended by {ext:g} m"
        tls = sorted((r for r in other.reg_elems if r in users), key=int)
        module = f"a module of {re.id} active on lanelet {other.id} stops the vehicle there"
        if tls:
            advice = (f"which uses traffic_light {', '.join(tls)}: {module} by the lights of {re.id}; merge them into "
                      f"one regulatory element if they show the same phase")
        else:
            advice = (f"which uses no traffic light: if it faces the same signal, add traffic_light {re.id} to it "
                      f"(vehicles on it do not stop at red); otherwise {module}")
        out.warn(f"traffic_light {re.id} (lanelet {', '.join(lanelets)}): stop line {stop.id} crosses lanelet "
                 f"{other.id} (same direction, {how}) at {fmt_xy(point)}, {advice}",
                 (re.id, *lanelets, other.id), pos=point)
