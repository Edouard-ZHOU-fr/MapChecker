"""Geometry utilities (all coordinates are local metric coordinates)"""

import math

import numpy as np


def arc_length(p):
    """Cumulative arc length at each point of the polyline"""
    return np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]


def resample_by_ratio(p, ratios):
    s = arc_length(p)
    t = ratios * s[-1]
    return np.c_[np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])]


def resample_by_step(p, step):
    s = arc_length(p)
    n = max(2, int(math.ceil(s[-1] / step)) + 1)
    t = np.linspace(0.0, s[-1], n)
    return np.c_[np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])]


def signed_area(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)


def fmt_xy(p):
    return f"({p[0]:.1f}, {p[1]:.1f})"


def centerline(ll, step=0.5):
    ratios = np.linspace(0.0, 1.0, 200)
    c = (resample_by_ratio(ll.left, ratios) + resample_by_ratio(ll.right, ratios)) / 2.0
    return resample_by_step(c, step)


def max_heading_change(center, step, window):
    """
    Maximum heading change within any centerline segment of length <= window (degrees, signed: + left turn, - right turn).
    If the lanelet is shorter than window, this is the turning angle of the whole lanelet.
    """
    seg = np.diff(center, axis=0)
    if len(seg) < 2:
        return 0.0
    heading = np.unwrap(np.arctan2(seg[:, 1], seg[:, 0]))
    # moving average over about 2 m, removes the kink noise at nodes
    k = max(1, min(int(round(2.0 / step)), len(heading) // 4))
    if k > 1:
        heading = np.convolve(heading, np.ones(k) / k, mode="valid")
    max_off = min(len(heading) - 1, max(1, int(round(window / step))))
    best = 0.0
    for off in range(1, max_off + 1):
        d = heading[off:] - heading[:-off]
        i = np.argmax(np.abs(d))
        if abs(d[i]) > abs(best):
            best = d[i]
    return math.degrees(best)


def points_in_polygon(pts, poly):
    """Ray casting test of whether points are inside the polygon (vectorized)"""
    x, y = pts[:, 0:1], pts[:, 1:2]
    xi, yi = poly[None, :, 0], poly[None, :, 1]
    xj, yj = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    straddle = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
    return np.count_nonzero(straddle & (x < x_cross), axis=1) % 2 == 1


def interior_samples(ll, step=0.5):
    """
    Sample the lanelet interior on a grid of about step meters, returns (points, area represented by each point).
    All points are half a cell away from the boundary.
    """
    length = 0.5 * (arc_length(ll.left)[-1] + arc_length(ll.right)[-1])
    n = max(2, int(math.ceil(length / step)))
    ratios = (np.arange(n) + 0.5) / n
    lb = resample_by_ratio(ll.left, ratios)
    rb = resample_by_ratio(ll.right, ratios)
    width = np.linalg.norm(lb - rb, axis=1)
    m = max(2, int(math.ceil(width.max() / step)))
    t = (np.arange(m) + 0.5) / m
    pts = (lb[:, None, :] + t[None, :, None] * (rb - lb)[:, None, :]).reshape(-1, 2)
    area = np.repeat(length / n * width / m, m)
    return pts, area


def overlap_area(a, b, step=0.5, cache=None):
    """Overlap area of two lanelets (estimated by interior sampling) and the sample points inside the overlap"""
    best_area, best_pts = 0.0, np.empty((0, 2))
    for x, y in ((a, b), (b, a)):
        key = (x.id, step)
        if cache is not None and key in cache:
            pts, area = cache[key]
        else:
            pts, area = interior_samples(x, step)
            if cache is not None:
                cache[key] = (pts, area)
        lo, hi = y.polygon.min(axis=0), y.polygon.max(axis=0)
        sel = np.nonzero(np.all((pts >= lo) & (pts <= hi), axis=1))[0]
        sel = sel[points_in_polygon(pts[sel], y.polygon)]
        s = area[sel].sum()
        if s > best_area:
            best_area, best_pts = s, pts[sel]
    return best_area, best_pts


def principal_extent(pts):
    """
    Length of the point set along the principal direction (first PCA axis); at an intersection the overlap is about one
    lane width, for parallel lanes on a narrow road the overlap is very long
    """
    if len(pts) < 2:
        return 0.0
    c = pts - pts.mean(axis=0)
    return float(np.ptp(c @ np.linalg.svd(c, full_matrices=False)[2][0]))


def centerline_enters(a, b, step=0.5):
    """
    The centerline of at least one lanelet enters the other lanelet, i.e. the vehicle drives into the other lane (a real
    crossing); otherwise the two lanelets only touch at the edges, e.g. the start of a diverge grazing the oncoming
    lane, parallel lanes on a narrow road
    """
    return bool(points_in_polygon(centerline(b, step), a.polygon).any()
                or points_in_polygon(centerline(a, step), b.polygon).any())


def heading_at(center, pos):
    """Unit direction vector at the point of the centerline closest to pos"""
    i = min(int(np.argmin(np.linalg.norm(center - pos, axis=1))), len(center) - 2)
    d = center[i + 1] - center[i]
    return d / max(np.linalg.norm(d), 1e-12)


def centerline_gap(a, b, pts, step=0.5):
    """
    Minimum distance between the centerlines of two lanelets alongside their overlap (centerline points within 2 step
    of an overlap point pts): parallel lanes (e.g. lanes of a narrowing road) stay apart along the whole overlap, while
    the centerlines of crossing / converging lanes come together
    """
    best = math.inf
    ca, cb = centerline(a, step), centerline(b, step)
    for c, other in ((ca, cb), (cb, ca)):
        near = np.min(np.linalg.norm(c[:, None] - pts[None], axis=2), axis=1) <= 2 * step
        if near.any():
            best = min(best, float(np.min(np.linalg.norm(c[near][:, None] - other[None], axis=2))))
    return best


def polygon_distance(pa, pb):
    """Minimum distance between the boundaries of two non-intersecting polygons"""
    def vertex_to_edges(p, poly):
        a, ab = poly[None], (np.roll(poly, -1, axis=0) - poly)[None]
        t = np.clip(((p[:, None] - a) * ab).sum(-1) / np.maximum((ab ** 2).sum(-1), 1e-12), 0.0, 1.0)
        return np.linalg.norm(p[:, None] - (a + t[..., None] * ab), axis=-1).min()
    return min(vertex_to_edges(pa, pb), vertex_to_edges(pb, pa))


def lanelets_in_aoi(lanelets, aois, step=1.0):
    """
    Ids of lanelets entirely inside the AOI: after sampling the left and right boundaries every step meters, all points
    fall inside the AOI polygons
    """
    result = set()
    for ll in lanelets.values():
        pts = np.vstack([ll.left, ll.right, resample_by_step(ll.left, step), resample_by_step(ll.right, step)])
        inside = np.zeros(len(pts), dtype=bool)
        for poly in aois:
            inside |= points_in_polygon(pts, poly)
        if inside.all():
            result.add(ll.id)
    return result
