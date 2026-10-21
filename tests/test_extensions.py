"""
扩展检查框架 (utils/ext.py) 和 ext/ 下各扩展的测试，用一张合成的小地图，不依赖仓库外的文件:
  cd MapChecker && python3 -m unittest discover -s tests -v

ext/ 下的每个扩展 (包括 _template) 都会被自动加载并运行一次，新加的扩展不用改这里也能发现接口问题。
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE_DIR, "libs"), BASE_DIR]

from utils import ext as extmod  # noqa: E402
from utils.context import MapContext  # noqa: E402
from utils.ignore import load_ignore, parse_items  # noqa: E402
from utils.printer import Printer  # noqa: E402

OPTS = {"min_overlap_area": 1.0, "only_check_AOI": False}


def write_map(path):
    """
    一条沿 +x 的道路 (0 <= x <= 50, 0 <= y <= 3.5)，和一条横穿它的 crosswalk (20 <= x <= 24, -2 <= y <= 5.5)，
    crosswalk 两端各超出道路 2 m
    """
    points = {1: (0, 3.5), 2: (50, 3.5), 3: (0, 0), 4: (50, 0),
              5: (20, -2), 6: (20, 5.5), 7: (24, -2), 8: (24, 5.5)}
    ways = {11: (1, 2), 12: (3, 4), 13: (5, 6), 14: (7, 8)}
    lanelets = {21: (11, 12, "road", {"speed_limit": "30"}), 22: (13, 14, "crosswalk", {})}
    lines = ['<?xml version="1.0"?>', '<osm version="0.6">']
    for nid, (x, y) in points.items():
        lines.append(f'<node id="{nid}" lat="{48 + y * 1e-5}" lon="{2 + x * 1e-5}">'
                     f'<tag k="local_x" v="{x}"/><tag k="local_y" v="{y}"/><tag k="ele" v="0"/></node>')
    for wid, refs in ways.items():
        lines.append(f'<way id="{wid}">' + "".join(f'<nd ref="{r}"/>' for r in refs) + "</way>")
    for rid, (left, right, subtype, tags) in lanelets.items():
        lines.append(f'<relation id="{rid}"><member type="way" role="left" ref="{left}"/>'
                     f'<member type="way" role="right" ref="{right}"/><tag k="type" v="lanelet"/>'
                     f'<tag k="subtype" v="{subtype}"/>' + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
                     + "</relation>")
    lines.append("</osm>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def run_extensions(extensions, map_path):
    """运行 load() 过的扩展，返回 (Printer, 输出的文字)"""
    out = Printer(False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        extmod.run_extensions(MapContext.from_file(map_path, OPTS), extensions, out)
    return out, buf.getvalue()


class ExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.map_path = os.path.join(self.tmp, "map.osm")
        write_map(self.map_path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_every_extension_loads_and_runs(self):
        """ext/ 下所有扩展 (包括 _template) 用默认配置都能加载，并在合成地图上运行不出错"""
        available = extmod.discover()
        self.assertIn("_template", available)
        for name, ext in available.items():
            with self.subTest(extension=name):
                extmod.load(ext)
                out, text = run_extensions([ext], self.map_path)
                self.assertNotIn("crashed", text)
                self.assertIn(f"=== Ext {name}", text, "the extension should print a header 'Ext <name>: ...'")

    def test_crosswalk(self):
        ext = extmod.load(extmod.discover()["crosswalk"], {"max_extension": 0.5, "ignore": []})
        cw = MapContext.from_file(self.map_path, OPTS)
        exts = ext.module.end_extensions(cw.lanelets["22"], cw.vehicle_near(cw.lanelets["22"].polygon))
        self.assertEqual([e[1] for e in exts], ["21", "21"])
        for length, _, inside in exts:
            self.assertAlmostEqual(length, 2.0, delta=0.02)
            self.assertFalse(inside)
        out, _ = run_extensions([ext], self.map_path)
        self.assertEqual(out.count["crosswalk"], 1)

        ext = extmod.load(extmod.discover()["crosswalk"], {"max_extension": 3.0})
        out, _ = run_extensions([ext], self.map_path)
        self.assertEqual(out.count["crosswalk"], 0)

    def test_new_extension_from_template(self):
        """按 ext/README.md 的步骤复制模板新建扩展: 自动发现、enable、--set、配置检查"""
        ext_dir = os.path.join(self.tmp, "ext")
        shutil.copytree(os.path.join(extmod.EXT_DIR, "_template"), os.path.join(ext_dir, "my_check"))
        available = extmod.discover(ext_dir)
        self.assertEqual(extmod.select(available), [])  # 模板的 enable 为 false
        self.write_ext(ext_dir, "my_check", None, "enable: true\n")
        available = extmod.discover(ext_dir)
        self.assertEqual(extmod.select(available), [available["my_check"]])

        ext = extmod.load(available["my_check"], {"required_tags": ["speed_limit", "one_way"]})
        out, text = run_extensions([ext], self.map_path)
        self.assertEqual(out.count["my_check"], 1)
        self.assertIn("lanelet 21: missing tag one_way", text)

        with self.assertRaisesRegex(ValueError, "unknown parameter"):
            extmod.load(extmod.discover(ext_dir)["my_check"], {"required_tag": ["x"]})
        with self.assertRaisesRegex(ValueError, "invalid config"):
            extmod.load(extmod.discover(ext_dir)["my_check"], {"required_tags": []})
        with self.assertRaisesRegex(ValueError, "unknown extension"):
            extmod.select(available, ["nope"])

    def test_helper_module_and_crash(self):
        """入口脚本可以相对导入 script/ 中的辅助模块；一个扩展崩溃时报 ERROR，之后的扩展照常运行"""
        ext_dir = os.path.join(self.tmp, "ext")
        self.write_ext(ext_dir, "broken", "def check(ctx, cfg, out):\n    raise RuntimeError('boom')\n")
        self.write_ext(ext_dir, "helped", "from ._helper import count_roads\n\n"
                                          "def check(ctx, cfg, out):\n    out.warn(f'{count_roads(ctx)} roads')\n")
        with open(os.path.join(ext_dir, "helped", "script", "_helper.py"), "w", encoding="utf-8") as f:
            f.write("def count_roads(ctx):\n    return len(ctx.vehicle)\n")

        available = extmod.discover(ext_dir)
        out, text = run_extensions([extmod.load(e) for e in extmod.select(available)], self.map_path)
        self.assertEqual(out.errors["broken"], 1)
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("[WARNING] 1 roads", text)

    @staticmethod
    def write_ext(ext_dir, name, script=None, config="enable: true\n"):
        for sub in ("script", "config"):
            os.makedirs(os.path.join(ext_dir, name, sub), exist_ok=True)
        with open(os.path.join(ext_dir, name, "config", "config.yaml"), "w", encoding="utf-8") as f:
            f.write(config)
        if script is not None:
            with open(os.path.join(ext_dir, name, "script", f"check_{name}.py"), "w", encoding="utf-8") as f:
                f.write(script)

    def test_ignore_items(self):
        self.assertEqual(parse_items([614, -99865, {615: "reason"}, {616: None}], "x"),
                         {"614": "", "-99865": "", "615": "reason", "616": ""})
        self.assertEqual(parse_items({614: "a"}, "x"), {"614": "a"})
        with self.assertRaisesRegex(ValueError, "must be a list"):
            parse_items(614, "x")
        with self.assertRaisesRegex(ValueError, "invalid lanelet id"):
            parse_items(["abc"], "x")
        path = os.path.join(self.tmp, "ignore.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("all: [614]\ncrosswalk: [22: too wide]\n")
        self.assertEqual(load_ignore(path, ["crosswalk"]), [("all", "614", ""), ("crosswalk", "22", "too wide")])
        with self.assertRaisesRegex(ValueError, "unknown key 'crosswalk'"):
            load_ignore(path)

    def test_cli(self):
        cfg = os.path.join(self.tmp, "config.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(f"input: {self.map_path}\nignore_file: {self.tmp}/none.yaml\nlog: {{enabled: false}}\n"
                    "heatmap: {enabled: false}\nUsing_LR: false\n")

        def run(*args):
            return subprocess.run([sys.executable, os.path.join(BASE_DIR, "check_lanelet_map.py"), "-c", cfg,
                                   "--no-color", *args], capture_output=True, text=True)

        r = run("--only-ext", "crosswalk", "--set", "crosswalk.max_extension=0.5")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("crosswalk warnings:      1", r.stdout)
        self.assertNotIn("Check 1", r.stdout)
        r = run("--only-ext", "crosswalk", "--set", "crosswalk.max_extension=3")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run("--only-ext", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown extension", r.stderr)
        r = run("--list-ext")
        self.assertEqual(r.returncode, 0)
        self.assertIn("crosswalk", r.stdout)


if __name__ == "__main__":
    unittest.main()
