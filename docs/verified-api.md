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
| node lookup | `hou.node(path)`. **There is no `hou.glob()` and no `hou.hpys()`.** Use `hou.node("/")` with **`allSubChildren()`** for the whole file (see below) and match with `fnmatch`. |
| frame range | `hou.playbar` is an **object, not callable**: `hou.playbar.frameRange()` -> `[1, 240]`, `hou.playbar.setFrameRange(start, end)`. **`hou.setFrameRange()`, `hou.playbackBarMinFrame()` and `hou.fetchGlobalSetup()` do not exist.** `hou.frame()` / `hou.setFrame()` do. |
| node types | `hou.objNodeTypeCategory().nodeTypes()`, `hou.sopNodeTypeCategory()`, `hou.ropNodeTypeCategory()`, `hou.lopNodeTypeCategory()`, `hou.dopNodeTypeCategory()`. `hou.networkNodeTypeCategory()` **does not exist**. From a node: `node.childTypeCategory().nodeTypes()`. |

## Walking the node tree

`hou.Node` has **no `allChildren()`** - the recursive call is **`allSubChildren()`**
(verified on 22.0.368, and Python even suggests it: `Did you mean: 'allSubChildren'?`).
The obvious `children()` fallback only returns one level, which is the dangerous
kind of wrong: `find("/obj/geo/*")` returns nothing instead of failing, and
cleanup patterns like `/obj/asmtest_*/bevel1` silently delete nothing.

A handle can also go dead mid-walk: after `destroy()` on a parent, the child
objects `allSubChildren()` returned raise `hou.ObjectWasDeleted` on `.path()`.
Read the path before destroying and skip handles that are already dead.

## Type-level introspection is not instance truth (22.0.368)

`hou.OpNodeType` — what `category.nodeTypes()` returns — offers `parmTemplates()` (a
tuple of templates) and `parmTemplateGroup()`. There is **no `parmTemplateSet()`** on
it; the `AttributeError` helpfully suggests `parmTemplates`.

Both type-level reads **under-report parms**, because the framework adds some parms
only at instance time:

| Node | Absent from the type templates | Present on a real node |
| --- | --- | --- |
| `grid` | `sizex`, `sizey` | both |
| `box` | `sizex`, `sizey`, `sizez`, `divsx` | all four |
| `polybevel` | `offset` | yes |
| `filecache` | `sopoutput`, `f1`, `f2` | all three |
| `karma` | `f1`, `f2`, `resolutionx`, `resolutiony` | all four |
| `usd` | `f1`, `f2` | both |

So: **never validate a parm name against type templates** — it rejects parms that
exist. Read `node.parms()` / `node.parmTuples()`. Type templates are good for labels,
folders and menu definitions only.

### Menus

- `hou.Parm.menuItems()` is the **only** menu accessor on a parm: a flat tuple of
  strings. There is no `menuItemIdentifier`, no `menuItemLabels`, no `menuItemValues`.
- `hou.MenuParmTemplate.menuItems()` is flat too — no `items()`, and it does not hand
  back `(label, identifier)` pairs. Anything that unpacks it into two names blows up
  with `too many values to unpack`.
- The settable value is what `rawValue()` reports back. Verified menus: Karma and USD
  ROP `trange` → `off`, `normal`, `on`, `stage`; File Cache `trange` → `off`, `normal`;
  Attribute Wrangle `class` → `detail`, `primitive`, `point`, `vertex`, `number`.

### Reading and writing parm values (22.0.368)

The accessors most 19.5-era scripts use are **gone**:

| Object | Exists | Does not exist |
| --- | --- | --- |
| `hou.Parm` | `rawValue()` (string/menu identifier), `eval()` (number/menu index), `set()` | `val()`, `vals()`, `getValue()` |
| `hou.ParmTuple` | `eval()` → e.g. `(1.0, 1.0, 1.0)`, `set((x,y,z))`, `name()`, `len(t)` → child count | `rawValue()`, `vals()`, `val()`, `parms()` |
| `hou.BoundingBox` | `minvec()`, `maxvec()` (lowercase v) | `minVec()`, `maxVec()` |
| `hou.PaneTabType` value | `name()` → `'NetworkEditor'` | `internalName()`, `internalType()`, `enumerationName()` |
| `hou` module | — | `hou.setCurrentNode()` |
| `hou.playbar.frameRange()` | returns a `hou.Vector2` — `rng[0]`/`rng[1]` | `.start()`, `.end()` |

So `parm.val()` and `parmTuple.vals()` — the two most common calls in older Houdini
Python — raise `AttributeError` on this build. Read tuples through `eval()`.

### What `set()` actually keeps (22.0.368)

A successful `set()` call is **not** proof the parm holds your value. Read it back:

| Case | Request | What hou keeps |
| --- | --- | --- |
| int parm, fractional value | `grid.rows = 2.7`, `karma.resolutionx = 0.5` | raises `TypeError: Cannot set an int parm to a fractional value` — it does not round |
| int parm, integral float | `karma.resolutionx = 64.0` | `64` |
| number below the parm's minimum | `grid.rows = -5`, `grid.rows = 0`, `karma.samplesperpixel = 0` | `1` — clamped, **no error** |
| float parm, negative | `grid.sizex = -3` | `-3.0` (allowed, no clamp) |
| menu parm by index | `karma.trange = 99` | index `3` → `rawValue()` `'stage'` — clamped, no error |
| menu parm, negative index | `karma.trange = -3` | index `0` → `'off'` |
| menu parm, in range | `karma.trange = 2` | index `2` → `'on'` |

`Session.set_parms` therefore returns `set` / `kept` / `drift` rather than echoing the
request, and `sop.set_parms` passes that through. `rawValue()` gives the human label
(`'stage'`), `eval()` gives the index (`3`) — compare the request against both.

Panes are GUI-only: `hou.ui` **does not exist in hython at all**
(`module 'hou' has no attribute 'ui'`). In a live GUI it does: `hou.ui.paneTabs()`
returned 12 tabs and `hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)` returned
the pane showing `/obj/rbd/rbd_solver1`. Guard pane code with `hasattr(hou, "ui")`.

All of it is locked by `tests/test_tier_b.py::TestHoudini22ApiSpellings`.

### Wiring and input inspection (22.0.368)

`hou.Node` gives you `inputs()`, `input(i)`, `inputConnections()`,
`inputConnectors()`, `inputLabels()`, `inputNames()`, `inputsWithIndices()`,
`numOrderedInputs()`, `insertInput()`, `setInput()`, `setNamedInput()`.
`hou.SopNode` adds `inputGeometry()`, `inputGeometryAtFrame()`, `inputsAsData()` —
and **not** the state accessors a 19.5 script expects:

| Guessed | Real on 22.0.368 |
| --- | --- |
| `SopNode.inputState(i)` | absent (this repo's own `diagnose_node` crashed on it) |
| `SopNode.inputOutputState(i)` | absent — the guarded mute check was dead code |
| `SopNode.inputIsMuted()` / `isInputMuted()` | absent; input mute is not readable via hou here |
| `SopNode.inputType(i)` | absent |
| `hou.NodeConnector` | renamed: `hou.NodeConnection` (AttributeError says so); it exposes no mute/state methods |
| `SopNode.inputLabels()` | present — this is the one that works |

So the usable diagnostic is **cook each input and count prims**
(`len(inp.geometry().prims())`): a `0` or `None` localises where geometry died, which
is what a mute-state guess was standing in for.

`Session.find()` matches **paths with fnmatch** (`/obj/asm*`, `/out/*usd*`), not
substrings — `find("asm_l5")` correctly returns nothing; write `/obj/asm_l5*/*`.

### Hip-file state

`hou.hipFile.hasUnsavedChanges()`. There is no `dirty()`, no `modified()`, no
`suppressSaveOnExit`. To show a probe left someone's session alone, count nodes
(`len(hou.node("/").allSubChildren())`) before and after — do **not** save.

### Type names collide across categories

| Pair | Shared type names | Examples |
| --- | --- | --- |
| SOP / LOP | 33 | `copytopoints`, `attribwrangle`, `cache`, `dopnet`, `null` |
| ROP / LOP | 17 | **`karma` is in both**, `fetch`, `null` |
| SOP / OBJ | 22 | `dopnet`, `chopnet`, `cop2net` |
| OBJ / LOP | 16 | `dopnet`, `light`, `matnet` |

`hou.lopNodeTypeCategory().nodeType("copytopoints")` is not the SOP tool. Qualify the
category, or resolve types against the network you are creating in.

Also corrected while probing: the ROP fetch driver is **`fetch`** — there is no
`fetchnetwork` and no `driver` type. LOP component types are `componentgeometry`,
`componentmaterial`, `componentoutput`; there is no `componentlist` and no `subgraph`
type (subgraph editing is a mode, not a node).

The category accessor is `hou.nodeTypeCategories()` — there is no
`hou.categoryAllAsDictionary()`. The categories on 22.0.368 are: `Chop`, `ChopNet`,
`Cop`, `Cop2`, `CopNet`, `Data`, `Director`, `Dop`, `Driver`, `Lop`, `Manager`,
`Object`, `Shop`, `Sop`, `Top`, `TopNet`, `Vop`, `VopNet`.

**Those keys are not the UI labels, and that bites.** The render category is `Driver`,
not `ROP`: `hou.ropNodeTypeCategory().name()` returns `'Driver'`, so
`hou.nodeTypeCategories()["ROP"]` raises `KeyError`. Same story for `Sop`/`Object`/`Lop`
versus SOP/OBJ/LOP — and a SOP *node's* `type().category().name()` is `'Sop'`, so any
comparison written `== "SOP"` flags every real SOP as the wrong category (this repo's own
`empty_geo_report` did exactly that until a live run caught it). The accessors differ per
class, and both are easy to get wrong:

| Class | Exists | Does not exist |
| --- | --- | --- |
| `hou.NodeTypeCategory` | `name()` (`Sop`), `label()` (`Geometry`), `typeName()`, `nodeTypes()` | `keyName()`, `internalName()`, `description()` |
| `hou.NodeType` / `OpNodeType` / `SopNodeType` | `name()` (the internal type name, e.g. `grid`), `description()`, `category()` | `internalName()`, `typeName()`, `label()`, `knownTypeNames()`, `supplements()` |

`label()` is the name of the tab in the UI, which is why the render network is `/out`
(category `Driver`, label **`Outputs`**) and Solaris is category `Lop`, label
**`Solaris`**:

| hou key | `label()` | types | hou key | `label()` | types |
| --- | --- | --- | --- | --- | --- |
| `Sop` | Geometry | 1693 | `Shop` | Shaders | 219 |
| `Object` | Objects | 186 | `Top` | Tasks | 183 |
| `Driver` | **Outputs** | 90 | `Chop` | Motion FX | 149 |
| `Lop` | **Solaris** | 219 | `Cop2` | Compositing | 169 |
| `Dop` | Dynamics | 505 | `Cop` | Copernicus | 384 |
| `Vop` | VEX Builder | 1305 | `Data` | Data | 446 |
| `TopNet`/`CopNet`/`ChopNet`/`VopNet` | networks | 2/3/1/17 | `Director`/`Manager` | (empty) | 0/0 |

Type counts and the collisions, read off the build:

The two names that bite, spelled out: `karma` is a `Driver` **and** a `Lop` type,
`filecache` is a `Sop` **and** a `Lop` type, and SOP/LOP share 33 type names — a bare name
lookup can hand you the wrong node class. `troub.node_type_lookup` answers that with
`creatable_here` for the network you name (via `node.childTypeCategory()`); `sop.list_types`
lists only what one network accepts.

Why searching one category lies — `pyro` by category, read off the build:

| Category | Types containing `pyro` | Examples |
| --- | --- | --- |
| Dop | 3 | `pyrosolver`, `pyrosolver::2.0`, `pyrosolver_sparse` |
| Sop | 14 | `pyrosource`, `pyrobakevolume`, `pyropostprocess`, `pyrosolver` |
| Cop | 36 | `pyro_advect`, `pyro_activate` (Copernicus pyro) |
| Vop | 23 | `kma_pyroshader`, `kma_pyropreview` |
| Shop | 3 | `pyro`, `pyro::2.0`, `pyro::3.0` (shader) |
| Data | 71 | `sidefx::recipe::coppyro::fireball` (HDA recipes) |

An `/obj`-only type search reported **0 pyro node types**, which is how an agent
concludes a workflow does not exist. `simulation-tools probe_types` now walks every
category and labels each hit.

## Names that changed since 19.5 (migration traps)

Verified on 22.0.368 by reading parms off live nodes. Each of these failed **silently**
in this repo's own code, because a missing `node.parm(name)` returns `None` and a
`_set()` helper that returns `False` gets ignored:

| 19.5 habit | 22.0.368 reality | What it broke here |
| --- | --- | --- |
| `xform` parm `rotatex` | tuples `t`/`r`/`s`/`p`/`pr`, children `tx..tz`, `rx..rz` | the RBD lean never applied, so a fractured column sat upright and looked like a solver failure |
| `scatter` parm `number` | `npts`; mode menu `generateby` → `bydensity`, `countperprimitive`, `texturespace` | a 20-site fracture scattered at default density: 11,067 prims |
| `scatter` `stdswitcher` | absent | — |
| `hou.Parm.val()` / `hou.ParmTuple.vals()` | `rawValue()`/`eval()`, tuple `eval()` | (not used here; would break any ported script) |
| `boundingBox().minVec()` | `minvec()` | `framed_bounds` vanished behind a bare `except` |

The rule these all teach: **bind a parm, then read the effect back off the
geometry**. `rbd_fall` now returns `knobs` (which parm name it bound, which it could
not), `site_points` (the real point count at the scatter output) and
`bounds_after_energy`, so a dead knob is visible in the payload instead of being a
mystery in the viewport.

## Headless ↔ live parity

`tools/fact_sheet.py` prints every claim on this page as JSON, from whichever
interpreter runs it:

```bash
"<Houdini>/bin/hython" tools/fact_sheet.py --instances > facts_hython.json
# inside a live session (read-only: no nodes created, never saved):
#   m.collect(instances=False) -> write JSON
python tools/fact_sheet.py --diff facts_hython.json facts_live.json
```

`instances=False` is the mode safe to run in someone else's open scene: it creates
nothing, which is provable because the node count is identical before and after
(13,533 → 13,533 on this box). `--instances` adds the real parm lists, and must not be
used in a session you do not own: creating and deleting nodes marks the `.hip` as
having unsaved changes.

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
| attribute lists | `geo.pointAttribs()`, `geo.primAttribs()` (singular **prim**), `geo.vertexAttribs()`, `geo.globalAttribs()`. **`geo.attribs()` and `geo.primsAttribs()` do not exist** - inside a `try/except` they silently return nothing and the report looks like empty geometry. |
| prim **types** matter | `geometry_stats` reports `prim_types` (a `{type_name: count}` map). A FLIP Source SOP holds **1 prim, 1 point** and the prim type is `VDB` — that is a correct source volume, not a broken chain. Verified `flipsource` facts: one input labelled "Geometry to Source From", output = 1 VDB prim + 1 point at the origin. |
| menu accessor | `hou.Parm.menuItems()` **raises `hou.OperationFailed: Cannot get menu items for a non-menu parm`** on a plain parm - it does not return an empty list. Ask the parm template group instead, or wrap the call. |
| `node.geometry()` can be None | an uncooked SOP solver returns no geometry object at all: a fresh `flipsolver` in a source chain gave `geometry() is None` while everything upstream cooked. Treat None as a state ("not stepped yet"), never as "empty geo". |

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
| `grid` | `sizex`, `sizey`, `rows`, `cols`, `tx`/`ty`/`tz`, `rx`/`ry`/`rz`, `orient`, `surftype`, `type` (`poly`, `mesh`, `nurbs`, `bezier`, `points`, `polysoup`, `prim`), `orderu`/`orderv`, `interpu`/`interpv` - **there is NO `sizez` and NO `divisionsx`/`divisionsz`**. A default Grid sits in the **XZ plane at y=0**, so `sizey` is the Z extent (probe: `sizex=4 sizey=2` -> x [-2,2], z [-1,1]). `cols`/`rows` are **point counts**: `cols` subdivides along X, `rows` along Z (probe: `cols=5 rows=3` -> 5 distinct x, 3 distinct z, 8 quads). Default is 10x10 points = 81 quads |
| `box` | `sizex`, `sizey`, `sizez`, **`divsx`/`divsy`/`divsz`**, `dodivs`, `scale`, `tx`/`ty`/`tz`, `rx`/`ry`/`rz`, `type`, `surftype`, `rebar`, `consolidatepts`, `orientedbbox`, `vertexnormals`, `orderrate1-3`, `divrate1-3` - **no `divisionsx`** (`sizex=2 sizey=4 sizez=6` -> bbox (-1,-2,-3)-(1,2,3), 6 prims) |
| `polybevel` (type name `polybevel::3.0`) | width is **`offset`** (plus `offsetedges`, `offsetpoints`, `useoffsetscale`) - **no `bevelamount`, `width` or `size`** |
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

A freshly built RBD setup (`box` -> scatter sites -> `voronoifracture` ->
`xform` lean/lift -> `rbdpack`) cooks with **zero node errors** on 22.0.368, but
with pieces resting upright on the ground plane and no initial motion, nothing
moves: **the graph is correct and static is the physically right answer.** To get
motion, give the pieces energy - a lean (`xform.rx`), a drop (`xform.ty`), or an
impactor. The `simulation-tools` skill does that and reports `not_done` including
"simulation not advanced", because it does not step the solver.

### SOP-level solvers own their simulation (22.0.368)

Instances of the three SOP solvers expose **no dop-related parm whatsoever**
(probed `doppath`, `simroot`, `dopnet`, `outputdop` - all absent). They simulate
themselves and cache through `cacheenabled`, `cachemaxsize`, `checkpoint_*`,
`explicitcache*`. A dopnet beside one is an empty object whose only child is
`file_output`, and any "link" reported to it is fiction - a live run of the FLIP
builder proved exactly that, which is why the builders stopped making dopnets.

Wire the solver's own inputs instead. `node.inputLabels()` on 22.0.368:

| solver | inputs, in index order |
|---|---|
| `pyrosolver` | Sources, Collision Geometry/Volumes |
| `flipsolver` | Sources, Container, Collisions, Boundary Flow |
| `rbdbulletsolver` | Geometry, Constraint Geometry, Proxy Geometry, Collision Geometry, Guide Sim |

`simulation-tools` returns these as `solver_wiring`, together with `dop_parms: []`
so a caller can see the absence rather than take the doc's word for it. It wires
input 0 only and lists the rest in `not_done`.

## USD reality check

A `usd` ROP and a SOP Import LOP write **two** files, and the one you asked for is
not where the geometry lives:

- The LOP authors its own layer through `savepath`, whose default is the template
  `$HIP/usd/$OS.usd` (`enable_savepath` gates it).
- The ROP writes `lopoutput` as a thin layer whose content is a
  `subLayers = [@…/usd/<lopname>.usd@]` entry. Verified: 229–237 bytes.
- Opening that file with pxr still composes the geometry correctly — `Traverse()`
  returned the `Xform` **and** `/…/mesh_0` `Mesh` with all 16 points of a 4×4 grid —
  so the export works, but it is not portable and byte count proves nothing.
- `flattensoplayers` on the ROP was **verified to change nothing** for this setup, and
  `sopimport asreference`/`flattenfilelayers`/`strippostlayers` made no difference
  either. Do not sell a flatten flag as a fix.

What actually makes an export travel: pin the LOP layer next to the output
(`enable_savepath=1`, `savepath=<output dir>/<container>_sop.usd`). The subLayer entry
then resolves as `./<container>_sop.usd` inside the output folder, and the Mesh still
carries its points. `usd-pipeline export` does this by default
(`keep_layers_together=true`) and reports `stage.self_contained`.

The proof recipe a skill should return, not a byte count:

```python
from pxr import Usd
stage = Usd.Stage.Open(path)                      # composes subLayers
prims = list(stage.Traverse())                    # Stage has Traverse/TraverseAll only
typed = [(str(p.GetPath()), str(p.GetTypeName())) # Prim.GetTypeName(), not GetType()
         for p in prims]
points = [len(p.GetAttribute("points").Get() or []) for p in prims
          if p.GetAttribute("points") is not None]
str(prims[0].GetPath())                           # Sdf.Path: pathString/str(), no .pathname
```

`export` compares the points it reads back against the SOP source point count and
returns `geometry_points_in_stage` / `geometry_verified`; when companion layers resolve
outside the output folder it returns `portability_note` naming them. Locked by
`tests/test_tier_b.py::TestUsdExportLandsGeometry`.
