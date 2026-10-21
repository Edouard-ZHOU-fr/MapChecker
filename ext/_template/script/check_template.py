"""
扩展检查模板: 车辆 lanelet 是否有 required_tags 中的标签 (示例，可以直接运行)

新建扩展: cp -r ext/_template ext/<名称>，然后改这个脚本、config/config.yaml 和 README.md，见 ext/README.md。
试运行: python3 check_lanelet_map.py --only-ext <名称>
"""

from utils.geometry import centerline
from utils.lanelet_map import by_id

# config.yaml 中缺省项的默认值 (enable / script / ignore 由 utils/ext.py 处理)；
# 配置中出现这里没有的参数名时报错，避免写错的参数被悄悄忽略
DEFAULT_CONFIG = {
    "required_tags": ["speed_limit"],
}


def configure(cfg):
    """可选: 检查 / 转换参数 (配置文件和 --set 的值)，有问题时 raise ValueError"""
    if isinstance(cfg["required_tags"], str):
        cfg["required_tags"] = [cfg["required_tags"]]
    if not cfg["required_tags"]:
        raise ValueError("required_tags must not be empty")
    return cfg


def check(ctx, cfg, out):
    """
    ctx: utils.context.MapContext，地图和共享的计算结果 (lanelets、vehicle、overlaps、topology、select() 等)
    cfg: 本扩展的配置 (已补全默认值)
    out: utils.printer.ScopedPrinter，warn() / error() 自动归入本检查项，忽略列表、Summary、日志和热力图都由框架处理
    """
    out.header(f"Ext {out.category}: vehicle lanelets with tags {', '.join(cfg['required_tags'])}")
    lanelets = ctx.select(ctx.vehicle)  # only_check_AOI 时只保留完全位于 AOI 内的
    for ll in sorted(lanelets.values(), key=by_id):
        missing = [tag for tag in cfg["required_tags"] if tag not in ll.tags]
        if missing:
            c = centerline(ll)
            out.warn(f"lanelet {ll.id}: missing tag {', '.join(missing)}", (ll.id,), pos=c[len(c) // 2])

    out.info(f"{len(lanelets)} vehicle lanelets checked: {out.warnings} with missing tags"
             + (f", {out.ignored} ignored" if out.ignored else ""))
    if out.warnings == 0:
        out.ok(f"All checked vehicle lanelets have {', '.join(cfg['required_tags'])}")
