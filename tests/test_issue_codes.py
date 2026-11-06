"""
Tests of the issue codes (config/issue_codes.yaml for Checks 1~3, ext/<name>/config/issue_codes.yaml for each extension,
utils/issue_codes.py): the registries, the codes used by the checks, severities, ignore lists by code and the Summary:
  cd MapChecker && python3 -m unittest discover -s tests -v
"""

import ast
import contextlib
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from test_extensions import BASE_DIR, run_extensions, write_map  # also puts libs/ and MapChecker/ on sys.path

from check_lanelet_map import report_codes  # noqa: E402
from utils import ext as extmod  # noqa: E402
from utils.checks import CORE_CATEGORIES  # noqa: E402
from utils.context import MapContext  # noqa: E402
from utils.ignore import ignore_by_category, load_ignore  # noqa: E402
from utils.issue_codes import CODE, DEFAULT_PATH, IssueCodes, check_of, read_extension_codes  # noqa: E402
from utils.printer import Printer  # noqa: E402

OPTS = {"min_overlap_area": 1.0, "parallel_min_length": 10.0, "parallel_min_gap": 1.5, "turn_angle": 30.0,
        "turn_window": 30.0, "only_check_AOI": False}


def string_literals(paths):
    """String constants of the Python files, and the constant first argument of the .issue(...) calls"""
    literals, issued = set(), set()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "issue" and node.args \
                    and isinstance(node.args[0], ast.Constant):
                issued.add(node.args[0].value)
    return literals, issued


def all_codes():
    """Codes of Checks 1~3 and of every extension of ext/"""
    return extmod.register_codes(IssueCodes.load(), extmod.discover().values())


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.map_path = os.path.join(self.tmp, "map.osm")
        write_map(self.map_path)  # the crosswalk extends 2 m beyond the road on both sides

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, data, name="issue_codes.yaml"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)
        return path

    def test_core_registry_matches_source(self):
        """config/issue_codes.yaml has the codes of Checks 1~3 and of the framework, exactly those used in utils/"""
        literals, _ = string_literals(glob.glob(os.path.join(BASE_DIR, "utils", "*.py")))
        checks = set(CORE_CATEGORIES) | {"ext"}
        used = {s for s in literals if CODE.fullmatch(s) and check_of(s) in checks}
        registered = set(IssueCodes.load().codes)
        self.assertEqual(sorted(used - registered), [], f"codes used in utils/ but missing from {DEFAULT_PATH}")
        self.assertEqual(sorted(registered - used), [], f"codes of {DEFAULT_PATH} used nowhere in utils/")
        self.assertEqual(sorted(c for c in registered if check_of(c) not in checks), [],
                         "codes of extensions belong in ext/<name>/config/issue_codes.yaml")

    def test_extension_registries_match_source(self):
        """Every extension has its own config/issue_codes.yaml, with exactly the issues its scripts report"""
        for name, ext in extmod.discover().items():
            with self.subTest(extension=name):
                self.assertIsNotNone(ext.codes, f"missing {ext.codes_path}")
                registered = {code.partition(".")[2] for code in ext.codes}
                self.assertTrue(registered, f"{ext.codes_path} registers no issue")
                literals, issued = string_literals(glob.glob(os.path.join(ext.path, "script", "*.py")))
                self.assertEqual(sorted(issued - registered), [], f"out.issue() names missing from {ext.codes_path}")
                self.assertEqual(sorted(registered - literals), [], f"issues of {ext.codes_path} never reported")

    def test_load_errors(self):
        good = {"severity": "warning", "title": "t"}
        IssueCodes.load(self.write({"right_of_way.missing": good}))
        for data, message in (([1], "top level"),
                              ({"Right_of_way.Missing": good}, "lower case"),
                              ({"missing": good}, "<check>.<issue>"),
                              ({"right_of_way.missing": "warning"}, "must be a mapping"),
                              ({"right_of_way.missing": {**good, "severity": "info"}}, "severity must be one of"),
                              ({"right_of_way.missing": {"severity": "error"}}, "missing title"),
                              ({"right_of_way.missing": {**good, "severtiy": "off"}}, "unknown field")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                IssueCodes.load(self.write(data))
        # the keys of an extension file are issue names, the code is <extension>.<issue>
        self.assertEqual(read_extension_codes(self.write({"polyline": good}, "ext.yaml"), "stopline"),
                         {"stopline.polyline": {**good, "description": ""}})
        with self.assertRaisesRegex(ValueError, "an issue name such as polyline"):
            read_extension_codes(self.write({"stopline.polyline": good}, "ext.yaml"), "stopline")

    def test_extension_codes_only_in_their_package(self):
        """A code of an extension in config/issue_codes.yaml is refused: it belongs in the extension's own file"""
        core = self.write({"right_of_way.missing": {"severity": "warning", "title": "t"},
                           "crosswalk.beyond_road": {"severity": "off", "title": "t"}})
        with self.assertRaisesRegex(ValueError, "codes crosswalk.\\* belong to extension crosswalk, register them in "
                                                ".*ext/crosswalk/config/issue_codes.yaml"):
            extmod.register_codes(IssueCodes.load(core), extmod.discover().values())

    def test_severity(self):
        """The severity comes from the registry: error, warning, off (not printed, counted apart); unknown codes
        raise"""
        codes = IssueCodes({"a.err": {"severity": "error", "title": ""}, "a.warn": {"severity": "warning", "title": ""},
                            "a.off": {"severity": "off", "title": ""}})
        out = Printer(False, codes=codes)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(out.issue("a", "a.err", "e", ("1",)), "[ERROR a.err] e")
            self.assertEqual(out.issue("a", "a.warn", "w", ("1",)), "[WARNING a.warn] w")
            self.assertIsNone(out.issue("a", "a.off", "o", ("1",)))
        self.assertEqual(buf.getvalue(), "[ERROR a.err] e\n[WARNING a.warn] w\n")
        self.assertEqual((out.errors["a"], out.count["a"], dict(out.by_code), dict(out.disabled)),
                         (1, 1, {"a.err": 1, "a.warn": 1}, {"a.off": 1}))
        with self.assertRaisesRegex(ValueError, "unknown issue code 'a.nope'"):
            out.issue("a", "a.nope", "x")

        # the training data does not depend on the chosen severities
        self.assertEqual({entry["severity"] for entry in codes.as_warnings().codes.values()}, {"warning"})

    def test_extension_severity_changed(self):
        """The severity in the extension's issue_codes.yaml: ERROR instead of WARNING, or not reported (Summary)"""
        ext = extmod.load(extmod.discover()["crosswalk"], {"max_extension": 0.5})
        entry = ext.codes["crosswalk.beyond_road"]
        for severity, expected in (("warning", (1, 0, 0)), ("error", (0, 1, 0)), ("off", (0, 0, 1))):
            with self.subTest(severity=severity):
                ext.codes = {**ext.codes, "crosswalk.beyond_road": {**entry, "severity": severity}}
                out = Printer(False)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    extmod.run_extensions(MapContext.from_file(self.map_path, OPTS), [ext], out)
                    report_codes(out)
                self.assertEqual((out.count["crosswalk"], out.errors["crosswalk"],
                                  out.disabled["crosswalk.beyond_road"]), expected)
                if severity == "off":
                    self.assertIn("not reported (severity off): crosswalk.beyond_road 1", buf.getvalue())
                else:
                    self.assertRegex(buf.getvalue(), f"issues by code:\n  crosswalk.beyond_road  {severity} +1  "
                                                     f"crosswalk extends more than")

    def write_ext(self, script, codes):
        """Extension mine in a new directory (a script already imported from a directory is not imported again)"""
        ext_dir = tempfile.mkdtemp(dir=self.tmp)
        for sub in ("script", "config"):
            os.makedirs(os.path.join(ext_dir, "mine", sub))
        with open(os.path.join(ext_dir, "mine", "config", "config.yaml"), "w", encoding="utf-8") as f:
            f.write("enable: true\n")
        codes_path = os.path.join(ext_dir, "mine", "config", "issue_codes.yaml")
        if codes is not None:
            with open(codes_path, "w", encoding="utf-8") as f:
                f.write(codes)
        with open(os.path.join(ext_dir, "mine", "script", "check_mine.py"), "w", encoding="utf-8") as f:
            f.write(script)
        return extmod.discover(ext_dir)["mine"]

    def test_extension_without_its_codes(self):
        """Every extension needs its config/issue_codes.yaml; an issue missing from it, or given with the extension
        name, is reported as a crash of the extension"""
        with self.assertRaisesRegex(ValueError, "extension 'mine': missing .*issue_codes.yaml, every extension "
                                                "registers the issues it reports there"):
            extmod.load(self.write_ext("def check(ctx, cfg, out):\n    pass\n", None))

        for issue, message in (("oops", "ValueError: unknown issue code 'mine.oops', register it in .*/mine/config/"
                                        "issue_codes.yaml"),
                               ("mine.found", "ValueError: issue name 'mine.found': give only the name registered")):
            with self.subTest(issue=issue):
                ext = self.write_ext(f"def check(ctx, cfg, out):\n    out.issue('{issue}', 'x')\n",
                                     "found: {severity: warning, title: t}\n")
                out, text = run_extensions([extmod.load(ext)], self.map_path)
                self.assertEqual(out.errors["mine"], 1)
                self.assertRegex(text, r"\[ERROR ext.crashed\] extension mine crashed: " + message)

        ext = self.write_ext("def check(ctx, cfg, out):\n    out.issue('found', 'x')\n",
                             "found: {severity: error, title: t}\n")
        out, text = run_extensions([extmod.load(ext)], self.map_path)
        self.assertIn("[ERROR mine.found] x", text)


class IgnoreByCodeTest(unittest.TestCase):
    """config/ignore.yaml with an issue code as key: only that issue type is ignored for the listed lanelets"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_load_and_scope(self):
        path = os.path.join(self.tmp, "ignore.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("all: [614]\nstopline.polyline: [52: drawn on purpose]\nright_of_way.missing: [7]\n")
        entries = load_ignore(path, ["stopline"], all_codes().codes)
        self.assertEqual(entries, [("all", "614", ""), ("stopline.polyline", "52", "drawn on purpose"),
                                   ("right_of_way.missing", "7", "")])
        with self.assertRaisesRegex(ValueError, "unknown key 'stopline.polyline'.*issue code"):
            load_ignore(path, ["stopline"], IssueCodes.load().codes)  # without the codes of the extensions
        # code lists only kept for the checks that run
        self.assertEqual(ignore_by_category(entries, ("stopline",)),
                         {"stopline": {"614": ""}, "stopline.polyline": {"52": "drawn on purpose"}})

    def test_only_that_code(self):
        out = Printer(False, ignore={"a": {}, "a.one": {"1": ""}},
                      codes=IssueCodes({"a.one": {"severity": "warning", "title": ""},
                                        "a.two": {"severity": "warning", "title": ""}}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(out.issue("a", "a.one", "x", ("1", "2")))
            self.assertTrue(out.issue("a", "a.two", "y", ("1", "2")))
            self.assertTrue(out.issue("a", "a.one", "z", ("2",)))
        self.assertEqual((out.count["a"], out.ignored["a"], out.suppressed[("a.one", "1")]), (2, 1, 1))

    def test_cli(self):
        """Summary per code, ignore by code in ignore.yaml, codes of an extension refused in config/issue_codes.yaml"""
        map_path = os.path.join(self.tmp, "map.osm")
        write_map(map_path)
        registry = os.path.join(self.tmp, "issue_codes.yaml")
        ignore = os.path.join(self.tmp, "ignore.yaml")
        cfg = os.path.join(self.tmp, "config.yaml")

        def run(ignore_text, extra_code=None):
            with open(DEFAULT_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if extra_code:
                data[extra_code] = {"severity": "off", "title": "t"}
            with open(registry, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f)
            with open(ignore, "w", encoding="utf-8") as f:
                f.write(ignore_text)
            with open(cfg, "w", encoding="utf-8") as f:
                f.write(f"input: {map_path}\nignore_file: {ignore}\nissue_codes_file: {registry}\n"
                        "log: {enabled: false}\nheatmap: {enabled: false}\nUsing_LR: false\n")
            return subprocess.run([sys.executable, os.path.join(BASE_DIR, "check_lanelet_map.py"), "-c", cfg,
                                   "--no-color", "--only-ext", "crosswalk", "--set", "crosswalk.max_extension=0.5"],
                                  capture_output=True, text=True)

        r = run("{}\n")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("[WARNING crosswalk.beyond_road] crosswalk 22: extends up to 2.00 m beyond the road", r.stdout)
        self.assertRegex(r.stdout, r"issues by code:\n  crosswalk.beyond_road  warning +1  crosswalk extends more")

        r = run("crosswalk.beyond_road: [22: wide on purpose]\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lanelet  22       [crosswalk.beyond_road] 1 issue(s) suppressed | wide on purpose", r.stdout)

        r = run("crosswalk.nope: [22]\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown key 'crosswalk.nope'", r.stderr)

        r = run("{}\n", extra_code="crosswalk.beyond_road")  # turning it off here instead of in ext/crosswalk/
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid issue codes file", r.stderr)
        self.assertIn("belong to extension crosswalk", r.stderr)


if __name__ == "__main__":
    unittest.main()
