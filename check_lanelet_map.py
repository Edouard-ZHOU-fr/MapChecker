#!/usr/bin/env python3
"""
Lanelet2 地图检查工具

检查项:
  1. 转向灯 (turn_direction): 弯道 / 转弯，以及与其他 lane 相交的直行 lanelet 是否设置了 turn_direction
  2. 路权 (right_of_way): 交叉 / 汇入的 lanelet 是否设置了 right_of_way；
     已有 right_of_way 是否被优先 lanelet 引用、两条 lanelet 是否真的重叠 (完全不重叠时输出红色 ERROR)
  3. Autoware (autoware_checking 为 true 时): 运行 utils/validate_autoware_map.py 并合并结果，
     其 turn_direction 结果合并到检查 1 (同一 lanelet 只报告一次)，其余 (load / elevation / traffic_light) 在检查 3 中报告
  扩展检查 (ext/<名称>/): 其 config/config.yaml 中 enable 为 true 时在检查 1~3 之后运行；
     --ext / --only-ext / --no-ext 选择要运行的扩展，--list-ext 列出所有扩展。编写新的扩展见 ext/README.md
各参数的含义见 config/config.yaml。只依赖 numpy 和 PyYAML；只有 autoware_checking 为 true 时才需要 source ROS，
只有 heatmap.enabled 为 true 时才需要 matplotlib。

Using_LR 为 true 时，用 model_file (train_model.py 生成的 logistic regression 模型) 给检查 1 和检查 2 中
缺少 turn_direction / right_of_way 的 WARNING 打分 [WARNING p=0.93]，p 为该告警是真问题 (之后会被修复) 的概率，
各检查中这些 WARNING 按 p 从大到小输出 (检查 2 先列出所有区域，每条注明所在区域)，没有分数的 WARNING / ERROR 在它们之前。
打分只用模型中的数字，不需要 scikit-learn。详见 README.md。

heatmap.enabled 为 true 时，在日志末尾列出问题频发的区域 (Hotspots)，并在日志目录生成同名的热力图 .png:
地图上按问题密度从蓝 (少) 到红 (多) 着色，并圈出这些区域；isShow 为 true 时，报告结束后弹出热力图窗口。
扩展检查也可以在 pyplot 中创建窗口 (如 altitude 的 isShow)，与热力图窗口一起弹出。
Using_LR 为 true 时热力图和区域按 p 加权 (有分数的 WARNING 计 p 条，其余计 1 条)，即按预计的真问题数排序。

用法:
  python3 check_lanelet_map.py                      # 使用 config/config.yaml
  python3 check_lanelet_map.py -c other_config.yaml
  python3 check_lanelet_map.py some_map.osm --turn-angle 45   # 命令行参数优先于 config.yaml
  python3 check_lanelet_map.py --only-ext crosswalk --set crosswalk.max_extension=1.0   # 只运行扩展 crosswalk

目录结构:
  MapChecker/
    check_lanelet_map.py   入口
    config/config.yaml     配置 (其中的相对路径以 MapChecker/ 为基准)
    config/ignore.yaml     手动忽略的 lanelet
    config/model.yaml      告警打分模型 (train_model.py 生成)
    utils/                 地图读取、几何、拓扑、各项检查、输出、特征与模型、训练数据与训练
    ext/                   扩展检查，每项有自己的 script/、config/ 和 README.md；ext/_template/ 为新扩展的模板
    train_model.py         从地图仓库的 git 历史生成训练数据并训练 logistic regression，生成 config/model.yaml
    libs/                  install.sh 安装的第三方库
"""

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# install.sh 把第三方库装在 libs/ 中，放在最前面，优先于系统和用户目录中的版本
sys.path.insert(0, os.path.join(BASE_DIR, "libs"))
# 系统 python3-matplotlib 的 matplotlib-*-nspkg.pth 在 Python 启动时就把系统的 mpl_toolkits 放进了 sys.modules，
# libs/ 中的 matplotlib 会导入系统旧版的 mpl_toolkits.mplot3d 而失败 (警告 "Unable to import Axes3D")，让 libs/ 中的优先
if "mpl_toolkits" in sys.modules:
    sys.modules["mpl_toolkits"].__path__.insert(0, os.path.join(BASE_DIR, "libs", "mpl_toolkits"))

import yaml

from utils import ext as extmod
from utils.checks import run_core_checks
from utils.config import DEFAULT_CONFIG_PATH, load_config
from utils.context import MapContext
from utils.heatmap import report_hotspots, show_windows
from utils.ignore import ignore_by_category, load_ignore, report_ignored
from utils.model import Model
from utils.printer import Printer


def run(map_path, opts, ignore_entries, ignore_path, out, core=True, validate_autoware=None, extensions=(),
        heatmap=None, output_prefix=None):
    """
    core:              是否运行检查 1~3 (--only-ext 时为 False)
    validate_autoware: utils/validate_autoware_map.py 的 validate()，None 表示不运行检查 3
    extensions:        要运行的扩展检查 (utils/ext.py 中 load() 过的 Extension)
    heatmap:           {"path", "radius", "top", "show"}，None 表示不生成热力图
    output_prefix:     日志 / 热力图的路径前缀 (不含扩展名)，扩展检查生成的文件 (如 altitude 的趋势图) 也用它命名
    """
    ctx = MapContext.from_file(map_path, opts, output_prefix)
    out.info(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    out.info(f"Map: {os.path.abspath(map_path)}")
    out.info(f"Lanelets: {len(ctx.lanelets)}, regulatory elements: {len(ctx.reg_elems)}")
    if ctx.map.skipped:
        out.info("Lanelets not loaded (incomplete, not checked): "
                 + "; ".join(f"{lid} ({reason})" for lid, reason in ctx.map.skipped))
    if ctx.only_check_aoi and not ctx.aois:
        out.info("AOI: only_check_AOI is enabled but no AOI found in map, no check performed")
        return 0

    autoware = None
    if core:
        # Autoware 检查先运行，turn_direction 结果在检查 1 中与几何检查合并
        autoware = validate_autoware(map_path) if validate_autoware else None
        run_core_checks(ctx, out, autoware)
    extmod.run_extensions(ctx, extensions, out)

    out.header("Summary")
    if core:
        out.info(f"turn_direction warnings: {out.count['turn_direction']}")
        out.info(f"right_of_way warnings:   {out.count['right_of_way']}")
        out.info(f"right_of_way errors:     {out.errors['right_of_way']}")
        if autoware:
            out.info(f"autoware warnings:       {out.count['autoware']}")
            out.info(f"autoware errors:         {out.errors['autoware']}")
    for ext in extensions:
        out.info(f"{ext.name + ' warnings:':<24} {out.count[ext.name]}")
        if out.errors[ext.name]:
            out.info(f"{ext.name + ' errors:':<24} {out.errors[ext.name]}")
    scores = [i.p for i in out.issues if i.p is not None]
    if scores:
        out.info(f"scored warnings:         {len(scores)}, {sum(p < 0.5 for p in scores)} of them with p < 0.5 "
                 f"(likely false positives)")
    if out.total == 0:
        out.ok("No issue found")

    if heatmap:
        report_hotspots(ctx.lanelets, ctx.aois if ctx.only_check_aoi else [], out, heatmap["path"],
                        heatmap["radius"], heatmap["top"], os.path.basename(map_path), heatmap["show"])
    report_ignored(ignore_entries, ignore_path, ctx, out)
    return out.total


def _names(text):
    return [n.strip() for n in text.split(",") if n.strip()]


def parse_args():
    parser = argparse.ArgumentParser(description="Check turn_direction and right_of_way in a Lanelet2 map, "
                                                 "plus the extension checks in ext/")
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
                        help="only check lanelets fully inside the AOI of the map, no check is performed if the "
                             "map has no AOI (overrides config)")
    parser.add_argument("--autoware-checking", action=argparse.BooleanOptionalAction, default=None,
                        help="also run utils/validate_autoware_map.py and merge its results, requires ROS "
                             "(overrides config)")
    parser.add_argument("--ignore-file", help="file listing lanelets to ignore (overrides config)")
    parser.add_argument("--using-lr", action=argparse.BooleanOptionalAction, default=None,
                        help="score warnings with the logistic regression model (overrides Using_LR in config)")
    parser.add_argument("--model-file", help="model generated by train_model.py, used when Using_LR is true "
                                             "(overrides config)")
    parser.add_argument("--heatmap", action=argparse.BooleanOptionalAction, default=None,
                        help="list the areas with the most issues and save a heatmap image next to the log, "
                             "requires matplotlib (overrides heatmap.enabled in config)")
    parser.add_argument("--show-heatmap", action=argparse.BooleanOptionalAction, default=None,
                        help="pop up the heatmap in a window after the report, only when the heatmap is generated "
                             "(overrides isShow in config)")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")

    group = parser.add_argument_group("extension checks (ext/<NAME>/, see ext/README.md)")
    which = group.add_mutually_exclusive_group()
    which.add_argument("--ext", type=_names, metavar="NAME[,NAME...]",
                       help="run these extensions instead of the enabled ones (regardless of 'enable')")
    which.add_argument("--only-ext", type=_names, metavar="NAME[,NAME...]",
                       help="run only these extensions (regardless of 'enable'), skip checks 1~3")
    which.add_argument("--no-ext", action="store_true", help="run no extension")
    group.add_argument("--set", action="append", metavar="NAME.KEY=VALUE",
                       help="override a parameter in the config of an extension, e.g. crosswalk.max_extension=1.0 "
                            "(repeatable)")
    group.add_argument("--list-ext", action="store_true", help="list the extensions and exit")
    return parser, parser.parse_args()


def list_extensions(available):
    if not available:
        print(f"No extension in {extmod.EXT_DIR}")
    for ext in available.values():
        status = "enabled" if ext.enabled else "template" if ext.name.startswith("_") else "disabled"
        print(f"{ext.name:20s} {status:9s} {ext.title}")


def main():
    parser, args = parse_args()
    try:
        available = extmod.discover()
    except (OSError, ValueError, yaml.YAMLError) as e:
        parser.error(f"invalid extension: {e}")
    if args.list_ext:
        list_extensions(available)
        return 0

    config_path = args.config or DEFAULT_CONFIG_PATH
    if not os.path.isfile(config_path):
        parser.error(f"config file not found: {config_path}")
    cfg = load_config(config_path)
    map_path = args.map or cfg["input"]
    if not os.path.isfile(map_path):
        parser.error(f"map file not found: {map_path}")
    ignore_path = os.path.abspath(args.ignore_file or cfg["ignore_file"])
    if args.ignore_file and not os.path.isfile(ignore_path):
        parser.error(f"ignore file not found: {args.ignore_file}")
    try:
        # ignore.yaml 中除了 all 和检查 1~3，也可以按扩展检查的名称写忽略列表
        ignore_entries = load_ignore(ignore_path, available)
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
        "autoware_checking": args.autoware_checking,
        "Using_LR": args.using_lr,
    }
    opts = {k: cfg[k] if v is None else v for k, v in cli.items()}
    core = not args.only_ext

    # 扩展检查: 默认运行 enable 为 true 的；配置中的 ignore 以扩展检查的名称为范围
    try:
        extensions = extmod.select(available, [] if args.no_ext else args.only_ext or args.ext)
        overrides = extmod.parse_overrides(args.set)
        not_run = [n for n in overrides if n not in {ext.name for ext in extensions}]
        if not_run:
            raise ValueError(f"--set: extension {', '.join(not_run)} is not run (enable it, or use --ext)")
        for ext in extensions:
            extmod.load(ext, overrides.get(ext.name))
    except (OSError, ValueError, yaml.YAMLError) as e:
        parser.error(f"invalid extension: {e}")
    ignore_entries += [(ext.name, lid, reason) for ext in extensions for lid, reason in ext.cfg["ignore"].items()]

    model = None
    if core and opts["Using_LR"]:
        model_path = os.path.abspath(args.model_file) if args.model_file else cfg["model_file"]
        if not model_path or not os.path.isfile(model_path):
            parser.error(f"Using_LR requires a model file (not found: {model_path}), "
                         f"run train_model.py first, or run with --no-using-lr")
        try:
            model = Model(model_path)
        except (ValueError, KeyError, yaml.YAMLError) as e:
            parser.error(f"invalid model file: {e}")

    validate_autoware = None
    if core and opts["autoware_checking"]:
        try:
            from utils.validate_autoware_map import validate as validate_autoware
        except ImportError as e:
            parser.error(f"autoware_checking requires ROS ({e}), or run with --no-autoware-checking")

    heatmap = None
    if cfg["heatmap"]["enabled"] if args.heatmap is None else args.heatmap:
        try:
            import matplotlib  # noqa: F401
        except ImportError as e:
            parser.error(f"heatmap requires matplotlib ({e}), or run with --no-heatmap")
        heatmap = {"radius": float(cfg["heatmap"]["radius"]), "top": int(cfg["heatmap"]["top"]),
                   "show": bool(cfg["isShow"] if args.show_heatmap is None else args.show_heatmap)}

    # 日志、热力图和扩展检查生成的文件同名，放在日志目录
    stamp = os.path.join(cfg["log"]["dir"], time.strftime("map_check_%Y%m%d_%H%M%S"))
    if cfg["log"]["enabled"] or heatmap:
        os.makedirs(cfg["log"]["dir"], exist_ok=True)
    if heatmap:
        heatmap["path"] = stamp + ".png"
    log_file = log_path = None
    if cfg["log"]["enabled"]:
        log_path = stamp + ".log"
        log_file = open(log_path, "w", encoding="utf-8")

    # 运行的检查项；忽略列表中其他检查项的条目在报告末尾标为 check not run
    categories = (("turn_direction", "right_of_way") + (("autoware",) if validate_autoware else ()) if core else ()) \
        + tuple(ext.name for ext in extensions)
    out = Printer(not args.no_color and sys.stdout.isatty(), log_file, ignore_by_category(ignore_entries, categories),
                  model)
    try:
        total = run(map_path, opts, ignore_entries, ignore_path, out, core, validate_autoware, extensions, heatmap,
                    stamp)
    finally:
        if log_file:
            log_file.close()
            print(f"\nLog saved to: {log_path}")
        if heatmap and os.path.isfile(heatmap["path"]):
            print(f"Heatmap saved to: {heatmap['path']}")
    # 热力图 (isShow) 和扩展检查创建的窗口 (如 altitude 的 isShow) 在报告结束后一起弹出
    show_windows()
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
