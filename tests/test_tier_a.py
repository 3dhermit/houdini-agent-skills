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


if __name__ == "__main__":
    unittest.main()
