#!/usr/bin/env python3
"""
Autoware Lanelet2 map check (Python version of autoware_lanelet2_validation)

Ported from autoware_lanelet2_extension/src/validation.cpp, with the same checks and criteria:
  1. elevation:      every <node> has an ele tag
  2. traffic_light:  the AutowareTrafficLight referenced by a lanelet has light_bulbs, light_bulb has traffic_light_id,
                     traffic_light is a linestring and has a height tag
  3. turn_direction: lanelets passable by vehicles that conflict with other lanelets / areas in the routing graph
                     must have turn_direction
Additionally, a check that validation.cpp does not do but lanelet2_map_loader does:
  0. load:           Lanelet2 parse errors (collected then discarded by validation.cpp) and MetaInfo format_version

Like validation.cpp, projects with MGRSProjector and builds the routing graph with Germany / Vehicle traffic rules.
Returns 1 when issues are found (validation.cpp always returns 0), usable in CI.
check_lanelet_map.py calls validate() when autoware_checking is true and merges the result into its report.

Unlike check_lanelet_map.py, this script depends on lanelet2 and autoware_lanelet2_extension_python from the ROS environment:
  source /opt/ros/humble/setup.bash
  source ~/ros/ad_ros2_ws/install/setup.bash

Usage:
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

# Major format_version supported by lanelet2_map_loader (lanelet::autoware::version = format_v2)
SUPPORTED_FORMAT_VERSION = 2


@dataclass
class Result:
    """Result of validate(), all ids are strings"""
    format_version: str = ""
    map_version: str = ""
    n_lanelets: int = 0
    n_reg_elems: int = 0
    load: list = field(default_factory=list)           # parse errors / format_version issues
    elevation: list = field(default_factory=list)      # ids of nodes missing the ele tag
    traffic_light: list = field(default_factory=list)  # [(issue, ids of lanelets referencing this traffic light)]
    turn_direction: dict = None                        # {lanelet id: conflicting lanelet / area ids}; None means not checked


def parse_major_version(format_version):
    """Same as parseMajorVersion in autoware_osm_parser.cpp: `1` / `1.10` / `1.10.100` -> 1, otherwise None"""
    m = re.fullmatch(r"(\d+\.)?(\d+\.)?(\d+)", format_version)
    if not m:
        return None
    return int((m.group(1) or m.group(3)).rstrip("."))


def check_format_version(osm_root, res):
    """Same as lanelet2_map_loader_node.cpp; the loader only warns when allow_unsupported_version=true"""
    meta = osm_root.find("MetaInfo")
    if meta is not None:
        res.format_version, res.map_version = meta.get("format_version", ""), meta.get("map_version", "")
    major = parse_major_version(res.format_version)
    if major is None:
        # The loader only rejects missing versions or ones not starting with a digit; unparsable ones (e.g. "2a") count here too
        res.load.append(f"map has no format_version(null) or non semver-style format_version({res.format_version})")
    elif major > SUPPORTED_FORMAT_VERSION:
        res.load.append(f"format_version({major}) of the map is larger than the supported version({SUPPORTED_FORMAT_VERSION})")


def check_elevation_tag(osm_root):
    return [node.get("id") for node in osm_root.findall("node") if node.find("tag[@k='ele']") is None]


def check_traffic_light(lanelet_map):
    # validation.cpp checks lanelet by lanelet, so a traffic light referenced by several lanelets is reported repeatedly;
    # here it is deduplicated, and the lanelets referencing it are recorded
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
    """Run all checks and return a Result; raise OSError / ET.ParseError if the map file cannot be read"""
    osm_root = ET.parse(map_path).getroot()
    res = Result()
    check_format_version(osm_root, res)
    res.elevation = check_elevation_tag(osm_root)
    try:
        lanelet_map, errors = lanelet2.io.loadRobust(map_path, MGRSProjector(Origin(0.0, 0.0)))
    except Exception as e:  # fatal lanelet2 parse error (not caught in validation.cpp, which simply crashes)
        res.load.append(f"failed to load map: {e}")
        return res
    # The first entry of errors is the title "Errors ocurred while parsing Lanelet Map:", each other one starts with "\t- "
    res.load += [f"parse error: {error.strip().removeprefix('- ')}" for error in errors
                 if not error.startswith("Errors ocurred")]
    res.n_lanelets, res.n_reg_elems = len(lanelet_map.laneletLayer), len(lanelet_map.regulatoryElementLayer)
    res.traffic_light = check_traffic_light(lanelet_map)
    # With parse errors the lanelet bounds may be incomplete, building a routing graph on them segfaults (validation.cpp too)
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
