# skills: each subfolder is one agent skill (SKILL.md + meta.json + skill.py).
# Loaded by houdini_skills.registry - and note that category folder names use
# hyphens ("sop-automation"), which are NOT legal Python identifiers, so skills
# are loaded by file path via import_skill_module(), never by import path.
from __future__ import annotations

import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_cache: dict = {}


def skill_dir(category: str) -> str:
    return os.path.join(_HERE, category)


def import_skill_module(category: str):
    """Load skills/<category>/skill.py regardless of hyphens in the folder name."""
    if category in _cache:
        return _cache[category]
    path = os.path.join(skill_dir(category), "skill.py")
    if not os.path.isfile(path):
        raise FileNotFoundError("no skill.py in " + skill_dir(category))
    safe = "houdini_skill_" + category.replace("-", "_")
    spec = importlib.util.spec_from_file_location(safe, path)
    mod = importlib.util.module_from_spec(spec)
    _cache[category] = mod
    spec.loader.exec_module(mod)
    return mod


load = import_skill_module
get = import_skill_module
