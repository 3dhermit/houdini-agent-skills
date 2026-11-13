"""troubleshooting - diagnose against the running build, not from memory.

The knowledge base is tiered honestly: every entry carries `verified`, meaning
either "reproduced by our own test suite on 22.0.368" (with the test name) or
false, i.e. advice from general practice that we have not reproduced here.
"""
from __future__ import annotations

import difflib
import re

from houdini_skills.errors import HOUSING, SkillFailure, ok, parse_node_errors, suggest
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))

KNOWLEDGE_BASE = [
    {
        "id": "hou-no-parmnames",
        "match": r"has no attribute '?parmNames'?",
        "cause": "hou.Node has no parmNames() method; it is a natural guess and it does not exist.",
        "fix": "Use node.parms() / node.parmTuples() (or node.allParms()) and read .name() off each.",
        "verified": True,
        "evidence": "reproduced on 22.0.368; tests/test_tier_b.py exercised it via Session.set_parms",
    },
    {
        "id": "hou-no-version",
        "match": r"has no attribute '?version(String)?'?",
        "cause": "hou.version() and hou.versionString() do not exist.",
        "fix": "Use hou.applicationVersion() (tuple) or hou.applicationVersionString(); "
               "hou.isApprentice() tells you the licence mode.",
        "verified": True,
        "evidence": "reproduced on 22.0.368 during package development",
    },
    {
        "id": "hou-no-parmtemplate",
        "match": r"has no attribute '?parmTemplate'?",
        "cause": "Parm templates are not read straight off a Node.",
        "fix": "node.parmTemplateGroup().find('parm') then .menuItems() for menus.",
        "verified": True,
        "evidence": "reproduced on 22.0.368 while resolving the wrangle class menu",
    },
    {
        "id": "hou-no-parmtype",
        "match": r"has no attribute '?parmType'?",
        "cause": "hou.Parm exposes no parmType()/template() accessor in this build.",
        "fix": "Inspect behaviour instead: parm.rawValue() / parm.eval(); menu choices come "
               "from parmTemplateGroup().find(name).menuItems().",
        "verified": True,
        "evidence": "reproduced on 22.0.368",
    },
    {
        "id": "vex-brace-init-variables",
        "match": r"unexpected identifier",
        "cause": "Most often a brace initializer holding variables, e.g. "
                 "v@scale = {s, s, s}; - H22's wrangle parser accepts only constant "
                 "brace initializers like {0, 1, 0}.",
        "fix": "Use set(a, b, c), or assign a named variable first. Also check for a "
               "truncated paste, which produces the same message at the cut point.",
        "verified": True,
        "evidence": "reproduced on 22.0.368: '{s,s,s}' and 'vector j = {a,a,a}' fail to "
                    "parse while 'set(a,a,a)' and '{1.0,1.0,1.0}' compile. Caught by "
                    "tests/test_tier_b.py::TestVexValidation and now flagged by the "
                    "brace_init_with_variables lint rule",
    },
    {
        "id": "no-object-level-sim-node",
        "match": r"(pyro|rbd|flip|vellum).{0,40}(not a valid|could not be found|no object|not found)",
        "cause": "Houdini has no OBJ-level 'pyro'/'rbd'/'flip' node type. The only "
                 "simulation object is a DOP Network ('dopnet'); the solvers and sources "
                 "are SOPs: pyrosolver, pyrosource, rbdbulletsolver, rbdpack, rbdconfigure, "
                 "flipsolver, flipsource, vellumsolver.",
        "fix": "Build the source/solver chain in SOPs and let a dopnet drive it; verify "
               "names with hou.sopNodeTypeCategory().nodeTypes() before creating them.",
        "verified": True,
        "evidence": "reproduced on 22.0.368 by skills/simulation-tools discovery; "
                    "hou.objNodeTypeCategory() contains dopnet but no pyro/rbd/flip",
    },
    {
        "id": "hou-objectwasdeleted",
        "match": r"(ObjectWasDeleted|no longer exists in Houdini)",
        "cause": "A hou.Node/Prim Python object was used after its node was destroyed - "
                 "very often because an error handler cleaned up first and then tried to "
                 "read the node's own path.",
        "fix": "Capture paths and error text into plain Python values BEFORE destroying "
               "anything.",
        "verified": True,
        "evidence": "reproduced on 22.0.368 in this repo's own vex-helper validate path; "
                    "fixed by capturing details before _cleanup",
    },
    {
        "id": "hython-no-hdefereval",
        "match": r"No module named '?hdefereval'?",
        "cause": "hdefereval needs a running Houdini GUI event loop; it does not import in hython.",
        "fix": "Run that code in the Houdini GUI, or restructure the tool so headless work "
               "never depends on the UI event loop.",
        "verified": False,
        "evidence": "general practice, not reproduced in this repo's tests",
    },
    {
        "id": "unknown-node-type",
        "match": r"(could not be found|Unknown operator type|not a valid node type|unknown node type)",
        "cause": "The node type name does not exist in that network category, or was renamed "
                 "between Houdini majors.",
        "fix": "Query the category instead of guessing: hou.sopNodeTypeCategory().nodeTypes(), "
               "objNodeTypeCategory(), ropNodeTypeCategory(); then create with the exact key.",
        "verified": False,
        "evidence": "advice; our own wrapper raises unknown_node_type and is tested, Houdini's "
                    "exact wording is version-dependent",
    },
    {
        "id": "empty-geo",
        "match": r"(empty geometry|no geometry|0 prims|contains no primitives)",
        "cause": "Commonly: display flag on the wrong node, an Object Merge pointing at a "
                 "non-display SOP, an inactive/muted input, a sim that was never stepped, or a "
                 "File Cache set to load a range that was never written.",
        "fix": "Walk upstream with empty_geo_report; check display flag, input activity, and "
               "whether the sim has actually been cooked at the current frame.",
        "verified": False,
        "evidence": "advice from practice; empty_geo_report automates the checks",
    },
    {
        "id": "sim-not-moving",
        "match": r"(sim(ulation)? (did not|didn't) (move|step)|pieces (sit|stay) still|nothing falls)",
        "cause": "Usually correct physics: source pieces start at rest, upright, supported. "
                 "No energy in, no motion out.",
        "fix": "Add energy deliberately - lean, lift, initial velocity, or an impactor - and "
               "confirm the DOP graph is actually cooking the frames (step the timeline rather "
               "than relying on a single cook).",
        "verified": False,
        "evidence": "advice; encoded preventively in the simulation-tools skill",
    },
    {
        "id": "karma-missing",
        "match": r"(karma).{0,40}(could not be found|not (a valid|found)|unknown)",
        "cause": "Karma ships from Houdini 20. On 19.5 the type genuinely does not exist.",
        "fix": "Use mantra on 19.5, or upgrade. Check with hou.ropNodeTypeCategory().nodeTypes(). "
               "Note the reverse drift too: on 22.x mantra itself is unregistered, so a "
               "mantra fallback written for the 19.5 floor will fail on a modern build.",
        "verified": True,
        "evidence": "rendering skill's KARMA_MIN gate; asserted in tests/test_tier_a.py, and "
                    "tests/test_tier_b.py::TestVerifiedApiFacts.test_mantra_absent_on_22",
    },
    {
        "id": "mantra-rop-gone",
        "match": r"mantra.{0,40}(no ROP type|not (a valid|found|registered)|could not be found)",
        "cause": "The mantra ROP is no longer registered on Houdini 22.0.368 - it is absent "
                 "from hou.ropNodeTypeCategory().nodeTypes() (90 types scanned). Code written "
                 "against the 19.5-21.x floor breaks forward, not just backward.",
        "fix": "On 22.x use engine='karma_cpu'. Never assume an older node type still exists: "
               "enumerate the category and let the skill raise engine_unavailable with the real list.",
        "verified": True,
        "evidence": "verified on 22.0.368; locked by "
                    "tests/test_tier_b.py::TestVerifiedApiFacts.test_mantra_absent_on_22",
    },
    {
        "id": "sop-wrangle-type-name",
        "match": r"(wrangle|pyro).{0,60}(not a valid|not registered|could not be found|NodeTypeDefinition)",
        "cause": "The SOP type is 'attribwrangle', not 'wrangle', and there is no SOP type "
                 "'pyro' either - it is 'pyrosource'/'pyrosolver'. Obvious names are frequently wrong.",
        "fix": "Query the category: hou.sopNodeTypeCategory().nodeTypes(). The registered name "
               "can differ from the menu label, and node.type().name() returns a versioned name "
               "like 'filecache::2.0'.",
        "verified": True,
        "evidence": "verified on 22.0.368; tests/test_tier_b.py::TestVerifiedApiFacts."
                    "test_wrangle_sop_type_name",
    },
    {
        "id": "hou-no-allchildren",
        "match": r"has no attribute '?allChildren'?|ObjectWasDeleted",
        "cause": "hou.Node has no allChildren(); the recursive call is allSubChildren(). "
                 "children() exists but is one level deep, so a node search or cleanup "
                 "pattern under /obj/geo returns nothing instead of failing. Separately, "
                 "a node handle read after destroy() raises ObjectWasDeleted.",
        "fix": "Use hou.node('/').allSubChildren(); read node.path() before destroy() and "
               "skip handles that are already dead.",
        "verified": True,
        "evidence": "verified on 22.0.368 (hython suggests allSubChildren); locked by "
                    "tests/test_tier_b.py::TestVerifiedApiFacts.test_node_walk_uses_allsubchildren",
    },
    {
        "id": "unresolved-knob-names",
        "match": r"unresolved_knobs",
        "cause": "The skill asked for parm names that do not exist on this build. Common false "
                 "guesses verified on 22.0.368: filecache 'outputname'/'t1'/'method' (real: "
                 "sopoutput/f1/filemethod), scatter 'perpoint'/'mindist' (real: npts/maxradius), "
                 "copytopoints 'pastepoints' (real: pack/pivot), karma 'renderer'/'schedule'.",
        "fix": "Call sop-automation read_parms (or Session.parm_names) on the node first, then "
               "set what actually exists. 'unresolved' means the value was NOT applied.",
        "verified": True,
        "evidence": "verified on 22.0.368 by dumping node.parms() for filecache::2.0, "
                    "scatter::2.0, copytopoints::2.0 and karma; locked by "
                    "tests/test_tier_b.py::TestVerifiedApiFacts",
    },
]

# Causes of empty geometry, checked in order by empty_geo_report.
EMPTY_GEO_CHECKS = [
    ("node_missing", "the path does not resolve"),
    ("wrong_category", "an object or LOP was queried instead of a SOP"),
    ("no_geometry", "node has no geometry object at all"),
    ("zero_prims", "geometry exists but has no primitives"),
    ("input_muted", "the incoming SOP input is muted/passthrough"),
    ("display_flag", "the container's display flag is elsewhere"),
]


CATEGORY = "troubleshooting"


def run(action: str, payload: dict) -> dict:
    # prepare() validates and fills schema defaults, so a direct skill.run()
    # behaves exactly like a CLI call through run_skill().
    from houdini_skills.registry import prepare
    payload = prepare(CATEGORY, action, payload)
    if action in ("hou_lookup", "explain_error"):
        return _HANDLERS[action](None, payload)
    s = get_session()
    s.require(SUPPORTED, f"troubleshooting.{action}")
    return _HANDLERS[action](s, payload)


def _explain_error(s, p):
    msg = p["message"]
    hits = [e for e in KNOWLEDGE_BASE if re.search(e["match"], msg, re.I)]
    if not hits:
        # nearest entries so the agent still gets something useful
        near = difflib.get_close_matches(msg.lower(),
                                         [e["id"] + " " + e["cause"].lower() for e in KNOWLEDGE_BASE],
                                         n=3, cutoff=0.3)
        return ok({"matched": [], "message": msg,
                   "closest_entries": near,
                   "note": "no KB entry matched; treat as new and add it via CONTRIBUTING.md"})
    return ok({"matched": hits, "count": len(hits),
               "verified_count": sum(1 for h in hits if h["verified"])})


def _hou_lookup(s, p):
    name = p["name"]
    try:
        import hou
        members = [m for m in dir(hou)]
    except ImportError:
        members = sorted(HOUSING)
    known = name in members
    return ok({
        "name": name,
        "exists_in_hou": known,
        "candidates": suggest(name, members, 8),
        "known_confusions": HOUSING.get(name, ""),
        "note": "" if members else "hou not importable; suggestions come from the "
                                   "built-in confusion table only",
    })


def _diagnose_node(s, p):
    node = s.node(p["path"])
    errs = parse_node_errors(node)
    hard = [e for e in errs if e["severity"] == "error"]
    explained = []
    for e in hard:
        for entry in KNOWLEDGE_BASE:
            if re.search(entry["match"], e["message"], re.I):
                explained.append({"id": entry["id"], "cause": entry["cause"],
                                  "fix": entry["fix"], "verified": entry["verified"]})
                break
    inputs = []
    for i, inp in enumerate(node.inputs()):
        inputs.append({"index": i, "path": inp.path() if inp else None,
                       "state": str(inp.inputState(i if inp else 0)) if inp else ""})
    return ok({"path": node.path(), "type": s._type(node),
               "errors": hard, "warnings": [e for e in errs if e["severity"] == "warning"],
               "explained": explained, "inputs": inputs,
               "suggested_actions": _suggest_actions(node, hard)})


def _suggest_actions(node, hard) -> list[str]:
    out = []
    for e in hard:
        m = e["message"].lower()
        if "parm" in m or "channel" in m:
            out.append("read_parms on this node and compare against docs/verified-api.md")
        if "type" in m:
            out.append("list_types for this network, then re-create with the exact name")
    if not out:
        out.append("cook the first upstream node and walk forward until it goes empty")
    return out


def _empty_geo_report(s, p):
    node = s.node(p["path"])
    findings = []
    cat = node.type().category().name()
    if cat != "SOP":
        findings.append({"check": "wrong_category",
                         "detail": f"{p['path']} is a {cat} node, not a SOP"})
    geo = None
    try:
        geo = node.geometry()
    except Exception:
        pass
    if geo is None:
        findings.append({"check": "no_geometry", "detail": "node exposes no geometry object"})
    else:
        prims = list(geo.prims())
        if not prims:
            findings.append({"check": "zero_prims", "detail": "geometry has 0 primitives"})
    for i, inp in enumerate(node.inputs()):
        if inp is None:
            continue
        try:
            state = node.inputOutputState(i)
        except Exception:
            state = None
        if state is not None and str(state) not in ("InputOutputState.ACTIVE", "0"):
            findings.append({"check": "input_muted",
                             "detail": f"input {i} from {inp.path()} is {state}"})
    up = node.inputs()
    walk = []
    cur = node
    for _ in range(12):
        ins = [i for i in cur.inputs() if i is not None]
        if not ins:
            break
        cur = ins[0]
        g = None
        try:
            g = cur.geometry()
        except Exception:
            pass
        count = len(list(g.prims())) if g is not None else -1
        walk.append({"path": cur.path(), "type": s._type(cur), "prims": count})
        if count > 0:
            break
    return ok({"path": node.path(), "category": cat,
               "findings": findings, "upstream_walk": walk,
               "first_non_empty": next((w["path"] for w in walk if w["prims"] > 0), None),
               "checks_available": [c[0] for c in EMPTY_GEO_CHECKS]})


def _node_type_lookup(s, p):
    name = p["name"].lower()
    hits = {}
    for label, getter in (
        ("SOP", lambda: s.hou.sopNodeTypeCategory().nodeTypes()),
        ("OBJ", lambda: s.hou.objNodeTypeCategory().nodeTypes()),
        ("DOP", lambda: s.hou.dopNodeTypeCategory().nodeTypes()),
        ("LOP", lambda: s.hou.lopNodeTypeCategory().nodeTypes()),
        ("ROP", lambda: s.hou.ropNodeTypeCategory().nodeTypes()),
        ("COP", lambda: s.hou.copNodeTypeCategory().nodeTypes()),
    ):
        try:
            types = set(getter().keys())
        except Exception:
            continue
        exact = [t for t in types if t.lower() == name]
        near = suggest(name, types, 6)
        if exact or near:
            hits[label] = {"exact": exact, "close": near}
    if not hits:
        raise SkillFailure("no_such_node_type", f"nothing like {p['name']!r} in any category",
                           hint="try a broader keyword, e.g. 'voronoi', 'wrangle', 'cache'")
    return ok({"query": p["name"], "categories": hits})


def _version_report(s, p):
    apprentice = False
    try:
        apprentice = bool(s.hou.isApprentice())
    except Exception:
        pass
    cats = {}
    for label, getter in (("SOP", s.hou.sopNodeTypeCategory), ("OBJ", s.hou.objNodeTypeCategory),
                          ("LOP", s.hou.lopNodeTypeCategory), ("ROP", s.hou.ropNodeTypeCategory)):
        try:
            cats[label] = len(getter().nodeTypes())
        except Exception:
            cats[label] = None
    from houdini_skills.versions import MIN_SUPPORTED
    return ok({"version": str(s.version), "series": list(s.version.series),
               "apprentice_licence": apprentice, "node_type_counts": cats,
               "repo_floor": list(MIN_SUPPORTED), "frame_range": s.frame_range()})


_HANDLERS = {
    "diagnose_node": _diagnose_node,
    "hou_lookup": _hou_lookup,
    "explain_error": _explain_error,
    "node_type_lookup": _node_type_lookup,
    "empty_geo_report": _empty_geo_report,
    "version_report": _version_report,
}
