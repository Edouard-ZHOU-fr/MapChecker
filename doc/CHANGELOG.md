# 更新日志

中文 | [Français](CHANGELOG.fr.md)

本文件记录 MapChecker 各版本的主要变化，最新的在前。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。版本号即仓库中的 git tag。标有 **不兼容** 的变更需要修改用法、配置或扩展代码。

## [未发布]

### 新增

- **扩展 `altitude`**：检查车道边界上高程 `ele` 的突兀点（跳变、尖峰、横坡过大、没有 `ele`），并在日志目录生成高程趋势图 `<日志名>_altitude.png`，见 [ext/altitude/README.md](../ext/altitude/README.md)。
- **altitude 的交互窗口**：配置中 `isShow: true` 时，报告结束后弹出独立的高程俯视图窗口；单击突兀点或 lanelet 时，在另一个小窗口中显示其纵断面和细节。
- **地图的全部信息**：`ctx.nodes` / `ctx.ways` / `ctx.relations` / `ctx.map` 提供地图文件中的所有节点、way、relation（全部标签、XML 属性和成员）以及 `MetaInfo`，新扩展不需要修改地图读取代码，见 [ext/README.md](../ext/README.md)。
- **扩展的输出文件和窗口**：`ctx.output_prefix` 为日志的路径前缀，扩展生成的文件与日志同名；扩展在 pyplot 中创建的窗口在报告结束后与热力图窗口一起弹出。

### 变更

- README 默认改为法语（`README.md`），中文版移到 [doc/README.zh.md](README.zh.md)。
- **平行车道不算重叠**（`parallel_min_length` 默认 10 m、`parallel_min_gap` 默认 1.5 m，命令行 `--parallel-min-length` / `--parallel-min-gap`）：两条都是直行、或方向相反（如弯道上的双向车道）的 lanelet，重叠长度 ≥ `parallel_min_length`、且沿重叠段两条中心线始终相距 ≥ `parallel_min_gap` 的，不再算重叠。狭窄或变窄的道路上沿路重叠的车道（如对向车道画得过宽），以前在检查 2 中被报成 "crossing overlap without right_of_way"，而且因为面积大反而 p 较高，在检查 1 中被报成 "intersects other lanes"；现在两项检查都不再报告。中心线会合的汇入 / 交叉，以及同向的弯道并排，不受影响。打分模型已按新规则重新训练。
- 结构不完整的 lanelet（左 / 右边界不正好各一条，或引用了不存在的 way / 节点）以前被悄悄跳过或导致程序崩溃，现在在报告开头列出 `Lanelets not loaded (incomplete, not checked): ...`。
- **不兼容**：`utils.lanelet_map.load_map()` 返回 `LaneletMap`（原来是 `(lanelets, reg_elems, aois)`），`MapContext(lanelet_map, opts, ...)` 的参数随之改变；`utils.heatmap.show_heatmap()` 改名为 `show_windows()`。只通过 `ctx` 使用地图的检查和扩展不受影响。

## [3.0.0] - 2026-10-21

新增扩展检查框架：在 `ext/` 下新建一个目录，就能加入一项新的检查。

### 新增

- **扩展检查 `ext/<名称>/`**：每项扩展有自己的脚本、配置和 README.md。
  - 配置中 `enable: true` 的扩展在检查 1~3 之后运行。
  - 结果写入同一份输出和日志，计入 Summary 和退出码。
  - 忽略列表、AOI 和热力图与检查 1~3 一样处理。
  - 编写方法见 [ext/README.md](../ext/README.md)。
- **扩展 `crosswalk`**：测量 crosswalk 每一端在横向上超出道路多少，超出 `max_extension` 时报 WARNING，见 [ext/crosswalk/README.md](../ext/crosswalk/README.md)。
- **模板 `ext/_template/`**：复制它来新建扩展。它本身也是一个可运行的示例（检查车辆 lanelet 的必需标签），不会自动运行。
- **扩展接口**：扩展只需写一个函数 `check(ctx, cfg, out)`。
  - `ctx`（[utils/context.py](../utils/context.py) 中的 `MapContext`）提供地图，以及 lanelet 重叠、前后继关系、AOI 过滤、空间粗筛等共享的计算结果。这些结果与检查 1、2 共用，只计算一次。
  - `out` 输出的告警自动归入本检查项。
  - 可选的 `DEFAULT_CONFIG` 写参数默认值；定义了它时，配置中的参数名写错会报错。
  - 可选的 `configure(cfg)` 用来检查参数。
  - `script/` 中的辅助模块可以用相对导入。
- **命令行参数**：

  | 参数 | 作用 |
  |---|---|
  | `--ext NAME[,NAME...]` | 运行指定的扩展，不看 `enable` |
  | `--only-ext NAME[,NAME...]` | 只运行指定的扩展，跳过检查 1~3 |
  | `--no-ext` | 不运行扩展 |
  | `--set NAME.KEY=VALUE` | 覆盖扩展配置中的参数，可重复 |
  | `--list-ext` | 列出所有扩展及是否启用 |

- **出错隔离**：扩展抛出异常时，报 `[ERROR] extension <名称> crashed` 并附上 traceback，其他检查照常运行。
- **热力图**：扩展检查的问题用绿色的点标出，每项扩展一种形状。它们不参与着色和区域的判定，在 Hotspots 中单独列出条数。
- **忽略列表**：`config/ignore.yaml` 中可以用扩展名称作键，写只在该扩展中忽略的 lanelet，例如 `crosswalk: [19942]`。
- **测试**：`python3 -m unittest discover -s tests` 会自动加载 `ext/` 下的每个扩展，在一张合成地图上运行一次。

### 变更

- 检查 1~3 的流程从 `check_lanelet_map.py` 移到 [utils/checks.py](../utils/checks.py)。`utils/build_dataset.py` 和 `train_model.py` 不再导入入口脚本。
- 报告末尾的 Ignored lanelets 中，没有运行的检查项的条目标为 `check not run`。

### 修复

- 忽略列表现在接受负数 lanelet id（JOSM 等编辑器新建的元素）。
- 忽略列表写成 `all: 614`（不是列表）时，会给出错误提示并以退出码 2 退出，不再直接崩溃。
- 忽略列表写成映射 `{614: 原因}` 时，不再丢掉原因。

## [2.1.1] - 2026-10-19

### 新增

- **`install.sh`**：一次安装除 ROS / Autoware 外的所有依赖（Ubuntu 22.04）。
  - apt 安装 `python3-pip`、`python3-tk`（热力图窗口用）和 `git`。
  - pip 把 numpy、PyYAML、matplotlib、scipy、scikit-learn 安装到项目中的 `libs/`，不影响系统和用户目录。
  - numpy 限制为 `< 2`，以兼容 ROS 2 Humble。
- `check_lanelet_map.py` 和 `train_model.py` 启动时把 `libs/` 放到 `sys.path` 最前面。

## [2.1.0] - 2026-10-19

### 新增

- **热力图窗口可以交互**：
  - 单击问题点：显示该处问题的 lanelet 编号、检查项和 p，相关 lanelet 用橙色描边，终端同时输出这些问题在日志中的那一行。
  - 单击其他位置：显示该处的 lanelet 编号。
  - 缩放 / 平移模式下也可以单击。
- `train_model.py --skip-build`：不重新生成训练数据，直接用已有的 `data/` 训练。

### 变更

- **不兼容**：`train_model.py` 现在一次完成生成训练数据和训练两步。
  - 原来的 `build_dataset.py` 移到 [utils/build_dataset.py](../utils/build_dataset.py)，不再单独运行。
  - 训练部分移到 [utils/tranning.py](../utils/tranning.py)。
  - `train_model.py` 用 `-c` 指定 `config/dataset.yaml`，`--data` 参数已去掉。
- `config/dataset.yaml` 中暂时去掉了 `sqy_map_planning`。

## [2.0.0] - 2026-10-16

### 新增

- **检查 3：Autoware**（`autoware_checking: true`，需要 source ROS）。
  - 运行 [utils/validate_autoware_map.py](../utils/validate_autoware_map.py)，它是 `autoware_lanelet2_validation` 的 Python 版。
  - 检查地图加载错误、`format_version`、节点高度 `ele` 和红绿灯。
  - 其 `turn_direction` 结果合并到检查 1，同一 lanelet 只报告一次。
  - 命令行：`--[no-]autoware-checking`。
- **告警打分**（`Using_LR: true`）：用 logistic regression 给检查 1、2 中缺少 `turn_direction` / `right_of_way` 的告警打分。
  - 输出 `[WARNING p=…]`，按 p 从大到小排序。Summary 中列出 p < 0.5 的条数。
  - 模型 `config/model.yaml` 由 `train_model.py` 从地图仓库的 git 历史训练，仓库列表和标注规则在 `config/dataset.yaml` 中。
  - 打分只用模型中的数字，不需要 scikit-learn。
  - 命令行：`--[no-]using-lr`、`--model-file`。
- **问题热力图**（`heatmap.enabled: true`）：
  - 报告末尾列出问题频发的区域（Hotspots），并在日志目录生成同名的 `.png`。
  - `Using_LR` 打开时按 p 加权。
  - `isShow: true` 时，报告结束后弹出热力图窗口。
  - 命令行：`--[no-]heatmap`、`--[no-]show-heatmap`。
- README.md。

### 变更

- **不兼容**：`validate_autoware_map.py` 从根目录移到 `utils/`，仍可单独运行：`python3 utils/validate_autoware_map.py lanelet2_map.osm`。
- **不兼容**：`only_check_AOI: true` 而地图中没有 AOI 时，不再做任何检查（之前是检查整张地图）。
- 仓库中 `config/config.yaml` 的默认设置改为：
  - 关闭 `only_check_AOI` 和 `autoware_checking`；
  - 打开 `heatmap`、`Using_LR` 和 `isShow`。

## [1.0.0] - 2026-10-15

首个版本。

### 新增

- **`check_lanelet_map.py`**：读取 Lanelet2 OSM 地图，有 `local_x` / `local_y` 时用局部坐标，否则投影经纬度。只依赖 numpy 和 PyYAML。
- **检查 1：turn_direction**：
  - 检查弯道（`turn_window` 米内航向变化 ≥ `turn_angle`）是否设置了 `turn_direction`。
  - 检查与其他 lane 交叉 / 汇入的直行 lanelet 是否设置了 `turn_direction`。
  - 相关选项：`ignore_isolated_turns`、`straight_as_turn_light`。
- **检查 2：right_of_way**：
  - 交叉 / 汇入（重叠面积 ≥ `min_overlap_area`）的 lanelet 对是否有 `right_of_way` 规则元素，按区域（通常是一个路口）分组输出。
  - 已有的 `right_of_way` 是否被优先 lanelet 引用。
  - 两条 lanelet 是否真的重叠，完全不重叠时报 ERROR。
- **`only_check_AOI`**：只检查完全位于地图 AOI 内的 lanelet。
- **忽略列表 `config/ignore.yaml`**：范围可以是 `all`、`turn_direction` 或 `right_of_way`。报告末尾列出每条忽略屏蔽的问题数。
- **配置和输出**：
  - 配置文件为 `config/config.yaml`，命令行参数优先。
  - 终端彩色输出，同时写入日志 `logs/map_check_YYYYmmdd_HHMMSS.log`。
  - 退出码：`0` 没有问题，`1` 有问题。
- **`validate_autoware_map.py`**：`autoware_lanelet2_validation` 的 Python 版，需要 ROS，可单独运行。

[未发布]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/3.0.0...HEAD
[3.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.1.1...3.0.0
[2.1.1]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.1.0...2.1.1
[2.1.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.0.0...2.1.0
[2.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/1.0.0...2.0.0
[1.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/releases/tag/1.0.0
