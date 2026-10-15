"""lanelet 之间的拓扑关系与几何重叠"""

from collections import defaultdict

import numpy as np

from .geometry import overlap_area
from .lanelet_map import by_id


def build_topology(lanelets):
    """前后继关系: 与 lanelet2 一致，按共享端点节点判断"""
    by_start = defaultdict(list)
    for ll in lanelets.values():
        by_start[(ll.left_ids[0], ll.right_ids[0])].append(ll.id)
    succ, pred = defaultdict(set), defaultdict(set)
    for ll in lanelets.values():
        for nxt in by_start.get((ll.left_ids[-1], ll.right_ids[-1]), []):
            if nxt != ll.id:
                succ[ll.id].add(nxt)
                pred[nxt].add(ll.id)
    return succ, pred


def relation_between(a, b, succ, pred):
    """返回两个 lanelet 的拓扑关系: follow / adjacent / diverge / merge；None 表示没有直接拓扑关系"""
    if b.id in succ[a.id] or a.id in succ[b.id]:
        return "follow"
    if {a.left_way, a.right_way} & {b.left_way, b.right_way}:
        return "adjacent"
    if pred[a.id] & pred[b.id] or (a.left_ids[0], a.right_ids[0]) == (b.left_ids[0], b.right_ids[0]):
        return "diverge"
    if succ[a.id] & succ[b.id] or (a.left_ids[-1], a.right_ids[-1]) == (b.left_ids[-1], b.right_ids[-1]):
        return "merge"
    return None


def find_overlaps(lanelets, min_area, step=0.5):
    """
    找出所有几何重叠的车辆 lanelet 对 (不含前后相接、左右相邻)。
    返回 [(a_id, b_id, rel, 面积, 重叠中心)]，rel 为 None (交叉) / "merge" / "diverge"。
    """
    lls = sorted((ll for ll in lanelets.values() if ll.is_vehicle), key=by_id)
    succ, pred = build_topology(lanelets)
    bbox = np.array([np.r_[ll.polygon.min(axis=0), ll.polygon.max(axis=0)] for ll in lls])
    samples = {}
    result = []
    for i, a in enumerate(lls):
        cand = np.nonzero(
            (bbox[i + 1:, 0] <= bbox[i, 2]) & (bbox[i + 1:, 2] >= bbox[i, 0])
            & (bbox[i + 1:, 1] <= bbox[i, 3]) & (bbox[i + 1:, 3] >= bbox[i, 1])
        )[0] + i + 1
        for j in cand:
            b = lls[j]
            rel = relation_between(a, b, succ, pred)
            if rel in ("follow", "adjacent"):
                continue
            area, pts = overlap_area(a, b, step, samples)
            if area >= min_area:
                result.append((a.id, b.id, rel, area, pts.mean(axis=0)))
    return result
