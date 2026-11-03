# 红绿灯检查（Autoware traffic_light 模块与相机匹配）

MapChecker 的扩展检查：按 Autoware 的 traffic_light 模块和 milla 的相机匹配（`milla_traffic_light_matching`）实际使用红绿灯的方式，检查 `subtype=traffic_light` 的规则元素。重点是那些地图看起来没问题、但会让车**在绿灯前一直不走**或**在别的车道上停车**的画法。

起因是 2026-10-29 velizy 地图上的一次实车问题：车在左车道 33868，前方红绿灯 39264 一直识别为绿灯，车却一直不走。原因是相邻右车道的红绿灯 36310 的灯 linestring 画反了（从右往左），相机匹配永远匹配不上它；它的模块又因为停止线延长了 5 m，在左车道上插入了停车点。删掉 36310 后车就走了。

## 背景：Autoware 和相机匹配怎么用红绿灯

| 环节 | 行为 | 代码（`ad_ros2_ws/src`） |
|---|---|---|
| 模块的创建 | path 上每个 lanelet 引用的 traffic_light 规则元素都会创建一个模块。引用同一个灯（`refers` 中同一个 way）的规则元素只创建一个模块 | `autoware_behavior_velocity_traffic_light_module/src/manager.cpp` |
| 模块的删除 | 只要 path 上还有任何一个已注册的红绿灯，**所有**模块都不删除，所以 path 上曾经出现过的车道的模块会一直留着 | 同上 `getModuleExpiredFunction()` |
| 停止线 | 只用第一个 `ref_line`（`TrafficLight::stopLine()`）。两端各延长 `stop_line_extend_length`（5 m）后，与**当前 path** 求交点，不检查交点是不是在这个规则元素的车道上 | `.../src/utils.cpp` `calcStopPointAndInsertIndex()`，`planning_utils::extendLine()` |
| 没有信号 | milla 修改：收不到这个规则元素 id 的信号时当作**红灯**，所以只要有一个灯认不出来，车就永远不走。只有 `CIRCLE` 形状的 `GREEN` 才算绿灯 | `.../src/scene.cpp` `updateTrafficSignal()` |
| 相机匹配 | 只用 `traffic_light_type=camera` 的灯。灯的方向取 linestring 方向 + 90°，与车头方向相差超过 `max_angle_range_deg`（40°）的灯直接跳过，所以灯 linestring 要**从停止线看过去从左往右画**。灯框是 linestring 往上 `height` 米 | `milla/perception/traffic_light_recognition/milla_traffic_light_matching/src/traffic_light_matching.cpp` |
| 一个灯属于多个规则元素 | 地图中的灯只按 way id 去重，一个灯只会归到其中一个规则元素，其他规则元素拿不到它的状态 | 同上 `TrafficLightSet`（`IdLessThan` 只比较 way id） |
| 多个灯属于一个规则元素 | 各灯的状态合并到同一个规则元素中，任意一个是绿灯就放行 | 同上 `removeDuplicates()` |

行驶方向按 lanelet2 读图后的方向：左边界在几何上位于右侧时，lanelet2 把两条边界都翻转（`ll.left` / `ll.right` 已按此处理，见 `utils/lanelet_map.py` `_orient_bounds()`）。所以不能只看边界 way 中节点的顺序。

## 运行

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true`（默认）后，`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入它的终端输出和日志。
- Summary 中列出 `trafficlight warnings`，并计入退出码。
- 热力图中 WARNING 用绿色的点标出，ERROR 用红色的点标出，不参与着色和问题频发区域的判定。

单独运行（不看 `enable`，跳过检查 1~3）：

```bash
cd MapChecker
python3 check_lanelet_map.py --only-ext trafficlight                                  # 地图为 config/config.yaml 中的 input
python3 check_lanelet_map.py path/to/lanelet2_map.osm --only-ext trafficlight
python3 check_lanelet_map.py --only-ext trafficlight --set trafficlight.check_light_bulbs=false   # 不检查灯泡
```

日志、`--no-color`、退出码等与 `check_lanelet_map.py` 相同。

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查（本目录的配置中为 `true`） |
| `max_facing_angle` | `40.0` | 灯的方向与车道方向最多相差多少度，同相机匹配的 `max_angle_range_deg` |
| `min_forward_distance` | `1.0` | 灯至少在停止线前方多少米（沿车道方向），否则停下的车看不到它 |
| `min_height` / `max_height` | `1.5` / `8.0` | 灯框中心高出停止线的范围（米），超出时报 WARNING |
| `check_light_bulbs` | `true` | `false`：不检查灯泡（规则元素的 `light_bulbs`） |
| `bulb_max_offset` | `1.0` | 灯泡（`light_bulbs` 的节点）离灯框最远多少米 |
| `stop_line_extend_length` | `5.0` | 停止线两端各延长多少米，同 `behavior_velocity_planner.param.yaml` 中的值 |
| `same_direction_angle` | `45.0` | 延长后的停止线穿过的其他 lanelet，与使用这个红绿灯的车道方向相差小于这个角度（度）时报告 |
| `ignore` | `[]` | 本检查忽略的 id，写法同 `config/ignore.yaml`：`[39264, 33868: 原因]` |

默认值是配置文件中缺少该项时使用的值。参数名写错或取值超出范围时程序报错（退出码 2）。

忽略时，既可以写规则元素的 id，也可以写问题中涉及的 lanelet id：

- 也可以写在 `config/ignore.yaml` 中以 `trafficlight` 为键的列表里。
- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。

地图 `input`、日志和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`；`only_check_AOI: true` 时只检查有车辆 lanelet 完全位于 AOI 内的红绿灯。只被非车辆 lanelet（如 crosswalk 的行人信号灯）引用的红绿灯不检查，末尾列出它们的 id。

## 判定方法

对每个 `subtype=traffic_light` 的规则元素：

| 规则 | 级别 | 后果 |
|---|---|---|
| 车道上**没有一个灯能被识别**：相机灯朝向不对（与车道方向相差超过 `max_facing_angle`），或者不在停止线前方 `min_forward_distance` 以外；又没有 `v2x` 的灯 | ERROR | 模块收不到信号，当作红灯，这条车道上的车**永远过不了**这条停止线 |
| 有的灯能识别，但某个灯从某些车道上识别不到 | WARNING | 这个灯不起作用，只靠其他灯 |
| 停止线不穿过车道（包括前后继 lanelet），延长 `stop_line_extend_length` 后也不穿过 | ERROR | 模块算不出停车点，**红灯不停** |
| 停止线只有延长后才穿过车道 | WARNING | 依赖 Autoware 的延长 |
| 一个灯被多个 traffic_light 规则元素引用 | ERROR | 相机匹配只把它归给其中一个，其他规则元素拿不到它的状态，当作红灯 |
| 延长后的停止线穿过**同方向**的其他 lanelet，而那条 lanelet 没有引用这个红绿灯（或引用同一个灯的规则元素） | WARNING | 这个红绿灯的模块在那条车道上生效时（它的车道在 path 上，或者模块残留），车按它的灯停在那里。那条车道引用了别的红绿灯时：相位相同就合并成一个规则元素；没有红绿灯时：如果它也看这个信号，要加上这个红绿灯，否则红灯不停 |
| 没有 `refers` / `ref_line`，或引用的 way 不存在；`refers` 的 way 不是 `traffic_light` / `ref_line` 的 way 不是 `stop_line` | ERROR / WARNING | 没有 `ref_line` 时不创建模块，红灯不停 |
| 多个 `ref_line` | WARNING | 只用第一个 |
| 没有被任何 lanelet 引用 | WARNING | 不会创建模块 |
| 灯没有 `traffic_light_type`，或不是 `camera` / `v2x` | WARNING | 相机匹配和 V2X 都不用它。读到没有这个标签的灯时，相机匹配跳过这个规则元素后面的灯，V2X 跳过地图中后面所有的红绿灯 |
| 相机灯没有有效的 `height` | WARNING | 相机匹配按高度 0 投影 |
| 灯框中心高出停止线不在 `min_height` ~ `max_height` | WARNING | 相机匹配按这个高度找灯 |
| 灯泡离灯框超过 `bulb_max_offset`；`light_bulbs` 的 way 不存在（`check_light_bulbs: true` 时） | WARNING / ERROR | 相机匹配不用灯泡，影响 RViz 的显示 |

车道方向取车道中心线与停止线交点处的方向，近似车停下时的车头方向；灯的位置取灯框中心。问题的位置为停车点、灯或灯泡。

## 输出

velizy 地图修改前（`velizy_base_aero_map_planning` commit `9012674`）：

```
=== Ext trafficlight: traffic lights as used by the Autoware traffic_light module and the camera matching (facing <= 40°, stop line extended by 5 m) ===
...
[WARNING] traffic_light 36310: light_bulbs way 36309 is up to 2.81 m from traffic light way 36308 (max 1 m): bulbs z 209.57 ~ 209.59, light z 212.16 ~ 212.66
[ERROR] traffic_light 36310 | lanelet 33871: no light can be recognized from the lanelet (light 36308: faces away, facing error +175° (max 40°); drawn right -> left, reverse its node order): the traffic_light module gets no signal, treats it as red and vehicles on lanelet 33871 never pass the stop line at (42624.4, 3500.6)
[WARNING] traffic_light 36310 (lanelet 33871): stop line 36307 crosses lanelet 33868 (same direction, once extended by 5 m) at (42621.6, 3502.0), which uses traffic_light 39264: a module of 36310 active on lanelet 33868 stops the vehicle there by the lights of 36310; merge them into one regulatory element if they show the same phase
...
[WARNING] traffic_light 36321 (lanelet 33944): stop line 36318 crosses lanelet 34281 (same direction, once extended by 5 m) at (42641.6, 3535.4), which uses no traffic light: if it faces the same signal, add traffic_light 36321 to it (vehicles on it do not stop at red); otherwise a module of 36321 active on lanelet 34281 stops the vehicle there
[WARNING] traffic_light 36373: traffic light way 36330 is 0.72 m above the stop line (expected 1.5 ~ 8 m), the camera matching looks for it at this height
...
[WARNING] traffic_light 39264 (lanelet 33868): stop line 39263 crosses lanelet 33871 (same direction, once extended by 5 m) at (42624.4, 3500.6), which uses traffic_light 36310: a module of 39264 active on lanelet 33871 stops the vehicle there by the lights of 39264; merge them into one regulatory element if they show the same phase
...
13 traffic lights used by 13 vehicle lanelets checked: 1 error(s), 29 warning(s)
traffic_light ways not referenced by any regulatory element: 36220 36231 36244 36340 36350 36360 36370
```

把 39264 和 36310 合并成一个规则元素（两个灯、一条横跨两条车道的停止线，33868 和 33871 都引用它）后，ERROR 和两条车道互相穿过的 WARNING 消失，只剩下灯 36308 方向画反的提示：

```
[WARNING] traffic_light 39264: traffic light way 36308 cannot be recognized from lanelet 33868, 33871 (faces away, facing error +175° (max 40°); drawn right -> left, reverse its node order); only the other light(s) of the regulatory element are used there
```

## 参考：现有地图的结果（2026-10-30）

| 地图 | 红绿灯 | ERROR | 灯画反 | 停止线穿过同向车道（其中引用别的红绿灯） | 灯泡偏离 | 灯高异常 |
|---|---|---|---|---|---|---|
| velizy `9012674` | 13 | 1 | 1（36310） | 16（2） | 12 | 1（36373） |
| velizy 合并 39264 / 36310 后 | 12 | 0 | 1（36308，有 39256 兜底） | 14（0） | 12 | 1 |
| `lanelet2_map.osm` | 2 | 0 | 0 | 0 | 2 | 0 |
| `lanelet2_map_nly.osm` | 2 | 0 | 0 | 0 | 2 | 0 |
| `lanelet2_map_buc.osm` | 1 | 1 | 1（30969 的灯 30964，lanelet 28965） | 0 | 0 | 0 |
| `lanelet2_map_cern.osm` | 0 | | | | | |

velizy 的灯泡普遍在灯框下方 2.5 ~ 6 m，几乎贴着路面。

## 局限

- 只检查地图本身，不模拟相机的视野和遮挡：灯朝向和位置都对，也可能因为偏离视野或地图精度不够而匹配不上（匹配阈值为 100 像素，近距离时地图 0.5 m 的误差就可能超出）。
- `v2x` 的灯假定可用，不检查朝向和位置。
- 不知道两个红绿灯是否同一相位，停止线穿过别的车道时只给出两种情况的处理办法。
- 模块残留、停止线延长穿过别的车道是 Autoware 代码的问题（见上面的背景），修好代码之前，地图只能尽量避免：同一相位的相邻车道共用一个规则元素。

## 目录结构

```
ext/trafficlight/
  README.md
  config/config.yaml
  script/check_trafficlight.py
```

脚本只包含检查逻辑：地图、AOI、忽略列表、日志和输出由 MapChecker 处理（见 [ext/README.md](../README.md)），只依赖 numpy。
