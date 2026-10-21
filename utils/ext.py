"""
扩展检查 (ext/<名称>/): 每项扩展有自己的脚本 (script/)、配置 (config/config.yaml) 和 README.md，
配置中 enable 为 true 时由 check_lanelet_map.py 在检查 1~3 之后运行。

script/ 下需要有一个 .py 文件提供:
  CATEGORY                                     检查项名称，用于输出、Summary、忽略列表和热力图
  load_config(path)                            读取 config.yaml，返回 dict，其中 ignore 为 {lanelet id: 原因}
  check(lanelets, reg_elems, aois, cfg, out)   运行检查；aois 为 None 时检查整张地图
"""

import glob
import importlib.util
import os
from collections import namedtuple

import yaml

Extension = namedtuple("Extension", "name category module cfg")


def load_extensions(ext_dir):
    """ext_dir 下 config/config.yaml 中 enable 为 true 的扩展，按名称排序；未启用的扩展不导入"""
    result = []
    for cfg_path in sorted(glob.glob(os.path.join(ext_dir, "*", "config", "config.yaml"))):
        with open(cfg_path, encoding="utf-8") as f:
            if not (yaml.safe_load(f) or {}).get("enable", False):
                continue
        ext_path = os.path.dirname(os.path.dirname(cfg_path))
        name = os.path.basename(ext_path)
        for path in sorted(glob.glob(os.path.join(ext_path, "script", "*.py"))):
            spec = importlib.util.spec_from_file_location(f"ext_{name}_{os.path.basename(path)[:-3]}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "check"):
                break
        else:
            raise ValueError(f"extension '{name}': no script with a check() function in {ext_path}/script")
        result.append(Extension(name, module.CATEGORY, module, module.load_config(cfg_path)))
    return result
