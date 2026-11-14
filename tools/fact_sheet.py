#!/usr/bin/env python3
"""Print the Houdini facts the docs claim, as JSON, from whatever interpreter runs it.

Why: the repo's parm/type tables were proven under `hython`. A live GUI session is a
different environment (hou.ui present, a real scene, hwebserver up), so "verified on
22.0.368" should mean *both* ways in. Run it two ways and diff:

    <Houdini>/bin/hython tools/fact_sheet.py --instances > facts_hython.json
    # in the live GUI through fxhoudinimcp execute_python:
    #   import importlib.util, json
    #   spec = importlib.util.spec_from_file_location("fact_sheet", "<repo>/tools/fact_sheet.py")
    #   m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    #   return_expression = "json.dumps(m.collect(instances=False))"
    python tools/fact_sheet.py --diff facts_hython.json facts_live.json

Two modes, and the difference matters:
  * collect(instances=False)  - creates nothing. Safe inside someone else's scene.
    Parm names come from the node TYPE only, which UNDER-REPORTS (see below).
  * collect(instances=True)   - instantiates nodes to read the real parm list. Use it
    under hython. In a live session it marks the .hip dirty; it prints the dirty flag
    before and after, destroys everything it created, and never saves.

VERIFIED ON 22.0.368, and the reason two modes exist: NodeType.parmTemplates() and
parmTemplateGroup() do NOT contain parms the framework adds at instance time. Grid's
`sizex`/`sizey`, File Cache's `sopoutput`/`f1`/`f2`, Karma's `f1`/`resolutionx` and USD
ROP's `mkpath` are absent from the type templates and present on a real node. So
type-level introspection must never be used as a parm-name validator: it rejects parms
that exist.

Keys here mirror docs/verified-api.md one-to-one. If a SideFX patch renames something,
the JSON changes and the diff says so.
"""
from __future__ import annotations

import json
import sys

MODULE_FNS = [
    "applicationVersion", "applicationVersionString", "isApprentice",
    "frame", "setFrame", "node", "objNodeTypeCategory", "sopNodeTypeCategory",
    "ropNodeTypeCategory", "lopNodeTypeCategory", "playbar", "undos",
]
# Claimed absent in the docs. If one of these flips to True, the doc is wrong now.
MODULE_FNS_ABSENT = [
    "version", "applicationVersionTuple", "applicationBuild", "licenseManager",
    "hpys", "glob", "setFrameRange", "fetchGlobalSetup", "playbackBarMinFrame",
]
NODE_FNS = ["allSubChildren", "children", "parm", "parms", "parmTuples",
            "allParms", "createNode", "destroy", "path"]
NODE_FNS_ABSENT = ["allChildren", "parmNames"]
NODETYPE_FNS = ["parmTemplates", "parmTemplateGroup"]

PARMS = {
    "grid": ["sizex", "sizey", "rows", "cols"],
    "box": ["sizex", "sizey", "sizez", "divsx"],
    "polybevel": ["offset"],
    "scatter": ["npts", "seed"],
    "copytopoints": ["pack", "pivot", "transform"],
    "filecache": ["sopoutput", "trange", "f1", "f2"],
    "karma": ["picture", "resolutionx", "resolutiony", "trange", "f1", "f2",
              "samplesperpixel"],
    "usd": ["loppath", "lopoutput", "execute", "trange", "f1", "f2"],
}
# Names that must NOT exist; a SideFX rename shows up as one of them appearing.
PARMS_ABSENT = {
    "grid": ["sizez", "divisionsx", "divisionsz"],
    "box": ["divisionsx"],
    "polybevel": ["bevelamount", "width", "size"],
    "scatter": ["perpoint", "mindist", "poisson"],
    "copytopoints": ["pastepoints", "pasteprims"],
    "filecache": ["outputname", "outputpath", "t1", "t2"],
}
TYPES_PRESENT = {
    "obj": ["dopnet", "geo"],
    "sop": ["grid", "box", "polybevel", "attribwrangle", "filecache", "dopio",
            "pyrosource", "pyrosolver", "rbdbulletsolver", "rbdmaterialfracture",
            "voronoifracture", "rbdpack", "flipsource", "flipsolver",
            "vellumsolver", "scatter", "copytopoints", "pack", "switch"],
    # Verified names, not guesses: the ROP fetch driver is `fetch` (there is no
    # `fetchnetwork` and no `driver` type), and the LOP types are `componentgeometry`
    # / `componentmaterial` / `componentoutput` - there is no `componentlist` type and
    # no `subgraph` type (subgraph editing is a mode, not a node type).
    "rop": ["karma", "usd", "fetch", "framecontainer"],
    "lop": ["sopimport", "sceneimport", "karma", "componentgeometry"],
}
TYPES_ABSENT = {
    "obj": ["pyro", "rbd", "flip", "pyroobject", "rbdobject", "flipobject"],
    "sop": ["wrangle", "pyro", "dopnetwork", "vellumsource", "findattribvalue"],
    "rop": ["mantra", "karma_xpu", "fetchnetwork", "driver"],
    "lop": ["subgraph", "componentlist", "mantra"],
}
# Type names collide across categories on 22.0.368: `karma` is both a ROP and a LOP,
# `copytopoints`/`attribwrangle`/`dopnet`/`null` exist in SOP and LOP, and 30+ others.
# Category-qualified lookups are mandatory. These are asserted True, not counted, so a
# build that finally disambiguates names will not break the run.
COLLISIONS = ("rop", "lop", "karma"), ("sop", "lop", "copytopoints"), \
             ("sop", "lop", "attribwrangle"), ("sop", "obj", "dopnet")
MENUS = (("rop", "karma", "trange"), ("rop", "usd", "trange"),
         ("sop", "filecache", "trange"), ("sop", "attribwrangle", "class"))
# Which category each probed node type actually lives in. Without this the loop
# asks the LOP category for `copytopoints` (a LOP node of the same name exists!)
# and reports a parm table that belongs to a different tool entirely.
NODE_CAT = {"grid": "sop", "box": "sop", "polybevel": "sop", "scatter": "sop",
            "copytopoints": "sop", "filecache": "sop", "attribwrangle": "sop",
            "karma": "rop", "usd": "rop"}
INSTANCE_NODES = NODE_CAT


def _categories():
    import hou
    return {"obj": hou.objNodeTypeCategory(), "sop": hou.sopNodeTypeCategory(),
            "rop": hou.ropNodeTypeCategory(), "lop": hou.lopNodeTypeCategory()}


def _type_names(node_type) -> set:
    """Type-declared parm names. Known to under-report; see the module docstring."""
    return {t.name() for t in node_type.parmTemplates()}


def collect(instances: bool = False) -> dict:
    import hou

    out = {"environment": {}, "facts": {}, "counts": {}}
    out["environment"] = {
        "applicationVersionString": hou.applicationVersionString(),
        "applicationVersion": list(hou.applicationVersion()),
        "isApprentice": hou.isApprentice(),
        "has_ui": hasattr(hou, "ui"),
        "hip_file": hou.hipFile.path(),
        # hou.hipFile has hasUnsavedChanges(); there is NO .dirty() or .modified().
        "hip_unsaved_before": hou.hipFile.hasUnsavedChanges(),
        "hipfile_hasUnsavedChanges": hasattr(hou.hipFile, "hasUnsavedChanges"),
        "hipfile_dirty_absent": not hasattr(hou.hipFile, "dirty"),
        "hipfile_modified_absent": not hasattr(hou.hipFile, "modified"),
        "playbar_type": type(hou.playbar).__name__,
        "playbar_has_frameRange": hasattr(hou.playbar, "frameRange"),
        "playbar_has_setFrameRange": hasattr(hou.playbar, "setFrameRange"),
    }

    for name in MODULE_FNS + MODULE_FNS_ABSENT:
        out["facts"]["hou." + name] = hasattr(hou, name)
    world = hou.node("/")
    for name in NODE_FNS + NODE_FNS_ABSENT:
        out["facts"]["Node." + name] = hasattr(world, name)

    cats = _categories()
    for name in NODETYPE_FNS:
        out["facts"]["NodeType." + name] = hasattr(cats["sop"].nodeTypes()["grid"],
                                                   name)
    for cat, category in cats.items():
        names = set(category.nodeTypes())
        out["counts"][cat + "_types"] = len(names)
        for t in TYPES_PRESENT.get(cat, []):
            out["facts"]["type[%s].%s" % (cat, t)] = t in names
        for t in TYPES_ABSENT.get(cat, []):
            out["facts"]["type[%s].%s" % (cat, t)] = t in names

    # Category collisions. On 22.0.368 `hou.ropNodeTypeCategory()` and
    # `hou.lopNodeTypeCategory()` both define `karma`; SOP and LOP share 33 names.
    # A tool that looks types up by bare name can end up configuring the wrong one.
    for ca, cb, name in COLLISIONS:
        out["facts"]["collide[%s|%s].%s" % (ca, cb, name)] = (
            name in set(cats[ca].nodeTypes()) and name in set(cats[cb].nodeTypes()))
    for ca, cb in (("sop", "lop"), ("rop", "lop"), ("obj", "lop"), ("sop", "obj")):
        out["counts"]["collide_%s_%s" % (ca, cb)] = len(
            set(cats[ca].nodeTypes()) & set(cats[cb].nodeTypes()))

    # Type-level visibility. False here does NOT mean the parm is missing from a real
    # node - it means the type templates do not declare it. `parms[...]` (instance
    # mode) is the honest list.
    for cat, category in cats.items():
        types = category.nodeTypes()
        for node_type, wanted in PARMS.items():
            if NODE_CAT.get(node_type) != cat or node_type not in types:
                continue
            names = _type_names(types[node_type])
            out["facts"]["typeparms[%s].%s" % (cat, node_type)] = {
                w: (w in names) for w in wanted}

    for cat, node_type, parm in MENUS:
        types = cats[cat].nodeTypes()
        if node_type not in types:
            continue
        tpl = types[node_type].parmTemplateGroup().find(parm)
        # VERIFIED on 22.0.368: MenuParmTemplate.menuItems() is a FLAT tuple of
        # labels, not (label, identifier) pairs, and the template has no items()
        # method. Identifiers only come off a live parm: hou.MenuParm.menuItems()
        # plus hou.MenuParm.menuItemIdentifier(index). Type level can only prove
        # labels, so that is all this mode reports.
        if tpl is None:
            continue
        # menuItems() only exists on a menu template; on any other template it
        # raises (and on a live non-menu hou.Parm it raises hou.OperationFailed).
        try:
            out["facts"]["menu_labels[%s].%s.%s" % (cat, node_type, parm)] = [
                str(x) for x in (tpl.menuItems() or ())]
        except Exception:
            out["facts"]["menu_labels[%s].%s.%s" % (cat, node_type, parm)] = None

    if instances:
        made = []
        try:
            for node_type, cat in INSTANCE_NODES.items():
                if node_type not in cats[cat].nodeTypes():
                    continue
                container = world
                if cat == "sop":
                    # hou.Node.createNode() takes (node_type, node_name, ...); there
                    # is no no_switch_to keyword - invented kwarg, caught by hython.
                    container = hou.node("/obj").createNode("geo", "factsheet_geo")
                    made.append(container)
                elif cat == "rop":
                    container = hou.node("/out")
                node = container.createNode(node_type, "factsheet_" + node_type)
                made.append(node)
                names = {p.name() for p in node.parms()}
                got = {w: (w in names) for w in PARMS.get(node_type, [])}
                for bad in PARMS_ABSENT.get(node_type, []):
                    got[bad] = bad in names
                out["facts"]["parms[%s].%s" % (cat, node_type)] = got
                # Menu values, read from a live parm. VERIFIED on 22.0.368: the only
                # menu accessor on hou.Parm is menuItems(); there is no
                # menuItemIdentifier / menuItemLabels / menuItemValues. The settable
                # identifier is what rawValue() reports back.
                for _c, menu_type, menu_parm in MENUS:
                    if menu_type != node_type:
                        continue
                    mp = node.parm(menu_parm)
                    if mp is None:
                        continue
                    out["facts"]["menu_ids[%s].%s.%s" % (cat, node_type, menu_parm)] = {
                        "menuItems": [str(x) for x in (mp.menuItems() or ())],
                        "rawValue": mp.rawValue(),
                        "menuItemIdentifier_absent": not hasattr(mp, "menuItemIdentifier"),
                        "menuItemLabels_absent": not hasattr(mp, "menuItemLabels"),
                        "menuItemValues_absent": not hasattr(mp, "menuItemValues"),
                    }
        finally:
            for node in reversed(made):
                try:
                    node.destroy()
                except Exception:  # noqa: BLE001 - stale handle; keep cleaning
                    pass
        out["environment"]["hip_unsaved_after"] = hou.hipFile.hasUnsavedChanges()
        out["environment"]["nodes_after_cleanup"] = len(list(world.allSubChildren()))
    return out


def diff(path_a: str, path_b: str) -> int:
    """Compare only keys both sides reported, so an instances=True run can be diffed
    against a read-only live run without inventing differences."""
    a = json.load(open(path_a, encoding="utf-8"))
    b = json.load(open(path_b, encoding="utf-8"))
    lines = []

    def mismatches(ka, kb, prefix=""):
        if isinstance(ka, dict) and isinstance(kb, dict):
            for key in sorted(set(ka) & set(kb)):
                mismatches(ka[key], kb[key], prefix + key + ".")
        elif ka != kb:
            lines.append("%s: hython=%r other=%r" % (prefix[:-1], ka, kb))

    for key in ("facts", "counts"):
        mismatches(a.get(key, {}), b.get(key, {}), key + ".")
    env_a, env_b = a.get("environment", {}), b.get("environment", {})
    skip = {"hip_file", "hip_dirty_before", "hip_dirty_after", "has_ui",
            "nodes_after_cleanup"}
    for key in sorted((set(env_a) & set(env_b)) - skip):
        if env_a[key] != env_b[key]:
            lines.append("environment.%s: %r vs %r" % (key, env_a[key], env_b[key]))
    if lines:
        print("PARITY DIFFERENCES (%d):" % len(lines))
        print("\n".join("  " + line for line in lines))
        return 1
    print("hython and the live session agree on every fact both could read.")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) == 3 and argv[0] == "--diff":
        return diff(argv[1], argv[2])
    print(json.dumps(collect(instances="--instances" in argv),
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
