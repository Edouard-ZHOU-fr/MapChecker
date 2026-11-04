"""
Heatmap window (IHM): the heatmap of report_hotspots(show=True) in a Tk window, opened after the report by
utils.heatmap.show_windows() (requires tkinter and a display):
  - map (left): the same drawing as the saved png. Drag to pan (the drawn map is moved while dragging, redrawn when
    the mouse stops or is released), mouse wheel to zoom around the cursor; toolbar: zoom to a rectangle, back /
    forward, home (whole map), save the view, coordinates and lanelet under the cursor. The map keeps a 1:1 scale and
    always fills its area, it is never stretched, and the legend and color scale, in the side panel, never move over
    it. Resizing the window keeps the whole map (or the selection the map went to) in view; once panned / zoomed by
    hand, it keeps the scale and center instead, showing more or less of the map around it
  - side panel (right): summary, color scale, layers (uncheck to hide), go to a lanelet by id, list of the hotspots
    and of the issues of each check (click, or ↑ / ↓ to step through them: the map goes to the selection), details
    of the selection (lines of the report, selectable and copyable)
  - click on the map: an issue point (within CLICK_PIXELS pixels) is selected in the list, the view stays; a lanelet
    shows its id and its issues; empty space clears the selection (also Escape)
The lanelets of the selection are outlined in orange and its issue points circled.
"""

import base64
import io
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import numpy as np
from matplotlib.backend_tools import Cursors
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.cm import ScalarMappable
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.path import Path
from matplotlib.transforms import Bbox

from .heatmap import CLICK_PIXELS, HIGHLIGHT_COLOR, color_scale, draw_layers, hotspot_text, scale_label, style_axes, \
    summary

SURFACE = "#fcfcfb"          # background of the map and of the details
MUTED = "#6b6b66"
WARNING_COLOR = "#a35200"    # lines of the report in the details
ERROR_COLOR = "#c92a2a"
MAX_WINDOW = (1600, 1000)    # initial size of the window (pixels), at most 90% of the screen
MIN_WINDOW = (760, 480)
PANEL_WIDTH = 360            # initial width of the side panel (pixels)
MARGINS = (62, 24, 12, 10)   # left, bottom, right, top margins of the map axes (pixels), for the tick labels
ZOOM_STEP = 1.25             # zoom factor per mouse wheel step
MIN_VIEW = 5.0               # zoom limits: the shorter side of the view is at least MIN_VIEW m, the longer side at
MAX_VIEW = 4.0               #   most MAX_VIEW times the whole map
LANELET_MARGIN = 10.0        # going to a lanelet: space around it (m)
PUSH_DELAY = 400             # ms after the last mouse wheel step before the view is added to back / forward
REDRAW_DELAY = 150           # dragging: ms without moving before the map is redrawn
ICON_SIZE = (22, 16)         # legend icons of the layers (pixels)
HINT = ("Drag: pan  ·  Wheel: zoom  ·  Click an issue or a lanelet: details  ·  List: click or ↑ / ↓ to go to each "
        "hotspot / issue  ·  Esc: clear")


def _count(n, noun):
    return f"{n} {noun}" + ("" if n == 1 else "s")


class _Toolbar(NavigationToolbar2Tk):
    """Toolbar of the map: home shows the whole map at the current size; no subplot settings (the layout is fixed)"""

    toolitems = [t for t in NavigationToolbar2Tk.toolitems if t[0] != "Subplots"]

    def __init__(self, canvas, master, home):
        self._home = home
        super().__init__(canvas, master, pack_toolbar=False)

    def home(self, *args):
        self._home()


class HeatmapWindow:
    """The heatmap window of h (utils.heatmap.Heatmap) in the Tk main window root"""

    def __init__(self, root, h):
        self.root, self.h = root, h
        self.paths = {lid: Path(ll.polygon) for lid, ll in h.lanelets.items()}
        self._area = None       # size (pixels) of the map axes at the last resize
        self._fitted = None     # (bounds, xlim, ylim) of the last fit(): fitted again on resize while the view is
                                #   unchanged
        self._drag = None       # left button pressed on the map: {"xy": pixel at press, "free": no toolbar mode,
                                #   "pan": dragging}
        self._placed = None     # selection set in the list by a click on the map: its <<TreeviewSelect>> is ignored
        self._push_id = None    # pending addition of the view to back / forward after mouse wheel zooming
        self._redraw_id = None  # pending redraw while dragging
        self._images = []       # Tk images, kept referenced

        root.title(f"MapChecker heatmap: {h.map_name}")
        style = ttk.Style(root)
        if "alt" in style.theme_names():
            style.theme_use("alt")  # check marks in the check buttons (clam draws a cross)
        self.bg = style.lookup("TFrame", "background") or "#dcdad5"
        width, height = (min(m, int(0.9 * s)) for m, s in zip(MAX_WINDOW, (root.winfo_screenwidth(),
                                                                          root.winfo_screenheight())))
        root.geometry(f"{width}x{height}")
        root.minsize(*MIN_WINDOW)

        ttk.Label(root, text=HINT, foreground=MUTED, padding=(8, 2)).pack(side=tk.BOTTOM, fill=tk.X)
        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(paned)
        panel = ttk.Frame(paned, padding=(10, 8, 10, 4), width=PANEL_WIDTH)
        panel.pack_propagate(False)
        paned.add(left, weight=1)
        paned.add(panel, weight=0)
        self._build_map(left, (width - PANEL_WIDTH, height))
        self._build_panel(panel)
        root.bind("<Escape>", lambda event: self.clear())
        root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        """
        Closes the window without leaving pending callbacks: other windows (e.g. altitude) may keep the event loop
        running, which would run them and print "invalid command name"
        """
        # cancelled by the widget that scheduled them (it also deletes their command)
        for widget, after_id in ((self.root, self._push_id), (self.root, self._redraw_id),
                                 (self.canvas.get_tk_widget(), getattr(self.canvas, "_idle_draw_id", None))):
            if after_id:
                widget.after_cancel(after_id)
        self.root.destroy()

    # ---- map

    def _build_map(self, master, size):
        h = self.h
        self.fig = Figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
        self.fig.patch.set_facecolor(SURFACE)
        self.ax = ax = self.fig.add_axes((0, 0, 1, 1))
        ax.set_axes_locator(self._locate)
        ax.set_aspect("equal", adjustable="datalim")
        ax.ticklabel_format(useOffset=False, style="plain")
        style_axes(ax)
        self.layers = draw_layers(ax, h)
        for image in ax.get_images():
            image.mouseover = False  # the toolbar shows coordinates and lanelet, not the color under the cursor
            image.set_data(np.round(np.asarray(image.get_array()) * 255).astype(np.uint8))  # draws faster than float
        ax.format_coord = self._format_coord
        self.ids = list(h.lanelets)
        self.boxes = np.array([np.r_[ll.polygon.min(axis=0), ll.polygon.max(axis=0)] for ll in h.lanelets.values()])
        self.highlight = PolyCollection([], facecolors="none", edgecolors=HIGHLIGHT_COLOR, linewidths=2.0, zorder=7)
        ax.add_collection(self.highlight)
        self.rings = ax.scatter(np.empty(0), np.empty(0), s=150, facecolors="none", edgecolors=HIGHLIGHT_COLOR,
                                linewidths=2.0, zorder=7)
        self.point_layer = np.zeros(len(h.items), dtype=int)  # layer of each issue point
        for k, layer in enumerate(self.layers):
            if layer.kind in ("check", "ext", "errors"):
                self.point_layer[[t[0] for t in layer.targets]] = k

        self.canvas = FigureCanvasTkAgg(self.fig, master=master)
        self.toolbar = _Toolbar(self.canvas, master, self.fit)
        self.toolbar.pack(side=tk.TOP, fill=tk.X)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("resize_event", self._on_resize)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.fit(push=False)  # fitted again to the actual size at the first resize

    def _lanelets_at(self, x, y):
        """Ids of the lanelets containing (x, y)"""
        x0, y0, x1, y1 = self.boxes.T
        near = np.nonzero((x0 <= x) & (x <= x1) & (y0 <= y) & (y <= y1))[0]
        return sorted((self.ids[k] for k in near if self.paths[self.ids[k]].contains_point((x, y))), key=int)

    def _format_coord(self, x, y):
        ids = self._lanelets_at(x, y)
        return f"x={x:.1f}  y={y:.1f}" + (f"   lanelet {', '.join(ids)}" if ids else "")

    def _map_area(self):
        """Size (pixels) of the map axes: the figure minus MARGINS"""
        left, bottom, right, top = MARGINS
        return max(self.fig.bbox.width - left - right, 1.0), max(self.fig.bbox.height - bottom - top, 1.0)

    def _locate(self, ax, renderer):
        """Axes locator: the map fills the figure up to MARGINS (pixels), whatever the size of the window"""
        width, height = self.fig.bbox.width, self.fig.bbox.height
        w, h = self._map_area()
        return Bbox.from_bounds(MARGINS[0] / width, MARGINS[1] / height, w / width, h / height)

    def _set_view(self, cx, cy, scale):
        """View centered on (cx, cy) at scale m per pixel"""
        w, h = self._map_area()
        self.ax.set_xlim(cx - scale * w / 2, cx + scale * w / 2)
        self.ax.set_ylim(cy - scale * h / 2, cy + scale * h / 2)

    def _on_resize(self, event):
        """
        View not changed since fit(): fits the same bounds to the new size. Otherwise keeps the center and the scale
        (m per pixel) of the view: a larger window shows more of the map
        """
        fitted = self._fitted
        if self._area is None or fitted is not None and np.allclose((self.ax.get_xlim(), self.ax.get_ylim()),
                                                                    fitted[1:], rtol=0, atol=1e-6 * self._span()):
            self.fit(None if fitted is None else fitted[0], push=False)
        else:
            (x0, x1), (y0, y1) = self.ax.get_xlim(), self.ax.get_ylim()
            scale = max((x1 - x0) / self._area[0], (y1 - y0) / self._area[1])
            self._set_view((x0 + x1) / 2, (y0 + y1) / 2, scale)
        self._area = self._map_area()

    def _remember(self):
        """Before changing the view: if back / forward is empty, adds the current view, so that back returns to it"""
        if self.toolbar._nav_stack() is None:
            self.toolbar.push_current()

    def _span(self):
        """Size of the whole map (m)"""
        x0, y0, x1, y1 = self.h.extent
        return max(x1 - x0, y1 - y0)

    def fit(self, bounds=None, push=True):
        """Shows bounds (x0, y0, x1, y1) as large as possible, the whole map by default (also the home button)"""
        x0, y0, x1, y1 = bounds = self.h.extent if bounds is None else bounds
        w, h = self._map_area()
        if push:
            self._remember()
        self._set_view((x0 + x1) / 2, (y0 + y1) / 2, max((x1 - x0) / w, (y1 - y0) / h))
        self._fitted = (bounds, self.ax.get_xlim(), self.ax.get_ylim())
        if push:
            self.toolbar.push_current()
        self.canvas.draw_idle()

    def _goto(self, pts, margin, least):
        """Shows the points pts [N, 2] with margin (m) around them, at least least m on each side of their center"""
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        center, half = (lo + hi) / 2, np.maximum((hi - lo) / 2 + margin, least)
        self.fit((*(center - half), *(center + half)))

    def _on_press(self, event):
        self._drag = None
        if event.button == 1 and event.inaxes is self.ax:
            self._drag = {"xy": (event.x, event.y), "free": not self.toolbar.mode, "pan": False}

    def _on_motion(self, event):
        """Drag with the left button (no toolbar mode): pan"""
        drag = self._drag
        if drag is None or not drag["free"]:
            return
        if not drag["pan"]:
            if np.hypot(event.x - drag["xy"][0], event.y - drag["xy"][1]) <= CLICK_PIXELS:
                return
            drag["pan"] = True
            self._remember()
            self.ax.start_pan(*drag["xy"], 1)
            self.canvas.set_cursor(Cursors.MOVE)
            self._snapshot(drag["xy"])
        self.ax.drag_pan(1, None, event.x, event.y)
        drag["last"] = (event.x, event.y)
        self._shift(event.x - drag["snap_xy"][0], event.y - drag["snap_xy"][1])
        if self._redraw_id is not None:
            self.root.after_cancel(self._redraw_id)
        self._redraw_id = self.root.after(REDRAW_DELAY, self._redraw_drag)

    def _snapshot(self, xy):
        """Dragging: keeps the drawn map (inside the axes, without the frame) and the pointer position xy"""
        if self.fig.stale:
            self.canvas.draw()
        self._drag["snap"] = self.canvas.copy_from_bbox(self.ax.bbox.padded(-1))
        self._drag["snap_xy"] = xy

    def _shift(self, dx, dy):
        """
        Dragging: shows the kept map moved by (dx, dy) pixels instead of drawing it again (much faster); the parts
        moved in are empty until _redraw_drag(), the tick labels are updated then
        """
        snap = self._drag["snap"]
        self.ax.draw_artist(self.ax.patch)  # clears the map area
        x1, y1, x2, y2 = snap.get_extents()  # pixels from the top left
        dx, dy = int(round(dx)), int(round(-dy))
        sx1, sx2, sy1, sy2 = x1 + max(0, -dx), x2 - max(0, dx), y1 + max(0, -dy), y2 - max(0, dy)
        if sx1 < sx2 and sy1 < sy2:
            self.canvas.restore_region(snap, bbox=(sx1, sy1, sx2, sy2), xy=(x1 + dx, y1 + dy))  # xy: of the whole
        for spine in self.ax.spines.values():
            self.ax.draw_artist(spine)
        self.canvas.blit(self.ax.bbox)

    def _redraw_drag(self):
        """Dragging, the mouse stopped: draws the map at the current view and keeps it for the rest of the drag"""
        self._redraw_id = None
        if self._drag is not None and self._drag.get("pan"):
            self.canvas.draw()
            self._snapshot(self._drag["last"])

    def _on_release(self, event):
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if drag["pan"]:
            if self._redraw_id is not None:
                self.root.after_cancel(self._redraw_id)
                self._redraw_id = None
            self.ax.end_pan()
            self.toolbar.push_current()
            self.canvas.set_cursor(Cursors.POINTER)
            self.canvas.draw_idle()
        elif (event.inaxes is self.ax
              and np.hypot(event.x - drag["xy"][0], event.y - drag["xy"][1]) <= CLICK_PIXELS):
            self._click(event)

    def _on_scroll(self, event):
        """Mouse wheel: zoom around the cursor"""
        if event.inaxes is not self.ax:
            return
        factor = ZOOM_STEP ** -event.step
        (x0, x1), (y0, y1) = self.ax.get_xlim(), self.ax.get_ylim()
        x, y = event.xdata, event.ydata
        x_size, y_size = self.h.extent[2] - self.h.extent[0], self.h.extent[3] - self.h.extent[1]
        if (factor < 1 and min(x1 - x0, y1 - y0) * factor < MIN_VIEW
                or factor > 1 and max((x1 - x0) / x_size, (y1 - y0) / y_size) * factor > MAX_VIEW):
            return
        self._remember()
        self.ax.set_xlim(x - (x - x0) * factor, x + (x1 - x) * factor)
        self.ax.set_ylim(y - (y - y0) * factor, y + (y1 - y) * factor)
        self.canvas.draw_idle()
        if self._push_id is not None:
            self.root.after_cancel(self._push_id)
        self._push_id = self.root.after(PUSH_DELAY, self._push_zoom)

    def _push_zoom(self):
        self._push_id = None
        self.toolbar.push_current()

    def _click(self, event):
        """Click on the map: an issue point (selected in the list), otherwise a lanelet, otherwise clears"""
        h = self.h
        shown = np.array([var.get() for var in self.shown])[self.point_layer]
        dist = np.linalg.norm(self.ax.transData.transform(h.pts) - (event.x, event.y), axis=1)
        hits = [int(i) for i in np.argsort(dist) if dist[i] <= CLICK_PIXELS and shown[i]]
        if hits:
            iids = [f"issue:{i}" for i in hits]
            self._select(iids)
            self._show_selection(iids, move=False)
            self.tree.focus_set()  # ↑ / ↓ then continue from this issue
            return
        ids = self._lanelets_at(event.xdata, event.ydata)
        self._select([])
        if ids:
            self._show_lanelets(ids)
        else:
            self._show_selection([], move=False)

    def _highlight(self, issues, ids):
        """Outlines the lanelets ids and circles the issue points issues"""
        self.highlight.set_verts([self.h.lanelets[i].polygon for i in ids if i in self.h.lanelets])
        self.rings.set_offsets(self.h.pts[issues] if len(issues) else np.empty((0, 2)))
        self.canvas.draw_idle()

    # ---- side panel

    def _build_panel(self, panel):
        h = self.h
        title = tkfont.nametofont("TkDefaultFont", root=self.root).copy()
        size = title.cget("size")  # negative: in pixels
        title.configure(weight="bold", size=size + 2 if size > 0 else size - 2)
        wrap = PANEL_WIDTH - 24
        ttk.Label(panel, text=h.map_name, font=title).pack(anchor="w")
        info = ttk.Label(panel, text=f"{summary(h)}\n{h.stamp}", foreground=MUTED, wraplength=wrap, justify="left")
        info.pack(anchor="w", fill=tk.X, pady=(2, 6))

        ttk.Label(panel, image=self._scale_image(wrap)).pack(anchor="w")
        scale = ttk.Label(panel, text=scale_label(h), foreground=MUTED, wraplength=wrap, justify="left")
        scale.pack(anchor="w", fill=tk.X, pady=(0, 6))

        def rewrap(event):
            for label in (info, scale):
                label.configure(wraplength=max(event.width - 24, 100))

        panel.bind("<Configure>", rewrap)

        box = ttk.LabelFrame(panel, text="Layers", padding=(6, 2))
        box.pack(fill=tk.X, pady=(0, 6))
        self.shown = []
        for k, layer in enumerate(self.layers):
            var = tk.BooleanVar(master=self.root, value=True)
            ttk.Checkbutton(box, text=layer.label, image=self._icon(layer), compound="left", variable=var,
                            command=lambda k=k: self._toggle(k)).pack(anchor="w")
            self.shown.append(var)

        search = ttk.Frame(panel)
        search.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(search, text="Go to lanelet").pack(side=tk.LEFT)
        self.search = ttk.Entry(search, width=12)
        self.search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.search.bind("<Return>", self._goto_lanelet)
        ttk.Button(search, text="Go", width=4, command=self._goto_lanelet).pack(side=tk.LEFT)

        split = ttk.PanedWindow(panel, orient=tk.VERTICAL)
        split.pack(fill=tk.BOTH, expand=True)
        split.add(self._build_tree(split), weight=3)
        split.add(self._build_details(split), weight=2)

    def _build_tree(self, master):
        """List: hotspots, ERRORs, then the issues of each check in the order of the output"""
        h = self.h
        frame = ttk.Frame(master)
        self.tree = tree = ttk.Treeview(frame, columns=("p",), selectmode="extended")
        tree.heading("#0", text="Hotspots and issues", anchor="w")
        tree.heading("p", text="p", anchor="e")
        tree.column("#0", width=200, minwidth=120, stretch=True)
        tree.column("p", width=44, minwidth=44, stretch=False, anchor="e")
        bar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        order = {"hotspots": 0, "errors": 1}
        for k in sorted(range(len(self.layers)), key=lambda k: order.get(self.layers[k].kind, 2)):
            layer = self.layers[k]
            if not layer.targets:
                continue
            if layer.kind == "hotspots":
                tree.insert("", "end", f"layer:{k}", text=f"Hotspots ({len(h.spots)})", open=True)
                for rank, members in enumerate(h.spots):
                    size = f"{len(members)} issues" + (f", p-weighted {h.weights[members].sum():.1f}"
                                                       if h.weighted else "")
                    tree.insert(f"layer:{k}", "end", f"spot:{rank}", text=f"#{rank + 1}  {size}")
                continue
            tree.insert("", "end", f"layer:{k}", text=layer.label, open=layer.kind != "ext")
            for (i,) in layer.targets:
                _, _, p, line = h.items[i]
                text = line.split("] ", 1)[-1].split(" | ", 1)[0]
                tree.insert(f"layer:{k}", "end", f"issue:{i}", text=text, values=("" if p is None else f"{p:.2f}",))
        tree.bind("<<TreeviewSelect>>", self._on_select)
        tree.focus_set()
        return frame

    def _build_details(self, master):
        """Details of the selection: read-only, selectable and copyable (Ctrl+C, Ctrl+A)"""
        frame = ttk.Frame(master)
        self.details = text = tk.Text(frame, wrap="word", height=8, relief="flat", borderwidth=0, padx=6, pady=4,
                                      background=SURFACE, font="TkDefaultFont")
        bar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bold = tkfont.nametofont("TkDefaultFont", root=self.root).copy()
        bold.configure(weight="bold")
        text.tag_configure("title", font=bold)
        text.tag_configure("muted", foreground=MUTED)
        text.tag_configure("warning", foreground=WARNING_COLOR)
        text.tag_configure("error", foreground=ERROR_COLOR)

        def read_only(event):
            if event.state & 0x4 and event.keysym.lower() == "a":
                text.tag_add("sel", "1.0", "end")
                return "break"
            if event.state & 0x4 and event.keysym.lower() == "c" or event.keysym in (
                    "Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"):
                return None
            return "break"

        text.bind("<Key>", read_only)
        for sequence in ("<<Paste>>", "<<PasteSelection>>", "<<Cut>>", "<<Clear>>"):
            text.bind(sequence, lambda event: "break")
        self._set_details([("Select a hotspot or an issue in the list, or click on the map", "muted")])
        return frame

    def _png_image(self, fig):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        image = tk.PhotoImage(master=self.root, data=base64.b64encode(buf.getvalue()))
        self._images.append(image)
        return image

    def _scale_image(self, width):
        """Color scale of the density (horizontal)"""
        fig = Figure(figsize=(width / 100, 0.36), dpi=100)
        fig.patch.set_facecolor(self.bg)
        cax = fig.add_axes((0.04, 0.5, 0.92, 0.42))
        bar = fig.colorbar(ScalarMappable(*color_scale(self.h)), cax=cax, orientation="horizontal")
        bar.outline.set_edgecolor("#b5b5b0")
        cax.tick_params(colors=MUTED, labelsize=8, length=2, pad=1)
        return self._png_image(fig)

    def _icon(self, layer):
        """Legend icon of layer, drawn like the layer"""
        fig = Figure(figsize=(ICON_SIZE[0] / 100, ICON_SIZE[1] / 100), dpi=100)
        fig.patch.set_facecolor(self.bg)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        if layer.symbol is None:
            ax.imshow(np.linspace(0, 1, 64)[None], cmap=color_scale(self.h)[1], aspect="auto",
                      extent=(0.1, 0.9, 0.25, 0.75))
        elif "marker" in layer.symbol:
            ax.scatter([0.5], [0.5], **layer.symbol)
        else:
            ax.plot([0.1, 0.9], [0.5, 0.5], **layer.symbol)
        return self._png_image(fig)

    def _toggle(self, k):
        for artist in self.layers[k].artists:
            artist.set_visible(self.shown[k].get())
        self.canvas.draw_idle()

    def _set_details(self, parts):
        """parts: [(text, tag)], one line each"""
        self.details.delete("1.0", "end")
        for text, tag in parts:
            self.details.insert("end", text + "\n", tag)

    def _lines(self, issues):
        """Lines of the report of the issues, colored"""
        return [(self.h.items[i][3], "error" if self.h.items[i][3].startswith("[ERROR]") else "warning")
                for i in issues]

    # ---- selection

    def _select(self, iids):
        """Selects iids in the list without moving the map (selection made on the map)"""
        if set(self.tree.selection()) != set(iids):
            self._placed = set(iids)
            self.tree.selection_set(iids)
        if iids:
            self.tree.see(iids[0])
            self.tree.focus(iids[0])

    def _on_select(self, event):
        iids = self.tree.selection()
        if self._placed is not None and set(iids) == self._placed:
            self._placed = None  # selection made on the map, already shown
            return
        self._placed = None
        self._show_selection(iids, move=True)

    def _issues_of(self, iid):
        kind, k = iid.split(":")
        if kind == "issue":
            return [int(k)]
        if kind == "spot":
            return [int(i) for i in self.h.spots[int(k)]]
        return [i for target in self.layers[int(k)].targets for i in target]

    def _show_selection(self, iids, move):
        """Details and highlight of the list items iids; move: the map goes to them"""
        h = self.h
        if not iids:
            self._highlight([], [])
            self._set_details([("Select a hotspot or an issue in the list, or click on the map", "muted")])
            return
        issues = sorted({i for iid in iids for i in self._issues_of(iid)})
        ids = sorted({lid for i in issues for lid in h.items[i][1]}, key=int)
        if len(iids) == 1 and iids[0].startswith("spot:"):
            rank = int(iids[0].split(":")[1])
            head = [(f"Hotspot #{rank + 1}", "title"),
                    (hotspot_text(h.pts, h.items, h.weights, h.weighted, h.spots[rank]), "muted")]
        elif len(iids) == 1 and iids[0].startswith("layer:"):
            head = [(self.tree.item(iids[0], "text"), "title")]
        elif len(issues) == 1:
            category, _, p, _ = h.items[issues[0]]
            head = [(category + ("" if p is None else f"  ·  p = {p:.2f}"), "title"),
                    (f"lanelet {' / '.join(h.items[issues[0]][1]) or '-'}", "muted")]
        else:
            head = [(f"{len(issues)} issues", "title"), (f"lanelets: {' '.join(ids) or '-'}", "muted")]
        self._set_details(head + [("", None)] + self._lines(issues))
        self._highlight(issues, ids)
        if move:
            self._goto(h.pts[issues], h.radius, 1.5 * h.radius)

    def _show_lanelets(self, ids):
        """Details and highlight of the lanelets ids, with the issues on them"""
        issues = [i for i, item in enumerate(self.h.items) if set(item[1]) & set(ids)]
        self._set_details([(f"lanelet {', '.join(ids)}", "title"),
                           (_count(len(issues), "issue") + " on the map" if issues else "no issue on the map",
                            "muted"),
                           ("", None)] + self._lines(issues))
        self._highlight(issues, ids)

    def _goto_lanelet(self, event=None):
        lid = self.search.get().strip()
        if lid not in self.h.lanelets:
            self._set_details([(f"lanelet {lid}: not in the map" if lid else "Enter a lanelet id", "muted")])
            return
        self._select([])
        self._show_lanelets([lid])
        self._goto(self.h.lanelets[lid].polygon, LANELET_MARGIN, LANELET_MARGIN)

    def clear(self):
        self._select([])
        self._show_selection([], move=False)
