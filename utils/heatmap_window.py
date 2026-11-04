"""
Heatmap window (IHM): the heatmap of report_hotspots(show=True) in a Tk window, opened after the report by
utils.heatmap.show_windows() (requires tkinter and a display):
  - header: map and time of the report; status bar: mouse and keyboard shortcuts
  - map (left): the same drawing as the saved png. Drag to pan (the drawn map is moved while dragging, redrawn when
    the mouse stops or is released), mouse wheel to zoom around the cursor; toolbar: zoom to a rectangle, back /
    forward, home (whole map), save the view, coordinates and lanelet under the cursor. The map keeps a 1:1 scale and
    always fills its area, it is never stretched, and the legend and color scale, in the side panel, never move over
    it. Resizing the window keeps the whole map (or the selection the map went to) in view; once panned / zoomed by
    hand, it keeps the scale and center instead, showing more or less of the map around it
  - side panel (right): numbers (issues, expected true issues, hotspots, ...), legend (color scale, and the layers:
    click one to show / hide it), go to a lanelet by id, list of the hotspots and of the issues of each check (click,
    or ↑ / ↓ to step through them: the map goes to the selection), details of the selection (lines of the report,
    selectable and copyable)
  - click on the map: an issue point (within CLICK_PIXELS pixels) is selected in the list, the view stays; a lanelet
    shows its id and its issues; empty space clears the selection (also Escape)
The lanelets of the selection are outlined in orange and its issue points circled. Light theme with the surfaces and
ink of the charts (as in ext/altitude/script/_plot.py), the accent is the first color of the heat scale.
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

from .heatmap import CLICK_PIXELS, HIGHLIGHT_COLOR, color_scale, draw_layers, hotspot_text, scale_label

BG = "#f0efec"               # window
CARD = "#fcfcfb"             # cards and map surface
BORDER = "#e1e0d9"
HOVER = "#f0efea"            # layer row under the mouse, toolbar button
CHIP = "#e4e3dd"             # keys in the status bar
THUMB = "#d6d5ce"            # scroll bars
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID = "#ecebe6"             # grid of the map
ACCENT = "#2a78d6"           # buttons, focus
ACCENT_DARK = "#1c5cab"
SELECTED = "#dce9f9"         # selected rows of the list
WARNING_INK = "#a35200"      # [WARNING ...] / [ERROR] in the details
ERROR_INK = "#b42318"
FONTS = ("Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans")  # the first one installed
MONO_FONTS = ("Ubuntu Mono", "DejaVu Sans Mono")
MAX_WINDOW = (1600, 1000)    # initial size of the window (pixels), at most 90% of the screen
MIN_WINDOW = (820, 540)
FOLDED_BELOW = 850           # the legend is folded at first when the window is less high than this (pixels)
PANEL_WIDTH = 380            # initial width of the side panel (pixels)
MARGINS = (58, 22, 10, 8)    # left, bottom, right, top margins of the map axes (pixels), for the tick labels
ZOOM_STEP = 1.25             # zoom factor per mouse wheel step
MIN_VIEW = 5.0               # zoom limits: the shorter side of the view is at least MIN_VIEW m, the longer side at
MAX_VIEW = 4.0               #   most MAX_VIEW times the whole map
LANELET_MARGIN = 10.0        # going to a lanelet: space around it (m)
PUSH_DELAY = 400             # ms after the last mouse wheel step before the view is added to back / forward
REDRAW_DELAY = 150           # dragging: ms without moving before the map is redrawn
ICON_SIZE = (22, 16)         # legend icons of the layers (pixels)
KEYS = (("Drag", "pan"), ("Wheel", "zoom"), ("Click", "issue / lanelet details"), ("↑ ↓", "next in the list"),
        ("Esc", "clear"))
NO_SELECTION = "Select a hotspot or an issue in the list, or click on the map"


def _count(n, noun):
    return f"{n} {noun}" + ("" if n == 1 else "s")


class _Canvas(FigureCanvasTkAgg):
    """
    The pixel ratio (screen dpi / 96) is rounded to 2 digits, as matplotlib does on Windows: on Linux it is not, and a
    ratio such as 0.9995 makes the drawing a fraction of a pixel smaller than the Tk image, whose last row and column
    then keep pixels of an older drawing
    """

    def _update_device_pixel_ratio(self, event=None):
        ratio = round(self._tkcanvas.winfo_fpixels("1i") / 96, 2)
        if self._set_device_pixel_ratio(ratio):
            w, h = self.get_width_height(physical=True)
            self._tkcanvas.configure(width=w, height=h)


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
        root.configure(background=BG)
        self._fonts()
        self._style()
        width, height = (min(m, int(0.9 * s)) for m, s in zip(MAX_WINDOW, (root.winfo_screenwidth(),
                                                                          root.winfo_screenheight())))
        root.geometry(f"{width}x{height}")
        root.minsize(*MIN_WINDOW)
        root.iconphoto(True, self._logo(32))

        self._build_header()
        self._build_status()  # packed before the main area: it stays when the window gets small
        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))
        left = tk.Frame(paned, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=BORDER)
        panel = tk.Frame(paned, background=BG, width=PANEL_WIDTH)
        panel.pack_propagate(False)
        paned.add(left, weight=1)
        paned.add(panel, weight=0)
        self._build_map(left, (width - PANEL_WIDTH - 40, height - 130))
        self._build_panel(panel, fold_legend=height < FOLDED_BELOW)
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

    # ---- look

    def _fonts(self):
        """Fonts of this window (named Tk fonts are per window: other windows are not changed)"""
        families = set(tkfont.families(self.root))
        family = next((f for f in FONTS if f in families), None) \
            or tkfont.nametofont("TkDefaultFont", root=self.root).actual("family")
        mono = next((f for f in MONO_FONTS if f in families), None) \
            or tkfont.nametofont("TkFixedFont", root=self.root).actual("family")
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name, root=self.root).configure(family=family, size=10)

        def font(family=family, size=10, weight="normal"):
            return tkfont.Font(root=self.root, family=family, size=size, weight=weight)

        self.f_body = tkfont.nametofont("TkDefaultFont", root=self.root)
        self.f_bold = font(weight="bold")
        self.f_small = font(size=9)
        self.f_small_bold = font(size=9, weight="bold")
        self.f_title = font(size=12, weight="bold")
        self.f_value = font(size=15, weight="bold")
        self.f_mono = font(mono, 10)
        self.f_mono_bold = font(mono, 10, "bold")

    def _style(self):
        """ttk styles: flat light theme"""
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, bordercolor=BORDER, lightcolor=CARD, darkcolor=BORDER,
                        troughcolor=CARD, focuscolor=ACCENT, selectbackground=SELECTED, selectforeground=INK,
                        font=self.f_body)
        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", sashthickness=12, gripcount=0, background=BG, bordercolor=BG, lightcolor=BG,
                        darkcolor=BG)
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no frame
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK, borderwidth=0,
                        rowheight=self.f_body.metrics("linespace") + 9, font=self.f_body)
        style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", INK)])
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, font=self.f_small_bold,
                        relief="flat", borderwidth=0, padding=(4, 2, 4, 6))
        style.map("Treeview.Heading", background=[("active", CARD)], relief=[("active", "flat")])
        style.layout("Vertical.TScrollbar",  # thumb only, no arrows
                     [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                         ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Vertical.TScrollbar", troughcolor=CARD, background=THUMB, bordercolor=CARD,
                        lightcolor=THUMB, darkcolor=THUMB, arrowsize=9, gripcount=0, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", MUTED)], lightcolor=[("active", MUTED)],
                  darkcolor=[("active", MUTED)])
        style.configure("Idle.Vertical.TScrollbar", background=CARD, lightcolor=CARD, darkcolor=CARD)  # nothing to
        style.map("Idle.Vertical.TScrollbar", background=[], lightcolor=[], darkcolor=[])              # scroll
        style.configure("TEntry", fieldbackground=CARD, foreground=INK, bordercolor=BORDER, lightcolor=CARD,
                        darkcolor=CARD, insertcolor=INK, padding=(8, 5))
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                        lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT, font=self.f_bold, padding=(16, 5))
        style.map("Accent.TButton", **{option: [("pressed", ACCENT_DARK), ("active", ACCENT_DARK)]
                                       for option in ("background", "bordercolor", "lightcolor", "darkcolor")})

    def _label(self, master, text, font=None, foreground=INK, background=CARD, **kwargs):
        return tk.Label(master, text=text, font=font or self.f_body, foreground=foreground, background=background,
                        **kwargs)

    def _card(self, master, title, hint=None, folded=None):
        """
        A card with a title (and a hint on the right): (card, body). folded True / False: a click on the title folds /
        unfolds the body, folded at first if True
        """
        card = tk.Frame(master, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=BORDER)
        head = tk.Frame(card, background=CARD)
        head.pack(fill=tk.X, padx=12, pady=(9, 5))
        name = self._label(head, title, self.f_bold)
        name.pack(side=tk.LEFT)
        note = self._label(head, hint or "", self.f_small, MUTED)
        note.pack(side=tk.RIGHT)
        body = tk.Frame(card, background=CARD)
        if folded is None:
            body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
            return card, body

        def fold(event=None, folded=not folded):
            if folded:
                body.pack_forget()
            else:
                body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
            name.configure(text=("▸ " if folded else "▾ ") + title)
            note.configure(text="click to unfold" if folded else hint or "")
            for widget in [head, *head.winfo_children()]:
                widget.bind("<Button-1>", lambda event: fold(folded=not folded))

        for widget in [head, *head.winfo_children()]:
            widget.configure(cursor="hand2")
        fold(folded=folded)
        return card, body

    def _scrollbar(self, master, view):
        """Vertical scroll bar of view (Treeview, Text), its thumb hidden while everything is visible"""
        bar = ttk.Scrollbar(master, orient=tk.VERTICAL, command=view.yview)

        def update(first, last):
            idle = float(first) <= 0.0 and float(last) >= 1.0
            bar.configure(style="Idle.Vertical.TScrollbar" if idle else "Vertical.TScrollbar")
            bar.set(first, last)

        view.configure(yscrollcommand=update)
        bar.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        return bar

    def _png_image(self, fig):
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        image = tk.PhotoImage(master=self.root, data=base64.b64encode(buf.getvalue()))
        self._images.append(image)
        return image

    def _logo(self, size):
        """Heat spot on a card: window icon and header"""
        fig = Figure(figsize=(size / 100, size / 100), dpi=100)
        fig.patch.set_facecolor(CARD)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_axis_off()
        y, x = np.mgrid[-1:1:96j, -1:1:96j]
        r = np.hypot(x + 0.15, y - 0.1) / 0.9
        rgba = color_scale(self.h)[1](np.clip(1.0 - r, 0.0, 1.0))
        rgba[..., 3] = np.clip(1.6 * (1.0 - r), 0.0, 1.0)
        ax.imshow(rgba, extent=(-1, 1, -1, 1), interpolation="bilinear")
        return self._png_image(fig)

    def _icon(self, layer, faded=False):
        """Legend icon of layer, drawn like the layer; faded: layer hidden"""
        fig = Figure(figsize=(ICON_SIZE[0] / 100, ICON_SIZE[1] / 100), dpi=100)
        fig.patch.set_facecolor(CARD)
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        alpha = 0.25 if faded else 1.0
        if layer.symbol is None:
            ax.imshow(np.linspace(0, 1, 64)[None], cmap=color_scale(self.h)[1], aspect="auto", alpha=alpha,
                      extent=(0.08, 0.92, 0.22, 0.78))
        elif "marker" in layer.symbol:
            ax.scatter([0.5], [0.5], alpha=alpha, **layer.symbol)
        else:
            ax.plot([0.08, 0.92], [0.5, 0.5], alpha=alpha, **layer.symbol)
        return self._png_image(fig)

    def _scale_image(self, width):
        """Color scale of the density (horizontal)"""
        fig = Figure(figsize=(width / 100, 0.34), dpi=100)
        fig.patch.set_facecolor(CARD)
        cax = fig.add_axes((0.015, 0.55, 0.97, 0.4))
        bar = fig.colorbar(ScalarMappable(*color_scale(self.h)), cax=cax, orientation="horizontal")
        bar.outline.set_visible(False)
        cax.tick_params(colors=MUTED, labelsize=8, length=0, pad=2)
        return self._png_image(fig)

    # ---- header, status bar

    def _build_header(self):
        bar = tk.Frame(self.root, background=CARD)
        bar.pack(side=tk.TOP, fill=tk.X)
        tk.Frame(self.root, background=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)
        tk.Label(bar, image=self._logo(26), background=CARD).pack(side=tk.LEFT, padx=(14, 8), pady=9)
        self._label(bar, "MapChecker", self.f_title).pack(side=tk.LEFT)
        self._label(bar, "issue heatmap", foreground=MUTED).pack(side=tk.LEFT, padx=(6, 0), pady=(2, 0))
        tk.Frame(bar, background=BORDER, width=1, height=20).pack(side=tk.LEFT, padx=14)
        self._label(bar, self.h.map_name, self.f_bold, INK_2).pack(side=tk.LEFT)
        self._label(bar, f"report {self.h.stamp}", self.f_small, MUTED).pack(side=tk.RIGHT, padx=16)

    def _build_status(self):
        bar = tk.Frame(self.root, background=BG)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(8, 8))
        for key, action in KEYS:
            self._label(bar, key, self.f_small_bold, INK_2, CHIP, padx=6).pack(side=tk.LEFT)
            self._label(bar, action, self.f_small, MUTED, BG).pack(side=tk.LEFT, padx=(5, 16))

    # ---- map

    def _build_map(self, master, size):
        h = self.h
        self.fig = Figure(figsize=(size[0] / 100, size[1] / 100), dpi=100)
        self.fig.patch.set_facecolor(CARD)
        self.ax = ax = self.fig.add_axes((0, 0, 1, 1))
        ax.set_axes_locator(self._locate)
        ax.set_aspect("equal", adjustable="datalim")
        ax.ticklabel_format(useOffset=False, style="plain")
        ax.set_facecolor(CARD)
        ax.tick_params(colors=MUTED, labelsize=8, length=0, pad=5)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)  # under the lanelets
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

        self.canvas = _Canvas(self.fig, master=master)
        self.toolbar = _Toolbar(self.canvas, master, self.fit)
        self._style_toolbar()
        self.toolbar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=4)
        tk.Frame(master, background=BORDER, height=1).pack(side=tk.TOP, fill=tk.X)
        widget = self.canvas.get_tk_widget()
        widget.configure(background=CARD)
        widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=1, pady=(6, 1))
        self.canvas.mpl_connect("resize_event", self._on_resize)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.fit(push=False)  # fitted again to the actual size at the first resize

    def _style_toolbar(self):
        """Flat toolbar on the card"""
        self.toolbar.configure(background=CARD)
        look = {"background": CARD, "activebackground": HOVER, "selectcolor": SELECTED, "relief": "flat",
                "overrelief": "flat", "offrelief": "flat", "borderwidth": 0, "highlightthickness": 0,
                "foreground": MUTED, "font": self.f_small}
        for child in self.toolbar.winfo_children():
            if isinstance(child, tk.Frame) and not child.winfo_children():  # separator
                child.configure(background=BORDER, relief="flat", width=1)
                continue
            options = set(child.keys())
            child.configure(**{k: v for k, v in look.items() if k in options})
            if isinstance(child, (tk.Button, tk.Checkbutton)):
                child.configure(padx=5, pady=3)

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
        """Dragging: keeps the drawn map (inside the axes) and the pointer position xy"""
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

    def _build_panel(self, panel, fold_legend=False):
        self._build_numbers(panel).pack(fill=tk.X)
        self._build_legend(panel, fold_legend).pack(fill=tk.X, pady=(10, 0))
        self._build_search(panel).pack(fill=tk.X, pady=10)
        split = ttk.PanedWindow(panel, orient=tk.VERTICAL)
        split.pack(fill=tk.BOTH, expand=True)
        split.add(self._build_tree(split), weight=1)
        split.add(self._build_details(split), weight=1)

    def _numbers(self):
        """(label, value) of the number tiles"""
        h = self.h
        errors = sum(item[3].startswith("[ERROR]") for item in h.items)
        numbers = [("Issues", f"{h.core.sum()}")]
        if h.weighted:
            numbers.append(("Expected true", f"{h.weights[h.core].sum():.1f}"))
        numbers.append(("Hotspots", f"{len(h.spots)}"))
        if errors:
            numbers.append(("Errors", f"{errors}"))
        if not h.core.all():
            numbers.append(("Ext checks", f"{(~h.core).sum()}"))
        if h.hidden:
            numbers.append((f"Hidden, p < {h.min_p:.2f}", f"{h.hidden}"))
        return numbers

    def _build_numbers(self, master):
        """Number tiles, at most three per row (two by two for four)"""
        frame = tk.Frame(master, background=BG)
        numbers = self._numbers()
        columns = {1: 1, 2: 2, 4: 2}.get(len(numbers), 3)
        for k, (label, value) in enumerate(numbers):
            tile = tk.Frame(frame, background=CARD, highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=BORDER)
            tile.grid(row=k // columns, column=k % columns, sticky="nsew", padx=(0 if k % columns == 0 else 8, 0),
                      pady=(0 if k < columns else 8, 0))
            self._label(tile, label, self.f_small, MUTED, anchor="w").pack(fill=tk.X, padx=10, pady=(6, 0))
            self._label(tile, value, self.f_value, anchor="w").pack(fill=tk.X, padx=10, pady=(0, 5))
        for column in range(columns):
            frame.columnconfigure(column, weight=1, uniform="tile")
        return frame

    def _build_legend(self, master, folded=False):
        """Color scale, and the layers: a click shows / hides one; a click on the title folds / unfolds it"""
        card, body = self._card(master, "Legend", "click a layer to show / hide it", folded)
        tk.Label(body, image=self._scale_image(PANEL_WIDTH - 30), background=CARD).pack(anchor="w")
        caption = self._label(body, scale_label(self.h), self.f_small, MUTED, justify="left",
                              wraplength=PANEL_WIDTH - 40)
        caption.pack(anchor="w", pady=(0, 6))
        body.bind("<Configure>", lambda event: caption.configure(wraplength=max(event.width - 4, 100)))
        self.shown, self.rows = [], []
        for k, layer in enumerate(self.layers):
            self.shown.append(tk.BooleanVar(master=self.root, value=True))
            self._layer_row(body, k, layer)
        return card

    def _layer_row(self, master, k, layer):
        if layer.kind in ("density", "aoi"):
            name, count = ("Issue density" if layer.kind == "density" else "AOI"), ""
        elif layer.kind == "hotspots":
            name, count = "Hotspots", f"{len(layer.targets)}"
        else:
            name, count = layer.label.split(" (")[0], f"{len(layer.targets)}"
        row = tk.Frame(master, background=CARD, cursor="hand2")
        row.pack(fill=tk.X)
        images = (self._icon(layer), self._icon(layer, faded=True))
        icon = tk.Label(row, image=images[0], background=CARD)
        icon.pack(side=tk.LEFT, padx=(4, 8), pady=2)
        texts = [self._label(row, name)]
        texts[0].pack(side=tk.LEFT)
        if layer.kind == "ext":
            texts.append(self._label(row, "ext", self.f_small, MUTED))
            texts[1].pack(side=tk.LEFT, padx=(6, 0))
        number = self._label(row, count, foreground=INK_2)
        number.pack(side=tk.RIGHT, padx=(0, 6))
        widgets = [row, icon, *texts, number]
        for widget in widgets:
            widget.bind("<Button-1>", lambda event: self._flip(k))
            widget.bind("<Enter>", lambda event: self._hover(k, True))
            widget.bind("<Leave>", lambda event: self._hover(k, False))
        self.rows.append((widgets, icon, images, texts, number))

    def _hover(self, k, on):
        for widget in self.rows[k][0]:
            widget.configure(background=HOVER if on else CARD)

    def _flip(self, k):
        self.shown[k].set(not self.shown[k].get())
        self._toggle(k)

    def _toggle(self, k):
        """Shows / hides layer k as self.shown[k]"""
        shown = self.shown[k].get()
        for artist in self.layers[k].artists:
            artist.set_visible(shown)
        _, icon, images, texts, number = self.rows[k]
        icon.configure(image=images[0] if shown else images[1])
        texts[0].configure(foreground=INK if shown else MUTED)
        number.configure(foreground=INK_2 if shown else MUTED)
        self.canvas.draw_idle()

    def _build_search(self, master):
        """Go to a lanelet by id"""
        frame = tk.Frame(master, background=BG)
        text = tk.StringVar(master=self.root)
        self.search = ttk.Entry(frame, textvariable=text, font=self.f_body)
        self.search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        placeholder = self._label(self.search, "Go to lanelet id…", foreground=MUTED, cursor="xterm")

        def update(*args):
            if text.get() or self.root.focus_get() is self.search:
                placeholder.place_forget()
            else:
                placeholder.place(x=9, rely=0.5, anchor="w")

        text.trace_add("write", update)
        self.search.bind("<FocusIn>", update, "+")
        self.search.bind("<FocusOut>", update, "+")
        placeholder.bind("<Button-1>", lambda event: self.search.focus_set())
        update()
        self.search.bind("<Return>", self._goto_lanelet)
        ttk.Button(frame, text="Go", style="Accent.TButton", width=4,
                   command=self._goto_lanelet).pack(side=tk.LEFT, padx=(8, 0))
        return frame

    def _build_tree(self, master):
        """List: hotspots, ERRORs, then the issues of each check in the order of the output"""
        h = self.h
        card, body = self._card(master, "Hotspots and issues", "↑ ↓ to step")
        self.tree = tree = ttk.Treeview(body, columns=("p",), selectmode="extended", show="tree", height=8)
        tree.column("#0", width=200, minwidth=120, stretch=True)
        tree.column("p", width=56, minwidth=56, stretch=False, anchor="e")
        tree.tag_configure("group", font=self.f_bold)
        self._scrollbar(body, tree)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        order = {"hotspots": 0, "errors": 1}
        for k in sorted(range(len(self.layers)), key=lambda k: order.get(self.layers[k].kind, 2)):
            layer = self.layers[k]
            if not layer.targets:
                continue
            icon = self.rows[k][2][0]
            if layer.kind == "hotspots":
                tree.insert("", "end", f"layer:{k}", text=f" Hotspots ({len(h.spots)})", image=icon, open=True,
                            tags=("group",))
                for rank, members in enumerate(h.spots):
                    size = _count(len(members), "issue") + (f" · p-weighted {h.weights[members].sum():.1f}"
                                                            if h.weighted else "")
                    tree.insert(f"layer:{k}", "end", f"spot:{rank}", text=f"#{rank + 1}   {size}")
                continue
            tree.insert("", "end", f"layer:{k}", text=f" {layer.label}", image=icon, open=layer.kind != "ext",
                        tags=("group",))
            for (i,) in layer.targets:
                _, _, p, line = h.items[i]
                text = line.split("] ", 1)[-1].split(" | ", 1)[0]
                tree.insert(f"layer:{k}", "end", f"issue:{i}", text=text, values=("" if p is None else f"p {p:.2f}",))
        tree.bind("<<TreeviewSelect>>", self._on_select)
        tree.focus_set()
        return card

    def _build_details(self, master):
        """Details of the selection: read-only, selectable and copyable (Ctrl+C, Ctrl+A)"""
        card, body = self._card(master, "Details")
        self.details = text = tk.Text(body, wrap="word", height=8, relief="flat", borderwidth=0,
                                      highlightthickness=0, padx=0, pady=0, background=CARD, foreground=INK,
                                      font=self.f_body, selectbackground=SELECTED, selectforeground=INK,
                                      insertwidth=0)
        self._scrollbar(body, text)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.tag_configure("title", font=self.f_title, spacing3=2)
        text.tag_configure("muted", foreground=MUTED, spacing3=2)
        for tag, color in (("line", INK), ("warning", WARNING_INK), ("error", ERROR_INK)):
            text.tag_configure(tag, foreground=color, font=self.f_mono if tag == "line" else self.f_mono_bold,
                               spacing1=7)

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
        self._set_details([(NO_SELECTION, "muted")])
        return card

    def _set_details(self, lines):
        """lines: [(text, tag)] or [[(text, tag), ...]] (one line made of several parts)"""
        self.details.delete("1.0", "end")
        for line in lines:
            for text, tag in line if isinstance(line, list) else [line]:
                self.details.insert("end", text, tag)
            self.details.insert("end", "\n")

    def _lines(self, issues):
        """Lines of the report of the issues: the [WARNING ...] / [ERROR] prefix in the color of its status"""
        lines = []
        for i in issues:
            line = self.h.items[i][3]
            prefix, rest = line.split("] ", 1) if "] " in line else ("", line)
            status = "error" if line.startswith("[ERROR]") else "warning"
            lines.append([(prefix + "] ", status), (rest, "line")] if prefix else [(rest, "line")])
        return lines

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
            self._set_details([(NO_SELECTION, "muted")])
            return
        issues = sorted({i for iid in iids for i in self._issues_of(iid)})
        ids = sorted({lid for i in issues for lid in h.items[i][1]}, key=int)
        if len(iids) == 1 and iids[0].startswith("spot:"):
            rank = int(iids[0].split(":")[1])
            head = [(f"Hotspot #{rank + 1}", "title"),
                    (hotspot_text(h.pts, h.items, h.weights, h.weighted, h.spots[rank]), "muted")]
        elif len(iids) == 1 and iids[0].startswith("layer:"):
            head = [(self.tree.item(iids[0], "text").strip(), "title")]
        elif len(issues) == 1:
            category, _, p, _ = h.items[issues[0]]
            head = [(category + ("" if p is None else f"  ·  p = {p:.2f}"), "title"),
                    (f"lanelet {' / '.join(h.items[issues[0]][1]) or '-'}", "muted")]
        else:
            head = [(f"{len(issues)} issues", "title"), (f"lanelets: {' '.join(ids) or '-'}", "muted")]
        self._set_details(head + self._lines(issues))
        self._highlight(issues, ids)
        if move:
            self._goto(h.pts[issues], h.radius, 1.5 * h.radius)

    def _show_lanelets(self, ids):
        """Details and highlight of the lanelets ids, with the issues on them"""
        issues = [i for i, item in enumerate(self.h.items) if set(item[1]) & set(ids)]
        self._set_details([(f"lanelet {', '.join(ids)}", "title"),
                           (_count(len(issues), "issue") + " on the map" if issues else "no issue on the map",
                            "muted")] + self._lines(issues))
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
