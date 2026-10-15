#!/usr/bin/env python3
"""
Lanelet2 地图检查工具

检查项:
  1. 转向灯 (turn_direction): 弯道 / 转弯，以及与其他 lane 相交的直行 lanelet 是否设置了 turn_direction
  2. 路权 (right_of_way): 交叉 / 汇入的 lanelet 是否设置了 right_of_way；
     已有 right_of_way 是否被优先 lanelet 引用、两条 lanelet 是否真的重叠 (完全不重叠时输出红色 ERROR)
各参数的含义见 config/config.yaml。只依赖 numpy 和 PyYAML，不需要 source ROS。

用法:
  python3 check_lanelet_map.py                      # 使用 config/config.yaml
  python3 check_lanelet_map.py -c other_config.yaml
  python3 check_lanelet_map.py some_map.osm --turn-angle 45   # 命令行参数优先于 config.yaml

目录结构:
  MapChecker/
    check_lanelet_map.py   入口
    config/config.yaml     配置 (其中的相对路径以 MapChecker/ 为基准)
    config/ignore.yaml     手动忽略的 lanelet
    utils/                 地图读取、几何、拓扑、各项检查、输出
"""

import argparse
import os
import sys
import time

import yaml

from utils.check_right_of_way import check_right_of_way
from utils.check_turn_direction import check_turn_direction
from utils.config import load_config
from utils.geometry import lanelets_in_aoi
from utils.ignore import ignore_by_category, load_ignore, report_ignored
from utils.lanelet_map import load_map
from utils.printer import Printer
from utils.topology import find_overlaps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")


def run(map_path, opts, ignore_entries, ignore_path, out):
    lanelets, reg_elems, aois = load_map(map_path)
    out.info(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    out.info(f"Map: {os.path.abspath(map_path)}")
    out.info(f"Lanelets: {len(lanelets)}, regulatory elements: {len(reg_elems)}")

    vehicle = {k: v for k, v in lanelets.items() if v.is_vehicle}
    aoi_ids = None
    if opts["only_check_AOI"]:
        if aois:
            aoi_ids = lanelets_in_aoi(vehicle, aois)
            out.info(f"AOI: {len(aois)} area(s), {len(aoi_ids)} of {len(vehicle)} lanelets fully inside (only these are checked)")
        else:
            out.info("AOI: only_check_AOI is enabled but no AOI found in map, checking the whole map")

    # 几何关系始终用整张地图计算，AOI 只限制报告范围
    overlaps = find_overlaps(lanelets, opts["min_overlap_area"])
    # 与其他 lane 相交 (交叉 / 汇入) 的 lanelet；分叉不算相交，分叉出去的弯道视为独立转向
    intersecting = {x for a_id, b_id, rel, *_ in overlaps if rel != "diverge" for x in (a_id, b_id)}

    check_turn_direction({k: v for k, v in vehicle.items() if aoi_ids is None or k in aoi_ids}, intersecting, opts, out)
    check_right_of_way(lanelets, reg_elems, overlaps, aoi_ids, opts, out)

    out.header("Summary")
    out.info(f"turn_direction warnings: {out.count['turn_direction']}")
    out.info(f"right_of_way warnings:   {out.count['right_of_way']}")
    out.info(f"right_of_way errors:     {out.errors['right_of_way']}")
    if out.total == 0:
        out.ok("No issue found")

    report_ignored(ignore_entries, ignore_path, lanelets, aoi_ids, out)
    return out.total


def parse_args():
    parser = argparse.ArgumentParser(description="Check turn_direction and right_of_way in a Lanelet2 map")
    parser.add_argument("map", nargs="?", help="path to lanelet2_map.osm (overrides 'input' in config)")
    parser.add_argument("-c", "--config", help="config file, default: config/config.yaml next to this script")
    parser.add_argument("--turn-angle", type=float,
                        help="min heading change (deg) to consider a lanelet as a curve (overrides config)")
    parser.add_argument("--turn-window", type=float,
                        help="max arc length (m) over which the heading change is measured, so gentle bends "
                             "on long lanelets are ignored (overrides config)")
    parser.add_argument("--min-overlap-area", type=float,
                        help="min overlap area (m²) to consider two lanelets as overlapping (overrides config)")
    parser.add_argument("--ignore-isolated-turns", action=argparse.BooleanOptionalAction, default=None,
                        help="ignore curves that do not intersect any other lane (overrides config)")
    parser.add_argument("--straight-as-turn-light", action=argparse.BooleanOptionalAction, default=None,
                        help="count turn_direction=straight as a turn light setting (overrides config)")
    parser.add_argument("--only-check-aoi", action=argparse.BooleanOptionalAction, default=None,
                        help="only check lanelets fully inside the AOI of the map (overrides config)")
    parser.add_argument("--ignore-file", help="file listing lanelets to ignore (overrides config)")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    return parser, parser.parse_args()


def main():
    parser, args = parse_args()

    config_path = args.config or DEFAULT_CONFIG_PATH
    if not os.path.isfile(config_path):
        parser.error(f"config file not found: {config_path}")
    cfg = load_config(config_path, BASE_DIR)
    map_path = args.map or cfg["input"]
    if not os.path.isfile(map_path):
        parser.error(f"map file not found: {map_path}")
    ignore_path = os.path.abspath(args.ignore_file or cfg["ignore_file"])
    if args.ignore_file and not os.path.isfile(ignore_path):
        parser.error(f"ignore file not found: {args.ignore_file}")
    try:
        ignore_entries = load_ignore(ignore_path)
    except (ValueError, yaml.YAMLError) as e:
        parser.error(str(e))

    # 命令行参数优先于 config.yaml
    cli = {
        "turn_angle": args.turn_angle,
        "turn_window": args.turn_window,
        "min_overlap_area": args.min_overlap_area,
        "ignore_isolated_turns": args.ignore_isolated_turns,
        "straight_as_turn_light": args.straight_as_turn_light,
        "only_check_AOI": args.only_check_aoi,
    }
    opts = {k: cfg[k] if v is None else v for k, v in cli.items()}

    log_file = log_path = None
    if cfg["log"]["enabled"]:
        os.makedirs(cfg["log"]["dir"], exist_ok=True)
        log_path = os.path.join(cfg["log"]["dir"], time.strftime("map_check_%Y%m%d_%H%M%S.log"))
        log_file = open(log_path, "w", encoding="utf-8")

    out = Printer(not args.no_color and sys.stdout.isatty(), log_file, ignore_by_category(ignore_entries))
    try:
        total = run(map_path, opts, ignore_entries, ignore_path, out)
    finally:
        if log_file:
            log_file.close()
            print(f"\nLog saved to: {log_path}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
