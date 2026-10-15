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
check_lanelet_map.py 在 autoware_checking 为 true 时调用 validate()，把结果合并到它的报告中。

与 check_lanelet_map.py 不同，本脚本依赖 ROS 环境中的 lanelet2 和 autoware_lanelet2_extension_python:
  source /opt/ros/humble/setup.bash
  source ~/ros/ad_ros2_ws/install/setup.bash

用法:
  python3 utils/validate_autoware_map.py lanelet2_map.osm
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field

try:
    import lanelet2
    from lanelet2.core import ConstLineString3d, LineString3d
    from lanelet2.io import Origin
    from lanelet2.routing import RoutingGraph
    from lanelet2.traffic_rules import Locations, Participants

    from autoware_lanelet2_extension_python.projection import MGRSProjector
    from autoware_lanelet2_extension_python.regulatory_elements import AutowareTrafficLight
except ImportError as e:
    _msg = f"{e}\nPlease source /opt/ros/humble/setup.bash and ad_ros2_ws/install/setup.bash first"
    if __name__ == "__main__":
        sys.exit(_msg)
    raise ImportError(_msg) from e

# lanelet2_map_loader 支持的 format_version 主版本 (lanelet::autoware::version = format_v2)
SUPPORTED_FORMAT_VERSION = 2


@dataclass
class Result:
    """validate() 的结果，id 均为字符串"""
    format_version: str = ""
    map_version: str = ""
    n_lanelets: int = 0
    n_reg_elems: int = 0
    load: list = field(default_factory=list)           # 解析错误 / format_version 问题
    elevation: list = field(default_factory=list)      # 缺少 ele 标签的 node id
    traffic_light: list = field(default_factory=list)  # [(问题, 引用该红绿灯的 lanelet id)]
    turn_direction: dict = None                        # {lanelet id: 冲突的 lanelet / area id}；None 表示未检查


def parse_major_version(format_version):
    """同 autoware_osm_parser.cpp 的 parseMajorVersion: `1` / `1.10` / `1.10.100` -> 1，否则 None"""
    m = re.fullmatch(r"(\d+\.)?(\d+\.)?(\d+)", format_version)
    if not m:
        return None
    return int((m.group(1) or m.group(3)).rstrip("."))


def check_format_version(osm_root, res):
    """同 lanelet2_map_loader_node.cpp；loader 在 allow_unsupported_version=true 时只告警"""
    meta = osm_root.find("MetaInfo")
    if meta is not None:
        res.format_version, res.map_version = meta.get("format_version", ""), meta.get("map_version", "")
    major = parse_major_version(res.format_version)
    if major is None:
        # loader 只拦截缺失或非数字开头的版本号，这里把无法解析的 (如 "2a") 也算作问题
        res.load.append(f"map has no format_version(null) or non semver-style format_version({res.format_version})")
    elif major > SUPPORTED_FORMAT_VERSION:
        res.load.append(f"format_version({major}) of the map is larger than the supported version({SUPPORTED_FORMAT_VERSION})")


def check_elevation_tag(osm_root):
    return [node.get("id") for node in osm_root.findall("node") if node.find("tag[@k='ele']") is None]


def check_traffic_light(lanelet_map):
    # validation.cpp 逐 lanelet 检查，被多条 lanelet 引用的红绿灯会重复报告；这里去重，并记下引用它的 lanelet
    lights, users = {}, defaultdict(set)
    for lanelet in lanelet_map.laneletLayer:
        for reg_elem in lanelet.regulatoryElements:
            if isinstance(reg_elem, AutowareTrafficLight):
                lights[reg_elem.id] = reg_elem
                users[reg_elem.id].add(lanelet.id)

    issues = []
    for light_id in sorted(lights):
        light = lights[light_id]
        ids = [str(i) for i in sorted(users[light_id])]
        light_bulbs = light.lightBulbs()
        if not light_bulbs:
            issues.append((f"regulatory element traffic light {light_id} is missing optional light_bulb member. "
                           "You won't be able to use region_tlr node with this map", ids))
        for light_string in light_bulbs:
            if "traffic_light_id" not in light_string.attributes:
                issues.append((f"light_bulb {light_string.id} is missing traffic_light_id tag", ids))
        for base_string_or_poly in light.trafficLights:
            if not isinstance(base_string_or_poly, (LineString3d, ConstLineString3d)):
                issues.append((f"traffic_light {base_string_or_poly.id} is polygon, "
                               "and only linestring class is currently supported for traffic lights", ids))
            if "height" not in base_string_or_poly.attributes:
                issues.append((f"traffic_light {base_string_or_poly.id} is missing height tag", ids))
    return issues


def check_turn_direction(lanelet_map):
    traffic_rules = lanelet2.traffic_rules.create(Locations.Germany, Participants.Vehicle)
    vehicle_graph = RoutingGraph(lanelet_map, traffic_rules)

    result = {}
    for lanelet in sorted(lanelet_map.laneletLayer, key=lambda ll: ll.id):
        if not traffic_rules.canPass(lanelet):
            continue
        conflicting = vehicle_graph.conflicting(lanelet)
        if conflicting and "turn_direction" not in lanelet.attributes:
            result[str(lanelet.id)] = [str(i) for i in sorted(c.id for c in conflicting)]
    return result


def validate(map_path):
    """运行全部检查，返回 Result；地图文件无法读取时抛出 OSError / ET.ParseError"""
    osm_root = ET.parse(map_path).getroot()
    res = Result()
    check_format_version(osm_root, res)
    res.elevation = check_elevation_tag(osm_root)
    try:
        lanelet_map, errors = lanelet2.io.loadRobust(map_path, MGRSProjector(Origin(0.0, 0.0)))
    except Exception as e:  # lanelet2 的致命解析错误 (validation.cpp 中未捕获，直接崩溃)
        res.load.append(f"failed to load map: {e}")
        return res
    # errors 的第一条是标题 "Errors ocurred while parsing Lanelet Map:"，其余每条以 "\t- " 开头
    res.load += [f"parse error: {error.strip().removeprefix('- ')}" for error in errors
                 if not error.startswith("Errors ocurred")]
    res.n_lanelets, res.n_reg_elems = len(lanelet_map.laneletLayer), len(lanelet_map.regulatoryElementLayer)
    res.traffic_light = check_traffic_light(lanelet_map)
    # 解析出错时 lanelet 的边界可能不完整，在其上建路由图会段错误 (validation.cpp 同样会崩)
    if not errors:
        res.turn_direction = check_turn_direction(lanelet_map)
    return res


def main():
    parser = argparse.ArgumentParser(description="Python port of autoware_lanelet2_validation")
    parser.add_argument("map", help="path to lanelet2_map.osm")
    args = parser.parse_args()

    print("starting validation")
    try:
        res = validate(args.map)
    except (OSError, ET.ParseError) as e:
        print(f"failed to read {args.map}: {e}", file=sys.stderr)
        return 1

    if res.turn_direction is None:
        turn_direction = ["skipped: fix the load errors first, building the routing graph on an incomplete map may crash"]
    else:
        turn_direction = [f"lanelet {lid} seems to be intersecting other lanelet, but does not have turn_direction "
                          f"tagging. (conflicting: {', '.join(ids)})" for lid, ids in res.turn_direction.items()]
    sections = {
        "load": res.load,
        "elevation": [f"failed to find elevation tag for node: {nid}" for nid in res.elevation],
        "traffic_light": [msg for msg, _ in res.traffic_light],
        "turn_direction": turn_direction,
    }
    for name, issues in sections.items():
        print(f"\n== {name}")
        if name == "load":
            print(f"format_version: {res.format_version}, map_version: {res.map_version}")
            print(f"lanelets: {res.n_lanelets}, regulatory elements: {res.n_reg_elems}")
        for msg in issues:
            print(msg)

    print("\nfinished validation")
    for name, issues in sections.items():
        print(f"  {name:<15} {len(issues)} issue(s)")
    return 1 if any(sections.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
