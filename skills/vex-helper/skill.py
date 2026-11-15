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
        "node_type": "attribwrangle",
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
        "node_type": "attribwrangle",
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
        "node_type": "attribwrangle",
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
        "node_type": "attribwrangle",
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
        # Not an Attribute Wrangle. Verified on 22.0.368: `volumewrangle` is a real SOP
        # type with `snippet`, `autobind`, `bindeach`, `bindings`, `exportlist`, `group`
        # and NO `class` parm; this snippet cooks clean through it and leaves the
        # volume's points carrying only P (autobind writes the field itself).
        #
        # The same code in an Attribute Wrangle also reports 0 errors, and it does
        # something - in the wrong place. Proven by attribute placement on one build:
        #   class point  -> point attribs gain `density`
        #   class prim   -> prim attribs gain `density`
        #   class detail -> detail attribs gain `density`
        #   volumewrangle -> nothing is added; the VDB field is what changes
        # So "it compiled" proves nothing here, and "an attribute appeared" does not
        # mean the volume was edited.
        "node_type": "volumewrangle",
        "wrangle_kind": None,
        "requires": ["a volume with a float field named density"],
        "binding_note": "the Volume Wrangle binds VDB fields into VEX by name "
                        "(autobind/bindeach/bindings parms); its first input is labelled "
                        "'Volumes to Process with Wrangle' and aux inputs are read with "
                        "volumesample(1, ...)",
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
        "node_type": "attribwrangle",
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
        "node_type": "attribwrangle",
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


CATEGORY = "vex-helper"


def run(action: str, payload: dict) -> dict:
    # prepare() validates and fills schema defaults, so a direct skill.run()
    # behaves exactly like a CLI call through run_skill().
    from houdini_skills.registry import prepare
    payload = prepare(CATEGORY, action, payload)
    handler = _HANDLERS[action]
    if action in ("validate",):
        from houdini_skills.hou_api import get_session
        s = get_session()
        s.require(SUPPORTED, f"vex-helper.{action}")
        return handler(s, payload)
    return handler(None, payload)


def _list_templates(s, p):
    return ok({"templates": {k: {"node_type": v.get("node_type", "attribwrangle"),
                                 "wrangle_kind": v["wrangle_kind"],
                                 "summary": v["summary"],
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
    return ok({"template": key, "node_type": tpl.get("node_type", "attribwrangle"),
               "wrangle_kind": kind, "code": code,
               "requires": tpl["requires"], "summary": tpl["summary"],
               "binding_note": tpl.get("binding_note", ""),
               "lint": lint(code)})


def _explain(s, p):
    key = p["template"]
    if key not in TEMPLATES:
        raise SkillFailure("unknown_template", f"no VEX template {key!r}",
                           hint="available: " + ", ".join(sorted(TEMPLATES)))
    tpl = TEMPLATES[key]
    return ok({"template": key, "node_type": tpl.get("node_type", "attribwrangle"),
               "wrangle_kind": tpl["wrangle_kind"], "summary": tpl["summary"],
               "requires": tpl["requires"], "code": tpl["code"],
               "binding_note": tpl.get("binding_note", ""),
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

    # Discovered the hard way on 22.0.368: brace initializers accept numeric
    # constants only. `{0, 1, 0}` parses; `{s, s, s}` fails with "Syntax error,
    # unexpected identifier", and so does a member reference - `vector p =
    # {@P.x, 2.0, 1.0};` fails to cook while the old narrower regex stayed silent.
    # set(a, b, c) and named-vector construction always parse.
    for m in re.finditer(r"\{\s*([^{}]*?)\s*\}", code):
        inner = m.group(1)
        if not inner or "," not in inner:
            continue
        numeric_only = bool(re.fullmatch(r"[\s0-9.,\-+eExX]+", inner))
        has_name = bool(re.search(r"(?<![A-Za-z_0-9@.])[A-Za-z_][A-Za-z_0-9]*\s*[,}]|@",
                                  inner))
        if has_name and not numeric_only:
            findings.append({
                "severity": "warning", "rule": "brace_init_with_variables",
                "message": f"brace initializer with non-constants: {{{inner}}}",
                "hint": "on H22 a wrangle brace initializer takes numeric "
                        "constants only - identifiers AND member references such as "
                        "@P.x both fail to cook. Use set(a, b, c) or build a named "
                        "vector variable first"})
            break
    return findings


VALIDATE_NODE_TYPES = ("attribwrangle", "volumewrangle")


def _validate(s, p):
    """Compile-check by cooking a real node of the right kind. The authority on validity.

    Which node the code lands in is part of the claim, so it is chosen deliberately
    rather than always defaulting to an Attribute Wrangle. Verified on 22.0.368:
    `volumewrangle` is a real SOP type (parms `snippet`, `autobind`, `bindeach`,
    `bindings`, `exportlist`, `group`, `vdb_signedflood`, `vex_*` - and **no** `class`
    parm; input 0 is labelled 'Volumes to Process with Wrangle'), and this repo's
    density-falloff snippet cooks clean through it: 0 errors, 1 Volume prim out. The
    identical snippet inside an Attribute Wrangle also reports 0 errors while changing
    nothing at all - the volume's points still carry only `P`. A validity answer that
    ignores node type therefore proves syntax and nothing more.
    """
    if not p.get("code") and not p.get("template"):
        raise SkillFailure("invalid_input", "validate needs 'code' or 'template'")
    if p.get("template"):
        key = p["template"]
        if key not in TEMPLATES:
            raise SkillFailure("unknown_template", f"no VEX template {key!r}",
                               hint="available: " + ", ".join(sorted(TEMPLATES)))
        code = TEMPLATES[key]["code"]
        node_type = p.get("node_type") or TEMPLATES[key].get("node_type", "attribwrangle")
        kind = p.get("wrangle_kind") or TEMPLATES[key].get("wrangle_kind")
    else:
        code = p["code"]
        node_type = p.get("node_type") or "attribwrangle"
        kind = p.get("wrangle_kind") or "point"
    if node_type not in VALIDATE_NODE_TYPES:
        raise SkillFailure("invalid_input",
                           f"validate cannot build node type {node_type!r}",
                           hint="supported: " + ", ".join(VALIDATE_NODE_TYPES),
                           details={"node_type": node_type})

    geo = s.ensure_geo(p["container"])
    mark = len(s.created)
    class_value, class_menu = None, []
    authored, bindings = {}, {}
    if node_type == "volumewrangle":
        # Give the code a field to act on; without this "it cooked" is meaningless.
        sphere, volume, target = s.chain(
            geo, ["sphere", "volume", "volumewrangle"],
            ["vexcheck_sphere", "vexcheck_vol", "vexcheck_wv"])
        for parm, value in (("name", "density"), ("type", "float"),
                            ("rank", "scalar"), ("initialval1", 1)):
            parm_obj = volume.parm(parm)
            authored[parm] = None if parm_obj is None else (
                parm_obj.set(value), parm_obj.rawValue())[1]
        target.parm("snippet").set(code)
        for parm in ("autobind", "bindeach", "group"):
            parm_obj = target.parm(parm)
            bindings[parm] = None if parm_obj is None else parm_obj.rawValue()
    else:
        grid, target = s.chain(geo, ["grid", "attribwrangle"],
                              ["vexcheck_grid", "vexcheck_wrangle"])
        class_value, class_menu = _class_index(target, kind)
        target.parm("class").set(class_value)
        target.parm("snippet").set(code)

    try:
        cook = s.cook(target.path())
    except SkillFailure as exc:
        # Capture everything BEFORE destroying the node: hou.Node objects raise
        # ObjectWasDeleted once their node is gone, which hides the real cause.
        target_path = target.path()
        details = {"target": target_path, "node_type": node_type,
                   "errors": exc.err.details, "static_lint": lint(code)}
        hint = _compile_hint(exc.err.details.get("errors", []))
        if not p.get("keep", False):
            _cleanup(s, geo)
        raise SkillFailure("vex_invalid", exc.err.message, hint=hint,
                           source="vex-helper.validate", details=details)
    stats = s.geometry_stats(target.path())
    # Snapshot everything about the node BEFORE cleanup destroys it: a hou.Node
    # object raises ObjectWasDeleted afterwards, which is how every template here
    # managed to "fail" a cook it had actually passed.
    target_path = target.path()
    input_labels = list(target.inputLabels())
    created = list(s.created[mark:])
    if not p.get("keep", False):
        _cleanup(s, geo)
    return ok({
        "valid": True,
        "node_type": node_type,
        "wrangle_kind": kind if node_type == "attribwrangle" else None,
        "class_parm_value": class_value,
        "class_menu": class_menu,
        "volume_field_authored": authored,
        "volume_bindings": bindings,
        "input_labels": input_labels,
        "static_lint": lint(code),
        "cook": cook,
        "result": stats,
        "created_nodes": [] if p.get("keep", False) else created,
        "cleaned_up": not p.get("keep", False),
        "scope": ("syntax plus a real cook of " + node_type + " over a synthetic input; "
                  + ("the field 'density' genuinely exists there, so the binding is "
                     "exercised - your own volumes' resolutions and field names are "
                     "still not implied"
                     if node_type == "volumewrangle" else
                     "polygon input, so a volume-shaped snippet proves syntax only")),
        "node_kept_at": target_path if p.get("keep", False) else None,
    })


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


# Calls this skill will not question. Two sources, both honest:
#  1. names this repo's own templates call - those snippets cook with 0 errors under
#     hython Tier B on 22.0.368, so VEX demonstrably accepts them on this build;
#  2. a curated set of intrinsics used while building these skills.
# This is NOT Houdini's full builtin list (the install ships no machine-readable
# reference to parse), so the note wording says "not in this skill's verified list"
# rather than claiming a function does not exist.
_VERIFIED_CALLS_EXTRA = {
    "float", "int", "vector", "vector4", "matrix", "matrix3", "matrix4", "string",
    "set", "get", "length", "len", "dot", "cross", "normalize", "min", "max", "abs",
    "floor", "ceil", "round", "clamp", "smooth", "random", "rand", "noise",
    "noisefreq", "pnoise", "curlnoise", "curlnoise4d", "fit", "fit01", "fit10",
    "fit11", "remap", "comp", "vtransform", "xform", "totexture", "voxelfetch",
    "volumesample", "volumeindex", "intersect", "prim", "vertex", "point",
    "prcount", "pointcount", "vertexcount", "primnum", "pointnum", "vertexnum",
    "detail", "getbbox", "rotate", "split", "join", "strip", "lpad", "rpad",
    "upper", "lower", "title", "find", "printf", "sprintf", "error", "warning",
    "ch", "chi", "chf", "chv", "chs", "chsop", "chip", "opinputpath", "opfullpath",
    "atan2", "pow", "sqrt", "log", "exp", "sin", "cos", "tan", "asin", "acos",
    "degrees", "radians", "sign", "frac", "isnan", "isinf", "atan", "cbrt",
}

_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def _verified_calls() -> set:
    names = set(_VERIFIED_CALLS_EXTRA)
    for tpl in TEMPLATES.values():
        for match in _CALL_RE.finditer(tpl["code"]):
            names.add(match.group(1))
    return names


# VEX type sigils this skill is sure of; anything else is reported verbatim rather
# than guessed at, because a wrong type name in a note is worse than no name.
# VEX type sigils this skill is sure of; anything else is reported verbatim rather
# than guessed at, because a wrong type name in a note is worse than no name.
_SIGIL = {"f": "float", "i": "int", "v": "vector", "s": "string", "b": "bool",
          "4": "vector4", "3": "matrix3", "2": "matrix2"}

_VEX_KEYWORDS = {"if", "else", "elif", "for", "while", "switch", "do", "return",
                 "break", "continue", "int", "float", "vector", "vector4", "matrix",
                 "matrix3", "matrix4", "string", "void"}

_RE_WRITE = re.compile(r"^\s*(?:(?P<sigil>[a-v0-9])?@)?(?P<attr>\w+)\s*(?:[-+*/]?=)\s*[^=]")
_RE_CONTROL = re.compile(r"\b(?P<kw>if|else|elif|for|while|switch|do)\b")
_RE_DECLARE = re.compile(r"\b(?P<type>float|int|vector|vector4|matrix|matrix3|matrix4|"
                         r"string|int\[\]|float\[\]|string\[\])\s+(?P<name>\w+)")
_RE_READ = re.compile(r"\b(?P<sigil>[a-v0-9])@(?P<attr>\w+)")
_RE_CHANNEL = re.compile(r"\bch(?:[ifvs]|sop|ip)?\(|opinputpath")


def _sigil_word(sigil: str) -> str:
    word = _SIGIL.get(sigil)
    return word or ("unknown type sigil " + sigil)


def _note_for(line: str, verified: set) -> str:
    """Rule-based note for one VEX line: structural, not semantic.

    `explain` deliberately does not pretend to understand what the code computes.
    The note says what the line *is* (a write, a declaration, a call on something the
    skill has not seen compile) so an agent can spot the suspicious parts and go read
    them. Validity itself is settled by cooking the code in validate().
    """
    note = ""
    write = _RE_WRITE.match(line)
    control = _RE_CONTROL.search(line)
    declare = _RE_DECLARE.search(line)
    if write:
        sigil = write.group("sigil")
        attr = write.group("attr")
        note = ("writes " + attr + " (" + _sigil_word(sigil) + " attribute)"
                if sigil else "assigns " + attr)
    elif declare:
        note = f"declares local {declare.group('name')} of type {declare.group('type')}"
    elif control:
        note = f"control flow ({control.group('kw')})"
    elif _RE_CHANNEL.search(line):
        note = "reads a channel or op-input path from the node, not from geometry"
    elif re.search(r"\breturn\b", line):
        note = "returns from the snippet"
    elif _RE_READ.search(line):
        read = _RE_READ.search(line)
        note = ("reads " + read.group("attr") + " ("
                + _sigil_word(read.group("sigil")) + " attribute)")
    elif _CALL_RE.search(line):
        note = "calls " + _CALL_RE.search(line).group(1) + "()"
    if not note:
        note = "expression / assignment"
    # Typos hide inside declarations and conditions, so the call check runs over the
    # whole line regardless of which shape won.
    suspect = sorted({name for name in _CALL_RE.findall(line)
                      if name not in verified and name not in _VEX_KEYWORDS})
    if suspect:
        note += ("; calls " + ", ".join(suspect) + " - not in this skill's "
                 "verified-call list (names its own templates compiled with on "
                 "22.0.368), so check the VEX reference before trusting it")
    return note


def _line_notes(code: str) -> list[dict]:
    verified = _verified_calls()
    out = []
    for i, ln in enumerate(code.splitlines(), 1):
        t = ln.strip()
        if not t or t.startswith("//"):
            continue
        out.append({"line": i, "code": t, "note": _note_for(t, verified)})
    return out


_HANDLERS = {
    "list_templates": _list_templates,
    "template": _template,
    "lint": _lint,
    "validate": _validate,
    "explain": _explain,
}
