"""Terminal / log output"""

from collections import defaultdict, namedtuple

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"

# WARNING with features: ids are the related lanelets, p is the model's probability of a true issue (None without a model)
Issue = namedtuple("Issue", "category ids features p")


class Printer:
    """Prints to the terminal (colored) and optionally to a log file (uncolored); lanelets in the ignore list are not output"""

    def __init__(self, use_color, log_file=None, ignore=None, model=None):
        self.use_color = use_color
        self.log_file = log_file
        self.ignore = ignore or {}          # {check: {lanelet id: reason}}
        self.model = model                  # utils.model.Model, scores the WARNINGs with features
        self.issues = []                    # printed WARNINGs with features (Issue)
        self.located = []                   # (check, lanelet ids, coordinates or None, p or None, printed line) of printed
                                            #   WARNINGs / ERRORs, for the heatmap
        self.count = defaultdict(int)       # number of WARNINGs
        self.errors = defaultdict(int)      # number of ERRORs
        self.suppressed = defaultdict(int)  # (check, lanelet id) -> number of entries not printed because ignored
        self._held = None                   # (p, scored WARNING) held after hold()

    def is_ignored(self, category, *ids):
        """Return True if a lanelet in ids is in the ignore list of this check, and record the number of entries not printed"""
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
        features: features of this warning (utils/features.py), prints [WARNING p=...] when there is a model
        pos:      coordinates of the issue, for the heatmap; None to use the position of the lanelets in ids
        """
        if self.is_ignored(category, *ids):
            return
        self.count[category] += 1
        p = None
        if features is not None:
            p = self.model.score(category, features) if self.model else None
            self.issues.append(Issue(category, tuple(ids), features, p))
        line = f"[WARNING] {text}" if p is None else f"[WARNING p={p:.2f}] {text}"
        self.located.append((category, tuple(ids), pos, p, line))
        if p is not None and self._held is not None:
            self._held.append((p, line))
        else:
            self._print(line, YELLOW)

    def hold(self):
        """Hold subsequent scored WARNINGs, flush() prints them by descending p; unscored ones are still printed immediately"""
        self._held = []

    def flush(self):
        held, self._held = self._held or [], None
        for _, line in sorted(held, key=lambda t: -t[0]):  # stable sort, keeps the original order for equal p
            self._print(line, YELLOW)

    def error(self, category, text, ids=(), pos=None):
        if not self.is_ignored(category, *ids):
            self.errors[category] += 1
            self.located.append((category, tuple(ids), pos, None, f"[ERROR] {text}"))
            self._print(f"[ERROR] {text}", RED)

    def ok(self, text):
        self._print(f"[OK] {text}", GREEN)

    def info(self, text):
        self._print(text)

    @property
    def total(self):
        return sum(self.count.values()) + sum(self.errors.values())

    def scoped(self, category):
        """Output bound to one check (ScopedPrinter), passed to check() of the extension checks (ext/)"""
        return ScopedPrinter(self, category)


class ScopedPrinter:
    """
    Printer bound to one check (the name of the extension check): warn() / error() no longer take the check;
    ignore list, counts, log and heatmap are handled the same as for Checks 1~3
    """

    def __init__(self, printer, category):
        self.printer = printer
        self.category = category

    def header(self, text):
        self.printer.header(text)

    def info(self, text):
        self.printer.info(text)

    def ok(self, text):
        self.printer.ok(text)

    def warn(self, text, ids=(), pos=None):
        """ids: related lanelet ids (ignore list, heatmap); pos: issue coordinates [x, y], None = position of lanelets in ids"""
        self.printer.warn(self.category, text, ids, pos=pos)

    def error(self, text, ids=(), pos=None):
        self.printer.error(self.category, text, ids, pos)

    @property
    def warnings(self):
        """Number of WARNINGs printed so far"""
        return self.printer.count[self.category]

    @property
    def errors(self):
        """Number of ERRORs printed so far"""
        return self.printer.errors[self.category]

    @property
    def ignored(self):
        """Number of entries not printed because of the ignore list"""
        return sum(n for (category, _), n in self.printer.suppressed.items() if category == self.category)
