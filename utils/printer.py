"""终端 / 日志输出"""

from collections import defaultdict, namedtuple

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"

# 带特征的 WARNING: ids 为相关 lanelet，p 为模型给出的真问题概率 (没有模型时为 None)
Issue = namedtuple("Issue", "category ids features p")


class Printer:
    """打印到终端 (带颜色)，并可同时写入日志文件 (无颜色)；忽略列表中的 lanelet 不输出"""

    def __init__(self, use_color, log_file=None, ignore=None, model=None):
        self.use_color = use_color
        self.log_file = log_file
        self.ignore = ignore or {}          # {检查项: {lanelet id: 原因}}
        self.model = model                  # utils.model.Model，给带特征的 WARNING 打分
        self.issues = []                    # 输出了的带特征的 WARNING (Issue)
        self.located = []                   # 输出了的 WARNING / ERROR 的 (检查项, lanelet ids, 坐标或 None, 权重)，用于热力图；
                                            #   权重: 有分数的 WARNING 为 p，其余为 1
        self.count = defaultdict(int)       # WARNING 数量
        self.errors = defaultdict(int)      # ERROR 数量
        self.suppressed = defaultdict(int)  # (检查项, lanelet id) -> 因忽略而未输出的条数
        self._held = None                   # hold() 之后暂存的 (p, 带分数的 WARNING)

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

    def warn(self, category, text, ids=(), features=None, pos=None):
        """
        features: 该告警的特征 (utils/features.py)，有模型时输出 [WARNING p=...]
        pos:      问题的坐标，用于热力图；None 时取 ids 中 lanelet 的位置
        """
        if self.is_ignored(category, *ids):
            return
        self.count[category] += 1
        p = None
        if features is not None:
            p = self.model.score(category, features) if self.model else None
            self.issues.append(Issue(category, tuple(ids), features, p))
        self.located.append((category, tuple(ids), pos, 1.0 if p is None else p))
        if p is None:
            self._print(f"[WARNING] {text}", YELLOW)
        elif self._held is not None:
            self._held.append((p, f"[WARNING p={p:.2f}] {text}"))
        else:
            self._print(f"[WARNING p={p:.2f}] {text}", YELLOW)

    def hold(self):
        """之后带分数的 WARNING 先暂存，flush() 时按 p 从大到小输出；没有分数的照常立即输出"""
        self._held = []

    def flush(self):
        held, self._held = self._held or [], None
        for _, line in sorted(held, key=lambda t: -t[0]):  # 稳定排序，p 相同时保持原来的顺序
            self._print(line, YELLOW)

    def error(self, category, text, ids=(), pos=None):
        if not self.is_ignored(category, *ids):
            self.errors[category] += 1
            self.located.append((category, tuple(ids), pos, 1.0))
            self._print(f"[ERROR] {text}", RED)

    def ok(self, text):
        self._print(f"[OK] {text}", GREEN)

    def info(self, text):
        self._print(text)

    @property
    def total(self):
        return sum(self.count.values()) + sum(self.errors.values())
