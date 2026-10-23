"""
Discovery, loading and running of extension checks (ext/<name>/). To write a new extension check, see ext/README.md.

Each extension is a directory under ext/, whose name is the check name (used in output, Summary, ignore list and --set).
Directories starting with _ (e.g. _template) are not run automatically, they can only be selected with --ext / --only-ext:
  config/config.yaml   enable / script / ignore are handled here, other parameters are passed to the extension as is
  script/*.py          entry script, provides:
                         check(ctx, cfg, out)  required: ctx is utils.context.MapContext, cfg is the config (dict),
                                               out is utils.printer.ScopedPrinter
                         DEFAULT_CONFIG        optional: default values of the extension parameters; when present,
                                               misspelled parameter names in the config raise an error
                         configure(cfg)        optional: check / convert the parameters and return cfg,
                                               raise ValueError on problems
                       script/ is imported as a package, so the entry can use other modules via relative imports
                       (from ._helper import f); if script/ has several .py files not starting with _, set the entry
                       with script in the config
  README.md            description, the title on the first line is used by --list-ext
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
FRAMEWORK_CONFIG = {"enable": False, "script": None, "ignore": []}  # config items handled by the framework, with default values
PACKAGE = "mapchecker_ext"  # ext/<name>/script/ is imported as package mapchecker_ext.<name>


@dataclass
class Extension:
    name: str              # directory name, i.e. the check name
    path: str              # ext/<name>/
    cfg: dict              # config; after load(), default values filled in and ignore is {lanelet id: reason}
    module: object = None  # after load(), the entry script

    @property
    def config_path(self):
        return os.path.join(self.path, "config", "config.yaml")

    @property
    def enabled(self):
        """Whether it runs automatically: enable is true in the config, and the directory name does not start with _"""
        return bool(self.cfg.get("enable")) and not self.name.startswith("_")

    @property
    def title(self):
        """Title on the first line of README.md"""
        try:
            with open(os.path.join(self.path, "README.md"), encoding="utf-8") as f:
                return f.readline().lstrip("#").strip()
        except OSError:
            return ""


def discover(ext_dir=EXT_DIR):
    """All extensions under ext_dir {name: Extension}, sorted by name; only reads the configs, does not import scripts"""
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
    """If names is None, return the enabled extensions; else return names (ignoring enable), ValueError on unknown names"""
    if names is None:
        return [ext for ext in available.values() if ext.enabled]
    unknown = [n for n in names if n not in available]
    if unknown:
        raise ValueError(f"unknown extension(s): {', '.join(unknown)}; available: {', '.join(available) or 'none'}")
    return [available[n] for n in dict.fromkeys(names)]


def parse_overrides(items):
    """
    List of --set NAME.KEY=VALUE -> {name: {param: value}};
    values are parsed as YAML: 1.0 is a float, true a bool, [1, 2] a list
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
    Import the extension's entry script and prepare its config: fill in default values, apply overrides
    ({param: value}, from --set), parse ignore, call configure(). Raise ValueError on script or config problems. Return ext
    """
    module = _import_script(ext)
    if not callable(getattr(module, "check", None)):
        raise ValueError(f"extension '{ext.name}': {module.__file__} has no check(ctx, cfg, out) function")
    defaults = {**FRAMEWORK_CONFIG, **getattr(module, "DEFAULT_CONFIG", {})}
    cfg = {**defaults, **ext.cfg}
    # With DEFAULT_CONFIG, parameters in the config file must also be known, so misspelled parameter names are caught
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

    # Register script/ as package mapchecker_ext.<name>; relative imports in the entry script are resolved within it
    if PACKAGE not in sys.modules:
        root = types.ModuleType(PACKAGE)
        root.__path__ = []
        sys.modules[PACKAGE] = root
    package = f"{PACKAGE}.{ext.name}"
    if getattr(sys.modules.get(package), "__path__", None) != [script_dir]:
        # An extension with the same name moved to another directory (e.g. in tests): drop previously imported modules
        for key in [k for k in sys.modules if k == package or k.startswith(package + ".")]:
            del sys.modules[key]
        pkg = types.ModuleType(package)
        pkg.__path__ = [script_dir]
        sys.modules[package] = pkg
    try:
        return importlib.import_module(f"{package}.{os.path.splitext(entry)[0]}")
    except Exception as e:  # syntax errors, missing dependencies, etc., reported as a problem of the extension
        raise ValueError(f"extension '{ext.name}': failed to import {os.path.join(script_dir, entry)} "
                         f"({type(e).__name__}: {e})") from e


def run_extensions(ctx, extensions, out):
    """Run load()ed extensions in turn; if one raises, report an ERROR (counted in the exit code) and run the other checks"""
    for ext in extensions:
        scoped = out.scoped(ext.name)
        try:
            ext.module.check(ctx, ext.cfg, scoped)
        except Exception as e:  # a bug in one extension does not affect the results of the other checks
            scoped.error(f"extension {ext.name} crashed: {type(e).__name__}: {e}")
            for line in traceback.format_exc().rstrip().splitlines():
                out.info(f"    {line}")
