"""读取 config.yaml"""

import os

import yaml

# config.yaml 中缺省项的默认值
DEFAULT_CONFIG = {
    "input": "lanelet2_map.osm",
    "log": {"enabled": False, "dir": "logs"},
    "turn_angle": 30.0,
    "turn_window": 30.0,
    "min_overlap_area": 1.0,
    "ignore_isolated_turns": False,
    "straight_as_turn_light": True,
    "ignore_file": "config/ignore.yaml",
    "only_check_AOI": False,
    "autoware_checking": False,
}


def load_config(path, base_dir):
    """读取 config.yaml，缺省项用 DEFAULT_CONFIG 补全；相对路径以 base_dir 为基准"""
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = {**DEFAULT_CONFIG, **user}
    cfg["log"] = {**DEFAULT_CONFIG["log"], **(user.get("log") or {})}

    def resolve(p):
        return os.path.join(base_dir, os.path.expanduser(str(p)))

    cfg["input"] = resolve(cfg["input"])
    cfg["log"]["dir"] = resolve(cfg["log"]["dir"])
    cfg["ignore_file"] = resolve(cfg["ignore_file"])
    return cfg
