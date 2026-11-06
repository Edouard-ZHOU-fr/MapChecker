"""Manually ignored lanelets (config/ignore.yaml, and ignore in the extension check configs)"""

import os
import re

import yaml

from .issue_codes import check_of

CATEGORIES = ("turn_direction", "right_of_way", "autoware")
LANELET_ID = re.compile(r"-?\d+")  # elements newly created in editors such as JOSM and not yet uploaded have negative ids


def parse_items(items, where):
    """
    One ignore list -> {lanelet id: reason}; where is used in error messages.
    items is a list `[614, 615: reason]` (can also be a mapping `{614: reason}`); if the same lanelet appears more than
    once, the entries are merged, keeping the reason that was written
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


def load_ignore(path, extra_scopes=(), codes=()):
    """
    Read ignore.yaml, return [(scope, lanelet id, reason)]; return an empty list if the file does not exist.
    The scope is all, a name of Checks 1~3, an extension check name in extra_scopes, or an issue code in codes
    (registered in config/issue_codes.yaml: only that issue type is ignored for the lanelet).
    Each item can be written as `- 614` or `- 614: reason`.
    """
    if not os.path.isfile(path):
        return []
    scopes = ("all",) + CATEGORIES + tuple(extra_scopes)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping with keys {', '.join(scopes)} or issue codes")
    entries = []
    for scope, items in data.items():
        if scope not in scopes and scope not in codes:
            raise ValueError(f"{path}: unknown key '{scope}', expected one of {', '.join(scopes)}, or an issue code "
                             f"of config/issue_codes.yaml (e.g. stopline.polyline)")
        entries += [(scope, lid, reason) for lid, reason in parse_items(items, f"{path}: '{scope}'").items()]
    return entries


def ignore_by_category(entries, categories=CATEGORIES):
    """
    [(scope, id, reason)] -> {check or issue code: {id: reason}}, only for categories (the checks to run) and the issue
    codes of these checks; all applies to all the checks
    """
    result = {c: {} for c in categories}
    for scope, lid, reason in entries:
        for category in categories if scope == "all" else (scope,):
            if category in result:
                result[category][lid] = reason
            elif check_of(category) in categories and category != check_of(category):  # issue code
                result.setdefault(category, {})[lid] = reason
    return result


def report_ignored(entries, ignore_path, ctx, out):
    """
    List the ignored lanelets and the number of issues they suppress at the end of the report;
    ignore lists in the extension check configs use the extension check name as scope, an issue code as scope only
    ignores that issue type.
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
        checks = [c for c in out.ignore if c == check_of(c)]  # without the issue codes
        n = sum(out.suppressed[(c, lid)] for c in (checks if scope == "all" else (scope,)))
        # an extension check may also report issues of other elements by their id (e.g. stopline: the stop line way or
        # the traffic_sign / road_marking regulatory element; trafficlight: the regulatory element)
        other = None
        if lid not in ctx.lanelets and n > 0:
            other = "way" if lid in ctx.ways else "reg_elem" if lid in ctx.reg_elems else None
        if scope != "all" and scope not in out.ignore:
            status = "check not run"
        elif other:
            status = f"{n} issue(s) suppressed"
        elif lid not in ctx.lanelets:
            status = "not found in map"
        elif not ctx.in_aoi(lid):
            status = "outside AOI"
        else:
            status = f"{n} issue(s) suppressed"
        out.info(f"{other or 'lanelet':<8} {lid:<8} [{scope}] {status}" + (f" | {reason}" if reason else ""))
