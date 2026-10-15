"""几何工具 (坐标均为局部米制坐标)"""

import math

import numpy as np


def arc_length(p):
    """折线各点处的累计弧长"""
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
    在任意长度 <= window 的中心线片段内的最大航向变化 (度, 带符号: + 左转, - 右转)。
    lanelet 比 window 短时，即为整条 lanelet 的转角。
    """
    seg = np.diff(center, axis=0)
    if len(seg) < 2:
        return 0.0
    heading = np.unwrap(np.arctan2(seg[:, 1], seg[:, 0]))
    # 约 2 m 的滑动平均，去除节点处的折角噪声
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
    """射线法判断点是否在多边形内 (向量化)"""
    x, y = pts[:, 0:1], pts[:, 1:2]
    xi, yi = poly[None, :, 0], poly[None, :, 1]
    xj, yj = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    straddle = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
    return np.count_nonzero(straddle & (x < x_cross), axis=1) % 2 == 1


def interior_samples(ll, step=0.5):
    """在 lanelet 内部按约 step 米的网格采样，返回 (点, 每个点代表的面积)。点都远离边界半个格子。"""
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
    """两个 lanelet 的重叠面积 (内部采样估算) 和落在重叠区内的采样点"""
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


def polygon_distance(pa, pb):
    """两个不相交多边形边界之间的最小距离"""
    def vertex_to_edges(p, poly):
        a, ab = poly[None], (np.roll(poly, -1, axis=0) - poly)[None]
        t = np.clip(((p[:, None] - a) * ab).sum(-1) / np.maximum((ab ** 2).sum(-1), 1e-12), 0.0, 1.0)
        return np.linalg.norm(p[:, None] - (a + t[..., None] * ab), axis=-1).min()
    return min(vertex_to_edges(pa, pb), vertex_to_edges(pb, pa))


def lanelets_in_aoi(lanelets, aois, step=1.0):
    """完全位于 AOI 内的 lanelet id: 左右边界按 step 米采样后，所有点都落在 AOI 多边形内"""
    result = set()
    for ll in lanelets.values():
        pts = np.vstack([ll.left, ll.right, resample_by_step(ll.left, step), resample_by_step(ll.right, step)])
        inside = np.zeros(len(pts), dtype=bool)
        for poly in aois:
            inside |= points_in_polygon(pts, poly)
        if inside.all():
            result.add(ll.id)
    return result
