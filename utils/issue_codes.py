"""
Issue codes: every WARNING / ERROR has a stable code <check>.<issue> and a severity.
Codes of Checks 1~3 (and of the framework) are in config/issue_codes.yaml; each extension check registers its own in
ext/<name>/config/issue_codes.yaml, whose keys are the issue names only (the code is <name>.<issue>)
"""

import os
import re

import yaml

from .config import BASE_DIR

DEFAULT_PATH = os.path.join(BASE_DIR, "config", "issue_codes.yaml")
SEVERITIES = ("error", "warning", "off")
FIELDS = {"severity", "title", "description"}
NAME = re.compile(r"[a-z0-9_]+")                       # issue name, the part after the check name
CODE = re.compile(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+")  # <check>.<issue>, the check being a check or extension name


def read_entries(path, pattern, example):
    """Read an issue codes file {key: {"severity", "title", "description"}}, the keys matching pattern"""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping of issue codes")
    entries = {}
    for key, entry in data.items():
        where = f"{path}: '{key}'"
        if not isinstance(key, str) or not pattern.fullmatch(key):
            raise ValueError(f"{where}: expected {example}, in lower case")
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: must be a mapping with severity and title")
        unknown = set(entry) - FIELDS
        if unknown:
            raise ValueError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}, "
                             f"expected {', '.join(sorted(FIELDS))}")
        if entry.get("severity") not in SEVERITIES:
            raise ValueError(f"{where}: severity must be one of {', '.join(SEVERITIES)}, got {entry.get('severity')!r}")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise ValueError(f"{where}: missing title")
        entries[key] = {"description": "", **entry}
    return entries


def read_extension_codes(path, name):
    """
    Issue codes of extension name from its config/issue_codes.yaml {<name>.<issue>: entry}; the keys of the file are the
    issue names
    """
    return {f"{name}.{issue}": entry
            for issue, entry in read_entries(path, NAME, "an issue name such as polyline (the code is "
                                                         "<extension>.<issue>)").items()}


class IssueCodes:
    """Registered issue codes {code: {"severity", "title", "description"}}, and the file of each check {check: path}"""

    def __init__(self, codes, path=DEFAULT_PATH, sources=None):
        self.codes = codes
        self.path = path
        self.sources = {check_of(code): path for code in codes} if sources is None else sources

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        """Read the codes of Checks 1~3 (config/issue_codes.yaml), ValueError on problems"""
        return cls(read_entries(path, CODE, "<check>.<issue>, e.g. right_of_way.missing"), path)

    def add_extension(self, name, codes, path):
        """
        Register the codes of extension name ({<name>.<issue>: entry}, from read_extension_codes()) read from path;
        ValueError if codes of this extension are in another file (e.g. config/issue_codes.yaml)
        """
        if self.sources.get(name, path) != path:
            raise ValueError(f"{self.sources[name]}: codes {name}.* belong to extension {name}, "
                             f"register them in {path}")
        self.sources[name] = path
        self.codes.update(codes)
        return self

    def severity(self, code):
        """error / warning / off; ValueError if the code is not registered"""
        if code not in self.codes:
            where = self.sources.get(check_of(code)) or \
                f"{self.path} (Checks 1~3) or in ext/{check_of(code)}/config/issue_codes.yaml"
            raise ValueError(f"unknown issue code '{code}', register it in {where}")
        return self.codes[code]["severity"]

    def title(self, code):
        return self.codes[code]["title"] if code in self.codes else ""

    def as_warnings(self):
        """
        Copy in which every code is a WARNING: the training data (utils/build_dataset.py) does not depend on the
        severities chosen in the files (scored WARNINGs that would be ERRORs or off are still recorded)
        """
        return IssueCodes({code: {**entry, "severity": "warning"} for code, entry in self.codes.items()}, self.path,
                          dict(self.sources))


def check_of(code):
    """Check (or extension) name of a code: stopline.polyline -> stopline"""
    return code.partition(".")[0]
