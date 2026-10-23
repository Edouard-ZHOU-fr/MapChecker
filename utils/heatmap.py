"""
Issue heatmap: colors the map by issue density from blue (few) to red (many), and labels hotspots in the figure and
in the log (requires matplotlib).
With Using_LR, weighted by p: a scored WARNING has weight p (probability of a true issue), unscored WARNINGs and ERRORs
have weight 1; density and hotspots use the sum of weights (i.e. the expected number of true issues); without a model,
all weights are 1, i.e. the number of issues.
Issues of extension checks (ext/, checks not in MARKERS) are only marked with green dots (one shape per extension),
and are not used for coloring or hotspots. ERRORs of all checks are marked with red dots.
In the pop-up window, clicking an issue point or a lanelet shows the lanelet id, see _enable_click()
"""

import sys
import time
from collections import Counter

import numpy as np

from .geometry import centerline, fmt_xy

HEAT_COLORS = ("#2a78d6", "#9b4fc2", "#e34948")  # issue density from low to high: blue -> purple -> red
MIN_HOTSPOT_ISSUES = 2   # minimum number of issues within the radius for a hotspot
MAX_CELLS = 1000         # maximum number of cells along the long side of the density grid
MAX_LISTED_LANELETS = 12
MARKERS = {"turn_direction": "o", "right_of_way": "s", "autoware": "^"}
EXT_COLOR = "#2f9e44"    # extension check (ext/) issue points: green, each extension uses the next shape in EXT_MARKERS
EXT_MARKERS = ("o", "D", "v", "P", "X", "*")
ERROR_COLOR = "#e03131"  # ERROR points (all checks): red dots, drawn above the WARNING points
SAVE_DPI = 150
SHOW_MAX_HEIGHT = 900    # maximum height of the pop-up window (pixels)
CLICK_PIXELS = 8         # in the pop-up window: within this many pixels of an issue point counts as a hit; press and
                         # release farther apart than this counts as a drag (zoom / pan), not a click
MAX_CLICK_LINES = 8      # maximum number of issues shown per click
HIGHLIGHT_COLOR = "#f08c00"


def issue_points(out, lanelets, radius):
    """
    Positions of the WARNINGs / ERRORs recorded by Printer; returns
    (coordinates [N, 2], [(check, lanelet ids, p or None, the output line)], weights [N]),
    weights: p for a scored WARNING, 1 otherwise.
    Without given coordinates, the mean of the centerline midpoints of the related lanelets is used; if they are more
    than radius apart (e.g. a right_of_way referencing a lanelet of another intersection), the mean would fall in an
    unrelated place, the midpoint of the first lanelet is used instead. Issues not related to a lanelet (e.g. node
    missing height, map loading error) are not included
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
            mids = np.array([middle(i) for i in known])
            far = np.linalg.norm(mids[:, None] - mids[None], axis=-1).max() > radius
            pos = mids[0] if far else mids.mean(axis=0)
        pts.append(pos)
        items.append((category, ids, p, line))
        weights.append(1.0 if p is None else p)
    return np.array(pts, dtype=float).reshape(-1, 2), items, np.array(weights, dtype=float)


def find_hotspots(pts, weights, radius, top):
    """
    Hotspots: repeatedly take as center the issue with the largest sum of remaining issue weights within radius; issues
    in that hotspot no longer take part in later hotspots, until top hotspots are found or the remaining hotspot weight
    sums are all below MIN_HOTSPOT_ISSUES. Returns [indices of the issues in each hotspot], largest weight first
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
    """
    Gaussian kernel density, each issue contributes its weight at its position, i.e. the value is roughly the number
    of nearby issues (sum of their weights); returns (grid [H, W], grid extent)
    """
    x0, y0, x1, y1 = extent
    cell = max(x1 - x0, y1 - y0) / MAX_CELLS
    xs = np.arange(x0, x1 + cell, cell)
    ys = np.arange(y0, y1 + cell, cell)
    gx = np.exp(-0.5 * ((xs[None] - pts[:, :1]) / sigma) ** 2)  # [N, W]
    gy = np.exp(-0.5 * ((ys[None] - pts[:, 1:]) / sigma) ** 2)  # [N, H]
    return gy.T @ (gx * weights[:, None]), (xs[0] - cell / 2, xs[-1] + cell / 2, ys[0] - cell / 2, ys[-1] + cell / 2)


def _extent(lanelets, aois, pts, margin):
    """Extent of the AOI (when only checking the AOI) or of the whole map, including all issues"""
    shapes = aois or [ll.polygon for ll in lanelets.values()]
    allp = np.vstack(shapes + [pts])
    lo, hi = allp.min(axis=0) - margin, allp.max(axis=0) + margin
    return lo[0], lo[1], hi[0], hi[1]


def report_hotspots(lanelets, aois, out, path, radius, top, map_name, show=False):
    """
    List hotspots in the log and save the heatmap to path (png).
    aois: AOI polygons when only checking the AOI, the figure is limited to the AOI; otherwise empty, draw the whole map
    show: also create a window in pyplot, popped up later by show_windows()
    With a model (Using_LR), weighted by p, see the beginning of this module
    """
    weighted = out.model is not None
    unit = "p-weighted issues" if weighted else "issues"
    out.header(f"Hotspots (at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m)")
    pts, items, weights = issue_points(out, lanelets, radius)
    if not len(pts):
        out.info("No issue located on the map, heatmap not generated")
        return

    core = np.array([item[0] in MARKERS for item in items], dtype=bool)  # Check 1~3 issues; extension checks not in hotspots
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
    Pop up the windows created in pyplot (zoomable, pannable): the heatmap of report_hotspots(show=True), and windows
    created by extension checks (e.g. isShow of altitude); returns after all windows are closed. Returns directly if no
    window was created; without a GUI, only prints a message
    """
    plt = sys.modules.get("matplotlib.pyplot")  # if pyplot was never imported there is no window, no need to import it
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
    Save the heatmap; if show is True, draw the same figure again in a pyplot window (scaled down to the screen),
    the saved png is unaffected.
    core: whether each issue belongs to Check 1~3, only those are used for coloring; extension check issues are only
    drawn as green dots
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

    # An isolated single issue (density <= 1) is blue, the denser the issues the redder; when weighted by p, low-p issues
    # are fainter; areas without issues are transparent, showing the map
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

    error = np.array([item[3].startswith("[ERROR]") for item in items], dtype=bool)
    for category, marker in MARKERS.items():
        sel = [i for i, item in enumerate(items) if item[0] == category and not error[i]]
        if sel:
            ax.scatter(pts[sel, 0], pts[sel, 1], s=14, marker=marker, facecolors="white", edgecolors="#222222",
                       linewidths=0.6, zorder=4, label=f"{category} ({len(sel)})")
    for k, category in enumerate(dict.fromkeys(item[0] for item, c in zip(items, core) if not c)):
        sel = [i for i, item in enumerate(items) if item[0] == category and not error[i]]
        if sel:
            ax.scatter(pts[sel, 0], pts[sel, 1], s=16, marker=EXT_MARKERS[k % len(EXT_MARKERS)],
                       facecolors=EXT_COLOR, edgecolors="none", zorder=4, label=f"{category} ({len(sel)}, ext)")
    if error.any():
        ax.scatter(pts[error, 0], pts[error, 1], s=30, marker="o", facecolors=ERROR_COLOR, edgecolors="#1f1f1f",
                   linewidths=0.7, zorder=4.5, label=f"ERROR ({error.sum()})")

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
    ax.legend(loc="best", fontsize=8, framealpha=0.9)  # automatically avoids issue points and hotspot circles

    bar = fig.colorbar(ScalarMappable(norm, cmap), ax=ax, shrink=0.6, pad=0.01)
    bar.set_label(f"{unit} nearby (Gaussian weighted, sigma = {radius / 2:.0f} m)", color="#6b6b66")
    bar.ax.tick_params(colors="#6b6b66", labelsize=8)
    return ax


def _enable_click(fig, ax, lanelets, pts, items):
    """
    Clicking in the pop-up window (also in zoom / pan mode, dragging doesn't count) shows lanelet ids, and outlines the
    related lanelets in orange:
      - hit an issue point: show the lanelets, check and p of all issues there, and print their log lines in the terminal
      - otherwise: show the lanelet(s) at the clicked location (possibly several where they overlap)
      - click on empty space: hide
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
            # place the text on the side of the click facing the figure center, so it doesn't go beyond the figure
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
