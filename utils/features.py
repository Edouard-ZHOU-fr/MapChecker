"""告警的特征 (logistic regression 打分用)，check_lanelet_map.py 和 utils/build_dataset.py 共用"""

import math
from collections import defaultdict

import numpy as np

from .geometry import arc_length, centerline, centerline_enters, max_heading_change
from .topology import build_topology

# 特征名及其在模型中的顺序；修改后需要重新运行 train_model.py
# 节点高度差试过后去掉了: 它随地图版本的处理方式 (如高度平滑) 而变，模型学到的是 "哪个版本" 而不是 "哪条告警"
FEATURES = {
    "turn_direction": (
        "abs_angle",           # 航向变化绝对值 (度)
        "curve",               # 1: 弯道 (abs_angle >= turn_angle)
        "log_length",          # log(1 + 中心线长度 m)
        "n_crossing",          # 与之真正交叉的 lanelet 数 (中心线进入对方车道，不含边缘相碰)
        "n_merge",             # 与之汇入的 lanelet 数
        "n_diverge",           # 与之分叉的 lanelet 数
        "log_overlap_length",  # log(1 + 与其他 lanelet 的最长重叠长度 m，分叉除外、含边缘相碰)，狭窄道路上平行车道的重叠很长
        "traffic_light",       # 1: 自身或前继 lanelet 引用了红绿灯 (有红绿灯的路口)
        "isolated",            # 1: 不与任何 lane 真正交叉 / 汇入 (只有分叉或边缘相碰)，即没有路口
    ),
    "right_of_way": (
        "log_area",       # log(1 + 重叠面积 m²)
        "merge",          # 1: 汇入，0: 交叉
        "heading_diff",   # 重叠处两条 lanelet 的航向差 (度，0~180)
        "max_abs_angle",  # 两条 lanelet 航向变化绝对值的较大者 (度)
    ),
}

# 领域知识: 这些特征的值越大，告警越可能是误报。训练时 (utils/tranning.py) 把它们的系数限制为 <= 0，
# 方向由这里决定，降低多少由数据决定 (数据不支持时系数为 0，即不起作用，不会反过来提高 p)
NEGATIVE_FEATURES = {
    "turn_direction": (
        "log_overlap_length",  # 重叠过长是道路狭窄造成的，不是真的交叉 / 汇入
        "traffic_light",       # 有红绿灯的路口
        "isolated",            # 没有路口 (例如分叉: Autoware 把分叉也算作冲突，分叉起点也常蹭到对向车道)
    ),
}


def lanelet_context(lanelets, reg_elems, overlaps, pred=None):
    """
    计算 turn_direction 特征用的每个 lanelet 的信息 {lanelet id: {...}}；pred 为已计算的前继关系，None 时重新计算:
      crossing / merge / diverge: 与之交叉 / 汇入 / 分叉的 lanelet 数；crossing 不含边缘相碰 (见 centerline_enters)
      overlap_length:             与其他 lanelet 的最长重叠长度 (m)，分叉除外、含边缘相碰
      traffic_light:              自身或前继 lanelet 是否引用了红绿灯
    """
    ctx = defaultdict(lambda: {"crossing": 0, "merge": 0, "diverge": 0, "overlap_length": 0.0, "traffic_light": False})
    for a_id, b_id, rel, _, _, length in overlaps:
        touch = rel is None and not centerline_enters(lanelets[a_id], lanelets[b_id])
        for x in (a_id, b_id):
            if not touch:
                ctx[x][rel or "crossing"] += 1
            if rel != "diverge":
                ctx[x]["overlap_length"] = max(ctx[x]["overlap_length"], length)
    tl = {ll.id for ll in lanelets.values()
          if any(r in reg_elems and reg_elems[r].subtype == "traffic_light" for r in ll.reg_elems)}
    if pred is None:
        _, pred = build_topology(lanelets)
    for lid in lanelets:
        ctx[lid]["traffic_light"] = lid in tl or bool(pred[lid] & tl)
    return ctx


def turn_direction_features(ll, center, angle, opts, ctx):
    """center / angle: 中心线和航向变化 (check_turn_direction 中已计算)；ctx: lanelet_context() 的结果"""
    c = ctx[ll.id]
    return {
        "abs_angle": abs(angle),
        "curve": float(abs(angle) >= opts["turn_angle"]),
        "log_length": math.log1p(arc_length(center)[-1]),
        "n_crossing": c["crossing"],
        "n_merge": c["merge"],
        "n_diverge": c["diverge"],
        "log_overlap_length": math.log1p(c["overlap_length"]),
        "traffic_light": float(c["traffic_light"]),
        "isolated": float(c["crossing"] + c["merge"] == 0),
    }


def right_of_way_features(a, b, rel, area, pos, opts, step=0.5):
    """rel / area / pos: find_overlaps() 给出的关系、重叠面积和重叠区中心"""
    ca, cb = centerline(a, step), centerline(b, step)
    cos = np.clip(np.dot(_heading_at(ca, pos), _heading_at(cb, pos)), -1.0, 1.0)
    return {
        "log_area": math.log1p(area),
        "merge": float(rel == "merge"),
        "heading_diff": math.degrees(math.acos(cos)),
        "max_abs_angle": max(abs(max_heading_change(c, step, opts["turn_window"])) for c in (ca, cb)),
    }


def _heading_at(center, pos):
    """中心线上离 pos 最近处的单位方向向量"""
    i = min(int(np.argmin(np.linalg.norm(center - pos, axis=1))), len(center) - 2)
    d = center[i + 1] - center[i]
    return d / max(np.linalg.norm(d), 1e-12)
