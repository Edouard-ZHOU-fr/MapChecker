"""
Extension check crosswalk: how far crosswalks extend beyond the road laterally at most, WARNING if too far

The left and right boundaries of a crosswalk lanelet follow the walking direction of pedestrians, i.e. across the
road. Measures the maximum length by which each end of the crosswalk extends beyond the road
(union of all vehicle lanelets overlapping it), WARNING if larger than max_extension, otherwise only the measured
value is printed.

See ext/crosswalk/README.md for details, ext/crosswalk/config/config.yaml for parameters. Run alone:
  python3 check_lanelet_map.py --only-ext crosswalk
"""

import math

import numpy as np

from utils.geometry import arc_length, fmt_xy, points_in_polygon, resample_by_ratio, resample_by_step
from utils.lanelet_map import by_id

# default values for items missing in config.yaml (enable / ignore are handled by utils/ext.py)
DEFAULT_CONFIG = {
    "max_extension": 1.0,
}

LINE_SPACING = 0.1   # spacing of the lines taken across the crosswalk width (m)
STEP = 0.01          # sampling step along each line (m), i.e. the measurement precision


def configure(cfg):
    cfg["max_extension"] = float(cfg["max_extension"])
    if cfg["max_extension"] < 0:
        raise ValueError(f"max_extension must be >= 0, got {cfg['max_extension']}")
    return cfg


def axis_lines(cw):
    """
    Takes a line parallel to the left and right boundaries every ~LINE_SPACING m across the crosswalk width,
    in the same direction as the boundaries
    """
    ratios = np.linspace(0.0, 1.0, 50)
    lb, rb = resample_by_ratio(cw.left, ratios), resample_by_ratio(cw.right, ratios)
    if arc_length((lb + rb) / 2.0)[-1] < STEP:
        return []
    n = max(1, math.ceil(np.linalg.norm(lb - rb, axis=1).max() / LINE_SPACING))
    return [lb + t * (rb - lb) for t in (np.arange(n) + 0.5) / n]


def end_extensions(cw, lanes):
    """
    Maximum length by which the two ends of the crosswalk (start end, finish end) extend beyond the road (union of
    lanes), measured along the crosswalk direction,
    each end is (length, id of the lane at the road edge, whether the end point is inside the road); None if the
    crosswalk overlaps no lane
    """
    best = [None, None]
    for line in axis_lines(cw):
        pts = resample_by_step(line, STEP)
        s = arc_length(pts)
        inside = np.array([points_in_polygon(pts, ll.polygon) for ll in lanes]).reshape(len(lanes), len(pts))
        hit = np.nonzero(inside.any(axis=0))[0]
        if not len(hit):
            continue
        half = (s[1] - s[0]) / 2.0  # the road boundary is taken midway between adjacent sample points
        for side, k in ((0, hit[0]), (1, hit[-1])):
            at_end = k == (0, len(pts) - 1)[side]
            ext = 0.0 if at_end else (s[k] - half if side == 0 else s[-1] - s[k] - half)
            if best[side] is None or ext > best[side][0]:
                best[side] = (ext, lanes[np.nonzero(inside[:, k])[0][0]].id, at_end)
    return None if best[0] is None else best


def check(ctx, cfg, out):
    """
    Prints the maximum length by which the two ends of each crosswalk extend beyond the road, WARNING if over
    cfg["max_extension"].
    With only_check_AOI, only crosswalks fully inside the AOI are checked (the road always uses the lanes of the
    whole map)
    """
    max_ext = cfg["max_extension"]
    out.header(f"Ext {out.category}: how far crosswalks extend beyond the road (max {max_ext:.2f} m)")
    crosswalks = ctx.by_subtype("crosswalk")
    if ctx.only_check_aoi:
        inside = ctx.select(crosswalks)
        out.info(f"AOI: {len(inside)} of {len(crosswalks)} crosswalks fully inside (only these are checked)")
        crosswalks = inside

    not_crossing = []  # crosswalks that overlap no lane
    for cw in sorted(crosswalks.values(), key=by_id):
        exts = end_extensions(cw, ctx.vehicle_near(cw.polygon))
        if exts is None:
            not_crossing.append(cw.id)
            continue
        ends = ((cw.left[0] + cw.right[0]) / 2.0, (cw.left[-1] + cw.right[-1]) / 2.0)
        detail = ", ".join(f"end {fmt_xy(pos)}: " + (f"inside lanelet {lane}" if inside_road else f"{ext:.2f} m beyond lanelet {lane}")
                           for (ext, lane, inside_road), pos in zip(exts, ends))
        worst = max((0, 1), key=lambda side: exts[side][0])
        if exts[worst][0] > max_ext:
            out.warn(f"crosswalk {cw.id}: extends up to {exts[worst][0]:.2f} m beyond the road "
                     f"(max {max_ext:.2f} m) | {detail}", (cw.id,), pos=ends[worst])
        else:
            out.info(f"crosswalk {cw.id}: extends up to {exts[worst][0]:.2f} m beyond the road | {detail}")

    out.info(f"{len(crosswalks)} crosswalks: {out.warnings} extend more than {max_ext:.2f} m beyond the road"
             + (f", {out.ignored} ignored" if out.ignored else ""))
    if not_crossing:
        out.info(f"crosswalks overlapping no vehicle lanelet (not checked): {' '.join(not_crossing)}")
    if out.warnings == 0:
        out.ok(f"No crosswalk extends more than {max_ext:.2f} m beyond the road")
