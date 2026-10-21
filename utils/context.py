"""
一张地图及各项检查共享的计算结果，传给检查 1~3 和扩展检查 (ext/) 的 check(ctx, cfg, out)。
重叠、拓扑等用到时才计算，并缓存下来供之后的检查复用。
"""

from functools import cached_property

import numpy as np

from .geometry import lanelets_in_aoi
from .lanelet_map import by_id, load_map
from .topology import build_topology, find_overlaps


class MapContext:
    """
    lanelets:  {id: utils.lanelet_map.Lanelet}，包括 crosswalk 等非车辆 lanelet
    reg_elems: {id: utils.lanelet_map.RegElem}
    aois:      地图中的 AOI 多边形列表
    opts:      检查参数 (config/config.yaml 合并命令行参数)，如 opts["min_overlap_area"]
    map_path:  地图文件路径 (从内存读取时为 None)
    """

    def __init__(self, lanelets, reg_elems, aois, opts, map_path=None):
        self.lanelets = lanelets
        self.reg_elems = reg_elems
        self.aois = aois
        self.opts = opts
        self.map_path = map_path
        self._in_aoi = {}

    @classmethod
    def from_file(cls, map_path, opts):
        return cls(*load_map(map_path), opts, map_path)

    @property
    def only_check_aoi(self):
        """True 时只检查完全位于 AOI 内的 lanelet (见 in_aoi / select)"""
        return bool(self.opts.get("only_check_AOI"))

    @cached_property
    def vehicle(self):
        """车辆 lanelet {id: Lanelet} (subtype 为 road / highway / road_shoulder)"""
        return {k: v for k, v in self.lanelets.items() if v.is_vehicle}

    def by_subtype(self, *subtypes):
        """subtype 为其中之一的 lanelet {id: Lanelet}，例如 ctx.by_subtype("crosswalk")"""
        return {k: v for k, v in self.lanelets.items() if v.subtype in subtypes}

    @cached_property
    def topology(self):
        """前后继关系 (succ, pred)，均为 {lanelet id: 前 / 后继 lanelet id 的集合}"""
        return build_topology(self.lanelets)

    @cached_property
    def overlaps(self):
        """
        几何重叠的车辆 lanelet 对 (不含前后相接、左右相邻)，始终用整张地图计算:
        [(a_id, b_id, rel, 面积 m², 重叠中心 [x, y], 重叠区沿主方向的长度 m)]，rel 为 None (交叉) / "merge" / "diverge"
        """
        return find_overlaps(self.lanelets, self.opts["min_overlap_area"], topology=self.topology)

    def in_aoi(self, lanelet_id):
        """lanelet 是否在检查范围内: only_check_AOI 为 false 时总是 True，否则需要完全位于 AOI 内"""
        if not self.only_check_aoi:
            return True
        if lanelet_id not in self._in_aoi:
            ll = self.lanelets.get(lanelet_id)
            self._in_aoi[lanelet_id] = ll is not None and bool(lanelets_in_aoi({lanelet_id: ll}, self.aois))
        return self._in_aoi[lanelet_id]

    def select(self, lanelets):
        """lanelets ({id: Lanelet}) 中在检查范围内的 (见 in_aoi)"""
        return {k: v for k, v in lanelets.items() if self.in_aoi(k)}

    def vehicle_near(self, points):
        """包围盒与 points ([N, 2]，如某个 lanelet 的 polygon) 的包围盒相交的车辆 lanelet，按 id 排序；用于先粗筛再精确计算"""
        lo, hi = np.min(points, axis=0), np.max(points, axis=0)
        sel = np.nonzero((self._vehicle_bbox[:, 0] <= hi[0]) & (self._vehicle_bbox[:, 2] >= lo[0])
                         & (self._vehicle_bbox[:, 1] <= hi[1]) & (self._vehicle_bbox[:, 3] >= lo[1]))[0]
        return [self._vehicle_sorted[i] for i in sel]

    @cached_property
    def _vehicle_sorted(self):
        return sorted(self.vehicle.values(), key=by_id)

    @cached_property
    def _vehicle_bbox(self):
        return np.array([np.r_[ll.polygon.min(axis=0), ll.polygon.max(axis=0)]
                         for ll in self._vehicle_sorted]).reshape(-1, 4)
