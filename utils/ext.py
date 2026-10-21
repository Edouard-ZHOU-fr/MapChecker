"""
扩展检查 (ext/<名称>/) 的发现、加载和运行。编写新的扩展检查见 ext/README.md。

每项扩展是 ext/ 下的一个目录，目录名即检查项名称 (用于输出、Summary、忽略列表和 --set)。
以 _ 开头的目录 (如 _template) 不会自动运行，只能用 --ext / --only-ext 指定:
  config/config.yaml   enable / script / ignore 由这里处理，其余参数原样传给扩展
  script/*.py          入口脚本，提供:
                         check(ctx, cfg, out)  必需: ctx 为 utils.context.MapContext，cfg 为配置 (dict)，
                                               out 为 utils.printer.ScopedPrinter
                         DEFAULT_CONFIG        可选: 扩展参数的默认值；有它时配置中写错的参数名会报错
                         configure(cfg)        可选: 检查 / 转换参数并返回 cfg，有问题时 raise ValueError
                       script/ 作为包导入，入口可以用相对导入 (from ._helper import f) 使用其他模块；
                       script/ 下有多个不以 _ 开头的 .py 时，用配置中的 script 指定入口
  README.md            说明，第一行标题用于 --list-ext
"""

import glob
import importlib
import os
import sys
import traceback
import types
from dataclasses import dataclass

import yaml

from .config import BASE_DIR
from .ignore import parse_items

EXT_DIR = os.path.join(BASE_DIR, "ext")
FRAMEWORK_CONFIG = {"enable": False, "script": None, "ignore": []}  # 由框架处理的配置项及其默认值
PACKAGE = "mapchecker_ext"  # ext/<名称>/script/ 导入为包 mapchecker_ext.<名称>


@dataclass
class Extension:
    name: str              # 目录名，即检查项名称
    path: str              # ext/<名称>/
    cfg: dict              # 配置；load() 之后补全了默认值，ignore 为 {lanelet id: 原因}
    module: object = None  # load() 之后为入口脚本

    @property
    def config_path(self):
        return os.path.join(self.path, "config", "config.yaml")

    @property
    def enabled(self):
        """是否自动运行: 配置中 enable 为 true，且目录名不以 _ 开头"""
        return bool(self.cfg.get("enable")) and not self.name.startswith("_")

    @property
    def title(self):
        """README.md 第一行的标题"""
        try:
            with open(os.path.join(self.path, "README.md"), encoding="utf-8") as f:
                return f.readline().lstrip("#").strip()
        except OSError:
            return ""


def discover(ext_dir=EXT_DIR):
    """ext_dir 下所有扩展 {名称: Extension}，按名称排序；只读配置，不导入脚本"""
    result = {}
    for cfg_path in sorted(glob.glob(os.path.join(ext_dir, "*", "config", "config.yaml"))):
        path = os.path.dirname(os.path.dirname(cfg_path))
        name = os.path.basename(path)
        if not name.isidentifier():
            raise ValueError(f"{path}: extension directory name must contain only letters, digits and _")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if not isinstance(cfg, dict):
            raise ValueError(f"{cfg_path}: top level must be a mapping")
        result[name] = Extension(name, path, cfg)
    return result


def select(available, names=None):
    """names 为 None 时返回启用的扩展；否则按 names 返回 (不看 enable)，有未知名称时抛出 ValueError"""
    if names is None:
        return [ext for ext in available.values() if ext.enabled]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"unknown extension(s): {', '.join(unknown)}; available: {', '.join(available) or 'none'}")
    return [available[n] for n in dict.fromkeys(names)]


def parse_overrides(items):
    """
    --set NAME.KEY=VALUE 的列表 -> {名称: {参数: 值}}；
    值按 YAML 解析: 1.0 为 float，true 为 bool，[1, 2] 为列表
    """
    result = {}
    for item in items or ():
        key, eq, value = item.partition("=")
        name, dot, param = key.strip().partition(".")
        if not (eq and dot and name and param):
            raise ValueError(f"--set '{item}': expected NAME.KEY=VALUE, e.g. crosswalk.max_extension=1.0")
        try:
            result.setdefault(name, {})[param] = yaml.safe_load(value)
        except yaml.YAMLError as e:
            raise ValueError(f"--set '{item}': invalid value ({e})") from e
    return result


def load(ext, overrides=None):
    """
    导入扩展的入口脚本并整理配置: 补全默认值，应用 overrides ({参数: 值}，来自 --set)，解析 ignore，调用 configure()。
    脚本或配置有问题时抛出 ValueError。返回 ext
    """
    module = _import_script(ext)
    if not callable(getattr(module, "check", None)):
        raise ValueError(f"extension '{ext.name}': {module.__file__} has no check(ctx, cfg, out) function")
    defaults = {**FRAMEWORK_CONFIG, **getattr(module, "DEFAULT_CONFIG", {})}
    cfg = {**defaults, **ext.cfg}
    # 有 DEFAULT_CONFIG 时，配置文件中的参数也必须是已知的，参数名写错能被发现
    known = defaults if hasattr(module, "DEFAULT_CONFIG") else cfg
    unknown = [k for k in list(ext.cfg) + list(overrides or {}) if k not in known]
    if unknown:
        raise ValueError(f"extension '{ext.name}': unknown parameter(s) {', '.join(unknown)} "
                         f"(in {ext.config_path} or --set), expected one of {', '.join(known)}")
    cfg.update(overrides or {})
    cfg["ignore"] = parse_items(cfg["ignore"], f"{ext.config_path}: 'ignore'")
    if hasattr(module, "configure"):
        try:
            cfg = module.configure(cfg)
        except (ValueError, TypeError, KeyError) as e:
            raise ValueError(f"extension '{ext.name}': invalid config ({type(e).__name__}: {e})") from e
        if not isinstance(cfg, dict):
            raise ValueError(f"extension '{ext.name}': configure() must return the config dict")
    ext.module, ext.cfg = module, cfg
    return ext


def _import_script(ext):
    script_dir = os.path.join(ext.path, "script")
    entry = ext.cfg.get("script")
    if not entry:
        files = sorted(f for f in os.listdir(script_dir) if f.endswith(".py") and not f.startswith("_")) \
            if os.path.isdir(script_dir) else []
        if len(files) != 1:
            raise ValueError(f"extension '{ext.name}': " + (
                f"no script in {script_dir}" if not files else
                f"several scripts in {script_dir} ({', '.join(files)}), set 'script' in {ext.config_path}"))
        entry = files[0]
    if not os.path.isfile(os.path.join(script_dir, entry)):
        raise ValueError(f"extension '{ext.name}': script not found: {os.path.join(script_dir, entry)}")

    # script/ 注册为包 mapchecker_ext.<名称>，入口脚本中的相对导入在其中查找
    if PACKAGE not in sys.modules:
        root = types.ModuleType(PACKAGE)
        root.__path__ = []
        sys.modules[PACKAGE] = root
    package = f"{PACKAGE}.{ext.name}"
    if getattr(sys.modules.get(package), "__path__", None) != [script_dir]:
        # 同名扩展换了目录 (如测试中)，去掉之前导入的模块
        for key in [k for k in sys.modules if k == package or k.startswith(package + ".")]:
            del sys.modules[key]
        pkg = types.ModuleType(package)
        pkg.__path__ = [script_dir]
        sys.modules[package] = pkg
    try:
        return importlib.import_module(f"{package}.{os.path.splitext(entry)[0]}")
    except Exception as e:  # 语法错误、缺少依赖等，报告为扩展的问题
        raise ValueError(f"extension '{ext.name}': failed to import {os.path.join(script_dir, entry)} "
                         f"({type(e).__name__}: {e})") from e


def run_extensions(ctx, extensions, out):
    """依次运行 load() 过的扩展；某个扩展抛出异常时报 ERROR (计入退出码) 并继续运行其他检查"""
    for ext in extensions:
        scoped = out.scoped(ext.name)
        try:
            ext.module.check(ctx, ext.cfg, scoped)
        except Exception as e:  # 一个扩展的 bug 不影响其他检查的结果
            scoped.error(f"extension {ext.name} crashed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().rstrip().splitlines():
                out.info(f"    {line}")
