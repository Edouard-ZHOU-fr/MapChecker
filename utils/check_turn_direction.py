"""检查 1: 弯道 / 转弯的 turn_direction (转向灯)"""

from .features import turn_direction_features
from .geometry import arc_length, centerline, fmt_xy, max_heading_change
from .lanelet_map import by_id

VALID_TURN_DIRECTIONS = {"left", "right", "straight"}


def check_turn_direction(lanelets, intersecting, opts, out, ctx, autoware=None, step=0.5):
    """
    intersecting: 与其他 lane 相交 (交叉 / 汇入，分叉不算) 的 lanelet id 集合
      - 弯道: 应设置 turn_direction；ignore_isolated_turns 为 True 时，不相交的弯道被忽略
      - 相交的直行 lanelet: 见 straight_as_turn_light
    straight_as_turn_light: straight 是否算作已设置转向灯
      - True:  只检查是否设置了 turn_direction (left/right/straight 均可)，不检查方向是否与几何一致；
               弯道和相交的直行 lanelet 都必须设置
      - False: 检查方向与几何是否一致；弯道标 straight 视为未设置 (警告)；相交的直行 lanelet 不要求标 straight
    ctx: utils.features.lanelet_context() 的结果，用于计算告警的特征
    autoware: Autoware 检查的 turn_direction 结果 {lanelet id: 冲突的 lanelet / area id}，None 表示未启用；
              上面的几何检查已报告的 lanelet 不再重复输出，只补充几何检查漏掉的 (如分叉、重叠面积很小)
    """
    turn_angle, window = opts["turn_angle"], opts["turn_window"]
    ignore_isolated, straight_ok = opts["ignore_isolated_turns"], opts["straight_as_turn_light"]

    out.header(f"Check 1: turn_direction on curves / turns (angle threshold {turn_angle:.0f}°, window {window:.0f} m)")
    n_turns = n_ignored = n_straight = n_autoware = n_autoware_only = 0
    out.hold()  # 有模型时按 p 从大到小输出
    for ll in sorted(lanelets.values(), key=by_id):
        td = ll.turn_direction
        if td is not None and td not in VALID_TURN_DIRECTIONS:
            out.warn("turn_direction", f"lanelet {ll.id}: turn_direction=\"{td}\" is not a valid value (left/right/straight)", (ll.id,))
            continue

        center = centerline(ll, step)
        angle = max_heading_change(center, step, window)
        msg = None

        if abs(angle) < turn_angle:
            # 直行: 只检查与其他 lane 相交的
            if ll.id in intersecting:
                n_straight += 1
                if td is None and straight_ok:
                    msg = "intersects other lanes without turn_direction (geometry straight, suggest turn_direction=straight)"
                elif td in ("left", "right") and not straight_ok:
                    msg = f"intersects other lanes, turn_direction={td} but geometry is straight"
        else:
            n_turns += 1
            geo = "left" if angle > 0 else "right"
            if ignore_isolated and ll.id not in intersecting:
                n_ignored += 1
            elif td is None:
                msg = f"curve without turn_direction (geometry turns {geo}, suggest turn_direction={geo})"
            elif straight_ok:
                pass  # 只检查是否设置，不检查方向
            elif td == "straight":
                msg = f"curve tagged turn_direction=straight, no turn signal (geometry turns {geo})"
            elif td != geo:
                msg = f"turn_direction={td} is opposite to geometry (turns {geo})"

        if autoware and ll.id in autoware:
            n_autoware += 1
            if msg is None:
                n_autoware_only += 1
                msg = f"conflicts with {', '.join(autoware[ll.id])} in Autoware routing graph without turn_direction"

        if msg:
            where = f"middle {fmt_xy(center[len(center) // 2])}, angle {angle:+.0f}°, length {arc_length(center)[-1]:.1f} m"
            out.warn("turn_direction", f"lanelet {ll.id}: {msg} | {where}", (ll.id,),
                     turn_direction_features(ll, center, angle, opts, ctx))
    out.flush()

    if ignore_isolated:
        out.info(f"{n_turns} curved lanelets detected, {n_ignored} ignored (no crossing / merging with other lanes)")
    else:
        out.info(f"{n_turns} curved lanelets detected")
    out.info(f"{n_straight} straight lanelets intersect other lanes")
    if autoware is not None:
        out.info(f"{n_autoware} lanelets without turn_direction conflict with others in Autoware routing graph, "
                 f"{n_autoware_only} of them not detected by the geometry check above")
    if out.count["turn_direction"] == 0:
        out.ok("All checked lanelets have a correct turn_direction")
