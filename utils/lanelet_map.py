"""读取 Lanelet2 OSM 地图"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .geometry import signed_area

# 参与检查的车辆 lanelet 类型 (crosswalk 等不参与)
VEHICLE_SUBTYPES = {"road", "highway", "road_shoulder"}


@dataclass
class Lanelet:
    id: str
    tags: dict
    left_ids: list
    right_ids: list
    left: np.ndarray
    right: np.ndarray
    left_way: str
    right_way: str
    reg_elems: set = field(default_factory=set)

    @property
    def subtype(self):
        return self.tags.get("subtype", "")

    @property
    def is_vehicle(self):
        return self.subtype in VEHICLE_SUBTYPES

    @property
    def turn_direction(self):
        return self.tags.get("turn_direction")

    @property
    def polygon(self):
        return np.vstack([self.left, self.right[::-1]])


@dataclass
class RegElem:
    id: str
    tags: dict
    members: list  # [(type, role, ref)]

    @property
    def subtype(self):
        return self.tags.get("subtype", "")

    def refs(self, role):
        return [ref for _, r, ref in self.members if r == role]


def by_id(elem):
    """按数字 id 排序用的 key"""
    return int(elem.id)


def _tags(elem):
    return {t.get("k"): t.get("v") for t in elem.findall("tag")}


def _project_latlon(latlon):
    """没有 local_x/local_y 时，用等距矩形投影把经纬度转成局部米制坐标 (只用于相对几何)"""
    ids = list(latlon)
    arr = np.array([latlon[i] for i in ids])
    lat0, lon0 = arr.mean(axis=0)
    r = 6378137.0
    x = np.radians(arr[:, 1] - lon0) * r * math.cos(math.radians(lat0))
    y = np.radians(arr[:, 0] - lat0) * r
    return {i: (x[k], y[k]) for k, i in enumerate(ids)}


def _orient_bounds(left_ids, right_ids, nodes):
    """
    与 lanelet2 读取地图时的行为一致:
      - 左右边界方向不一致时翻转右边界
      - 若左边界在几何上位于右侧，则两条边界同时翻转
    """
    left = np.array([nodes[i] for i in left_ids])
    right = np.array([nodes[i] for i in right_ids])
    same = np.linalg.norm(left[0] - right[0]) + np.linalg.norm(left[-1] - right[-1])
    crossed = np.linalg.norm(left[0] - right[-1]) + np.linalg.norm(left[-1] - right[0])
    if crossed < same:
        right_ids, right = right_ids[::-1], right[::-1]
    if signed_area(np.vstack([left, right[::-1]])) > 0:
        left_ids, left = left_ids[::-1], left[::-1]
        right_ids, right = right_ids[::-1], right[::-1]
    return left_ids, right_ids, left, right


def load_map(path):
    """返回 (lanelets, reg_elems, aois)；aois 为 AOI 多边形列表"""
    root = ET.parse(path).getroot()

    local, latlon = {}, {}
    for n in root.iter("node"):
        nid = n.get("id")
        t = _tags(n)
        if "local_x" in t and "local_y" in t:
            local[nid] = (float(t["local_x"]), float(t["local_y"]))
        latlon[nid] = (float(n.get("lat")), float(n.get("lon")))
    nodes = local if len(local) == len(latlon) else _project_latlon(latlon)

    ways = {w.get("id"): [nd.get("ref") for nd in w.findall("nd")] for w in root.iter("way")}

    # AOI: subtype=AOI 的闭合 way (例如 type=detection_area, subtype=AOI, area=yes)
    aois = [
        np.array([nodes[i] for i in ways[w.get("id")]])
        for w in root.iter("way")
        if _tags(w).get("subtype", "").lower() == "aoi" and len(ways[w.get("id")]) >= 3
    ]

    lanelets, reg_elems = {}, {}
    for rel in root.iter("relation"):
        rid = rel.get("id")
        t = _tags(rel)
        members = [(m.get("type"), m.get("role"), m.get("ref")) for m in rel.findall("member")]
        if t.get("type") == "lanelet":
            lw = [ref for _, role, ref in members if role == "left"]
            rw = [ref for _, role, ref in members if role == "right"]
            if len(lw) != 1 or len(rw) != 1 or lw[0] not in ways or rw[0] not in ways:
                continue
            left_ids, right_ids, left, right = _orient_bounds(ways[lw[0]], ways[rw[0]], nodes)
            lanelets[rid] = Lanelet(
                id=rid, tags=t,
                left_ids=left_ids, right_ids=right_ids, left=left, right=right,
                left_way=lw[0], right_way=rw[0],
                reg_elems={ref for _, role, ref in members if role == "regulatory_element"},
            )
        elif t.get("type") == "regulatory_element":
            reg_elems[rid] = RegElem(id=rid, tags=t, members=members)

    return lanelets, reg_elems, aois
