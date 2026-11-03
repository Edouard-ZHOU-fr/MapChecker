"""
Read a Lanelet2 OSM map. load_map() returns a LaneletMap that keeps all the information in the file,
so new checks don't need to change this module:
  nodes      all <node> (Node): coordinates, elevation ele, lat/lon, all tags and XML attributes
  ways       all <way> (Way): nodes, coordinates, elevation, all tags
             (line type of lane boundaries, height of traffic lights, stop lines, etc.)
  relations  raw data of all <relation> (Relation), including lanelet, regulatory_element and other types (e.g. multipolygon)
  lanelets   Lanelets parsed from type=lanelet relations, boundaries oriented following the lanelet2 rules
  reg_elems  type=regulatory_element relations (RegElem)
  aois       AOI polygons
  meta / attrib / extra   <MetaInfo> attributes / <osm> root element attributes /
                          other top-level elements (e.g. <bounds>, kept as is)
  skipped    lanelet relations with an incomplete structure that were not parsed into a Lanelet, and the reason
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .geometry import signed_area

# Vehicle lanelet types that take part in the checks (crosswalk etc. do not)
VEHICLE_SUBTYPES = {"road", "highway", "road_shoulder"}


@dataclass
class Node:
    id: str
    x: float      # local metric coordinates local_x / local_y; if some nodes in the map lack them,
                  # all nodes are projected from lat/lon (LaneletMap.projected)
    y: float
    ele: float    # elevation ele (m), nan if absent
    lat: float
    lon: float
    tags: dict    # all tags, including local_x, local_y, ele
    attrib: dict  # XML attributes (id, lat, lon, and version etc.)

    @property
    def xy(self):
        return np.array([self.x, self.y])


@dataclass
class Way:
    id: str
    node_ids: list      # order in the file
    points: np.ndarray  # [N, 2] local metric coordinates, nan where the referenced node does not exist
    z: np.ndarray       # [N] elevation ele, nan where there is no ele or the node does not exist
    tags: dict
    attrib: dict
    missing: list = field(default_factory=list)  # ids of nodes that are referenced but do not exist in the map

    @property
    def type(self):
        return self.tags.get("type", "")

    @property
    def subtype(self):
        return self.tags.get("subtype", "")

    @property
    def closed(self):
        """First and last nodes are the same node (polygon, e.g. AOI, bus_stop_area)"""
        return len(self.node_ids) >= 3 and self.node_ids[0] == self.node_ids[-1]


@dataclass
class Relation:
    id: str
    tags: dict
    members: list  # [(type, role, ref)], order in the file; type is node / way / relation
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
    """type=regulatory_element relation; member geometry is looked up by ref in LaneletMap.ways / nodes"""


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
    left_z: np.ndarray = None    # elevation ele (m) of each point of the left / right boundary, one-to-one with
                                 # left / right; nan where the node has no ele
    right_z: np.ndarray = None
    centerline_way: str = None   # centerline specified in the map (way with role=centerline), None if absent
    left_inverted: bool = False  # left / right boundary runs opposite to the node order of its way (flipped on load)
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
    """All the information in the map file, see the beginning of this module"""
    lanelets: dict    # {id: Lanelet}
    reg_elems: dict   # {id: RegElem}
    aois: list        # AOI polygons [N, 2]
    nodes: dict       # {id: Node}
    ways: dict        # {id: Way}
    relations: dict   # {id: Relation}, all relations; regulatory_element ones are the same objects as in reg_elems
    meta: dict        # <MetaInfo> attributes, e.g. {"format_version": "1", "map_version": "24"}; {} if absent
    attrib: dict      # <osm> root element attributes, e.g. {"generator": "VMB"}
    extra: list       # other top-level elements (xml.etree.ElementTree.Element)
    skipped: list     # lanelet relations not parsed into a Lanelet: [(id, reason)]
    projected: bool   # True: some nodes lack local_x / local_y, all coordinates are projected from lat/lon


def by_id(elem):
    """Key for sorting by numeric id"""
    return int(elem.id)


def _tags(elem):
    return {t.get("k"): t.get("v") for t in elem.findall("tag")}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _project_latlon(latlon):
    """Without local_x/local_y, project lat/lon to local metric coordinates (equirectangular, relative geometry only)"""
    ids = list(latlon)
    arr = np.array([latlon[i] for i in ids])
    lat0, lon0 = np.nanmean(arr, axis=0)
    r = 6378137.0
    x = np.radians(arr[:, 1] - lon0) * r * math.cos(math.radians(lat0))
    y = np.radians(arr[:, 0] - lat0) * r
    return {i: (x[k], y[k]) for k, i in enumerate(ids)}


def _orient_bounds(left_ids, right_ids, nodes):
    """
    Same behavior as lanelet2 when reading a map:
      - if the left and right boundaries point in different directions, flip the right boundary
      - if the left boundary lies geometrically on the right side, flip both boundaries
    Returns (left_ids, right_ids, left, right, left boundary flipped, right boundary flipped)
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
    """Reason why the lanelet relation cannot be parsed into a Lanelet, None if it can"""
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
    """path is a file path or a file object; returns a LaneletMap"""
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

    # AOI: ways with subtype=AOI (e.g. type=detection_area, subtype=AOI, area=yes)
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
