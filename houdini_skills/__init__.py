"""houdini_skills - a shared, version-aware wrapper over Houdini's `hou` module.

v1 supports Houdini 19.5+. The modules here are deliberately import-safe
outside Houdini: importing this package on a system Python must work so that
schema validation, VEX templating and the error knowledge base can be unit
tested in CI without a Houdini install.
"""
from .errors import SkillError, SkillFailure, ok, suggest  # noqa: F401
from .registry import discover, get, run_skill  # noqa: F401
from .versions import HoudiniNotAvailable, MIN_SUPPORTED, parse_version  # noqa: F401

__version__ = "2.0.0"
__all__ = ["SkillFailure", "SkillError", "ok", "suggest", "discover", "get",
           "run_skill", "parse_version", "MIN_SUPPORTED", "HoudiniNotAvailable",
           "__version__"]
