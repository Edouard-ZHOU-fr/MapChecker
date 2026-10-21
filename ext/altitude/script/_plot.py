"""
Elevation trend chart of the altitude extension (requires matplotlib), called by check_altitude.check():
  save_trend_chart()  saves a png when plot is true:
    top: top view, checked vehicle lanelets colored by elevation (light blue low, dark blue high, one segment
        every ~SEGMENT m), red circles are abrupt points, #k is the number in the report
    bottom: elevation profiles of the lanes of the max_profiles abrupt points with the largest height difference:
        elevation of the left and right boundaries vs. distance along the lane centerline,
        with one predecessor and one successor lanelet attached (gray background: the lanelet of the abrupt
        point), abrupt points marked in red
  open_window()       if isShow is true, creates the top view window in pyplot (separate from the heatmap), which
                      check_lanelet_map.py pops up together with the heatmap after the report
                      (utils.heatmap.show_windows); clicking an abrupt point or a lanelet shows its elevation
                      profile in another small window, see _enable_click()
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
OTHER_LANELETS = "#e4e3de"   # unchecked lanelets (crosswalk, outside the AOI, etc.)
NO_ELEVATION = "#9b9a94"     # parts without ele
ELEVATION_RAMP = ("#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b")  # low to high, single-hue blue
SIDE_COLORS = {"left": "#2a78d6", "right": "#eb6834"}
ABRUPT = "#d03b3b"
LANE_SHADE = "#f0efec"
SEGMENT = 2.0          # length of each segment colored by elevation in the top view (m)
CONTEXT = 40.0         # length drawn on each side of the abrupt point in the elevation profile (m)
NCOLS = 3
PLAN_WIDTH = 12.0      # figure width (inches)
PROFILE_HEIGHT = 2.6   # height of each row of elevation profiles (inches)
DPI = 150
DETAIL_TITLE = "MapChecker altitude detail"  # title of the detail window, the same window is reused on repeated clicks
DETAIL_SIZE = (8.0, 4.8)                     # size of the detail window (inches)


def save_trend_chart(path, ctx, lanelets, issues, max_profiles):
    """
    lanelets: checked vehicle lanelets {id: Lanelet}; issues: reported abrupt points (check_altitude.Abrupt), the k-th
    is numbered #k+1, an elevation profile is drawn for the first max_profiles of them
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
    Creates the pyplot window of the top view (not popped up here; utils.heatmap.show_windows() pops it up together
    with the heatmap after the report).
    lines: the report line of each abrupt point, printed to the terminal on click; n_labeled: the first n_labeled
    abrupt points get their number labeled in the plot. Returns the Figure
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
    """
    Extent (lower left corner, upper right corner) and height (inches, width is PLAN_WIDTH) of the top view:
    the AOI (when only checking the AOI) or the checked lanelets
    """
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
    """Lanelet split into quads [n, 4, 2] of ~SEGMENT m and mean elevation of each [n] (nan if a point has no ele)"""
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
        # color range from 1% ~ 99%, so a few absurd elevations don't compress the colors of the rest
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
        # abrupt points close to each other share one label "#1 #3"
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
    """
    Lanelet lid with one vehicle lanelet attached before and after (the predecessor / successor with the smallest id):
    ([Lanelet], index of lid in it)
    """
    succ, pred = ctx.topology
    before = min((i for i in pred[lid] if i in ctx.vehicle), key=int, default=None)
    after = min((i for i in succ[lid] if i in ctx.vehicle and i != before), key=int, default=None)
    chain = [ctx.lanelets[i] for i in (before, lid, after) if i is not None]
    return chain, int(before is not None)


def _profile(ctx, lid):
    """
    Elevation profile of lanelet lid (one lanelet attached before and after): ({"left" / "right": (node ids, distance
    along the centerline, elevations)}, chain, index of lid, length of lid);
    distance is measured along the centerline of the whole chain, 0 is the start of lid, the points on the left and
    right boundaries are all projected onto the centerline
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
            skip = int(bool(ids) and ids[-1] == getattr(ll, f"{name}_ids")[0])  # consecutive lanelets share the end point
            ids += getattr(ll, f"{name}_ids")[skip:]
            pts.append(getattr(ll, name)[skip:])
            z.append(getattr(ll, f"{name}_z")[skip:])
        k, t, _ = nearest_on_line(np.vstack(pts), line)
        profile[name] = (ids, s[k] + t * (s[k + 1] - s[k]) - start, np.concatenate(z))
    return profile, chain, at, arc_length(parts[at])[-1]


def _locate(profile, lid, a):
    """
    Position of abrupt point a in the elevation profile of lid: (side, distance along the centerline [n],
    elevations [n]); None if it is not on a boundary of lid
    """
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
    Elevation profile of lanelet lid (one predecessor / successor lanelet attached before and after, gray background
    is lid).
    marks: [(label, abrupt point)], marked with red circles; focus: (label, abrupt point), highlighted (a step is
    drawn as a red segment, a cross slope is joined to the other boundary with a dotted line),
    and only CONTEXT m before and after it are drawn. Without focus the whole lid is drawn
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

    must_show = []  # elevations that must be included in the y-axis range
    for k, (label, a) in enumerate(list(marks) + ([focus] if focus else [])):
        found = _locate(profile, lid, a)
        if found is None:
            continue
        _, x, y = found
        is_focus = focus is not None and a is focus[1]
        if is_focus and a.kind == "step":
            ax.plot(x, y, color=SURFACE, linewidth=4.5, zorder=3, solid_capstyle="round")
            ax.plot(x, y, color=ABRUPT, linewidth=2.5, zorder=4, solid_capstyle="round")
        elif is_focus and a.kind == "cross":  # height difference to the other boundary
            ax.plot([x[0], x[0]], [y[0], y[0] - a.dz], color=ABRUPT, linewidth=1.2, linestyle=":", zorder=3)
            must_show.append(y[0] - a.dz)
        ax.scatter(x, y, s=60, facecolors="none", edgecolors=ABRUPT, linewidths=1.4, zorder=5,
                   label="abrupt point" if k == 0 else None)
        ax.annotate(f"{a.dz:+.2f} m" if is_focus else label, (x.mean(), y.max()), xytext=(6, 4),
                    textcoords="offset points", fontsize=8, color=INK, zorder=6)
        must_show += list(y)

    # x axis: CONTEXT m before and after focus, otherwise the whole lid plus 10 m on each side; y axis from the
    # points within it
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
    """Description shown when a lanelet is clicked"""
    z = np.r_[ll.left_z, ll.right_z]
    z = z[np.isfinite(z)]
    rise = (ll.left_z[-1] + ll.right_z[-1] - ll.left_z[0] - ll.right_z[0]) / 2.0  # end minus start (mean of both sides)
    numbers = [f"#{k}" for k, a in enumerate(issues, 1) if ll.id in {lid for lid, _ in a.users}]
    length = arc_length(centerline(ll))[-1]
    return (f"lanelet {ll.id} ({ll.subtype}): {length:.1f} m"
            + (f", elevation {z.min():.2f} ~ {z.max():.2f} m" if len(z) else ", no elevation")
            + (f", {rise:+.2f} m from start to end" if np.isfinite(rise) else "")
            + (f", abrupt points {' '.join(numbers)}" if numbers else ", no abrupt point"))


def show_detail(ctx, lid, title, text, marks=(), focus=None):
    """
    Shows the elevation profile of lanelet lid and the description text in the detail window (DETAIL_TITLE),
    parameters as in _draw_profile().
    The window is reused if already open (content replaced), reopened after being closed. Returns the Figure
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
    with warnings.catch_warnings():  # without a GUI (Agg), show() only gives a warning
        warnings.simplefilter("ignore")
        fig.show()
    return fig


def _enable_click(fig, ax, ctx, lanelets, issues, lines):
    """
    Click in the top view window (also in zoom / pan mode, a drag doesn't count), same behavior as the heatmap window:
      - on an abrupt point (within CLICK_PIXELS pixels, the nearest one): shows its elevation profile (CONTEXT m before
        and after) and its line in the report in the detail window,
        and prints this line to the terminal
      - on a lanelet: shows the elevation profile of the whole lanelet in the detail window, with its abrupt points marked
      - on empty space: clears the orange highlight in the top view (the detail window is kept)
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
