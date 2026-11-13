"""Skill registry: discovers skills/*/meta.json and exposes their actions."""
from __future__ import annotations

import importlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "skills")


class SkillSpec:
    def __init__(self, path: str, meta: dict):
        self.dir = path
        self.meta = meta

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def category(self) -> str:
        return os.path.basename(self.dir)

    def module(self):
        # Category names contain hyphens, which are not valid Python module
        # paths, so load skill.py by file path.
        from skills import import_skill_module
        return import_skill_module(self.category)

    def to_dict(self) -> dict:
        m = self.meta
        return {
            "name": m["name"],
            "category": self.category,
            "version": m.get("version", "0.0.0"),
            "houdini_versions": m.get("houdini_versions", []),
            "verified_on": m.get("verified_on", []),
            "dependencies": m.get("dependencies", []),
            "actions": sorted(m.get("actions", {}).keys()),
            "schemas": m.get("actions", {}),
            "examples": m.get("examples", []),
            "description": m.get("description", ""),
            "triggers": m.get("triggers", []),
            "sources": m.get("sources", []),
        }


def discover(skills_dir: str = SKILLS_DIR) -> dict[str, SkillSpec]:
    out: dict[str, SkillSpec] = {}
    if not os.path.isdir(skills_dir):
        return out
    for entry in sorted(os.listdir(skills_dir)):
        d = os.path.join(skills_dir, entry)
        meta_path = os.path.join(d, "meta.json")
        if os.path.isdir(d) and os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            meta.setdefault("name", entry)
            spec = SkillSpec(d, meta)
            out[spec.name] = spec
    return out


def get(name: str) -> SkillSpec:
    skills = discover()
    if name not in skills:
        from .errors import SkillFailure, suggest
        raise SkillFailure("unknown_skill", f"no skill named {name!r}",
                           hint="available: " + ", ".join(sorted(skills)),
                           details={"suggestions": suggest(name, sorted(skills), 5)})
    return skills[name]


def by_category(category: str):
    for spec in discover().values():
        if spec.category == category:
            return spec
    return None


def prepare(category: str, action: str, payload: dict) -> dict:
    """Validate a payload and fill schema defaults for a direct skill.run() call.

    run_skill() - the CLI door - already validates. Skills call this themselves so
    both doors behave identically: without it, an optional knob with a default
    arrives as a missing key and the handler dies with a bare KeyError instead of
    a structured error. Found by the tier A hygiene check after sop-automation
    find/limit raised KeyError in a direct call.
    """
    from .errors import SkillFailure
    from .schema import validate

    spec = by_category(category)
    if spec is None:
        raise SkillFailure("broken_skill", f"no skill registered for category {category!r}",
                           hint="check the 'category' field in skills/*/meta.json")
    schemas = spec.meta.get("actions", {})
    if action not in schemas:
        raise SkillFailure("unknown_action", f"{category} has no action {action!r}",
                           hint="actions: " + ", ".join(sorted(schemas)),
                           details={"suggestions": sorted(schemas)})
    try:
        return validate(payload, schemas[action])
    except SkillFailure as exc:
        exc.err.source = f"{category}.{action}"
        exc.err.details.setdefault("schema", schemas[action])
        raise


def run_skill(name: str, action: str, payload: dict) -> dict:
    """Entry point every caller uses: validate input, dispatch, wrap errors."""
    from .errors import SkillFailure
    from .schema import validate

    spec = get(name)
    schemas = spec.meta.get("actions", {})
    if action not in schemas:
        raise SkillFailure("unknown_action", f"{name} has no action {action!r}",
                           hint="actions: " + ", ".join(sorted(schemas)),
                           details={"suggestions": sorted(schemas)})
    try:
        clean = validate(payload, schemas[action])
    except SkillFailure as exc:
        exc.err.source = f"{name}.{action}"
        exc.err.details.setdefault("schema", schemas[action])
        raise
    mod = spec.module()
    fn = getattr(mod, "run", None)
    if fn is None:
        raise SkillFailure("broken_skill", f"{name} has no run() function")
    return fn(action, clean)
