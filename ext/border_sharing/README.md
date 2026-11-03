# 相邻车道共用边界检查

MapChecker 的扩展检查：并排的两条车道之间应当共用同一条边界线（同一个 way），而不是各画一条。

在 Lanelet2 / Autoware 中，两条 lanelet 只有共用中间那条边界 linestring 时才算相邻：

- **同向车道**：路由图中左右相邻，才能换道。
- **对向车道**：共用一条中心线（Autoware 地图要求中对向车道共用中心线）。

各画一条时，即使两条线位置重合，路由图也不认为它们相邻；两条线之间通常还留下一点缝隙或重叠。重叠的对向车道还会被检查 2 报成 "crossing overlap without right_of_way"，例如 cern 的 5353/9684。

## 运行

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true` 后，`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入它的终端输出和日志。
- Summary 中列出 `border_sharing warnings`，并计入退出码。
- 热力图中用绿色的点标出问题点，不参与着色和问题频发区域的判定。

单独运行（不看 `enable`，跳过检查 1~3）：

```bash
cd MapChecker
python3 check_lanelet_map.py --only-ext border_sharing                              # 地图为 config/config.yaml 中的 input
python3 check_lanelet_map.py path/to/lanelet2_map.osm --only-ext border_sharing
python3 check_lanelet_map.py --only-ext border_sharing --set border_sharing.max_distance=0.5   # 命令行优先于配置文件
```

日志、`--no-color`、退出码等与 `check_lanelet_map.py` 相同。

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查 |
| `max_distance` | `1.0` | 两条边界相距不超过多少米算"同一条线" |
| `min_fraction` | `0.8` | 两条边界中较短的那条，至少有多大比例与另一条重合 |
| `min_length` | `2.0` | 重合部分至少多少米 |
| `ignore` | `[]` | 本检查忽略的 lanelet id，写法同 `config/ignore.yaml`：`[5353, 9684: 原因]` |

参数名写错时程序报错（退出码 2）。

忽略列表：

- 也可以写在 `config/ignore.yaml` 中以 `border_sharing` 为键的列表里。
- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。

地图 `input`、日志和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`。`only_check_AOI: true` 时只检查两条都完全位于 AOI 内的车道对。

## 判定方法

对每一对车辆 lanelet，比较它们"面对面"的边界：

| 车道方向 | 比较的边界 |
|---|---|
| 同向 | 一条的右边界与另一条的左边界 |
| 对向 | 两条的左边界，或两条的右边界 |

具体步骤：

1. 沿第一条边界每 0.5 m 取一个点，求它到第二条边界的距离和第二条边界在该处的方向。
2. 距离不超过 `max_distance`、且两条边界方向平行（对向时反平行，夹角 25° 以内）的点，算作重合。
3. 两条边界是同一个 way：计为已共用，不报告。
4. 两条边界是不同的 way，且重合部分 ≥ `min_length`，并且 ≥ 较短那条边界长度的 `min_fraction`：报 WARNING。

只有一部分靠得近的边界不报告。例如路口里互相经过的转弯车道，只在几米内靠近。15 张地图的统计：

- 该共用而没共用的边界，几乎都整条重合（≥ 90%），两条线之间相距 0~1 m。
- 只是局部靠近的边界，大多重合不到 70%。

两条线之间如果确实有宽度小于 `max_distance` 的隔离带（不是同一条线），把这对车道加入忽略列表即可。

同向车道的例子（sqy）：

```
[WARNING] lanelet 12057 left (way 12056) / lanelet 12075 right (way 12072): boundaries coincide over 21.0 m (100% of the shorter) but are different ways, gap 0.26 m between the lanes; they are not adjacent in the routing graph, no lane change between them | at (29967.8, 4605.0)
```

输出中给出两条线的相对位置，取重合部分有符号距离的中位数：

| 输出 | 含义 |
|---|---|
| `drawn on top of each other` | 两条线重合在一起 |
| `gap x m between the lanes` | 两条车道之间留有缝隙 |
| `overlap x m between the lanes` | 两条车道互相重叠 |

## 输出

```
=== Ext border_sharing: side-by-side lanelets sharing their boundary (coinciding within 1.00 m along >= 80% of the boundary) ===
[WARNING] lanelet 5353 left (way 9270) / lanelet 9684 left (way 9681), opposite directions: boundaries coincide over 80.1 m (100% of the shorter) but are different ways, overlap 0.47 m between the lanes; opposite lanes should share one center line | at (71816.3, 24414.8)
...
107 pairs of side-by-side lanelets: 83 share their boundary, 24 do not
```

修正方法：在地图编辑器中删掉其中一条线，让两条 lanelet 都引用剩下的那条 way（对向车道时一条正向引用、一条反向引用）。

## 文件

```
ext/border_sharing/
  README.md
  config/config.yaml
  script/check_border_sharing.py
```

脚本只包含检查逻辑：地图、AOI、忽略列表、日志和输出由 MapChecker 处理（见 [ext/README.md](../README.md)），只依赖 numpy。
