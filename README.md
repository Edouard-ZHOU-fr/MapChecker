# MapChecker

Lanelet2 地图检查工具：找出地图中缺少 `turn_direction`（转向灯）和 `right_of_way`（路权）的地方，可选地合并 Autoware 官方检查的结果，并可用 logistic regression 给告警打分，把更可能是真问题的告警和可能是误报的告警区分开。

## 检查项

| 检查 | 内容 |
|---|---|
| 1. turn_direction | 弯道 / 转弯，以及与其他 lane 相交（交叉 / 汇入）的直行 lanelet 是否设置了 `turn_direction` |
| 2. right_of_way | 交叉 / 汇入的 lanelet 对是否有 `right_of_way` 规则元素；已有的 `right_of_way` 是否被优先 lanelet 引用、两条 lanelet 是否真的重叠（完全不重叠时输出 ERROR） |
| 3. Autoware（`autoware_checking: true`） | 运行 [utils/validate_autoware_map.py](utils/validate_autoware_map.py)（`autoware_lanelet2_validation` 的 Python 版）：地图加载错误、节点高度 `ele`、红绿灯、路由图中有冲突的 lanelet 的 `turn_direction`。其中 `turn_direction` 的结果合并到检查 1，同一 lanelet 只报告一次 |
| LR 打分（`Using_LR: true`） | 给检查 1、2 中缺少 `turn_direction` / `right_of_way` 的告警打分 `[WARNING p=0.77]`，并按分数从高到低输出，见 [告警打分](#告警打分-using_lr) |
| 扩展检查（`ext/<名称>/config/config.yaml` 中 `enable: true`） | 例如 crosswalk 在横向上最多超出道路多少，见 [扩展检查](#扩展检查-ext) |
| 热力图（`heatmap.enabled: true`） | 列出问题频发的区域，并生成热力图：按问题密度从蓝（少）到红（多）给地图着色，见 [问题热力图](#问题热力图-heatmap) |

## 依赖

| 用途 | 需要 |
|---|---|
| 检查 1、2，LR 打分 | Python 3.10+、`numpy`、`PyYAML` |
| 热力图 | `matplotlib` |
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
```

命令行参数优先于配置文件：

| 参数 | 对应配置 |
|---|---|
| `map`（位置参数） | `input` |
| `-c, --config FILE` | 配置文件，默认 `config/config.yaml` |
| `--turn-angle DEG` | `turn_angle` |
| `--turn-window M` | `turn_window` |
| `--min-overlap-area M2` | `min_overlap_area` |
| `--[no-]ignore-isolated-turns` | `ignore_isolated_turns` |
| `--[no-]straight-as-turn-light` | `straight_as_turn_light` |
| `--[no-]only-check-aoi` | `only_check_AOI` |
| `--[no-]autoware-checking` | `autoware_checking` |
| `--ignore-file FILE` | `ignore_file` |
| `--[no-]using-lr` | `Using_LR` |
| `--model-file FILE` | `model_file` |
| `--[no-]heatmap` | `heatmap.enabled` |
| `--[no-]show-heatmap` | `isShow` |
| `--no-color` | 不输出颜色 |

## 配置 `config/config.yaml`

相对路径以 `MapChecker/` 为基准。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `input` | `lanelet2_map.osm` | 要检查的地图 |
| `ignore_file` | `config/ignore.yaml` | 手动忽略的 lanelet，文件不存在时不忽略 |
| `model_file` | `config/model.yaml` | 告警打分模型，`Using_LR: true` 时使用 |
| `log.enabled` / `log.dir` | `false` / `logs` | 终端输出的同时写入日志 `logs/map_check_YYYYmmdd_HHMMSS.log` |
| `heatmap.enabled` | `false` | `true`：在报告末尾列出问题频发的区域，并在 `log.dir` 生成同名的热力图 `map_check_YYYYmmdd_HHMMSS.png`，需要 matplotlib |
| `heatmap.radius` | `30.0` | 区域半径（米）：相距不超过 `radius` 的问题算同一区域，热力图的扩散范围约为 `radius` |
| `heatmap.top` | `10` | 最多标注几个区域（至少有 2 条问题才算） |
| `turn_angle` | `30.0` | 判定为弯道的最小航向变化（度） |
| `turn_window` | `30.0` | 计算航向变化的最大弧长窗口（米），长 lanelet 上的缓弯不算弯道 |
| `min_overlap_area` | `1.0` | 判定两条 lanelet 重叠的最小面积（m²） |
| `ignore_isolated_turns` | `false` | `true`：不与其他 lane 交叉 / 汇入的弯道（分叉不算）不检查 `turn_direction` |
| `straight_as_turn_light` | `true` | `true`：`straight` 也算设置了转向灯，弯道和相交的直行 lanelet 都必须设置 `turn_direction`，不检查方向<br>`false`：检查方向与几何是否一致，直行 lanelet 不要求 `straight` |
| `only_check_AOI` | `false` | `true`：只检查完全位于地图 AOI（`subtype=AOI` 的闭合 way）内的 lanelet；地图中没有 AOI 时检查整张地图 |
| `autoware_checking` | `false` | `true`：运行检查 3，需要 ROS |
| `isShow` | `false` | `true`：报告结束后弹出热力图窗口（可缩放、平移），关闭窗口后程序退出；只在 `heatmap.enabled: true` 时有效，没有图形界面（如 ssh）时只输出提示 |
| `Using_LR` | `false` | `true`：用 `model_file` 给告警打分，各检查中按分数从高到低输出；模型文件不存在时报错 |

默认值是配置文件中缺少该项时使用的值；仓库里的 `config/config.yaml` 打开了 `log`、`heatmap`、`ignore_isolated_turns`、`only_check_AOI`、`autoware_checking` 和 `Using_LR`。

## 忽略列表 `config/ignore.yaml`

确认不需要修改的 lanelet 写在这里，之后不再输出对应的告警，并在报告末尾列出每条忽略屏蔽了多少问题。

```yaml
all:            [13252: "roule pas", 17618]   # 所有检查都忽略
turn_direction: [614: 主路弯道，不需要转向灯]   # 只在检查 1 中忽略
right_of_way:   []                            # 只在检查 2 中忽略 (涉及该 lanelet 的重叠对和 right_of_way 规则元素)
autoware:       []                            # 只在检查 3 中忽略 (该 lanelet 引用的红绿灯问题)
```

每项可以只写 lanelet id，也可以写成 `id: 原因`。

## 输出

- `[WARNING]`（黄色）：需要确认的问题；`[ERROR]`（红色）：确定有错，例如 `right_of_way` 的两条 lanelet 完全不重叠、地图加载错误。
- 检查 1 按 lanelet id 顺序输出。检查 2 先输出已有 `right_of_way` 规则元素本身的问题，再输出缺少 `right_of_way` 的告警，按区域（通常是一个路口）分组，每组给出中心坐标和涉及的 lanelet。
- `Using_LR: true` 时顺序不同，见 [输出顺序](#输出顺序)。
- 末尾的 Summary 列出各检查项的告警数，Hotspots 列出问题频发的区域（`heatmap.enabled: true` 时，见 [问题热力图](#问题热力图-heatmap)），Ignored lanelets 列出忽略列表的使用情况（不在地图中、在 AOI 外、屏蔽了几条）。
- 退出码：`0` 没有问题，`1` 有未忽略的 WARNING / ERROR，`2` 参数或环境错误。可以用于 CI。

Autoware 检查也可以单独运行（需要 source ROS）：

```bash
python3 utils/validate_autoware_map.py lanelet2_map.osm
```

## 问题热力图 (`heatmap`)

一张地图的告警常常集中在几个路口。打开 `heatmap.enabled` 后，报告末尾会列出问题最多的区域，并在日志目录生成与日志同名的 `.png`，方便先处理问题最集中的地方：

```
=== Hotspots (at least 2 issues within 30 m) ===
#1  center (67932.7, 6497.8): 16 issues (turn_direction 8, right_of_way 8), lanelets: 3973 4060 4081 4095 4102 4107 4134 4167 4202 4313
#2  center (68200.6, 6151.5): 13 issues (right_of_way 9, turn_direction 4), lanelets: 2690 3668 3676 3690 3698 5595 6165 6811 6866
...
Heatmap: .../logs/map_check_20261016_093357.png
```

- **位置**：检查 1 的告警取 lanelet 中心线的中点（即日志中的 `middle`），检查 2 缺少 `right_of_way` 的告警取重叠区的中心，其他告警取相关 lanelet 中点的平均；与 lanelet 无关的告警（节点缺少高度、地图加载错误）不参与。被忽略的告警不参与。
- **颜色**：高斯核密度（sigma = `radius / 2`），数值约为附近的问题数。只有 1 条孤立问题的地方为蓝色，问题越密集越接近红色；没有问题的地方透明，露出地图。
- **区域**：每次取 `radius` 米内剩余问题最多的位置作为一个区域，区域内的问题不再计入之后的区域，直到找到 `heatmap.top` 个区域或剩下的位置都不足 2 条问题。图中用圆圈和 `#排名 问题数` 标注，与日志中的编号一致。
- **按 p 加权**（`Using_LR: true`）：有分数的告警计 p 条，没有分数的告警和 ERROR 计 1 条，颜色、区域的判定和排名都用加权后的条数，即预计的真问题数；模型认为多半是误报的区域颜色更淡、排名靠后或不再列出。日志中同时给出条数和加权值，图中标注加权值：
  ```
  === Hotspots (at least 2 p-weighted issues within 30 m) ===
  #1  center (67936.5, 6494.3): 14 issues, p-weighted 9.4 (right_of_way 9, turn_direction 5), lanelets: 3973 3980 4060 4107 4134 4167 4202 4313
  ```
- **弹出窗口**：`isShow: true` 时，报告和日志写完后弹出热力图窗口，可以用工具栏缩放到某个区域查看；关闭窗口后程序退出，退出码不变。
  - 单击问题点：显示该处问题的 lanelet 编号、检查项和 p（同一位置有多条时都列出），相关 lanelet 用橙色描边，终端同时输出这些问题在日志中的那一行（方便复制）。
  - 单击其他位置：显示点击处的 lanelet 编号（重叠处可能有多条）；单击空白处隐藏。
  - 缩放 / 平移模式下也可以直接单击，拖动不算单击。
- **范围**：`only_check_AOI: true` 时只画 AOI 的范围（虚线为 AOI 边界），否则画整张地图。问题点按检查项用不同形状标出：圆形 turn_direction、方形 right_of_way、三角形 autoware。扩展检查（`ext/`）的问题只用绿色圆点标出，不参与着色和区域的判定，Hotspots 中单独列出其条数。

## 告警打分 (`Using_LR`)

### 作用

一张地图常有上百条告警，其中一部分是地图团队认为不需要修的。打开 `Using_LR` 后，检查 1、2 中缺少 `turn_direction` / `right_of_way` 的告警会带上分数 `p`，即该告警为真问题的概率，也就是**地图团队之后会修复它的概率**。Summary 中会列出 `p < 0.5` 的告警数。分数只用来决定先看哪些告警，所有告警仍然全部输出。

### 输出顺序

终端和日志中，每个检查内带分数的告警按 `p` 从大到小输出（`p` 相同时按原来的顺序）：

- 检查 1：没有分数的告警（例如 `turn_direction` 的值不合法）在最前面，之后是按 `p` 排序的告警。
- 检查 2：先输出已有 `right_of_way` 规则元素本身的问题（没有分数），再列出所有区域，最后是所有区域缺少 `right_of_way` 的告警一起按 `p` 排序，每条注明所在区域 `(area k)`。

```
=== Check 1: turn_direction on curves / turns (angle threshold 30°, window 30 m) ===
[WARNING p=0.77] lanelet 12528: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
[WARNING p=0.77] lanelet 17361: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
...
[WARNING p=0.07] lanelet 13646: intersects other lanes without turn_direction (geometry straight, ...) | ...

=== Check 2: right_of_way on overlapping lanelets (min overlap area 1.0 m²) ===
-- Area 1, center (30772.0, 10528.6), lanelets: 9 14 445 450 10735 10754
...
-- Area 26, center (30572.3, 11333.2), lanelets: 16541 16550 17931 17936
[WARNING p=0.84] lanelet 12494 / 19041 (area 12): merge overlap (~13.5 m² at (31465.0, 9665.5)) without right_of_way
[WARNING p=0.69] lanelet 14182 / 17379 (area 22): merge overlap (~12.9 m² at (30830.3, 11337.8)) without right_of_way
...
```

### 计算方法

模型是 logistic regression：`p = 1 / (1 + e^-z)`，`z = intercept + Σ coef × (特征 - mean) / scale`。这些数字保存在 `config/model.yaml` 中，检查时直接计算，不需要 scikit-learn。

`model.yaml` 还记录了每个特征在训练数据中的取值范围（`min` / `max`）。如果某个特征在训练数据中始终不变，而一条告警的值不同（例如训练数据中没有只由 Autoware 检出的告警，`isolated` 始终为 0），说明模型没有见过这种告警，这条告警不打分，而不是给出一个外推的分数。

### 训练 / 更新模型

训练数据来自地图仓库的 git 历史：在每个仓库地图的每个历史版本上运行检查 1、2（`config/dataset.yaml` 中 `autoware_checking: true` 时同时运行 Autoware 检查），跟踪每条告警后来是被修复、被忽略还是一直保留。

只由 Autoware 检出的 `turn_direction` 告警（例如分叉处的 lanelet，Autoware 的路由图把分叉也算作冲突）和几何检查的告警很不一样，必须在训练数据中出现，模型才能正确打分。所以生成训练数据时默认运行 Autoware 检查，需要先 source ROS；关闭时检查中不给这类告警打分（见 [计算方法](#计算方法)）。

1. 在 [config/dataset.yaml](config/dataset.yaml) 中列出地图仓库，以及该仓库对应的 ignore 文件（如果有）。
2. 生成训练数据并训练、评估，生成 `config/model.yaml`：

   ```bash
   python3 train_model.py                # --C 0.1 可以加强正则化
   python3 train_model.py --skip-build   # 不重新生成训练数据，直接用已有的 data/ 训练（例如只调整 --C）
   ```

   `train_model.py` 先用 [utils/build_dataset.py](utils/build_dataset.py) 重放地图仓库的历史，生成训练数据 `data/turn_direction.csv` 和 `data/right_of_way.csv`（已加入 `.gitignore`），再用 [utils/tranning.py](utils/tranning.py) 训练。

地图仓库有新的提交、ignore 文件更新了，或者修改了 [utils/features.py](utils/features.py) 中的特征后，重新运行 `train_model.py` 即可。检查阈值（`turn_angle` 等）使用 `config/config.yaml` 中的设置，修改阈值后也应重新训练。

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

**真正交叉与边缘相碰**：几何检查把重叠面积 ≥ `min_overlap_area` 的非分叉 lanelet 对都当作交叉。其中一部分只是边缘相碰，例如分叉起点的弯道蹭到对向车道、狭窄道路上的平行车道沿路重叠。特征中只有至少一条 lanelet 的中心线进入另一条 lanelet 的范围（车辆会驶入对方车道）才算真正交叉（`utils/geometry.py` 的 `centerline_enters`）。边缘相碰的重叠很窄：平均宽度的中位数为 0.5~0.9 m，真正交叉为 1.6~2.0 m。

节点高度差也试过，后来去掉了：它随地图版本的处理方式（如高度平滑）而变，模型学到的是版本的差别。

### 领域知识约束

有些经验是确定的方向，例如 turn_direction 告警中：

- 重叠过长（`log_overlap_length`）是道路狭窄造成的，不是真的交叉 / 汇入；
- 有红绿灯的路口（`traffic_light`）的告警多为误报；
- 没有路口（`isolated`，例如分叉）的告警多为误报。

这类特征列在 [utils/features.py](utils/features.py) 的 `NEGATIVE_FEATURES` 中，训练时系数限制为 ≤ 0：**方向由经验决定，降低多少由数据决定**。数据不支持时系数会停在 0（`train_model.py` 输出 `at the bound`），即不起作用，但不会反过来提高 p。sklearn 的 `LogisticRegression` 不支持限制系数，训练时（`utils/tranning.py`）用 scipy 的 L-BFGS-B 求解同样的目标函数，不加限制时结果与 sklearn 一致。

把经验告诉模型有三种方法，效果不同：

| 方法 | 做法 | 效果 |
|---|---|---|
| 加特征 | 在 `FEATURES` 中加入能描述这种情况的特征 | 数据决定方向和大小；样本少时不稳定 |
| 限制系数方向 | 加入 `NEGATIVE_FEATURES` | 方向固定，数据决定大小；数据不支持时不起作用 |
| 改标签 | 把这类告警写进对应仓库的 ignore 文件（`config/dataset.yaml` 中的 `ignore_file`），成为 label 0 | 模型一定会学到；但如果所有这类告警都是误报，直接写成规则更简单 |

目前 `traffic_light` 只有 14 个训练样本（其中 3 条被修复），降低的幅度不可靠；要让它更可靠，可以把各地图中确认不需要的红绿灯路口告警写进 ignore 文件后重新训练。

### 当前效果（2026-10-15，10 个仓库、256 个地图版本）

评估用 leave-one-map-out：每次留出一个仓库，用其他仓库训练后给它打分，相当于在一张新地图上的效果。AUC 是随机取一条真问题和一条误报、真问题得分更高的概率，0.5 等于乱猜。

| 检查项 | 训练样本 | 模型 AUC | 最好的单个特征 AUC |
|---|---|---|---|
| turn_direction | 632（218 条修复） | 0.77 | 0.72（`isolated`） |
| right_of_way | 359（105 条修复） | 0.72 | 0.62 |

`train_model.py` 会输出每个仓库的结果和模型系数，重新训练后以它的输出为准。

模型学到的规律：

- **turn_direction**：
  - 没有路口的告警（`isolated`）最不会被修，是最有用的特征；把边缘相碰排除在交叉之外后，模型 AUC 从 0.70 提高到 0.77。
  - 与其他 lanelet 交叉越多，越不会被修，即团队通常不给相交的直行 lanelet 标 `straight`。
  - 重叠越长越不会被修：buc、cern 中未修的告警有一半以上重叠超过 15 m，修复了的只有 2%~11%；重叠 30 m 时 odds 约为 5 m 时的 0.77 倍。
  - 红绿灯路口的 odds 约为其他的 0.26 倍（平均 p 从 0.41 降到 0.19），但只有 14 个样本。
- **right_of_way**：重叠面积越大越会被修，面积很小的重叠多数没有处理。

### 局限

- label 表示 "地图团队之后是否修复了它"，学到的是团队的习惯，不等于 Autoware 是否需要。例如 Autoware 要求路由图中有冲突的直行 lanelet 也设置 `turn_direction`，但模型会给这类告警较低的分数。
- right_of_way 只有 3 个仓库可以评估，结果不稳定。
- 正在修改中的地图（如 Noisy-le-Roi）里 "一直没修" 的告警可能只是还没修到，label 0 不可靠；该仓库 turn_direction 的 AUC 只有 0.56。它的样本最多（278 条），也拖低了其他地图的效果。
- 发现的规律如果足够明确，更好的做法是把它变成规则或阈值，而不是一直依赖模型。例如几何检查本身仍把边缘相碰当作交叉：检查 1 会报这类弯道，检查 2 会报 "crossing overlap without right_of_way"（le_plessis 中 37 条里有 21 条是边缘相碰），目前只是在打分中降低了它们的 p。

## 扩展检查 (`ext/`)

`ext/` 下每个子目录是一项独立的扩展检查，有自己的脚本（`script/`）、配置（`config/config.yaml`）和 README.md。其配置中 `enable: true` 时，`check_lanelet_map.py` 在检查 1~3 之后运行它：

- 结果写入同一份输出和日志。
- Summary 中列出该检查项的告警数，并计入退出码。
- 热力图中只用绿色圆点标出问题点，不参与着色和区域的判定（所有扩展检查都一样）。

`config/ignore.yaml` 中 `all` 下的 lanelet 在扩展检查中也忽略。扩展检查自己的忽略列表写在它的配置中，在 Ignored lanelets 中以 `[检查项名称]` 列出。`enable: false` 的扩展不运行，也不导入。

| 扩展 | 内容 |
|---|---|
| [crosswalk](ext/crosswalk/README.md) | crosswalk 在横向上最多超出道路多少，超出 `max_extension` 时报 WARNING |

新增扩展检查：在 `ext/<名称>/script/` 下的 .py 文件中提供 `CATEGORY`、`load_config(path)` 和 `check(lanelets, reg_elems, aois, cfg, out)`，配置中写 `enable` 和 `ignore`，见 [utils/ext.py](utils/ext.py)。

## 目录结构

```
MapChecker/
  check_lanelet_map.py        入口
  train_model.py              生成训练数据并训练 logistic regression，生成 config/model.yaml
  install.sh                  安装依赖 (ROS / Autoware 除外)
  ext/                        扩展检查 (配置中 enable 为 true 时运行)，每项有自己的 README.md
    crosswalk/                crosswalk 在横向上最多超出道路多少
  config/
    config.yaml               检查配置
    ignore.yaml               手动忽略的 lanelet
    dataset.yaml              train_model.py 的配置 (地图仓库列表、标注规则)
    model.yaml                告警打分模型 (train_model.py 生成)
  utils/
    lanelet_map.py            读取 Lanelet2 OSM 地图
    geometry.py               几何工具 (中心线、航向变化、重叠面积、AOI)
    topology.py               前后继关系和 lanelet 重叠
    check_turn_direction.py   检查 1
    check_right_of_way.py     检查 2
    check_autoware.py         检查 3 的报告
    validate_autoware_map.py  Autoware 检查 (需要 ROS)，也可单独运行
    ext.py                    加载启用的扩展检查
    features.py               告警的特征
    model.py                  读取模型并打分
    ignore.py                 忽略列表
    config.py                 读取 config.yaml
    printer.py                终端 / 日志输出
    heatmap.py                问题热力图和问题频发的区域
    build_dataset.py          从地图仓库的 git 历史生成训练数据
    tranning.py               训练 logistic regression
  libs/                       install.sh 安装的第三方库 (不提交)
  data/                       训练数据 (不提交)
  logs/                       日志和热力图 (不提交)
```
