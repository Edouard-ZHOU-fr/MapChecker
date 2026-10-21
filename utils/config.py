"""读取 config.yaml"""

import os

import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # MapChecker/，配置中的相对路径以它为基准
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")

# config.yaml 中缺省项的默认值
DEFAULT_CONFIG = {
    "input": "lanelet2_map.osm",
    "log": {"enabled": False, "dir": "logs"},
    "heatmap": {"enabled": False, "radius": 30.0, "top": 10},
    "turn_angle": 30.0,
    "turn_window": 30.0,
    "min_overlap_area": 1.0,
    "ignore_isolated_turns": False,
    "straight_as_turn_light": True,
    "ignore_file": "config/ignore.yaml",
    "model_file": "config/model.yaml",
    "only_check_AOI": False,
    "autoware_checking": False,
    "Using_LR": False,
    "isShow": False,
}


def resolve(path, base_dir=BASE_DIR):
    """配置中的路径: 展开 ~，相对路径以 base_dir 为基准"""
    return os.path.join(base_dir, os.path.expanduser(str(path)))


def load_config(path, base_dir=BASE_DIR):
    """读取 config.yaml，缺省项用 DEFAULT_CONFIG 补全；相对路径以 base_dir 为基准"""
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
