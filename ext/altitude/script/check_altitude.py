"""
扩展检查 altitude: 车道 (车辆 lanelet) 边界上高程 (ele) 的突兀点，并生成高程趋势图

突兀点 (高差小于 min_step 的不报):
  step   跳变: 边界上相邻两点之间的坡度 (高差 / 水平距离) 大于 max_slope
  spike  尖峰: 边界上一点前后两段都是跳变且方向相反，即该点比前后两点都高 (或都低)
  cross  横坡: lanelet 一侧边界上的点与另一侧边界的高差大于 max_cross_slope × 两者的距离
没有 ele 的节点单独报告。按高差从大到小输出，编号 #k 与趋势图中的编号一致。
趋势图 (plot 为 true 时) 保存在日志旁，见 _plot.py。

说明见 ext/altitude/README.md，参数见 ext/altitude/config/config.yaml。单独运行:
  python3 check_lanelet_map.py --only-ext altitude
"""

import os
from dataclasses import dataclass

import numpy as np

from utils.geometry import fmt_xy
from utils.lanelet_map import by_id

# config.yaml 中缺省项的默认值 (enable / ignore 由 utils/ext.py 处理)
DEFAULT_CONFIG = {
    "max_slope": 0.15,
    "max_cross_slope": 0.15,
    "min_step": 0.1,
    "plot": True,
    "max_profiles": 12,
}

MIN_STAT_LENGTH = 1.0  # 统计坡度分布时只用水平距离不小于它 (米) 的点对，太近的两点坡度没有意义


def configure(cfg):
    for key in ("max_slope", "max_cross_slope", "min_step"):
        cfg[key] = float(cfg[key])
        if cfg[key] < 0:
            raise ValueError(f"{key} must be >= 0, got {cfg[key]}")
    cfg["max_profiles"] = int(cfg["max_profiles"])
    if cfg["max_profiles"] < 0:
        raise ValueError(f"max_profiles must be >= 0, got {cfg['max_profiles']}")
    if not isinstance(cfg["plot"], bool):
        raise ValueError(f"plot must be true or false, got {cfg['plot']!r}")
    if cfg["plot"]:
        try:
            import matplotlib  # noqa: F401
        except ImportError as e:
            raise ValueError(f"plot requires matplotlib ({e}), or set plot: false") from e
    return cfg


@dataclass
class Abrupt:
    """一个突兀点"""
    kind: str        # "step" / "spike" / "cross"
    nodes: tuple     # step: 相邻两点的 id；spike / cross: 该点的 id
    dz: float        # 高差 (米，带符号) step: 后一点减前一点；spike: 该点减前后两点的连线；cross: 该点减另一侧边界
    dist: float      # 水平距离 (米) step: 两点间；spike: 前后两点间；cross: 该点到另一侧边界
    slope: float     # 坡度 (绝对值)，spike 为两侧中较大的
    pos: np.ndarray  # 坐标 [x, y]
    users: list      # 该点所在的边界 [(lanelet id, "left" / "right")]，第一条用于画纵断面
    text: str        # 输出的说明 (不含编号)


def pct(x):
    return f"{x:.0%}" if np.isfinite(x) else "vertical"


def boundaries(lanelets):
    """
    车辆 lanelet 的边界，相邻车道共用的边界只算一次:
    {way id: (节点 id, 坐标 [N, 2], 高程 [N], [(lanelet id, "left" / "right")])}
    """
    result = {}
    for ll in sorted(lanelets.values(), key=by_id):
        for side, way, ids, pts, z in (("left", ll.left_way, ll.left_ids, ll.left, ll.left_z),
                                       ("right", ll.right_way, ll.right_ids, ll.right, ll.right_z)):
            result.setdefault(way, (ids, pts, z, []))[3].append((ll.id, side))
    return result


def slope_points(pts, z, max_slope, min_step):
    """
    一条边界上的跳变和尖峰: [(kind, 点的下标, 高差, 水平距离, 坡度)]
      step:  第 k 段 (点 k -> k+1) 高差 |dz| >= min_step 且坡度 |dz| / 距离 > max_slope，下标为 k
      spike: 相邻两段都是 step 且方向相反，下标为中间的点 k+1，高差为它相对前后两点连线的偏离
    没有高程 (nan) 的点不参与
    """
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    dz = np.diff(z)
    with np.errstate(invalid="ignore", divide="ignore"):
        steep = (np.abs(dz) >= min_step) & (np.abs(dz) > max_slope * d)
        slope = np.abs(dz) / d  # 两点重合 (d == 0) 时为 inf
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
    """pts 中每个点在折线 line (至少 2 点) 上的最近点: (所在段的下标 k [N], 段内比例 t [N], 距离 [N])"""
    a, ab = line[:-1], np.diff(line, axis=0)
    l2 = (ab ** 2).sum(axis=1)
    t = np.clip(((pts[:, None] - a[None]) * ab[None]).sum(axis=-1) / np.where(l2 > 0, l2, 1.0), 0.0, 1.0)
    dist = np.linalg.norm(a[None] + t[..., None] * ab[None] - pts[:, None], axis=-1)  # [N, 段数]
    k = np.argmin(dist, axis=1)
    i = np.arange(len(pts))
    return k, t[i, k], dist[i, k]


def cross_points(ll):
    """
    lanelet 两侧边界上每个点与另一侧边界 (最近点处，沿边界线性插值) 的高差:
    [(side, 节点 id, 坐标, 高差, 距离, 另一侧边界上最近一段两端的节点 id)]
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
    lanelets ({id: Lanelet}) 边界上的突兀点，按高差从大到小排序；
    返回 (突兀点 [Abrupt], 统计 {"slope": 相邻两点的坡度, "cross": 横坡}，只含距离 >= MIN_STAT_LENGTH 的)
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
            if frozenset(nodes) in seen:  # 两条边界有相同的一段 (重叠的 way)
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

    # 横坡: 每条 lanelet 取高差最大的点。该点或另一侧对应的点已作为跳变 / 尖峰报告时不再报告，避免同一处报两次
    flagged = {n for a in found for n in a.nodes}
    worst = {}  # 节点 id -> [(|高差|, lanelet id, side, 坐标, 高差, 距离, 该 lanelet 中超过 max 的点数)]
    for ll in sorted(lanelets.values(), key=by_id):
        bad = []
        for side, nid, pos, dz, dist, near in cross_points(ll):
            if dist >= MIN_STAT_LENGTH and np.isfinite(dz):
                stats["cross"].append(abs(dz) / dist)
            # 高程为 nan 时比较的结果为 False，不会报告
            if abs(dz) >= min_step and abs(dz) > max_cross * dist and not flagged.intersection((nid,) + near):
                bad.append((abs(dz), side, nid, pos, dz, dist))
        if bad:
            size, side, nid, pos, dz, dist = max(bad, key=lambda b: b[0])
            worst.setdefault(nid, []).append((size, ll.id, side, pos, dz, dist, len(bad)))
    # 重叠的 lanelet (如分叉处) 的最大高差在同一个点时合并为一条，高差最大的 lanelet 在前
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
    """边界上没有 ele 的节点 {lanelet id: [节点 id]}"""
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
    报告车辆 lanelet 边界上的突兀点和没有 ele 的节点，plot 为 true 时生成高程趋势图。
    only_check_AOI 时只检查完全位于 AOI 内的车辆 lanelet
    """
    out.header(f"Ext {out.category}: abrupt elevation changes on lane boundaries (max slope {pct(cfg['max_slope'])}, "
               f"max cross slope {pct(cfg['max_cross_slope'])}, min step {cfg['min_step']:.2f} m)")
    lanelets = ctx.select(ctx.vehicle)
    if ctx.only_check_aoi:
        out.info(f"AOI: {len(lanelets)} of {len(ctx.vehicle)} vehicle lanelets fully inside (only these are checked)")

    found, stats = find_abrupt(lanelets, cfg)
    reported = []  # 输出了的突兀点 (没有被忽略)，编号为下标 + 1
    for a in found:
        before = out.warnings
        out.warn(f"#{len(reported) + 1} {a.text}", tuple(dict.fromkeys(lid for lid, _ in a.users)), pos=a.pos)
        if out.warnings > before:
            reported.append(a)

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
    if out.warnings == 0:
        out.ok("No abrupt elevation change on the checked lane boundaries")
