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
    # Verified on 22.0.368 by reading the composed stage back with pxr: turning this
    # on changed nothing for a SOP Import LOP - the requested file still referenced a
    # separate SOP layer and the geometry composed through it either way. Kept as an
    # opt-in knob only; do not claim it "inlines" anything.
    "flatten_sop_layers": ["flattensoplayers"],
    "default_prim": ["defaultprim"],
}
SOPIMPORT_PARMS = {
    "soppath": ["soppath", "soppath1", "inputpath"],
    # Verified on 22.0.368: a SOP Import LOP writes its own layer to `savepath`,
    # whose default is the template '$HIP/usd/$OS.usd'. That is where geometry
    # actually lands, and the ROP's file just points at it with a subLayers entry.
    "savepath": ["savepath"],
    "enable_savepath": ["enable_savepath"],
}
# Knobs that are nice to have but must never abort an export when absent.
OPTIONAL_KNOBS = {"make_dirs", "flatten_sop_layers", "default_prim", "sop_layer_path"}


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
        net = s.create_at("/obj", "lopnet", stage_path.rsplit("/", 1)[-1])
    return net


def _export(s, p):
    mark = len(s.created)
    if not p.get("output"):
        raise SkillFailure("invalid_input", "export needs an 'output' path")
    src = s.node(p["path"])
    out_path = os.path.abspath(os.path.expandvars(os.path.expanduser(p["output"])))

    rop_type = _rop_type(s)
    rop = s.create_at("/out", rop_type, p["container"])
    applied, unresolved = {}, {}

    target_for_rop = src.path()
    if src.type().category().name() != "LOP":
        stage = _lop_net(s, "/stage")
        imp = s.create_at(stage.path(), _sopimport_type(s, stage),
                          f"{p['container']}_import")
        bound = _set_first(imp, SOPIMPORT_PARMS["soppath"], src.path())
        if not bound:
            raise SkillFailure("parm_not_found",
                               f"no sop-path parm on {imp.path()}",
                               hint="parm names on this LOP: "
                                    + ", ".join(sorted(s.parm_names(imp.path()))[:30]))
        applied["sopimport"] = {"node": imp.path(), "parm": bound}
        target_for_rop = imp.path()
        # Verified on 22.0.368: this LOP authors its own layer file, default
        # '$HIP/usd/$OS.usd', and the ROP's file only references it. Pin that layer
        # next to the requested output so the export travels as one folder instead
        # of scattering layers under $HIP.
        if p.get("keep_layers_together", True):
            companion = (os.path.join(os.path.dirname(out_path),
                                      f"{p['container']}_sop.usd").replace("\\", "/"))
            en = _set_first(imp, SOPIMPORT_PARMS["enable_savepath"], 1)
            sp = _set_first(imp, SOPIMPORT_PARMS["savepath"], companion)
            applied["sop_layer"] = {"enable_parm": en, "path_parm": sp, "value": companion}
            if not en or not sp:
                unresolved["sop_layer_path"] = SOPIMPORT_PARMS["savepath"]

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
        elif knob == "flatten_sop_layers":
            # Only touch it when the caller asks; verified no observable effect for a
            # SOP Import LOP, so the default is to leave Houdini's own value alone.
            value = 1 if p.get("flatten_sop_layers") else None
        elif knob == "default_prim":
            value = p.get("default_prim") or None
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
    contents = _stage_contents(out_path)
    src_points = _point_count(src)
    result = {"written": out_path, **proof, "rop": rop.path(),
              "rop_type": rop_type, "applied": applied, "execute_parm": execute,
              "stage": contents, "source_point_count": src_points}
    # Geometry proof: prims carrying a `points` attribute in the composed stage,
    # compared against the SOP source. Bytes on disk prove nothing on its own.
    mesh_points = sum(g["points"] for g in contents.get("geometry_prims", []))
    result["geometry_points_in_stage"] = mesh_points
    if src_points is not None:
        result["geometry_verified"] = (mesh_points == src_points)
    # A layer whose only content is subLayers pointing at a file that is not there
    # is a broken export; layers that resolve elsewhere are merely not portable.
    if contents.get("pointer_only_layer"):
        holders = [f["resolved"] for f in contents.get("external_layer_files", [])
                   if f.get("exists")]
        result["warning"] = ("the requested file contains no prims of its own: it is a "
                            "layer that references other layer files. Real geometry is in "
                            + (", ".join(holders) if holders else "unknown/missing layers"))
    elif any(not f.get("inside_output_dir") for f in contents.get("external_layer_files", [])):
        outside = [f["resolved"] for f in contents.get("external_layer_files", [])
                   if not f.get("inside_output_dir")]
        result["portability_note"] = (
            "geometry composes correctly but lives in layer file(s) outside the output "
            "folder: " + ", ".join(outside) + ". The SOP Import LOP authors its own layer "
            "at `savepath` (default '$HIP/usd/$OS.usd'). keep_layers_together defaults to "
            "true and pins it; this note means it was turned off, or the LOP already "
            "existed with its own savepath - set enable_savepath/savepath yourself.")
    result["created_nodes"] = s.created[mark:]
    return ok(result)


def _point_count(node):
    try:
        return node.geometry().pointCount()
    except Exception:
        return None


def _stage_contents(path: str) -> dict:
    """Open the written file with pxr and report what actually composes from it.

    22.0.368 pxr facts this relies on (all read off the build, not from memory):
    Usd.Stage exposes only Traverse()/TraverseAll(); Usd.Prim's accessor is
    GetTypeName() (there is no GetType()); Sdf.Path has pathString/str(), not
    .pathname. A subLayers entry composes into the opened stage, so prims authored
    in a referenced layer still count here.
    """
    try:
        from pxr import Usd  # Houdini ships pxr in its own Python
    except Exception as exc:
        return {"pxr_unavailable": str(exc)[:160]}
    try:
        stage = Usd.Stage.Open(path)
    except Exception as exc:
        return {"open_failed": str(exc)[:160]}
    if stage is None:
        return {"opened": False}
    prims = list(stage.Traverse())
    try:
        subs = [str(x) for x in (stage.GetRootLayer().subLayerPaths or [])]
    except Exception:
        subs = []
    default = stage.GetDefaultPrim()
    geometry = []
    for pr in prims:
        try:
            attr = pr.GetAttribute("points")
        except Exception:
            attr = None
        if attr is None:
            continue
        try:
            val = attr.Get()
        except Exception:
            val = None
        geometry.append({"path": str(pr.GetPath()), "type": str(pr.GetTypeName()),
                         "points": (len(val) if val is not None else 0)})
    out = {
        "opened": True,
        "prim_count": len(prims),
        "prim_types": sorted({str(pr.GetTypeName()) for pr in prims})[:8],
        "prim_paths": [str(pr.GetPath()) for pr in prims][:8],
        "geometry_prims": geometry[:8],
        "default_prim": (str(default.GetPath()) if default else None),
        "sublayers": subs[:8],
    }
    out_dir = os.path.dirname(os.path.abspath(path))
    resolved = []
    for sub in subs:
        cand = sub if os.path.isabs(sub) else os.path.normpath(
            os.path.join(os.path.dirname(path), sub))
        resolved.append({"ref": sub, "resolved": cand, "exists": os.path.isfile(cand),
                         "bytes": (os.path.getsize(cand) if os.path.isfile(cand) else None),
                         "inside_output_dir": os.path.dirname(os.path.abspath(cand)) == out_dir})
    out["external_layer_files"] = resolved
    out["self_contained"] = (not subs) or all(f["inside_output_dir"] for f in resolved)
    out["pointer_only_layer"] = (len(prims) == 0 and bool(subs))
    return out


def _verify(s, p):
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(p["output"])))
    proof = _disk_proof(path)
    contents = _stage_contents(path) if proof["exists"] else {"not_opened": "file missing"}
    result = {"path": path, **proof, "stage": contents}
    if contents.get("pointer_only_layer"):
        result["warning"] = ("file is a subLayers pointer with no prims of its own - "
                             "geometry lives in the referenced layer files (see "
                             "stage.sublayer_files), not here")
    return ok(result)


def _sopimport(s, p):
    mark = len(s.created)
    stage = _lop_net(s, p["stage"])
    imp = s.create_at(stage.path(), _sopimport_type(s, stage), "asm_import")
    bound = _set_first(imp, SOPIMPORT_PARMS["soppath"], p["path"])
    if not bound:
        raise SkillFailure("parm_not_found", f"no sop-path parm on {imp.path()}")
    try:
        s.cook(imp.path())
    except SkillFailure as exc:
        raise SkillFailure("sopimport_failed", exc.err.message,
                           hint=exc.err.hint, details=exc.err.details)
    return ok({"lop": imp.path(), "parm": bound, "prim_path": p["prim_path"],
               "created_nodes": s.created[mark:]})


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
