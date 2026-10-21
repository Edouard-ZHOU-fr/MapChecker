"""
altitude 扩展的高程趋势图 (需要 matplotlib)，check_altitude.check() 在 plot 为 true 时调用 save_trend_chart():
  上: 俯视图，检查的车辆 lanelet 按高程着色 (浅蓝低、深蓝高，每约 SEGMENT 米一段)，红圈为突兀点，#k 为报告中的编号
  下: 高差最大的 max_profiles 个突兀点所在车道的纵断面: 左右边界的高程 - 沿车道中心线的距离，
      前继、后继各接一条 lanelet (灰底为突兀点所在的 lanelet)，红色标出突兀点
"""

import math
import time

import numpy as np

from utils.geometry import arc_length, centerline, resample_by_ratio

from .check_altitude import nearest_on_line

SURFACE = "#fcfcfb"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
OTHER_LANELETS = "#e4e3de"   # 不检查的 lanelet (crosswalk、AOI 外的等)
NO_ELEVATION = "#9b9a94"     # 没有 ele 的部分
ELEVATION_RAMP = ("#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b")  # 由低到高，单色蓝
SIDE_COLORS = {"left": "#2a78d6", "right": "#eb6834"}
ABRUPT = "#d03b3b"
LANE_SHADE = "#f0efec"
SEGMENT = 2.0          # 俯视图中按高程着色的每段长度 (米)
CONTEXT = 40.0         # 纵断面中突兀点两侧各画多长 (米)
NCOLS = 3
PLAN_WIDTH = 12.0      # 图宽 (英寸)
PROFILE_HEIGHT = 2.6   # 每行纵断面的高度 (英寸)
DPI = 150


def save_trend_chart(path, ctx, lanelets, issues, max_profiles):
    """
    lanelets: 检查的车辆 lanelet {id: Lanelet}；issues: 报告了的突兀点 (check_altitude.Abrupt)，第 k 个编号为 #k+1，
    其中前 max_profiles 个画纵断面
    """
    from matplotlib.figure import Figure

    profiles = issues[:max_profiles]
    shapes = (ctx.aois if ctx.only_check_aoi else []) or [ll.polygon for ll in lanelets.values()] \
        or [ll.polygon for ll in ctx.lanelets.values()]
    pts = np.vstack(shapes) if shapes else np.zeros((1, 2))
    lo, hi = pts.min(axis=0) - 10.0, pts.max(axis=0) + 10.0
    plan_height = float(np.clip(PLAN_WIDTH * (hi[1] - lo[1]) / (hi[0] - lo[0]), 4.0, 12.0))
    rows = math.ceil(len(profiles) / NCOLS)

    fig = Figure(figsize=(PLAN_WIDTH, plan_height + 1.0 + PROFILE_HEIGHT * rows), dpi=DPI, constrained_layout=True)
    fig.patch.set_facecolor(SURFACE)
    grid = fig.add_gridspec(1 + rows, NCOLS, height_ratios=[plan_height] + [PROFILE_HEIGHT] * rows)
    _draw_plan(fig, fig.add_subplot(grid[0, :]), ctx, lanelets, issues, len(profiles), lo, hi)
    for k, a in enumerate(profiles):
        _draw_profile(fig.add_subplot(grid[1 + k // NCOLS, k % NCOLS]), ctx, k + 1, a, first=k == 0)

    name = ctx.map_path and ctx.map_path.replace("\\", "/").rsplit("/", 1)[-1] or "map"
    shown = f", profiles of #1~#{len(profiles)} below" if profiles else ""
    fig.suptitle(f"MapChecker altitude trend: {name}\n{len(lanelets)} vehicle lanelets, {len(issues)} abrupt points"
                 f"{shown}, {time.strftime('%Y-%m-%d %H:%M:%S')}", fontsize=11, color=INK)
    fig.savefig(path, facecolor=SURFACE)


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(AXIS)


def _elevation_segments(ll):
    """lanelet 按约 SEGMENT 米分成的四边形 [n, 4, 2] 和每段的平均高程 [n] (有点没有 ele 时为 nan)"""
    sl, sr = arc_length(ll.left), arc_length(ll.right)
    n = max(1, math.ceil(max(sl[-1], sr[-1]) / SEGMENT))
    ratios = np.linspace(0.0, 1.0, n + 1)
    left, right = resample_by_ratio(ll.left, ratios), resample_by_ratio(ll.right, ratios)
    zl, zr = np.interp(ratios * sl[-1], sl, ll.left_z), np.interp(ratios * sr[-1], sr, ll.right_z)
    quads = np.stack([left[:-1], left[1:], right[1:], right[:-1]], axis=1)
    return quads, (zl[:-1] + zl[1:] + zr[1:] + zr[:-1]) / 4.0


def _draw_plan(fig, ax, ctx, lanelets, issues, n_labeled, lo, hi):
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    _style(ax)
    others = [ll.polygon for lid, ll in ctx.lanelets.items() if lid not in lanelets]
    ax.add_collection(PolyCollection(others, facecolors=OTHER_LANELETS, edgecolors="none", zorder=1))

    if lanelets:
        quads, values = (np.concatenate(v) for v in zip(*(_elevation_segments(ll) for ll in lanelets.values())))
        finite = values[np.isfinite(values)]
        # 颜色范围取 1% ~ 99%，个别离谱的高程不压缩其余部分的颜色
        z0, z1 = np.percentile(finite, [1, 99]) if len(finite) else (0.0, 1.0)
        if z1 - z0 < 1.0:
            z0, z1 = (z0 + z1) / 2.0 - 0.5, (z0 + z1) / 2.0 + 0.5
        cmap = LinearSegmentedColormap.from_list("elevation", ELEVATION_RAMP)
        cmap.set_bad(NO_ELEVATION)
        segments = PolyCollection(quads, array=np.ma.masked_invalid(values), cmap=cmap, norm=Normalize(z0, z1),
                                  edgecolors="face", linewidths=0.3, zorder=2)
        ax.add_collection(segments)
        bar = fig.colorbar(segments, ax=ax, shrink=0.7, pad=0.01, extend="both")
        bar.set_label("elevation (m)", color=INK_2)
        bar.ax.tick_params(colors=MUTED, labelsize=8)
        bar.outline.set_edgecolor(AXIS)

    if ctx.only_check_aoi:
        for k, poly in enumerate(ctx.aois):
            closed = np.vstack([poly, poly[:1]])
            ax.plot(closed[:, 0], closed[:, 1], color=MUTED, linewidth=1.0, linestyle="--", zorder=3,
                    label="AOI" if k == 0 else None)

    if issues:
        pos = np.array([a.pos for a in issues])
        ax.scatter(pos[:, 0], pos[:, 1], s=46, facecolors="none", edgecolors=SURFACE, linewidths=3.0, zorder=4)
        ax.scatter(pos[:, 0], pos[:, 1], s=46, facecolors="none", edgecolors=ABRUPT, linewidths=1.4, zorder=5,
                   label=f"abrupt point ({len(issues)})")
        # 相距很近的突兀点共用一个标签 "#1 #3"
        near = max(hi - lo) / 60.0
        groups = []
        for k, a in enumerate(issues[:n_labeled], 1):
            group = next((g for g in groups if np.linalg.norm(g[0] - a.pos) <= near), None)
            if group:
                group[1].append(k)
            else:
                groups.append((a.pos, [k]))
        for p, ks in groups:
            ax.annotate(" ".join(f"#{k}" for k in ks), p, xytext=(6, 6), textcoords="offset points", fontsize=8,
                        color=INK, zorder=6,
                        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.85))
    if issues or ctx.only_check_aoi:
        ax.legend(loc="best", fontsize=8, framealpha=0.9)

    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[1], hi[1])
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)", color=MUTED)
    ax.set_ylabel("y (m)", color=MUTED)


def _chain(ctx, lid):
    """lanelet lid 前后各接一条车辆 lanelet (前继、后继中 id 最小的): ([Lanelet], lid 在其中的下标)"""
    succ, pred = ctx.topology
    before = min((i for i in pred[lid] if i in ctx.vehicle), key=int, default=None)
    after = min((i for i in succ[lid] if i in ctx.vehicle and i != before), key=int, default=None)
    chain = [ctx.lanelets[i] for i in (before, lid, after) if i is not None]
    return chain, int(before is not None)


def _draw_profile(ax, ctx, number, a, first):
    lid, side = a.users[0]
    chain, at = _chain(ctx, lid)
    # 沿整条链的中心线量距离，0 为 lid 的起点；左右边界上的点都投影到中心线上
    parts = [centerline(ll) for ll in chain]
    line = np.vstack([parts[0]] + [p[1:] for p in parts[1:]])
    s = arc_length(line)
    start = s[sum(len(p) - 1 for p in parts[:at])]
    length = arc_length(parts[at])[-1]

    profile = {}
    for name in ("left", "right"):
        ids, pts, z = [], [], []
        for ll in chain:
            skip = int(bool(ids) and ids[-1] == getattr(ll, f"{name}_ids")[0])  # 前后相接的 lanelet 共用端点
            ids += getattr(ll, f"{name}_ids")[skip:]
            pts.append(getattr(ll, name)[skip:])
            z.append(getattr(ll, f"{name}_z")[skip:])
        k, t, _ = nearest_on_line(np.vstack(pts), line)
        station = s[k] + t * (s[k + 1] - s[k]) - start
        profile[name] = (ids, station, np.concatenate(z))

    _style(ax)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.axvspan(0.0, length, color=LANE_SHADE, zorder=0)
    for name in ("left", "right"):
        _, station, z = profile[name]
        ax.plot(station, z, color=SIDE_COLORS[name], linewidth=1.5, marker="o", markersize=3.5,
                markeredgecolor=SURFACE, markeredgewidth=0.6, zorder=2, label=f"{name} boundary")

    ids, station, z = profile[side]
    idx = [ids.index(n) for n in a.nodes]
    x, y = station[idx], z[idx]
    if a.kind == "step":
        ax.plot(x, y, color=SURFACE, linewidth=4.5, zorder=3, solid_capstyle="round")
        ax.plot(x, y, color=ABRUPT, linewidth=2.5, zorder=4, solid_capstyle="round")
    elif a.kind == "cross":  # 到另一侧边界的高差
        ax.plot([x[0], x[0]], [y[0], y[0] - a.dz], color=ABRUPT, linewidth=1.2, linestyle=":", zorder=3)
    ax.scatter(x, y, s=60, facecolors="none", edgecolors=ABRUPT, linewidths=1.4, zorder=5,
               label="abrupt point" if first else None)
    ax.annotate(f"{a.dz:+.2f} m", (x.mean(), y.max()), xytext=(6, 4), textcoords="offset points", fontsize=8,
                color=INK, zorder=6)

    # 只画突兀点两侧各 CONTEXT 米，纵轴范围按其中的点取
    lo, hi = x.mean() - CONTEXT, x.mean() + CONTEXT
    lo = max(lo, min(min(p[1]) for p in profile.values()) - 1.0)
    hi = min(hi, max(max(p[1]) for p in profile.values()) + 1.0)
    shown = np.concatenate([p[2][(p[1] >= lo) & (p[1] <= hi)] for p in profile.values()] + [y, y - a.dz])
    shown = shown[np.isfinite(shown)]
    pad = max(0.1, 0.1 * (shown.max() - shown.min()))
    ax.set_xlim(lo, hi)
    ax.set_ylim(shown.min() - pad, shown.max() + pad)
    ax.ticklabel_format(axis="y", useOffset=False)

    neighbors = [f"pred {chain[0].id}" if at else "", f"succ {chain[-1].id}" if chain[-1].id != lid else ""]
    ax.set_title(f"#{number} {a.kind} {a.dz:+.2f} m · lanelet {lid} {side}", fontsize=9, color=INK, loc="left")
    ax.set_xlabel(f"distance along lanelet {lid} (m)" + "".join(f" · {n}" for n in neighbors if n),
                  color=MUTED, fontsize=8)
    if number % NCOLS == 1:
        ax.set_ylabel("elevation (m)", color=MUTED, fontsize=8)
    if first:
        ax.legend(loc="best", fontsize=7, framealpha=0.9)
