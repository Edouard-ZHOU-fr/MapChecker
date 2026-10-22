"""Topological relations and geometric overlaps between lanelets"""

from collections import defaultdict

import numpy as np

from .geometry import overlap_area, principal_extent
from .lanelet_map import by_id


def build_topology(lanelets):
    """Predecessor / successor relations: as in lanelet2, determined by shared end nodes"""
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
    """Returns the topological relation of two lanelets: follow / adjacent / diverge / merge; None: no direct relation"""
    if b.id in succ[a.id] or a.id in succ[b.id]:
        return "follow"
    if {a.left_way, a.right_way} & {b.left_way, b.right_way}:
        return "adjacent"
    if pred[a.id] & pred[b.id] or (a.left_ids[0], a.right_ids[0]) == (b.left_ids[0], b.right_ids[0]):
        return "diverge"
    if succ[a.id] & succ[b.id] or (a.left_ids[-1], a.right_ids[-1]) == (b.left_ids[-1], b.right_ids[-1]):
        return "merge"
    return None


def find_overlaps(lanelets, min_area, min_width=0.0, step=0.5, topology=None):
    """
    Find all geometrically overlapping vehicle lanelet pairs (excluding consecutive and adjacent ones): overlap area
    >= min_area and mean overlap width (area / length) >= min_width; the narrow overlaps of edge contact (parallel
    lanes on a narrow road, a curve grazing the opposite lane) are below min_width.
    Returns [(a_id, b_id, rel, area, overlap center, length of the overlap along the principal direction)],
    rel is None (crossing) / "merge" / "diverge".
    topology: precomputed build_topology(lanelets), recomputed if None
    """
    lls = sorted((ll for ll in lanelets.values() if ll.is_vehicle), key=by_id)
    succ, pred = topology or build_topology(lanelets)
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
            if area < min_area:
                continue
            length = principal_extent(pts)
            if area >= min_width * length:
                result.append((a.id, b.id, rel, area, pts.mean(axis=0), length))
    return result
