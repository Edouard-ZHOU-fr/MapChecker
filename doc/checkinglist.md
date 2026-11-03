# MapChecker 检查清单

中文 | [Français](checkinglist.fr.md)

本文列出 `check_lanelet_map.py` 目前检查的所有项目，以及每条 WARNING / ERROR 在什么条件下输出。内容按 2026-10-30 的代码整理；文中的"当前值"取自 `config/config.yaml` 和 `ext/<名称>/config/config.yaml`，修改配置后以配置文件为准。

## 总览

| # | 检查项（Summary 和忽略列表中的名称） | 检查对象 | 什么时候运行 | 级别 |
|---|---|---|---|---|
| 1 | `turn_direction` | 车辆 lanelet | 总是运行（`--only-ext` 时跳过） | WARNING |
| 2 | `right_of_way` | 重叠的车辆 lanelet 对、`right_of_way` 规则元素 | 总是运行（`--only-ext` 时跳过） | WARNING / ERROR |
| 3 | `autoware` | 整个地图文件 | `autoware_checking: true`（当前 `false`），需要 source ROS | WARNING / ERROR |
| 扩展 | `altitude` | 车辆 lanelet 边界节点的高程 `ele` | `enable: true`（当前关闭） | WARNING |
| 扩展 | `crosswalk` | `subtype=crosswalk` 的 lanelet | `enable: true`（当前开启） | WARNING |
| 扩展 | `stopline` | `type=stop_line` 的 way | `enable: true`（当前开启） | WARNING / ERROR |
| 扩展 | `border_sharing` | 并排的车辆 lanelet 对 | `enable: true`（当前关闭） | WARNING |
| 扩展 | `trafficlight` | `subtype=traffic_light` 的规则元素 | `enable: true`（当前开启） | WARNING / ERROR |
| 扩展 | `_template` | 车辆 lanelet 的标签 | 不自动运行，只在 `--ext _template` 时运行 | WARNING |

## 通用规则

- **车辆 lanelet**：`subtype` 为 `road`、`highway` 或 `road_shoulder` 的 lanelet。检查 1、2、altitude 和 border_sharing 只检查车辆 lanelet；trafficlight 只检查车辆 lanelet 对红绿灯的使用。
- **级别**：`[WARNING]` 需要人工确认；`[ERROR]` 确定有错。
- **退出码**：`0` 没有问题；`1` 有未被忽略的 WARNING / ERROR；`2` 参数、配置或环境错误。
- **忽略列表**（`config/ignore.yaml`，以及各扩展配置中的 `ignore`）：
  - `all` 下的 lanelet 在所有检查中忽略，其他键只在对应检查中忽略。
  - 一条问题涉及多条 lanelet 时（如检查 2 的 lanelet 对、altitude 中相邻车道共用的边界），只要其中一条被忽略，这条问题就不输出。
  - 与具体 lanelet 无关的问题（节点缺少 `ele`、地图格式 / 解析错误）不能忽略。
- **AOI**（`only_check_AOI: true`，当前 `false`）：
  - 只报告所涉及的 lanelet 全部完全位于 AOI（`subtype=AOI` 的闭合 way）内的问题。
  - 重叠、前后继等几何关系仍用整张地图计算。
  - 地图中没有 AOI 时不做任何检查，直接结束（退出码 `0`）。
- **只输出提示、不算问题**：
  - 无法解析的 lanelet（缺少左 / 右边界、边界 way 不存在、引用了不存在的节点）：在报告开头的 `Lanelets not loaded (incomplete, not checked)` 中列出，不参与任何检查。
  - 不与任何车道重叠的 crosswalk、没有被任何规则元素引用的停止线和 `traffic_light` way、只被非车辆 lanelet 引用的红绿灯（如 crosswalk 的行人信号灯）：只列出 id。
  - `Using_LR` 的打分 `p`、Hotspots、热力图和减速 masque（`masque.enabled: true`，当前 `false`）：只给已输出的问题打分、排序、统计，不会增加或隐藏报告中的任何问题。`map_min_p`（当前 0.5）只决定热力图中画出哪些问题，报告和日志中仍全部列出。

## 基本概念（检查 1、2 共用）

| 概念 | 判定 |
|---|---|
| 弯道 | 中心线（左右边界的平均）在任意一段长度不超过 `turn_window`（当前 30 m）的区间内，航向变化 ≥ `turn_angle`（当前 30°）。航向先做约 2 m 的滑动平均，去掉节点处的折角噪声；比 `turn_window` 短的 lanelet 就是整条的转角。正值为左转，负值为右转 |
| 直行 | 不是弯道 |
| 重叠 | 两条车辆 lanelet 的重叠面积 ≥ `min_overlap_area`（当前 1.0 m²，按 0.5 m 网格采样估算）。两条都是直行、或方向相反（重叠处航向差 > 150°）的 lanelet，重叠如果长度 ≥ `parallel_min_length`（当前 30 m），且沿重叠段两条中心线始终相距 ≥ `parallel_min_gap`（当前 1.8 m），算平行车道（如狭窄或变窄的道路上沿路重叠），不算重叠。前后相接（一条是另一条的前继 / 后继）和左右相邻（共用边界 way）的不算重叠 |
| 分叉 diverge | 重叠的两条 lanelet 有共同的前继，或起点相同 |
| 汇入 merge | 重叠的两条 lanelet 有共同的后继，或终点相同 |
| 交叉 crossing | 重叠，但既不是分叉也不是汇入 |
| 相交 | 与其他 lanelet 交叉或汇入；分叉不算相交 |

## 检查 1：`turn_direction`

检查对象：车辆 lanelet；启用检查 3 时，还包括 Autoware 报告的 lanelet（即使其 `subtype` 不是车辆类型）。

当前配置为 `straight_as_turn_light: true`、`ignore_isolated_turns: true`，此时报告以下问题：

| 问题 | 输出条件 | 输出信息 |
|---|---|---|
| 值不合法 | 有 `turn_direction`，但值不是 `left` / `right` / `straight`。这条 lanelet 不再做下面的检查 | `turn_direction="..." is not a valid value (left/right/straight)` |
| 弯道没有转向灯 | 弯道，且没有 `turn_direction` | `curve without turn_direction (geometry turns right, suggest turn_direction=right)` |
| 相交的直行 lanelet 没有 `turn_direction` | 直行、相交，且没有 `turn_direction` | `intersects other lanes without turn_direction (geometry straight, suggest turn_direction=straight)` |
| 只由 Autoware 检出 | 启用检查 3；在 Autoware 路由图中与其他 lanelet / area 冲突、没有 `turn_direction`，而上面的几何检查没有报告它（例如分叉、重叠面积小于 `min_overlap_area`、平行车道、被 `ignore_isolated_turns` 跳过的弯道） | `conflicts with <id> in Autoware routing graph without turn_direction` |

当前配置下**不报告**：

- 不与其他 lanelet 交叉 / 汇入的弯道，例如只是分叉出去的弯道（`ignore_isolated_turns: true`）。
- 方向与几何不一致的弯道：只要设置了合法值就通过，不检查方向（`straight_as_turn_light: true`）。
- 不与其他 lanelet 相交的直行 lanelet。

如果改成 `straight_as_turn_light: false`，检查方向，上表中间两行换成：

| 问题 | 输出条件 | 输出信息 |
|---|---|---|
| 弯道没有转向灯 | 弯道，且没有 `turn_direction` | `curve without turn_direction (...)` |
| 弯道标了 straight | 弯道，`turn_direction=straight` | `curve tagged turn_direction=straight, no turn signal (geometry turns left)` |
| 方向相反 | 弯道，`turn_direction` 与几何转向相反 | `turn_direction=left is opposite to geometry (turns right)` |
| 直行却标了左右 | 直行、相交，`turn_direction` 为 `left` / `right` | `intersects other lanes, turn_direction=left but geometry is straight` |

此时相交的直行 lanelet 不要求标 `straight`。如果把 `ignore_isolated_turns` 改成 `false`，不相交的弯道也会检查。

每行末尾的 `| middle (x, y), angle +45°, length 12.3 m` 是中心线中点、航向变化和长度。

## 检查 2：`right_of_way`

### 2a. 已有 `right_of_way` 规则元素本身的问题

对地图中每个 `subtype=right_of_way` 的规则元素：

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| WARNING | 引用的 lanelet（`right_of_way` 或 `yield` 成员）不在地图中 | `right_of_way <id>: referenced lanelet <id> does not exist` |
| WARNING | 缺少 `right_of_way` 成员或 `yield` 成员 | `right_of_way <id>: missing right_of_way or yield member` |
| WARNING | 优先 lanelet（`right_of_way` 成员）自己没有引用这个规则元素，Autoware 会忽略这条规则 | `right_of_way <id>: priority lanelet <id> does not reference this regulatory element (ignored by Autoware)` |
| ERROR | 优先 lanelet 与让行 lanelet 完全不重叠（用 0.1 m 的细网格确认），同时给出两者的最小距离 | `right_of_way <id>: lanelet <r> (right_of_way) and <y> (yield) do not overlap at all (min distance 3.2 m)` |

### 2b. 缺少 `right_of_way`

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| WARNING | 两条车辆 lanelet 交叉或汇入，且没有任何 `right_of_way` 规则元素把其中一条列为 `right_of_way`、另一条列为 `yield`（哪条优先都可以） | `lanelet <a> / <b>: merge overlap (~13.5 m² at (x, y)) without right_of_way`，交叉时为 `crossing overlap ...` |

- 两条 lanelet 都引用了红绿灯时仍然报告，只在末尾加上 `[both controlled by traffic lights]`。
- 告警按区域（`-- Area k`）分组：重叠关系连在一起的 lanelet 为一个区域，通常就是一个路口。
- **不报告**：分叉的 lanelet 对、前后相接或左右相邻的 lanelet、重叠面积小于 `min_overlap_area` 的、平行车道（两条直行或对向的 lanelet 重叠 ≥ `parallel_min_length` 且中心线始终相距 ≥ `parallel_min_gap`）。

## 检查 3：`autoware`（`autoware_checking: true` 时）

运行 `utils/validate_autoware_map.py`，即 `autoware_lanelet2_validation` 的 Python 版：用 MGRS 投影，按 Germany / Vehicle 交规建立路由图。

| 级别 | 问题 | 输出条件 |
|---|---|---|
| ERROR | `format_version` 不合法 | `MetaInfo` 中没有 `format_version`；不是 `2`、`2.1`、`2.1.0` 这样的数字版本号；或主版本大于 2 |
| ERROR | 地图解析错误 | Lanelet2 读取地图时报告的每一条错误（`parse error: ...`），或读取直接失败（`failed to load map: ...`）。有解析错误时跳过下面的 `turn_direction` 检查 |
| WARNING | 节点缺少高程 | 地图文件中有 `<node>` 没有 `ele` 标签（不只是 lanelet 的节点）。合并为一条，列出前 10 个节点 id |
| WARNING | 红绿灯没有 light_bulb | lanelet 引用的红绿灯规则元素没有 `light_bulbs` 成员 |
| WARNING | light_bulb 缺少 id | `light_bulbs` 中的 linestring 没有 `traffic_light_id` 标签 |
| WARNING | 红绿灯是多边形 | 红绿灯（`refers` 成员）是 polygon，只支持 linestring |
| WARNING | 红绿灯缺少高度 | 红绿灯（`refers` 成员）没有 `height` 标签 |
| WARNING（合并到检查 1） | 冲突的 lanelet 没有 `turn_direction` | 车辆可以通行、在路由图中与其他 lanelet / area 冲突，且没有 `turn_direction` 标签。几何检查已经报告的 lanelet 不重复报告 |

- 格式、解析错误和节点高程是整张地图的问题，不受 AOI 和忽略列表影响。
- 红绿灯问题只在引用它的 lanelet 位于 AOI 内时报告，可以用 `config/ignore.yaml` 中的 `autoware:` 忽略。

## 扩展检查 `altitude`

检查对象：车辆 lanelet 左右边界上的节点；相邻车道共用的边界只检查一次。坡度 = 高差 / 水平距离。

当前值：`max_slope: 0.30`、`max_cross_slope: 0.20`、`min_step: 0.5` m。高差小于 `min_step` 的一律不报。

| 问题 | 输出条件 | 输出信息 |
|---|---|---|
| 跳变 step | 边界上相邻两个节点的高差 ≥ `min_step`，且坡度 > `max_slope` | `step between nodes A -> B: 183.18 -> 174.72 m (...)` |
| 尖峰 spike | 一个节点前后两段都是跳变，且方向相反（比前后两点都高或都低）。只报告这个节点，不再报告两段跳变 | `spike at node N: 0.62 m above nodes A and B (...)` |
| 横坡 cross | lanelet 一侧边界上的节点，与另一侧边界上最近点（高程沿边界线性插值）的高差 ≥ `min_step`，且大于 `max_cross_slope` × 两者的距离。每条 lanelet 只报告高差最大的点；这个点或另一侧对应的节点已报告为跳变 / 尖峰时不再报告 | `cross slope at node N: 4.75 m below the right boundary 2.31 m away (...)` |
| 节点没有 `ele` | lanelet 边界上有节点没有 `ele`。每条 lanelet 一条，这些节点不参与上面的检查 | `lanelet <id>: no elevation (ele) on nodes ..., not checked` |

突兀点按高差从大到小输出，编号 `#k` 与高程趋势图中的编号一致。

## 扩展检查 `crosswalk`

检查对象：`subtype=crosswalk` 的 lanelet。当前值：`max_extension: 0.5` m。

| 情况 | 判定 | 输出 |
|---|---|---|
| 超出道路过多 | crosswalk 某一端超出道路的长度 > `max_extension`。道路是与 crosswalk 重叠的所有车辆 lanelet 的并集；沿 crosswalk 方向测量，在宽度方向每隔 0.1 m 取一条线，沿线每 0.01 m 采样，每端取所有线中的最大值 | `[WARNING] crosswalk <id>: extends up to 2.10 m beyond the road (max 0.50 m) \| ...` |
| 没有超出过多 | ≤ `max_extension`；端点落在道路内时这一端为 0 | 只输出测量值，不算问题 |
| 不与车道重叠 | 不与任何车辆 lanelet 重叠 | 只在末尾列出 id，不检查 |

## 扩展检查 `stopline`

检查对象：地图中所有 `type=stop_line` 的 way，不论是否被规则元素引用。本检查没有参数。

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| WARNING | 超过 2 个节点（折线）。给出离两端连线最远的中间节点及其距离：距离为 0 表示只是落在直线上的多余节点，删掉即可 | `stop_line <id>: 3 points (polyline), should be a straight line of 2 points; node N is 1.00 m off the line between its ends (length 7.3 m) \| lanelet <id> at (x, y)` |
| ERROR | 少于 2 个节点 | `stop_line <id>: only 1 point(s), a stop line needs 2 \| ...` |
| ERROR | 引用了地图中不存在的节点 | `stop_line <id>: references node(s) ... missing from the map \| ...` |

- `| lanelet ...` 为通过规则元素的 `ref_line` 引用这条停止线的 lanelet，没有时为 `not referenced by any regulatory element`。
- 忽略列表中既可以写停止线的 way id，也可以写引用它的 lanelet id。
- `only_check_AOI: true` 时只检查完全位于 AOI 内的停止线。

## 扩展检查 `border_sharing`

检查对象：并排的两条车辆 lanelet"面对面"的边界。同向时比较一条的右边界与另一条的左边界，对向时比较两条的左边界或两条的右边界。当前值：`max_distance: 0.1` m、`min_fraction: 0.8`、`min_length: 2.0` m。

沿一条边界每 0.5 m 取一个点。点到另一条边界的距离 ≤ `max_distance`，且两条边界方向平行（对向时反平行，夹角约 25° 以内）时，这个点算作重合。

| 情况 | 判定 | 输出 |
|---|---|---|
| 同向车道没有共用边界 | 两条边界是不同的 way，重合部分 ≥ `min_length`，且 ≥ 较短那条边界长度的 `min_fraction` | `[WARNING] lanelet <a> left (way ..) / lanelet <b> right (way ..): boundaries coincide over 21.0 m (100% of the shorter) but are different ways, gap 0.26 m between the lanes; they are not adjacent in the routing graph, no lane change between them \| at (x, y)` |
| 对向车道没有共用中心线 | 同上，两条车道方向相反 | `[WARNING] lanelet <a> left (way ..) / lanelet <b> left (way ..), opposite directions: boundaries coincide over ... but are different ways, ...; opposite lanes should share one center line \| at (x, y)` |
| 已共用 | 两条边界是同一个 way | 只计数，不报告 |
| 只是局部靠近 | 重合部分达不到上面的长度 / 比例，例如路口里互相经过的转弯车道 | 不报告 |

- 两条线的相对位置取重合部分有符号距离的中位数：`drawn on top of each other`（重合在一起）、`gap x m between the lanes`（车道之间有缝隙）、`overlap x m between the lanes`（车道互相重叠）。
- `only_check_AOI: true` 时只检查两条都完全位于 AOI 内的车道对。

## 扩展检查 `trafficlight`

检查对象：`subtype=traffic_light` 的规则元素。按 Autoware 的 traffic_light 模块和 milla 的相机匹配（`milla_traffic_light_matching`）实际使用红绿灯的方式检查，每条问题的后果见 [ext/trafficlight/README.md](../ext/trafficlight/README.md)。

当前值：`max_facing_angle: 40`°、`min_forward_distance: 1.0` m、`min_height: 0.5` / `max_height: 5.0` m、`stop_line_extend_length: 5.0` m、`same_direction_angle: 45`°、`check_light_bulbs: false`（`bulb_max_offset: 1.0` m）。

| 概念 | 判定 |
|---|---|
| 停车点、车道方向 | 车道中心线与停止线（第一个 `ref_line`）的第一个交点，以及中心线在该处的方向。这条 lanelet 上没有交点时，依次在它的后继、前继上找 |
| 灯能被识别 | `traffic_light_type=camera` 的灯：灯的方向（linestring 方向 + 90°，即从停止线看过去从左往右画）与车道方向相差 ≤ `max_facing_angle`，且灯框中心沿车道方向在停车点前方 ≥ `min_forward_distance`。`v2x` 的灯总是算能识别 |
| 灯高 | 灯框中心（灯 linestring 的高程 + `height` / 2）比停止线（节点 `ele` 的平均值）高多少米。缺少 `ele` 时不检查 |

**识别与停止线**（每条使用这个红绿灯的车辆 lanelet 分别检查）：

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| ERROR | 从这条车道上没有一个灯能被识别：相机灯都朝向不对或不在停车点前方，又没有 `v2x` 的灯 | `traffic_light <id> \| lanelet <id>: no light can be recognized from the lanelet (light <w>: faces away, facing error +175° (max 40°); drawn right -> left, reverse its node order): ...`；没有 `camera` / `v2x` 的灯时括号中为 `no light with traffic_light_type camera or v2x` |
| WARNING | 有的灯能识别，但某个相机灯从某些车道上识别不到。每个灯一条 | `traffic_light <id>: traffic light way <w> cannot be recognized from lanelet <ids> (<原因>); only the other light(s) of the regulatory element are used there` |
| ERROR | 停止线不穿过这条车道的中心线（包括前后继），两端各延长 `stop_line_extend_length` 后也不穿过 | `traffic_light <id> \| lanelet <id>: stop line <s> does not cross the lanelet (nor its predecessors / successors), even extended by 5 m: ...` |
| WARNING | 停止线只有延长后才穿过车道 | `traffic_light <id> \| lanelet <id>: stop line <s> only crosses the lanelet once extended by 5 m (stop_line_extend_length) at (x, y)` |

识别不到的原因：`faces away, facing error +x° (max 40°)`（反过来画就能识别时加上 `drawn right -> left, reverse its node order`）、`x m behind the stop line`、`only x m ahead of the stop line (min 1 m)`。

**与其他车道的关系**：

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| ERROR | 一个灯（`refers` 中的 way）被多个 traffic_light 规则元素引用 | `traffic light way <w> is referenced by traffic_light <a>, <b>: the camera matching keeps a single (light, regulatory element) pair per light, ...` |
| WARNING | 延长后的停止线穿过附近另一条车辆 lanelet 的中心线，交点处方向与使用这个红绿灯的车道相差 < `same_direction_angle`。那条 lanelet 不是使用它的车道或其前后继，也没有引用与它共用灯的规则元素 | `traffic_light <a> (lanelet <ids>): stop line <s> crosses lanelet <o> (same direction, once extended by 5 m) at (x, y), which uses traffic_light <b>: ...`；那条 lanelet 没有红绿灯时为 `which uses no traffic light: ...`；不延长也穿过时为 `the stop line itself` |

**结构与标签**：

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| ERROR | 没有 `refers`；`refers` 的 way 不存在、缺少节点或少于 2 个点 | `no traffic light (refers)` / `traffic light way <w> missing from the map` / `traffic light way <w> with missing nodes or fewer than 2 points` |
| WARNING | `refers` 的 way 不是 `type=traffic_light` | `refers way <w> has type=..., not traffic_light` |
| ERROR | 没有 `ref_line`；第一个 `ref_line` 的 way 不存在或无效 | `no stop line (ref_line): ...` / `stop line way <s> is missing from the map or invalid` |
| WARNING | 多个 `ref_line`（只用第一个）；`ref_line` 的 way 不是 `type=stop_line` | `2 ref_line, only the first one (way <s>) is used as stop line` / `ref_line way <s> has type=..., not stop_line` |
| WARNING | 没有被任何 lanelet 引用 | `not referenced by any lanelet, no traffic_light module is ever created for it` |
| WARNING | 灯没有 `traffic_light_type`，或不是 `camera` / `v2x` | `traffic light way <w> has no traffic_light_type: ...` / `traffic light way <w> has traffic_light_type=..., used neither by the camera matching (camera) nor by the V2X handler (v2x)` |
| WARNING | 相机灯没有有效的 `height`（不是正数） | `camera traffic light way <w> has no valid height tag, the camera matching projects a box of height 0` |
| WARNING | 灯高不在 `min_height` ~ `max_height` 内 | `traffic light way <w> is 0.72 m above the stop line (expected 0.5 ~ 5 m), ...` |

**灯泡**（`check_light_bulbs: true` 时；当前为 `false`，不检查）：

| 级别 | 输出条件 | 输出信息 |
|---|---|---|
| WARNING | 灯泡（`light_bulbs` 的节点）离最近的灯框超过 `bulb_max_offset`。灯框为灯 linestring 往上 `height` 米 | `light_bulbs way <b> is up to 2.81 m from traffic light way <w> (max 1 m): bulbs z ... ~ ..., light z ... ~ ...` |
| ERROR | `light_bulbs` 的 way 不存在或缺少节点 | `light_bulbs way <b> is missing from the map or has missing nodes` |

- 结构与标签、灯泡两张表中的信息前面都有 `traffic_light <id>: `。问题的 id 为规则元素和涉及的 lanelet，忽略列表中写其中任何一个即可。
- `only_check_AOI: true` 时只检查有车辆 lanelet 完全位于 AOI 内的红绿灯。
- 与检查 3 中的红绿灯项目（light_bulb、`height`）相互独立：检查 3 由 Autoware 的验证工具报告。

## 扩展检查 `_template`（示例）

目录名以 `_` 开头，不会自动运行，只在 `--ext _template` 时运行：车辆 lanelet 缺少 `required_tags` 中的标签（默认 `speed_limit`）时报 WARNING `lanelet <id>: missing tag speed_limit`。
