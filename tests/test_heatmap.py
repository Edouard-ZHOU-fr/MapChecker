"""
Tests of the issue heatmap (utils/heatmap.py) and of its window (utils/heatmap_window.py):
  cd MapChecker && python3 -m unittest discover -s tests -v
The window tests need tkinter and a display (skipped otherwise); the window is not shown on the screen, mouse events
are sent to the map directly.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE_DIR, "libs"), BASE_DIR, os.path.dirname(os.path.abspath(__file__))]

import numpy as np  # noqa: E402

from test_masque import write_crossing  # noqa: E402
from utils import heatmap as hm  # noqa: E402
from utils.checks import run_core_checks  # noqa: E402
from utils.config import DEFAULT_CONFIG  # noqa: E402
from utils.context import MapContext  # noqa: E402
from utils.printer import Printer  # noqa: E402


class HeatmapTest(unittest.TestCase):
    """Heatmap of the crossing of test_masque.py: 3 issues close together, one hotspot"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        map_path = os.path.join(self.tmp, "map.osm")
        write_crossing(map_path)
        ctx = MapContext.from_file(map_path, DEFAULT_CONFIG)
        self.png = os.path.join(self.tmp, "map.png")
        with contextlib.redirect_stdout(io.StringIO()):
            out = Printer(False)
            run_core_checks(ctx, out)
            self.spots = hm.report_hotspots(ctx.lanelets, [], out, self.png, 30.0, 10, "map.osm", show=True)
        self.assertEqual(len(hm._pending), 1)
        self.h = hm._pending.pop()

    def tearDown(self):
        hm._pending.clear()
        shutil.rmtree(self.tmp)


class ReportTest(HeatmapTest):
    def test_report(self):
        h = self.h
        self.assertTrue(os.path.isfile(self.png))
        self.assertEqual(len(self.spots), 1)
        self.assertEqual(sorted(h.spots[0].tolist()), list(range(len(h.items))))
        self.assertEqual({item[0] for item in h.items}, {"turn_direction", "right_of_way"})

    def test_show_windows_without_display(self):
        hm._pending.append(self.h)
        tk_root, hm._tk_root = hm._tk_root, lambda: None
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                hm.show_windows()
        finally:
            hm._tk_root = tk_root
        self.assertIn("Windows not shown: no display available", buf.getvalue())
        self.assertIn("MapChecker heatmap: map.osm", buf.getvalue())
        self.assertEqual(hm._pending, [])


class WindowTest(HeatmapTest):
    def setUp(self):
        super().setUp()
        self.root = hm._tk_root()
        if self.root is None:
            self.skipTest("no display")
        self.root.withdraw()
        from utils.heatmap_window import HeatmapWindow

        self.win = HeatmapWindow(self.root, self.h)
        self.resize(900, 600)

    def tearDown(self):
        if getattr(self, "root", None) is not None:
            self.win.close()
        super().tearDown()

    def resize(self, width, height):
        """The map area becomes width x height pixels, as when the window is resized"""
        self.win.canvas.resize(SimpleNamespace(width=width, height=height))
        self.win.canvas.draw()

    def view(self):
        """(m per pixel along x, along y, center) of the map"""
        ax = self.win.ax
        (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
        return (x1 - x0) / ax.bbox.width, (y1 - y0) / ax.bbox.height, np.array([(x0 + x1) / 2, (y0 + y1) / 2])

    def mouse(self, name, pixel, **kwargs):
        """Mouse event at pixel (x, y) of the map"""
        from matplotlib.backend_bases import MouseEvent

        canvas = self.win.canvas
        canvas.callbacks.process(name, MouseEvent(name, canvas, *pixel, **kwargs))
        self.root.update()

    def pixel(self, xy):
        """Pixel of the data coordinates xy in the current view"""
        return self.win.ax.transData.transform(xy)

    def click(self, xy):
        self.mouse("button_press_event", self.pixel(xy), button=1)
        self.mouse("button_release_event", self.pixel(xy), button=1)

    def details(self):
        return self.win.details.get("1.0", "end")

    def test_resize(self):
        """Never stretched; fitted while not navigated by hand, then keeps scale and center"""
        from utils.heatmap_window import MARGINS

        x0, y0, x1, y1 = self.h.extent
        for size in ((900, 600), (400, 800), (1200, 500)):
            self.resize(*size)
            bbox = self.win.ax.bbox
            self.assertEqual((round(bbox.x0), round(bbox.y0), round(bbox.width), round(bbox.height)),
                             (MARGINS[0], MARGINS[1], size[0] - MARGINS[0] - MARGINS[2],
                              size[1] - MARGINS[1] - MARGINS[3]))
            sx, sy, center = self.view()
            self.assertAlmostEqual(sx / sy, 1.0, places=6)
            (vx0, vx1), (vy0, vy1) = self.win.ax.get_xlim(), self.win.ax.get_ylim()
            self.assertTrue(vx0 <= x0 + 1e-6 and vx1 >= x1 - 1e-6 and vy0 <= y0 + 1e-6 and vy1 >= y1 - 1e-6, size)
            np.testing.assert_allclose(center, [(x0 + x1) / 2, (y0 + y1) / 2])

        before = self.view()[0]
        self.mouse("scroll_event", self.pixel(self.view()[2]), step=2)  # zoom in by 1.25²
        self.win.canvas.draw()
        scale, _, center = self.view()
        self.assertAlmostEqual(before / scale, 1.25 ** 2)
        self.resize(700, 700)
        sx, sy, moved = self.view()
        self.assertAlmostEqual(sx / scale, 1.0)
        self.assertAlmostEqual(sy / scale, 1.0)
        np.testing.assert_allclose(moved, center)
        self.win.toolbar.home()  # whole map again
        self.win.canvas.draw()
        (vx0, vx1), (vy0, vy1) = self.win.ax.get_xlim(), self.win.ax.get_ylim()
        self.assertTrue(vx0 <= x0 + 1e-6 and vx1 >= x1 - 1e-6 and vy0 <= y0 + 1e-6 and vy1 >= y1 - 1e-6)

    def test_select_and_click(self):
        win, h = self.win, self.h
        win.tree.selection_set(["spot:0"])
        self.root.update()
        self.assertIn("Hotspot #1", self.details())
        (x0, x1), (y0, y1) = win.ax.get_xlim(), win.ax.get_ylim()
        self.assertTrue(((h.pts >= (x0, y0)) & (h.pts <= (x1, y1))).all())
        self.assertLess(self.view()[0], 0.2)  # zoomed in on the hotspot
        self.assertEqual(len(win.highlight.get_paths()), 2)  # lanelets 21 and 22 outlined

        # click on an issue point: selected in the list, the view stays
        win.toolbar.home()
        win.canvas.draw()
        view = self.view()
        i = int(np.argmin(h.pts[:, 0]))
        self.click(h.pts[i])
        self.assertIn(f"issue:{i}", win.tree.selection())
        self.assertIn(h.items[i][3], self.details())
        np.testing.assert_allclose(self.view()[2], view[2])

        # click on lanelet 21 far from the issues, then on empty space
        self.click((3.0, 1.75))
        self.assertEqual(win.tree.selection(), ())
        self.assertTrue(self.details().startswith("lanelet 21\n"))
        self.click((h.extent[0] + 1.0, h.extent[1] + 1.0))
        self.assertIn("Select a hotspot", self.details())

        # a hidden layer cannot be clicked
        k = int(win.point_layer[i])
        win.shown[k].set(False)
        win._toggle(k)
        self.click(h.pts[i])
        self.assertNotIn(f"issue:{i}", win.tree.selection())

        win.search.insert(0, "22")
        win._goto_lanelet()
        self.assertTrue(self.details().startswith("lanelet 22\n"))

    def test_drag(self):
        """Drag to pan: the drawn map is moved while dragging, back returns to the view before"""
        win = self.win
        scale, _, center = self.view()
        start = np.round(self.pixel(center))  # mouse events are in whole pixels
        self.mouse("button_press_event", start, button=1)
        self.mouse("motion_notify_event", start + (40, 20), button=1)
        transform = win.ax.transData.frozen()  # the view moves with the mouse
        self.mouse("motion_notify_event", start + (100, 50), button=1)
        shifted = np.asarray(win.canvas.buffer_rgba()).copy()
        self.mouse("button_release_event", start + (100, 50), button=1)
        np.testing.assert_allclose(self.view()[2], center - np.array([100, 50]) * scale)
        win.canvas.draw()
        drawn = np.asarray(win.canvas.buffer_rgba())
        # inside the map, without the parts moved in (100 pixels on the left, 50 at the bottom): the moved drawing
        # is the same as the map drawn again
        bbox, height = win.ax.bbox, win.fig.bbox.height
        top, bottom = int(height - bbox.y1) + 10, int(height - bbox.y0) - 60
        left, right = int(bbox.x0) + 110, int(bbox.x1) - 10
        diff = np.abs(shifted[top:bottom, left:right].astype(int) - drawn[top:bottom, left:right]).max(axis=-1)
        self.assertGreater(np.mean(diff <= 8), 0.99)
        self.assertFalse(np.allclose(transform.get_matrix(), win.ax.transData.get_matrix()))
        win.toolbar.back()
        win.canvas.draw()
        np.testing.assert_allclose(self.view()[2], center)


if __name__ == "__main__":
    unittest.main()
