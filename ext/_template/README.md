# 扩展检查模板：车辆 lanelet 的必需标签

复制本目录来新建扩展检查，步骤见 [ext/README.md](../README.md)。复制后把第一行改成新检查的名字（`--list-ext` 显示它），并改写下面各节。

本模板本身也是一个可以运行的示例：检查车辆 lanelet 是否有 `required_tags` 中的标签，缺少时报 WARNING。

## 运行

```bash
cd MapChecker
python3 check_lanelet_map.py --only-ext _template                                # 只运行本检查
python3 check_lanelet_map.py --only-ext _template --set _template.required_tags=[speed_limit,one_way]
```

## 配置 `config/config.yaml`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `enable` | `false` | `true`：`check_lanelet_map.py` 自动运行本检查（以 `_` 开头的目录不会自动运行） |
| `required_tags` | `[speed_limit]` | 车辆 lanelet 必须有的标签 |
| `ignore` | `[]` | 本检查忽略的 lanelet id，写法同 `config/ignore.yaml` |

## 输出

```
=== Ext _template: vehicle lanelets with tags speed_limit ===
[WARNING] lanelet 1234: missing tag speed_limit
279 vehicle lanelets checked: 1 with missing tags
```
