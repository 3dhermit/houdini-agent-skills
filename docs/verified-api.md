# Verified Houdini API facts

Every line here was read off a live Houdini **22.0.368** (Windows) with `hython`,
not recalled from documentation or guessed from another version. This file is
why the skill tables can be trusted, and
`tests/test_tier_b.py::TestVerifiedApiFacts` asserts every row against a running
Houdini so this doc cannot drift. When a new Houdini major lands, run that test
class first: the failures *are* the migration list.

This file has already been corrected once by its own test - which caught wrong
claims about `scatter`, `copytopoints`, `filecache`, `karma`, `mantra` and the
LOP types. That is the process working, not a typo.

## Module level

| fact | detail |
|---|---|
| version | `hou.applicationVersion()` -> `(22, 0, 368)`, `hou.applicationVersionString()` -> `'22.0.368'`, `hou.isApprentice()` -> `False`. **`hou.version()`, `hou.applicationVersionTuple()`, `hou.applicationBuild()`, `hou.licenseManager()` do not exist** in `hython`. |
| node lookup | `hou.node(path)`. **There is no `hou.glob()` and no `hou.hpys()`.** Use `hou.node("/").allChildren()` and match with `fnmatch`. |
| frame range | `hou.playbar` is an **object, not callable**: `hou.playbar.frameRange()` -> `[1, 240]`, `hou.playbar.setFrameRange(start, end)`. **`hou.setFrameRange()`, `hou.playbackBarMinFrame()` and `hou.fetchGlobalSetup()` do not exist.** `hou.frame()` / `hou.setFrame()` do. |
| node types | `hou.objNodeTypeCategory().nodeTypes()`, `hou.sopNodeTypeCategory()`, `hou.ropNodeTypeCategory()`, `hou.lopNodeTypeCategory()`, `hou.dopNodeTypeCategory()`. `hou.networkNodeTypeCategory()` **does not exist**. From a node: `node.childTypeCategory().nodeTypes()`. |

## Nodes and parms

| fact | detail |
|---|---|
| parm enumeration | `hou.Node` has **no `parmNames()`**. Use `node.parms()` (-> `hou.Parm`), `node.parmTuples()`, `node.allParms()`. |
| reading a value | `parm.rawValue()` is safe on any type; `parm.eval()` throws on strings/menus. Menu parms: `parm.menuItems()`. |
| menu items shape | On 22.0.368 `menuItems()` returns **bare strings**. Wrangle `class`: `('detail','primitive','point','vertex','number')`. Karma `trange`: `('off','normal','on','stage')`. File Cache `trange`: `('off','normal')`. Older builds return `(label, value)` pairs - resolve by label, never by hardcoded index. |
| parm templates | `node.parmTemplate(name)` raises unless the name is a **parm**; for folder/menu templates use `node.parmTemplateGroup().find("class")` and `.menuItems()`. |

## Geometry

| fact | detail |
|---|---|
| primitives | `geo.prims()`, `hou.PolySphere`, `hou.PackedPrim` exist. |
| attribs on a prim | `prim.findAttrib(name)` -> `hou.PrimAttribute` or `None`, then `prim.attribValue(name)`. **`hou.Prim.findAttribValue()` does not exist** (that is the `hou.Geometry` API). |
| wrangle context | a detail wrangle on a raw grid sees **1 prim, 0 points** in `s@numprims`/`n@numpoints` style reads for packed contexts - do not write stats tests that assume both. |

## Node types that exist (and do not)

**OBJ category.** `dopnet` is the simulation container. There is **no `pyro`,
`rbd`, `flip`, `pyroobject`, `rbdobject` or `flipobject` OBJ node type** (all
verified absent). LLMs invent those constantly; on 22.0.368 creation fails with
`hou.NodeTypeDefinitionError`.

**SOP category - present.** `pyrosource`, `pyrosolver`, `pyrosourcepack`,
`pyrospawnsources`, `pyrobakevolume`, `pyropostprocess`, `rbdbulletsolver`,
`rbdmaterialfracture`, `voronoifracture`, `rbdpack`, `flipsource`, `flipsolver`,
`vellumsolver`, `filecache`, `dopio`, `attribwrangle` (plus `attribwranglecore`,
`pointwrangle`, `volumewrangle`, `deformationwrangle`), `grid`, `box`, `polybevel`,
`subdivide`, `blast`, `groupexpression`, `scatter`, `copytopoints`, `pack`,
`switch`.

**SOP category - absent.** `wrangle`, `pyro`, `findattribvalue`, `dopnetwork`,
`rbdpointfracture`, `rbdconnectedparticles`, `rigidbodyforces`, `flipobject`,
`flipconfigureobject`, `vellumsource`. A type can exist in one category and not
another, and the registered name can differ from the obvious one - which is
exactly why these lists come off the running build instead of memory.

**ROP category** (90 types on this build). `karma`, `usd`, `usdrender`,
`usdstitch`, `usdstitchclips`, `usdzip`, `opengl`. **`mantra` is not registered
on 22.0.368.** The 19.5-21.x floor the skills declare still needs a mantra path,
but on this build the mantra fallback raises `engine_unavailable` with the real
available list. `karma_xpu` was never a node type.

**LOP category.** `sopimport`, `sublayer`, `componentoutput`,
`componentgeometry`, `stagemanager`, `sceneimport`, `graftstages`,
`configurestage`. **Not** `stageimport`, `componentlist`, `addloplattriblayer`.

## Verified parm names

| node | verified parms |
|---|---|
| `grid` | `sizex`, `sizez`, `divisionsx`, `divisionsz` (**not** `sx`/`sz`/`rows`/`cols`) |
| `box` | `sizex`, `sizey`, `sizez`, `divisionsx`, `divisionsy`, `divisionsz` |
| `polybevel` | width is **`offset`** (plus `offsetedges`, `offsetpoints`, `useoffsetscale`) - **no `bevelamount`, `width` or `size`** |
| `scatter::2.0` | `npts`, `seed`, `generateby`, `usemaxradius`, `maxradius`, `relaxpoints`, `relaxiterations`, `emergencylimit`, `group`, `pointattribs`, `primattribs`, `densityattrib` - **no `perpoint`, `mindist`, `usedist`, `poisson`, `number`** |
| `copytopoints::2.0` | `pack`, `pivot`, `transform`, `targetattribs`, `sourcegroup`, `sourcegrouptype`, `targetgroup`, `idattrib`, `resettargetattribs`, `viewportlod` - **no `pastepoints`, `pasteprims`** |
| `attribwrangle` | `snippet`; class parm is `class` with menu values `detail`, `primitive`, `point`, `vertex`, `number` |
| `filecache::2.0` | `sopoutput` (the path), `f1`, `f2`, `trange` (`off`/`normal`), `execute`, `filemethod`, `filetype`, `loadfromdisk`, `mkpath`, `take` - **no `outputname`, `outputpath`, `t1`, `t2`, `method`, `name`, `outputnode`** |
| `karma` | `picture`, `resolutionx`, `resolutiony`, `res_overridex`, `res_overridey`, `res_fraction`, `trange` (`off`/`normal`/`on`/`stage`), `f1`, `f2`, `samplesperpixel`, `pathtracedsamples`, `geosamples`, `xformsamples`, `varianceaa_*`, `render`, `renderdialog`, `execute`, `allframesatonce` - **no `renderer`, `rendermethod`, `schedule`, `vm_renderer`, `outputmode`, `usevpresolution`, `saverange`** |
| USD Output ROP (`usd`) | `loppath`, `lopoutput` (default **`$HIP/usd/$OS.usd`**), `trange`, `f1`, `f2`, `execute`, `executebackground`, `outputprocessors` |
| `sopimport` (LOP) | `soppath` |

Frame range on ROPs and on File Cache is a **menu**, not a toggle: set `trange`
to the menu's *item value* (`normal` on both here), never `1`/`0`.

Karma CPU vs XPU: **no parm on the 22.0.368 Karma ROP selects XPU** - the full
parm list was scanned for `renderer`/`rendermethod`/`schedule`/`xpu` and none
exist. XPU is a licence and render-setting concern, so the rendering skill says
so instead of inventing a knob.

`createNode("filecache")` succeeds but `node.type().name()` returns the
**versioned** internal name `filecache::2.0` (same for `scatter::2.0`,
`copytopoints::2.0`, `pyropostprocess::2.0`). Compare against `nodeTypes()` keys
and strip `::` for display.

## VEX compiler reality (22.0.368)

| construct | result |
|---|---|
| `point(0, "attrib", @ptnum)` | OK - getter order is **(geo, attribute, index)**, not (geo, index, attribute) |
| `v@x = {1.0, 2.0, 3.0};` | OK - constant brace initializer |
| `float s = ch("s"); v@x = {s, s, s};` | **FAILS**: `Syntax error, unexpected identifier` |
| `float s = 0.5; v@x = set(s, s, s);` | OK - use `set()` when any component is a variable |
| `v@x = {1.0*p, 2.0*p, 3.0*p};` | OK - constant expressions inside braces are fine |
| `@Cd = set(t, 1.0 - t, 0.0);` | OK |
| `comp(f@`my_attr`, 0)` / `ramp("ramp", t, "lookup")` | not valid VEX - dynamic attribute access needs `indirect` attribute syntax, and `ramp()` is not a VEX function |
| `@id = fit01(@ptnum, 0, 1);` | FAILS: `@id` is read-only ("Cannot modify built-in id") |
| `@scale` on non-packed prims | not a writable attribute there; write a named attrib (e.g. `f@piece_scale`) or pack first |
| `xyzdist(1, @P, nearpr, nearpos)` + `prbbox(1, nearpr, lo, hi)` | OK - ground distance idiom for RBD leans |
| `@Cd = {1,0,0} + 0.2*rand(@id + 3.1);` | OK (constant braces on the right of arithmetic) |

## Simulation reality check

A freshly built RBD setup (`rbdpack` -> `rbdbulletsolver` -> `dopnet`) cooks with
**zero node errors** on 22.0.368, but with pieces resting on the ground plane and
no initial motion, nothing moves: **the graph is correct and static is the
physically right answer.** To get motion, give the pieces energy - a lean, a
drop, or an impactor. The `simulation-tools` skill does that with `xyzdist` +
`prbbox`, and its result says `simulated: false` because it does not step the
solver.

## USD reality check

`usd-pipeline export` wrote a real file: 239 bytes, `#usda 1.0`, with a
`sublayer` reference to a second layer the ROP produced next to it. The reported
`verify` block includes `sibling_usd_files` because reporting only the one path
you were handed under-describes the stage on disk.
