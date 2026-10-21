# crosswalk 横向超出检查

MapChecker 的扩展检查：测量每个 crosswalk 在横向上最多超出道路多少，超出过多时报 WARNING。

crosswalk lanelet 的左右边界沿行人行走的方向，即横穿道路的方向。crosswalk 两端通常会画到人行道上一点；但如果超出道路太多，crosswalk 会覆盖大片人行道，在人行道上行走、并不打算过马路的行人也会落在 crosswalk 内。

## 启用

在 [config/config.yaml](config/config.yaml) 中设置 `enable: true` 后，`check_lanelet_map.py` 会在检查 1~3 之后运行本检查：

- 结果写入它的终端输出和日志。
- Summary 中列出 `crosswalk warnings`，并计入退出码。
- 热力图中只用绿色圆点标出问题点，不参与着色和问题频发区域的判定。

`enable: false` 时 `check_lanelet_map.py` 不运行本检查，也不导入本目录中的脚本。

也可以单独运行（不看 `enable`）：

```bash
cd MapChecker
python3 ext/crosswalk/script/check_crosswalk_width.py                       # 地图为 config/config.yaml 中的 input
python3 ext/crosswalk/script/check_crosswalk_width.py path/to/lanelet2_map.osm
python3 ext/crosswalk/script/check_crosswalk_width.py --max-extension 1.0   # 命令行参数优先于配置文件
```

| 参数 | 对应配置 |
|---|---|
| `map`（位置参数） | `config/config.yaml` 中的 `input` |
| `-c, --config FILE` | 本检查的配置文件，默认 `ext/crosswalk/config/config.yaml` |
| `--max-extension M` | `max_extension` |
| `--no-color` | 不输出颜色 |

单独运行时退出码与 `check_lanelet_map.py` 相同：`0` 没有问题，`1` 有未忽略的 WARNING，`2` 参数错误。

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 运行本检查 |
| `max_extension` | `1.0` | crosswalk 每一端最多可以超出道路多少米，超出更多时报 WARNING |
| `ignore` | `[]` | 本检查忽略的 crosswalk id，写法同 `config/ignore.yaml`：`[19942, 19991: 原因]` |
| `log.enabled` / `log.dir` | `false` / `logs` | 只在单独运行时使用：同时写入日志 `logs/crosswalk_check_YYYYmmdd_HHMMSS.log` |

默认值是配置文件中缺少该项时使用的值。

地图 `input`、忽略列表 `ignore_file` 和 `only_check_AOI` 沿用 MapChecker 的 `config/config.yaml`，有以下几点：

- `config/ignore.yaml` 中 `all` 下的 lanelet 在本检查中也忽略。
- 被忽略的 crosswalk 在报告末尾的 Ignored lanelets 中以 `[crosswalk]` 列出。
- `only_check_AOI: true` 时只检查完全位于 AOI 内的 crosswalk。

## 测量方法

1. **道路**：与 crosswalk 重叠的所有车辆 lanelet（`road`、`highway`、`road_shoulder`）的并集，始终用整张地图计算。起点 / 终点落在 crosswalk 内的车道也算在内，中间有分隔带时以最外侧的车道为道路边缘。
2. **采样线**：在 crosswalk 宽度方向上每隔约 0.5 m 取一条平行于其左右边界的线，沿线每 0.02 m 采样。每条线上第一个 / 最后一个落在道路内的采样点就是道路边缘，crosswalk 起点端 / 终点端到它的距离即这一端超出道路的长度。
3. **取最大值**：每一端取所有线中的最大值，即这一端最多超出道路多少。斜穿道路的 crosswalk 沿它自己的方向测量。
4. **报告**：两端中较大的值大于 `max_extension` 时报 WARNING，否则只输出测量值。端点落在道路内时，这一端的超出量为 0。不与任何车道重叠的 crosswalk 只列出 id，不检查。

测量精度约 0.02 m。

## 输出

每个 crosswalk 一行，按 id 排序。`end` 后是 crosswalk 这一端（端边中点）的坐标，以及这一端超出了哪条车道（道路边缘的车道）多少米：

```
=== Ext crosswalk: how far crosswalks extend beyond the road (max 2.00 m) ===
crosswalk 1824: extends up to 1.27 m beyond the road | end (72096.1, 24113.9): 1.27 m beyond lanelet 5115, end (72100.4, 24120.4): 1.13 m beyond lanelet 11928
...
[WARNING] crosswalk 9039: extends up to 9.90 m beyond the road (max 2.00 m) | end (72839.5, 24019.6): 9.90 m beyond lanelet 9120, end (72849.2, 24008.9): 2.63 m beyond lanelet 9120
...
46 crosswalks: 19 extend more than 2.00 m beyond the road
```

端点落在道路内时写 `inside`，例如 `lanelet2_map_nly.osm` 中的 `end (31274.0, 10432.1): inside lanelet 1007`。

## 参考：现有地图中的超出量（2026-10-21）

| 地图 | crosswalk 端数 | 中位数 | 90% | 最大 | > 0.5 m | > 1 m | > 2 m |
|---|---|---|---|---|---|---|---|
| `lanelet2_map.osm` | 92 | 1.3 m | 4.1 m | 9.9 m | 89 | 66 | 27 |
| `lanelet2_map_nly.osm` | 84 | 0.4 m | 0.8 m | 1.4 m | 27 | 6 | 0 |

## 目录结构

```
ext/crosswalk/
  README.md
  config/config.yaml                本检查的配置
  script/check_crosswalk_width.py   检查脚本 (CATEGORY、load_config()、check()，也可单独运行)
```

脚本复用 MapChecker `utils/` 中的地图读取、几何、忽略列表和输出，只依赖 numpy 和 PyYAML。
