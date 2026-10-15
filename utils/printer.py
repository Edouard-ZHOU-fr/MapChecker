"""终端 / 日志输出"""

from collections import defaultdict

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"


class Printer:
    """打印到终端 (带颜色)，并可同时写入日志文件 (无颜色)；忽略列表中的 lanelet 不输出"""

    def __init__(self, use_color, log_file=None, ignore=None):
        self.use_color = use_color
        self.log_file = log_file
        self.ignore = ignore or {}          # {检查项: {lanelet id: 原因}}
        self.count = defaultdict(int)       # WARNING 数量
        self.errors = defaultdict(int)      # ERROR 数量
        self.suppressed = defaultdict(int)  # (检查项, lanelet id) -> 因忽略而未输出的条数

    def is_ignored(self, category, *ids):
        """ids 中有 lanelet 在该检查项的忽略列表里时返回 True，并记录未输出的条数"""
        hit = [i for i in ids if i in self.ignore.get(category, {})]
        for i in hit:
            self.suppressed[(category, i)] += 1
        return bool(hit)

    def _print(self, text, color=None):
        print(f"{color}{text}{RESET}" if color and self.use_color else text)
        if self.log_file:
            self.log_file.write(text + "\n")

    def header(self, text):
        self._print("")
        self._print(f"=== {text} ===", BOLD)

    def warn(self, category, text, ids=()):
        if not self.is_ignored(category, *ids):
            self.count[category] += 1
            self._print(f"[WARNING] {text}", YELLOW)

    def error(self, category, text, ids=()):
        if not self.is_ignored(category, *ids):
            self.errors[category] += 1
            self._print(f"[ERROR] {text}", RED)

    def ok(self, text):
        self._print(f"[OK] {text}", GREEN)

    def info(self, text):
        self._print(text)

    @property
    def total(self):
        return sum(self.count.values()) + sum(self.errors.values())
