"""Read config.yaml"""

import os

import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # MapChecker/, base for relative paths in the config
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")

# Default values for items missing from config.yaml
DEFAULT_CONFIG = {
    "input": "lanelet2_map.osm",
    "log": {"enabled": False, "dir": "logs"},
    "heatmap": {"enabled": False, "radius": 30.0, "top": 10},
    "turn_angle": 30.0,
    "turn_window": 30.0,
    "min_overlap_area": 1.0,
    "parallel_min_length": 10.0,
    "parallel_min_gap": 1.5,
    "ignore_isolated_turns": False,
    "straight_as_turn_light": True,
    "ignore_file": "config/ignore.yaml",
    "model_file": "config/model.yaml",
    "only_check_AOI": False,
    "autoware_checking": False,
    "Using_LR": False,
    "map_min_p": 0.0,
    "isShow": False,
}


def resolve(path, base_dir=BASE_DIR):
    """Path in the config: expand ~, relative paths are based on base_dir"""
    return os.path.join(base_dir, os.path.expanduser(str(path)))


def load_config(path, base_dir=BASE_DIR):
    """Read config.yaml, fill in missing items from DEFAULT_CONFIG; relative paths are based on base_dir"""
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = {**DEFAULT_CONFIG, **user}
    cfg["log"] = {**DEFAULT_CONFIG["log"], **(user.get("log") or {})}
    cfg["heatmap"] = {**DEFAULT_CONFIG["heatmap"], **(user.get("heatmap") or {})}

    cfg["input"] = resolve(cfg["input"], base_dir)
    cfg["log"]["dir"] = resolve(cfg["log"]["dir"], base_dir)
    cfg["ignore_file"] = resolve(cfg["ignore_file"], base_dir)
    cfg["model_file"] = resolve(cfg["model_file"], base_dir) if cfg["model_file"] else None
    return cfg
