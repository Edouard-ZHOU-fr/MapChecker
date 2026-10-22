"""Check 2: right_of_way (right of way) of overlapping lanelets"""

from collections import defaultdict

import numpy as np

from .features import right_of_way_features
from .geometry import fmt_xy, overlap_area, polygon_distance
from .lanelet_map import by_id


def check_right_of_way(lanelets, reg_elems, overlaps, aoi_ids, opts, out):
    """
    overlaps: result of find_overlaps()
    aoi_ids:  only report issues whose involved lanelets are all in it; None means check the whole map
    """
    out.header(f"Check 2: right_of_way on overlapping lanelets (min overlap area {opts['min_overlap_area']:.1f} m², "
               f"parallel lanes excluded: straight or opposite, overlap >= {opts['parallel_min_length']:.0f} m, centerlines >= "
               f"{opts['parallel_min_gap']:.1f} m apart)")

    def in_aoi(*ids):
        return aoi_ids is None or all(i in aoi_ids for i in ids)

    covered = _check_rules(lanelets, reg_elems, in_aoi, out)

    # crossing / merging lanelet pairs need a right_of_way (those diverging from the same lane do not)
    has_tl = {ll.id: any(r in reg_elems and reg_elems[r].subtype == "traffic_light" for r in ll.reg_elems)
              for ll in lanelets.values()}
    n_conflicts = n_covered = n_ignored = 0
    missing = []  # (a, b, description, position, features)
    for a_id, b_id, rel, area, pos, _ in overlaps:
        if rel == "diverge" or not in_aoi(a_id, b_id):
            continue
        n_conflicts += 1
        if frozenset((a_id, b_id)) in covered:
            n_covered += 1
            continue
        if out.is_ignored("right_of_way", a_id, b_id):
            n_ignored += 1
            continue
        kind = "merge" if rel == "merge" else "crossing"
        note = " [both controlled by traffic lights]" if has_tl[a_id] and has_tl[b_id] else ""
        missing.append((a_id, b_id, f"{kind} overlap (~{area:.1f} m² at {fmt_xy(pos)}) without right_of_way{note}", pos,
                        right_of_way_features(lanelets[a_id], lanelets[b_id], rel, area, pos, opts)))

    groups = _group_by_area(missing)
    for k, items in enumerate(groups, 1):
        ids = sorted({x for it in items for x in it[:2]}, key=int)
        center = np.mean([it[3] for it in items], axis=0)
        out.info(f"-- Area {k}, center {fmt_xy(center)}, lanelets: {' '.join(ids)}")
        if not out.model:
            for a_id, b_id, text, pos, features in items:
                out.warn("right_of_way", f"lanelet {a_id} / {b_id}: {text}", (a_id, b_id), features, pos)
    if out.model:
        # with a model, list all areas first, then output the warnings of all areas together by descending p,
        # each one noting its area
        out.hold()
        for k, items in enumerate(groups, 1):
            for a_id, b_id, text, pos, features in items:
                out.warn("right_of_way", f"lanelet {a_id} / {b_id} (area {k}): {text}", (a_id, b_id), features, pos)
        out.flush()

    out.info(f"{n_conflicts} overlapping lanelet pairs detected: {n_covered} with right_of_way, "
             f"{len(missing)} missing (in {len(groups)} areas)" + (f", {n_ignored} ignored" if n_ignored else ""))
    if out.count["right_of_way"] == 0 and out.errors["right_of_way"] == 0:
        out.ok("All overlapping lanelets have a right_of_way")


def covered_pairs(reg_elems):
    """lanelet pairs covered by right_of_way regulatory elements {frozenset((right_of_way, yield))}"""
    return {frozenset((r, y)) for re_ in reg_elems.values() if re_.subtype == "right_of_way"
            for r in re_.refs("right_of_way") for y in re_.refs("yield")}


def _check_rules(lanelets, reg_elems, in_aoi, out):
    """Check issues of the existing right_of_way regulatory elements themselves, return the lanelet pairs they cover"""
    for re_ in sorted(reg_elems.values(), key=by_id):
        if re_.subtype != "right_of_way":
            continue
        rows, yields = re_.refs("right_of_way"), re_.refs("yield")
        existing = [x for x in rows + yields if x in lanelets]
        for ref in rows + yields:
            if ref not in lanelets and in_aoi(*existing):
                out.warn("right_of_way", f"right_of_way {re_.id}: referenced lanelet {ref} does not exist", (ref,))
        if (not rows or not yields) and in_aoi(*existing):
            out.warn("right_of_way", f"right_of_way {re_.id}: missing right_of_way or yield member", rows + yields)
        for r in rows:
            if r in lanelets and re_.id not in lanelets[r].reg_elems and in_aoi(r):
                out.warn("right_of_way", f"right_of_way {re_.id}: priority lanelet {r} does not reference "
                                         f"this regulatory element (ignored by Autoware)", (r,))
            for y in yields:
                if r not in lanelets or y not in lanelets or not in_aoi(r, y):
                    continue
                # use finer sampling to confirm whether the two lanelets do not overlap at all
                if overlap_area(lanelets[r], lanelets[y], step=0.1)[0] == 0:
                    dist = polygon_distance(lanelets[r].polygon, lanelets[y].polygon)
                    out.error("right_of_way", f"right_of_way {re_.id}: lanelet {r} (right_of_way) and {y} (yield) "
                                              f"do not overlap at all (min distance {dist:.1f} m)", (r, y))
    return covered_pairs(reg_elems)


def _group_by_area(missing):
    """Group by area (connected components of the overlap relation, usually one intersection)"""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a_id, b_id, *_ in missing:
        parent[find(a_id)] = find(b_id)
    groups = defaultdict(list)
    for item in missing:
        groups[find(item[0])].append(item)
    return sorted(groups.values(), key=lambda g: int(g[0][0]))
