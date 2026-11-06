"""Terminal / log output"""

from collections import defaultdict, namedtuple

from .issue_codes import NAME, IssueCodes

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"
RESET = "\033[0m"

# WARNING with features: ids are the related lanelets, p is the model's probability of a true issue (None without a model)
Issue = namedtuple("Issue", "category code ids features p")


class Printer:
    """
    Prints to the terminal (colored) and optionally to a log file (uncolored). Each issue has a code registered in
    config/issue_codes.yaml (Checks 1~3) or ext/<name>/config/issue_codes.yaml (extensions), which gives its severity;
    issues of lanelets in the ignore list are not output
    """

    def __init__(self, use_color, log_file=None, ignore=None, model=None, codes=None):
        self.use_color = use_color
        self.log_file = log_file
        self.ignore = ignore or {}          # {check or issue code: {lanelet id: reason}}
        self.model = model                  # utils.model.Model, scores the WARNINGs with features
        self.codes = codes or IssueCodes.load()  # utils.issue_codes.IssueCodes, default config/issue_codes.yaml;
                                                 #   utils.ext.run_extensions() adds the codes of the extensions
        self.issues = []                    # printed WARNINGs with features (Issue)
        self.located = []                   # (check, lanelet ids, coordinates or None, p or None, printed line) of printed
                                            #   WARNINGs / ERRORs, for the heatmap
        self.count = defaultdict(int)       # number of WARNINGs
        self.errors = defaultdict(int)      # number of ERRORs
        self.suppressed = defaultdict(int)  # (check or issue code, lanelet id) -> number of entries not printed because
                                            #   ignored
        self.ignored = defaultdict(int)     # check -> number of issues not printed because ignored
        self.by_code = defaultdict(int)     # issue code -> number of printed issues, in the order of the output
        self.disabled = defaultdict(int)    # issue code -> number of issues not printed because its severity is off
        self._held = None                   # (p, scored WARNING) held after hold()

    def is_ignored(self, category, code, *ids):
        """
        Return True if a lanelet in ids is in the ignore list of this check or of this issue code, and record the number
        of entries not printed
        """
        hit = [(scope, i) for scope in (category, code) for i in ids if i in self.ignore.get(scope, {})]
        for key in hit:
            self.suppressed[key] += 1
        if hit:
            self.ignored[category] += 1
        return bool(hit)

    def _print(self, text, color=None):
        print(f"{color}{text}{RESET}" if color and self.use_color else text)
        if self.log_file:
            self.log_file.write(text + "\n")

    def header(self, text):
        self._print("")
        self._print(f"=== {text} ===", BOLD)

    def issue(self, category, code, text, ids=(), features=None, pos=None):
        """
        Reports an issue of the check category as [WARNING code] or [ERROR code], by the severity of the code; not
        reported if the severity is off, or if a lanelet in ids is ignored for the check or for the code.
        Return the printed line, None if not reported; ValueError if the code is not registered
        features: features of this warning (utils/features.py), prints [WARNING code p=...] when there is a model;
                  not used when the code is an ERROR
        pos:      coordinates of the issue, for the heatmap; None to use the position of the lanelets in ids
        """
        severity = self.codes.severity(code)
        if severity == "off":
            self.disabled[code] += 1
            return None
        if self.is_ignored(category, code, *ids):
            return None
        self.by_code[code] += 1
        if severity == "error":
            self.errors[category] += 1
            line = f"[ERROR {code}] {text}"
            self.located.append((category, tuple(ids), pos, None, line))
            self._print(line, RED)
            return line

        self.count[category] += 1
        p = None
        if features is not None:
            p = self.model.score(category, features) if self.model else None
            self.issues.append(Issue(category, code, tuple(ids), features, p))
        line = f"[WARNING {code}] {text}" if p is None else f"[WARNING {code} p={p:.2f}] {text}"
        self.located.append((category, tuple(ids), pos, p, line))
        if p is not None and self._held is not None:
            self._held.append((p, line))
        else:
            self._print(line, YELLOW)
        return line

    def hold(self):
        """Hold subsequent scored WARNINGs, flush() prints them by descending p; unscored ones are still printed immediately"""
        self._held = []

    def flush(self):
        held, self._held = self._held or [], None
        for _, line in sorted(held, key=lambda t: -t[0]):  # stable sort, keeps the original order for equal p
            self._print(line, YELLOW)

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
    Printer bound to one check (the name of the extension check): issue() takes the issue name registered in
    ext/<name>/config/issue_codes.yaml, the code being <name>.<issue>; ignore list, counts, log and heatmap are handled
    the same as for Checks 1~3
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

    def issue(self, name, text, ids=(), pos=None):
        """
        [WARNING <check>.<name>] / [ERROR <check>.<name>] by the severity of the issue name in the
        config/issue_codes.yaml of the extension (nothing if off); return the printed line, None if not reported.
        ids: related lanelet ids (ignore list, heatmap); pos: issue coordinates [x, y], None = position of the lanelets
        in ids
        """
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError(f"issue name '{name}': give only the name registered in ext/{self.category}/config/"
                             f"issue_codes.yaml (lower case, without '{self.category}.')")
        return self.printer.issue(self.category, f"{self.category}.{name}", text, ids, pos=pos)

    @property
    def warnings(self):
        """Number of WARNINGs printed so far"""
        return self.printer.count[self.category]

    @property
    def errors(self):
        """Number of ERRORs printed so far"""
        return self.printer.errors[self.category]

    @property
    def total(self):
        """Number of WARNINGs and ERRORs printed so far (the severity of an issue is set in config/issue_codes.yaml)"""
        return self.warnings + self.errors

    @property
    def ignored(self):
        """Number of issues not printed because of the ignore list"""
        return self.printer.ignored[self.category]
