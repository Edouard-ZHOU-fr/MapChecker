"""Warning features (for logistic regression scoring), shared by check_lanelet_map.py and utils/build_dataset.py"""

import math
from collections import defaultdict

import numpy as np

from .geometry import arc_length, centerline, centerline_enters, max_heading_change
from .topology import build_topology

# Feature names and their order in the model; run train_model.py again after changing them
# Node elevation difference was tried and removed: it changes with how each map version is processed (e.g. elevation
# smoothing), so the model learned "which version" rather than "which warning"
FEATURES = {
    "turn_direction": (
        "abs_angle",           # absolute heading change (deg)
        "curve",               # 1: curve (abs_angle >= turn_angle)
        "log_length",          # log(1 + centerline length m)
        "n_crossing",          # number of lanelets truly crossing it (centerline enters the other lane, no edge contact)
        "n_merge",             # number of lanelets merging with it
        "n_diverge",           # number of lanelets diverging from it
        "log_overlap_length",  # log(1 + longest overlap length m with other lanelets, diverge excluded, edge contact
                               #   included), parallel lanes on narrow roads have very long overlaps
        "traffic_light",       # 1: itself or a predecessor lanelet references a traffic light (intersection with traffic lights)
        "isolated",            # 1: no true crossing / merge with any lane (only diverge or edge contact), i.e. no intersection
    ),
    "right_of_way": (
        "log_area",       # log(1 + overlap area m²)
        "merge",          # 1: merge, 0: crossing
        "heading_diff",   # heading difference of the two lanelets at the overlap (deg, 0~180)
        "max_abs_angle",  # larger of the two lanelets' absolute heading changes (deg)
    ),
}

# Domain knowledge: the larger these features, the more likely the warning is a false positive. Training
# (utils/tranning.py) constrains their coefficients to <= 0: the direction is decided here, how much they lower p is
# decided by the data (when the data does not support it the coefficient is 0, i.e. no effect; it never raises p instead)
NEGATIVE_FEATURES = {
    "turn_direction": (
        "log_overlap_length",  # an overlong overlap is caused by a narrow road, not a true crossing / merge
        "traffic_light",       # intersection with traffic lights
        "isolated",            # no intersection (e.g. diverge: Autoware also counts diverges as conflicts, and the start
                               #   of a diverge often grazes the opposite lane)
    ),
}


def lanelet_context(lanelets, reg_elems, overlaps, pred=None):
    """
    Per-lanelet info for the turn_direction features {lanelet id: {...}}; pred is the already computed predecessor
    relation, recomputed when None:
      crossing / merge / diverge: number of lanelets crossing / merging / diverging with it; crossing excludes edge
                                  contact (see centerline_enters)
      overlap_length:             longest overlap length with other lanelets (m), diverge excluded, edge contact included
      traffic_light:              whether itself or a predecessor lanelet references a traffic light
    """
    ctx = defaultdict(lambda: {"crossing": 0, "merge": 0, "diverge": 0, "overlap_length": 0.0, "traffic_light": False})
    for a_id, b_id, rel, _, _, length in overlaps:
        touch = rel is None and not centerline_enters(lanelets[a_id], lanelets[b_id])
        for x in (a_id, b_id):
            if not touch:
                ctx[x][rel or "crossing"] += 1
            if rel != "diverge":
                ctx[x]["overlap_length"] = max(ctx[x]["overlap_length"], length)
    tl = {ll.id for ll in lanelets.values()
          if any(r in reg_elems and reg_elems[r].subtype == "traffic_light" for r in ll.reg_elems)}
    if pred is None:
        _, pred = build_topology(lanelets)
    for lid in lanelets:
        ctx[lid]["traffic_light"] = lid in tl or bool(pred[lid] & tl)
    return ctx


def turn_direction_features(ll, center, angle, opts, ctx):
    """center / angle: centerline and heading change (already computed in check_turn_direction); ctx: lanelet_context() result"""
    c = ctx[ll.id]
    return {
        "abs_angle": abs(angle),
        "curve": float(abs(angle) >= opts["turn_angle"]),
        "log_length": math.log1p(arc_length(center)[-1]),
        "n_crossing": c["crossing"],
        "n_merge": c["merge"],
        "n_diverge": c["diverge"],
        "log_overlap_length": math.log1p(c["overlap_length"]),
        "traffic_light": float(c["traffic_light"]),
        "isolated": float(c["crossing"] + c["merge"] == 0),
    }


def right_of_way_features(a, b, rel, area, pos, opts, step=0.5):
    """rel / area / pos: relation, overlap area and center of the overlap region given by find_overlaps()"""
    ca, cb = centerline(a, step), centerline(b, step)
    cos = np.clip(np.dot(_heading_at(ca, pos), _heading_at(cb, pos)), -1.0, 1.0)
    return {
        "log_area": math.log1p(area),
        "merge": float(rel == "merge"),
        "heading_diff": math.degrees(math.acos(cos)),
        "max_abs_angle": max(abs(max_heading_change(c, step, opts["turn_window"])) for c in (ca, cb)),
    }


def _heading_at(center, pos):
    """Unit direction vector at the point of the centerline closest to pos"""
    i = min(int(np.argmin(np.linalg.norm(center - pos, axis=1))), len(center) - 2)
    d = center[i + 1] - center[i]
    return d / max(np.linalg.norm(d), 1e-12)
