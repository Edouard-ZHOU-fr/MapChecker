"""
Tests of the slow-down masque generated from the hotspots (utils/masque.py, --masque):
  cd MapChecker && python3 -m unittest discover -s tests -v
"""

import glob
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE_DIR, "libs"), BASE_DIR]

import numpy as np  # noqa: E402

from utils.config import DEFAULT_CONFIG  # noqa: E402
from utils.context import MapContext  # noqa: E402
from utils.geometry import points_in_polygon, signed_area  # noqa: E402
from utils.lanelet_map import load_map  # noqa: E402
from utils.masque import check_config, min_area_rectangle, write_masque  # noqa: E402

OPTS = {"min_overlap_area": 1.0, "parallel_min_length": 10.0, "parallel_min_gap": 1.5, "turn_angle": 30.0,
        "turn_window": 30.0, "only_check_AOI": False}


def write_crossing(path):
    """
    Two straight lanelets crossing without right_of_way nor turn_direction: 21 along +x (0 <= x <= 60,
    0 <= y <= 3.5), 22 along +y (28 <= x <= 31.5, -20 <= y <= 30): 3 issues close together, i.e. one hotspot.
    lat = 48 + y * 1e-5, lon = 2 + x * 1e-5, ele = 100 + x / 100
    """
    points = {1: (0, 3.5), 2: (60, 3.5), 3: (0, 0), 4: (60, 0), 5: (28, -20), 6: (28, 30), 7: (31.5, -20),
              8: (31.5, 30)}
    lines = ['<?xml version="1.0"?>', '<osm version="0.6">']
    for nid, (x, y) in points.items():
        lines.append(f'<node id="{nid}" lat="{48 + y * 1e-5}" lon="{2 + x * 1e-5}"><tag k="local_x" v="{x}"/>'
                     f'<tag k="local_y" v="{y}"/><tag k="ele" v="{100 + x / 100}"/></node>')
    for wid, refs in {11: (1, 2), 12: (3, 4), 13: (5, 6), 14: (7, 8)}.items():
        lines.append(f'<way id="{wid}">' + "".join(f'<nd ref="{r}"/>' for r in refs) + "</way>")
    for rid, (left, right) in {21: (11, 12), 22: (13, 14)}.items():
        lines.append(f'<relation id="{rid}"><member type="way" role="left" ref="{left}"/>'
                     f'<member type="way" role="right" ref="{right}"/><tag k="type" v="lanelet"/>'
                     '<tag k="subtype" v="road"/></relation>')
    lines.append("</osm>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class MasqueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.map_path = os.path.join(self.tmp, "map.osm")
        write_crossing(self.map_path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_rectangle(self):
        # points along a 20 m segment at 30°: the rectangle follows the segment, margin on each side
        d = np.array([math.cos(math.radians(30)), math.sin(math.radians(30))])
        pts = np.array([[5.0, 7.0]]) + np.linspace(0, 20, 5)[:, None] * d
        corners, size = min_area_rectangle(pts, 3.0)
        self.assertEqual(sorted(np.round(size, 1)), [6.0, 26.0])
        self.assertGreater(signed_area(corners), 0)  # counterclockwise
        self.assertTrue(points_in_polygon(pts, corners).all())
        # a single point: square of 2 margin
        _, size = min_area_rectangle(np.array([[1.0, 2.0]]), 3.0)
        self.assertEqual(np.round(size, 6).tolist(), [6.0, 6.0])

    def test_config(self):
        cfg = check_config({**DEFAULT_CONFIG["masque"], "slow_down_speed": "12", "subtype": "végétation"})
        self.assertEqual((cfg["slow_down_speed"], cfg["subtype"]), (12.0, "végétation"))
        for bad in ({"slow_down_speed": 0}, {"margin": -1}, {"subtype": " "}, {"height": "high"}, {"speed": 10}):
            with self.assertRaises(ValueError, msg=bad):
                check_config({**DEFAULT_CONFIG["masque"], **bad})

    def test_write(self):
        ctx = MapContext.from_file(self.map_path, OPTS)
        cfg = check_config({**DEFAULT_CONFIG["masque"], "subtype": "végétation & co", "slow_down_speed": 12})
        path = os.path.join(self.tmp, "masque.osm")
        spots = [np.array([[30.0, 1.75], [29.75, 5.0]]), np.array([[10.0, 1.75], [12.0, 1.75]])]
        areas = write_masque(path, spots, ctx, cfg)
        self.assertEqual([wid for wid, *_ in areas], ["5", "10"])

        masque = load_map(path)
        self.assertEqual(sorted(masque.nodes, key=int), ["1", "2", "3", "4", "6", "7", "8", "9"])
        self.assertEqual(masque.ways["5"].node_ids, ["1", "2", "3", "4"])
        self.assertEqual(masque.ways["10"].node_ids, ["6", "7", "8", "9"])
        self.assertEqual(masque.ways["10"].tags, {"type": "detection_area", "subtype": "végétation & co",
                                                  "area": "yes", "height": "0.2", "slow_down_speed": "12"})
        for wid, corners, _ in areas:
            way = masque.ways[wid]
            np.testing.assert_allclose(way.points, corners, atol=1e-4)
            self.assertTrue(points_in_polygon(spots[int(wid) // 5 - 1], way.points).all())
        for n in masque.nodes.values():
            # same lat/lon as the map nodes would have at this position, ele of the nearest boundary node
            self.assertAlmostEqual(n.lat, 48 + n.y * 1e-5, places=9)
            self.assertAlmostEqual(n.lon, 2 + n.x * 1e-5, places=9)
            nearest = min(ctx.nodes.values(), key=lambda m: math.hypot(m.x - n.x, m.y - n.y))
            self.assertEqual(n.ele, nearest.ele)

    def cli(self, *args, **config):
        cfg = {"input": self.map_path, "ignore_file": f"{self.tmp}/none.yaml", "log": "{enabled: true, dir: "
               f"{self.tmp}/logs}}", "heatmap": "{enabled: true}", "Using_LR": "false", "isShow": "false", **config}
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(f"{k}: {v}\n" for k, v in cfg.items()))
        return subprocess.run([sys.executable, os.path.join(BASE_DIR, "check_lanelet_map.py"), "-c", path,
                               "--no-color", "--no-ext", *args], capture_output=True, text=True,
                              env={**os.environ, "MPLBACKEND": "Agg"})

    def test_cli(self):
        r = self.cli("--masque")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        files = glob.glob(os.path.join(self.tmp, "logs", "*_masque.osm"))
        self.assertEqual(len(files), 1, r.stdout)
        self.assertIn(f"Masque saved to: {files[0]}", r.stdout)
        self.assertIn("way 5: hotspot #1", r.stdout)
        masque = load_map(files[0])
        self.assertEqual(list(masque.ways), ["5"])
        self.assertEqual(masque.ways["5"].tags["subtype"], "mapchecker_hotspot")

        # off by default; requires the heatmap
        self.cli()
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "logs", "*_masque.osm"))), 1)
        r = self.cli("--masque", "--no-heatmap")
        self.assertEqual(r.returncode, 2)
        self.assertIn("requires the heatmap", r.stderr)
        r = self.cli(masque="{enabled: true, speed: 10}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid masque config: unknown parameter(s) speed", r.stderr)


if __name__ == "__main__":
    unittest.main()
