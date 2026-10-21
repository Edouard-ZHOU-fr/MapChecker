"""
读取 Lanelet2 OSM 地图。load_map() 返回 LaneletMap，保留文件中的全部信息，新的检查不用再改这里:
  nodes      所有 <node> (Node): 坐标、高程 ele、经纬度、全部标签和 XML 属性
  ways       所有 <way> (Way): 节点、坐标、高程、全部标签 (车道边界的线型、红绿灯的 height、停止线等)
  relations  所有 <relation> 的原始数据 (Relation)，包括 lanelet、regulatory_element 和其他类型 (如 multipolygon)
  lanelets   type=lanelet 的 relation 解析成的 Lanelet，边界按 lanelet2 的规则定向
  reg_elems  type=regulatory_element 的 relation (RegElem)
  aois       AOI 多边形
  meta / attrib / extra   <MetaInfo> 的属性 / <osm> 根元素的属性 / 其他顶层元素 (如 <bounds>，原样保留)
  skipped    结构不完整、没有解析成 Lanelet 的 lanelet relation 及原因
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .geometry import signed_area

# 参与检查的车辆 lanelet 类型 (crosswalk 等不参与)
VEHICLE_SUBTYPES = {"road", "highway", "road_shoulder"}


@dataclass
class Node:
    id: str
    x: float      # 局部米制坐标 local_x / local_y；地图中有节点缺少它们时，所有节点都由经纬度投影 (LaneletMap.projected)
    y: float
    ele: float    # 高程 ele (米)，没有时为 nan
    lat: float
    lon: float
    tags: dict    # 全部标签，包括 local_x、local_y、ele
    attrib: dict  # XML 属性 (id、lat、lon，以及 version 等)

    @property
    def xy(self):
        return np.array([self.x, self.y])


@dataclass
class Way:
    id: str
    node_ids: list      # 文件中的顺序
    points: np.ndarray  # [N, 2] 局部米制坐标，引用的节点不存在时为 nan
    z: np.ndarray       # [N] 高程 ele，没有 ele 或节点不存在时为 nan
    tags: dict
    attrib: dict
    missing: list = field(default_factory=list)  # 引用了、但地图中不存在的节点 id

    @property
    def type(self):
        return self.tags.get("type", "")

    @property
    def subtype(self):
        return self.tags.get("subtype", "")

    @property
    def closed(self):
        """首尾是同一个节点 (多边形，如 AOI、bus_stop_area)"""
        return len(self.node_ids) >= 3 and self.node_ids[0] == self.node_ids[-1]


@dataclass
class Relation:
    id: str
    tags: dict
    members: list  # [(type, role, ref)]，文件中的顺序；type 为 node / way / relation
    attrib: dict = field(default_factory=dict)

    @property
    def type(self):
        return self.tags.get("type", "")

    @property
    def subtype(self):
        return self.tags.get("subtype", "")

    def refs(self, role):
        return [ref for _, r, ref in self.members if r == role]


class RegElem(Relation):
    """type=regulatory_element 的 relation；成员的几何用 LaneletMap.ways / nodes 按 ref 查找"""


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
    left_z: np.ndarray = None    # 左右边界各点的高程 ele (米)，与 left / right 一一对应；节点没有 ele 时为 nan
    right_z: np.ndarray = None
    centerline_way: str = None   # 地图中指定的中心线 (role=centerline 的 way)，没有时为 None
    left_inverted: bool = False  # 左 / 右边界的方向与 way 中节点的顺序相反 (读取时翻转过)
    right_inverted: bool = False

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
class LaneletMap:
    """地图文件中的全部信息，见本模块开头"""
    lanelets: dict    # {id: Lanelet}
    reg_elems: dict   # {id: RegElem}
    aois: list        # AOI 多边形 [N, 2]
    nodes: dict       # {id: Node}
    ways: dict        # {id: Way}
    relations: dict   # {id: Relation}，所有 relation；regulatory_element 与 reg_elems 中的是同一个对象
    meta: dict        # <MetaInfo> 的属性，如 {"format_version": "1", "map_version": "24"}；没有时为 {}
    attrib: dict      # <osm> 根元素的属性，如 {"generator": "VMB"}
    extra: list       # 其他顶层元素 (xml.etree.ElementTree.Element)
    skipped: list     # 没有解析成 Lanelet 的 lanelet relation: [(id, 原因)]
    projected: bool   # True: 有节点缺少 local_x / local_y，所有坐标都由经纬度投影得到


def by_id(elem):
    """按数字 id 排序用的 key"""
    return int(elem.id)


def _tags(elem):
    return {t.get("k"): t.get("v") for t in elem.findall("tag")}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _project_latlon(latlon):
    """没有 local_x/local_y 时，用等距矩形投影把经纬度转成局部米制坐标 (只用于相对几何)"""
    ids = list(latlon)
    arr = np.array([latlon[i] for i in ids])
    lat0, lon0 = np.nanmean(arr, axis=0)
    r = 6378137.0
    x = np.radians(arr[:, 1] - lon0) * r * math.cos(math.radians(lat0))
    y = np.radians(arr[:, 0] - lat0) * r
    return {i: (x[k], y[k]) for k, i in enumerate(ids)}


def _orient_bounds(left_ids, right_ids, nodes):
    """
    与 lanelet2 读取地图时的行为一致:
      - 左右边界方向不一致时翻转右边界
      - 若左边界在几何上位于右侧，则两条边界同时翻转
    返回 (left_ids, right_ids, left, right, 左边界是否翻转, 右边界是否翻转)
    """
    left = np.array([nodes[i] for i in left_ids])
    right = np.array([nodes[i] for i in right_ids])
    left_inv = right_inv = False
    same = np.linalg.norm(left[0] - right[0]) + np.linalg.norm(left[-1] - right[-1])
    crossed = np.linalg.norm(left[0] - right[-1]) + np.linalg.norm(left[-1] - right[0])
    if crossed < same:
        right_ids, right, right_inv = right_ids[::-1], right[::-1], True
    if signed_area(np.vstack([left, right[::-1]])) > 0:
        left_ids, left, left_inv = left_ids[::-1], left[::-1], True
        right_ids, right, right_inv = right_ids[::-1], right[::-1], not right_inv
    return left_ids, right_ids, left, right, left_inv, right_inv


def _incomplete(members, ways):
    """lanelet relation 不能解析成 Lanelet 的原因，可以解析时为 None"""
    for role in ("left", "right"):
        refs = [ref for _, r, ref in members if r == role]
        if len(refs) != 1:
            return f"{len(refs)} {role} ways"
        way = ways.get(refs[0])
        if way is None:
            return f"{role} way {refs[0]} not in map"
        if not way.node_ids:
            return f"{role} way {refs[0]} has no node"
        if way.missing:
            return f"{role} way {refs[0]} references missing nodes {' '.join(way.missing)}"
    return None


def load_map(path):
    """path 为文件路径或文件对象；返回 LaneletMap"""
    root = ET.parse(path).getroot()
    meta = dict(next((child.attrib for child in root if child.tag == "MetaInfo"), {}))
    extra = [child for child in root if child.tag not in ("node", "way", "relation", "MetaInfo")]

    raw, local, latlon = [], {}, {}
    for n in root.iter("node"):
        nid, t = n.get("id"), _tags(n)
        raw.append((nid, t, dict(n.attrib)))
        if "local_x" in t and "local_y" in t:
            local[nid] = (float(t["local_x"]), float(t["local_y"]))
        latlon[nid] = (_float(n.get("lat")), _float(n.get("lon")))
    projected = len(local) != len(latlon)
    xy = _project_latlon(latlon) if projected else local
    nodes = {nid: Node(nid, *xy[nid], _float(t.get("ele")), *latlon[nid], t, attrib) for nid, t, attrib in raw}

    nan2 = (math.nan, math.nan)
    ways = {}
    for w in root.iter("way"):
        wid, ids = w.get("id"), [nd.get("ref") for nd in w.findall("nd")]
        ways[wid] = Way(wid, ids, np.array([xy.get(i, nan2) for i in ids], dtype=float).reshape(-1, 2),
                        np.array([nodes[i].ele if i in nodes else math.nan for i in ids], dtype=float),
                        _tags(w), dict(w.attrib), [i for i in ids if i not in nodes])

    # AOI: subtype=AOI 的 way (例如 type=detection_area, subtype=AOI, area=yes)
    aois = [w.points for w in ways.values()
            if w.subtype.lower() == "aoi" and len(w.node_ids) >= 3 and not w.missing]

    lanelets, reg_elems, relations, skipped = {}, {}, {}, []
    for rel in root.iter("relation"):
        rid, t = rel.get("id"), _tags(rel)
        members = [(m.get("type"), m.get("role"), m.get("ref")) for m in rel.findall("member")]
        relations[rid] = (RegElem if t.get("type") == "regulatory_element" else Relation)(rid, t, members,
                                                                                          dict(rel.attrib))
        if t.get("type") == "lanelet":
            reason = _incomplete(members, ways)
            if reason:
                skipped.append((rid, reason))
                continue
            lw, rw = relations[rid].refs("left")[0], relations[rid].refs("right")[0]
            left_ids, right_ids, left, right, left_inv, right_inv = _orient_bounds(ways[lw].node_ids,
                                                                                   ways[rw].node_ids, xy)
            centerline = relations[rid].refs("centerline")
            lanelets[rid] = Lanelet(
                id=rid, tags=t,
                left_ids=left_ids, right_ids=right_ids, left=left, right=right,
                left_way=lw, right_way=rw,
                reg_elems={ref for _, role, ref in members if role == "regulatory_element"},
                left_z=np.array([nodes[i].ele for i in left_ids]), right_z=np.array([nodes[i].ele for i in right_ids]),
                centerline_way=centerline[0] if centerline else None,
                left_inverted=left_inv, right_inverted=right_inv,
            )
        elif t.get("type") == "regulatory_element":
            reg_elems[rid] = relations[rid]

    return LaneletMap(lanelets, reg_elems, aois, nodes, ways, relations, meta, dict(root.attrib), extra, skipped,
                      projected)
