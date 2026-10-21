"""
地图读取 (utils/lanelet_map.py) 的测试: 地图文件中的信息都要能从 LaneletMap / MapContext 拿到，用一张合成的小地图:
  cd MapChecker && python3 -m unittest discover -s tests -v
"""

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

from utils.context import MapContext  # noqa: E402
from utils.lanelet_map import RegElem, load_map  # noqa: E402

# 一条沿 +x 的道路 lanelet 21 (右边界 way 12 的节点顺序与行驶方向相反)，带自定义中心线、停止线和红绿灯；
# 22、23 结构不完整；41 为 multipolygon
MAP = """<?xml version="1.0"?>
<osm version="0.6" generator="test">
  <MetaInfo format_version="1" map_version="3"/>
  <bounds minlat="48.0" minlon="2.0" maxlat="48.1" maxlon="2.1"/>
  {nodes}
  <way id="11"><nd ref="1"/><nd ref="2"/><tag k="type" v="line_thin"/><tag k="subtype" v="solid"/></way>
  <way id="12"><nd ref="4"/><nd ref="3"/><tag k="type" v="line_thin"/><tag k="subtype" v="dashed"/></way>
  <way id="15"><nd ref="5"/><nd ref="6"/></way>
  <way id="16"><nd ref="7"/><nd ref="8"/><tag k="type" v="stop_line"/><tag k="subtype" v="solid"/></way>
  <way id="17"><nd ref="10"/><nd ref="11"/><tag k="type" v="traffic_light"/><tag k="height" v="0.8"/></way>
  <way id="18"><nd ref="1"/><nd ref="99"/></way>
  <way id="19"><nd ref="12"/><nd ref="13"/><nd ref="14"/><nd ref="12"/><tag k="area" v="yes"/></way>
  <relation id="21"><member type="way" role="left" ref="11"/><member type="way" role="right" ref="12"/>
    <member type="way" role="centerline" ref="15"/><member type="relation" role="regulatory_element" ref="31"/>
    <tag k="type" v="lanelet"/><tag k="subtype" v="road"/><tag k="speed_limit" v="30"/></relation>
  <relation id="22"><member type="way" role="left" ref="11"/><tag k="type" v="lanelet"/></relation>
  <relation id="23"><member type="way" role="left" ref="18"/><member type="way" role="right" ref="12"/>
    <tag k="type" v="lanelet"/></relation>
  <relation id="31" version="5"><member type="way" role="refers" ref="17"/><member type="way" role="ref_line" ref="16"/>
    <tag k="type" v="regulatory_element"/><tag k="subtype" v="traffic_light"/></relation>
  <relation id="41"><member type="way" role="outer" ref="19"/><tag k="type" v="multipolygon"/></relation>
</osm>
"""
POINTS = {1: (0, 3.5, 1.0), 2: (50, 3.5, 2.0), 3: (0, 0, 1.0), 4: (50, 0, 2.0), 5: (0, 1.75, 1.0), 6: (50, 1.75, 2.0),
          7: (30, 3.6, 1.6), 8: (30, -0.1, 1.6), 9: (32, 5, None), 10: (32, 5, 6.0), 11: (32, 6, 6.0),
          12: (60, 0, 0), 13: (70, 0, 0), 14: (70, 10, 0)}


def osm(local=True):
    """MAP 的文本；local 为 False 时节点 1 没有 local_x / local_y (所有坐标改用经纬度投影)"""
    nodes = []
    for nid, (x, y, z) in POINTS.items():
        tags = (f'<tag k="local_x" v="{x}"/><tag k="local_y" v="{y}"/>' if local or nid != 1 else "") \
            + ("" if z is None else f'<tag k="ele" v="{z}"/>') + ('<tag k="color" v="red"/>' if nid == 9 else "")
        version = ' version="2"' if nid == 9 else ""
        # 1e-5 度约为纬度方向 1.1 m、经度方向 0.73 m (纬度 48 度)
        nodes.append(f'<node id="{nid}" lat="{48 + y * 1e-5}" lon="{2 + x * 1e-5}"{version}>{tags}</node>')
    return MAP.format(nodes="\n  ".join(nodes))


class LaneletMapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "map.osm")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(osm())
        self.map = load_map(self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_file_level(self):
        m = self.map
        self.assertEqual(m.meta, {"format_version": "1", "map_version": "3"})
        self.assertEqual(m.attrib, {"version": "0.6", "generator": "test"})
        self.assertEqual([(e.tag, e.get("maxlat")) for e in m.extra], [("bounds", "48.1")])
        self.assertFalse(m.projected)

    def test_nodes(self):
        n = self.map.nodes
        self.assertEqual(len(n), len(POINTS))
        self.assertEqual((n["2"].x, n["2"].y, n["2"].ele), (50.0, 3.5, 2.0))
        np.testing.assert_allclose(n["2"].xy, [50.0, 3.5])
        self.assertAlmostEqual(n["2"].lat, 48 + 3.5e-5)
        self.assertEqual(n["9"].tags["color"], "red")
        self.assertEqual(n["9"].attrib["version"], "2")
        self.assertTrue(math.isnan(n["9"].ele))

    def test_ways(self):
        w = self.map.ways
        self.assertEqual(len(w), 7)
        self.assertEqual((w["12"].type, w["12"].subtype, w["12"].node_ids), ("line_thin", "dashed", ["4", "3"]))
        self.assertEqual(w["17"].tags["height"], "0.8")
        np.testing.assert_allclose(w["16"].points, [[30, 3.6], [30, -0.1]])
        np.testing.assert_allclose(w["16"].z, [1.6, 1.6])
        self.assertTrue(w["19"].closed)
        self.assertFalse(w["11"].closed)
        self.assertEqual(w["18"].missing, ["99"])
        self.assertTrue(np.isnan(w["18"].points[1]).all())

    def test_lanelets_and_relations(self):
        m = self.map
        self.assertEqual(list(m.lanelets), ["21"])
        ll = m.lanelets["21"]
        self.assertEqual((ll.left_way, ll.right_way, ll.centerline_way), ("11", "12", "15"))
        self.assertEqual(ll.right_ids, ["3", "4"])  # 右边界翻转成与行驶方向一致
        self.assertEqual((ll.left_inverted, ll.right_inverted), (False, True))
        np.testing.assert_allclose(ll.left_z, [1.0, 2.0])
        self.assertEqual(ll.reg_elems, {"31"})
        self.assertEqual(ll.tags["speed_limit"], "30")
        # 结构不完整的 lanelet 不解析，记下原因
        self.assertEqual(m.skipped, [("22", "0 right ways"), ("23", "left way 18 references missing nodes 99")])

        self.assertEqual(set(m.relations), {"21", "22", "23", "31", "41"})
        self.assertEqual(m.relations["21"].refs("centerline"), ["15"])
        self.assertEqual(m.relations["41"].type, "multipolygon")
        self.assertEqual(m.relations["41"].members, [("way", "outer", "19")])
        re_ = m.reg_elems["31"]
        self.assertIs(re_, m.relations["31"])
        self.assertIsInstance(re_, RegElem)
        self.assertEqual((re_.subtype, re_.attrib["version"]), ("traffic_light", "5"))
        # 规则元素成员的几何: 红绿灯的高度、停止线的位置
        self.assertEqual(m.ways[re_.refs("refers")[0]].tags["height"], "0.8")
        np.testing.assert_allclose(m.ways[re_.refs("ref_line")[0]].points[:, 0], [30, 30])

    def test_projected(self):
        """有节点缺少 local_x / local_y 时，所有节点都用经纬度投影，几何关系不变"""
        path = os.path.join(self.tmp, "latlon.osm")
        with open(path, "w", encoding="utf-8") as f:
            f.write(osm(local=False))
        m = load_map(path)
        self.assertTrue(m.projected)
        width = np.linalg.norm(m.nodes["1"].xy - m.nodes["3"].xy)
        self.assertAlmostEqual(width, 3.5 * 1.11, delta=0.05)  # 纬度方向 1e-5 度约 1.11 m
        self.assertEqual(m.nodes["1"].tags.get("local_x"), None)

    def test_context(self):
        ctx = MapContext.from_file(self.path, {"min_overlap_area": 1.0, "only_check_AOI": False})
        self.assertIs(ctx.map.ways, ctx.ways)
        self.assertIs(ctx.nodes, ctx.map.nodes)
        self.assertIs(ctx.relations, ctx.map.relations)
        self.assertEqual(list(ctx.lanelets), ["21"])
        self.assertEqual(ctx.map.meta["map_version"], "3")

    def test_cli_reports_skipped(self):
        cfg = os.path.join(self.tmp, "config.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(f"input: {self.path}\nignore_file: {self.tmp}/none.yaml\nlog: {{enabled: false}}\n"
                    "heatmap: {enabled: false}\nUsing_LR: false\n")
        r = subprocess.run([sys.executable, os.path.join(BASE_DIR, "check_lanelet_map.py"), "-c", cfg, "--no-color",
                            "--no-ext"], capture_output=True, text=True)
        self.assertIn("Lanelets not loaded (incomplete, not checked): 22 (0 right ways); "
                      "23 (left way 18 references missing nodes 99)", r.stdout, r.stderr)


if __name__ == "__main__":
    unittest.main()
