"""
Tests of the GeoJSON output of the issues (utils/geojson.py, json.enabled, --json):
  cd MapChecker && python3 -m unittest discover -s tests -v
"""

import contextlib
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_masque import BASE_DIR, write_crossing  # also puts libs/ and MapChecker/ on sys.path

from utils.context import MapContext  # noqa: E402
from utils.geojson import id_types, issue_key, write_geojson  # noqa: E402
from utils.printer import Printer  # noqa: E402

OPTS = {"min_overlap_area": 1.0, "parallel_min_length": 10.0, "parallel_min_gap": 1.5, "turn_angle": 30.0,
        "turn_window": 30.0, "only_check_AOI": False}


class GeoJsonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.map_path = os.path.join(self.tmp, "map.osm")
        write_crossing(self.map_path)  # lanelets 21 along +x and 22 along +y crossing at x ~ 30, y ~ 1.75

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_key_and_ids(self):
        # ids sorted as numbers, duplicates removed; the message identifies an issue without ids
        self.assertEqual(issue_key("right_of_way.missing", ("22", "-5", "21", "22"), "x"), "right_of_way.missing:-5/21/22")
        self.assertEqual(issue_key("autoware.load_failed", (), "failed to load map"),
                         "autoware.load_failed:failed to load map")
        ctx = MapContext.from_file(self.map_path, OPTS)
        self.assertEqual(id_types(("21", "11", "1", "999", "21"), ctx),
                         {"lanelet": ["21"], "way": ["11"], "node": ["1"], "unknown": ["999"]})

    def test_write(self):
        """One point per printed issue at the position of the heatmap, with lon/lat; no geometry without a position;
        ignored issues and codes turned off are not written but counted"""
        ctx = MapContext.from_file(self.map_path, OPTS)
        out = Printer(False, ignore={"turn_direction": {"22": ""}})
        with contextlib.redirect_stdout(io.StringIO()):
            out.issue("right_of_way", "right_of_way.missing", "lanelet 21 / 22: crossing", ("21", "22"),
                      pos=(29.75, 1.75))
            out.issue("turn_direction", "turn_direction.missing_straight", "lanelet 21: straight", ("21",))
            out.issue("turn_direction", "turn_direction.missing_straight", "lanelet 22: straight", ("22",))  # ignored
            out.issue("autoware", "autoware.load_failed", "failed to load map")
        path = os.path.join(self.tmp, "issues.geojson")
        n, unplaced, note = write_geojson(path, out, ctx, {"map": self.map_path, "checks": ["right_of_way"]}, 30.0)
        self.assertEqual((n, unplaced, note), (3, 1, None))

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["type"], "FeatureCollection")
        info = data["mapchecker"]
        self.assertEqual((info["format"], info["map"], info["checks"]), (1, self.map_path, ["right_of_way"]))
        self.assertEqual(info["summary"], {"issues": 3, "warnings": 2, "errors": 1, "ignored": {"turn_direction": 1},
                                           "not_reported": {},
                                           "by_code": {"right_of_way.missing": 1, "turn_direction.missing_straight": 1,
                                                       "autoware.load_failed": 1}})

        row, td, load = data["features"]
        self.assertEqual(row["properties"], {
            "key": "right_of_way.missing:21/22", "code": "right_of_way.missing", "severity": "warning",
            "check": "right_of_way", "title": out.codes.title("right_of_way.missing"),
            "message": "lanelet 21 / 22: crossing", "ids": {"lanelet": ["21", "22"]}, "x": 29.75, "y": 1.75, "p": None})
        # lat = 48 + y * 1e-5, lon = 2 + x * 1e-5 in this map
        self.assertEqual(row["geometry"]["type"], "Point")
        lon, lat = row["geometry"]["coordinates"]
        self.assertAlmostEqual(lon, 2 + 29.75e-5, places=8)
        self.assertAlmostEqual(lat, 48 + 1.75e-5, places=8)
        # without given coordinates: the centerline midpoint of the lanelet, as in the heatmap
        self.assertEqual((td["properties"]["x"], td["properties"]["y"]), (30.0, 1.75))
        self.assertAlmostEqual(td["geometry"]["coordinates"][0], 2 + 30.0e-5, places=8)
        self.assertEqual((load["geometry"], load["properties"]["severity"], load["properties"]["x"]),
                         (None, "error", None))
        self.assertEqual(load["properties"]["key"], "autoware.load_failed:failed to load map")

    def cli(self, *args, **config):
        cfg = {"input": self.map_path, "ignore_file": f"{self.tmp}/none.yaml", "log": "{enabled: false, dir: "
               f"{self.tmp}/logs}}", "heatmap": "{enabled: false}", "Using_LR": "false", **config}
        path = os.path.join(self.tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(f"{k}: {v}\n" for k, v in cfg.items()))
        return subprocess.run([sys.executable, os.path.join(BASE_DIR, "check_lanelet_map.py"), "-c", path,
                               "--no-color", "--no-ext", *args], capture_output=True, text=True,
                              env={**os.environ, "MPLBACKEND": "Agg"})

    def files(self):
        return sorted(glob.glob(os.path.join(self.tmp, "logs", "*", "*.geojson")))

    def test_cli(self):
        # off by default (and nothing written without log nor heatmap)
        r = self.cli()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.files(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "logs")))

        # --json: written in the directory of the run even without log, same counts as the report
        r = self.cli("--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        files = self.files()
        self.assertEqual(len(files), 1, r.stdout)
        run_dir = os.path.dirname(files[0])
        self.assertEqual(os.listdir(run_dir), [os.path.basename(run_dir) + ".geojson"])
        self.assertIn(f"GeoJSON saved to: {files[0]}", r.stdout)
        self.assertRegex(r.stdout, r"GeoJSON: .*\.geojson \(3 issues\)")
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(sorted(feat["properties"]["key"] for feat in data["features"]),
                         ["right_of_way.missing:21/22", "turn_direction.missing_straight:21",
                          "turn_direction.missing_straight:22"])
        self.assertEqual(data["mapchecker"]["checks"], ["turn_direction", "right_of_way"])
        self.assertTrue(all(feat["geometry"] for feat in data["features"]))

        # json.enabled in the config, --no-json overrides it
        self.cli(json="{enabled: true}")
        self.assertEqual(len(self.files()), 2)
        self.cli("--no-json", json="{enabled: true}")
        self.assertEqual(len(self.files()), 2)


if __name__ == "__main__":
    unittest.main()
