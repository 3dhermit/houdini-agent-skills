"""Tier A tests - pure Python, no Houdini. These are what CI can actually run.

Why two tiers: GitHub-hosted runners cannot legally install Houdini without a
SideFX account and a licence redeem code, so anything that needs `hou` must run
either on Jake's machine or a self-hosted runner. Tier A still covers schema
validation, version gating, VEX static lint, registry/metadata hygiene and the
error envelope - roughly the parts most likely to regress on a PR.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from houdini_skills.errors import SkillFailure, suggest  # noqa: E402
from houdini_skills.registry import discover  # noqa: E402
from houdini_skills.schema import validate  # noqa: E402
from houdini_skills.versions import (MIN_SUPPORTED, check_supported,  # noqa: E402
                                     parse_version, series_range)


def hou_available() -> bool:
    return importlib.util.find_spec("hou") is not None


REAL_ONLY = unittest.skipUnless(hou_available(), "needs hython / a Houdini install")


class TestSchema(unittest.TestCase):
    def test_required_missing_is_structured(self):
        schema = {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}
        with self.assertRaises(SkillFailure) as cm:
            validate({}, schema)
        err = cm.exception.to_result()["error"]
        self.assertEqual(err["kind"], "invalid_input")
        self.assertIn("path", err["message"])

    def test_unknown_key_rejected_with_known_keys(self):
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        with self.assertRaises(SkillFailure) as cm:
            validate({"path": "/obj/a", "typo": 1}, schema)
        err = cm.exception.to_result()["error"]
        self.assertIn("path", err["hint"])

    def test_defaults_filled(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer", "default": 7}}}
        self.assertEqual(validate({}, schema), {"n": 7})

    def test_enum_rejects_and_lists_values(self):
        schema = {"type": "object", "properties": {"e": {"type": "string", "enum": ["a", "b"]}}}
        with self.assertRaises(SkillFailure) as cm:
            validate({"e": "c"}, schema)
        self.assertIn("a, b", cm.exception.to_result()["error"]["hint"])

    def test_integer_accepts_whole_float(self):
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        self.assertEqual(validate({"n": 5.0}, schema)["n"], 5)

    def test_min_max(self):
        schema = {"type": "object", "properties": {"s": {"type": "number", "minimum": 0.05}}}
        with self.assertRaises(SkillFailure):
            validate({"s": 0.001}, schema)


class TestVersions(unittest.TestCase):
    def test_parse(self):
        v = parse_version("22.0.368")
        self.assertEqual((v.major, v.minor, v.build), (22, 0, 368))
        self.assertEqual(v.series, (22, 0))

    def test_floor(self):
        self.assertIsNotNone(check_supported(parse_version("18.5.402"), series_range((19, 5), (22, 0))))

    def test_inside_range(self):
        self.assertIsNone(check_supported(parse_version("20.5.300"), series_range((19, 5), (22, 0))))

    def test_karma_gate_matches_reality(self):
        """Karma exists from H20; the rendering skill must fail loudly on 19.5."""
        rendering = importlib.import_module("skills.rendering.skill")
        self.assertLess(MIN_SUPPORTED, rendering.KARMA_MIN)
        reason = check_supported(parse_version("19.5.584"), rendering.ALL)
        self.assertIsNone(reason, "19.5 is inside the repo floor; the Karma gate is separate")
        self.assertTrue(rendering.KARMA_MIN >= (20, 0))

    def test_suggest(self):
        self.assertEqual(suggest("polybevel", ["polybevel", "grid"])[0], "polybevel")
        self.assertIn("polybevel", suggest("polybevle", ["polybevel", "grid"]))


class TestRegistry(unittest.TestCase):
    EXPECTED = {"sop-automation", "vex-helper", "simulation-tools",
                "rendering", "usd-pipeline", "troubleshooting"}
    REQUIRED_META = ("name", "version", "description", "houdini_versions",
                     "dependencies", "examples", "actions", "sources")

    def setUp(self):
        self.skills = discover()

    def test_all_six_categories_present(self):
        cats = {s.category for s in self.skills.values()}
        self.assertEqual(cats, self.EXPECTED, f"skills/ must hold exactly {sorted(self.EXPECTED)}")

    def test_metadata_hygiene(self):
        for spec in self.skills.values():
            for key in self.REQUIRED_META:
                self.assertIn(key, spec.meta, f"{spec.category}/meta.json missing {key}")
            self.assertTrue(spec.meta["sources"], f"{spec.category} needs a sources list")
            for src in spec.meta["sources"]:
                self.assertIn("license", src, f"{spec.category} source missing license")
                self.assertTrue(src.get("used_for"), f"{spec.category} source must say what was used")

    def test_every_action_has_an_object_schema(self):
        for spec in self.skills.values():
            for action, schema in spec.meta["actions"].items():
                self.assertIsInstance(schema, dict, f"{spec.category}.{action} schema must be an object")
                self.assertEqual(schema.get("type"), "object",
                                 f"{spec.category}.{action} must declare type: object")

    def test_every_action_has_a_handler(self):
        for spec in self.skills.values():
            mod = spec.module()
            handlers = getattr(mod, "_HANDLERS", {})
            for action in spec.meta["actions"]:
                self.assertIn(action, handlers,
                              f"{spec.category}: action {action} declared but not implemented")

    def test_skill_md_present_with_frontmatter(self):
        for spec in self.skills.values():
            path = os.path.join(spec.dir, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"{spec.category} needs SKILL.md")
            text = open(path, encoding="utf-8").read()
            self.assertTrue(text.startswith("---"), f"{spec.category}/SKILL.md needs frontmatter")
            self.assertIn("description:", text)

    def test_versions_are_json_loadable(self):
        for spec in self.skills.values():
            with open(os.path.join(spec.dir, "meta.json"), encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertEqual(raw["name"], spec.meta["name"])

    def test_handlers_only_require_keys_the_schema_guarantees(self):
        """A handler may read p["x"] only if x is required or has a schema default.

        prepare() fills defaults, so those are safe; an optional key with no
        default is a KeyError waiting at the call site. sop-automation find/limit
        hit exactly this before the check existed.
        """
        import re
        offenders = []
        for spec in self.skills.values():
            guaranteed = set()
            for schema in spec.meta["actions"].values():
                guaranteed.update(schema.get("required", []))
                for key, ks in (schema.get("properties", {}) or {}).items():
                    if isinstance(ks, dict) and "default" in ks:
                        guaranteed.add(key)
            with open(os.path.join(spec.dir, "skill.py"), encoding="utf-8") as fh:
                src = fh.read()
            keys = set(re.findall(r'\bp\["([a-z_0-9]+)"\]', src))
            keys |= set(re.findall(r'\bpayload\["([a-z_0-9]+)"\]', src))
            for key in sorted(keys - guaranteed):
                offenders.append(f'{spec.category}: p["{key}"]')
            if "prepare(" not in src:
                offenders.append(f"{spec.category}: run() does not call registry.prepare(), "
                                 "so a direct skill.run() skips validation and defaults")
        self.assertEqual(offenders, [], "unguarded payload reads: " + ", ".join(offenders))


class TestCliContract(unittest.TestCase):
    """The CLI contract has to hold from a shell, not just in-process.

    Every expectation here was produced by running the commands on 22.0.368 /
    this machine; if an exit code or error kind changes, this test says so before
    an agent's automation does.
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _cli(self, *args):
        import subprocess
        return subprocess.run([sys.executable, "-m", "houdini_skills.cli", *args],
                              cwd=self.ROOT, capture_output=True, text=True)

    def test_list_is_json_exit_zero(self):
        r = self._cli("--list")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["skills"])

    def test_pure_action_works_without_houdini(self):
        r = self._cli("troubleshooting", "hou_lookup", "--json", '{"name":"parmNames"}')
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertTrue(data["ok"])
        self.assertFalse(data["exists_in_hou"])

    def test_scene_action_without_houdini_is_structured_not_a_traceback(self):
        r = self._cli("sop-automation", "grid_bevel",
                      "--json", '{"container":"x","size":[1,0,1],"divisions":2}')
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["kind"], "houdini_unavailable")
        self.assertNotIn("Traceback", r.stderr,
                         "an expected condition leaked a traceback")

    def test_bad_payload_is_a_structured_error(self):
        r = self._cli("sop-automation", "grid_bevel", "--json", "{not json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["error"]["kind"], "invalid_json")

    def test_unknown_skill_lists_the_real_ones(self):
        r = self._cli("nope-skill", "go", "--json", "{}")
        self.assertEqual(r.returncode, 1)
        err = json.loads(r.stdout)["error"]
        self.assertEqual(err["kind"], "unknown_skill")
        self.assertIn("sop-automation", err["hint"])

    def test_argparse_misuse_exits_two(self):
        r = self._cli("--not-a-flag")
        self.assertEqual(r.returncode, 2)


class TestSkillTreeCheck(unittest.TestCase):
    """tools/check_skills.py is what CI runs; it has to be right, not just green.

    Regression for the v1.0.0 tag push (2026-11-13): the same walk existed as a
    heredoc in ci.yml, where skills/__pycache__ - created by an earlier build
    step - was counted as a skill folder and failed all three Tier A jobs.
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _tool(self):
        path = os.path.join(self.ROOT, "tools", "check_skills.py")
        spec = importlib.util.spec_from_file_location("check_skills", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _tree(self, tmp, name, *, complete=True, sources=None):
        d = os.path.join(tmp, name)
        os.makedirs(d)
        if complete:
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("# skill\n")
            with open(os.path.join(d, "skill.py"), "w", encoding="utf-8") as fh:
                fh.write("def run(a, p):\n    return {}\n")
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name, "actions": {"go": {"description": "x"}},
                       "sources": sources or []}, fh)
        return d

    def test_caches_and_hidden_dirs_are_not_skills(self):
        import tempfile
        tool = self._tool()
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp, "good-skill")
            os.makedirs(os.path.join(tmp, "__pycache__"))
            with open(os.path.join(tmp, "__pycache__", "x.pyc"), "wb") as fh:
                fh.write(b"\x00")
            os.makedirs(os.path.join(tmp, ".vcs"))
            self.assertEqual(tool.check_complete(tmp), [])
            self.assertEqual(tool.check_licences(tmp), [])
            self.assertEqual(len(tool.candidate_dirs(tmp)), 1)

    def test_incomplete_skill_is_named(self):
        import tempfile
        tool = self._tool()
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp, "half-skill", complete=False)
            problems = tool.check_complete(tmp)
            self.assertEqual(len(problems), 1)
            self.assertIn("half-skill", problems[0])
            self.assertIn("SKILL.md", problems[0])

    def test_non_permissive_source_is_an_offender(self):
        import tempfile
        tool = self._tool()
        with tempfile.TemporaryDirectory() as tmp:
            self._tree(tmp, "skill-a", sources=[{"repo": "x/y", "license": None,
                                                 "used_for": "code"}])
            self._tree(tmp, "skill-b", sources=[{"repo": "x/z", "license": None,
                                                 "used_for": "NOTHING (reviewed)"}])
            offenders = tool.check_licences(tmp)
            self.assertEqual(len(offenders), 1, "the reviewed-and-unused entry "
                                                "must not be flagged")
            self.assertIn("x/y", offenders[0])

    def test_the_real_tree_passes_with_caches_present(self):
        """Exactly the CI condition: the walk runs after something imported skills/."""
        import tempfile
        tool = self._tool()
        cache = os.path.join(self.ROOT, "skills", "__pycache__")
        os.makedirs(cache, exist_ok=True)
        try:
            self.assertEqual(tool.check_complete(os.path.join(self.ROOT, "skills")), [])
            self.assertEqual(tool.check_licences(os.path.join(self.ROOT, "skills")), [])
            self.assertEqual(tool.main([os.path.join(self.ROOT, "skills")]), 0)
        finally:
            for f in os.listdir(cache):
                os.remove(os.path.join(cache, f))
            os.rmdir(cache)


if __name__ == "__main__":
    unittest.main()
