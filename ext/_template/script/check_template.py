"""
Extension check template: whether vehicle lanelets have the tags in required_tags (example, can be run as is)

New extension: cp -r ext/_template ext/<name>, then edit this script, config/config.yaml and README.md, see
ext/README.md.
Test run: python3 check_lanelet_map.py --only-ext <name>
"""

from utils.geometry import centerline
from utils.lanelet_map import by_id

# default values for items missing in config.yaml (enable / script / ignore are handled by utils/ext.py);
# a parameter name in the config that is not here raises an error, so misspelled parameters are not silently ignored
DEFAULT_CONFIG = {
    "required_tags": ["speed_limit"],
}


def configure(cfg):
    """Optional: check / convert the parameters (values from the config file and --set), raise ValueError on problems"""
    if isinstance(cfg["required_tags"], str):
        cfg["required_tags"] = [cfg["required_tags"]]
    if not cfg["required_tags"]:
        raise ValueError("required_tags must not be empty")
    return cfg


def check(ctx, cfg, out):
    """
    ctx: utils.context.MapContext, the map and shared computed results (lanelets, vehicle, overlaps, topology,
         select(), etc.)
    cfg: configuration of this extension (default values already filled in)
    out: utils.printer.ScopedPrinter, warn() / error() automatically go to this check item; the ignore list, Summary,
         log and heatmap are all handled by the framework
    """
    out.header(f"Ext {out.category}: vehicle lanelets with tags {', '.join(cfg['required_tags'])}")
    lanelets = ctx.select(ctx.vehicle)  # with only_check_AOI, only those fully inside the AOI are kept
    for ll in sorted(lanelets.values(), key=by_id):
        missing = [tag for tag in cfg["required_tags"] if tag not in ll.tags]
        if missing:
            c = centerline(ll)
            out.warn(f"lanelet {ll.id}: missing tag {', '.join(missing)}", (ll.id,), pos=c[len(c) // 2])

    out.info(f"{len(lanelets)} vehicle lanelets checked: {out.warnings} with missing tags"
             + (f", {out.ignored} ignored" if out.ignored else ""))
    if out.warnings == 0:
        out.ok(f"All checked vehicle lanelets have {', '.join(cfg['required_tags'])}")
