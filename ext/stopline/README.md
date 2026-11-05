# 停止线检查：两点直线、traffic_sign / road_marking 的引用

MapChecker 的扩展检查，包含两部分：

1. **几何**：每条停止线是否只由两个点组成，即一条直线而不是折线。
2. **引用**：`traffic_sign` 和 `road_marking` 规则元素是否按 Autoware 读取的方式引用停止线。

停止线是 `type=stop_line` 的 way，车辆在它前面停车。引用它的方式有两种：

- 红绿灯、交通标志、right_of_way 规则元素：用 `ref_line` 引用。
- road_marking 规则元素：用 `refers` 引用。

停止线应当是一条直线段，只有起点和终点两个节点；中间多出的节点会让它变成折线。规则元素中停止线放错 role 或 `type` 写错时，Autoware 读不到它，车辆不会在这里停车。

## 运行

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true` 后，`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入它的终端输出和日志。
- Summary 中列出 `stopline warnings` / `stopline errors`，并计入退出码。
- 热力图中 WARNING 用绿色的点标出，ERROR 用红色的点标出，不参与着色和问题频发区域的判定。

单独运行（不看 `enable`，跳过检查 1~3）：

```bash
cd MapChecker
python3 check_lanelet_map.py --only-ext stopline                                 # 地图为 config/config.yaml 中的 input
python3 check_lanelet_map.py path/to/lanelet2_map.osm --only-ext stopline
```

日志、`--no-color`、退出码等与 `check_lanelet_map.py` 相同。

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查（本目录的配置中为 `true`） |
| `ignore` | `[]` | 本检查忽略的 id，写法同 `config/ignore.yaml`：`[7731, 7732: 原因]` |

本检查没有其他参数。

忽略时可以写以下任何一种 id：

- 停止线本身的 way id；
- 规则元素的 id，或它引用的任何一个 way 的 id；
- 引用停止线或规则元素的 lanelet id。

另外：

- 也可以写在 `config/ignore.yaml` 中以 `stopline` 为键的列表里。
- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。
- 报告末尾的忽略列表中，way 显示为 `way`，规则元素显示为 `reg_elem`。

地图 `input`、日志和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`。`only_check_AOI: true` 时：

- 只检查完全位于 AOI 内的停止线；
- 只检查引用的所有线都完全位于 AOI 内的规则元素。

## 判定方法

### 几何

对地图中每条 `type=stop_line` 的 way：

| 情况 | 级别 | 说明 |
|---|---|---|
| 正好 2 个节点 | 通过 | |
| 超过 2 个节点 | WARNING | 折线。给出离两端连线最远的中间节点及其距离：距离为 0 表示中间节点只是落在直线上的多余节点，删掉即可；距离大说明停止线确实拐了弯 |
| 少于 2 个节点 | ERROR | 不能构成一条线 |
| 引用了地图中不存在的节点 | ERROR | 无法检查几何 |

问题的位置取离两端连线最远的那个节点。

末尾另外列出没有被任何规则元素引用的停止线，仅供参考，不报告为问题。这里的"引用"指：被某条 lanelet 引用的规则元素，以 `ref_line` 或 road_marking 的 `refers` 用到这条停止线。

### 引用

要求与 Autoware 地图规范（vm-02-02、vm-03-14）和 [autoware_lanelet2_map_validator](https://github.com/tier4/autoware_lanelet2_map_validator) 中的 `regulatory_element_details_for_traffic_signs`、`road_markings` 一致。

**`subtype=traffic_sign`**：`refers` 为标志牌（`type=traffic_sign`），`ref_line` 为停止线（`type=stop_line`）。Autoware 的 stop_line 模块只处理带 `stop_sign` 的 traffic_sign，并在它的 `ref_line` 前停车。

| 情况 | 级别 | 说明 |
|---|---|---|
| `refers` 中没有 `type=traffic_sign` 的 way | ERROR | 没有标志牌，Autoware 不会把它识别为停车标志 |
| 停车标志（或没有标志牌）且没有 `ref_line` | ERROR | stop_line 模块找不到停车位置，车辆不会在这里停车。如果停止线被放进了 `refers`，提示把它的 role 改成 `ref_line` |
| 有 `ref_line`，但 `refers` 中还有停止线 | WARNING | 多余的停止线放错了 role |
| `refers` 中有其他类型的 way | WARNING | 不是标志牌 |
| `ref_line` 的 way 不是 `type=stop_line` | WARNING | 停止线的 `type` 写错了；本检查的几何部分也不会检查它 |

`stop_sign` 以外的交通标志不要求有 `ref_line`。

**`subtype=road_marking`**：恰好一个 `refers`，为停止线（`type=stop_line`），没有 `ref_line`。Autoware 只有在这条线是 `type=stop_line` 时才把它当作停止线使用（如 intersection 模块的路口停止线）。

| 情况 | 级别 | 说明 |
|---|---|---|
| `refers` 不是正好一个 | ERROR | 不知道哪条是停止线 |
| `refers` 的 way 不是 `type=stop_line` | WARNING | 常见于横跨车道画的停止线选成了 `line_thin`：Autoware 不使用它，车辆停在模块自己算的默认位置 |
| 有 `ref_line` | WARNING | road_marking 只用 `refers`，`ref_line` 不起作用 |

两种规则元素的成员不是地图中的 way 时（不存在或类型不对），报 ERROR。

问题的位置取停止线（包括放错 role 的停止线）的中点，没有时取第一条线的中点。

## 输出

```
=== Ext stopline: stop lines are straight lines of 2 points, traffic_sign / road_marking regulatory elements reference them correctly ===
[WARNING] stop_line 8019: 3 points (polyline), should be a straight line of 2 points; node -126419 is 1.00 m off the line between its ends (length 7.3 m) | lanelet 4199 at (72578.2, 24155.3)
114 stop lines checked: 1 polylines, 0 invalid
[ERROR] traffic_sign 51096: no stop line (ref_line): Autoware's stop_line module stops at the ref_line, the vehicle does not stop at this sign; stop line way 36087 is in role refers, change its role to ref_line | lanelet 49770 at (29762.1, 4846.8)
[WARNING] road_marking 101437: refers way 101436 has type=line_thin, not stop_line: Autoware does not use it as a stop line | lanelet 38398 at (30141.4, 4984.0)
31 regulatory elements checked (8 traffic_sign, 23 road_marking): 1 WARNING, 1 ERROR
```

`| lanelet ...` 的含义：

- 停止线的问题：引用这条停止线的 lanelet，即上面所说的引用方式；没有时为 `not referenced by any regulatory element`。
- 规则元素的问题：引用这个规则元素的 lanelet；没有时为 `not referenced by any lanelet`。

## 文件

```
ext/stopline/
  README.md
  config/config.yaml
  script/check_stopline.py
```

脚本只包含检查逻辑：地图、AOI、忽略列表、日志和输出由 MapChecker 处理（见 [ext/README.md](../README.md)），只依赖 numpy。
