"""
Issue heatmap: colors the map by issue density from blue (few) to red (many), and labels hotspots in the figure and
in the log (requires matplotlib).
With Using_LR, weighted by p: a scored WARNING has weight p (probability of a true issue), unscored WARNINGs and ERRORs
have weight 1; density and hotspots use the sum of weights (i.e. the expected number of true issues); without a model,
all weights are 1, i.e. the number of issues.
Issues of extension checks (ext/, checks not in MARKERS) are only marked with green dots (one shape per extension),
and are not used for coloring or hotspots. ERRORs of all checks are marked with red dots.
With isShow, the same map is shown after the report in a Tk window (utils/heatmap_window.py: pan / zoom without
distortion, layers, list of the hotspots and issues, details of the selection); show_windows() opens it together
with the pyplot windows of the extension checks
"""

import os
import sys
import time
from collections import Counter, namedtuple

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
SHOW_MAX_HEIGHT = 900    # maximum height of the pyplot windows of the extension checks (pixels), e.g. altitude
CLICK_PIXELS = 8         # in the windows: within this many pixels of an issue point counts as a hit; press and
                         # release farther apart than this counts as a drag (zoom / pan), not a click
HIGHLIGHT_COLOR = "#f08c00"
NON_GUI_BACKENDS = ("agg", "pdf", "pgf", "ps", "svg", "cairo", "template")

# What report_hotspots() draws, in the png and in the window: pts / items / weights as returned by issue_points()
# (without the issues hidden by map_min_p), core: whether each issue belongs to Check 1~3, spots: indices of the issues
# of each hotspot, note: issues hidden by map_min_p, stamp: time of the report, extent: area drawn (x0, y0, x1, y1),
# dens / img_extent: density(), hidden: number of issues hidden by map_min_p (min_p)
Heatmap = namedtuple("Heatmap", "lanelets aois pts items weights core weighted spots radius map_name note stamp "
                                "extent dens img_extent hidden min_p", defaults=(0, 0.0))
# A layer of the drawing, i.e. an entry of the legend:
#   kind:    "density", "aoi", "check" (issue points of Check 1~3), "ext" (of an extension check), "errors" (ERRORs of
#            all checks) or "hotspots"
#   targets: the items the window can go to, each one the indices of its issues: one issue per target for the points,
#            in the order of the output (unscored first, then by descending p); one hotspot per target, by rank
#   symbol:  legend icon in the window: scatter() arguments (with "marker"), plot() arguments, or None (density)
Layer = namedtuple("Layer", "kind label artists targets symbol")

_pending = []  # Heatmaps of report_hotspots(show=True), shown by show_windows()


def issue_position(reported, lanelets, radius, middles):
    """
    Position of a WARNING / ERROR recorded by Printer (utils.printer.Reported): the coordinates given by the check, else
    the mean of the centerline midpoints of the related lanelets; if they are more than radius apart (e.g. a
    right_of_way referencing a lanelet of another intersection), the mean would fall in an unrelated place, the
    midpoint of the first lanelet is used instead. None for an issue not related to a lanelet (e.g. node missing
    height, map loading error). middles: cache {lanelet id: centerline midpoint}, shared between calls
    """
    if reported.pos is not None:
        return np.asarray(reported.pos, dtype=float)
    known = [i for i in reported.ids if i in lanelets]
    if not known:
        return None
    for lid in known:
        if lid not in middles:
            c = centerline(lanelets[lid])
            middles[lid] = c[len(c) // 2]
    mids = np.array([middles[i] for i in known])
    far = np.linalg.norm(mids[:, None] - mids[None], axis=-1).max() > radius
    return mids[0] if far else mids.mean(axis=0)


def issue_points(out, lanelets, radius):
    """
    Positions of the WARNINGs / ERRORs recorded by Printer (see issue_position); returns
    (coordinates [N, 2], [(check, lanelet ids, p or None, the output line)], weights [N]),
    weights: p for a scored WARNING, 1 otherwise. Issues without a position are not included
    """
    middles = {}
    pts, items, weights = [], [], []
    for r in out.located:
        pos = issue_position(r, lanelets, radius, middles)
        if pos is None:
            continue
        pts.append(pos)
        items.append((r.category, r.ids, r.p, r.line))
        weights.append(1.0 if r.p is None else r.p)
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


def hotspot_text(pts, items, weights, weighted, members):
    """Description of a hotspot (indices members of its issues), as in the log: center, size, checks, lanelets"""
    by_cat = Counter(items[m][0] for m in members)
    ids = sorted({i for m in members for i in items[m][1]}, key=int)
    listed = " ".join(ids[:MAX_LISTED_LANELETS]) + (f" ... (+{len(ids) - MAX_LISTED_LANELETS})"
                                                    if len(ids) > MAX_LISTED_LANELETS else "")
    size = f"{len(members)} issues" + (f", p-weighted {weights[members].sum():.1f}" if weighted else "")
    return (f"center {fmt_xy(pts[members].mean(axis=0))}: {size} "
            f"({', '.join(f'{c} {n}' for c, n in by_cat.most_common())}), lanelets: {listed}")


def report_hotspots(lanelets, aois, out, path, radius, top, map_name, show=False, min_p=0.0):
    """
    List hotspots in the log and save the heatmap to path (png).
    aois:  AOI polygons when only checking the AOI, the figure is limited to the AOI; otherwise empty, draw the whole map
    show:  also show the heatmap in a window (utils/heatmap_window.py), opened after the report by show_windows()
    min_p: with a model, scored WARNINGs with p < min_p are not shown on the map (points, coloring, hotspots, window);
           the report still lists them
    With a model (Using_LR), weighted by p, see the beginning of this module.
    Returns the issue coordinates of each hotspot ([N, 2] each, in rank order), used for the masque (utils/masque.py)
    """
    weighted = out.model is not None
    unit = "p-weighted issues" if weighted else "issues"
    out.header(f"Hotspots (at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m)")
    pts, items, weights = issue_points(out, lanelets, radius)
    note, hidden = "", 0
    if weighted and min_p > 0:
        keep = np.array([item[2] is None or item[2] >= min_p for item in items], dtype=bool)
        hidden = int((~keep).sum())
        if hidden:
            pts, weights, items = pts[keep], weights[keep], [item for item, k in zip(items, keep) if k]
            note = f" ({hidden} with p < {min_p:.2f} hidden)"
            out.info(f"{hidden} scored warnings with p < {min_p:.2f} not shown on the map (map_min_p)")
    if not len(pts):
        out.info("No issue located on the map, heatmap not generated")
        return []

    core = np.array([item[0] in MARKERS for item in items], dtype=bool)  # Check 1~3 issues; extension checks not in hotspots
    core_idx = np.nonzero(core)[0]
    spots = [core_idx[m] for m in find_hotspots(pts[core_idx], weights[core_idx], radius, top)]
    for k, members in enumerate(spots, 1):
        out.info(f"#{k:<2} {hotspot_text(pts, items, weights, weighted, members)}")
    if not spots:
        out.info(f"No area with at least {MIN_HOTSPOT_ISSUES} {unit} within {radius:.0f} m")
    ext = Counter(item[0] for item, c in zip(items, core) if not c)
    if ext:
        out.info(f"Ext checks ({', '.join(f'{c} {n}' for c, n in ext.items())}): not counted in hotspots, "
                 f"shown as green dots in the heatmap")

    extent = _extent(lanelets, aois, pts, radius)
    dens, img_extent = density(pts[core], weights[core], extent, radius / 2)
    heat = Heatmap(lanelets, aois, pts, items, weights, core, weighted, spots, radius, map_name, note,
                   time.strftime("%Y-%m-%d %H:%M:%S"), extent, dens, img_extent, hidden, min_p)
    _save_image(path, heat)
    if show:
        _pending.append(heat)
    out.info(f"Heatmap: {path}")
    return [pts[members] for members in spots]


def prepare_windows():
    """
    Called before the checks when the heatmap window will be shown: the pyplot windows of the extension checks (e.g.
    isShow of altitude) then use the Tk backend, so that show_windows() shows them together with the heatmap window,
    in the same event loop. Does nothing if pyplot is already in use, if MPLBACKEND is set, or without tkinter or a
    display (no window is shown then, see show_windows())
    """
    if "matplotlib.pyplot" in sys.modules or os.environ.get("MPLBACKEND"):
        return
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return
    import matplotlib

    matplotlib.use("TkAgg")


def _tk_root():
    """A new Tk main window, None without tkinter or a display"""
    try:
        import tkinter
    except ImportError:
        return None
    try:
        return tkinter.Tk(className="MapChecker")
    except tkinter.TclError:
        return None


def show_windows():
    """
    Pop up the windows: the heatmap window of report_hotspots(show=True) (Tk, utils/heatmap_window.py) and the windows
    created in pyplot by extension checks (e.g. isShow of altitude); returns after all windows are closed. Returns
    directly if no window was created; without a GUI, only prints a message
    """
    heatmaps = list(_pending)
    _pending.clear()
    plt = sys.modules.get("matplotlib.pyplot")  # if pyplot was never imported there is no window, no need to import it
    figs = [plt.figure(n) for n in plt.get_fignums()] if plt is not None else []
    if not heatmaps and not figs:
        return
    import matplotlib

    backend = matplotlib.get_backend()
    windows, hidden = [], []
    for h in heatmaps:
        root = _tk_root()
        if root is None:
            hidden.append(f"MapChecker heatmap: {h.map_name}")
            continue
        from .heatmap_window import HeatmapWindow

        windows.append(HeatmapWindow(root, h))
    if backend.lower() in NON_GUI_BACKENDS:
        hidden += [f.canvas.manager.get_window_title() for f in figs]
        figs = []
    if hidden:
        print(f"Windows not shown: no display available (matplotlib backend {backend}): {', '.join(hidden)}")
    if not windows and not figs:
        return
    titles = [w.root.title() for w in windows] + [f.canvas.manager.get_window_title() for f in figs]
    print(f"Showing {', '.join(titles)} (click an issue or a lanelet for details), close the windows to exit")
    if not windows:
        plt.show()
    elif figs and "tk" not in backend.lower():
        windows[0].root.mainloop()
        plt.show()  # pyplot of another GUI toolkit (prepare_windows() not called): after the heatmap window
    else:
        if figs:
            plt.show(block=False)  # Tk windows of pyplot: in the same event loop as the heatmap window
        windows[0].root.mainloop()  # runs until all Tk windows (heatmap and pyplot) are closed


def _save_image(path, h):
    """Save heatmap h (png)"""
    from matplotlib.figure import Figure

    w, ht = h.extent[2] - h.extent[0], h.extent[3] - h.extent[1]
    fig = Figure(figsize=(12, float(np.clip(12 * ht / w, 5, 14))), dpi=SAVE_DPI, constrained_layout=True)
    _draw(fig, h)
    fig.savefig(path)


def _unit(h):
    return "p-weighted issues" if h.weighted else "issues"


def color_scale(h):
    """
    Colors of the density: (norm, cmap). An isolated single issue (density <= 1) is blue, the denser the issues the
    redder
    """
    from matplotlib.colors import LinearSegmentedColormap, Normalize

    norm = Normalize(1.0, max(h.dens.max(), MIN_HOTSPOT_ISSUES + 1), clip=True)
    return norm, LinearSegmentedColormap.from_list("issues", HEAT_COLORS)


def scale_label(h):
    """Label of the color scale"""
    return f"{_unit(h)} nearby (Gaussian weighted, sigma = {h.radius / 2:.0f} m)"


def summary(h):
    """Number of issues shown and of hotspots"""
    located = (f"{h.core.sum()} issues located" + (f" (p-weighted {h.weights[h.core].sum():.1f})" if h.weighted else "")
               + h.note)
    if not h.core.all():
        located += f" + {(~h.core).sum()} ext (green dots)"
    return f"{located}, {len(h.spots)} hotspots"


def style_axes(ax):
    """Colors of the map axes (png and window)"""
    ax.set_facecolor("#fcfcfb")
    ax.tick_params(colors="#6b6b66", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#d0d0cc")


def draw_layers(ax, h):
    """
    Draw heatmap h in ax, the same in the png and in the window: lanelets, AOI, density coloring, issue points and
    hotspots. Returns the layers ([Layer]): density, AOI, then the entries of the legend in order
    """
    from matplotlib.collections import PolyCollection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle

    # when weighted by p, low-p issues are fainter; areas without issues are transparent, showing the map
    norm, cmap = color_scale(h)
    rgba = cmap(norm(h.dens))
    rgba[..., 3] = 0.75 * np.clip(h.dens / 0.5, 0.0, 1.0)
    pts, items = h.pts, h.items

    ax.add_collection(PolyCollection([ll.polygon for ll in h.lanelets.values()], facecolors="#dcdcd8",
                                     edgecolors="#b5b5b0", linewidths=0.3, zorder=1))
    aoi_style = dict(color="#8a8a85", linewidth=1.0, linestyle="--")
    aoi = []
    for k, poly in enumerate(h.aois):
        closed = np.vstack([poly, poly[:1]])
        aoi += ax.plot(closed[:, 0], closed[:, 1], zorder=2, label="AOI" if k == 0 else None, **aoi_style)
    image = ax.imshow(rgba, origin="lower", extent=h.img_extent, interpolation="bilinear", zorder=3)
    layers = [Layer("density", "density coloring", [image], [], None)]
    if aoi:
        layers.append(Layer("aoi", "AOI", aoi, [], aoi_style))

    def points(kind, sel, label, zorder, **style):
        targets = [[i] for i in sorted(sel, key=lambda i: (items[i][2] is not None, -(items[i][2] or 0.0)))]
        artist = ax.scatter(pts[sel, 0], pts[sel, 1], zorder=zorder, label=label, **style)
        layers.append(Layer(kind, label, [artist], targets, style))

    error = np.array([item[3].startswith("[ERROR") for item in items], dtype=bool)
    for category, marker in MARKERS.items():
        sel = [i for i, item in enumerate(items) if item[0] == category and not error[i]]
        if sel:
            points("check", sel, f"{category} ({len(sel)})", 4, s=14, marker=marker, facecolors="white",
                   edgecolors="#222222", linewidths=0.6)
    for k, category in enumerate(dict.fromkeys(item[0] for item, c in zip(items, h.core) if not c)):
        sel = [i for i, item in enumerate(items) if item[0] == category and not error[i]]
        if sel:
            points("ext", sel, f"{category} ({len(sel)}, ext)", 4, s=16, marker=EXT_MARKERS[k % len(EXT_MARKERS)],
                   facecolors=EXT_COLOR, edgecolors="none")
    if error.any():
        sel = list(np.nonzero(error)[0])
        points("errors", sel, f"ERROR ({len(sel)})", 4.5, s=30, marker="o", facecolors=ERROR_COLOR,
               edgecolors="#1f1f1f", linewidths=0.7)

    spots = []
    for k, members in enumerate(h.spots, 1):
        c = pts[members].mean(axis=0)
        r = max(h.radius / 2, np.linalg.norm(pts[members] - c, axis=1).max() + 5.0)
        spots.append(ax.add_patch(Circle(c, r, fill=False, edgecolor="#1f1f1f", linewidth=1.2, zorder=5)))
        size = f"{h.weights[members].sum():.1f}" if h.weighted else f"{len(members)}"
        spots.append(ax.annotate(f"#{k}  {size}", c + r * np.sqrt(0.5), fontsize=9, fontweight="bold",
                                 color="#1f1f1f", zorder=6,
                                 bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="none",
                                           alpha=0.85)))
    if h.spots:
        label = f"hotspot (#rank  {_unit(h)})"
        spots.append(ax.add_line(Line2D([], [], color="#1f1f1f", linewidth=1.2, label=label)))  # legend of the png
        layers.append(Layer("hotspots", label, spots, [list(members) for members in h.spots],
                            dict(marker="o", s=90, facecolors="none", edgecolors="#1f1f1f", linewidths=1.2)))
    return layers


def _draw(fig, h):
    """The png: map, title, legend and color scale"""
    from matplotlib.cm import ScalarMappable

    fig.patch.set_facecolor("#fcfcfb")
    ax = fig.add_subplot()
    style_axes(ax)
    draw_layers(ax, h)
    ax.set_xlim(h.extent[0], h.extent[2])
    ax.set_ylim(h.extent[1], h.extent[3])
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)", color="#6b6b66")
    ax.set_ylabel("y (m)", color="#6b6b66")
    fig.suptitle(f"MapChecker issue heatmap: {h.map_name}\n{summary(h)}, {h.stamp}", fontsize=11, color="#1f1f1f")
    ax.legend(loc="best", fontsize=8, framealpha=0.9)  # automatically avoids issue points and hotspot circles

    bar = fig.colorbar(ScalarMappable(*color_scale(h)), ax=ax, shrink=0.6, pad=0.01)
    bar.set_label(scale_label(h), color="#6b6b66")
    bar.ax.tick_params(colors="#6b6b66", labelsize=8)
