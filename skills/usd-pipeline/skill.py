"""usd-pipeline - SOP -> USD round trip with on-disk proof.

Verified against Houdini 22.0.368 (see docs/verified-api.md): the USD Output ROP
binds to a LOP through `loppath` and writes to `lopoutput` (default
'$HIP/usd/$OS.usd'), executed by pressing `execute`. A SOP source therefore
needs a Solaris SOP Import LOP in between - that is why this skill creates one
instead of feeding a SOP path straight at the ROP.

Every export returns the real file size from disk. If the file is not there,
the skill says so; it never reports success from "the ROP did not throw".
"""
from __future__ import annotations

import os

from houdini_skills.errors import SkillFailure, ok
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))

USD_ROP_PARMS = {
    "loppath": ["loppath"],
    "output": ["lopoutput", "outputname", "outputpath"],
    "range_enable": ["trange"],
    "start": ["f1", "saverangeStart", "startframe"],
    "end": ["f2", "saverangeEnd", "endframe"],
    "make_dirs": ["mkpath"],
}
SOPIMPORT_PARMS = {"soppath": ["soppath", "soppath1", "inputpath"]}
# Knobs that are nice to have but must never abort an export when absent.
OPTIONAL_KNOBS = {"make_dirs"}


CATEGORY = "usd-pipeline"


def run(action: str, payload: dict) -> dict:
    # prepare() validates and fills schema defaults, so a direct skill.run()
    # behaves exactly like a CLI call through run_skill().
    from houdini_skills.registry import prepare
    payload = prepare(CATEGORY, action, payload)
    s = get_session()
    s.require(SUPPORTED, f"usd-pipeline.{action}")
    return _HANDLERS[action](s, payload)


def _rop_type(s) -> str:
    names = _rop_names(s)
    for cand in ("usd", "usd_rop", "usdoutput"):
        if cand in names:
            return cand
    raise SkillFailure("node_type_unavailable", "no USD ROP type on this build",
                       hint="ROP types containing 'usd': "
                            + ", ".join(n for n in names if "usd" in n.lower())[:300],
                       details={"category_sample": sorted(names)[:30]})


def _lop_net(s, stage_path: str):
    net = s.hou.node(stage_path)
    if net is None:
        obj = s.hou.node("/obj")
        net = obj.createNode("lopnet", stage_path.rsplit("/", 1)[-1])
    return net


def _export(s, p):
    if not p.get("output"):
        raise SkillFailure("invalid_input", "export needs an 'output' path")
    src = s.node(p["path"])
    out_path = os.path.abspath(os.path.expandvars(os.path.expanduser(p["output"])))

    rop_type = _rop_type(s)
    rop = s.hou.node("/out").createNode(rop_type, p["container"])
    applied, unresolved = {}, {}

    target_for_rop = src.path()
    if src.type().category().name() != "LOP":
        stage = _lop_net(s, "/stage")
        imp = stage.createNode(_sopimport_type(s, stage), f"{p['container']}_import")
        bound = _set_first(imp, SOPIMPORT_PARMS["soppath"], src.path())
        if not bound:
            raise SkillFailure("parm_not_found",
                               f"no sop-path parm on {imp.path()}",
                               hint="parm names on this LOP: "
                                    + ", ".join(sorted(s.parm_names(imp.path()))[:30]))
        applied["sopimport"] = {"node": imp.path(), "parm": bound}
        target_for_rop = imp.path()

    frames = p.get("frames") or []
    for knob, cands in USD_ROP_PARMS.items():
        if knob == "range_enable":
            if not frames:
                continue
            value = "normal"
        elif knob == "start":
            value = float(frames[0]) if frames else None
        elif knob == "end":
            value = float(frames[1]) if frames else None
        elif knob == "output":
            value = out_path
        elif knob == "loppath":
            value = target_for_rop
        elif knob == "make_dirs":
            value = 1
        else:
            value = None
        if value is None:
            continue
        bound = _set_first(rop, cands, value)
        if bound:
            applied[knob] = {"parm": bound, "value": value}
        else:
            unresolved[knob] = cands

    if unresolved:
        blocking = {k: v for k, v in unresolved.items() if k not in OPTIONAL_KNOBS}
        if blocking:
            raise SkillFailure(
                "parm_not_found",
                f"USD ROP {rop.path()} is missing parm(s) {sorted(blocking)} on {s.version}",
                hint="closest real names: " + "; ".join(
                    f"{k}->{_closest(s, rop, v)}" for k, v in blocking.items()),
                details={"unresolved": unresolved},
            )

    execute = next((name for name in ("execute", "render", "driverparm")
                    if rop.parm(name) is not None), "")
    if not execute:
        raise SkillFailure("no_execute_parm",
                           f"cannot find a way to run {rop.path()}",
                           hint="parms: " + ", ".join(sorted(s.parm_names(rop.path()))[:40]))
    rop.parm(execute).pressButton()

    proof = _disk_proof(out_path)
    if not proof["exists"]:
        raise SkillFailure("export_produced_no_file",
                           f"ROP ran but {out_path} is not on disk",
                           hint="check $HIP and the ROP's own error output; "
                                "a LOP path pointing at nothing writes nothing",
                           details={"rop": rop.path(), "applied": applied})
    return ok({"written": out_path, **proof, "rop": rop.path(),
               "rop_type": rop_type, "applied": applied, "execute_parm": execute})


def _verify(s, p):
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(p["output"])))
    proof = _disk_proof(path)
    stage = None
    try:
        from pxr import Usd  # Houdini ships pxr in its own Python on USD builds
        if proof["exists"]:
            usd = Usd.Stage.Open(path)
            stage = {"opened": usd is not None,
                     "prim_count": len(list(usd.TraverseAllPims())) if usd and hasattr(usd, "TraverseAllPims") else None,
                     "default_prim": (usd.GetDefaultPrim().GetPath().pathname
                                      if usd and usd.GetDefaultPrim() else None)}
    except Exception as exc:
        stage = {"pxr_unavailable": str(exc)[:120]}
    return ok({"path": path, **proof, "stage": stage})


def _sopimport(s, p):
    stage = _lop_net(s, p["stage"])
    imp = stage.createNode(_sopimport_type(s, stage), "asm_import")
    bound = _set_first(imp, SOPIMPORT_PARMS["soppath"], p["path"])
    if not bound:
        raise SkillFailure("parm_not_found", f"no sop-path parm on {imp.path()}")
    try:
        s.cook(imp.path())
    except SkillFailure as exc:
        raise SkillFailure("sopimport_failed", exc.err.message,
                           hint=exc.err.hint, details=exc.err.details)
    return ok({"lop": imp.path(), "parm": bound, "prim_path": p["prim_path"]})


def _sopimport_type(s, stage) -> str:
    """SOP Import LOP type, checked against the LOP network's real child types."""
    names = set(s._type_names(stage))
    for cand in ("sopimport", "sopimport::1.0", "sopimport::2.0"):
        if cand in names:
            return cand
    raise SkillFailure("node_type_unavailable", "no SOP Import LOP on this build",
                       hint="LOP child types containing 'sop': " + ", ".join(
                           n for n in sorted(names) if "sop" in n.lower())[:300],
                       details={"stage": stage.path(), "sample": sorted(names)[:40]})


def _set_first(node, candidates, value) -> str:
    for name in candidates:
        parm = node.parm(name)
        if parm is not None:
            try:
                parm.set(value)
                return name
            except Exception:
                continue
    return ""


def _closest(s, node, candidates) -> str:
    from houdini_skills.errors import suggest
    return ", ".join(suggest(candidates[0], s.parm_names(node.path()), 4))


def _rop_names(s):
    try:
        return set(s.hou.ropNodeTypeCategory().nodeTypes())
    except Exception:
        try:
            return set(s.hou.node("/out").type().category().nodeTypes())
        except Exception:
            return set()


def _disk_proof(path: str) -> dict:
    """On-disk proof, including sibling layers.

    Verified behaviour on 22.0.368: the USD ROP wrote the requested file AND a
    sub-layer next to the SOP Import node's default output ($HIP/usd/...). An
    exporter that reports only the one path it was given is lying about the
    stage it produced.
    """
    exists = os.path.isfile(path)
    out = {"exists": exists,
           "bytes": os.path.getsize(path) if exists else 0,
           "absolute_path": path}
    try:
        folder = os.path.dirname(path) or "."
        out["sibling_usd_files"] = sorted(
            f for f in os.listdir(folder)
            if f.lower().endswith((".usd", ".usda", ".usdc"))
            and os.path.join(folder, f) != path)[:20]
    except Exception:
        out["sibling_usd_files"] = []
    return out


_HANDLERS = {"export": _export, "verify": _verify, "sopimport": _sopimport}
