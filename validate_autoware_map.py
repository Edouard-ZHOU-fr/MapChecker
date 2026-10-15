#!/usr/bin/env python3
"""
Autoware Lanelet2 地图检查 (autoware_lanelet2_validation 的 Python 版)

移植自 autoware_lanelet2_extension/src/validation.cpp，检查项和判定条件与其一致:
  1. elevation:      每个 <node> 都有 ele 标签
  2. traffic_light:  lanelet 引用的 AutowareTrafficLight 有 light_bulbs，light_bulb 有 traffic_light_id，
                     traffic_light 为 linestring 且有 height 标签
  3. turn_direction: 车辆可通行、且在路由图中与其他 lanelet / area 冲突的 lanelet 必须有 turn_direction
另外补充 validation.cpp 没做、但 lanelet2_map_loader 会做的检查:
  0. load:           Lanelet2 解析错误 (validation.cpp 收集后直接丢弃) 和 MetaInfo format_version

与 validation.cpp 一样用 MGRSProjector 投影，用 Germany / Vehicle 交规建路由图。
发现问题时返回 1 (validation.cpp 总是返回 0)，可用于 CI。

与 check_lanelet_map.py 不同，本脚本依赖 ROS 环境中的 lanelet2 和 autoware_lanelet2_extension_python:
  source /opt/ros/humble/setup.bash
  source ~/ros/ad_ros2_ws/install/setup.bash

用法:
  python3 validate_autoware_map.py lanelet2_map.osm
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET

try:
    import lanelet2
    from lanelet2.core import ConstLineString3d, LineString3d
    from lanelet2.io import Origin
    from lanelet2.routing import RoutingGraph
    from lanelet2.traffic_rules import Locations, Participants

    from autoware_lanelet2_extension_python.projection import MGRSProjector
    from autoware_lanelet2_extension_python.regulatory_elements import AutowareTrafficLight
except ImportError as e:
    sys.exit(f"{e}\nPlease source /opt/ros/humble/setup.bash and ad_ros2_ws/install/setup.bash first")

# lanelet2_map_loader 支持的 format_version 主版本 (lanelet::autoware::version = format_v2)
SUPPORTED_FORMAT_VERSION = 2


class Report:
    """按检查项统计问题数"""

    def __init__(self):
        self.count = {}
        self.current = None

    def section(self, name):
        print(f"\n== {name}")
        self.current = name
        self.count[name] = 0

    def issue(self, msg):
        print(msg)
        self.count[self.current] += 1

    @property
    def total(self):
        return sum(self.count.values())


def parse_major_version(format_version):
    """同 autoware_osm_parser.cpp 的 parseMajorVersion: `1` / `1.10` / `1.10.100` -> 1，否则 None"""
    m = re.fullmatch(r"(\d+\.)?(\d+\.)?(\d+)", format_version)
    if not m:
        return None
    return int((m.group(1) or m.group(3)).rstrip("."))


def check_format_version(osm_root, rep):
    """同 lanelet2_map_loader_node.cpp；loader 在 allow_unsupported_version=true 时只告警"""
    meta = osm_root.find("MetaInfo")
    format_version = meta.get("format_version", "") if meta is not None else ""
    major = parse_major_version(format_version)
    if major is None:
        # loader 只拦截缺失或非数字开头的版本号，这里把无法解析的 (如 "2a") 也算作问题
        rep.issue(f"map has no format_version(null) or non semver-style format_version({format_version})")
    elif major > SUPPORTED_FORMAT_VERSION:
        rep.issue(f"format_version({major}) of the map is larger than the supported version({SUPPORTED_FORMAT_VERSION})")
    else:
        print(f"format_version: {format_version}, map_version: {meta.get('map_version', '')}")


def check_elevation_tag(osm_root, rep):
    for node in osm_root.findall("node"):
        if node.find("tag[@k='ele']") is None:
            rep.issue(f"failed to find elevation tag for node: {node.get('id')}")


def check_traffic_light(lanelet_map, rep):
    # validation.cpp 逐 lanelet 检查，被多条 lanelet 引用的红绿灯会重复报告；这里去重
    lights = {}
    for lanelet in lanelet_map.laneletLayer:
        for reg_elem in lanelet.regulatoryElements:
            if isinstance(reg_elem, AutowareTrafficLight):
                lights[reg_elem.id] = reg_elem

    for light_id in sorted(lights):
        light = lights[light_id]
        light_bulbs = light.lightBulbs()
        if not light_bulbs:
            rep.issue(f"regulatory element traffic light {light_id} is missing optional light_bulb member. "
                      "You won't be able to use region_tlr node with this map")
        for light_string in light_bulbs:
            if "traffic_light_id" not in light_string.attributes:
                rep.issue(f"light_bulb {light_string.id} is missing traffic_light_id tag")
        for base_string_or_poly in light.trafficLights:
            if not isinstance(base_string_or_poly, (LineString3d, ConstLineString3d)):
                rep.issue(f"traffic_light {base_string_or_poly.id} is polygon, "
                          "and only linestring class is currently supported for traffic lights")
            if "height" not in base_string_or_poly.attributes:
                rep.issue(f"traffic_light {base_string_or_poly.id} is missing height tag")


def check_turn_direction(lanelet_map, rep):
    traffic_rules = lanelet2.traffic_rules.create(Locations.Germany, Participants.Vehicle)
    vehicle_graph = RoutingGraph(lanelet_map, traffic_rules)

    for lanelet in sorted(lanelet_map.laneletLayer, key=lambda ll: ll.id):
        if not traffic_rules.canPass(lanelet):
            continue
        conflicting = vehicle_graph.conflicting(lanelet)
        if not conflicting:
            continue
        if "turn_direction" not in lanelet.attributes:
            ids = ", ".join(str(c.id) for c in conflicting)
            rep.issue(f"lanelet {lanelet.id} seems to be intersecting other lanelet, "
                      f"but does not have turn_direction tagging. (conflicting: {ids})")


def main():
    parser = argparse.ArgumentParser(description="Python port of autoware_lanelet2_validation")
    parser.add_argument("map", help="path to lanelet2_map.osm")
    args = parser.parse_args()
    # lanelet2 的 C++ 部分崩溃时，保留已经打印的结果
    sys.stdout.reconfigure(line_buffering=True)

    try:
        osm_root = ET.parse(args.map).getroot()
    except (OSError, ET.ParseError) as e:
        print(f"failed to read {args.map}: {e}", file=sys.stderr)
        return 1

    rep = Report()
    print("starting validation")

    rep.section("load")
    check_format_version(osm_root, rep)
    try:
        lanelet_map, errors = lanelet2.io.loadRobust(args.map, MGRSProjector(Origin(0.0, 0.0)))
    except Exception as e:  # lanelet2 的致命解析错误 (validation.cpp 中未捕获，直接崩溃)
        rep.issue(f"failed to load map: {e}")
        return 1
    for error in errors:
        rep.issue(f"parse error: {error}")
    print(f"lanelets: {len(lanelet_map.laneletLayer)}, "
          f"regulatory elements: {len(lanelet_map.regulatoryElementLayer)}")

    rep.section("elevation")
    check_elevation_tag(osm_root, rep)
    rep.section("traffic_light")
    check_traffic_light(lanelet_map, rep)
    rep.section("turn_direction")
    if errors:
        # 解析出错时 lanelet 的边界可能不完整，在其上建路由图会段错误 (validation.cpp 同样会崩)
        rep.issue("skipped: fix the parse errors first, building the routing graph on an incomplete map may crash")
    else:
        check_turn_direction(lanelet_map, rep)

    print("\nfinished validation")
    for name, n in rep.count.items():
        print(f"  {name:<15} {n} issue(s)")
    return 1 if rep.total else 0


if __name__ == "__main__":
    sys.exit(main())
