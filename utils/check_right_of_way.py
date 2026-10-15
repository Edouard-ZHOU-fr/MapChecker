"""检查 2: 重叠 lanelet 的 right_of_way (路权)"""

from collections import defaultdict

import numpy as np

from .geometry import fmt_xy, overlap_area, polygon_distance
from .lanelet_map import by_id


def check_right_of_way(lanelets, reg_elems, overlaps, aoi_ids, opts, out):
    """
    overlaps: find_overlaps() 的结果
    aoi_ids:  只报告涉及的 lanelet 全部在其中的问题；None 表示检查整张地图
    """
    out.header(f"Check 2: right_of_way on overlapping lanelets (min overlap area {opts['min_overlap_area']:.1f} m²)")

    def in_aoi(*ids):
        return aoi_ids is None or all(i in aoi_ids for i in ids)

    covered = _check_rules(lanelets, reg_elems, in_aoi, out)

    # 交叉 / 汇入的 lanelet 对需要 right_of_way (同一条 lane 分叉出来的不需要)
    has_tl = {ll.id: any(r in reg_elems and reg_elems[r].subtype == "traffic_light" for r in ll.reg_elems)
              for ll in lanelets.values()}
    n_conflicts = n_covered = n_ignored = 0
    missing = []  # (a, b, 描述, 位置)
    for a_id, b_id, rel, area, pos in overlaps:
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
        missing.append((a_id, b_id, f"{kind} overlap (~{area:.1f} m² at {fmt_xy(pos)}) without right_of_way{note}", pos))

    groups = _group_by_area(missing)
    for k, items in enumerate(groups, 1):
        ids = sorted({x for it in items for x in it[:2]}, key=int)
        center = np.mean([it[3] for it in items], axis=0)
        out.info(f"-- Area {k}, center {fmt_xy(center)}, lanelets: {' '.join(ids)}")
        for a_id, b_id, text, _ in items:
            out.warn("right_of_way", f"lanelet {a_id} / {b_id}: {text}")

    out.info(f"{n_conflicts} overlapping lanelet pairs detected: {n_covered} with right_of_way, "
             f"{len(missing)} missing (in {len(groups)} areas)" + (f", {n_ignored} ignored" if n_ignored else ""))
    if out.count["right_of_way"] == 0 and out.errors["right_of_way"] == 0:
        out.ok("All overlapping lanelets have a right_of_way")


def _check_rules(lanelets, reg_elems, in_aoi, out):
    """检查已有 right_of_way 规则元素本身的问题，返回其覆盖的 lanelet 对"""
    covered = set()
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
                covered.add(frozenset((r, y)))
                if r not in lanelets or y not in lanelets or not in_aoi(r, y):
                    continue
                # 用更细的采样确认两条 lanelet 是否完全没有重叠
                if overlap_area(lanelets[r], lanelets[y], step=0.1)[0] == 0:
                    dist = polygon_distance(lanelets[r].polygon, lanelets[y].polygon)
                    out.error("right_of_way", f"right_of_way {re_.id}: lanelet {r} (right_of_way) and {y} (yield) "
                                              f"do not overlap at all (min distance {dist:.1f} m)", (r, y))
    return covered


def _group_by_area(missing):
    """按区域 (重叠关系的连通分量，通常就是一个路口) 分组"""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a_id, b_id, _, _ in missing:
        parent[find(a_id)] = find(b_id)
    groups = defaultdict(list)
    for item in missing:
        groups[find(item[0])].append(item)
    return sorted(groups.values(), key=lambda g: int(g[0][0]))
