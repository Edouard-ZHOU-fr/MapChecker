"""
从地图仓库的 git 历史生成告警打分模型 (logistic regression) 的训练数据，由 train_model.py 调用

在每个仓库地图的每个历史版本上运行检查 1 和检查 2 (阈值同 config/config.yaml)，
跟踪每条缺少 turn_direction / right_of_way 的告警之后是被修复、被忽略还是一直保留，据此标注 label。
标注规则和仓库列表见 config/dataset.yaml。dataset.yaml 中 autoware_checking 为 true 时同时运行 Autoware 检查，
只由 Autoware 检出的 turn_direction 告警也作为训练样本，此时需要先 source ROS (见 utils/validate_autoware_map.py)。

输出 (output_dir 下):
  turn_direction.csv, right_of_way.csv   每行一条告警: 仓库、key (lanelet id，right_of_way 为 "a/b")、
                                         首次 / 最后出现的版本、label (1 / 0 / 空)、标注原因、特征 (取最后出现的版本)
之后由 utils/tranning.py 训练模型。
"""

import csv
import io
import os
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout

import yaml

from utils.check_right_of_way import covered_pairs
from utils.check_turn_direction import VALID_TURN_DIRECTIONS
from utils.checks import run_core_checks
from utils.config import BASE_DIR, DEFAULT_CONFIG_PATH, load_config, resolve
from utils.context import MapContext
from utils.features import FEATURES
from utils.ignore import ignore_by_category, load_ignore
from utils.lanelet_map import load_map
from utils.printer import Printer

DEFAULT_DATASET_CONFIG = os.path.join(BASE_DIR, "config", "dataset.yaml")
CHECK_OPTS = ("turn_angle", "turn_window", "min_overlap_area", "ignore_isolated_turns", "straight_as_turn_light",
              "only_check_AOI")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def map_revisions(repo, map_rel, include_working_copy):
    """修改过地图的提交，按时间顺序；地图有未提交的修改时最后加上 None (工作区)"""
    revs = git(repo, "log", "--reverse", "--format=%h", "--", map_rel).decode().split()
    if include_working_copy and git(repo, "status", "--porcelain", "--", map_rel).strip():
        revs.append(None)
    return revs


def check_version(repo, map_rel, rev, opts, autoware):
    """
    在地图的一个版本上运行检查 (在子进程中执行)，autoware 为 True 时同时运行 Autoware 检查。返回
    (告警 {(检查项, key): 特征}, {lanelet id: turn_direction}, right_of_way 覆盖的 lanelet 对)
    """
    if rev is None:
        with open(os.path.join(repo, map_rel), "rb") as f:
            data = f.read()
    else:
        data = git(repo, "show", f"{rev}:{map_rel}")
    lanelet_map = load_map(io.BytesIO(data))
    out = Printer(False)
    with redirect_stdout(io.StringIO()):
        result = None
        if autoware:
            from utils.validate_autoware_map import validate  # 需要 ROS，main() 中已确认可以导入
            with tempfile.NamedTemporaryFile(suffix=".osm") as f:  # lanelet2 只能从文件读取地图
                f.write(data)
                f.flush()
                result = _quiet_stderr(validate, f.name)
        run_core_checks(MapContext(lanelet_map, opts), out, result)
    issues = {(i.category, "/".join(sorted(i.ids, key=int))): i.features for i in out.issues}
    return (issues, {k: ll.turn_direction for k, ll in lanelet_map.lanelets.items()},
            covered_pairs(lanelet_map.reg_elems))


def _quiet_stderr(func, *args):
    """屏蔽 C++ 库直接写到 stderr 的提示 (如 MGRS 投影的 "Projected MGRS Grid changed")，每个版本都会打印"""
    saved = os.dup(2)
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), 2)
        try:
            return func(*args)
        finally:
            os.dup2(saved, 2)
            os.close(saved)


def label_repo(name, results, ignore, min_rounds):
    """
    results: 按时间顺序的 [(版本, check_version() 的结果)]
    返回样本列表，每个样本是一个 dict
    """
    active, samples = {}, []
    fixes = {c: set() for c in FEATURES}  # 各检查项有修复的版本序号
    for n, (rev, (issues, tds, covered)) in enumerate(results):
        for k in [k for k in active if k not in issues]:
            s = active.pop(k)
            ids = s["key"].split("/")
            if any(i not in tds for i in ids):
                s["reason"] = "removed"
            elif s["category"] == "turn_direction":
                td = tds[ids[0]]
                s["reason"] = "fixed" if td in VALID_TURN_DIRECTIONS and td != s["turn_direction"] else "changed"
            else:
                s["reason"] = "fixed" if frozenset(ids) in covered else "changed"
            if s["reason"] == "fixed":
                s["label"] = 1
                fixes[s["category"]].add(n)
            samples.append(s)
        for (category, key), features in issues.items():
            s = active.setdefault((category, key), {"repo": name, "category": category, "key": key, "first": n,
                                                    "first_rev": rev, "label": "", "reason": ""})
            s.update(last_rev=rev, features=features, turn_direction=tds.get(key))

    # 保留到最新版本的告警
    for s in active.values():
        if any(i in ignore[s["category"]] for i in s["key"].split("/")):
            s.update(label=0, reason="ignored")
        elif sum(i > s["first"] for i in fixes[s["category"]]) >= min_rounds:
            s.update(label=0, reason="survived")
        else:
            s["reason"] = "pending"
        samples.append(s)
    return samples


def write_csv(path, category, samples):
    names = FEATURES[category]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["repo", "key", "first_rev", "last_rev", "label", "reason", *names])
        for s in samples:
            if s["category"] == category:
                w.writerow([s["repo"], s["key"], s["first_rev"] or "working copy", s["last_rev"] or "working copy",
                            s["label"], s["reason"], *(round(s["features"][x], 4) for x in names)])


def load_dataset_config(path):
    """读取 dataset.yaml，返回 (配置, 训练数据的输出目录)"""
    with open(path, encoding="utf-8") as f:
        ds = yaml.safe_load(f) or {}
    return ds, resolve(ds.get("output_dir", "data"))


def build_dataset(config_path=DEFAULT_DATASET_CONFIG):
    """按 dataset.yaml 生成训练数据，返回输出目录；需要 ROS 但无法导入时抛出 RuntimeError"""
    ds, out_dir = load_dataset_config(config_path)
    cfg = load_config(DEFAULT_CONFIG_PATH)
    opts = {k: cfg[k] for k in CHECK_OPTS}
    map_rel = ds.get("map", "lanelet2_map.osm")
    autoware = ds.get("autoware_checking", False)
    if autoware:
        try:
            import utils.validate_autoware_map  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"autoware_checking in {config_path} requires ROS ({e})") from e

    repos = []  # (名称, 路径, 版本列表, 忽略列表)
    for r in ds["repos"]:
        path = resolve(r["path"])
        if not os.path.isdir(os.path.join(path, ".git")):
            print(f"{path}: not a git repository, skipped")
            continue
        ignore = ignore_by_category(load_ignore(resolve(r["ignore_file"])) if r.get("ignore_file") else [])
        repos.append((os.path.basename(path), path, map_revisions(path, map_rel, ds.get("include_working_copy", True)),
                      ignore))
    print(f"Replaying {sum(len(r[2]) for r in repos)} map versions of {len(repos)} repositories ...")

    samples = []
    with ProcessPoolExecutor() as pool:
        futures = {(name, rev): pool.submit(check_version, path, map_rel, rev, opts, autoware)
                   for name, path, revs, _ in repos for rev in revs}
        for name, path, revs, ignore in repos:
            results = []
            for rev in revs:
                try:
                    results.append((rev, futures[(name, rev)].result()))
                except Exception as e:  # 历史版本可能是空文件、合并冲突等，跳过
                    print(f"  {name} {rev}: skipped ({type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''})")
            repo_samples = label_repo(name, results, ignore, ds.get("min_fix_rounds", 2))
            samples += repo_samples
            for category in FEATURES:
                c = Counter(s["reason"] for s in repo_samples if s["category"] == category)
                print(f"{name:36s} {len(results):3d} versions  {category:15s} "
                      + "  ".join(f"{k} {c[k]:4d}" for k in ("fixed", "ignored", "survived", "pending", "changed", "removed")))

    os.makedirs(out_dir, exist_ok=True)
    for category in FEATURES:
        path = os.path.join(out_dir, f"{category}.csv")
        write_csv(path, category, samples)
        labels = Counter(s["label"] for s in samples if s["category"] == category)
        print(f"{category}: {labels[1]} label 1, {labels[0]} label 0, {labels['']} unlabeled -> {path}")
    return out_dir
