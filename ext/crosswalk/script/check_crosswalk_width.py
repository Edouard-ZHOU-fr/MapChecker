#!/usr/bin/env python3
"""
扩展检查 crosswalk: crosswalk 在横向上最多超出道路多少，超出过多时报 WARNING

crosswalk lanelet 的左右边界沿行人行走的方向，即横穿道路的方向。测量 crosswalk 每一端超出道路
(与它重叠的所有车辆 lanelet 的并集) 的最大长度，大于 max_extension 时报 WARNING，其余只输出测量值。

说明见 ext/crosswalk/README.md，参数见 ext/crosswalk/config/config.yaml。
config.yaml 中 enable 为 true 时由 check_lanelet_map.py 运行 (见 utils/ext.py)，也可以单独运行:
  python3 ext/crosswalk/script/check_crosswalk_width.py                     # 地图为 config/config.yaml 中的 input
  python3 ext/crosswalk/script/check_crosswalk_width.py some_map.osm --max-extension 1.0
"""

import argparse
import math
import os
import sys
import time

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ext/crosswalk/
BASE_DIR = os.path.dirname(os.path.dirname(MODULE_DIR))                    # MapChecker/
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
    # install.sh 把第三方库装在 libs/ 中，放在最前面，优先于系统和用户目录中的版本
    sys.path.insert(0, os.path.join(BASE_DIR, "libs"))

import numpy as np
import yaml

from utils.config import load_config as load_main_config
from utils.geometry import arc_length, fmt_xy, lanelets_in_aoi, points_in_polygon, resample_by_ratio, resample_by_step
from utils.ignore import load_ignore, report_ignored
from utils.lanelet_map import by_id, load_map
from utils.printer import Printer

CATEGORY = "crosswalk"
DEFAULT_CONFIG_PATH = os.path.join(MODULE_DIR, "config", "config.yaml")
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
# config.yaml 中缺省项的默认值
DEFAULT_CONFIG = {
    "enable": False,
    "log": {"enabled": False, "dir": "logs"},
    "max_extension": 1.0,
    "ignore": [],
}

LINE_SPACING = 0.1   # 沿 crosswalk 宽度方向取线的间距 (米)
STEP = 0.01         # 沿线采样的间距 (米)，即测量精度


def load_config(path):
    """读取配置，缺省项用 DEFAULT_CONFIG 补全；ignore 转成 {lanelet id: 原因}，log.dir 以 MapChecker/ 为基准"""
    with open(path, encoding="utf-8") as f:
        user = yaml.safe_load(f) or {}
    cfg = {**DEFAULT_CONFIG, **user}
    cfg["log"] = {**DEFAULT_CONFIG["log"], **(user.get("log") or {})}
    cfg["log"]["dir"] = os.path.join(BASE_DIR, os.path.expanduser(str(cfg["log"]["dir"])))
    cfg["max_extension"] = float(cfg["max_extension"])
    ignore = {}
    for item in cfg["ignore"] or []:
        lid, reason = next(iter(item.items())) if isinstance(item, dict) and len(item) == 1 else (item, "")
        if isinstance(lid, bool) or not str(lid).isdigit():
            raise ValueError(f"{path}: invalid lanelet id '{lid}' in 'ignore'")
        ignore[str(lid)] = ignore.get(str(lid)) or ("" if reason is None else str(reason))
    cfg["ignore"] = ignore
    return cfg


def axis_lines(cw):
    """在 crosswalk 宽度方向上每隔约 LINE_SPACING 米取一条平行于左右边界的线，方向与边界相同"""
    ratios = np.linspace(0.0, 1.0, 50)
    lb, rb = resample_by_ratio(cw.left, ratios), resample_by_ratio(cw.right, ratios)
    if arc_length((lb + rb) / 2.0)[-1] < STEP:
        return []
    n = max(1, math.ceil(np.linalg.norm(lb - rb, axis=1).max() / LINE_SPACING))
    return [lb + t * (rb - lb) for t in (np.arange(n) + 0.5) / n]


def end_extensions(cw, lanes):
    """
    crosswalk 两端 (起点端, 终点端) 超出道路 (lanes 的并集) 的最大长度，沿 crosswalk 方向测量，
    每端为 (长度, 道路边缘的车道 id, 端点是否在道路内)；crosswalk 不与任何车道重叠时返回 None
    """
    best = [None, None]
    for line in axis_lines(cw):
        pts = resample_by_step(line, STEP)
        s = arc_length(pts)
        inside = np.array([points_in_polygon(pts, ll.polygon) for ll in lanes]).reshape(len(lanes), len(pts))
        hit = np.nonzero(inside.any(axis=0))[0]
        if not len(hit):
            continue
        half = (s[1] - s[0]) / 2.0  # 道路边界取在相邻采样点的中间
        for side, k in ((0, hit[0]), (1, hit[-1])):
            at_end = k == (0, len(pts) - 1)[side]
            ext = 0.0 if at_end else (s[k] - half if side == 0 else s[-1] - s[k] - half)
            if best[side] is None or ext > best[side][0]:
                best[side] = (ext, lanes[np.nonzero(inside[:, k])[0][0]].id, at_end)
    return None if best[0] is None else best


def check(lanelets, reg_elems, aois, cfg, out):
    """
    输出每个 crosswalk 两端超出道路的最大长度，超过 cfg["max_extension"] 时报 WARNING。
    aois: 只检查完全位于其中的 crosswalk；None 表示检查整张地图 (道路始终用整张地图的车道)
    """
    max_ext = cfg["max_extension"]
    out.header(f"Ext crosswalk: how far crosswalks extend beyond the road (max {max_ext:.2f} m)")
    crosswalks = {k: v for k, v in lanelets.items() if v.subtype == "crosswalk"}
    if aois is not None:
        inside = lanelets_in_aoi(crosswalks, aois)
        out.info(f"AOI: {len(inside)} of {len(crosswalks)} crosswalks fully inside (only these are checked)")
        crosswalks = {k: v for k, v in crosswalks.items() if k in inside}
    vehicle = sorted((ll for ll in lanelets.values() if ll.is_vehicle), key=by_id)
    bbox = np.array([np.r_[ll.polygon.min(axis=0), ll.polygon.max(axis=0)] for ll in vehicle]).reshape(-1, 4)

    not_crossing = []  # 不与任何车道重叠的 crosswalk
    for cw in sorted(crosswalks.values(), key=by_id):
        lo, hi = cw.polygon.min(axis=0), cw.polygon.max(axis=0)
        cand = np.nonzero((bbox[:, 0] <= hi[0]) & (bbox[:, 2] >= lo[0])
                          & (bbox[:, 1] <= hi[1]) & (bbox[:, 3] >= lo[1]))[0]
        exts = end_extensions(cw, [vehicle[j] for j in cand])
        if exts is None:
            not_crossing.append(cw.id)
            continue
        ends = ((cw.left[0] + cw.right[0]) / 2.0, (cw.left[-1] + cw.right[-1]) / 2.0)
        detail = ", ".join(f"end {fmt_xy(pos)}: " + (f"inside lanelet {lane}" if inside_road else f"{ext:.2f} m beyond lanelet {lane}")
                           for (ext, lane, inside_road), pos in zip(exts, ends))
        worst = max((0, 1), key=lambda side: exts[side][0])
        if exts[worst][0] > max_ext:
            out.warn(CATEGORY, f"crosswalk {cw.id}: extends up to {exts[worst][0]:.2f} m beyond the road "
                               f"(max {max_ext:.2f} m) | {detail}", (cw.id,), pos=ends[worst])
        else:
            out.info(f"crosswalk {cw.id}: extends up to {exts[worst][0]:.2f} m beyond the road | {detail}")

    n_ignored = sum(n for (category, _), n in out.suppressed.items() if category == CATEGORY)
    out.info(f"{len(crosswalks)} crosswalks: {out.count[CATEGORY]} extend more than {max_ext:.2f} m beyond the road"
             + (f", {n_ignored} ignored" if n_ignored else ""))
    if not_crossing:
        out.info(f"crosswalks overlapping no vehicle lanelet (not checked): {' '.join(not_crossing)}")
    if out.count[CATEGORY] == 0:
        out.ok(f"No crosswalk extends more than {max_ext:.2f} m beyond the road")


def parse_args():
    parser = argparse.ArgumentParser(description="Check how far crosswalks extend laterally beyond the road")
    parser.add_argument("map", nargs="?", help="path to lanelet2_map.osm (overrides 'input' in config/config.yaml)")
    parser.add_argument("-c", "--config", help="config file, default: ext/crosswalk/config/config.yaml")
    parser.add_argument("--max-extension", type=float,
                        help="max length (m) by which each end of a crosswalk may extend beyond the road "
                             "(overrides config)")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    return parser, parser.parse_args()


def main():
    """单独运行: 地图、忽略列表和 only_check_AOI 使用 config/config.yaml 中的设置，不看 enable"""
    parser, args = parse_args()

    config_path = args.config or DEFAULT_CONFIG_PATH
    if not os.path.isfile(config_path):
        parser.error(f"config file not found: {config_path}")
    try:
        cfg = load_config(config_path)
        main_cfg = load_main_config(MAIN_CONFIG_PATH, BASE_DIR)
        # 本检查自己的 ignore 列表，加上 config/ignore.yaml 中对所有检查都生效的 all
        entries = [e for e in load_ignore(main_cfg["ignore_file"]) if e[0] == "all"]
    except (ValueError, yaml.YAMLError) as e:
        parser.error(str(e))
    entries += [(CATEGORY, lid, reason) for lid, reason in cfg["ignore"].items()]
    if args.max_extension is not None:
        cfg["max_extension"] = args.max_extension
    map_path = args.map or main_cfg["input"]
    if not os.path.isfile(map_path):
        parser.error(f"map file not found: {map_path}")

    log_file = log_path = None
    if cfg["log"]["enabled"]:
        os.makedirs(cfg["log"]["dir"], exist_ok=True)
        log_path = os.path.join(cfg["log"]["dir"], time.strftime("crosswalk_check_%Y%m%d_%H%M%S.log"))
        log_file = open(log_path, "w", encoding="utf-8")

    out = Printer(not args.no_color and sys.stdout.isatty(), log_file,
                  {CATEGORY: {lid: reason for _, lid, reason in entries}})
    try:
        lanelets, reg_elems, aois = load_map(map_path)
        out.info(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        out.info(f"Map: {os.path.abspath(map_path)}")
        if main_cfg["only_check_AOI"] and not aois:
            out.info("AOI: only_check_AOI is enabled but no AOI found in map, no check performed")
        else:
            check(lanelets, reg_elems, aois if main_cfg["only_check_AOI"] else None, cfg, out)
            report_ignored(entries, main_cfg["ignore_file"], lanelets, None, out)
    finally:
        if log_file:
            log_file.close()
            print(f"\nLog saved to: {log_path}")
    return 1 if out.total else 0


if __name__ == "__main__":
    sys.exit(main())
