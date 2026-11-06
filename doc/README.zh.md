# MapChecker

中文 | [Français](../README.md)

Lanelet2 地图检查工具：找出地图中缺少 `turn_direction`（转向灯）和 `right_of_way`（路权）的地方，可选地合并 Autoware 官方检查的结果，并可用 logistic regression 给告警打分，把更可能是真问题的告警和可能是误报的告警区分开。

## 检查项

| 检查 | 内容 |
|---|---|
| 1. turn_direction | 弯道 / 转弯，以及与其他 lane 相交（交叉 / 汇入）的直行 lanelet 是否设置了 `turn_direction` |
| 2. right_of_way | 交叉 / 汇入的 lanelet 对是否有 `right_of_way` 规则元素；已有的 `right_of_way` 是否被优先 lanelet 引用、两条 lanelet 是否真的重叠（完全不重叠时输出 ERROR） |
| 3. Autoware（`autoware_checking: true`） | 运行 [utils/validate_autoware_map.py](../utils/validate_autoware_map.py)（`autoware_lanelet2_validation` 的 Python 版）：地图加载错误、节点高度 `ele`、红绿灯、路由图中有冲突的 lanelet 的 `turn_direction`。其中 `turn_direction` 的结果合并到检查 1，同一 lanelet 只报告一次 |
| LR 打分（`Using_LR: true`） | 给检查 1、2 中缺少 `turn_direction` / `right_of_way` 的告警打分 `[WARNING turn_direction.missing p=0.77]`，并按分数从高到低输出，见 [告警打分](#告警打分-using_lr) |
| 扩展检查（`ext/<名称>/config/config.yaml` 中 `enable: true`） | 例如 crosswalk 在横向上最多超出道路多少，见 [扩展检查](#扩展检查-ext) |
| 热力图（`heatmap.enabled: true`） | 列出问题频发的区域，并生成热力图：按问题密度从蓝（少）到红（多）给地图着色，见 [问题热力图](#问题热力图-heatmap) |

## 依赖

| 用途 | 需要 |
|---|---|
| 检查 1、2，LR 打分 | Python 3.10+、`numpy`、`PyYAML` |
| 热力图 | `matplotlib` |
| 热力图窗口（`isShow`） | `tkinter`（`python3-tk`） |
| 检查 3（Autoware） | ROS 2 Humble 和 `autoware_lanelet2_extension_python`，运行前先 source：<br>`source /opt/ros/humble/setup.bash`<br>`source ~/ros/ad_ros2_ws/install/setup.bash` |
| 训练打分模型 | `scikit-learn`（及其依赖 `scipy`）、`git`（只有 `train_model.py` 需要）；`config/dataset.yaml` 中 `autoware_checking: true` 时还需要 source ROS |

除 ROS / Autoware 外的依赖可以用 `./install.sh` 一次装好（Ubuntu 22.04）：apt 安装 `python3-pip`、`python3-tk`（热力图弹出窗口）、`git`，pip 把 `numpy`、`PyYAML`、`matplotlib`、`scipy`、`scikit-learn` 及其依赖全部安装到项目中的 `libs/`，不影响系统和用户目录；`check_lanelet_map.py` 和 `train_model.py` 启动时把 `libs/` 放到 `sys.path` 最前面。每次运行都会重新安装并整体替换 `libs/`；其中有编译好的库，只能用安装时的 Python 版本运行。`numpy` 限制为 `< 2`，以免破坏 ROS 2 Humble 的 Python 包。

## 快速开始

```bash
cd MapChecker
python3 check_lanelet_map.py                          # 使用 config/config.yaml 中的地图和设置
python3 check_lanelet_map.py path/to/lanelet2_map.osm # 检查另一张地图
python3 check_lanelet_map.py -c other_config.yaml     # 使用另一个配置文件
python3 check_lanelet_map.py --no-autoware-checking   # 没有 ROS 环境时
python3 check_lanelet_map.py --only-ext crosswalk     # 只运行扩展检查 crosswalk (见 ext/README.md)
```

命令行参数优先于配置文件：

| 参数 | 对应配置 |
|---|---|
| `map`（位置参数） | `input` |
| `-c, --config FILE` | 配置文件，默认 `config/config.yaml` |
| `--turn-angle DEG` | `turn_angle` |
| `--turn-window M` | `turn_window` |
| `--min-overlap-area M2` | `min_overlap_area` |
| `--parallel-min-length M` | `parallel_min_length` |
| `--parallel-min-gap M` | `parallel_min_gap` |
| `--[no-]ignore-isolated-turns` | `ignore_isolated_turns` |
| `--[no-]straight-as-turn-light` | `straight_as_turn_light` |
| `--[no-]only-check-aoi` | `only_check_AOI` |
| `--[no-]autoware-checking` | `autoware_checking` |
| `--ignore-file FILE` | `ignore_file` |
| `--[no-]using-lr` | `Using_LR` |
| `--model-file FILE` | `model_file` |
| `--map-min-p P` | `map_min_p` |
| `--[no-]heatmap` | `heatmap.enabled` |
| `--[no-]show-heatmap` | `isShow` |
| `--[no-]masque` | `masque.enabled` |
| `--no-color` | 不输出颜色 |
| `--ext NAME[,NAME...]` | 运行这些扩展检查，代替配置中 `enable: true` 的 |
| `--only-ext NAME[,NAME...]` | 只运行这些扩展检查，跳过检查 1~3 |
| `--no-ext` | 不运行扩展检查 |
| `--set NAME.KEY=VALUE` | 覆盖扩展检查配置中的参数，例如 `crosswalk.max_extension=1.0`，可重复 |
| `--list-ext` | 列出所有扩展检查及是否启用 |

## 配置 `config/config.yaml`

相对路径以 `MapChecker/` 为基准。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `input` | `lanelet2_map.osm` | 要检查的地图 |
| `ignore_file` | `config/ignore.yaml` | 手动忽略的 lanelet，文件不存在时不忽略 |
| `issue_codes_file` | `config/issue_codes.yaml` | 检查 1~3 的 issue code 及其级别和说明（扩展的在 `ext/<名称>/config/issue_codes.yaml` 中），见 [issue code](#issue-code-issue_codesyaml) |
| `model_file` | `config/model.yaml` | 告警打分模型，`Using_LR: true` 时使用 |
| `log.enabled` / `log.dir` | `false` / `logs` | 终端输出的同时写入日志 `logs/map_check_YYYYmmdd_HHMMSS/map_check_YYYYmmdd_HHMMSS.log`。每次运行的所有输出文件（日志、热力图、masque、扩展生成的文件）都放在 `log.dir` 下新建的目录 `map_check_YYYYmmdd_HHMMSS/` 中（同一秒内启动的多次运行加后缀 `_2`、`_3`…） |
| `heatmap.enabled` | `false` | `true`：在报告末尾列出问题频发的区域，并在日志旁生成同名的热力图 `map_check_YYYYmmdd_HHMMSS.png`，需要 matplotlib |
| `heatmap.radius` | `30.0` | 区域半径（米）：相距不超过 `radius` 的问题算同一区域，热力图的扩散范围约为 `radius` |
| `heatmap.top` | `10` | 最多标注几个区域（至少有 2 条问题才算） |
| `masque.enabled` | `false` | `true`：同时在日志旁生成问题频发区域的减速 masque `map_check_YYYYmmdd_HHMMSS_masque.osm`，见[减速 masque](#减速-masque-masque)；需要 `heatmap.enabled` |
| `masque.subtype` | `mapchecker_hotspot` | masque 区域的 `subtype`（减速原因） |
| `masque.slow_down_speed` | `15` | masque 区域的 `slow_down_speed` 标签（km/h） |
| `masque.height` | `0.2` | masque 区域的 `height` 标签 |
| `masque.margin` | `3.0` | 在区域内问题点的四周各加宽的距离（米） |
| `turn_angle` | `30.0` | 判定为弯道的最小航向变化（度） |
| `turn_window` | `30.0` | 计算航向变化的最大弧长窗口（米），长 lanelet 上的缓弯不算弯道 |
| `min_overlap_area` | `1.0` | 判定两条 lanelet 重叠的最小面积（m²） |
| `parallel_min_length` | `10.0` | 平行车道不算重叠：两条都是直行、或方向相反（重叠处航向差 > 150°）的 lanelet，重叠长度（重叠区沿主方向）≥ 它（米），且沿重叠段两条中心线始终相距 ≥ `parallel_min_gap`，算平行车道（狭窄或变窄的道路上沿路重叠），不算交叉 / 汇入 |
| `parallel_min_gap` | `1.5` | 上面平行车道的中心线最小间距（米）；设成很大的值（如 `99`）即不排除 |
| `ignore_isolated_turns` | `false` | `true`：不与其他 lane 交叉 / 汇入的弯道（分叉不算）不检查 `turn_direction` |
| `straight_as_turn_light` | `true` | `true`：`straight` 也算设置了转向灯，弯道和相交的直行 lanelet 都必须设置 `turn_direction`，不检查方向<br>`false`：检查方向与几何是否一致，直行 lanelet 不要求 `straight` |
| `only_check_AOI` | `false` | `true`：只检查完全位于地图 AOI（`subtype=AOI` 的闭合 way）内的 lanelet；地图中没有 AOI 时不做任何检查，直接结束（退出码 `0`） |
| `autoware_checking` | `false` | `true`：运行检查 3，需要 ROS |
| `isShow` | `false` | `true`：报告结束后弹出热力图窗口（可缩放、平移，问题列表和详情，改变窗口大小不变形，见[问题热力图](#问题热力图-heatmap)），关闭窗口后程序退出；只在 `heatmap.enabled: true` 时有效，没有图形界面（如 ssh）时只输出提示 |
| `Using_LR` | `false` | `true`：用 `model_file` 给告警打分，各检查中按分数从高到低输出；模型文件不存在时报错 |
| `map_min_p` | `0.0` | `Using_LR` 时，p 小于它的有分数告警不在热力图上显示（点、着色、区域和弹出窗口）；报告和日志中仍然列出。`0` 为全部显示 |

默认值是配置文件中缺少该项时使用的值；仓库里的 `config/config.yaml` 打开了 `log`、`heatmap`、`ignore_isolated_turns`、`Using_LR` 和 `isShow`，并设置了 `map_min_p: 0.5`、`parallel_min_length: 30.0`、`parallel_min_gap: 1.8`。

## 忽略列表 `config/ignore.yaml`

确认不需要修改的 lanelet 写在这里，之后不再输出对应的告警，并在报告末尾列出每条忽略屏蔽了多少问题。

```yaml
all:            [13252: "roule pas", 17618]   # 所有检查都忽略
turn_direction: [614: 主路弯道，不需要转向灯]   # 只在检查 1 中忽略
right_of_way:   []                            # 只在检查 2 中忽略 (涉及该 lanelet 的重叠对和 right_of_way 规则元素)
autoware:       []                            # 只在检查 3 中忽略 (该 lanelet 引用的红绿灯问题)
crosswalk:      [19942]                       # 只在扩展检查 crosswalk 中忽略 (键为扩展检查的名称)
stopline.polyline: [52]                      # 只忽略这一类问题 (键为 issue code)
```

每项可以只写 lanelet id（可以是负数），也可以写成 `id: 原因`。键为 issue code 时，只忽略这些 lanelet 上的这一类问题，同一检查的其他问题照常报告。扩展检查的忽略列表也可以写在该扩展自己的配置中。没有运行的检查项的条目在报告末尾标为 `check not run`。

## 输出

- `[WARNING]`（黄色）：需要确认的问题；`[ERROR]`（红色）：确定有错，例如 `right_of_way` 的两条 lanelet 完全不重叠、地图加载错误。每行带有问题的 code：`[WARNING stopline.polyline] stop_line 52: ...`，见 [issue code](#issue-code-issue_codesyaml)。
- 检查 1 按 lanelet id 顺序输出。检查 2 先输出已有 `right_of_way` 规则元素本身的问题，再输出缺少 `right_of_way` 的告警，按区域（通常是一个路口）分组，每组给出中心坐标和涉及的 lanelet。
- `Using_LR: true` 时顺序不同，见 [输出顺序](#输出顺序)。
- 末尾的 Summary 列出各检查项的告警数和每个 code 的问题数（`issues by code`），Hotspots 列出问题频发的区域（`heatmap.enabled: true` 时，见 [问题热力图](#问题热力图-heatmap)），Ignored lanelets 列出忽略列表的使用情况（不在地图中、在 AOI 外、屏蔽了几条）。
- 退出码：`0` 没有问题，`1` 有未忽略的 WARNING / ERROR，`2` 参数或环境错误。可以用于 CI。

### issue code `issue_codes.yaml`

每一类问题都有一个固定的 code `<检查项>.<问题>`（如 `turn_direction.missing`、`stopline.polyline`、`trafficlight.not_recognized`），登记时带有级别、标题和说明。程序、测试和忽略列表都认 code，不依赖提示文字的具体写法，提示文字可以随时修改。每个检查有自己的登记文件：

| 文件 | 登记的 code | 键 |
|---|---|---|
| [config/issue_codes.yaml](../config/issue_codes.yaml) | 检查 1~3（`turn_direction.*`、`right_of_way.*`、`autoware.*`）和 `ext.crashed` | 完整的 code，如 `right_of_way.missing` |
| `ext/<名称>/config/issue_codes.yaml` | 扩展 `<名称>` 的 code，每个扩展都必须有 | 只写问题名：`ext/stopline/` 中的 `polyline` 即 code `stopline.polyline` |

```yaml
# ext/stopline/config/issue_codes.yaml
polyline:
  severity: warning        # error | warning | off
  title: stop line with more than 2 points (polyline)
  description: A stop line should be a straight line of 2 points; ...
```

- **级别**：`severity` 决定这类问题是 WARNING 还是 ERROR（也就影响退出码）；`off` 表示不再报告，Summary 中给出未报告的条数（`not reported (severity off ...)`）。
- **按类型忽略**：在 `config/ignore.yaml` 中以 code 为键，见 [忽略列表](#忽略列表-configignoreyaml)。
- **新增问题类型**：检查中用到的 code 必须先登记在它自己的文件中（未登记的 code 会被报告为该检查崩溃）；扩展的 code 写在 `config/issue_codes.yaml` 中会报错。`tests/test_issue_codes.py` 逐个文件检查源码中的 code 与登记的是否一一对应。所有 code 及其输出条件见 [doc/checkinglist.md](checkinglist.md)。

Autoware 检查也可以单独运行（需要 source ROS）：

```bash
python3 utils/validate_autoware_map.py lanelet2_map.osm
```

## 问题热力图 (`heatmap`)

一张地图的告警常常集中在几个路口。打开 `heatmap.enabled` 后，报告末尾会列出问题最多的区域，并在日志旁生成与日志同名的 `.png`，方便先处理问题最集中的地方：

```
=== Hotspots (at least 2 issues within 30 m) ===
#1  center (67932.7, 6497.8): 16 issues (turn_direction 8, right_of_way 8), lanelets: 3973 4060 4081 4095 4102 4107 4134 4167 4202 4313
#2  center (68200.6, 6151.5): 13 issues (right_of_way 9, turn_direction 4), lanelets: 2690 3668 3676 3690 3698 5595 6165 6811 6866
...
Heatmap: .../logs/map_check_20261016_093357/map_check_20261016_093357.png
```

- **位置**：检查 1 的告警取 lanelet 中心线的中点（即日志中的 `middle`），检查 2 缺少 `right_of_way` 的告警取重叠区的中心，其他告警取相关 lanelet 中点的平均；与 lanelet 无关的告警（节点缺少高度、地图加载错误）不参与。被忽略的告警不参与。
- **颜色**：高斯核密度（sigma = `radius / 2`），数值约为附近的问题数。只有 1 条孤立问题的地方为蓝色，问题越密集越接近红色；没有问题的地方透明，露出地图。
- **区域**：每次取 `radius` 米内剩余问题最多的位置作为一个区域，区域内的问题不再计入之后的区域，直到找到 `heatmap.top` 个区域或剩下的位置都不足 2 条问题。图中用圆圈和 `#排名 问题数` 标注，与日志中的编号一致。
- **只看 p 较高的问题**（`Using_LR: true`）：`map_min_p` 大于 0 时，p 低于它的有分数告警不画在图上，也不参与着色和区域判定；Hotspots 和图的标题中注明隐藏了多少条，报告和日志不受影响。
- **按 p 加权**（`Using_LR: true`）：有分数的告警计 p 条，没有分数的告警和 ERROR 计 1 条，颜色、区域的判定和排名都用加权后的条数，即预计的真问题数；模型认为多半是误报的区域颜色更淡、排名靠后或不再列出。日志中同时给出条数和加权值，图中标注加权值：
  ```
  === Hotspots (at least 2 p-weighted issues within 30 m) ===
  #1  center (67936.5, 6494.3): 14 issues, p-weighted 9.4 (right_of_way 9, turn_direction 5), lanelets: 3973 3980 4060 4107 4134 4167 4202 4313
  ```
- **弹出窗口**：`isShow: true` 时，报告和日志写完后弹出热力图窗口（Tk 图形界面，需要 `python3-tk`）；关闭窗口后程序退出，退出码不变。扩展检查也可以有自己的窗口（如 altitude 配置中的 `isShow`），与热力图窗口一起弹出，关闭所有窗口后程序退出。
  - **地图**（左侧）：与保存的 `.png` 相同。拖动平移，滚轮以鼠标为中心缩放；工具栏可以框选放大、后退 / 前进、回到整张地图、保存当前视图，右侧显示鼠标处的坐标和 lanelet 编号。地图始终保持 1:1 比例并占满左侧区域，改变窗口大小时不会被拉伸或压到一边；图例和色标在侧栏中，不会遮挡或偏离地图。还没有手动平移 / 缩放时，改变窗口大小会让整张地图（或刚跳转到的问题）保持完整可见；手动平移 / 缩放之后，改变窗口大小保持比例尺和中心不变，窗口变大就显示更多的地图。拖动时直接移动已画好的地图，停下或松开后再重画，大地图上也很流畅。
  - **侧栏**（右侧）：统计卡片（问题数、`Using_LR` 时加权后的预计真问题数、区域数、ERROR 数、扩展检查的问题数、被 `map_min_p` 隐藏的条数）；图例（色标和各图层：单击某一层即可隐藏 / 显示密度着色、某一类问题点或区域圆圈，隐藏的层变淡，隐藏的点也不能单击；单击“Legend”标题可以折叠图例，窗口较矮时默认折叠）；按编号跳转到 lanelet；问题列表；选中项的详情。窗口顶部为地图名和报告时间，底部为鼠标和键盘操作提示。
  - **问题列表**：依次为问题频发区域（Hotspots）、ERROR 和各检查项的问题，顺序与输出相同，`p` 列为模型给出的概率。单击某一项，或用 ↑ / ↓ 逐个切换，地图移到该处，相关 lanelet 用橙色描边、问题点用橙色圆圈标出；选中一个检查项则显示它的全部问题。详情中显示这些问题在日志中的整行，可以选中复制（Ctrl+C，Ctrl+A 全选）。
  - **单击地图**：单击问题点时在列表中选中它（同一位置有多条时都选中，视角不动，之后可以用 ↑ / ↓ 继续）；单击 lanelet 显示其编号和它上面的问题；单击空白处或按 Esc 取消选择。缩放 / 平移模式下也可以单击，拖动不算单击。
- **范围**：`only_check_AOI: true` 时只画 AOI 的范围（虚线为 AOI 边界），否则画整张地图。问题点按检查项用不同形状标出：圆形 turn_direction、方形 right_of_way、三角形 autoware。扩展检查（`ext/`）的问题用绿色的点标出（每项扩展一种形状），不参与着色和区域的判定，Hotspots 中单独列出其条数。所有检查（包括扩展检查）的 ERROR 都用红色的点标出，画在其他点之上。

## 减速 masque (`masque`)

`masque.enabled: true`（或 `--masque`）且热力图开启时，Hotspots 中的区域同时写入日志旁单独的 OSM 文件 `map_check_YYYYmmdd_HHMMSS_masque.osm`，格式与在 VMB 中手工编辑的 `masque_lanelet2.osm` 相同，让车辆在地图问题最集中的地方减速：

```
Masque: .../logs/map_check_20261027_115711/map_check_20261027_115711_masque.osm (10 detection_area, subtype=mapchecker_hotspot, slow_down_speed=15, margin 3 m)
  way 5: hotspot #1, 58.4 x 26.2 m, center (31471.6, 9692.2)
  way 10: hotspot #2, 12.3 x 11.7 m, center (30962.8, 11223.4)
```

- **每个区域一块**，顺序同 Hotspots（与日志和图中的编号一致）：包含该区域所有问题点的最小矩形（任意方向），四周各加宽 `masque.margin` 米。参与的问题与区域判定相同（`map_min_p`、按 p 加权、不含扩展检查）。
- **格式**：4 个节点 + 一个 way，`type=detection_area`、`subtype=<masque.subtype>`、`area=yes`、`height`、`slow_down_speed`；第 k 块用节点 5k+1 ~ 5k+4 和 way 5k+5，与 masque 文件相同。
- **坐标**：`local_x` / `local_y` 为矩形的角点；`lat` / `lon` 用最近的 16 个地图节点做局部仿射拟合（与 MGRS 投影相差 < 1 mm，不需要投影库）；`ele` 取最近的车辆 lanelet 边界节点的高程。
- 使用前请检查（如在 VMB / JOSM 中打开）：按实际情况调整区域、`subtype` 和速度，再复制到地图仓库。

## 告警打分 (`Using_LR`)

### 作用

一张地图常有上百条告警，其中一部分是地图团队认为不需要修的。打开 `Using_LR` 后，检查 1、2 中缺少 `turn_direction` / `right_of_way` 的告警会带上分数 `p`，即该告警为真问题的概率，也就是**地图团队之后会修复它的概率**。Summary 中会列出 `p < 0.5` 的告警数。分数只用来决定先看哪些告警，所有告警仍然全部输出。

### 输出顺序

终端和日志中，每个检查内带分数的告警按 `p` 从大到小输出（`p` 相同时按原来的顺序）：

- 检查 1：没有分数的告警（例如 `turn_direction` 的值不合法）在最前面，之后是按 `p` 排序的告警。
- 检查 2：先输出已有 `right_of_way` 规则元素本身的问题（没有分数），再列出所有区域，最后是所有区域缺少 `right_of_way` 的告警一起按 `p` 排序，每条注明所在区域 `(area k)`。

```
=== Check 1: turn_direction on curves / turns (angle threshold 30°, window 30 m) ===
[WARNING turn_direction.missing p=0.77] lanelet 12528: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
[WARNING turn_direction.missing p=0.77] lanelet 17361: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
...
[WARNING turn_direction.missing_straight p=0.07] lanelet 13646: intersects other lanes without turn_direction (geometry straight, ...) | ...

=== Check 2: right_of_way on overlapping lanelets (min overlap area 1.0 m²) ===
-- Area 1, center (30772.0, 10528.6), lanelets: 9 14 445 450 10735 10754
...
-- Area 26, center (30572.3, 11333.2), lanelets: 16541 16550 17931 17936
[WARNING right_of_way.missing p=0.84] lanelet 12494 / 19041 (area 12): merge overlap (~13.5 m² at (31465.0, 9665.5)) without right_of_way
[WARNING right_of_way.missing p=0.69] lanelet 14182 / 17379 (area 22): merge overlap (~12.9 m² at (30830.3, 11337.8)) without right_of_way
...
```

### 计算方法

模型是 logistic regression：`p = 1 / (1 + e^-z)`，`z = intercept + Σ coef × (特征 - mean) / scale`。这些数字保存在 `config/model.yaml` 中，检查时直接计算，不需要 scikit-learn。

`model.yaml` 还记录了每个特征在训练数据中的取值范围（`min` / `max`）。如果某个特征在训练数据中始终不变，而一条告警的值不同（例如训练数据中没有只由 Autoware 检出的告警，`isolated` 始终为 0），说明模型没有见过这种告警，这条告警不打分，而不是给出一个外推的分数。

### 训练 / 更新模型

训练数据来自地图仓库的 git 历史：在每个仓库地图的每个历史版本上运行检查 1、2（`config/dataset.yaml` 中 `autoware_checking: true` 时同时运行 Autoware 检查），跟踪每条告警后来是被修复、被忽略还是一直保留。

只由 Autoware 检出的 `turn_direction` 告警（例如分叉处的 lanelet，Autoware 的路由图把分叉也算作冲突）和几何检查的告警很不一样，必须在训练数据中出现，模型才能正确打分。所以生成训练数据时默认运行 Autoware 检查，需要先 source ROS；关闭时检查中不给这类告警打分（见 [计算方法](#计算方法)）。

1. 在 [config/dataset.yaml](../config/dataset.yaml) 中列出地图仓库，以及该仓库对应的 ignore 文件（如果有）。
2. 生成训练数据并训练、评估，生成 `config/model.yaml`：

   ```bash
   python3 train_model.py                # --C 0.1 可以加强正则化
   python3 train_model.py --skip-build   # 不重新生成训练数据，直接用已有的 data/ 训练（例如只调整 --C）
   ```

   `train_model.py` 先用 [utils/build_dataset.py](../utils/build_dataset.py) 重放地图仓库的历史，生成训练数据 `data/turn_direction.csv` 和 `data/right_of_way.csv`（已加入 `.gitignore`），再用 [utils/tranning.py](../utils/tranning.py) 训练。

地图仓库有新的提交、ignore 文件更新了，或者修改了 [utils/features.py](../utils/features.py) 中的特征后，重新运行 `train_model.py` 即可。检查阈值（`turn_angle` 等）使用 `config/config.yaml` 中的设置，修改阈值后也应重新训练。

### 标注规则

| label | 条件 |
|---|---|
| 1（真问题） | 告警在之后的版本中消失，且原因是补上了 `turn_direction` / 覆盖该 lanelet 对的 `right_of_way` |
| 0（误报） | 告警保留到最新版本，且相关 lanelet 在 ignore 文件中；或告警出现之后，同一仓库同一检查项至少有 `min_fix_rounds`（默认 2）个版本修复了其他告警，这条却一直没修 |
| 不标注 | 告警因 lanelet 被删除 / 几何改变而消失，或保留到最新版本但不满足上面的条件（可能只是还没修） |

修复按版本数而不是条数计算，因为一次提交常常批量修复几十条。只有一种 label 的仓库（例如全部告警都被修复了）不参与训练，否则模型会学到 "哪张地图" 而不是 "哪条告警" 的差别。

### 特征

| 检查项 | 特征 |
|---|---|
| turn_direction | `abs_angle` 航向变化（度）、`curve` 是否弯道、`log_length` 长度、`n_crossing` / `n_merge` / `n_diverge` 与之真正交叉 / 汇入 / 分叉的 lanelet 数、`log_overlap_length` 与其他 lanelet（分叉除外）的最长重叠长度（重叠区沿主方向的长度）、`traffic_light` 自身或前继 lanelet 引用了红绿灯、`isolated` 不与任何 lane 真正交叉 / 汇入，即没有路口 |
| right_of_way | `log_area` 重叠面积、`merge` 汇入还是交叉、`heading_diff` 重叠处航向差、`max_abs_angle` 两条 lanelet 中较大的航向变化 |

**真正交叉、平行车道与边缘相碰**：有些重叠并不是路口。狭窄或变窄的道路上，两条直行车道会沿路重叠：例如对向两条车道的中心线只相距约 2 m，而车道画得比路面宽，重叠出一条几十米长的带子。几十米长的路口基本不可能；真正的交叉 / 汇入，两条中心线会会合到一起。在 15 张地图中，两条直行 lanelet 重叠 ≥ 10 m 的有 167 对，其中团队加了 right_of_way 的 40 对，沿重叠段两条中心线的最小间距都不超过 1.02 m（车道在会合）。因此两条都是直行（航向变化 < `turn_angle`）、或方向相反（重叠处航向差 > 150°，例如弯道上的双向车道）的 lanelet，重叠长度 ≥ `parallel_min_length`（默认 10 m）、且沿重叠段中心线始终相距 ≥ `parallel_min_gap`（默认 1.5 m）的，算平行车道、不算重叠，检查 1、2 都不再把它当作交叉（`utils/context.py` 的 `is_parallel`）；这样排除 58 对（其中 11 对涉及弯道，如 cern 的 9700/9710），没有团队加了 right_of_way 的。涉及弯道的只排除对向的：同向的弯道并排（转弯车道在弯道前后贴着相邻车道擦过）里有团队加了 right_of_way 的，中心线间距可达 1.8 m。只看重叠宽度区分不了：例如 le_plessis 的 -99869/4029，4029 画成 4.2 m 宽，重叠宽 1.5 m、长 60 m，但两条中心线始终相距 1.8~2.1 m。重叠不到 10 m 的不排除：更短的平行重叠里有团队加了 right_of_way 的。其余的边缘相碰（例如分叉起点的弯道蹭到对向车道）仍由特征区分：只有至少一条 lanelet 的中心线进入另一条 lanelet 的范围（车辆会驶入对方车道）才算真正交叉（`utils/geometry.py` 的 `centerline_enters`）。

节点高度差也试过，后来去掉了：它随地图版本的处理方式（如高度平滑）而变，模型学到的是版本的差别。

### 领域知识约束

有些经验是确定的方向，例如 turn_direction 告警中：

- 重叠过长（`log_overlap_length`）是道路狭窄造成的，不是真的交叉 / 汇入；
- 有红绿灯的路口（`traffic_light`）的告警多为误报；
- 没有路口（`isolated`，例如分叉）的告警多为误报。

这类特征列在 [utils/features.py](../utils/features.py) 的 `NEGATIVE_FEATURES` 中，训练时系数限制为 ≤ 0：**方向由经验决定，降低多少由数据决定**。数据不支持时系数会停在 0（`train_model.py` 输出 `at the bound`），即不起作用，但不会反过来提高 p。sklearn 的 `LogisticRegression` 不支持限制系数，训练时（`utils/tranning.py`）用 scipy 的 L-BFGS-B 求解同样的目标函数，不加限制时结果与 sklearn 一致。

把经验告诉模型有三种方法，效果不同：

| 方法 | 做法 | 效果 |
|---|---|---|
| 加特征 | 在 `FEATURES` 中加入能描述这种情况的特征 | 数据决定方向和大小；样本少时不稳定 |
| 限制系数方向 | 加入 `NEGATIVE_FEATURES` | 方向固定，数据决定大小；数据不支持时不起作用 |
| 改标签 | 把这类告警写进对应仓库的 ignore 文件（`config/dataset.yaml` 中的 `ignore_file`），成为 label 0 | 模型一定会学到；但如果所有这类告警都是误报，直接写成规则更简单 |

目前 `traffic_light` 只有 18 个训练样本（其中 3 条被修复），降低的幅度不可靠；要让它更可靠，可以把各地图中确认不需要的红绿灯路口告警写进 ignore 文件后重新训练。

### 当前效果（`config/model.yaml`，2026-10-30 训练，12 个仓库）

评估用 leave-one-map-out：每次留出一个仓库，用其他仓库训练后给它打分，相当于在一张新地图上的效果。AUC 是随机取一条真问题和一条误报、真问题得分更高的概率，0.5 等于乱猜。作为对比，单个特征的 AUC 也同样只用其他仓库选出最好的一个特征及其方向，再给留出的仓库排序。表中为各仓库的平均值。

| 检查项 | 有 label 的样本 | 模型 AUC | 最好的单个特征 AUC |
|---|---|---|---|
| turn_direction | 453（213 条修复），6 个仓库 | 0.73 | 0.47 |
| right_of_way | 353（129 条修复），4 个仓库 | 0.65 | 0.64 |

turn_direction：单个特征换到一张新地图上不起作用（0.47），模型明显更好；right_of_way：模型与只按重叠面积排序差不多（0.65 / 0.64）。以 `p < 0.5` 当作误报：turn_direction 的 240 条误报中识别出 103 条，213 条真问题中 43 条被打了低分；right_of_way 的 224 条误报中识别出 132 条，129 条真问题中 35 条被打了低分。

`train_model.py` 会输出每个仓库的结果和模型系数，重新训练后以它的输出为准。

模型学到的规律（括号中为标准化系数，正数表示更可能是真问题）：

- **turn_direction**：
  - 没有路口的告警（`isolated`，-0.84）最不会被修，odds 约为有路口时的 0.08 倍。
  - 与其他 lanelet 交叉越多越不会被修（`n_crossing`，-0.61，每多一条交叉 odds 约乘 0.6），即团队通常不给相交的直行 lanelet 标 `straight`。
  - 弯道比直行更会被修（`curve`，+0.32）。
  - 红绿灯路口的告警 odds 约为其他的 0.15 倍（`traffic_light`，-0.37），但只有 18 个样本。
  - 重叠长度（`log_overlap_length`）已不起作用，系数停在 0：长条形的平行重叠已由 `parallel_min_length` / `parallel_min_gap` 直接排除，剩下的数据不支持用它降低 p。
- **right_of_way**：重叠面积越大越会被修（`log_area`，+0.91），面积很小的重叠多数没有处理；涉及转弯的 lanelet 对也更会被修（`max_abs_angle`，+0.34）。

### 局限

- label 表示 "地图团队之后是否修复了它"，学到的是团队的习惯，不等于 Autoware 是否需要。例如 Autoware 要求路由图中有冲突的直行 lanelet 也设置 `turn_direction`，但模型会给这类告警较低的分数。
- right_of_way 只有 4 个仓库可以评估（le_plessis_trevise 只有一种 label，不参与），各仓库的 AUC 在 0.57~0.68 之间，结果不稳定。
- 正在修改中的地图（如 Noisy-le-Roi）里 "一直没修" 的告警可能只是还没修到，label 0 不可靠；该仓库 turn_direction 的 AUC 只有 0.47。它的样本最多（228 条，占一半），也拖低了其他地图的效果。
- 发现的规律如果足够明确，更好的做法是把它变成规则或阈值，而不是一直依赖模型。例如平行车道：right_of_way 模型没有描述它的特征，长条形的重叠面积大，反而得到较高的 p；现在已由 `parallel_min_length` / `parallel_min_gap` 直接排除，同向弯道的边缘相碰仍留给模型。

## 扩展检查 (`ext/`)

`ext/` 下每个子目录是一项独立的扩展检查，有自己的脚本（`script/`）、配置（`config/config.yaml`）和 README.md。配置中 `enable: true` 的扩展在检查 1~3 之后运行，结果写入同一份输出和日志，计入 Summary 和退出码；忽略列表、AOI、热力图都与检查 1~3 一样处理。`--ext` / `--only-ext` / `--no-ext` 选择要运行的扩展，`--set` 覆盖其参数。

| 扩展 | 内容 |
|---|---|
| [altitude](../ext/altitude/README.md) | 车道边界上高程 `ele` 的突兀点（跳变、尖峰、横坡过大、没有 `ele`），另外在日志目录生成高程趋势图 `<日志名>_altitude.png` |
| [border_sharing](../ext/border_sharing/README.md) | 并排的车道是否共用中间的边界线；位置重合但各画一条（不同的 way）时报 WARNING，此时不能换道，对向车道之间可能有缝或重叠 |
| [crosswalk](../ext/crosswalk/README.md) | crosswalk 在横向上最多超出道路多少，超出 `max_extension` 时报 WARNING |
| [stopline](../ext/stopline/README.md) | 停止线是否只由两个点组成（一条直线而不是折线），多于 2 个点时报 WARNING；traffic_sign 的 `ref_line`、road_marking 的 `refers` 是否为停止线（停止线放错 role 或 `type` 写错时 Autoware 不会在这里停车） |
| [trafficlight](../ext/trafficlight/README.md) | 红绿灯能否被 Autoware 的 traffic_light 模块和相机匹配使用：灯画反或在停止线后面导致车道上认不出任何灯（车永远不走）、停止线不穿过车道（红灯不停）、一个灯属于多个规则元素、停止线延长后穿过别的同向车道、灯高和灯泡位置 |

**新增扩展检查**：复制模板 `cp -r ext/_template ext/<名称>`，只需写一个 `check(ctx, cfg, out)` 函数；地图读取、配置、忽略列表、日志和热力图都由框架处理。`ctx` 中有地图文件的全部信息（所有节点、way、relation 的标签和几何）和共享的计算结果（如 lanelet 重叠、前后继关系），不用改地图读取代码，也不用重复计算。步骤和接口见 [ext/README.md](../ext/README.md)。`python3 -m unittest discover -s tests` 会自动加载 `ext/` 下的每个扩展，在一张合成地图上运行一次。

## 目录结构

```
MapChecker/
  check_lanelet_map.py        入口
  train_model.py              生成训练数据并训练 logistic regression，生成 config/model.yaml
  install.sh                  安装依赖 (ROS / Autoware 除外)
  README.md                   说明 (法语，中文版见 doc/README.zh.md)
  doc/
    README.zh.md              本文档 (中文版)
    CHANGELOG.md              更新日志 (法语版 CHANGELOG.fr.md)
    checkinglist.md           检查清单：每项检查及告警的输出条件 (法语版 checkinglist.fr.md)
  ext/                        扩展检查 (配置中 enable 为 true 时运行)，每项有自己的 README.md
    README.md                 如何编写扩展检查 (接口、ctx、out)
    _template/                新扩展的模板 (不会自动运行)
    altitude/                 车道高程的突兀点，并生成高程趋势图
    border_sharing/           并排的车道是否共用边界线
    crosswalk/                crosswalk 在横向上最多超出道路多少
    stopline/                 停止线是否为两点直线，traffic_sign / road_marking 是否正确引用停止线
    trafficlight/             红绿灯能否被 Autoware 和相机匹配使用
  tests/                      测试 (python3 -m unittest discover -s tests)
  config/
    config.yaml               检查配置
    ignore.yaml               手动忽略的 lanelet
    issue_codes.yaml          检查 1~3 的 issue code 及其级别和说明
    dataset.yaml              train_model.py 的配置 (地图仓库列表、标注规则)
    model.yaml                告警打分模型 (train_model.py 生成)
  utils/
    lanelet_map.py            读取 Lanelet2 OSM 地图 (保留全部节点、way、relation 和 MetaInfo)
    context.py                MapContext: 地图的全部信息和各项检查共享的计算结果 (重叠、拓扑、AOI)
    checks.py                 检查 1~3 的流程
    geometry.py               几何工具 (中心线、航向变化、重叠面积、AOI)
    topology.py               前后继关系和 lanelet 重叠
    check_turn_direction.py   检查 1
    check_right_of_way.py     检查 2
    check_autoware.py         检查 3 的报告
    validate_autoware_map.py  Autoware 检查 (需要 ROS)，也可单独运行
    ext.py                    扩展检查的发现、加载和运行
    features.py               告警的特征
    model.py                  读取模型并打分
    ignore.py                 忽略列表
    issue_codes.py            读取各 issue_codes.yaml
    config.py                 读取 config.yaml
    printer.py                终端 / 日志输出
    heatmap.py                问题热力图和问题频发的区域
    heatmap_window.py         热力图窗口 (Tk 图形界面)
    masque.py                 由问题频发区域生成减速 masque
    build_dataset.py          从地图仓库的 git 历史生成训练数据
    tranning.py               训练 logistic regression
  libs/                       install.sh 安装的第三方库 (不提交)
  data/                       训练数据 (不提交)
  logs/                       每次运行一个目录：日志、热力图、masque 等 (不提交)
```
