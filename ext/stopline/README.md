# 停止线两点直线检查

MapChecker 的扩展检查：检查每条停止线是否只由两个点组成，即一条直线而不是折线。

停止线是 `type=stop_line` 的 way，由红绿灯、交通标志或 right_of_way 规则元素以 `ref_line` 引用，车辆在它前面停车。停止线应当是一条直线段，只有起点和终点两个节点；中间多出的节点会让它变成折线。

## 运行

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true` 后，`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入它的终端输出和日志。
- Summary 中列出 `stopline warnings`，并计入退出码。
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
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查 |
| `ignore` | `[]` | 本检查忽略的 id，写法同 `config/ignore.yaml`：`[7731, 7732: 原因]` |

本检查没有其他参数。

忽略时，既可以写停止线本身的 way id，也可以写引用它的 lanelet id：

- 也可以写在 `config/ignore.yaml` 中以 `stopline` 为键的列表里。
- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。

地图 `input`、日志和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`；`only_check_AOI: true` 时只检查完全位于 AOI 内的停止线。

## 判定方法

对地图中每条 `type=stop_line` 的 way：

| 情况 | 级别 | 说明 |
|---|---|---|
| 正好 2 个节点 | 通过 | |
| 超过 2 个节点 | WARNING | 折线。给出离两端连线最远的中间节点及其距离：距离为 0 表示中间节点只是落在直线上的多余节点，删掉即可；距离大说明停止线确实拐了弯 |
| 少于 2 个节点 | ERROR | 不能构成一条线 |
| 引用了地图中不存在的节点 | ERROR | 无法检查几何 |

问题的位置取离两端连线最远的那个节点。末尾另外列出没有被任何规则元素引用的停止线，仅供参考，不报告为问题。

## 输出

```
=== Ext stopline: stop lines must be straight lines of exactly 2 points ===
[WARNING] stop_line 8019: 3 points (polyline), should be a straight line of 2 points; node -126419 is 1.00 m off the line between its ends (length 7.3 m) | lanelet 4199 at (72578.2, 24155.3)
6 stop lines checked: 1 polylines, 0 invalid
```

`| lanelet ...` 为引用这条停止线的 lanelet，即通过规则元素的 `ref_line` 引用它的 lanelet；没有时为 `not referenced by any regulatory element`。

## 文件

```
ext/stopline/
  README.md
  config/config.yaml
  script/check_stopline.py
```

脚本只包含检查逻辑：地图、AOI、忽略列表、日志和输出由 MapChecker 处理（见 [ext/README.md](../README.md)），只依赖 numpy。
