"""simulation-tools - DOP object setup with discovered node names.

Honest scope warning for v1: this skill builds the *containers and sources*
(a Pyro/RBD/FLIP object with an animated, energy-carrying source plus a File
Cache) and reports what it created. It does not pretend to have advanced a
simulation. A sim that never moves is usually bad sourcing, not a broken tool:
an upright fractured column sitting on the ground is correct physics. So
rbd_fall deliberately leans and lifts the pieces - geometry needs a reason to
move.

Node type names are resolved against the running build, because SideFX has
renamed object-level sim types across versions.
"""
from __future__ import annotations

import os

from houdini_skills.errors import SkillFailure, ok
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))

# VERIFIED on 22.0.368: hou.objNodeTypeCategory() has NO 'pyro'/'rbd'/'flip'.
# The only simulation object is 'dopnet'; sources and solvers are SOPs.
DOPNET_TYPE = ["dopnet"]
SOP_TYPES = {
    "pyro_source": ["pyrosource"],
    "pyro_solver": ["pyrosolver"],
    "rbd_fracture": ["voronoifracture", "rbdmaterialfracture"],
    "rbd_pack": ["rbdpack"],
    "rbd_solver": ["rbdbulletsolver"],
    "flip_source": ["flipsource"],
    "flip_solver": ["flipsolver"],
    "vellum_solver": ["vellumsolver"],
    "file_cache": ["filecache"],
}
FILECACHE_PARMS = {
    # Verified on 22.0.368, filecache::2.0: the path parm is 'sopoutput' and the
    # range parms are 'f1'/'f2' with the menu 'trange' ('off', 'normal').
    # There is no 'outputname', 'outputpath', 't1', 't2' or 'name' parm.
    "cache_path": ["sopoutput", "outputname", "outputpath", "cachedir"],
    "frame_range": ["trange"],
    "start": ["f1", "saverangeStart"],
    "end": ["f2", "saverangeEnd"],
}


def run(action: str, payload: dict) -> dict:
    s = get_session()
    s.require(SUPPORTED, f"simulation-tools.{action}")
    return _HANDLERS[action](s, payload)


def _obj_types(s) -> set:
    """Object-level node types on this build (verified: hou.objNodeTypeCategory())."""
    for getter in (lambda: s.hou.objNodeTypeCategory().nodeTypes(),
                   lambda: s.hou.node("/obj").type().category().nodeTypes()):
        try:
            return set(getter().keys())
        except Exception:
            continue
    return set()


def _resolve_sop_type(s, geo_path: str, kind: str) -> str:
    """Which candidate SOP node name does THIS build actually have?"""
    names = s._type_names(s.node(geo_path))
    for cand in SOP_TYPES[kind]:
        if cand in names:
            return cand
    near = sorted(n for n in names if kind.split("_")[0] in n.lower())
    raise SkillFailure(
        "node_type_unavailable",
        f"no SOP node for {kind!r} on {s.version}",
        hint=f"tried {SOP_TYPES[kind]}; names containing "
             f"'{kind.split('_')[0]}': {(near[:12] or ['none'])}",
        details={"candidates": SOP_TYPES[kind], "found": near[:12]},
    )


def _dopnet(s, container: str) -> str:
    """Create the one simulation object Houdini actually has: a DOP Network."""
    names = _obj_types(s)
    kind = next((c for c in DOPNET_TYPE if c in names), "")
    if not kind:
        raise SkillFailure("node_type_unavailable", f"no 'dopnet' object type on {s.version}",
                           hint="OBJ types containing 'dop': "
                                + ", ".join(n for n in sorted(names) if "dop" in n.lower()))
    return s.create(kind, f"{container}_dopnet", "/obj").path()


def _set_frames(s, frames) -> dict:
    start, end = float(frames[0]), float(frames[1])
    if end < start:
        raise SkillFailure("invalid_input", f"frame range {frames} is reversed")
    return s.set_frame_range(start, end)


def _pyro(s, p):
    """Pyro source + SOP solver + dopnet. Verified node names on 22.0.368."""
    geo = s.ensure_geo(p["container"])
    src_type = _resolve_sop_type(s, geo, "pyro_source")
    solver_type = _resolve_sop_type(s, geo, "pyro_solver")
    sphere, volume, source = s.chain(geo, ["sphere", "volume", src_type],
                                     ["pyro_src", "pyro_vol", "pyro_source"])
    set_report = {}
    for node, parm, value in ((sphere, "rad", 0.5), (volume, "voxelsize", 0.1)):
        set_report[f"{node.name()}.{parm}"] = _set(node, parm, value)
    source.parm("snippet").set(
        "// Give the source something to burn: density and temperature.\n"
        "f@density = clamp(1.0 - length(@P) * 0.2, 0.0, 1.0);\n"
        "f@temperature = 1.5;\n"
    ) if _has(source, "snippet") else None
    _set_display(s, source)

    solver = s.create(solver_type, "pyro_solver", geo)
    solver.setInput(0, source)
    dop = _dopnet(s, p["container"])
    link = _link_solver(solver, dop)
    bounds = s.geometry_stats(source.path())
    return ok({
        "source_chain": [n.path() for n in (sphere, volume, source)],
        "solver": solver.path(), "solver_type": solver_type,
        "dopnet": dop, "dop_link": link,
        "frames": _set_frames(s, p["frames"]),
        "parm_report": set_report,
        "source_bounds": bounds.get("framed_bounds"),
        "not_done": ["simulation not advanced", "no cache written",
                     "dop graph contents not authored"],
    })


def _rbd_fall(s, p):
    """Voronoi-fractured column with deliberate potential energy, then RBDPack."""
    geo = s.ensure_geo(p["container"])
    frac_type = _resolve_sop_type(s, geo, "rbd_fracture")
    box, sites = s.chain(geo, ["box", "scatter"], ["rbd_column", "rbd_sites"])
    _set(box, "sizex", 1.0); _set(box, "sizey", 3.0); _set(box, "sizez", 1.0)
    _set(sites, "number", p["voronoi_points"])

    frac = s.create(frac_type, "rbd_shatter", geo)
    frac.setInput(0, box)
    wired_sites = False
    if frac_type == "voronoifracture":
        # Voronoi Fracture needs site points on input 1; with only input 0 it
        # fails to cook on 22.0.368 (verified), so wire them explicitly.
        try:
            frac.setInput(1, sites)
            wired_sites = True
        except Exception:
            pass
    xform = s.create("xform", "rbd_lean", geo)
    xform.setInput(0, frac)
    _set(xform, "rotatex", p["lean_degrees"])
    _set(xform, "ty", p["drop_distance"])
    _set_display(s, xform)
    cook = s.cook(xform.path())
    stats = s.geometry_stats(xform.path())

    pack_type = _resolve_sop_type(s, geo, "rbd_pack") if "rbd_pack" in SOP_TYPES else ""
    pack = None
    try:
        pack = s.create(pack_type, "rbd_pack", geo)
        pack.setInput(0, xform)
        _set_display(s, pack)
    except SkillFailure:
        pack = None
    dop = _dopnet(s, p["container"])
    solver_type = _resolve_sop_type(s, geo, "rbd_solver")
    return ok({
        "fracture_chain": [n.path() for n in (box, sites, frac, xform)]
                          + ([pack.path()] if pack else []),
        "fracture_node": frac.path(), "fracture_type": frac_type,
        "site_points_wired": wired_sites,
        "pack": pack.path() if pack else None,
        "dopnet": dop, "solver_type_available": solver_type,
        "frames": _set_frames(s, p["frames"]),
        "cook": cook,
        "source_pieces": stats.get("piece_count_by_name") or stats["prim_count"],
        "energy": {"lean_degrees": p["lean_degrees"], "lift": p["drop_distance"]},
        "why_energy": "fractured pieces that start upright and supported will "
                      "correctly never move; the lean + drop is the potential energy",
        "not_done": ["simulation not advanced", "no cache written",
                     "dop graph contents not authored"],
    })


def _flip(s, p):
    geo = s.ensure_geo(p["container"])
    src_type = _resolve_sop_type(s, geo, "flip_source")
    sphere, scatter = s.chain(geo, ["sphere", "scatter"], ["flip_src", "flip_points"])
    _set(scatter, "number", 2000)
    source = s.create(src_type, "flip_source", geo)
    source.setInput(0, scatter)
    _set_display(s, source)
    solver_type = _resolve_sop_type(s, geo, "flip_solver")
    solver = s.create(solver_type, "flip_solver", geo)
    solver.setInput(0, source)
    dop = _dopnet(s, p["container"])
    link = _link_solver(solver, dop)
    return ok({"source_chain": [sphere.path(), scatter.path(), source.path()],
               "solver": solver.path(), "solver_type": solver_type,
               "dopnet": dop, "dop_link": link,
               "frames": _set_frames(s, p["frames"]),
               "not_done": ["simulation not advanced"]})


def _link_solver(solver, dop_path: str) -> dict:
    """Try to point a SOP solver at the dopnet; report honestly what bound."""
    for name in ("doppath", "simroot", "dopnet", "outputdop"):
        parm = solver.parm(name)
        if parm is not None:
            try:
                parm.set(dop_path)
                return {"parm": name, "value": dop_path}
            except Exception:
                continue
    return {"unresolved": "no dop-path parm found on the solver; "
                          "dopnet created but not linked"}


def _cache_sop(s, p):
    """Attach a File Cache SOP below an existing SOP node."""
    target = s.node(p["path"])
    parent = target.parent()
    if parent is None:
        raise SkillFailure("invalid_input", f"{p['path']} has no parent network")
    if parent.path() == "/obj":
        raise SkillFailure("invalid_input",
                           f"{p['path']} is an object; cache a SOP inside a geometry container")
    fc = s.create("filecache", f"{target.name()}_cache", parent.path())
    fc.setInput(0, target)
    applied, unresolved = {}, {}
    for knob, cands in FILECACHE_PARMS.items():
        name = next((c for c in cands if fc.parm(c) is not None), "")
        if not name:
            unresolved[knob] = cands
            continue
        if knob == "cache_path":
            fc.parm(name).set(os.path.abspath(os.path.expandvars(p["cache_path"])))
            applied[knob] = {"parm": name, "value": p["cache_path"]}
        elif knob == "frame_range" and p.get("frames"):
            # Verified: filecache trange is a menu with items ('off', 'normal').
            fc.parm(name).set("normal")
            applied[knob] = {"parm": name, "value": "normal"}
        elif knob in ("start", "end") and p.get("frames"):
            fc.parm(name).set(float(p["frames"][0] if knob == "start" else p["frames"][1]))
            applied[knob] = {"parm": name}
    _set_display(s, fc)
    return ok({"file_cache": fc.path(), "applied": applied, "unresolved": unresolved,
               "note": "parms reported as unresolved were not set; check the real "
                       "parm names on this build with read_parms"})


def _set_frame_range(s, p):
    return ok(_set_frames(s, p["frames"]))


def _probe_types(s, p):
    names = _obj_types(s)
    hit = sorted(n for n in names if (p["contains"] or "").lower() in n.lower())
    return ok({"version": str(s.version), "count": len(hit), "types": hit[:60]})


def _has(node, parm: str) -> bool:
    return node.parm(parm) is not None


def _set(node, parm: str, value) -> bool:
    p = node.parm(parm)
    if p is None:
        return False
    p.set(value)
    return True


def _set_display(s, node) -> None:
    try:
        node.setDisplayFlag(True)
    except AttributeError:
        parm = node.parm("displayflag")
        if parm is not None:
            parm.on()


_HANDLERS = {
    "pyro": _pyro, "rbd_fall": _rbd_fall, "flip": _flip,
    "cache_sop": _cache_sop, "set_frame_range": _set_frame_range,
    "probe_types": _probe_types,
}
