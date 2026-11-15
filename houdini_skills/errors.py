"""Uniform error envelope for every skill.

An agent should be able to read the result of any skill call without parsing
prose. Every failure comes back as:

    {"ok": false, "error": {"kind": ..., "message": ..., "hint": ...,
     "source": ..., "recovered": ...}}

`hint` is where the teaching lives: the most common Houdini agent mistakes are
wrong node type names and wrong parm names, so we attach a "did you mean"
suggestion whenever the running Houdini can supply one.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field

# Common confusion between the hou module's real names and names that feel
# right but do not exist. Seed of a living table; extend via tests.
HOUSING = {
    "version": "applicationVersion() / applicationVersionString()",
    "versionString": "applicationVersionString()",
    "build": "applicationVersion() (hou.build() is not a module function)",
    "createNode": "node.createNode() on a HDA/node object, not hou.createNode()",
    "selectedNodes": "hou.selectedNodes() exists; hou.selectedPrims() does not",
}


@dataclass
class SkillError:
    kind: str
    message: str
    hint: str = ""
    source: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SkillFailure(Exception):
    """Raise to return a structured error instead of a traceback."""

    def __init__(self, kind: str, message: str, hint: str = "", source: str = "",
                 details: dict | None = None):
        super().__init__(message)
        self.err = SkillError(kind, message, hint, source, details or {})

    @property
    def kind(self) -> str:
        return self.err.kind

    def __str__(self) -> str:
        """Include the hint and the closest matches.

        Agents usually see str(exception), not to_result(): when the suggestion lives
        only in `hint`, a refusal like "'polyexdiv' is not valid under /obj/..." tells
        them what failed and not what to type instead. Verified in live pass 9c, where
        the SkillFailure carried 'closest: subdivide, ...' but str() dropped it.
        """
        parts = [self.err.message]
        if self.err.hint:
            parts.append(self.err.hint)
        text = " | ".join(p for p in parts if p)
        if self.err.source:
            text += f" (in {self.err.source})"
        return text

    def to_result(self) -> dict:
        return {"ok": False, "error": self.err.to_dict()}


def ok(payload: dict) -> dict:
    return {"ok": True, **payload}


def suggest(name: str, candidates, n: int = 5) -> list[str]:
    """'Did you mean' over an arbitrary candidate list (node types, parms)."""
    if not name:
        return []
    low = name.lower()
    exact = [c for c in candidates if c.lower() == low]
    if exact:
        return exact[:n]
    return difflib.get_close_matches(low, [str(c) for c in candidates], n=n, cutoff=0.6)


def hou_name_error(exc: BaseException) -> SkillError:
    """Turn an AttributeError from hou into a teachable error.

    'module hou has no attribute version. Did you mean: session?'
    """
    text = str(exc)
    m = re.search(r"has no attribute '?([A-Za-z_0-9]+)'?", text)
    name = m.group(1) if m else ""
    hint = HOUSING.get(name, "")
    if not hint:
        m2 = re.search(r"Did you mean:?\s*(.+)", text)
        if m2:
            hint = f"hou suggests: {m2.group(1).strip()}"
    return SkillError(
        kind="bad_api_name",
        message=text,
        hint=hint or "check hou module docs for the running version",
        source="hou",
        details={"attribute": name},
    )


def parse_node_errors(node) -> list[dict]:
    """Read real cook errors off a Houdini node (22.x node.errors() / node.warnings()).

    Returns [{severity, message}] with cook failures first. Warnings are kept
    separate so a green-but-warning node is never reported as broken.
    """
    out: list[dict] = []
    for severity, getter in (("error", "errors"), ("warning", "warnings")):
        fn = getattr(node, getter, None)
        if fn is None:
            continue
        try:
            items = fn()
        except Exception:  # API differs by version; never mask the real problem
            continue
        for it in items or []:
            msg = getattr(it, "detailedMessage", None)
            if callable(msg):
                try:
                    msg = msg()
                except Exception:
                    msg = ""
            out.append({"severity": severity, "message": str(msg or it)})
    return out
