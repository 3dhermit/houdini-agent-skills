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
        "id": "type-templates-under-report",
        "match": r"parmTemplate(Group|s)?|unknown_parm|unresolved_parms",
        "cause": "NodeType.parmTemplates() and parmTemplateGroup() list only what the "
                 "type declares. Parms the framework adds at instance time are absent: "
                 "grid sizex/sizey, filecache sopoutput/f1/f2, karma f1/resolutionx, "
                 "USD ROP mkpath. A validator built on the type rejects parms that exist.",
        "fix": "Read parms from an instance (node.parms() / node.parmTuples()). Use type "
               "templates only for labels, folders and menu definitions.",
        "verified": True,
        "evidence": "verified on 22.0.368; locked by tests/test_tier_b.py::"
                    "TestIntrospectionReality.test_type_templates_under_report_instance_parms",
    },
    {
        "id": "opnodetype-no-parmtemplateset",
        "match": r"has no attribute '?parmTemplateSet'?",
        "cause": "parmTemplateSet() is on hou.NodeType, but category.nodeTypes() returns "
                 "hou.OpNodeType, which exposes parmTemplates() and parmTemplateGroup().",
        "fix": "Use node_type.parmTemplates() (a tuple of templates) or "
               "node_type.parmTemplateGroup().find(name).",
        "verified": True,
        "evidence": "verified on 22.0.368 (AttributeError even suggests parmTemplates); "
                    "locked by TestIntrospectionReality.test_opnodetype_api_names",
    },
    {
        "id": "parm-no-menu-item-identifier",
        "match": r"menuItemIdentifier|menuItemLabels|menuItemValues",
        "cause": "hou.Parm has exactly one menu accessor: menuItems(), a flat tuple of "
                 "strings. There is no menuItemIdentifier/menuItemLabels/menuItemValues. "
                 "MenuParmTemplate.menuItems() is likewise flat - it has no items() and "
                 "does not return (label, identifier) pairs.",
        "fix": "Match menu values by index or by the string itself, and confirm what a "
               "parm expects by reading rawValue() after a set. Never unpack "
               "menuItems() into pairs.",
        "verified": True,
        "evidence": "verified on 22.0.368; locked by "
                    "TestIntrospectionReality.test_parm_menu_api_is_menuitems_only",
    },
    {
        "id": "hipfile-no-dirty",
        "match": r"hipFile' object has no attribute '(dirty|modified|suppressSaveOnExit)'",
        "cause": "hou.hipFile has no dirty(), modified() or suppressSaveOnExit. The "
                 "unsaved-changes check is hasUnsavedChanges().",
        "fix": "hou.hipFile.hasUnsavedChanges() to test, .save()/.saveAndBackup() to write. "
               "Never save someone else's session to prove a probe was harmless - prove it "
               "by counting nodes before and after instead.",
        "verified": True,
        "evidence": "verified on 22.0.368; locked by "
                    "TestIntrospectionReality.test_hipfile_dirty_check_is_hasUnsavedChanges",
    },
    {
        "id": "nodetype-name-collision",
        "match": r"nodeType|nodeTypes\(\)",
        "cause": "Type names collide across categories on 22.0.368: karma is both a ROP "
                 "and a LOP type, and SOP/LOP share 33 names (copytopoints, attribwrangle, "
                 "cache, dopnet, null ...), ROP/LOP 17, SOP/OBJ 22. Looking a name up in "
                 "the wrong category silently gives you a different tool.",
        "fix": "Always qualify: hou.sopNodeTypeCategory().nodeTypes() vs "
               "hou.lopNodeTypeCategory().nodeTypes(). Resolve types against the network "
               "you are creating in (network.childTypeCategory()).",
        "verified": True,
        "evidence": "verified on 22.0.368 (33 sop/lop, 17 rop/lop shared names); locked by "
                    "TestIntrospectionReality.test_node_type_names_collide_across_categories",
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
    {
        "id": "parm-parmtuple-value-accessors",
        "match": r"has no attribute '(val|vals|getValue)'",
        "cause": "hou.Parm has no val()/vals()/getValue(), and hou.ParmTuple has no "
                 "rawValue()/vals()/val()/parms(). 19.5-era scripts use parm.val() and "
                 "parmTuple.vals() constantly, so this breaks on porting. On 22.0.368 "
                 "Parm exposes rawValue() (menu identifier string) and eval() (menu "
                 "index); ParmTuple exposes eval() - the only tuple value reader.",
        "fix": "Read a single parm with parm.rawValue() (strings/menus) or parm.eval() "
               "(numbers/objects); read a tuple with parmTuple.eval(). Write with "
               "parm.set() / parmTuple.set((x, y, z)).",
        "verified": True,
        "evidence": "verified on 22.0.368 (dir() plus AttributeError suggestions); locked "
                    "by tests/test_tier_b.py::TestHoudini22ApiSpellings.test_parm_value_accessors",
    },
    {
        "id": "boundingbox-minvec-lowercase",
        "match": r"has no attribute 'minVec'|has no attribute 'maxVec'",
        "cause": "hou.BoundingBox spells its corners minvec()/maxvec() with a lowercase v. "
                 "minVec()/maxVec() is the natural camelCase guess; when it sits inside a "
                 "bare except it drops bounds silently instead of failing.",
        "fix": "Use b.minvec()/b.maxvec() (or b.minvec3()/b.maxvec3()); if a bounds probe "
               "returns nothing, check for the swallowed AttributeError before believing "
               "the geometry is empty.",
        "verified": True,
        "evidence": "verified on 22.0.368; this repo's own geometry_stats lost framed_bounds "
                    "for exactly this reason; locked by "
                    "tests/test_tier_b.py::TestHoudini22ApiSpellings.test_bounding_box_is_lowercase_vec",
    },
    {
        "id": "xform-no-rotatex-use-rx",
        "match": r"rotatex|rotatey|rotatez|translatey",
        "cause": "The xform SOP on 22.0.368 has no rotatex/translatey parms. It exposes the "
                 "t/r/s (and p/pr/xOrd) parm tuples whose children are tx,ty,tz and rx,ry,rz. "
                 "A skill that set 'rotatex' returned False and was ignored, so an RBD column "
                 "never leaned - and a column with no potential energy honestly never falls, "
                 "which reads like a solver problem.",
        "fix": "Set xform.parm('rx') / parmTuple('r').set((x,y,z)) and xform.parm('ty'); then "
               "prove it by comparing boundingBox extents before and after, not by trusting "
               "the set call.",
        "verified": True,
        "evidence": "verified on 22.0.368 (parm('rotatex') is None; rx=12 + ty=1 moved the box "
                    "to y -0.571..2.571, z -0.801..0.801); locked by "
                    "tests/test_tier_b.py::TestHoudini22ApiSpellings.test_xform_uses_tuple_children_not_rotatex",
    },
    {
        "id": "scatter-no-number-use-npts",
        "match": r"scatter.*\bnumber\b|\bnumber\b.*scatter|stdswitcher",
        "cause": "scatter::2.0 on 22.0.368 has no `number` parm (and no 19.5-era "
                 "stdswitcher). Point count is `npts`; the mode menu `generateby` offers "
                 "exactly bydensity, countperprimitive, texturespace. Silent no-ops here are "
                 "how a 20-site fracture turns into an 11,067-prim explosion.",
        "fix": "Bind the count through npts (with fallbacks), then read the real point count "
               "off the cooked geometry and report it, so a knob that did not land is visible.",
        "verified": True,
        "evidence": "verified on 22.0.368 (npts=7 cooked exactly 7 points); locked by "
                    "tests/test_tier_b.py::TestHoudini22ApiSpellings.test_scatter_counts_points_through_npts",
    },
    {
        "id": "usd-export-layers-not-self-contained",
        "match": r"portability_note|self_contained|subLayers",
        "cause": "A `usd` ROP pointed at a SOP Import LOP writes the requested file as a thin "
                 "layer whose only content is a subLayers entry; the geometry is authored by "
                 "the LOP into its own layer at `savepath` (default '$HIP/usd/$OS.usd'). The "
                 "composed stage is correct, but the folder is not portable and bytes-on-disk "
                 "proved nothing. Verified: `flattensoplayers` changed nothing for this setup.",
        "fix": "Set the LOP's enable_savepath=1 and savepath=<output dir>/<name>.usd so both "
               "files land together (subLayer then resolves as './<name>.usd'). Prove an export "
               "by opening it with pxr and counting prims that carry a points attribute, "
               "compared against the SOP source point count.",
        "verified": True,
        "evidence": "verified on 22.0.368 (default run wrote a 229-byte pointer layer plus a "
                    "SOP layer under $HIP; with savepath pinned, subLayer = './a_sop.usd' and "
                    "the Mesh still had all 16 points); locked by "
                    "tests/test_tier_b.py::TestUsdExportLandsGeometry",
    },
    {
        "id": "pxr-usd-accessor-spellings",
        "match": r"TraverseAllPims|TraverseAllPrims|has no attribute 'GetType'|has no attribute 'pathname'",
        "cause": "pxr in Houdini 22: Usd.Stage has only Traverse() and TraverseAll(); "
                 "Usd.Prim's type accessor is GetTypeName() (no GetType()); Sdf.Path has "
                 "pathString/str() (no .pathname). A hasattr() guard around a misspelling "
                 "turns the bug into a silent None.",
        "fix": "Usd.Stage.Open(p).Traverse(), prim.GetTypeName(), str(prim.GetPath()). Never "
               "wrap a guessed accessor in hasattr - assert the spelling in a test instead.",
        "verified": True,
        "evidence": "verified on 22.0.368 pxr; locked by "
                    "tests/test_tier_b.py::TestHoudini22ApiSpellings.test_pxr_spellings_used_by_verify",
    },
    {
        "id": "node-type-categories-accessor",
        "match": r"categoryAllAsDictionary|nodeTypeCategories|no pyro",
        "cause": "The accessor is hou.nodeTypeCategories() (there is no "
                 "categoryAllAsDictionary). Categories are Chop, ChopNet, Cop, Cop2, CopNet, "
                 "Data, Director, Dop, Driver, Lop, Manager, Object, Shop, Sop, Top, TopNet, "
                 "Vop, VopNet. Searching only /obj types says 'pyro does not exist': on "
                 "22.0.368 pyro appears as 3 Dop, 14 Sop, 36 Cop, 23 Vop, 3 Shop and 71 Data "
                 "types. Bare names also collide - karma is a ROP and a LOP type.",
        "fix": "Iterate hou.nodeTypeCategories() and report each hit with its category; create "
               "the node inside a network of that category.",
        "verified": True,
        "evidence": "verified on 22.0.368; locked by "
                    "tests/test_tier_b.py::TestHoudini22ApiSpellings.test_node_type_categories_cover_pyro",
    },
    {
        "id": "hou-ui-absent-in-hython",
        "match": r"module 'hou' has no attribute 'ui'|has no attribute 'setCurrentNode'|has no attribute 'internalName'",
        "cause": "hou.ui exists only in a GUI product - hython/hbatch have no hou.ui at all, "
                 "so any pane call is GUI-only. The hou module also has no setCurrentNode, and "
                 "hou.PaneTabType values expose only name() (no internalName/internalType/"
                 "enumerationName).",
        "fix": "Guard pane work with hasattr(hou, 'ui') and report it as unavailable in "
               "headless runs. To move a network editor view in the GUI: "
               "hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor).setCurrentNode(node).",
        "verified": True,
        "evidence": "verified in hython 22.0.368 (AttributeError: module 'hou' has no "
                    "attribute 'ui') and in the live GUI, where hou.ui.paneTabs() returned "
                    "12 tabs and hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor) returned "
                    "a pane whose currentNode() was /obj/rbd/rbd_solver1. "
                    "PaneTabType.name() returned 'NetworkEditor'; no other PaneTabType "
                    "accessor exists. setCurrentNode() on that pane was deliberately not "
                    "called - it would move someone else's viewport.",
    },
    {
        "id": "live-session-module-cache",
        "match": r"KeyError.*'knobs'|old payload shape|edited skill had no effect",
        "cause": "Houdini's Python keeps imported modules in sys.modules between calls, so "
                 "a skill layer edited on disk is NOT reloaded inside a running GUI "
                 "session. A live run then returns the pre-edit payload shape (a KeyError "
                 "on a key the new code adds, or a fix that 'does nothing').",
        "fix": "At the top of any script injected into a live session, purge the layer "
               "before importing it: "
               "`for k in [k for k in list(sys.modules) if k.split('.')[0] in "
               "('houdini_skills', 'skills')]: del sys.modules[k]`. Then re-import. "
               "Verify the new shape is present before trusting a negative result.",
        "verified": True,
        "evidence": "live session on 22.0.368 raised KeyError 'knobs' on a run before the "
                    "purge was added, and returned the full knobs payload after it; the "
                    "purging runner is what all later live smokes used",
    },
    {
        "id": "builder-leftover-dopnet",
        "match": r"leftover|_dopnet|asm_live.*still present",
        "cause": "Simulation builders make nodes in more than one place: the container you "
                 "named at /obj plus a separate dopnet object (/obj/<container>_dopnet). "
                 "A caller that destroys only the container leaks the dopnet, and each run "
                 "leaks another one (_dopnet1, _dopnet2).",
        "fix": "Destroy the payload's `created_nodes` list, deepest paths first - every "
               "builder and exporter now reports it, including the /out ROP and the LOP in "
               "/stage. Prove the scene is clean by node count: "
               "len(hou.node('/').allSubChildren()) before == after.",
        "verified": True,
        "evidence": "live smokes left /obj/asm_live_rbd_dopnet{,1,2} while destroying only "
                    "the named containers; after created_nodes landed, a live run went "
                    "13533 -> 13533 nodes with swept_leaked_paths == []",
    },
    {
        "id": "sopnode-no-input-state-api",
        "match": r"has no attribute 'inputState'|has no attribute 'inputOutputState'|has no attribute 'inputIsMuted'|has no attribute 'inputType'|has no attribute 'NodeConnector'",
        "cause": "hou.SopNode on 22.0.368 has inputLabels(), inputs(), "
                 "inputConnections() and inputConnectors(), but NOT inputState(), "
                 "inputOutputState(), inputIsMuted(), isInputMuted() or inputType() - "
                 "input mute state is simply not queryable through hou on this build. "
                 "The connector class is hou.NodeConnection; hou.NodeConnector raises "
                 "AttributeError with a 'Did you mean' pointing at it.",
        "fix": "Diagnose a chain by cooking it: per input report "
               "len(inp.geometry().prims()) - a 0 or None tells you where geometry "
               "died, which is what a mute-state guess was standing in for. Use "
               "inputLabels() for names. Never claim an input is muted when you cannot "
               "read that state.",
        "verified": True,
        "evidence": "live session 22.0.368: SopNode.inputState() raised AttributeError "
                    "inside this repo's own diagnose_node; hasattr probes on "
                    "/obj/geo1/xform1 were False for inputState/inputOutputState/"
                    "inputIsMuted/isInputMuted/inputType and True for inputLabels; "
                    "hou.NodeConnector raised AttributeError (Did you mean: "
                    "NodeConnection), whose dir() exposes no mute/state methods",
    },
    {
        "id": "category-names-are-hous-not-the-menus",
        "cause": "hou's category names are not the UI labels. hou.nodeTypeCategories() is "
                 "keyed: Chop, ChopNet, Cop, Cop2, CopNet, Data, Director, Dop, Driver, "
                 "Lop, Manager, Object, Shop, Sop, Top, TopNet, Vop, VopNet. The render "
                 "category is **Driver** - hou.ropNodeTypeCategory().name() returns "
                 "'Driver', so hou.nodeTypeCategories()['ROP'] raises KeyError. Its "
                 "label() is 'Outputs', which is the tab name in the UI (Lop labels as "
                 "'Solaris', Sop as 'Geometry'). Accessors differ by class: "
                 "NodeTypeCategory has name()/label()/typeName() but no keyName(), "
                 "internalName() or description(); NodeType/OpNodeType/SopNodeType have "
                 "name() and description() but no label(), internalName(), typeName() or "
                 "knownTypeNames().",
        "match": r"KeyError.*'ROP'|nodeTypeCategories\(\)\[|category\(\)\.name\(\)|has no attribute 'label'|has no attribute 'knownTypeNames'",
        "fix": "Key category lookups by hou's own names, and compare case-insensitively "
               "(a SOP node's category().name() is 'Sop'). Ask a network what it accepts "
               "with node.childTypeCategory() (/out -> Driver, a geo SOP net -> Sop, "
               "/stage -> Lop). troub.version_report returns counts keyed by hou's names "
               "with a human 'label' beside each.",
        "verified": True, "observed_on": "22.0.368",
        "evidence": "hython + live probe: hou.ropNodeTypeCategory().name() == 'Driver' "
                    "(90 types, karma among them), label() == 'Outputs'; Lop name 'Lop' "
                    "label 'Solaris' (219 types, karma, filecache); Sop name 'Sop' label "
                    "'Geometry' (1693); NodeTypeCategory lacks keyName/internalName/"
                    "description; SopNodeType lacks label/internalName/typeName/"
                    "knownTypeNames; a case-sensitive category()=='SOP' check flagged "
                    "every SOP in this repo's own empty_geo_report",
    },
    {
        "id": "type-names-collide-across-categories",
        "cause": "Node type names collide across categories: 'karma' is both a Driver "
                 "(render) and a Lop type; 'filecache' is both a Sop and a Lop type. A "
                 "lookup that ignores category hands you the wrong node class, and the "
                 "wrong node silently refuses the parms you set.",
        "match": r"wrong node type|no parm|parm.*not found|invalid.*type",
        "fix": "Resolve the type against the network you are creating in: "
               "troub.node_type_lookup reports valid_in_network for the network you name, "
               "and sop.list_types lists only what that network accepts.",
        "verified": True, "observed_on": "22.0.368",
        "evidence": "22.0.368 probe: karma present in Driver (90) and Lop (219); filecache "
                    "present in Sop (1693) and Lop (219); SOP and LOP share 33 type names",
    },
    {
        "id": "parm-set-silently-clamps",
        "cause": "A set() call that does not raise is not proof the parm holds your "
                 "value. On 22.0.368 hou clamps silently: grid.rows = -5 and rows = 0 "
                 "both end up 1; karma.samplesperpixel = 0 ends up 1; a menu parm takes "
                 "an index and clamps it (karma.trange = 99 -> index 3, rawValue() "
                 "'stage'; -3 -> index 0, 'off'). An int parm given a fractional value "
                 "instead RAISES TypeError: 'Cannot set an int parm to a fractional "
                 "value' - it does not round. Float parms with no range keep negatives "
                 "(grid.sizex = -3 stays -3.0).",
        "match": r"Cannot set an int parm to a fractional value|clamped|parm.*reset|"
                 r"value.*not.*stick|set.*had no effect|parm_set_failed",
        "fix": "Read the parm back after setting: rawValue() for the label/string, "
               "eval() for the number or menu index. Session.set_parms does this and "
               "returns set/kept/drift; trust kept and act on drift (clamp the input "
               "yourself, or pick a valid menu index) instead of believing the request.",
        "verified": True, "observed_on": "22.0.368",
        "evidence": "hython probe 22.0.368: grid.rows set to -5 -> 1, 0 -> 1, 2.7 -> "
                    "TypeError 'Cannot set an int parm to a fractional value', 3 -> 3; "
                    "grid.sizex 6.5 -> 6.5, -3 -> -3.0; karma.samplesperpixel 0 -> 1; "
                    "karma.trange menuItems ('off','normal','on','stage') with 99 -> eval "
                    "3 raw 'stage', -3 -> eval 0 raw 'off', 2 -> eval 2 raw 'on'",
    },
]

# Causes of empty geometry, checked in order by empty_geo_report.
EMPTY_GEO_CHECKS = [
    ("wrong_category", "the node is not a SOP (hou's category name is 'Sop', so this "
                       "must be compared case-insensitively)"),
    ("no_geometry", "node has no geometry object at all"),
    ("zero_prims", "geometry exists but has no primitives"),
    ("input_cook", "every wired input is cooked and its prim count reported"),
    ("input_empty", "a wired input already cooks to 0 primitives, so the loss is upstream"),
    ("upstream_walk", "up to 12 levels of first inputs walked for the last non-empty node"),
]
# Honest about the limits: the mute-state check a 19.5 script would want is not
# possible on 22.0.368, and this action does not inspect the display flag.
EMPTY_GEO_NOT_CHECKED = [
    "input mute/passthrough state (hou.SopNode has no inputState/inputOutputState/"
    "inputIsMuted on 22.0.368)",
    "where the container's display flag is set",
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
    # Verified on 22.0.368: hou.SopNode has inputLabels() but NO inputState(),
    # inputOutputState(), inputIsMuted() or inputType() - and input muting is not
    # queryable through the hou API on this build. What actually locates a broken
    # chain is each input's own type and cooked prim count.
    try:
        labels = list(node.inputLabels() or [])
    except Exception:
        labels = []
    for i, inp in enumerate(node.inputs()):
        if inp is None:
            inputs.append({"index": i, "label": labels[i] if i < len(labels) else None,
                           "path": None, "type": None, "output_prims": None})
            continue
        try:
            prims = len(inp.geometry().prims())
        except Exception:
            prims = None
        inputs.append({"index": i, "label": labels[i] if i < len(labels) else None,
                       "path": inp.path(), "type": s._type(inp), "output_prims": prims})
    return ok({"path": node.path(), "type": s._type(node),
               "errors": hard, "warnings": [e for e in errs if e["severity"] == "warning"],
               "explained": explained, "inputs": inputs,
               "input_state_note": "input mute/cook state is not exposed by hou on "
                                   "22.0.368 (inputState/inputOutputState/"
                                   "inputIsMuted/inputType all absent on hou.SopNode); "
                                   "output_prims per input is the usable signal",
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
    # hou spells category names 'Sop'/'Lop'/'Rop' - a case-sensitive compare against
    # "SOP" flagged every real SOP as the wrong category (found by a live run).
    if cat.lower() != "sop":
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
        # Verified on 22.0.368: no inputOutputState()/inputIsMuted() on hou.SopNode,
        # so an empty input is detected by cooking it, not by reading mute state.
        try:
            count = len(inp.geometry().prims())
        except Exception:
            count = None
        findings.append({"check": "input_cook",
                         "detail": f"input {i} from {inp.path()} cooked "
                                   + ("0 primitives" if count == 0 else
                                      (f"{count} primitives" if count is not None else
                                       "no geometry (node failed to cook)"))})
        if count == 0:
            findings.append({"check": "input_empty",
                             "detail": f"input {i} ({inp.path()}) is already empty - "
                                       "the loss is upstream of here"})
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
               "checks_available": [c[0] for c in EMPTY_GEO_CHECKS],
               "not_checked": EMPTY_GEO_NOT_CHECKED})


def _node_type_lookup(s, p):
    name = p["name"].lower()
    network = p.get("network") or ""
    # Which category can actually create that name in the network you named. Name-based
    # scoping alone would lie: 'karma' is a type name in BOTH the Driver (render) and Lop
    # categories, so "karma is valid in /out" says nothing about WHICH karma you get.
    # hou answers this with node.childTypeCategory() - the category a network accepts.
    child_cat = None
    if network:
        net = s.node(network)
        try:
            child_cat = net.childTypeCategory().name()
        except Exception:
            child_cat = None
    hits = {}
    cats = s.hou.nodeTypeCategories()
    for label in sorted(cats):
        try:
            types = set(cats[label].nodeTypes())
        except Exception:
            continue
        exact = sorted(t for t in types if t.lower() == name)
        near = suggest(name, types, 6)
        if exact or near:
            hits[label] = {"exact": exact, "close": near,
                           "creatable_here": (label == child_cat) if network else None}
    if not hits:
        raise SkillFailure("no_such_node_type", f"nothing like {p['name']!r} in any category",
                           hint="try a broader keyword, e.g. 'voronoi', 'wrangle', 'cache'")
    return ok({"query": p["name"], "network": network or None,
               "network_child_category": child_cat,
               "categories_searched": sorted(cats), "categories": hits,
               "note": "type names collide across categories (karma is a Driver and a Lop "
                       "type, filecache a Sop and a Lop type); creatable_here marks the "
                       "category node.childTypeCategory() reports for the network you named"})


def _version_report(s, p):
    apprentice = False
    try:
        apprentice = bool(s.hou.isApprentice())
    except Exception:
        pass
    cats = {}
    # Key the counts by the names hou itself uses, so a caller can feed them straight
    # back into hou.nodeTypeCategories(). The UI labels do NOT match: hou calls the
    # render category 'Driver' (label 'Outputs'), and a lookup by "ROP" raises KeyError.
    hou_cats = s.hou.nodeTypeCategories()
    for key in sorted(hou_cats):
        try:
            cats[key] = {"count": len(hou_cats[key].nodeTypes()),
                         "label": hou_cats[key].label()}
        except Exception:
            cats[key] = {"count": None, "label": None}
    from houdini_skills.versions import MIN_SUPPORTED
    return ok({"version": str(s.version), "series": list(s.version.series),
               "apprentice_licence": apprentice, "node_type_counts": cats,
               "category_name_warning": "hou names the render category 'Driver', not "
                                        "'ROP' (hou.ropNodeTypeCategory().name() == 'Driver')",
               "repo_floor": list(MIN_SUPPORTED), "frame_range": s.frame_range()})


_HANDLERS = {
    "diagnose_node": _diagnose_node,
    "hou_lookup": _hou_lookup,
    "explain_error": _explain_error,
    "node_type_lookup": _node_type_lookup,
    "empty_geo_report": _empty_geo_report,
    "version_report": _version_report,
}
