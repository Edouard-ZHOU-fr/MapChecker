"""手动忽略的 lanelet (config/ignore.yaml，以及扩展检查配置中的 ignore)"""

import os
import re

import yaml

CATEGORIES = ("turn_direction", "right_of_way", "autoware")
LANELET_ID = re.compile(r"-?\d+")  # JOSM 等编辑器新建、还没上传的元素 id 为负数


def parse_items(items, where):
    """
    一个忽略列表 -> {lanelet id: 原因}；where 用于错误信息。
    items 为列表 `[614, 615: 原因]` (也可以是映射 `{614: 原因}`)；同一 lanelet 重复出现时合并，保留写了的原因
    """
    if items is None:
        return {}
    if isinstance(items, dict):
        items = [{k: v} for k, v in items.items()]
    elif not isinstance(items, list):
        raise ValueError(f"{where}: must be a list of lanelet ids such as [614, 615: reason], got '{items}'")
    result = {}
    for item in items:
        lid, reason = next(iter(item.items())) if isinstance(item, dict) and len(item) == 1 else (item, "")
        if isinstance(lid, bool) or not LANELET_ID.fullmatch(str(lid)):
            raise ValueError(f"{where}: invalid lanelet id '{lid}'")
        result[str(lid)] = result.get(str(lid)) or ("" if reason is None else str(reason))
    return result


def load_ignore(path, extra_scopes=()):
    """
    读取 ignore.yaml，返回 [(范围, lanelet id, 原因)]；文件不存在时返回空列表。
    范围为 all、检查 1~3 的名称，或 extra_scopes 中的扩展检查名称。每项可写成 `- 614` 或 `- 614: 原因`。
    """
    if not os.path.isfile(path):
        return []
    scopes = ("all",) + CATEGORIES + tuple(extra_scopes)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping with keys {', '.join(scopes)}")
    entries = []
    for scope, items in data.items():
        if scope not in scopes:
            raise ValueError(f"{path}: unknown key '{scope}', expected one of {', '.join(scopes)}")
        entries += [(scope, lid, reason) for lid, reason in parse_items(items, f"{path}: '{scope}'").items()]
    return entries


def ignore_by_category(entries, categories=CATEGORIES):
    """
    [(范围, id, 原因)] -> {检查项: {id: 原因}}，只包括 categories (要运行的检查项)；all 同时作用于其中所有检查项
    """
    result = {c: {} for c in categories}
    for scope, lid, reason in entries:
        for category in categories if scope == "all" else (scope,):
            if category in result:
                result[category][lid] = reason
    return result


def report_ignored(entries, ignore_path, ctx, out):
    """
    在报告末尾列出被忽略的 lanelet 及其屏蔽的问题数；扩展检查配置中的忽略列表以扩展检查名称为范围。
    ctx: utils.context.MapContext
    """
    out.header("Ignored lanelets")
    if os.path.isfile(ignore_path):
        out.info(f"Ignore file: {ignore_path}")
    elif not entries:
        out.info(f"None (ignore file not found: {ignore_path})")
        return
    if not entries:
        out.info("None")
        return
    for scope, lid, reason in entries:
        if scope != "all" and scope not in out.ignore:
            status = "check not run"
        elif lid not in ctx.lanelets:
            status = "not found in map"
        elif not ctx.in_aoi(lid):
            status = "outside AOI"
        else:
            n = sum(out.suppressed[(c, lid)] for c in (out.ignore if scope == "all" else (scope,)))
            status = f"{n} issue(s) suppressed"
        out.info(f"lanelet {lid:<8} [{scope}] {status}" + (f" | {reason}" if reason else ""))
