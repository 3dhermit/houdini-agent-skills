#!/usr/bin/env python3
"""Guard: every payload knob a skill's meta.json declares must be READ by its handler.

Why this exists: a schema key that no code reads is a lie an agent can act on. The
`simulation-tools pyro` action shipped in v1.0.0 advertising `cache_dir` and
`resolution_scale`; nothing read either, and on 22.0.368 the Pyro Solver SOP has no
resolution or cache-path parm to bind them to (670 parms, none matching). A caller
could only discover that by probing the node itself.

Resolution is static and deliberately generous about indirection: a handler's body
plus the bodies of every module-level function it calls **with the payload variable**
(transitively) counts as reading. Run from CI and from tests/test_tier_a.py.

Exit 0 = clean. Exit 1 = at least one declared knob is never read.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _def_bodies(src: str) -> dict[str, tuple[str, str]]:
    """name -> (payload_arg_name, body) for every module-level def."""
    marks = [(m.start(), m.group(1), m.group(2)) for m in re.finditer(
        r"^def ([A-Za-z_0-9]+)\(([^)]*)\)", src, re.M)]
    out: dict[str, tuple[str, str]] = {}
    for i, (pos, name, args) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
        body = src[pos:end]
        names = [a.split("=")[0].strip() for a in args.split(",") if a.strip()]
        # the payload is conventionally p / payload / payload_dict
        payload = next((n for n in names if n in ("p", "payload", "payload_dict")), "")
        out[name] = (payload, body)
    return out


def _handlers_map(src: str) -> dict[str, str]:
    """_HANDLERS = {"action": _func, ...} found by brace matching, not one regex."""
    # anchor on the *definition* line, not the first mention (run() uses
    # `_HANDLERS[action]`, and the '{' after that belongs to an unrelated dict)
    dm = re.search(r"^_HANDLERS\s*[:=]", src, re.M)
    if not dm:
        return {}
    i = dm.start()
    j = src.find("{", i)
    if j < 0:
        return {}
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    block = src[j:k + 1]
    return dict(re.findall(r'"([a-z_0-9]+)":\s*([A-Za-z_0-9]+)', block))


def _reachable_body(fname: str, bodies: dict) -> str:
    """Handler body plus every function it reaches while passing the payload along."""
    seen: set[str] = set()
    joined = []

    def walk(name: str, payload_var: str, depth: int) -> None:
        if name in seen or depth > 6 or name not in bodies:
            return
        seen.add(name)
        own_payload, body = bodies[name]
        joined.append(body)
        if not payload_var:
            return
        for call in re.finditer(r"\b([A-Za-z_0-9]+)\(([^()]*)\)", body):
            callee, args = call.group(1), call.group(2)
            if callee not in bodies or callee == name:
                continue
            if re.search(r"\b%s\b" % re.escape(payload_var), args):
                walk(callee, bodies[callee][0], depth + 1)

    walk(fname, bodies.get(fname, ("", ""))[0], 0)
    return "\n".join(joined)


def check(root: pathlib.Path = ROOT) -> list[str]:
    problems: list[str] = []
    for meta in sorted((root / "skills").glob("*/meta.json")):
        src = (meta.parent / "skill.py").read_text(encoding="utf-8")
        bodies = _def_bodies(src)
        handlers = _handlers_map(src)
        schema = json.loads(meta.read_text(encoding="utf-8"))
        for action, spec in (schema.get("actions") or {}).items():
            props = list((spec.get("properties") or {}).keys())
            if not props:
                continue
            fname = handlers.get(action)
            if not fname:
                problems.append(f"{meta.parent.name}.{action}: no _HANDLERS entry, "
                                f"cannot prove any knob is read")
                continue
            body = _reachable_body(fname, bodies)
            for key in props:
                read_pats = [
                    re.compile(r'(?:\bp|payload)\s*\[\s*["\']%s["\']\s*\]' % re.escape(key)),
                    re.compile(r'(?:\bp|payload)\s*(?:\.get)?\(\s*["\']%s["\']'
                               % re.escape(key)),
                ]
                if not any(rx.search(body) for rx in read_pats):
                    problems.append(
                        f"{meta.parent.name}.{action}: declares '{key}' but no code "
                        f"reads it (handler {fname})")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("payload knob audit FAILED - declared knobs nothing reads:")
        for p in problems:
            print("  " + p)
        return 1
    print("payload knob audit clean: every declared knob is read by its handler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
