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

### Menu parms: `rawValue()` is the label, `eval()` is the index (22.0.368)

The same menu answers two questions, and mixing them up manufactures drift:

| parm | after | `rawValue()` | `eval()` |
| --- | --- | --- | --- |
| `karma.trange` | `set("normal")` | `'normal'` | `1` |
| `volume.type` | `set("float")` | `'float'` | `0` |
| `volume.rank` | `set("scalar")` | `'scalar'` | `0` |

This is where a helper gets punished for its own naming: v1.0.0's
`_set_and_read()` put `eval()` into a key called `kept`, so a correctly-set
`type` menu reported `kept: 0` against a request of `'float'`. The contract is now
identical in both places — `kept` = `rawValue()`, `eval` = `eval()`.

Compare like for like: a label against `rawValue()`, an index against `eval()`, or
resolve the selection by indexing `menuItems()` with the index. `hou_api._same_value`
takes `menu_items` as that fallback and drift entries carry the item list.
`menuItems()` raises on most non-menu parms, which is how the wrapper usually tells the
two apart - but see the caveat below: on `volumewrangle.snippet` it returns default text
instead of raising, so menu-ness comes from the parm template when it matters.

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

## Volume SOP field parms (22.0.368)

A `volume` SOP makes **one** field. Read off a fresh node (48 parms):

| parm | value / menu |
|---|---|
| `name` | `''` - the field name; set it to `density` for pyro fuel |
| `type` | menu `('float','int')` |
| `rank` | menu `('scalar','vector','matrix')` |
| `components` | `'1'` |
| `initialval1` | `'0'` - the fill value (fuel amount) |
| `divsize` | `'0.1'` - one parm; `parmTuple("divsize")` has size 1 |
| `divsx` / `divsy` / `divsz` | `'10'` each |
| `dimensionsource1`, `stdswitcher1` | `'0'` - choose which resolution set is live |

There is **no `voxelsize`** and **no `gridtype`** parm. The v1.0.0 pyro recipe wrote
`voxelsize` behind a `hasattr` guard: the write did nothing and the payload said the
source was configured.

`hou.Volume` prims then expose no way to check the result: `fieldNames()`,
`volumePrimitive()` and `volumefields()` all raise AttributeError, and the only
name on the prim matching field/vdb is `isHeightField`. `hou.Geometry` also has no
`attribs()`. Field configuration is therefore proven by parm read-back, and the
pyro payload carries a `not_verified` list saying the voxel data was never read.

## Pyro Source SOP on 22.0.368

`pyrosource` is a particle source, not a fuel author:

| fact | value |
|---|---|
| `snippet` parm | **absent** (`src.parm("snippet") is None`) - a wrangle injected there is silently skipped |
| `mode` menu | `('0','1','2')`, default `'1'` |
| `densityattrib` | default `'scattering_density'` |
| other parms | `radius` `'0.1'`, `particlescale` `'2'` |
| `inputLabels()` | `['Geometry to Source From', 'Rest Geometry']` |
| output fed a Volume SOP | 0 prims / 1 point carrying `P`, `pscale` |

The SOP `pyrosolver`'s input 0 is labelled `Sources` and takes the density-carrying
volume directly, which is what the builder wires now.

### Current frame vs frame range (22.0.368)

| question | accessor | verified |
| --- | --- | --- |
| current frame | `hou.frame()` → `1.0`, and `hou.time()` → `0.0` (seconds) | module-level, not on a node |
| set it | `hou.setFrame(frame)` / `hou.setTime(time)` — `setFrame()` with no argument raises `TypeError: missing 1 required positional argument: 'frame'` | |
| frame range | `hou.playbar.frameRange()` → `hou.Vector2`; `hou.playbar.setFrameRange(start, end)` | |
| current frame on the playbar | **absent** — `hou.playbar.frame()` raises `AttributeError: 'playbar' object has no attribute 'frame'`; the playbar offers `timeRange()`, `timelineRange()`, `frameRange()`, `frameIncrement()`, `frameBookmark()` | |
| current frame on the context node | **absent** — `hou.pwd().frame()` raises `AttributeError: 'OpNode' object has no attribute 'frame'`. A live session's `hou.pwd()` is whatever node the UI is sitting on (it was `/obj/rbd/rbd_solver1` here), so it is not a safe handle either | |

Conversion helpers that do exist: `hou.frameToTime`, `hou.timeToFrame`,
`hou.frameTolerance`, `hou.timeTolerance`, `hou.intFrame`.

## Render ROP reality (22.0.368)

Read off a freshly created `karma` ROP in `/out` (405 parms):

| question | answer |
|---|---|
| A parm naming the geometry/LOP to render? | **No.** No `soppath`, no `lophotoflame`; the only parms containing "path" are `mkpath` (make output dirs) and `pathtracedsamples`. What renders comes from the LOP context's displayed output plus the ROP's object scope. |
| Camera | `camera` parm, default `'/obj/cam1'`; also `dicingcamera`, `usedicingcamera`, `override_camerares` |
| Object scope | `objects` (empty), `candobjects` (`'*'`), `excludeobjects` (empty) |
| Stage-driven settings | `samplesfromstage` = `'on'`, `trange` menu `('off','normal','on','stage')` |
| Frame range | `f1`/`f2` arrive **channelled**: defaults hold `'$FSTART'` / `'$FEND'`. `set(24)` raised nothing and changed nothing (rawValue stayed `'$FEND'`, eval 40.0). `deleteAllKeyframes()` then `set(31)` gave raw `'31'` / eval 31.0; `setExpression('$FEND')` gave `'$FEND'` / 99.0 after the playbar moved. `parmTuple("f")` is size 3 (f1, f2, increment) and `t.set((3, 27))` raises `hou.InvalidSize`. `hou.Parm` has no `channel()` and no `isChannable()` |
| XPU vs CPU | no parm: no `renderer`, `rendermethod`, `schedule` or any `*xpu*` parm, and no `karma_xpu` node type |

`rendering.configure` therefore exposes `camera` (verified) and **not** a scene-source
`path` knob - that key shipped in v1.0.0, was never read by any code, and is gone.

## Pyro Solver SOP parm reality (22.0.368)

A fresh `pyrosolver` SOP node reports **670 parms**, and among them there is **no**
resolution, voxel, upres or cache-path parm: `voxel` and `upres` never occur, and no
parm starts with `sim`, `geo` or `data`. Cache-related names are only `cacheenabled`,
`cachemaxsize`, `checkpoint_basedir` (default `'$HIP/geo'`), `checkpoint_openbasedir`,
`checkpoint_version`, `checkpoint_explicitcachensteps`,
`checkpoint_explicitcachespacing`.

So `pyro`'s v1.0.0 knobs `resolution_scale` and `cache_dir` had nothing to bind to.
They are removed from the schema; source resolution goes where it is real - the
Volume SOP's `voxelsize`, which the builder binds and reads back.

## Geometry

| fact | detail |
|---|---|
| primitives | `geo.prims()`, `hou.PolySphere`, `hou.PackedPrim` exist. |
| attribs on a prim | `prim.findAttrib(name)` -> `hou.PrimAttribute` or `None`, then `prim.attribValue(name)`. **`hou.Prim.findAttribValue()` does not exist** (that is the `hou.Geometry` API). |
| wrangle context | a detail wrangle on a raw grid sees **1 prim, 0 points** in `s@numprims`/`n@numpoints` style reads for packed contexts - do not write stats tests that assume both. |
| attribute lists | `geo.pointAttribs()`, `geo.primAttribs()` (singular **prim**), `geo.vertexAttribs()`, `geo.globalAttribs()`. **`geo.attribs()` and `geo.primsAttribs()` do not exist** - inside a `try/except` they silently return nothing and the report looks like empty geometry. |
| prim **types** matter | `geometry_stats` reports `prim_types` (a `{type_name: count}` map). A FLIP Source SOP holds **1 prim, 1 point** and the prim type is `VDB` — that is a correct source volume, not a broken chain. Verified `flipsource` facts: one input labelled "Geometry to Source From", output = 1 VDB prim + 1 point at the origin. |
| menu accessor | `hou.Parm.menuItems()` **usually raises `hou.OperationFailed: Cannot get menu items for a non-menu parm`** on a plain parm - but do not treat a quiet call as proof of a menu. On 22.0.368 `volumewrangle.parm("snippet").menuItems()` returned that parm's **default snippet text** as a one-item list instead of raising. Decide menu-ness from `parmTemplateGroup().find(name)` (a `MenuParmTemplate`), not from whether `menuItems()` threw. |
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
| `sopimport` (LOP) | `soppath`; `primpath` (label 'Primitive Path', default `'/$OS'`), `refprimpath`, `pathprefix` + `enable_pathprefix`, `savepath`, `enable_savepath`, `asreference`, `bindmaterials`, and ~90 handling parms (probed list). **`primpath` is bindable but does not move the authored prims on 22.0.368** - see the LOP stage section below |

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

## LOP stage truth (22.0.368)

| fact | detail |
|---|---|
| stage accessor | `stage()`, `editableStage()`, `uneditableStage()`, `stagePrimStats()` and `isMostRecentStageLock()` exist on a **LOP node**. `hou.LopNetwork` has **none** of them - `dir(lopnet)` matched no 'stage' name at all, and `displayLop()` does not exist on it either. Read the stage off a LOP *node* (the SOP Import node itself, or a downstream `null` LOP). |
| prim census | `node.stagePrimStats()` returns keys like `Mesh:Total`, `Xform:Total` - a cheap structured answer to "did the geometry actually land?" |
| LOP container type | `lopnet`. There is **no `lop` node type**: `createNode("lop")` raises `hou.OperationFailed: Invalid node type name`. |
| `primpath` trap | A SOP Import LOP with `primpath` set to `/world` reads back `/world`, yet `stage().Traverse()` still yields `/HoudiniLayerInfo`, `/<node>`, `/<node>/mesh_0` - checked on the importing node **and** one LOP downstream through a `null` LOP. Setting it to `''` changed nothing either. So `primpath` is not "where the prims go" in the configuration the skill builds, and a payload that echoes it back as if it were would be lying. `usd-pipeline.sopimport` now returns `prim_path_requested`, `prim_path_kept`, `stage_prim_paths`, `stage_prim_stats`, and a note when the value and the stage disagree. |
| parm labels | `hou.Parm` has **no `label()`**. Labels come from `node.parmTemplateGroup().find(name).label()` - which is how `primpath` was identified as 'Primitive Path'. |
| LOP types present | `sopimport`, `null`, `sublayer`, `componentoutput`, `componentgeometry`, `stagemanager`, `sceneimport`, `graftstages`, `configurestage`. |

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
