"""
问题热力图: 按问题密度从蓝 (少) 到红 (多) 给地图着色，并在图中和日志中标注问题频发的区域 (需要 matplotlib)。
Using_LR 时按 p 加权: 有分数的 WARNING 权重为 p (真问题的概率)，没有分数的 WARNING 和 ERROR 权重为 1，
密度和区域都按权重之和 (即预计的真问题数) 计算；没有模型时权重都为 1，即问题条数。
扩展检查 (ext/，检查项不在 MARKERS 中) 的问题只用绿色的点标出 (每项扩展一种形状)，不参与着色和区域。
弹出的窗口中单击问题点或 lanelet 显示 lanelet 编号，见 _enable_click()
"""

import sys
import time
from collections import Counter

import numpy as np

from .geometry import centerline, fmt_xy

HEAT_COLORS = ("#2a78d6", "#9b4fc2", "#e34948")  # 问题密度从少到多: 蓝 -> 紫 -> 红
MIN_HOTSPOT_ISSUES = 2   # 半径内至少有几条问题才算问题频发的区域
MAX_CELLS = 1000         # 密度网格长边的最大格数
MAX_LISTED_LANELETS = 12
MARKERS = {"turn_direction": "o", "right_of_way": "s", "autoware": "^"}
EXT_COLOR = "#2f9e44"    # 扩展检查 (ext/) 的问题点: 绿色，每项扩展依次用 EXT_MARKERS 中的一种形状
EXT_MARKERS = ("o", "D", "v", "P", "X", "*")
SAVE_DPI = 150
SHOW_MAX_HEIGHT = 900    # 弹出窗口的最大高度 (像素)
CLICK_PIXELS = 8         # 弹出窗口中: 离问题点多少像素以内算点中；按下和松开相距超过它算拖动 (缩放 / 平移)，不算单击
MAX_CLICK_LINES = 8      # 一次单击最多显示几条问题
HIGHLIGHT_COLOR = "#f08c00"


def issue_points(out, lanelets):
    """
    Printer 记录的 WARNING / ERROR 的位置，返回 (坐标 [N, 2], [(检查项, lanelet ids, p 或 None, 输出的那一行)], 权重 [N])，
    权重: 有分数的 WARNING 为 p，其余为 1。
    没有给出坐标的取相关 lanelet 中心线中点的平均；与 lanelet 无关的 (如节点缺少高度、地图加载错误) 不参与
    """
    middles = {}

    def middle(lid):
        if lid not in middles:
            c = centerline(lanelets[lid])
            middles[lid] = c[len(c) // 2]
        return middles[lid]

    pts, items, weights = [], [], []
    for category, ids, pos, p, line in out.located:
        if pos is None:
            known = [i for i in ids if i in lanelets]
            if not known:
                continue
            pos = np.mean([middle(i) for i in known], axis=0)
        pts.append(pos)
        items.append((category, ids, p, line))
        weights.append(1.0 if p is None else p)
    return np.array(pts, dtype=float).reshape(-1, 2), items, np.array(weights, dtype=float)


def find_hotspots(pts, weights, radius, top):
    """
    问题频发的区域: 每次取半径 radius 内剩余问题权重之和最大的问题为中心，区域内的问题不再参与之后的区域，
    直到找到 top 个区域或剩下的区域权重之和都小于 MIN_HOTSPOT_ISSUES。返回 [区域内问题的下标]，权重大的在前
    """
    near = np.linalg.norm(pts[:, None] - pts[None], axis=-1) <= radius
    left = np.ones(len(pts), dtype=bool)
    spots = []
    while len(spots) < top and left.any():
        counts = (near & left) @ weights * left
        i = int(np.argmax(counts))
        if counts[i] < MIN_HOTSPOT_ISSUES:
            break
        members = np.nonzero(near[i] & left)[0]
        left[members] = False
        spots.append(members)
    return spots


def density(pts, weights, extent, sigma):
    """高斯核密度，每条问题在其位置贡献其权重，即数值约为附近的问题数 (的权重之和)；返回 (网格 [H, W], 网格范围)"""
    x0, y0, x1, y1 = extent
    cell = max(x1 - x0, y1 - y0) / MAX_CELLS
    xs = np.arange(x0, x1 + cell, cell)
    ys = np.arange(y0, y1 + cell, cell)
    gx = np.exp(-0.5 * ((xs[None] - pts[:, :1]) / sigma) ** 2)  # [N, W]
    gy = np.exp(-0.5 * ((ys[None] - pts[:, 1:]) / sigma) ** 2)  # [N, H]
    return gy.T @ (gx * weights[:, None]), (xs[0] - cell / 2, xs[-1] + cell / 2, ys[0] - cell / 2, ys[-1] + cell / 2)


def _extent(lanelets, aois, pts, margin):
    """AOI (只检查 AOI 时) 或整张地图的范围，并包含所有问题"""
    shapes = aois or [ll.polygon for ll in lanelets.values()]
    allp = np.vstack(shapes + [pts])
    lo, hi = allp.min(axis=0) - margin, allp.max(axis=0) + margin
    return lo[0], lo[1], hi[0], hi[1]


def report_hotspots(lanelets, aois, out, path, radius, top, map_name, show=False):
    """
    在日志中列出问题频发的区域，并把热力图保存到 path (png)。
    aois: 只检查 AOI 时为 AOI 多边形，图的范围限制在 AOI；否则为空，画整张地图
    show: 同时在 pyplot 中创建窗口，之后由 show_windows() 弹出
    有模型 (Using_LR) 时按 p 加权，见本模块开头
    """
    weighted = out.model is not None
    unit = "p-weighted issues" if weighted else "issues"
    out.header(f"Hotspots (at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m)")
    pts, items, weights = issue_points(out, lanelets)
    if not len(pts):
        out.info("No issue located on the map, heatmap not generated")
        return

    core = np.array([item[0] in MARKERS for item in items], dtype=bool)  # 检查 1~3 的问题，扩展检查的不参与区域
    core_idx = np.nonzero(core)[0]
    spots = [core_idx[m] for m in find_hotspots(pts[core_idx], weights[core_idx], radius, top)]
    for k, members in enumerate(spots, 1):
        by_cat = Counter(items[m][0] for m in members)
        ids = sorted({i for m in members for i in items[m][1]}, key=int)
        listed = " ".join(ids[:MAX_LISTED_LANELETS]) + (f" ... (+{len(ids) - MAX_LISTED_LANELETS})"
                                                        if len(ids) > MAX_LISTED_LANELETS else "")
        size = f"{len(members)} issues" + (f", p-weighted {weights[members].sum():.1f}" if weighted else "")
        out.info(f"#{k:<2} center {fmt_xy(pts[members].mean(axis=0))}: {size} "
                 f"({', '.join(f'{c} {n}' for c, n in by_cat.most_common())}), lanelets: {listed}")
    if not spots:
        out.info(f"No area with at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m")
    ext = Counter(item[0] for item, c in zip(items, core) if not c)
    if ext:
        out.info(f"Ext checks ({', '.join(f'{c} {n}' for c, n in ext.items())}): not counted in hotspots, "
                 f"shown as green dots in the heatmap")

    _save_image(path, lanelets, aois, pts, items, weights, core, weighted, spots, radius, map_name, show)
    out.info(f"Heatmap: {path}")


def show_windows():
    """
    弹出 pyplot 中创建的窗口 (可缩放、平移): report_hotspots(show=True) 的热力图，以及扩展检查创建的窗口
    (如 altitude 的 isShow)；关闭所有窗口后返回。没有创建窗口时直接返回，没有图形界面时只输出提示
    """
    plt = sys.modules.get("matplotlib.pyplot")  # 没有导入过 pyplot 时不会有窗口，也不用导入
    if plt is None or not plt.get_fignums():
        return
    import matplotlib

    titles = ", ".join(plt.figure(n).canvas.manager.get_window_title() for n in plt.get_fignums())
    if matplotlib.get_backend().lower() in ("agg", "pdf", "pgf", "ps", "svg", "cairo", "template"):
        print(f"Windows not shown: no display available (matplotlib backend {matplotlib.get_backend()}): {titles}")
        return
    print(f"Showing {titles} (click an issue or a lanelet for details), close the windows to exit")
    plt.show()


def _save_image(path, lanelets, aois, pts, items, weights, core, weighted, spots, radius, map_name, show=False):
    """
    保存热力图；show 为 True 时再画一张同样的图放进 pyplot 的窗口 (按屏幕缩小)，保存的 png 不受影响。
    core: 每个问题是否属于检查 1~3，只有它们参与着色；扩展检查的问题只画绿色的点
    """
    from matplotlib.figure import Figure

    extent = _extent(lanelets, aois, pts, radius)
    dens, img_extent = density(pts[core], weights[core], extent, radius / 2)
    w, h = extent[2] - extent[0], extent[3] - extent[1]
    figsize = (12, float(np.clip(12 * h / w, 5, 14)))
    args = (lanelets, aois, pts, items, weights, core, weighted, spots, radius, map_name, extent, dens, img_extent)

    fig = Figure(figsize=figsize, dpi=SAVE_DPI, constrained_layout=True)
    _draw(fig, *args)
    fig.savefig(path)
    if show:
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=figsize, dpi=min(100.0, SHOW_MAX_HEIGHT / figsize[1]), constrained_layout=True)
        fig.canvas.manager.set_window_title(f"MapChecker heatmap: {map_name}")
        _enable_click(fig, _draw(fig, *args), lanelets, pts, items)


def _draw(fig, lanelets, aois, pts, items, weights, core, weighted, spots, radius, map_name, extent, dens,
          img_extent):
    from matplotlib.cm import ScalarMappable
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle

    # 孤立的单条问题 (密度 <= 1) 为蓝色，问题越密集越红；按 p 加权时 p 低的问题颜色更淡；没有问题的地方透明，露出地图
    unit = "p-weighted issues" if weighted else "issues"
    norm = Normalize(1.0, max(dens.max(), MIN_HOTSPOT_ISSUES + 1), clip=True)
    cmap = LinearSegmentedColormap.from_list("issues", HEAT_COLORS)
    rgba = cmap(norm(dens))
    rgba[..., 3] = 0.75 * np.clip(dens / 0.5, 0.0, 1.0)

    fig.patch.set_facecolor("#fcfcfb")
    ax = fig.add_subplot()
    ax.set_facecolor("#fcfcfb")

    ax.add_collection(PolyCollection([ll.polygon for ll in lanelets.values()], facecolors="#dcdcd8",
                                     edgecolors="#b5b5b0", linewidths=0.3, zorder=1))
    for k, poly in enumerate(aois):
        closed = np.vstack([poly, poly[:1]])
        ax.plot(closed[:, 0], closed[:, 1], color="#8a8a85", linewidth=1.0, linestyle="--", zorder=2,
                label="AOI" if k == 0 else None)
    ax.imshow(rgba, origin="lower", extent=img_extent, interpolation="bilinear", zorder=3)

    for category, marker in MARKERS.items():
        sel = [i for i, item in enumerate(items) if item[0] == category]
        if sel:
            ax.scatter(pts[sel, 0], pts[sel, 1], s=14, marker=marker, facecolors="white", edgecolors="#222222",
                       linewidths=0.6, zorder=4, label=f"{category} ({len(sel)})")
    for k, category in enumerate(dict.fromkeys(item[0] for item, c in zip(items, core) if not c)):
        sel = [i for i, item in enumerate(items) if item[0] == category]
        ax.scatter(pts[sel, 0], pts[sel, 1], s=16, marker=EXT_MARKERS[k % len(EXT_MARKERS)], facecolors=EXT_COLOR,
                   edgecolors="none", zorder=4, label=f"{category} ({len(sel)}, ext)")

    for k, members in enumerate(spots, 1):
        c = pts[members].mean(axis=0)
        r = max(radius / 2, np.linalg.norm(pts[members] - c, axis=1).max() + 5.0)
        ax.add_patch(Circle(c, r, fill=False, edgecolor="#1f1f1f", linewidth=1.2, zorder=5))
        size = f"{weights[members].sum():.1f}" if weighted else f"{len(members)}"
        ax.annotate(f"#{k}  {size}", c + r * np.sqrt(0.5), fontsize=9, fontweight="bold", color="#1f1f1f",
                    zorder=6, bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="none", alpha=0.85))
    if spots:
        ax.add_line(Line2D([], [], color="#1f1f1f", linewidth=1.2, label=f"hotspot (#rank  {unit})"))

    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)", color="#6b6b66")
    ax.set_ylabel("y (m)", color="#6b6b66")
    ax.tick_params(colors="#6b6b66", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#d0d0cc")
    located = f"{core.sum()} issues located" + (f" (p-weighted {weights[core].sum():.1f})" if weighted else "")
    if not core.all():
        located += f" + {(~core).sum()} ext (green dots)"
    fig.suptitle(f"MapChecker issue heatmap: {map_name}\n{located}, {len(spots)} hotspots, "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}", fontsize=11, color="#1f1f1f")
    ax.legend(loc="best", fontsize=8, framealpha=0.9)  # 自动避开问题点和区域圆圈

    bar = fig.colorbar(ScalarMappable(norm, cmap), ax=ax, shrink=0.6, pad=0.01)
    bar.set_label(f"{unit} nearby (Gaussian weighted, sigma = {radius / 2:.0f} m)", color="#6b6b66")
    bar.ax.tick_params(colors="#6b6b66", labelsize=8)
    return ax


def _enable_click(fig, ax, lanelets, pts, items):
    """
    弹出窗口中单击 (缩放 / 平移模式下也可以，拖动不算) 显示 lanelet 编号，并把相关 lanelet 用橙色描边:
      - 点中问题点: 显示该处所有问题的 lanelet、检查项和 p，并在终端输出这些问题在日志中的那一行
      - 否则: 显示点击处的 lanelet (重叠处可能有多条)
      - 点空白处: 隐藏
    """
    from matplotlib.collections import PolyCollection
    from matplotlib.path import Path

    label = ax.annotate("", (0, 0), xytext=(12, 12), textcoords="offset points", fontsize=9, color="#1f1f1f",
                        zorder=8, visible=False,
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#1f1f1f", alpha=0.95))
    highlight = PolyCollection([], facecolors="none", edgecolors=HIGHLIGHT_COLOR, linewidths=2.0, zorder=7)
    ax.add_collection(highlight)
    fig.text(0.005, 0.005, "click an issue or a lanelet to show its id", fontsize=8, color="#6b6b66")
    paths = {lid: Path(ll.polygon) for lid, ll in lanelets.items()}
    pressed = {}

    def on_press(event):
        pressed["xy"] = (event.x, event.y)

    def on_release(event):
        start = pressed.pop("xy", None)
        if (event.inaxes is not ax or event.button != 1 or start is None
                or np.hypot(event.x - start[0], event.y - start[1]) > CLICK_PIXELS):
            return
        dist = np.linalg.norm(ax.transData.transform(pts) - (event.x, event.y), axis=1)
        hits = [i for i in np.argsort(dist) if dist[i] <= CLICK_PIXELS]
        if hits:
            xy = pts[hits[0]]
            ids = sorted({i for h in hits for i in items[h][1]}, key=int)
            lines = [f"lanelet {' / '.join(items[h][1]) or '-'}  {items[h][0]}"
                     + ("" if items[h][2] is None else f"  p={items[h][2]:.2f}") for h in hits[:MAX_CLICK_LINES]]
            if len(hits) > MAX_CLICK_LINES:
                lines.append(f"... (+{len(hits) - MAX_CLICK_LINES})")
            print("\n".join(items[h][3] for h in hits))
        else:
            xy = (event.xdata, event.ydata)
            ids = sorted((lid for lid, path in paths.items() if path.contains_point(xy)), key=int)
            lines = [f"lanelet {', '.join(ids)}"] if ids else []
            if ids:
                print(lines[0])
        highlight.set_verts([lanelets[i].polygon for i in ids if i in lanelets])
        label.set_visible(bool(lines))
        if lines:
            # 文字放在点击处朝向图中心的一侧，避免超出图的范围
            box = ax.bbox
            right, top = event.x > (box.x0 + box.x1) / 2, event.y > (box.y0 + box.y1) / 2
            label.xy = xy
            label.xyann = (-12 if right else 12, -12 if top else 12)
            label.set_horizontalalignment("right" if right else "left")
            label.set_verticalalignment("top" if top else "bottom")
            label.set_text("\n".join(lines))
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("button_press_event", on_press)
    fig.canvas.mpl_connect("button_release_event", on_release)
