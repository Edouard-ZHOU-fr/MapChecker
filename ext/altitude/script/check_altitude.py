"""
Extension check altitude: abrupt points of the elevation (ele) on lane (vehicle lanelet) boundaries, and generation
of the elevation trend chart

Abrupt points (height differences below min_step are not reported):
  step   slope (height difference / horizontal distance) between two adjacent points on a boundary above max_slope
  spike  the two segments before and after a point on a boundary are both steps in opposite directions, i.e. the
         point is higher (or lower) than both the points before and after
  cross  cross slope: height difference between a point on one boundary of a lanelet and the other boundary
         greater than max_cross_slope × their distance
Nodes without ele are reported separately. Output in decreasing order of height difference, the number #k matches
the one in the trend chart.
The trend chart (when plot is true) is saved next to the log; when isShow is true it is shown in a window after the
report, clicking an abrupt point pops up the detail window, see _plot.py.

See ext/altitude/README.md for details, ext/altitude/config/config.yaml for parameters. Run alone:
  python3 check_lanelet_map.py --only-ext altitude
"""

import os
from dataclasses import dataclass

import numpy as np

from utils.geometry import fmt_xy
from utils.lanelet_map import by_id

# default values for items missing in config.yaml (enable / ignore are handled by utils/ext.py)
DEFAULT_CONFIG = {
    "max_slope": 0.15,
    "max_cross_slope": 0.15,
    "min_step": 0.1,
    "plot": True,
    "max_profiles": 12,
    "isShow": False,
}

MIN_STAT_LENGTH = 1.0  # slope stats use only point pairs >= this far apart horizontally (m), too-close slopes are meaningless


def configure(cfg):
    for key in ("max_slope", "max_cross_slope", "min_step"):
        cfg[key] = float(cfg[key])
        if cfg[key] < 0:
            raise ValueError(f"{key} must be >= 0, got {cfg[key]}")
    cfg["max_profiles"] = int(cfg["max_profiles"])
    if cfg["max_profiles"] < 0:
        raise ValueError(f"max_profiles must be >= 0, got {cfg['max_profiles']}")
    for key in ("plot", "isShow"):
        if not isinstance(cfg[key], bool):
            raise ValueError(f"{key} must be true or false, got {cfg[key]!r}")
    if cfg["plot"] or cfg["isShow"]:
        try:
            import matplotlib  # noqa: F401
        except ImportError as e:
            raise ValueError(f"plot / isShow require matplotlib ({e}), or set both to false") from e
    return cfg


@dataclass
class Abrupt:
    """An abrupt point"""
    kind: str        # "step" / "spike" / "cross"
    nodes: tuple     # step: ids of the two adjacent points; spike / cross: id of the point
    dz: float        # height difference (m, signed) step: next point minus previous point; spike: the point minus the line
                     # joining the points before and after; cross: the point minus the other boundary
    dist: float      # horizontal distance (m) step: between the two points; spike: between the points before and after;
                     # cross: from the point to the other boundary
    slope: float     # slope (absolute value), for spike the larger of the two sides
    pos: np.ndarray  # coordinates [x, y]
    users: list      # boundaries of the point [(lanelet id, "left" / "right")], the first is used to draw the elevation profile
    text: str        # output description (without the number)


def pct(x):
    return f"{x:.0%}" if np.isfinite(x) else "vertical"


def boundaries(lanelets):
    """
    Boundaries of the vehicle lanelets, a boundary shared by adjacent lanes is counted only once:
    {way id: (node ids, coordinates [N, 2], elevations [N], [(lanelet id, "left" / "right")])}
    """
    result = {}
    for ll in sorted(lanelets.values(), key=by_id):
        for side, way, ids, pts, z in (("left", ll.left_way, ll.left_ids, ll.left, ll.left_z),
                                       ("right", ll.right_way, ll.right_ids, ll.right, ll.right_z)):
            result.setdefault(way, (ids, pts, z, []))[3].append((ll.id, side))
    return result


def slope_points(pts, z, max_slope, min_step):
    """
    Steps and spikes on one boundary: [(kind, point index, height difference, horizontal distance, slope)]
      step:  segment k (point k -> k+1) with height difference |dz| >= min_step and slope |dz| / distance > max_slope,
             index k
      spike: two adjacent segments are both steps in opposite directions, index is the middle point k+1, height
             difference is its deviation from the line joining the points before and after
    Points without elevation (nan) are not considered
    """
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    dz = np.diff(z)
    with np.errstate(invalid="ignore", divide="ignore"):
        steep = (np.abs(dz) >= min_step) & (np.abs(dz) > max_slope * d)
        slope = np.abs(dz) / d  # inf when the two points coincide (d == 0)
    result, k = [], 0
    while k < len(d):
        if steep[k] and k + 1 < len(d) and steep[k + 1] and dz[k] * dz[k + 1] < 0:
            span = d[k] + d[k + 1]
            t = d[k] / span if span > 0 else 0.5
            result.append(("spike", k + 1, z[k + 1] - (z[k] + t * (z[k + 2] - z[k])), span,
                           max(slope[k], slope[k + 1])))
            k += 2
        else:
            if steep[k]:
                result.append(("step", k, dz[k], d[k], slope[k]))
            k += 1
    return result


def nearest_on_line(pts, line):
    """
    Nearest point on the polyline line (at least 2 points) of each point in pts: (index k of the segment [N],
    ratio t within the segment [N], distance [N])
    """
    a, ab = line[:-1], np.diff(line, axis=0)
    l2 = (ab ** 2).sum(axis=1)
    t = np.clip(((pts[:, None] - a[None]) * ab[None]).sum(axis=-1) / np.where(l2 > 0, l2, 1.0), 0.0, 1.0)
    dist = np.linalg.norm(a[None] + t[..., None] * ab[None] - pts[:, None], axis=-1)  # [N, number of segments]
    k = np.argmin(dist, axis=1)
    i = np.arange(len(pts))
    return k, t[i, k], dist[i, k]


def cross_points(ll):
    """
    Height difference between each point on both boundaries of the lanelet and the other boundary (at the nearest
    point, linearly interpolated along the boundary):
    [(side, node id, coordinates, height difference, distance, end node ids of the nearest segment on the other boundary)]
    """
    result = []
    for side, ids, pts, z, other_ids, other, other_z in (
            ("left", ll.left_ids, ll.left, ll.left_z, ll.right_ids, ll.right, ll.right_z),
            ("right", ll.right_ids, ll.right, ll.right_z, ll.left_ids, ll.left, ll.left_z)):
        if len(other) < 2:
            dist, z_other = np.linalg.norm(pts - other[0], axis=1), np.full(len(pts), other_z[0])
            near = [tuple(other_ids)] * len(pts)
        else:
            k, t, dist = nearest_on_line(pts, other)
            z_other = other_z[k] + t * (other_z[k + 1] - other_z[k])
            near = [(other_ids[j], other_ids[j + 1]) for j in k]
        result += list(zip([side] * len(ids), ids, pts, z - z_other, dist, near))
    return result


def find_abrupt(lanelets, cfg):
    """
    Abrupt points on the boundaries of lanelets ({id: Lanelet}), sorted by height difference, largest first;
    returns (abrupt points [Abrupt], statistics {"slope": slopes between adjacent points, "cross": cross slopes}, only
    those with distance >= MIN_STAT_LENGTH)
    """
    max_slope, max_cross, min_step = cfg["max_slope"], cfg["max_cross_slope"], cfg["min_step"]
    found, stats, seen = [], {"slope": [], "cross": []}, set()
    for ids, pts, z, users in boundaries(lanelets).values():
        where = ", ".join(f"lanelet {lid} {side}" for lid, side in users)
        d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        long_enough = (d >= MIN_STAT_LENGTH) & np.isfinite(np.diff(z))
        stats["slope"] += list(np.abs(np.diff(z))[long_enough] / d[long_enough])
        for kind, k, dz, dist, slope in slope_points(pts, z, max_slope, min_step):
            nodes = (ids[k], ids[k + 1]) if kind == "step" else (ids[k],)
            if frozenset(nodes) in seen:  # two boundaries share the same segment (overlapping ways)
                continue
            seen.add(frozenset(nodes))
            if kind == "step":
                pos = (pts[k] + pts[k + 1]) / 2.0
                text = (f"step between nodes {ids[k]} -> {ids[k + 1]}: {z[k]:.2f} -> {z[k + 1]:.2f} m "
                        f"({dz:+.2f} m over {dist:.2f} m, slope {pct(slope)}, max {pct(max_slope)})")
            else:
                pos = pts[k]
                text = (f"spike at node {ids[k]}: {abs(dz):.2f} m {'above' if dz > 0 else 'below'} nodes "
                        f"{ids[k - 1]} and {ids[k + 1]} ({z[k]:.2f} m, slope up to {pct(slope)}, max {pct(max_slope)})")
            found.append(Abrupt(kind, nodes, dz, dist, slope, pos, users, f"{text} | {where} at {fmt_xy(pos)}"))

    # cross slope: take the point with the largest height difference in each lanelet. Not reported if this point or
    # the corresponding point on the other side is already reported as a step / spike, to avoid reporting a spot twice
    flagged = {n for a in found for n in a.nodes}
    worst = {}  # node id -> [(|height difference|, lanelet id, side, coordinates, height difference, distance,
                #              number of points over max in this lanelet)]
    for ll in sorted(lanelets.values(), key=by_id):
        bad = []
        for side, nid, pos, dz, dist, near in cross_points(ll):
            if dist >= MIN_STAT_LENGTH and np.isfinite(dz):
                stats["cross"].append(abs(dz) / dist)
            # comparisons are False when the elevation is nan, so it is not reported
            if abs(dz) >= min_step and abs(dz) > max_cross * dist and not flagged.intersection((nid,) + near):
                bad.append((abs(dz), side, nid, pos, dz, dist))
        if bad:
            size, side, nid, pos, dz, dist = max(bad, key=lambda b: b[0])
            worst.setdefault(nid, []).append((size, ll.id, side, pos, dz, dist, len(bad)))
    # overlapping lanelets (e.g. at forks) whose largest height difference is at the same point are merged into one,
    # the lanelet with the largest height difference first
    for nid, items in worst.items():
        items.sort(key=lambda b: -b[0])
        _, lid, side, pos, dz, dist, _ = items[0]
        slope = abs(dz) / dist if dist > 0 else np.inf
        where = ", ".join(f"lanelet {i[1]} {i[2]}" + (f" ({i[6]} nodes over max)" if i[6] > 1 else "") for i in items)
        text = (f"cross slope at node {nid}: {abs(dz):.2f} m {'above' if dz > 0 else 'below'} the "
                f"{'right' if side == 'left' else 'left'} boundary {dist:.2f} m away "
                f"(cross slope {pct(slope)}, max {pct(max_cross)}) | {where} at {fmt_xy(pos)}")
        found.append(Abrupt("cross", (nid,), dz, dist, slope, pos, [(i[1], i[2]) for i in items], text))

    found.sort(key=lambda a: (-abs(a.dz), a.kind, a.nodes))
    return found, {k: np.array(v) for k, v in stats.items()}


def missing_elevation(lanelets):
    """Nodes without ele on the boundaries {lanelet id: [node id]}"""
    result = {}
    for ll in sorted(lanelets.values(), key=by_id):
        nodes = [i for i, z in zip(ll.left_ids + ll.right_ids, np.r_[ll.left_z, ll.right_z]) if np.isnan(z)]
        if nodes:
            result[ll.id] = list(dict.fromkeys(nodes))
    return result


def distribution(values):
    if not len(values):
        return "n/a"
    p50, p99 = np.percentile(values, [50, 99])
    return f"median {p50:.1%}, 99% {p99:.1%}"


def check(ctx, cfg, out):
    """
    Reports abrupt points and nodes without ele on vehicle lanelet boundaries, generates the elevation trend chart
    when plot is true.
    With only_check_AOI, only the vehicle lanelets fully inside the AOI are checked
    """
    out.header(f"Ext {out.category}: abrupt elevation changes on lane boundaries (max slope {pct(cfg['max_slope'])}, "
               f"max cross slope {pct(cfg['max_cross_slope'])}, min step {cfg['min_step']:.2f} m)")
    lanelets = ctx.select(ctx.vehicle)
    if ctx.only_check_aoi:
        out.info(f"AOI: {len(lanelets)} of {len(ctx.vehicle)} vehicle lanelets fully inside (only these are checked)")

    found, stats = find_abrupt(lanelets, cfg)
    reported, lines = [], []  # reported abrupt points (not ignored, number is index + 1) and their output line
    for a in found:
        before = out.warnings
        text = f"#{len(reported) + 1} {a.text}"
        out.warn(text, tuple(dict.fromkeys(lid for lid, _ in a.users)), pos=a.pos)
        if out.warnings > before:
            reported.append(a)
            lines.append(f"[WARNING] {text}")

    missing = missing_elevation(lanelets)
    for lid, nodes in missing.items():
        ll = lanelets[lid]
        out.warn(f"lanelet {lid}: no elevation (ele) on node{'s' if len(nodes) > 1 else ''} {' '.join(nodes)}, "
                 f"not checked", (lid,),
                 pos=(ll.left[len(ll.left) // 2] + ll.right[len(ll.right) // 2]) / 2.0)

    z = np.concatenate([np.r_[ll.left_z, ll.right_z] for ll in lanelets.values()] or [np.array([])])
    z = z[np.isfinite(z)]
    if len(z):
        out.info(f"elevation {z.min():.2f} ~ {z.max():.2f} m; slope between adjacent nodes: "
                 f"{distribution(stats['slope'])}; cross slope: {distribution(stats['cross'])} "
                 f"(node pairs >= {MIN_STAT_LENGTH:.0f} m apart)")
    n_nodes = len({i for ll in lanelets.values() for i in ll.left_ids + ll.right_ids})
    by_kind = {kind: sum(a.kind == kind for a in reported) for kind in ("step", "spike", "cross")}
    out.info(f"{len(lanelets)} vehicle lanelets ({n_nodes} nodes) checked: {by_kind['step']} steps, "
             f"{by_kind['spike']} spikes, {by_kind['cross']} cross slopes"
             + (f", {len(missing)} lanelets with nodes without ele" if missing else "")
             + (f", {out.ignored} ignored" if out.ignored else ""))

    if cfg["plot"]:
        if ctx.output_prefix is None:
            out.info("Trend chart not generated: no output directory")
        else:
            from ._plot import save_trend_chart

            path = f"{ctx.output_prefix}_{out.category}.png"
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            save_trend_chart(path, ctx, lanelets, reported, cfg["max_profiles"])
            out.info(f"Trend chart: {path}")
    if cfg["isShow"]:
        from ._plot import open_window

        open_window(ctx, lanelets, reported, lines, cfg["max_profiles"])
        out.info("Trend chart window: opens after the report, click an abrupt point or a lanelet to show its "
                 "elevation profile in a new window")
    if out.warnings == 0:
        out.ok("No abrupt elevation change on the checked lane boundaries")
