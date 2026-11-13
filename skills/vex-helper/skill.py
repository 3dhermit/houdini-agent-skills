"""vex-helper - VEX generation, static lint, and real compile-check.

Two tiers on purpose:

* `lint` is pure Python. It runs in CI with no Houdini and catches the boring
  killers (unbalanced braces, C types VEX does not have, bad attribute sigils).
* `validate` is the honest one: it drops the snippet into a real Attribute
  Wrangle downstream of a Grid, cooks it, and reports Houdini's own compile
  errors. A snippet is only 'valid' when this passes.

Templates use explicit VEX constants rather than ch() lookups so they compile
without spare parms needing to exist first.
"""
from __future__ import annotations

import re

from houdini_skills.errors import SkillFailure, ok
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))

TEMPLATES: dict[str, dict] = {
    "point_displacement_noise": {
        "wrangle_kind": "point",
        "requires": ["@N on the input geometry"],
        "summary": "Displace points along their normal by layered noise.",
        "code": """\
// Point wrangle: organic displacement along normals.
float amplitude = 0.25;
float frequency = 1.8;
vector nrm = normalize(@N);
float n = noise(@P * frequency);
@P += nrm * (n - 0.5) * amplitude;
""",
    },
    "slope_mask": {
        "wrangle_kind": "point",
        "requires": ["@N"],
        "summary": "Writes f@steepness (0 flat .. 1 vertical) for slope-driven scattering.",
        "code": """\
// Point wrangle: slope mask for scatter / paint rules.
vector up = {0, 1, 0};
float align = dot(normalize(@N), up);
f@steepness = 1.0 - clamp(align, 0.0, 1.0);
""",
    },
    "curl_noise_velocity": {
        "wrangle_kind": "point",
        "requires": [],
        "summary": "Divergence-free swirl field into v@vel (pop/pyro seeding).",
        "code": """\
// Point wrangle: curl noise velocity.
float frequency = 0.6;
float amplitude = 2.0;
v@vel = curlnoise(@P * frequency) * amplitude;
""",
    },
    "random_packed_scale": {
        "wrangle_kind": "prim",
        "requires": ["compiles on any primitive input; feed f@piece_scale into a Pack/"
                     "Transform for packed prims rather than writing @scale directly"],
        "summary": "Deterministic per-piece scale variation written to a named attribute.",
        "code": """\
// Prim wrangle: per-piece scale jitter, stable across cooks.
// Written to a named attribute on purpose: on non-packed prims @scale is not a
// real attribute, so writing it directly fails to compile. Feed this into the
// scale channel of a Pack SOP, or use Attribute Rewrite on packed prims.
float seed = 7.0;
float lo = 0.6, hi = 1.4;
float s = fit01(rand(@primnum + seed), lo, hi);
f@piece_scale = s;
v@piece_scale3 = set(s, s, s);
""",
    },
    "volume_density_falloff": {
        "wrangle_kind": "volume",
        "requires": ["a volume with f@density"],
        "summary": "Radial density falloff for a pyro source volume.",
        "code": """\
// Volume wrangle: soften density outward from a centre.
vector centre = {0, 0, 0};
float radius = 1.5;
float amount = 1.0;
float d = length(@P - centre) / max(radius, 0.001);
f@density *= smooth(1.0, 0.0, d) * amount;
""",
    },
    "color_by_attribute": {
        "wrangle_kind": "point",
        "requires": ["any float point attribute named in attrib_name"],
        "summary": "Ramp colour from a float attribute, for lookdev debugging.",
        "code": """\
// Point wrangle: false-colour an attribute so you can SEE the sim data.
// VEX signature is point(geoinput, "attribname", pointnum) - attribute first.
string attrib_name = "steepness";
float lo = 0.0, hi = 1.0;
float v = point(0, attrib_name, @ptnum);
float t = fit01(v, lo, max(hi, lo + 0.001));
@Cd = set(t, 1.0 - t, 0.0);
""",
    },
    "distance_falloff": {
        "wrangle_kind": "point",
        "requires": [],
        "summary": "f@influence falling off from a world-space pivot.",
        "code": """\
// Point wrangle: influence mask from a pivot.
vector pivot = {0, 0, 0};
float radius = 3.0;
float d = length(@P - pivot);
f@influence = smooth(radius, 0.0, d);
""",
    },
}

# C types that do not exist in VEX - a very common LLM slip.
_BAD_TYPES = {
    "double": "use float", "long": "use int", "short": "use int",
    "unsigned": "not supported in VEX", "boolean": "use int 0/1",
    "char": "use string or int", "uint": "use int",
}
_ATTR_SIGIL = re.compile(r"(?P<prefix>\b[fivsc4m]?@|(?<![fivsc4m@])@)(?P<name>[A-Za-z_][A-Za-z_0-9]*)")
_PAIRS = {"(": ")", "{": "}", "[": "]"}


def run(action: str, payload: dict) -> dict:
    handler = _HANDLERS[action]
    if action in ("validate",):
        from houdini_skills.hou_api import get_session
        s = get_session()
        s.require(SUPPORTED, f"vex-helper.{action}")
        return handler(s, payload)
    return handler(None, payload)


def _list_templates(s, p):
    return ok({"templates": {k: {"wrangle_kind": v["wrangle_kind"], "summary": v["summary"],
                                 "requires": v["requires"]}
                             for k, v in TEMPLATES.items()}})


def _template(s, p):
    key = p["template"]
    if key not in TEMPLATES:
        from houdini_skills.errors import suggest
        raise SkillFailure("unknown_template", f"no VEX template {key!r}",
                           hint="available: " + ", ".join(sorted(TEMPLATES)),
                           details={"suggestions": suggest(key, sorted(TEMPLATES), 5)})
    tpl = TEMPLATES[key]
    code = _apply_overrides(tpl["code"], p.get("overrides") or {})
    kind = p.get("wrangle_kind") or tpl["wrangle_kind"]
    return ok({"template": key, "wrangle_kind": kind, "code": code,
               "requires": tpl["requires"], "summary": tpl["summary"],
               "lint": lint(code)})


def _explain(s, p):
    key = p["template"]
    if key not in TEMPLATES:
        raise SkillFailure("unknown_template", f"no VEX template {key!r}",
                           hint="available: " + ", ".join(sorted(TEMPLATES)))
    tpl = TEMPLATES[key]
    return ok({"template": key, "summary": tpl["summary"], "requires": tpl["requires"],
               "code": tpl["code"],
               "line_notes": _line_notes(tpl["code"])})


def _lint(s, p):
    findings = lint(p["code"])
    return ok({"code_length": len(p["code"]), "findings": findings,
               "clean": not [f for f in findings if f["severity"] == "error"]})


def lint(code: str) -> list[dict]:
    """Pure-Python static check. Not a compiler - catches only the cheap,
    frequent mistakes. `validate` is the authority."""
    findings: list[dict] = []
    if not code or not code.strip():
        findings.append({"severity": "error", "rule": "empty", "message": "no VEX source"})
        return findings

    stack: list[tuple[str, int]] = []
    line = 1
    in_str = in_comment_line = False
    for chx in code:
        if chx == "\n":
            line += 1
            in_comment_line = False
        if in_comment_line:
            continue
        if chx == "#" and not in_str:
            in_comment_line = True
            continue
        if chx in '"''':
            in_str = not in_str
        if in_str:
            continue
        if chx in _PAIRS:
            stack.append((chx, line))
        elif chx in _PAIRS.values():
            if not stack:
                findings.append({"severity": "error", "rule": "unbalanced",
                                 "message": f"stray '{chx}' at line {line}"})
            else:
                opener, oline = stack.pop()
                if _PAIRS[opener] != chx:
                    findings.append({"severity": "error", "rule": "unbalanced",
                                     "message": f"'{opener}' at line {oline} closed by "
                                                f"'{chx}' at line {line}"})
    for opener, oline in stack:
        findings.append({"severity": "error", "rule": "unbalanced",
                         "message": f"'{opener}' at line {oline} never closed"})

    for word, fix in _BAD_TYPES.items():
        if re.search(rf"(?<![A-Za-z_0-9]){word}\s+[A-Za-z_]", code):
            findings.append({"severity": "error", "rule": "bad_type",
                             "message": f"'{word}' is not a VEX type", "hint": fix})

    for m in _ATTR_SIGIL.finditer(code):
        name = m.group("name")
        if name in ("P", "N", "v", "Cd", "id", "ptnum", "primnum", "numpoints", "numprims"):
            continue
        if name in ("opinputpath",):
            continue

    if "s@`" in code or re.search(r"@\w+\(`", code):
        findings.append({"severity": "warning", "rule": "backtick_attr",
                         "message": "backtick attribute names need an existing string "
                                    "attribute; prefer a literal name"})
    if re.search(r"\bch\(\"", code):
        findings.append({"severity": "warning", "rule": "channel_ref",
                         "message": "ch()/chi()/chv() reference parms that may not exist "
                                    "on the wrangle; create them or inline a constant"})
    if "#include" in code:
        findings.append({"severity": "error", "rule": "c_preprocessor",
                         "message": "VEX has no C preprocessor"})

    # Discovered the hard way on 22.0.368: brace initializers accept constants
    # only. `{0, 1, 0}` parses; `{s, s, s}` fails with
    # "Syntax error, unexpected identifier". set(a, b, c) always parses.
    for m in re.finditer(r"\{\s*([^{}]*?)\s*\}", code):
        inner = m.group(1)
        if not inner or "," not in inner:
            continue
        if re.search(r"(?<![A-Za-z_0-9@.])[A-Za-z_][A-Za-z_0-9]*\s*,", inner) and \
                not re.fullmatch(r"[\s0-9.,\-+eExX]+", inner):
            findings.append({
                "severity": "warning", "rule": "brace_init_with_variables",
                "message": f"brace initializer with non-constants: {{{inner}}}",
                "hint": "on H22 an Attribute Wrangle rejects variable brace inits; "
                        "use set(a, b, c) or build a named vector variable first"})
            break
    return findings


def _validate(s, p):
    """Compile-check by cooking a real wrangle. The authority on validity."""
    if not p.get("code") and not p.get("template"):
        raise SkillFailure("invalid_input", "validate needs 'code' or 'template'")
    if p.get("template"):
        key = p["template"]
        if key not in TEMPLATES:
            raise SkillFailure("unknown_template", f"no VEX template {key!r}",
                               hint="available: " + ", ".join(sorted(TEMPLATES)))
        code = TEMPLATES[key]["code"]
        kind = p.get("wrangle_kind") or TEMPLATES[key]["wrangle_kind"]
    else:
        code, kind = p["code"], (p.get("wrangle_kind") or "point")

    geo = s.ensure_geo(p["container"])
    grid, wrangle = s.chain(geo, ["grid", "attribwrangle"], ["vexcheck_grid", "vexcheck_wrangle"])
    index, menu = _class_index(wrangle, kind)
    wrangle.parm("class").set(index)
    wrangle.parm("snippet").set(code)
    try:
        cook = s.cook(wrangle.path())
    except SkillFailure as exc:
        # Capture everything BEFORE destroying the node: hou.Node objects raise
        # ObjectWasDeleted once their node is gone, which hides the real cause.
        wrangle_path = wrangle.path()
        details = {"wrangle": wrangle_path, "errors": exc.err.details,
                   "static_lint": lint(code)}
        hint = _compile_hint(exc.err.details.get("errors", []))
        if not p.get("keep", False):
            _cleanup(s, geo)
        raise SkillFailure("vex_invalid", exc.err.message, hint=hint,
                           source="vex-helper.validate", details=details)
    stats = s.geometry_stats(wrangle.path())
    if not p.get("keep", False):
        _cleanup(s, geo)
    return ok({"valid": True, "wrangle_kind": kind, "class_parm_value": index,
               "class_menu": menu, "static_lint": lint(code),
               "cook": cook, "result": stats, "cleaned_up": not p.get("keep", False),
               "scope": "syntax/compile check on polygon input; semantic correctness "
                        "(e.g. a volume template over real voxels) is not proven",
               "node_kept_at": wrangle.path() if p.get("keep", False) else None})


def _cleanup(s, geo_path: str) -> None:
    try:
        s.hou.node(geo_path).destroy()
    except Exception:
        pass


def _compile_hint(errors: list) -> str:
    msgs = " | ".join(e.get("message", "") for e in errors) if errors else ""
    low = msgs.lower()
    for needle, advice in (
        ("syntax error", "the snippet does not parse: check for a stray operator, a "
                         "VEX-invalid assignment like '@x *= {a,b,c}' (build the vector "
                         "in a variable first), or a truncated paste"),
        ("no matching function", "argument order/types are wrong for this VEX function. "
                                 "The error lists the real signatures - read them: many "
                                 "VEX getters take (geo, attribute, index), not "
                                 "(geo, index, attribute)"),
        ("undefined function", "the function name is wrong for this VEX class; "
                               "check the VEX functions reference for your wrangle type"),
        ("type mismatch", "VEX is strict about vector vs float vs vector4 - cast explicitly"),
        ("unknown variable", "attribute may not exist upstream, or you are in the wrong "
                             "wrangle class (point vs prim vs detail)"),
        ("uninitialized variable", "a declaration failed above; fix the first error, "
                                   "the rest are usually cascade"),
    ):
        if needle in low:
            return advice
    return "read the cook error text; it carries the line number"


_CLASS_FALLBACK = {"point": 0, "prim": 1, "detail": 2, "volume": 3}


def _normalise_menu(menu) -> list:
    """hou MenuParmTemplate.menuItems() shape differs by build.

    Verified on 22.0.368: it returns bare strings, e.g.
    ('detail', 'primitive', 'point', 'vertex', 'number'). Older/newer builds
    may return (label, value) pairs, so accept both and key off position.
    """
    out = []
    for idx, item in enumerate(menu or []):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            out.append((str(item[0]), item[1]))
        else:
            out.append((str(item), idx))
    return out


def _class_index(wrangle, kind: str) -> tuple[int, list]:
    """Resolve the wrangle 'class' menu index from the *running* build's labels.

    Menu order and membership differ across versions, so never trust a
    hardcoded int when the node can tell us its own menu.
    """
    menu: list = []
    try:
        tpl = wrangle.parmTemplateGroup().find("class")
        menu = _normalise_menu(tpl.menuItems() if tpl else [])
    except Exception:
        menu = []
    want = kind.lower()
    for label, value in menu:
        if label.strip().lower() == want:
            return int(value), [l for l, _ in menu]
    for label, value in menu:
        if want in label.strip().lower():
            return int(value), [l for l, _ in menu]
    if menu:
        raise SkillFailure("parm_choice_unavailable",
                           f"the 'class' menu has no {kind!r} choice on this build",
                           hint="menu items: " + ", ".join(l for l, _ in menu),
                           details={"menu": [l for l, _ in menu]})
    return _CLASS_FALLBACK[kind], []


def _apply_overrides(code: str, overrides: dict) -> str:
    for name, value in (overrides or {}).items():
        pattern = rf"^(\s*(?:float|int|string|vector)\s+{re.escape(name)}\s*=\s*)[^;]+(;)"
        text = str(value)
        new, n = re.subn(pattern, lambda m: m.group(1) + text + m.group(2),
                         code, flags=re.MULTILINE)
        if n == 0:
            raise SkillFailure("invalid_input", f"template has no knob {name!r} to override",
                               hint="overrides must match a declared local constant")
        code = new
    return code


def _line_notes(code: str) -> list[dict]:
    out = []
    for i, ln in enumerate(code.splitlines(), 1):
        t = ln.strip()
        if not t or t.startswith("//"):
            continue
        out.append({"line": i, "code": t})
    return out


_HANDLERS = {
    "list_templates": _list_templates,
    "template": _template,
    "lint": _lint,
    "validate": _validate,
    "explain": _explain,
}
