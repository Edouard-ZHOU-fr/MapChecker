"""手动忽略的 lanelet (ignore.yaml)"""

import os

import yaml

CATEGORIES = ("turn_direction", "right_of_way")
IGNORE_SCOPES = ("all",) + CATEGORIES


def load_ignore(path):
    """
    读取 ignore.yaml，返回 [(范围, lanelet id, 原因)]；文件不存在时返回空列表。
    每项可写成 `- 614` 或 `- 614: 原因`。
    """
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping with keys {', '.join(IGNORE_SCOPES)}")
    entries = {}  # (范围, id) -> 原因；同一 lanelet 重复出现时合并，保留写了的原因
    for scope, items in data.items():
        if scope not in IGNORE_SCOPES:
            raise ValueError(f"{path}: unknown key '{scope}', expected one of {', '.join(IGNORE_SCOPES)}")
        for item in items or []:
            lid, reason = next(iter(item.items())) if isinstance(item, dict) and len(item) == 1 else (item, "")
            if isinstance(lid, bool) or not str(lid).isdigit():
                raise ValueError(f"{path}: invalid lanelet id '{lid}' in '{scope}'")
            key = (scope, str(lid))
            entries[key] = entries.get(key) or ("" if reason is None else str(reason))
    return [(scope, lid, reason) for (scope, lid), reason in entries.items()]


def _categories(scope):
    return CATEGORIES if scope == "all" else (scope,)


def ignore_by_category(entries):
    """[(范围, id, 原因)] -> {检查项: {id: 原因}}，all 同时作用于所有检查项"""
    result = {c: {} for c in CATEGORIES}
    for scope, lid, reason in entries:
        for category in _categories(scope):
            result[category][lid] = reason
    return result


def report_ignored(entries, ignore_path, lanelets, aoi_ids, out):
    """在报告末尾列出被忽略的 lanelet 及其屏蔽的问题数"""
    out.header("Ignored lanelets")
    if not os.path.isfile(ignore_path):
        out.info(f"None (ignore file not found: {ignore_path})")
        return
    out.info(f"Ignore file: {ignore_path}")
    if not entries:
        out.info("None")
        return
    for scope, lid, reason in entries:
        if lid not in lanelets:
            status = "not found in map"
        elif aoi_ids is not None and lid not in aoi_ids:
            status = "outside AOI"
        else:
            status = f"{sum(out.suppressed[(c, lid)] for c in _categories(scope))} issue(s) suppressed"
        out.info(f"lanelet {lid:<8} [{scope}] {status}" + (f" | {reason}" if reason else ""))
