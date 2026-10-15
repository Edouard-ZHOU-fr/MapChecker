"""检查 3: Autoware 官方检查 (validate_autoware_map.py) 的结果；其中 turn_direction 已合并到检查 1"""

MAX_LISTED_NODES = 10


def check_autoware(result, aoi_ids, out):
    """
    result:  validate_autoware_map.validate() 的结果
    aoi_ids: 红绿灯问题只报告引用它的 lanelet 在其中的；None 表示检查整张地图。
             load / elevation 是整张地图的问题，不受 AOI 和 ignore 影响
    """
    out.header("Check 3: Autoware validation (autoware_lanelet2_validation, turn_direction merged into Check 1)")
    out.info(f"format_version: {result.format_version or '-'}, map_version: {result.map_version or '-'}")

    for msg in result.load:
        out.error("autoware", msg)
    if result.turn_direction is None:
        out.info("Autoware turn_direction check skipped: fix the load errors first")

    if result.elevation:
        listed = ", ".join(result.elevation[:MAX_LISTED_NODES])
        more = f", ... (+{len(result.elevation) - MAX_LISTED_NODES})" if len(result.elevation) > MAX_LISTED_NODES else ""
        out.warn("autoware", f"{len(result.elevation)} node(s) without elevation (ele) tag: {listed}{more}")

    for msg, lanelet_ids in result.traffic_light:
        ids = [i for i in lanelet_ids if aoi_ids is None or i in aoi_ids]
        if ids:
            out.warn("autoware", f"{msg} (lanelet {', '.join(ids)})", ids)

    if out.count["autoware"] == 0 and out.errors["autoware"] == 0:
        out.ok("No issue found by Autoware validation")
