"""
Extension check border_sharing: side-by-side lanelets must share the boundary between them

In Lanelet2 / Autoware, two lanelets are adjacent only if the boundary between them is the same linestring (way):
lanelets in the same direction then allow lane changes, lanelets in opposite directions share one center line.
When each lanelet has its own way at the same place, the routing graph does not see them as adjacent, and the two
lines usually leave a small gap or overlap between the lanes (an overlap is also reported as a crossing by Check 2).

Checks each pair of vehicle lanelets whose facing boundaries (right / left in the same direction, left / left or
right / right in opposite directions) are different ways that coincide: closer than max_distance, along at least
min_fraction of the shorter of the two boundaries and at least min_length meters. Boundaries close over only a part
of their length (e.g. turning lanes passing each other in an intersection) are not reported.

See ext/border_sharing/README.md for details. Run alone:
  python3 check_lanelet_map.py --only-ext border_sharing
"""

import numpy as np

from utils.geometry import arc_length, fmt_xy, resample_by_step
from utils.lanelet_map import by_id

# default values for items missing in config.yaml (enable / ignore are handled by utils/ext.py)
DEFAULT_CONFIG = {
    "max_distance": 1.0,
    "min_fraction": 0.8,
    "min_length": 2.0,
}

STEP = 0.5          # sampling step along the boundaries (m)
MIN_COS = 0.9       # the two boundaries are parallel (or antiparallel) within about 25°
# facing boundaries of side-by-side lanelets: (side of a, side of b, 1 same direction / -1 opposite directions)
FACING = (("right", "left", 1), ("left", "right", 1), ("left", "left", -1), ("right", "right", -1))


def configure(cfg):
    for key in DEFAULT_CONFIG:
        cfg[key] = float(cfg[key])
    if cfg["max_distance"] <= 0 or cfg["min_length"] < 0 or not 0 < cfg["min_fraction"] <= 1:
        raise ValueError("need max_distance > 0, min_length >= 0 and 0 < min_fraction <= 1")
    return cfg


def _project(p, line):
    """Foot of each point p on the polyline line, and the unit direction of the segment of the foot"""
    a, ab = line[:-1], np.diff(line, axis=0)
    len2 = np.maximum((ab ** 2).sum(-1), 1e-12)
    t = np.clip(((p[:, None] - a[None]) * ab[None]).sum(-1) / len2, 0.0, 1.0)
    feet = a[None] + t[..., None] * ab[None]
    k = np.argmin(np.linalg.norm(p[:, None] - feet, axis=-1), axis=1)
    return feet[np.arange(len(p)), k], ab[k] / np.sqrt(len2[k])[:, None]


class Boundary:
    """A boundary of a lanelet sampled every STEP m, oriented in the driving direction"""

    def __init__(self, ll, side):
        self.ll, self.side = ll, side
        self.way = ll.left_way if side == "left" else ll.right_way
        self.line = ll.left if side == "left" else ll.right
        self.pts = resample_by_step(self.line, STEP)
        d = np.gradient(self.pts, axis=0)
        self.tangent = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
        # normal pointing out of the lanelet
        self.outward = self.tangent @ (np.array([[0, -1], [1, 0]]) if side == "right" else np.array([[0, 1], [-1, 0]]))
        self.seg = np.linalg.norm(np.diff(self.pts, axis=0), axis=1)  # length of each sampling interval
        self.length = arc_length(self.line)[-1]
        self.lo, self.hi = self.line.min(axis=0), self.line.max(axis=0)


def coincidence(a, b, sign, max_distance):
    """
    Where boundary b coincides with boundary a: (length m, median signed distance m (> 0 gap, < 0 overlap),
    middle point), measured on the samples of a closer than max_distance to b and parallel (sign 1) / antiparallel
    (sign -1) to it; None if they never coincide
    """
    if np.any(a.lo > b.hi + max_distance) or np.any(b.lo > a.hi + max_distance):
        return None
    feet, direction = _project(a.pts, b.line)
    offset = feet - a.pts
    near = (np.linalg.norm(offset, axis=1) <= max_distance) & (sign * (a.tangent * direction).sum(1) >= MIN_COS)
    if not near.any():
        return None
    signed = (offset[near] * a.outward[near]).sum(1)
    length = float(a.seg[near[:-1] & near[1:]].sum())  # intervals whose two ends both coincide
    return length, float(np.median(signed)), a.pts[near][near.sum() // 2]


def relative(signed):
    """How the two lines lie relative to each other, from the median signed distance"""
    if abs(signed) < 0.005:
        return "drawn on top of each other"
    return f"{'gap' if signed > 0 else 'overlap'} {abs(signed):.2f} m between the lanes"


def check(ctx, cfg, out):
    """
    Pairs of vehicle lanelets (with only_check_AOI: both fully inside the AOI) whose bounding boxes are close;
    the ids of an issue are the two lanelets
    """
    max_d, min_frac, min_len = cfg["max_distance"], cfg["min_fraction"], cfg["min_length"]
    out.header(f"Ext {out.category}: side-by-side lanelets sharing their boundary (coinciding within {max_d:.2f} m "
               f"along >= {min_frac:.0%} of the boundary)")
    lanelets = sorted(ctx.select(ctx.vehicle).values(), key=by_id)
    if ctx.only_check_aoi:
        out.info(f"AOI: {len(lanelets)} of {len(ctx.vehicle)} vehicle lanelets fully inside (only these are checked)")
    bounds = {(ll.id, side): Boundary(ll, side) for ll in lanelets for side in ("left", "right")}
    box = np.array([np.r_[ll.polygon.min(axis=0), ll.polygon.max(axis=0)] for ll in lanelets]).reshape(-1, 4)

    n_shared = 0
    for i, a in enumerate(lanelets):
        near = np.nonzero((box[i + 1:, 0] <= box[i, 2] + max_d) & (box[i + 1:, 2] >= box[i, 0] - max_d)
                          & (box[i + 1:, 1] <= box[i, 3] + max_d) & (box[i + 1:, 3] >= box[i, 1] - max_d))[0] + i + 1
        for j in near:
            b = lanelets[j]
            for side_a, side_b, sign in FACING:
                ba, bb = bounds[(a.id, side_a)], bounds[(b.id, side_b)]
                found = coincidence(ba, bb, sign, max_d)
                if found is None:
                    continue
                length, signed, pos = found
                shorter = min(ba.length, bb.length)
                if length < min_len or length < min_frac * shorter:
                    continue
                if ba.way == bb.way:
                    n_shared += 1
                    continue
                pair = f"lanelet {a.id} {side_a} (way {ba.way}) / lanelet {b.id} {side_b} (way {bb.way})"
                consequence = ("they are not adjacent in the routing graph, no lane change between them" if sign > 0
                               else "opposite lanes should share one center line")
                out.warn(f"{pair}{', opposite directions' if sign < 0 else ''}: boundaries coincide over "
                         f"{length:.1f} m ({min(length / shorter, 1.0):.0%} of the shorter) but are different ways, "
                         f"{relative(signed)}; {consequence} | at {fmt_xy(pos)}",
                         (a.id, b.id), pos=pos)

    out.info(f"{n_shared + out.warnings + out.ignored} pairs of side-by-side lanelets: {n_shared} share their boundary, "
             f"{out.warnings} do not" + (f", {out.ignored} ignored" if out.ignored else ""))
    if out.warnings == 0:
        out.ok("All side-by-side lanelets share their boundary")
