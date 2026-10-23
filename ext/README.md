# 扩展检查 (`ext/`)

`ext/` 下每个子目录是一项独立的扩展检查，目录名就是检查项的名称（用于输出、Summary、忽略列表和 `--set`）。扩展只写检查逻辑；命令行、配置、地图读取、AOI、忽略列表、日志、热力图和退出码都由 MapChecker 处理。

| 扩展 | 内容 |
|---|---|
| [altitude](altitude/README.md) | 车道边界上高程 `ele` 的突兀点（跳变、尖峰、横坡过大、没有 `ele`），另外生成高程趋势图 `<日志名>_altitude.png` |
| [crosswalk](crosswalk/README.md) | crosswalk 在横向上最多超出道路多少，超出 `max_extension` 时报 WARNING |
| [stopline](stopline/README.md) | 停止线是否只由两个点组成（一条直线而不是折线），多于 2 个点时报 WARNING |
| [_template](_template/README.md) | 新扩展的模板（示例：车辆 lanelet 的必需标签），不会自动运行 |

## 运行

配置中 `enable: true` 的扩展在检查 1~3 之后自动运行：

- 结果写入同一份输出和日志。
- Summary 中列出该检查项的告警数，并计入退出码。
- 热力图中用绿色的点标出问题点，每项扩展一种形状；它们不参与着色和区域的判定。

```bash
python3 check_lanelet_map.py --list-ext                     # 列出所有扩展及是否启用
python3 check_lanelet_map.py --only-ext crosswalk           # 只运行 crosswalk，跳过检查 1~3
python3 check_lanelet_map.py --ext crosswalk,_template      # 检查 1~3 + 这些扩展，不看 enable
python3 check_lanelet_map.py --no-ext                       # 不运行扩展
python3 check_lanelet_map.py --set crosswalk.max_extension=1.0   # 覆盖扩展配置中的参数，可重复
```

忽略列表：

- `config/ignore.yaml` 中 `all` 下的 lanelet 在扩展检查中也忽略。
- 只在某项扩展中忽略的 lanelet 可以写在该扩展配置的 `ignore` 中，也可以写在 `config/ignore.yaml` 中以扩展名称为键的列表里（例如 `crosswalk: [19942]`）。

## 新建扩展

1. 复制模板，目录名只能包含字母、数字和 `_`：

   ```bash
   cp -r ext/_template ext/my_check
   ```

2. 改写 `script/check_template.py`（可以改名）：`DEFAULT_CONFIG` 中写参数的默认值，`check()` 中写检查逻辑，接口见下文。
3. 改写 `config/config.yaml`（参数，以及 `enable: true`）和 `README.md`（第一行标题会显示在 `--list-ext` 中）。
4. 试运行，并运行测试。测试会自动加载 `ext/` 下的每个扩展，在一张合成地图上运行一次：

   ```bash
   python3 check_lanelet_map.py --only-ext my_check
   python3 -m unittest discover -s tests
   ```

5. 在本文件开头的表格中加上一行。

## 目录结构

```
ext/my_check/
  README.md             说明；第一行标题用于 --list-ext
  config/config.yaml    配置: enable / script / ignore 由框架处理，其余参数原样传给 check()
  script/check_*.py     入口脚本
  script/_helper.py     (可选) 辅助模块，入口中用 from ._helper import f 导入
```

`script/` 作为一个包导入，入口脚本可以用相对导入使用其中的其他模块，也可以直接导入 MapChecker 的 `utils.*`。`script/` 下只有一个不以 `_` 开头的 `.py` 时，它就是入口；有多个时，要在配置中用 `script: 文件名.py` 指定入口。

## 脚本接口

| 名称 | 是否必需 | 说明 |
|---|---|---|
| `check(ctx, cfg, out)` | 必需 | 运行检查。`ctx` 为地图和共享的计算结果，`cfg` 为配置（dict），`out` 为本检查项的输出 |
| `DEFAULT_CONFIG` | 可选 | 参数的默认值（dict）。定义了它时，配置文件或 `--set` 中出现未知的参数名会报错，参数名写错不会被悄悄忽略 |
| `configure(cfg)` | 可选 | 检查 / 转换参数（例如转成 float、检查取值范围），返回 `cfg`；有问题时 `raise ValueError`，程序以退出码 2 报错 |

配置的合并顺序：框架的默认值（`enable: false`、`script`、`ignore: []`）→ `DEFAULT_CONFIG` → `config/config.yaml` → `--set`，最后调用 `configure()`。`cfg["ignore"]` 已转成 `{lanelet id: 原因}`。`--set` 的值按 YAML 解析：`1.0` 为 float，`true` 为 bool，`'[a, b]'` 为列表。

`check()` 抛出异常时，框架报 `[ERROR] extension <名称> crashed: ...`，并在输出和日志中附上 traceback，计入退出码；之后的扩展照常运行。

### `ctx`：[utils.context.MapContext](../utils/context.py)

| 属性 / 方法 | 说明 |
|---|---|
| `ctx.lanelets` / `ctx.reg_elems` | 所有 lanelet（包括 crosswalk 等）/ 规则元素：`{id: Lanelet}` / `{id: RegElem}`，见 [utils/lanelet_map.py](../utils/lanelet_map.py) |
| `ctx.nodes` / `ctx.ways` / `ctx.relations` | 地图中所有的 `<node>` / `<way>` / `<relation>`：`{id: Node}` / `{id: Way}` / `{id: Relation}`，包括不是车道边界的 way（停止线、红绿灯、减速带等）和其他类型的 relation（如 multipolygon），见下文 |
| `ctx.map` | 地图文件中的全部信息（`utils.lanelet_map.LaneletMap`），上面几项是它的同名属性；另外有 `meta`（`<MetaInfo>` 的属性，如 `format_version`、`map_version`）、`attrib`（`<osm>` 根元素的属性，如 `generator`）、`extra`（其他顶层元素，如 `<bounds>`，为原始的 XML 元素）、`skipped`（结构不完整、没有读入 `ctx.lanelets` 的 lanelet：`[(id, 原因)]`）、`projected`（`True` 表示有节点缺少 `local_x` / `local_y`，所有坐标由经纬度投影） |
| `ctx.vehicle` | 车辆 lanelet（`road`、`highway`、`road_shoulder`） |
| `ctx.by_subtype("crosswalk", ...)` | 指定 subtype 的 lanelet |
| `ctx.only_check_aoi` / `ctx.select(lanelets)` / `ctx.in_aoi(id)` | `only_check_AOI` 是否开启 / 只保留完全位于 AOI 内的（未开启时原样返回）/ 单个 lanelet 是否在检查范围内 |
| `ctx.vehicle_near(points)` | 包围盒与 `points`（如 `ll.polygon`）相交的车辆 lanelet，按 id 排序，用于先粗筛再精确计算 |
| `ctx.overlaps` | 几何重叠的车辆 lanelet 对 `[(a_id, b_id, rel, 面积, 中心, 长度)]`，`rel` 为 `None`（交叉）/ `"merge"` / `"diverge"`；与检查 1、2 共用，只算一次 |
| `ctx.topology` | 前后继关系 `(succ, pred)`：`{lanelet id: 后继 / 前继 id 的集合}` |
| `ctx.opts` / `ctx.aois` / `ctx.map_path` | 检查参数（`config/config.yaml` 合并命令行）/ AOI 多边形 / 地图路径 |
| `ctx.output_prefix` | 本次运行的日志 / 热力图的路径前缀（如 `logs/map_check_20261021_141000`）。扩展生成的其他文件（如图）命名为 `f"{ctx.output_prefix}_{out.category}.png"`，目录可能还不存在，写文件前先 `os.makedirs`。为 `None` 时（如测试中）不生成文件 |

地图文件中的所有内容都能从 `ctx` 拿到（每个元素的全部标签、XML 属性、成员都原样保留），新扩展不需要修改地图读取的代码：

| 类 | 属性 |
|---|---|
| `Lanelet` | `id`、`tags`、`subtype`、`turn_direction`、`left` / `right`（边界点 `[N, 2]`，局部米制坐标，方向与行驶方向一致）、`left_ids` / `right_ids`（边界点的节点 id）、`left_z` / `right_z`（边界点的高程 `ele` `[N]`，节点没有 `ele` 时为 `nan`）、`left_way` / `right_way`（边界的 way id，线型等见 `ctx.ways[ll.left_way].tags`）、`left_inverted` / `right_inverted`（边界方向与 way 中节点的顺序相反，读取时翻转过）、`centerline_way`（地图中指定的中心线，没有时为 `None`）、`polygon`、`reg_elems`（引用的规则元素 id） |
| `Node` | `id`、`x` / `y` / `xy`（局部米制坐标）、`ele`（没有时为 `nan`）、`lat` / `lon`、`tags`（全部标签，如灯泡的 `color`）、`attrib`（XML 属性） |
| `Way` | `id`、`node_ids`（文件中的顺序）、`points`（`[N, 2]`）、`z`（`[N]` 高程）、`tags`、`type` / `subtype`（如 `line_thin` / `dashed`、`stop_line`、`traffic_light`）、`closed`（首尾是同一个节点）、`attrib`、`missing`（引用了但地图中不存在的节点，对应的 `points` 为 `nan`） |
| `Relation` / `RegElem` | `id`、`tags`、`type` / `subtype`、`members`（`[(type, role, ref)]`，文件中的顺序）、`refs(role)`、`attrib`。规则元素成员的几何按 ref 查找，例如停止线 `ctx.ways[re.refs("ref_line")[0]].points`、红绿灯高度 `ctx.ways[re.refs("refers")[0]].tags["height"]` |

几何函数（中心线、重采样、点是否在多边形内、重叠面积、多边形距离等）见 [utils/geometry.py](../utils/geometry.py)。

`ctx` 中的数据由所有检查共用，只读，不要修改。

### `out`：[utils.printer.ScopedPrinter](../utils/printer.py)

| 方法 / 属性 | 说明 |
|---|---|
| `out.header(text)` | 本检查的标题，约定为 `f"Ext {out.category}: ..."` |
| `out.warn(text, ids=(), pos=None)` / `out.error(...)` | `[WARNING]` / `[ERROR]`。`ids` 为相关 lanelet id：其中有 lanelet 在忽略列表中时不输出；`pos` 为问题的坐标 `[x, y]`，用于热力图，`None` 时取 `ids` 中 lanelet 中心线的中点 |
| `out.info(text)` / `out.ok(text)` | 普通输出 / `[OK]` |
| `out.category` | 检查项名称（即目录名） |
| `out.warnings` / `out.errors` / `out.ignored` | 本检查已输出的 WARNING / ERROR 数，以及因忽略列表而未输出的条数 |

所有输出都要经过 `out`，不要直接 `print`，否则不会写入日志。

## 约定

- 第三方库：`libs/` 中有 numpy、PyYAML、matplotlib、scipy、scikit-learn。需要其他库时加到 [install.sh](../install.sh) 的 `PIP_PACKAGES` 中。
- 未启用的扩展不会被导入，所以只有启用后才需要它的依赖。
- 计算量大时，先用 `ctx.vehicle_near()` 或包围盒粗筛，再做精确的几何计算。
- 生成的文件（如图）放在 `ctx.output_prefix` 旁边。需要交互窗口时，在 `check()` 中用 `matplotlib.pyplot.figure(num="窗口标题")` 创建，**不要调用 `plt.show()`**：报告结束后框架（`utils.heatmap.show_windows()`）把它和热力图窗口一起弹出，没有图形界面时只输出提示。参考 [altitude](altitude/README.md) 的 `isShow`。
- 在扩展的 README.md 中写明检查什么、为什么、参数、输出示例和测量方法，参考 [crosswalk](crosswalk/README.md)。
