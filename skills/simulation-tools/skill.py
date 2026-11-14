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
# Read off 22.0.368 by creating each SOP solver and calling node.inputLabels().
# Wiring a source into the wrong input index is how a solver cooks clean and does
# nothing, so every builder hands these back to the caller.
# These solvers expose NO dop-related parm on 22.0.368 (probed instance parm names:
# doppath, simroot, dopnet, outputdop all absent). Do not add a dopnet or a
# dop-linking helper back: a live run showed the extra dopnet held only
# `file_output` and its "link" could never resolve.
SOLVER_INPUT_LABELS = {
    "pyrosolver": ["Sources", "Collision Geometry/Volumes"],
    "flipsolver": ["Sources", "Container", "Collisions", "Boundary Flow"],
    "rbdbulletsolver": ["Geometry", "Constraint Geometry", "Proxy Geometry",
                        "Collision Geometry", "Guide Sim"],
}


def _solver_wiring(solver) -> dict:
    """What this build's solver reports for its own inputs and cache knobs.

    Verified on 22.0.368: these SOP-level solvers expose NO dop-related parm
    (`doppath`, `simroot`, `dopnet`, `outputdop` are all absent from the instance).
    They run the simulation themselves and cache through cacheenabled /
    cachemaxsize / checkpoint_* / explicitcache*. A dopnet beside them is an empty
    node nothing drives - a live run proved its only child was `file_output`.
    """
    labels = []
    try:
        labels = list(solver.inputLabels())
    except Exception:
        pass
    names = set()
    try:
        names = {p.name() for p in solver.parms()}
    except Exception:
        pass
    return {
        "input_labels": labels or SOLVER_INPUT_LABELS.get(solver.type().name(), []),
        "labels_from_probe_only": not labels,
        "cache_parms": sorted(n for n in names
                               if n.startswith(("cache", "checkpoint", "explicitcache")))[:12],
        "dop_parms": sorted(n for n in names if "dop" in n.lower()),
        "note": "SOP-level solvers on 22.0.368 are self-contained: no dop-path parm "
                "exists, so no dopnet is created or needed; step the cache to advance",
    }


def _set_and_read(node, candidates, value) -> dict:
    """Set the first parm name that exists, then read it back.

    `kept` is hou's answer afterwards, never the requested value: hou clamps
    out-of-range numbers silently and raises TypeError on a fraction in an int parm.
    """
    parm, bound = _set_any(node, candidates, value)
    kept = None
    if bound:
        try:
            kept = node.parm(parm).eval()
        except Exception:
            kept = None
    return {"parm": parm, "requested": value, "kept": kept, "bound": bound}


def _set_radius(node, value) -> dict:
    """Set a uniform radius/size on a primitive SOP.

    On 22.0.368 these are ParmTuples: sphere.parm("rad") is None while
    sphere.parmTuple("rad").eval() is (1.0, 1.0, 1.0), with children radx/rady/radz.
    Setting "rad" through node.parm() used to return False and leave the node at its
    default, which read as a sizing result rather than a no-op.
    """
    for tup in ("rad", "size", "scale"):
        t = node.parmTuple(tup)
        if t is None:
            continue
        try:
            if len(t) == 1:
                t.set((value,))
            else:
                t.set((value,) * len(t))
            return {"parm": t.name(), "requested": value, "kept": list(t.eval()),
                    "bound": True}
        except Exception:
            continue
    return _set_and_read(node, ["radx", "radius", "r"], value)

FILECACHE_PARMS = {
    # Verified on 22.0.368, filecache::2.0: the path parm is 'sopoutput' and the
    # range parms are 'f1'/'f2' with the menu 'trange' ('off', 'normal').
    # There is no 'outputname', 'outputpath', 't1', 't2' or 'name' parm.
    "cache_path": ["sopoutput", "outputname", "outputpath", "cachedir"],
    "frame_range": ["trange"],
    "start": ["f1", "saverangeStart"],
    "end": ["f2", "saverangeEnd"],
}


CATEGORY = "simulation-tools"


def run(action: str, payload: dict) -> dict:
    # prepare() validates and fills schema defaults, so a direct skill.run()
    # behaves exactly like a CLI call through run_skill().
    from houdini_skills.registry import prepare
    payload = prepare(CATEGORY, action, payload)
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


def _set_frames(s, frames) -> dict:
    start, end = float(frames[0]), float(frames[1])
    if end < start:
        raise SkillFailure("invalid_input", f"frame range {frames} is reversed")
    return s.set_frame_range(start, end)


def _pyro(s, p):
    """Pyro source + self-contained SOP solver. Verified node names on 22.0.368."""
    mark = len(s.created)
    geo = s.ensure_geo(p["container"])
    src_type = _resolve_sop_type(s, geo, "pyro_source")
    solver_type = _resolve_sop_type(s, geo, "pyro_solver")
    sphere, volume, source = s.chain(geo, ["sphere", "volume", src_type],
                                     ["pyro_src", "pyro_vol", "pyro_source"])
    applied = {"pyro_src.rad": _set_radius(sphere, 0.5),
               "pyro_vol.voxelsize": _set_and_read(volume, ["voxelsize", "size"], 0.1)}
    source.parm("snippet").set(
        "// Give the source something to burn: density and temperature.\n"
        "f@density = clamp(1.0 - length(@P) * 0.2, 0.0, 1.0);\n"
        "f@temperature = 1.5;\n"
    ) if _has(source, "snippet") else None
    _set_display(s, source)

    solver = s.create(solver_type, "pyro_solver", geo)
    solver.setInput(0, source)
    return ok({
        "source_chain": [n.path() for n in (sphere, volume, source)],
        "solver": solver.path(), "solver_type": solver_type,
        # what the solver itself says its inputs and cache knobs are
        "solver_wiring": _solver_wiring(solver),
        # Everything this action made. No dopnet: verified absent parm means a
        # dopnet here would be decoration, not part of the simulation.
        "created_nodes": s.created[mark:],
        "frames": _set_frames(s, p["frames"]),
        "parm_report": applied,
        "source_bounds": s.geometry_stats(source.path()).get("framed_bounds"),
        "not_done": ["simulation not advanced", "no cache written",
                     "collision input not wired"],
    })


def _rbd_fall(s, p):
    """Voronoi-fractured column with deliberate potential energy, then RBDPack."""
    mark = len(s.created)
    geo = s.ensure_geo(p["container"])
    frac_type = _resolve_sop_type(s, geo, "rbd_fracture")
    box, sites = s.chain(geo, ["box", "scatter"], ["rbd_column", "rbd_sites"])
    _set(box, "sizex", 1.0); _set(box, "sizey", 3.0); _set(box, "sizez", 1.0)
    # 22.0.368: scatter drives point count through `npts`; `number` does not exist,
    # so the old single-name call silently left the default density in place.
    sites_parm, sites_ok = _set_any(sites, ["npts", "number", "numpts"], p["voronoi_points"])
    site_points = _point_count(sites)

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
    # 22.0.368 xform exposes the `t`/`r`/`s` tuples with children tx..rz; the 19.5-era
    # `rotatex` name is gone, so the lean used to be a silent no-op and the column
    # stood upright with nothing to fall through - which reads like a physics result.
    lean_parm, lean_ok = _set_any(xform, ["rx", "rotatex"], p["lean_degrees"])
    drop_parm, drop_ok = _set_any(xform, ["ty", "translatey"], p["drop_distance"])
    _set_display(s, xform)
    cook = s.cook(xform.path())
    stats = s.geometry_stats(xform.path())
    bounds = stats.get("framed_bounds")

    pack_type = _resolve_sop_type(s, geo, "rbd_pack") if "rbd_pack" in SOP_TYPES else ""
    pack = None
    try:
        pack = s.create(pack_type, "rbd_pack", geo)
        pack.setInput(0, xform)
        _set_display(s, pack)
    except SkillFailure:
        pack = None
    # The RBD Bullet Solver SOP is NOT created here: cooking it starts a sim, which
    # is a decision for the caller, not a side effect of building a fracture chain.
    solver_type = _resolve_sop_type(s, geo, "rbd_solver")
    return ok({
        "fracture_chain": [n.path() for n in (box, sites, frac, xform)]
                          + ([pack.path()] if pack else []),
        "fracture_node": frac.path(), "fracture_type": frac_type,
        "created_nodes": s.created[mark:],
        # the dopnet is an /obj container outside the one you named - it is in
        # created_nodes, so cleanup cannot miss it
        "site_points_wired": wired_sites,
        "pack": pack.path() if pack else None,
        "solver_type_available": solver_type,
        "solver_inputs_if_created": SOLVER_INPUT_LABELS.get("rbdbulletsolver", []),
        "solver_note": "the rbdbulletsolver SOP node was not created: cooking it runs "
                       "a sim. Its inputs on 22.0.368 are Geometry, Constraint "
                       "Geometry, Proxy Geometry, Collision Geometry, Guide Sim.",
        "frames": _set_frames(s, p["frames"]),
        "cook": cook,
        # Piece count and prim count are different quantities; counting one and
        # labelling it the other is how an agent ends up believing a column broke
        # into 11,067 pieces. None means the output carries no @name attribute.
        "source_prims": stats["prim_count"],
        "source_pieces_by_name": stats.get("piece_count_by_name"),
        "piece_note": "pieces = distinct @name values on prims; None means no name "
                      "attribute at this node, so piece count is unknown (a prim count "
                      "is not a piece count)",
        "site_points": site_points,
        "site_count_requested": p["voronoi_points"],
        "knobs": {"site_count_parm": sites_parm, "lean_parm": lean_parm,
                  "drop_parm": drop_parm,
                  "unbound": [k for k, ok in (("site_count", sites_ok), ("lean", lean_ok),
                                              ("drop", drop_ok)) if not ok]},
        "bounds_after_energy": bounds,
        "energy": {"lean_degrees": p["lean_degrees"], "lift": p["drop_distance"],
                   "lean_applied": lean_ok, "drop_applied": drop_ok},
        "why_energy": "fractured pieces that start upright and supported will "
                      "correctly never move; the lean + drop is the potential energy",
        "not_done": ["simulation not advanced", "no cache written",
                     "dop graph contents not authored"],
    })


def _flip(s, p):
    """FLIP source chain + the self-contained FLIP Solver SOP. Nothing is simulated.

    A live 22.0.368 run caught the earlier version of this builder creating a dopnet
    whose only child was `file_output` and then reporting `dop_link: unresolved`.
    That dopnet was never wired to anything: `flipsolver` has no dop-path parm at all
    (doppath/simroot/dopnet/outputdop absent from the instance), so it is gone.
    """
    mark = len(s.created)
    geo = s.ensure_geo(p["container"])
    src_type = _resolve_sop_type(s, geo, "flip_source")
    sphere, scatter = s.chain(geo, ["sphere", "scatter"], ["flip_src", "flip_points"])
    applied = {
        # `rad` is a ParmTuple here: node.parm("rad") is None, so the old direct set
        # silently did nothing and the sphere stayed at radius 1.0.
        "flip_src.rad": _set_radius(sphere, 0.5),
        # same verified 22.0.368 name as rbd_fall: `npts`, not `number`
        "flip_points.npts": _set_and_read(scatter, ["npts", "number", "numpts"],
                                          p["site_count"]),
    }
    source = s.create(src_type, "flip_source", geo)
    source.setInput(0, scatter)
    _set_display(s, source)
    solver_type = _resolve_sop_type(s, geo, "flip_solver")
    solver = s.create(solver_type, "flip_solver", geo)
    # input 0 is the FLIP Solver's "Sources" plug (read from inputLabels())
    solver.setInput(0, source)
    stats = s.geometry_stats(source.path())
    return ok({"source_chain": [sphere.path(), scatter.path(), source.path()],
               "created_nodes": s.created[mark:],
               "solver": solver.path(), "solver_type": solver_type,
               "parm_report": applied,
               "site_points": _point_count(scatter),
               "source_stats": {"points": stats.get("point_count"),
                                "prims": stats.get("prim_count"),
                                "prim_types": stats.get("prim_types")},
               "source_stats_note": "1 prim / 1 point here is correct: the FLIP Source "
                                    "SOP emits a single VDB volume prim (verified prim "
                                    "type 'VDB'). Seed particle counts live at the "
                                    "Scatter SOP; the solver resamples the volume.",
               "solver_wiring": _solver_wiring(solver),
               "frames": _set_frames(s, p["frames"]),
               "not_done": ["simulation not advanced", "no cache written",
                            "Container/Collisions inputs left empty",
                            "FLIP particles are sampled inside the solver, not here"]})


def _cache_sop(s, p):
    """Attach a File Cache SOP below an existing SOP node."""
    mark = len(s.created)
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
               "created_nodes": s.created[mark:],
               "written": False,
               "note": "parms reported as unresolved were not set; check the real "
                       "parm names on this build with read_parms. No cache was "
                       "written: this only configures the File Cache SOP. Writing is "
                       "its Output-tab button, whose parm name this skill has not "
                       "verified - list it with read_parms rather than guessing it"})


def _set_frame_range(s, p):
    return ok(_set_frames(s, p["frames"]))


def _probe_types(s, p):
    """Search node types across every category, not just /obj containers.

    Verified on 22.0.368 with hou.nodeTypeCategories(): the categories are Chop,
    ChopNet, Cop, Cop2, CopNet, Data, Director, Dop, Driver, Lop, Manager, Object,
    Shop, Sop, Top, TopNet, Vop, VopNet. Searching only Object types reported
    'no pyro anywhere' when pyro exists as Dop (pyrosolver...), Sop (pyrosource...),
    Cop, Shop and Vop types. Names also collide across categories (karma is both a
    ROP and a LOP type), so every hit carries its category.
    """
    needle = (p.get("contains") or "").lower()
    cats = s.hou.nodeTypeCategories()
    by_cat, hits, total = {}, [], 0
    for cat_name in sorted(cats):
        try:
            names = sorted(cats[cat_name].nodeTypes())
        except Exception:
            continue
        matched = [n for n in names if needle in n.lower()]
        if not matched:
            continue
        total += len(matched)
        by_cat[cat_name] = {"count": len(matched), "sample": matched[:12]}
        hits.extend({"category": cat_name, "type": n} for n in matched[:12])
    return ok({"version": str(s.version), "needle": needle or None,
               "categories_searched": sorted(cats),
               "count": total, "by_category": by_cat, "types": hits[:60],
               "note": "a bare node-type name is ambiguous: on 22.0.368 SOP and LOP "
                       "share 33 names and 'karma' exists in both ROP and LOP. Create "
                       "the node inside the network whose category owns it."})



def _has(node, parm: str) -> bool:
    return node.parm(parm) is not None


def _set(node, parm: str, value) -> bool:
    p = node.parm(parm)
    if p is None:
        return False
    p.set(value)
    return True


def _set_any(node, candidates: list, value):
    """Set the first parm name that actually exists on this build; (bound, ok).

    A single hardcoded name is how silent breakage happens: on 22.0.368 `scatter`
    has `npts` and no `number`, and `xform` has `rx` (the `r` tuple) and no
    `rotatex`. `_set()` returns False in those cases, which callers used to drop.
    """
    for name in candidates:
        if node.parm(name) is not None:
            node.parm(name).set(value)
            return name, True
    return None, False


def _point_count(node):
    try:
        return len(node.geometry().points())
    except Exception:
        return None


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
