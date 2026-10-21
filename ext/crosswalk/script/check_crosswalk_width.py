"""
扩展检查 crosswalk: crosswalk 在横向上最多超出道路多少，超出过多时报 WARNING

crosswalk lanelet 的左右边界沿行人行走的方向，即横穿道路的方向。测量 crosswalk 每一端超出道路
(与它重叠的所有车辆 lanelet 的并集) 的最大长度，大于 max_extension 时报 WARNING，其余只输出测量值。

说明见 ext/crosswalk/README.md，参数见 ext/crosswalk/config/config.yaml。单独运行:
  python3 check_lanelet_map.py --only-ext crosswalk
"""

import math

import numpy as np

from utils.geometry import arc_length, fmt_xy, points_in_polygon, resample_by_ratio, resample_by_step
from utils.lanelet_map import by_id

# config.yaml 中缺省项的默认值 (enable / ignore 由 utils/ext.py 处理)
DEFAULT_CONFIG = {
    "max_extension": 1.0,
}

LINE_SPACING = 0.1   # 沿 crosswalk 宽度方向取线的间距 (米)
STEP = 0.01          # 沿线采样的间距 (米)，即测量精度


def configure(cfg):
    cfg["max_extension"] = float(cfg["max_extension"])
    if cfg["max_extension"] < 0:
        raise ValueError(f"max_extension must be >= 0, got {cfg['max_extension']}")
    return cfg


def axis_lines(cw):
    """在 crosswalk 宽度方向上每隔约 LINE_SPACING 米取一条平行于左右边界的线，方向与边界相同"""
    ratios = np.linspace(0.0, 1.0, 50)
    lb, rb = resample_by_ratio(cw.left, ratios), resample_by_ratio(cw.right, ratios)
    if arc_length((lb + rb) / 2.0)[-1] < STEP:
        return []
    n = max(1, math.ceil(np.linalg.norm(lb - rb, axis=1).max() / LINE_SPACING))
    return [lb + t * (rb - lb) for t in (np.arange(n) + 0.5) / n]


def end_extensions(cw, lanes):
    """
    crosswalk 两端 (起点端, 终点端) 超出道路 (lanes 的并集) 的最大长度，沿 crosswalk 方向测量，
    每端为 (长度, 道路边缘的车道 id, 端点是否在道路内)；crosswalk 不与任何车道重叠时返回 None
    """
    best = [None, None]
    for line in axis_lines(cw):
        pts = resample_by_step(line, STEP)
        s = arc_length(pts)
        inside = np.array([points_in_polygon(pts, ll.polygon) for ll in lanes]).reshape(len(lanes), len(pts))
        hit = np.nonzero(inside.any(axis=0))[0]
        if not len(hit):
            continue
        half = (s[1] - s[0]) / 2.0  # 道路边界取在相邻采样点的中间
        for side, k in ((0, hit[0]), (1, hit[-1])):
            at_end = k == (0, len(pts) - 1)[side]
            ext = 0.0 if at_end else (s[k] - half if side == 0 else s[-1] - s[k] - half)
            if best[side] is None or ext > best[side][0]:
                best[side] = (ext, lanes[np.nonzero(inside[:, k])[0][0]].id, at_end)
    return None if best[0] is None else best


def check(ctx, cfg, out):
    """
    输出每个 crosswalk 两端超出道路的最大长度，超过 cfg["max_extension"] 时报 WARNING。
    only_check_AOI 时只检查完全位于 AOI 内的 crosswalk (道路始终用整张地图的车道)
    """
    max_ext = cfg["max_extension"]
    out.header(f"Ext {out.category}: how far crosswalks extend beyond the road (max {max_ext:.2f} m)")
    crosswalks = ctx.by_subtype("crosswalk")
    if ctx.only_check_aoi:
        inside = ctx.select(crosswalks)
        out.info(f"AOI: {len(inside)} of {len(crosswalks)} crosswalks fully inside (only these are checked)")
        crosswalks = inside

    not_crossing = []  # 不与任何车道重叠的 crosswalk
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
