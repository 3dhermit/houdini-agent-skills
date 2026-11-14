"""rendering - ROP configuration through the shared wrapper.

Scope conflict recorded up front: the repo's v1 floor is Houdini 19.5, but
Karma does not exist before Houdini 20. So on 19.5 this skill raises
`engine_unavailable` with a mantra fallback instead of quietly creating a node
type that is not there. Karma CPU/XPU node names are discovered per build.

This skill configures a Render Output ROP. It does not render. Rendering is
slow, costs a licence, and writes files - that stays an explicit human action.
"""
from __future__ import annotations

from houdini_skills.errors import SkillFailure, ok
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

ALL = series_range((19, 5), (22, 0))
KARMA_MIN = (20, 0)

ENGINE_TYPES = {
    "karma_cpu": ["karma"],
    "karma_xpu": ["karma_xpu", "karma"],
    "mantra": ["mantra"],
}
PARM_CANDIDATES = {
    # Names verified on 22.0.368 Karma ROP - see docs/verified-api.md
    "resolutionx": ["resolutionx", "res_overridex", "resx"],
    "resolutiony": ["resolutiony", "res_overridey", "resy"],
    "output_path": ["picture", "vm_picture", "outputpath"],
    "start": ["f1", "saverangeStart", "startframe"],
    "end": ["f2", "saverangeEnd", "endframe"],
    "range_enable": ["trange"],
    "samples": ["samplesperpixel", "samples", "maxsamples"],
}

# Verified on 22.0.368: the Karma ROP has NO parm that selects CPU vs XPU
# (no 'renderer', 'rendermethod', 'schedule' or any *xpu* parm in its full parm
# list), and there is no 'karma_xpu' node type. XPU is a licence/render-setting
# concern, not a ROP knob, so this skill reports that instead of silently
# setting a made-up parm.
XPU_PARM_HINTS = ("renderer", "rendermethod", "schedule", "xpu")


CATEGORY = "rendering"


def run(action: str, payload: dict) -> dict:
    # prepare() validates and fills schema defaults, so a direct skill.run()
    # behaves exactly like a CLI call through run_skill().
    from houdini_skills.registry import prepare
    payload = prepare(CATEGORY, action, payload)
    s = get_session()
    s.require(ALL, f"rendering.{action}")
    return _HANDLERS[action](s, payload)


def _out_types(s) -> set:
    """Real ROP node types on this build (verified: hou.ropNodeTypeCategory())."""
    for getter in (lambda: s.hou.ropNodeTypeCategory().nodeTypes(),
                   lambda: s.hou.node("/out").type().category().nodeTypes()):
        try:
            return set(getter().keys())
        except Exception:
            continue
    return set()


def _list_engines(s, p):
    names = _out_types(s)
    found = {eng: [t for t in ENGINE_TYPES[eng] if t in names]
             for eng in ENGINE_TYPES}
    return ok({"version": str(s.version), "engines": {k: bool(v) for k, v in found.items()},
               # `engines: karma_xpu=True` on its own is misleading: it is true because
               # the candidate list falls back to the plain `karma` ROP. Say which type
               # was actually matched, and whether the engine's own name is registered.
               "resolved_types": {k: (v[0] if v else None) for k, v in found.items()},
               "exact_type_registered": {eng: (eng in names) for eng in ENGINE_TYPES},
               "render_node_types_matching": sorted(n for n in names
                                                    if "karma" in n or "mantra" in n or "rop" in n)[:40],
               "note": "an empty engine list means that node type is not registered on "
                       "this build - on 22.0.368 'mantra' is gone and 'karma_xpu' was "
                       "never a node type (both verified via ropNodeTypeCategory). "
                       "karma_xpu therefore resolves to the plain 'karma' ROP, and no "
                       "ROP parm selects CPU vs XPU here: check xpu_note from configure."})


def _configure(s, p):
    engine = p["engine"]
    if engine.startswith("karma") and s.version.series < KARMA_MIN:
        raise SkillFailure(
            "engine_unavailable",
            f"Karma requires Houdini {KARMA_MIN[0]}.{KARMA_MIN[1]}+; running {s.version}",
            hint="use engine='mantra' on 19.5, or upgrade Houdini",
            details={"requested": engine, "karma_min": "20.0"},
        )
    out_net = s.node("/out")
    available = _out_types(s)
    chosen = next((t for t in ENGINE_TYPES[engine] if t in available), "")
    if not chosen:
        raise SkillFailure(
            "engine_unavailable", f"no ROP type for {engine!r} in /out on {s.version}",
            hint="/out render node types: " + ", ".join(
                sorted(n for n in available if "rop" in n or "karma" in n or "mantra" in n)[:20]),
            details={"tried": ENGINE_TYPES[engine]})

    mark = len(s.created)
    rop = s.create_at(out_net.path(), chosen, p["container"])
    applied, unresolved = {}, {}
    knobs = {}
    if p.get("resolution"):
        knobs["resolutionx"] = int(p["resolution"][0])
        knobs["resolutiony"] = int(p["resolution"][1])
    frames = p.get("frames") or []
    if frames:
        knobs["range_enable"] = "normal"   # Karma's trange is a menu, not a toggle
        knobs["start"] = float(frames[0])
        knobs["end"] = float(frames[1])
    if p.get("output_path"):
        knobs["output_path"] = p["output_path"]
    if p.get("samples"):
        knobs["samples"] = int(p["samples"])
    xpu_note = ""
    if engine == "karma_xpu":
        # Verified on 22.0.368: no ROP parm selects XPU. Scan for one rather than
        # trusting that; if none exists, say so instead of setting a fake parm.
        xpu_parms = [c for c in s.parm_names(rop.path())
                     if any(h in c.lower() for h in XPU_PARM_HINTS)]
        xpu_note = ("karma_xpu requested, but this build's Karma ROP exposes no "
                    "renderer/XPU parm" + (f" (found: {xpu_parms})" if xpu_parms else "")
                    + " - XPU is chosen by licence and render settings, not by this ROP")

    for knob, value in knobs.items():
        name = next((c for c in PARM_CANDIDATES[knob] if rop.parm(c) is not None), "")
        if not name:
            unresolved[knob] = PARM_CANDIDATES[knob]
            continue
        try:
            rop.parm(name).set(value)
            applied[knob] = {"parm": name, "value": value}
        except Exception as exc:
            unresolved[knob] = [f"{name}: {exc}"]

    aov_note = ("AOVs are not authored by this skill in v1: the Karma ROP AOV list "
                "is a parm-group UI, not a single parm, and guessing it produces "
                "silently-missing passes. Ask for explicit AOV nodes instead."
                if p.get("aovs") else "")
    try:
        rop.parm("versionviewer").set(0)
    except Exception:
        pass

    return ok({"rop": rop.path(), "node_type": chosen, "engine": engine,
               "created_nodes": s.created[mark:],
               "applied": applied, "unresolved": unresolved,
               "xpu_note": xpu_note,
               "requested_aovs": p.get("aovs") or [], "aov_note": aov_note,
               "rendered": False,
               "next_step": f"render manually or via the MCP bridge: hou.node('{rop.path()}').parm('execute').pressButton()"})


def _inspect(s, p):
    rop = s.node(p["path"])
    values = {}
    for knob, cands in PARM_CANDIDATES.items():
        name = next((c for c in cands if rop.parm(c) is not None), "")
        if name:
            values[knob] = {"parm": name, "value": rop.parm(name).rawValue()}
    return ok({"path": rop.path(), "type": rop.type().name(), "values": values})


_HANDLERS = {"configure": _configure, "inspect": _inspect, "list_engines": _list_engines}
