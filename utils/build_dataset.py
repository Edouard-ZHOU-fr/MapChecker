"""
Build the training data of the warning scoring model (logistic regression) from the git history of map repositories,
called by train_model.py

Runs Check 1 and Check 2 (same thresholds as config/config.yaml) on every historical revision of each repository's map,
tracks whether each warning about missing turn_direction / right_of_way is later fixed, ignored or kept all along,
and labels it accordingly. See config/dataset.yaml for the labeling rules and the list of repositories.
When autoware_checking is true in dataset.yaml, the Autoware check is also run, and turn_direction warnings found only
by Autoware are also used as training samples; ROS must then be sourced first (see utils/validate_autoware_map.py).

Output (under output_dir):
  turn_direction.csv, right_of_way.csv   one warning per row: repository, key (lanelet id, "a/b" for right_of_way),
                                         first / last revision it appears in, label (1 / 0 / empty), labeling reason,
                                         features (taken from the last revision it appears in)
The model is then trained by utils/tranning.py.
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
CHECK_OPTS = ("turn_angle", "turn_window", "min_overlap_area", "parallel_min_length", "parallel_min_gap",
              "ignore_isolated_turns", "straight_as_turn_light", "only_check_AOI")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True).stdout


def map_revisions(repo, map_rel, include_working_copy):
    """Commits that modified the map, in chronological order; None (working copy) is appended at the end when the
    map has uncommitted changes"""
    revs = git(repo, "log", "--reverse", "--format=%h", "--", map_rel).decode().split()
    if include_working_copy and git(repo, "status", "--porcelain", "--", map_rel).strip():
        revs.append(None)
    return revs


def check_version(repo, map_rel, rev, opts, autoware):
    """
    Run the checks on one revision of the map (executed in a subprocess), also the Autoware check when autoware is True.
    Returns (warnings {(check, key): features}, {lanelet id: turn_direction}, lanelet pairs covered by right_of_way)
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
            from utils.validate_autoware_map import validate  # requires ROS, already confirmed importable in main()
            with tempfile.NamedTemporaryFile(suffix=".osm") as f:  # lanelet2 can only read maps from files
                f.write(data)
                f.flush()
                result = _quiet_stderr(validate, f.name)
        run_core_checks(MapContext(lanelet_map, opts), out, result)
    issues = {(i.category, "/".join(sorted(i.ids, key=int))): i.features for i in out.issues}
    return (issues, {k: ll.turn_direction for k, ll in lanelet_map.lanelets.items()},
            covered_pairs(lanelet_map.reg_elems))


def _quiet_stderr(func, *args):
    """Silence messages written by C++ libraries directly to stderr (e.g. "Projected MGRS Grid changed" of the MGRS
    projection), which are printed for every revision"""
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
    results: [(revision, result of check_version())] in chronological order
    Returns the list of samples, each sample is a dict
    """
    active, samples = {}, []
    fixes = {c: set() for c in FEATURES}  # per check, the indices of the revisions with fixes
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

    # warnings kept until the latest revision
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
    """Read dataset.yaml, return (config, output directory of the training data)"""
    with open(path, encoding="utf-8") as f:
        ds = yaml.safe_load(f) or {}
    return ds, resolve(ds.get("output_dir", "data"))


def build_dataset(config_path=DEFAULT_DATASET_CONFIG):
    """Build the training data according to dataset.yaml and return the output directory;
    raises RuntimeError when ROS is required but cannot be imported"""
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

    repos = []  # (name, path, list of revisions, ignore list)
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
                except Exception as e:  # historical revisions may be empty files, merge conflicts, etc., skip them
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
