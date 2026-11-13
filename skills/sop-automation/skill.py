"""sop-automation - SOP network authoring through the shared hou wrapper.

Design note: parm names are resolved by candidate list, never hardcoded blind.
H22's Grid SOP uses sizex/sizez (not sx/sy/sz the way many agents guess), and
PolyBevel's width parm has moved across versions - so grid_bevel() binds to
whatever the running build actually exposes and reports which knob it landed on.
"""
from __future__ import annotations

from houdini_skills.errors import SkillFailure, ok
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))

# Friendly knob -> candidate parm names, most-likely first.
GRID_PARM_CANDIDATES = {
    "sizex": ["sizex", "sx", "size1"],
    "sizez": ["sizez", "sz", "size2"],
    "divisionsx": ["divisionsx", "numx", "divsx", "cols"],
    "divisionsz": ["divisionsz", "numz", "divsz", "rows"],
}
BEVEL_PARM_CANDIDATES = {
    "width": ["bevelamount", "width", "size", "offset"],
}


def run(action: str, payload: dict) -> dict:
    s = get_session()
    s.require(SUPPORTED, f"sop-automation.{action}")
    handler = _HANDLERS[action]
    return handler(s, payload)


def _create_node(s, p):
    n = s.create(p["node_type"], p["name"], p["parent"])
    return ok({"node": s.ref(n)})


def _chain(s, p):
    made = s.chain(p["parent"], p["types"], p.get("names"))
    return ok({"nodes": [s.ref(n) for n in made],
               "wired": [f"{a.name()} -> {b.name()}" for a, b in zip(made, made[1:])]})


def _grid_bevel(s, p):
    geo = s.ensure_geo(p["container"])
    grid, bevel = s.chain(geo, ["grid", "polybevel"], ["grid1", "bevel1"])
    applied, unresolved = {}, {}

    sx, sz = p["size"][0], p["size"][-1]
    for knob, value in (("sizex", sx), ("sizez", sz),
                        ("divisionsx", p["divisions"]), ("divisionsz", p["divisions"])):
        name = _first_existing(s, grid.path(), GRID_PARM_CANDIDATES[knob])
        if name:
            grid.parm(name).set(value)
            applied[knob] = {"parm": name, "value": value}
        else:
            unresolved[knob] = GRID_PARM_CANDIDATES[knob]

    name = _first_existing(s, bevel.path(), BEVEL_PARM_CANDIDATES["width"])
    if name:
        bevel.parm(name).set(p["bevel_width"])
        applied["bevel_width"] = {"parm": name, "value": p["bevel_width"]}
    else:
        unresolved["bevel_width"] = BEVEL_PARM_CANDIDATES["width"]

    _set_display(s, bevel)
    cook = s.cook(bevel.path())
    stats = s.geometry_stats(bevel.path())
    return ok({"grid": grid.path(), "bevel": bevel.path(), "applied_parms": applied,
               "unresolved_parms": unresolved, "cook": cook, "result": stats})


def _set_parms(s, p):
    applied = s.set_parms(p["path"], p["parms"])
    return ok({"path": p["path"], "applied": applied})


def _read_parms(s, p):
    return ok({"path": p["path"], "parms": s.read_parms(p["path"], p.get("names"))})


def _find(s, p):
    return ok({"pattern": p["pattern"], "matches": s.find(p["pattern"], p["limit"])})


def _geo_stats(s, p):
    return ok(s.geometry_stats(p["path"]))


def _delete(s, p):
    return ok(s.delete(p["path"], p["confirm"]))


def _list_types(s, p):
    types = s.list_types(p["parent"], p["contains"])
    return ok({"parent": p["parent"], "count": len(types), "types": types[:400]})


def _first_existing(s, path: str, candidates: list[str]) -> str:
    names = s.parm_names(path)
    for c in candidates:
        if c in names:
            return c
    return ""


def _set_display(s, node) -> None:
    """Put the display flag on the chain output so Object Merge can pull it."""
    try:
        node.setDisplayFlag(True)
        return
    except AttributeError:
        pass
    parm = node.parm("displayflag")
    if parm is not None:
        parm.on()


_HANDLERS = {
    "create_node": _create_node,
    "chain": _chain,
    "grid_bevel": _grid_bevel,
    "set_parms": _set_parms,
    "read_parms": _read_parms,
    "find": _find,
    "geo_stats": _geo_stats,
    "delete_node": _delete,
    "list_types": _list_types,
}
