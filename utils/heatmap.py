"""
问题热力图: 按问题密度从蓝 (少) 到红 (多) 给地图着色，并在图中和日志中标注问题频发的区域 (需要 matplotlib)。
Using_LR 时按 p 加权: 有分数的 WARNING 权重为 p (真问题的概率)，没有分数的 WARNING 和 ERROR 权重为 1，
密度和区域都按权重之和 (即预计的真问题数) 计算；没有模型时权重都为 1，即问题条数
"""

import time
from collections import Counter

import numpy as np

from .geometry import centerline, fmt_xy

HEAT_COLORS = ("#2a78d6", "#9b4fc2", "#e34948")  # 问题密度从少到多: 蓝 -> 紫 -> 红
MIN_HOTSPOT_ISSUES = 2   # 半径内至少有几条问题才算问题频发的区域
MAX_CELLS = 1000         # 密度网格长边的最大格数
MAX_LISTED_LANELETS = 12
MARKERS = {"turn_direction": "o", "right_of_way": "s", "autoware": "^"}
SAVE_DPI = 150
SHOW_MAX_HEIGHT = 900    # 弹出窗口的最大高度 (像素)


def issue_points(out, lanelets):
    """
    Printer 记录的 WARNING / ERROR 的位置，返回 (坐标 [N, 2], [(检查项, lanelet ids)], 权重 [N])。
    没有给出坐标的取相关 lanelet 中心线中点的平均；与 lanelet 无关的 (如节点缺少高度、地图加载错误) 不参与
    """
    middles = {}

    def middle(lid):
        if lid not in middles:
            c = centerline(lanelets[lid])
            middles[lid] = c[len(c) // 2]
        return middles[lid]

    pts, items, weights = [], [], []
    for category, ids, pos, weight in out.located:
        if pos is None:
            known = [i for i in ids if i in lanelets]
            if not known:
                continue
            pos = np.mean([middle(i) for i in known], axis=0)
        pts.append(pos)
        items.append((category, ids))
        weights.append(weight)
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
    show: 同时在 pyplot 中创建窗口，之后由 show_heatmap() 弹出
    有模型 (Using_LR) 时按 p 加权，见本模块开头
    """
    weighted = out.model is not None
    unit = "p-weighted issues" if weighted else "issues"
    out.header(f"Hotspots (at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m)")
    pts, items, weights = issue_points(out, lanelets)
    if not len(pts):
        out.info("No issue located on the map, heatmap not generated")
        return

    spots = find_hotspots(pts, weights, radius, top)
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

    _save_image(path, lanelets, aois, pts, items, weights, weighted, spots, radius, map_name, show)
    out.info(f"Heatmap: {path}")


def show_heatmap():
    """弹出 report_hotspots(show=True) 创建的热力图窗口 (可缩放、平移)，关闭窗口后返回；没有图形界面时只输出提示"""
    import matplotlib
    import matplotlib.pyplot as plt

    if not plt.get_fignums():
        return
    if matplotlib.get_backend().lower() in ("agg", "pdf", "pgf", "ps", "svg", "cairo", "template"):
        print(f"Heatmap not shown: no display available (matplotlib backend {matplotlib.get_backend()})")
        return
    print("Showing heatmap, close the window to exit")
    plt.show()


def _save_image(path, lanelets, aois, pts, items, weights, weighted, spots, radius, map_name, show=False):
    """保存热力图；show 为 True 时再画一张同样的图放进 pyplot 的窗口 (按屏幕缩小)，保存的 png 不受影响"""
    from matplotlib.figure import Figure

    extent = _extent(lanelets, aois, pts, radius)
    dens, img_extent = density(pts, weights, extent, radius / 2)
    w, h = extent[2] - extent[0], extent[3] - extent[1]
    figsize = (12, float(np.clip(12 * h / w, 5, 14)))
    args = (lanelets, aois, pts, items, weights, weighted, spots, radius, map_name, extent, dens, img_extent)

    fig = Figure(figsize=figsize, dpi=SAVE_DPI, constrained_layout=True)
    _draw(fig, *args)
    fig.savefig(path)
    if show:
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=figsize, dpi=min(100.0, SHOW_MAX_HEIGHT / figsize[1]), constrained_layout=True)
        fig.canvas.manager.set_window_title(f"MapChecker heatmap: {map_name}")
        _draw(fig, *args)


def _draw(fig, lanelets, aois, pts, items, weights, weighted, spots, radius, map_name, extent, dens, img_extent):
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
        sel = [i for i, (c, _) in enumerate(items) if c == category]
        if sel:
            ax.scatter(pts[sel, 0], pts[sel, 1], s=14, marker=marker, facecolors="white", edgecolors="#222222",
                       linewidths=0.6, zorder=4, label=f"{category} ({len(sel)})")

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
    located = f"{len(pts)} issues located" + (f" (p-weighted {weights.sum():.1f})" if weighted else "")
    fig.suptitle(f"MapChecker issue heatmap: {map_name}\n{located}, {len(spots)} hotspots, "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}", fontsize=11, color="#1f1f1f")
    ax.legend(loc="best", fontsize=8, framealpha=0.9)  # 自动避开问题点和区域圆圈

    bar = fig.colorbar(ScalarMappable(norm, cmap), ax=ax, shrink=0.6, pad=0.01)
    bar.set_label(f"{unit} nearby (Gaussian weighted, sigma = {radius / 2:.0f} m)", color="#6b6b66")
    bar.ax.tick_params(colors="#6b6b66", labelsize=8)
