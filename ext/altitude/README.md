# 车道高程突兀点检查

MapChecker 的扩展检查：检查车道（车辆 lanelet：`road`、`highway`、`road_shoulder`）边界上各节点的高程 `ele`，找出高程突然跳变的点并报 WARNING，另外生成一张高程趋势图。

路面的高程是连续变化的：纵坡一般不超过 10% 左右，横坡一般只有百分之几。相邻两个节点之间高差很大、或者车道一侧比另一侧高很多，基本都是数据问题，例如没有实测高程的节点填了默认值、两次测量的数据接在一起时没有对齐。Autoware 会使用这些高程，突兀点可能导致车辆的 z 坐标跳变，或者与点云地图对不上。

## 运行

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true` 后（默认已启用），`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入终端输出和日志。
- Summary 中列出 `altitude warnings`，并计入退出码。
- 热力图中用绿色的点标出突兀点，不参与着色和问题频发区域的判定。
- 在日志目录中生成高程趋势图 `map_check_YYYYmmdd_HHMMSS_altitude.png`，与日志同名。

单独运行（不看 `enable`，跳过检查 1~3）：

```bash
cd MapChecker
python3 check_lanelet_map.py --only-ext altitude                                  # 地图为 config/config.yaml 中的 input
python3 check_lanelet_map.py path/to/lanelet2_map.osm --only-ext altitude
python3 check_lanelet_map.py --only-ext altitude --set altitude.min_step=0.2      # 命令行优先于配置文件
python3 check_lanelet_map.py --only-ext altitude --set altitude.plot=false        # 不生成趋势图
```

日志、`--no-color` 等与 `check_lanelet_map.py` 相同。退出码也相同：`0` 没有问题，`1` 有未忽略的 WARNING / ERROR，`2` 参数或配置错误。

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查（配置文件中为 `true`） |
| `max_slope` | `0.15` | 车道边界上相邻两点之间的最大坡度（高差 / 水平距离，0.15 即 15%），超过时为跳变或尖峰 |
| `max_cross_slope` | `0.15` | lanelet 一侧边界上的点与另一侧边界之间的最大横坡 |
| `min_step` | `0.1` | 高差小于它（米）时不报，避免测量噪声、几乎重合的两点被误报 |
| `plot` | `true` | 生成高程趋势图，需要 matplotlib；`false` 时不需要 matplotlib |
| `max_profiles` | `12` | 趋势图中最多画几个突兀点的纵断面（高差最大的几个） |
| `isShow` | `false` | `true`：报告结束后弹出高程俯视图窗口，单击突兀点时在另一个小窗口中显示细节，见下文。需要 matplotlib 和图形界面，与 `plot` 无关（配置文件中为 `true`） |
| `ignore` | `[]` | 本检查忽略的 lanelet id，写法同 `config/ignore.yaml`：`[534, 12868: 原因]` |

默认值是配置文件中缺少该项时使用的值。参数名写错、取值为负数时程序报错（退出码 2）。

地图 `input`、日志目录、忽略列表 `ignore_file` 和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`：

- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。只在本检查中忽略的 lanelet，可以写在上面的 `ignore` 中，也可以写在 `config/ignore.yaml` 的 `altitude:` 下。
- 一个突兀点涉及多条 lanelet（例如相邻车道共用的边界）时，其中任意一条被忽略，这个突兀点就不报告。
- `only_check_AOI: true` 时只检查完全位于 AOI 内的车辆 lanelet。

## 判定方法

坡度都是高差除以水平距离（xy 平面上的距离）。高差小于 `min_step` 的都不报。

1. **跳变（step）**：沿每条车道边界，相邻两个节点之间的坡度大于 `max_slope`。相邻车道共用的边界只检查一次。
2. **尖峰（spike）**：边界上一个节点前后两段都是跳变、并且方向相反（先升后降或先降后升），即这个点比前后两点都高（或都低）。报告为这一个点，高差为它偏离前后两点连线的距离，不再报告两段跳变。
3. **横坡（cross）**：lanelet 一侧边界上的每个节点，与另一侧边界上最近点（沿边界线性插值高程）的高差大于 `max_cross_slope` × 两者的距离。每条 lanelet 只报告高差最大的点，并注明有几个点超出；重叠的 lanelet（例如分叉处）最大高差在同一个点时合并为一条。这个点或另一侧对应的点已经报告为跳变 / 尖峰时不再报告，同一处问题只报一次。
4. **没有 `ele` 的节点**：每条 lanelet 报一条 WARNING，列出这些节点；它们不参与上面的检查。

所有突兀点按高差从大到小输出，编号 `#k` 与趋势图中的编号一致。

## 输出

```
=== Ext altitude: abrupt elevation changes on lane boundaries (max slope 15%, max cross slope 15%, min step 0.10 m) ===
[WARNING] #1 step between nodes 681 -> 682: 183.18 -> 174.72 m (-8.46 m over 7.29 m, slope 116%, max 15%) | lanelet 693 right at (31496.8, 9715.5)
[WARNING] #7 cross slope at node 664: 4.75 m below the right boundary 2.31 m away (cross slope 206%, max 15%) | lanelet 680 left, lanelet 693 left at (31498.9, 9725.1)
[WARNING] #55 spike at node 1674: 0.28 m above nodes 1673 and 1675 (179.59 m, slope up to 19%, max 15%) | lanelet 1683 right at (30877.3, 10635.5)
...
elevation 169.34 ~ 192.13 m; slope between adjacent nodes: median 0.0%, 99% 15.7%; cross slope: median 0.0%, 99% 10.7% (node pairs >= 1 m apart)
581 vehicle lanelets (5381 nodes) checked: 54 steps, 4 spikes, 6 cross slopes, 4 ignored
Trend chart: /home/.../MapChecker/logs/map_check_20261021_142946_altitude.png
```

`|` 后为突兀点所在的边界（`lanelet 693 right` 即 lanelet 693 的右边界）和坐标。倒数第三行是坡度的分布（只用水平距离不小于 1 m 的点对），可用于调整阈值。

## 高程趋势图

`plot: true` 时生成，保存在日志目录，文件名与日志相同，后缀为 `_altitude.png`：

- **上：俯视图**。检查的车辆 lanelet 按高程着色（浅蓝低、深蓝高，每约 2 m 一段；颜色范围取全部高程的 1%~99%，超出的用两端的颜色），可以看出整体的高程走势；高程跳变处颜色会突然变化。灰色为不检查的 lanelet（crosswalk 等），深灰为没有 `ele` 的部分。红圈为突兀点，`#k` 为下面画了纵断面的突兀点的编号，相距很近的共用一个标签。
- **下：纵断面**。高差最大的 `max_profiles` 个突兀点，每个一张小图：横轴为沿车道中心线的距离（0 为突兀点所在 lanelet 的起点，灰底为这条 lanelet），前面和后面各接一条前继 / 后继 lanelet；纵轴为高程。蓝线为左边界，橙线为右边界，红色标出突兀点：跳变为红色的一段，横坡用红色虚线连到另一侧边界。只画突兀点前后各约 40 m。

## 交互窗口（`isShow: true`）

报告和日志写完后，弹出一个**独立的窗口**（标题 `MapChecker altitude: <地图名>`，与热力图窗口分开，两者同时弹出），内容为上面趋势图中的俯视图，可以用工具栏缩放、平移。在其中单击（拖动不算）：

- **突兀点**（红圈，8 像素以内取最近的一个）：弹出细节小窗口 `MapChecker altitude detail`，显示这个突兀点所在车道的纵断面（与趋势图下半部分的画法相同，前后各约 40 m）和报告中的那一行，终端中也输出这一行。俯视图中用橙色标出这个点和它所在的 lanelet。
- **lanelet**（不在突兀点上）：细节小窗口中显示整条 lanelet 的纵断面（前后各接一条前继 / 后继），标出其上的突兀点，并给出长度、高程范围和起点到终点的高差。
- **空白处**：取消俯视图中的橙色标记，细节小窗口保留。

细节小窗口只有一个，再次单击时更新其中的内容；关掉后再单击会重新打开。关闭所有窗口后程序退出，退出码不变。没有图形界面（如 ssh）时不弹出窗口，只输出提示。临时不想弹出窗口时用 `--set altitude.isShow=false`。

## 参考：现有地图的统计（2026-10-21）

| 地图 | 高程范围 | 相邻两点坡度 中位数 / 99% | 横坡 中位数 / 99% | 默认参数下 step / spike / cross |
|---|---|---|---|---|
| `lanelet2_map.osm` | 475.2 ~ 510.8 m | 2.1% / 13.7% | 0.2% / 14.7% | 43 / 0 / 12 |
| `lanelet2_map_nly.osm` | 169.3 ~ 192.1 m | 0.0% / 15.7% | 0.0% / 10.7% | 57 / 5 / 6（其中 4 条在 `config/ignore.yaml` 中忽略） |

不同阈值下的突兀点数（`max_slope` 与 `max_cross_slope` 取相同的值）：

| `max_slope` / `min_step` | 0.15 / 0.1（默认） | 0.15 / 0.2 | 0.2 / 0.2 | 0.3 / 0.3 |
|---|---|---|---|---|
| `lanelet2_map.osm` | 55 | 35 | 20 | 6 |
| `lanelet2_map_nly.osm` | 68 | 63 | 41 | 16 |

`lanelet2_map_nly.osm` 中约一半的节点（5659 个中的 2849 个）高程都是 183.1772 m，很可能是没有实测高程时填的默认值；高差最大的几处跳变（6~8 m）都在实测高程的节点与这些节点的交界处。`lanelet2_map.osm` 中的突兀点高差多为 0.1~0.5 m，最大约 1 m（lanelet 534 与 12868 末端左右边界的高差）。

## 目录结构

```
ext/altitude/
  README.md
  config/config.yaml           本检查的配置
  script/check_altitude.py     检查脚本 (DEFAULT_CONFIG、configure()、check())
  script/_plot.py              高程趋势图和交互窗口 (matplotlib)，plot / isShow 为 true 时才导入
```

脚本只包含检查逻辑：地图（包括各节点的 `ele`，见 `Lanelet.left_z` / `right_z`）、AOI、忽略列表、日志和输出由 MapChecker 处理（见 [ext/README.md](../README.md)），趋势图的路径由 `ctx.output_prefix` 给出。检查只依赖 numpy，趋势图另外需要 matplotlib。
