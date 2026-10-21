"""Flow of Checks 1~3 (core checks), shared by check_lanelet_map.py and utils/build_dataset.py;
for extension checks see utils/ext.py"""

from .check_autoware import check_autoware
from .check_right_of_way import check_right_of_way
from .check_turn_direction import check_turn_direction
from .features import lanelet_context

CORE_CATEGORIES = ("turn_direction", "right_of_way", "autoware")


def run_core_checks(ctx, out, autoware=None):
    """
    ctx:      utils.context.MapContext
    autoware: result of validate() in utils/validate_autoware_map.py, None means Check 3 is not run
    """
    autoware_td = autoware.turn_direction if autoware else None
    # lanelets reported by Autoware take part in Check 1 even if their subtype is not in VEHICLE_SUBTYPES
    vehicle = {k: v for k, v in ctx.lanelets.items() if v.is_vehicle or k in (autoware_td or {})}
    aoi_ids = None
    if ctx.only_check_aoi:
        aoi_ids = {k for k in vehicle if ctx.in_aoi(k)}
        out.info(f"AOI: {len(ctx.aois)} area(s), {len(aoi_ids)} of {len(vehicle)} lanelets fully inside "
                 f"(only these are checked)")

    # geometric relations are always computed on the whole map, AOI only limits the reporting scope
    overlaps = ctx.overlaps
    # lanelets intersecting other lanes (crossing / merge); diverge does not count as intersecting,
    # curves diverging off are treated as independent turns
    intersecting = {x for a_id, b_id, rel, *_ in overlaps if rel != "diverge" for x in (a_id, b_id)}

    check_turn_direction({k: v for k, v in vehicle.items() if aoi_ids is None or k in aoi_ids}, intersecting,
                         ctx.opts, out, lanelet_context(ctx.lanelets, ctx.reg_elems, overlaps, ctx.topology[1]),
                         autoware_td)
    check_right_of_way(ctx.lanelets, ctx.reg_elems, overlaps, aoi_ids, ctx.opts, out)
    if autoware:
        check_autoware(autoware, aoi_ids, out)
