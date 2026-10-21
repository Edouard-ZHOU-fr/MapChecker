"""
altitude 扩展的高程趋势图 (需要 matplotlib)，由 check_altitude.check() 调用:
  save_trend_chart()  plot 为 true 时保存 png:
    上: 俯视图，检查的车辆 lanelet 按高程着色 (浅蓝低、深蓝高，每约 SEGMENT 米一段)，红圈为突兀点，#k 为报告中的编号
    下: 高差最大的 max_profiles 个突兀点所在车道的纵断面: 左右边界的高程 - 沿车道中心线的距离，
        前继、后继各接一条 lanelet (灰底为突兀点所在的 lanelet)，红色标出突兀点
  open_window()       isShow 为 true 时在 pyplot 中创建俯视图的窗口 (独立于热力图)，报告结束后由
                      check_lanelet_map.py 与热力图一起弹出 (utils.heatmap.show_windows)；单击突兀点或 lanelet
                      时在另一个小窗口中显示其纵断面，见 _enable_click()
"""

import math
import textwrap
import time
import warnings

import numpy as np

from utils.geometry import arc_length, centerline, resample_by_ratio
from utils.heatmap import CLICK_PIXELS, HIGHLIGHT_COLOR, SHOW_MAX_HEIGHT

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
DETAIL_TITLE = "MapChecker altitude detail"  # 细节小窗口的标题，多次单击时重用同一个窗口
DETAIL_SIZE = (8.0, 4.8)                     # 细节小窗口的大小 (英寸)


def save_trend_chart(path, ctx, lanelets, issues, max_profiles):
    """
    lanelets: 检查的车辆 lanelet {id: Lanelet}；issues: 报告了的突兀点 (check_altitude.Abrupt)，第 k 个编号为 #k+1，
    其中前 max_profiles 个画纵断面
    """
    from matplotlib.figure import Figure

    profiles = issues[:max_profiles]
    lo, hi, plan_height = _plan_extent(ctx, lanelets)
    rows = math.ceil(len(profiles) / NCOLS)

    fig = Figure(figsize=(PLAN_WIDTH, plan_height + 1.0 + PROFILE_HEIGHT * rows), dpi=DPI, constrained_layout=True)
    fig.patch.set_facecolor(SURFACE)
    grid = fig.add_gridspec(1 + rows, NCOLS, height_ratios=[plan_height] + [PROFILE_HEIGHT] * rows)
    _draw_plan(fig, fig.add_subplot(grid[0, :]), ctx, lanelets, issues, len(profiles), lo, hi)
    for k, a in enumerate(profiles):
        _draw_profile(fig.add_subplot(grid[1 + k // NCOLS, k % NCOLS]), ctx, a.users[0][0], _issue_title(k + 1, a),
                      focus=(f"#{k + 1}", a), legend=k == 0, ylabel=k % NCOLS == 0)

    shown = f", profiles of #1~#{len(profiles)} below" if profiles else ""
    fig.suptitle(f"MapChecker altitude trend: {_map_name(ctx)}\n{len(lanelets)} vehicle lanelets, "
                 f"{len(issues)} abrupt points{shown}, {time.strftime('%Y-%m-%d %H:%M:%S')}", fontsize=11, color=INK)
    fig.savefig(path, facecolor=SURFACE)


def open_window(ctx, lanelets, issues, lines, n_labeled):
    """
    创建俯视图的 pyplot 窗口 (不弹出，报告结束后由 utils.heatmap.show_windows() 与热力图一起弹出)。
    lines: 每个突兀点在报告中的那一行，单击时在终端输出；n_labeled: 前几个突兀点在图中标出编号。返回 Figure
    """
    import matplotlib.pyplot as plt

    lo, hi, plan_height = _plan_extent(ctx, lanelets)
    figsize = (PLAN_WIDTH, plan_height + 0.8)
    fig = plt.figure(num=f"MapChecker altitude: {_map_name(ctx)}", figsize=figsize, clear=True,
                     dpi=min(100.0, SHOW_MAX_HEIGHT / figsize[1]), layout="constrained")
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_subplot()
    _draw_plan(fig, ax, ctx, lanelets, issues, n_labeled, lo, hi)
    fig.suptitle(f"MapChecker altitude: {_map_name(ctx)}, {len(issues)} abrupt points, "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}", fontsize=11, color=INK)
    _enable_click(fig, ax, ctx, lanelets, issues, lines)
    return fig


def _map_name(ctx):
    return ctx.map_path and ctx.map_path.replace("\\", "/").rsplit("/", 1)[-1] or "map"


def _issue_title(number, a):
    lid, side = a.users[0]
    return f"#{number} {a.kind} {a.dz:+.2f} m · lanelet {lid} {side}"


def _plan_extent(ctx, lanelets):
    """俯视图的范围 (左下角, 右上角) 和高度 (英寸，宽为 PLAN_WIDTH)：AOI (只检查 AOI 时) 或检查的 lanelet"""
    shapes = (ctx.aois if ctx.only_check_aoi else []) or [ll.polygon for ll in lanelets.values()] \
        or [ll.polygon for ll in ctx.lanelets.values()]
    pts = np.vstack(shapes) if shapes else np.zeros((1, 2))
    lo, hi = pts.min(axis=0) - 10.0, pts.max(axis=0) + 10.0
    return lo, hi, float(np.clip(PLAN_WIDTH * (hi[1] - lo[1]) / (hi[0] - lo[0]), 4.0, 12.0))


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


def _profile(ctx, lid):
    """
    lanelet lid (前后各接一条) 的纵断面: ({"left" / "right": (节点 id, 沿中心线的距离, 高程)}, 链, lid 的下标, lid 的长度)；
    距离沿整条链的中心线量，0 为 lid 的起点，左右边界上的点都投影到中心线上
    """
    chain, at = _chain(ctx, lid)
    parts = [centerline(ll) for ll in chain]
    line = np.vstack([parts[0]] + [p[1:] for p in parts[1:]])
    s = arc_length(line)
    start = s[sum(len(p) - 1 for p in parts[:at])]
    profile = {}
    for name in ("left", "right"):
        ids, pts, z = [], [], []
        for ll in chain:
            skip = int(bool(ids) and ids[-1] == getattr(ll, f"{name}_ids")[0])  # 前后相接的 lanelet 共用端点
            ids += getattr(ll, f"{name}_ids")[skip:]
            pts.append(getattr(ll, name)[skip:])
            z.append(getattr(ll, f"{name}_z")[skip:])
        k, t, _ = nearest_on_line(np.vstack(pts), line)
        profile[name] = (ids, s[k] + t * (s[k + 1] - s[k]) - start, np.concatenate(z))
    return profile, chain, at, arc_length(parts[at])[-1]


def _locate(profile, lid, a):
    """突兀点 a 在 lid 的纵断面中的位置: (side, 沿中心线的距离 [n], 高程 [n])；不在 lid 的边界上时为 None"""
    side = next((s for l, s in a.users if l == lid), None)
    if side is None:
        return None
    ids, station, z = profile[side]
    if not all(n in ids for n in a.nodes):
        return None
    idx = [ids.index(n) for n in a.nodes]
    return side, station[idx], z[idx]


def _draw_profile(ax, ctx, lid, title, marks=(), focus=None, legend=False, ylabel=True):
    """
    lanelet lid 的纵断面 (前后各接一条前继 / 后继 lanelet，灰底为 lid)。
    marks: [(标签, 突兀点)]，用红圈标出；focus: (标签, 突兀点)，重点标出 (跳变画成红色的一段、横坡用虚线连到另一侧边界)，
    并只画它前后各 CONTEXT 米。没有 focus 时画整条 lid
    """
    profile, chain, at, length = _profile(ctx, lid)
    _style(ax)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.axvspan(0.0, length, color=LANE_SHADE, zorder=0)
    for name in ("left", "right"):
        _, station, z = profile[name]
        ax.plot(station, z, color=SIDE_COLORS[name], linewidth=1.5, marker="o", markersize=3.5,
                markeredgecolor=SURFACE, markeredgewidth=0.6, zorder=2, label=f"{name} boundary")

    must_show = []  # 纵轴范围中一定要包括的高程
    for k, (label, a) in enumerate(list(marks) + ([focus] if focus else [])):
        found = _locate(profile, lid, a)
        if found is None:
            continue
        _, x, y = found
        is_focus = focus is not None and a is focus[1]
        if is_focus and a.kind == "step":
            ax.plot(x, y, color=SURFACE, linewidth=4.5, zorder=3, solid_capstyle="round")
            ax.plot(x, y, color=ABRUPT, linewidth=2.5, zorder=4, solid_capstyle="round")
        elif is_focus and a.kind == "cross":  # 到另一侧边界的高差
            ax.plot([x[0], x[0]], [y[0], y[0] - a.dz], color=ABRUPT, linewidth=1.2, linestyle=":", zorder=3)
            must_show.append(y[0] - a.dz)
        ax.scatter(x, y, s=60, facecolors="none", edgecolors=ABRUPT, linewidths=1.4, zorder=5,
                   label="abrupt point" if k == 0 else None)
        ax.annotate(f"{a.dz:+.2f} m" if is_focus else label, (x.mean(), y.max()), xytext=(6, 4),
                    textcoords="offset points", fontsize=8, color=INK, zorder=6)
        must_show += list(y)

    # 横轴: focus 前后各 CONTEXT 米，否则整条 lid 加前后各 10 m；纵轴按其中的点取
    data_lo = min(min(p[1]) for p in profile.values()) - 1.0
    data_hi = max(max(p[1]) for p in profile.values()) + 1.0
    at_focus = _locate(profile, lid, focus[1]) if focus else None
    if at_focus is not None:
        centre = at_focus[1].mean()
        lo, hi = max(centre - CONTEXT, data_lo), min(centre + CONTEXT, data_hi)
    else:
        lo, hi = max(-10.0, data_lo), min(length + 10.0, data_hi)
    shown = np.concatenate([p[2][(p[1] >= lo) & (p[1] <= hi)] for p in profile.values()] + [np.array(must_show)])
    shown = shown[np.isfinite(shown)]
    if len(shown):
        pad = max(0.1, 0.1 * (shown.max() - shown.min()))
        ax.set_ylim(shown.min() - pad, shown.max() + pad)
    ax.set_xlim(lo, hi)
    ax.ticklabel_format(axis="y", useOffset=False)

    neighbors = [f"pred {chain[0].id}" if at else "", f"succ {chain[-1].id}" if chain[-1].id != lid else ""]
    ax.set_title(title, fontsize=9, color=INK, loc="left")
    ax.set_xlabel(f"distance along lanelet {lid} (m)" + "".join(f" · {n}" for n in neighbors if n),
                  color=MUTED, fontsize=8)
    if ylabel:
        ax.set_ylabel("elevation (m)", color=MUTED, fontsize=8)
    if legend:
        ax.legend(loc="best", fontsize=7, framealpha=0.9)


def _lanelet_summary(ll, issues):
    """单击 lanelet 时显示的说明"""
    z = np.r_[ll.left_z, ll.right_z]
    z = z[np.isfinite(z)]
    rise = (ll.left_z[-1] + ll.right_z[-1] - ll.left_z[0] - ll.right_z[0]) / 2.0  # 终点减起点 (两侧的平均)
    numbers = [f"#{k}" for k, a in enumerate(issues, 1) if ll.id in {lid for lid, _ in a.users}]
    length = arc_length(centerline(ll))[-1]
    return (f"lanelet {ll.id} ({ll.subtype}): {length:.1f} m"
            + (f", elevation {z.min():.2f} ~ {z.max():.2f} m" if len(z) else ", no elevation")
            + (f", {rise:+.2f} m from start to end" if np.isfinite(rise) else "")
            + (f", abrupt points {' '.join(numbers)}" if numbers else ", no abrupt point"))


def show_detail(ctx, lid, title, text, marks=(), focus=None):
    """
    在细节小窗口 (DETAIL_TITLE) 中显示 lanelet lid 的纵断面和说明 text，参数见 _draw_profile()。
    窗口已打开时重用 (内容替换)，被关掉后重新打开。返回 Figure
    """
    import matplotlib.pyplot as plt

    fig = plt.figure(num=DETAIL_TITLE, figsize=DETAIL_SIZE, clear=True)
    fig.set_layout_engine("constrained")
    fig.patch.set_facecolor(SURFACE)
    grid = fig.add_gridspec(2, 1, height_ratios=[4.0, 1.0])
    _draw_profile(fig.add_subplot(grid[0]), ctx, lid, title, marks, focus, legend=True)
    note = fig.add_subplot(grid[1])
    note.axis("off")
    note.text(0.0, 1.0, "\n".join(textwrap.wrap(text, 120)), transform=note.transAxes, va="top", ha="left",
              fontsize=8, color=INK_2)
    fig.canvas.draw_idle()
    with warnings.catch_warnings():  # 没有图形界面 (Agg) 时 show() 只给出警告
        warnings.simplefilter("ignore")
        fig.show()
    return fig


def _enable_click(fig, ax, ctx, lanelets, issues, lines):
    """
    俯视图窗口中单击 (缩放 / 平移模式下也可以，拖动不算)，与热力图窗口的操作一致:
      - 点中突兀点 (CLICK_PIXELS 像素以内，取最近的): 在细节小窗口中显示其纵断面 (前后各 CONTEXT 米) 和报告中的那一行，
        并在终端输出这一行
      - 点中 lanelet: 在细节小窗口中显示整条 lanelet 的纵断面，标出其上的突兀点
      - 点空白处: 取消俯视图中的橙色高亮 (细节小窗口保留)
    """
    from matplotlib.collections import PolyCollection
    from matplotlib.path import Path

    pos = np.array([a.pos for a in issues], dtype=float).reshape(-1, 2)
    paths = {lid: Path(ll.polygon) for lid, ll in lanelets.items()}
    outline = PolyCollection([], facecolors="none", edgecolors=HIGHLIGHT_COLOR, linewidths=2.0, zorder=7)
    ax.add_collection(outline)
    ring = ax.scatter(np.empty(0), np.empty(0), s=150, facecolors="none", edgecolors=HIGHLIGHT_COLOR, linewidths=2.0,
                      zorder=7)
    fig.text(0.005, 0.005, "click an abrupt point or a lanelet to show its elevation profile in a new window",
             fontsize=8, color=MUTED)
    pressed = {}

    def on_press(event):
        pressed["xy"] = (event.x, event.y)

    def on_release(event):
        start = pressed.pop("xy", None)
        if (event.inaxes is not ax or event.button != 1 or start is None
                or np.hypot(event.x - start[0], event.y - start[1]) > CLICK_PIXELS):
            return
        dist = np.linalg.norm(ax.transData.transform(pos) - (event.x, event.y), axis=1) if len(pos) else []
        hit = int(np.argmin(dist)) if len(dist) and min(dist) <= CLICK_PIXELS else None
        ids = sorted((lid for lid, path in paths.items() if path.contains_point((event.xdata, event.ydata))), key=int)
        if hit is not None:
            a = issues[hit]
            lid = a.users[0][0]
            print(lines[hit])
            ring.set_offsets(a.pos[None])
            show_detail(ctx, lid, _issue_title(hit + 1, a), lines[hit], focus=(f"#{hit + 1}", a))
        elif ids:
            lid = ids[0]
            text = _lanelet_summary(lanelets[lid], issues) + (f" (also here: lanelet {', '.join(ids[1:])})"
                                                              if len(ids) > 1 else "")
            print(text)
            ring.set_offsets(np.empty((0, 2)))
            marks = [(f"#{k}", a) for k, a in enumerate(issues, 1) if lid in {l for l, _ in a.users}]
            show_detail(ctx, lid, f"lanelet {lid}", text, marks=marks)
        else:
            lid = None
            ring.set_offsets(np.empty((0, 2)))
        outline.set_verts([lanelets[lid].polygon] if lid else [])
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("button_release_event", on_release)
