"""检查 1~3 (核心检查) 的流程，check_lanelet_map.py 和 utils/build_dataset.py 共用；扩展检查见 utils/ext.py"""

from .check_autoware import check_autoware
from .check_right_of_way import check_right_of_way
from .check_turn_direction import check_turn_direction
from .features import lanelet_context

CORE_CATEGORIES = ("turn_direction", "right_of_way", "autoware")


def run_core_checks(ctx, out, autoware=None):
    """
    ctx:      utils.context.MapContext
    autoware: utils/validate_autoware_map.py 的 validate() 的结果，None 表示不运行检查 3
    """
    autoware_td = autoware.turn_direction if autoware else None
    # Autoware 报告的 lanelet 即使 subtype 不在 VEHICLE_SUBTYPES 中也参与检查 1
    vehicle = {k: v for k, v in ctx.lanelets.items() if v.is_vehicle or k in (autoware_td or {})}
    aoi_ids = None
    if ctx.only_check_aoi:
        aoi_ids = {k for k in vehicle if ctx.in_aoi(k)}
        out.info(f"AOI: {len(ctx.aois)} area(s), {len(aoi_ids)} of {len(vehicle)} lanelets fully inside "
                 f"(only these are checked)")

    # 几何关系始终用整张地图计算，AOI 只限制报告范围
    overlaps = ctx.overlaps
    # 与其他 lane 相交 (交叉 / 汇入) 的 lanelet；分叉不算相交，分叉出去的弯道视为独立转向
    intersecting = {x for a_id, b_id, rel, *_ in overlaps if rel != "diverge" for x in (a_id, b_id)}

    check_turn_direction({k: v for k, v in vehicle.items() if aoi_ids is None or k in aoi_ids}, intersecting,
                         ctx.opts, out, lanelet_context(ctx.lanelets, ctx.reg_elems, overlaps, ctx.topology[1]),
                         autoware_td)
    check_right_of_way(ctx.lanelets, ctx.reg_elems, overlaps, aoi_ids, ctx.opts, out)
    if autoware:
        check_autoware(autoware, aoi_ids, out)
