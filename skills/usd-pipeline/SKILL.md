---
name: usd-pipeline
description: Use when exporting Houdini geometry to USD or checking whether a USD file really landed on disk - SOP Import LOP plus USD Output ROP, with on-disk proof.
houdini_versions: 19.5 - 22.0
verified_on: 22.0.368
---

# USD pipeline

The rule this skill enforces: **an export is only done when the file exists on
disk with a plausible size.** "The ROP did not throw" is not evidence.

## Shape of a Houdini USD export
The USD Output ROP does **not** take a SOP. It takes a **LOP** (`loppath`) and
writes to `lopoutput` (default `$HIP/usd/$OS.usd`). So:

```
SOP (/obj/geo/bevel1)  ->  /stage/sopimport  ->  /out/usd ROP  ->  .usda on disk
```

Creating the SOP Import LOP is the part agents most often skip, then wonder why
the USD is empty.

## Actions
| action | payload | returns |
|---|---|---|
| `export` | `path, output, container, frames, unpack, keep_layers_together, flatten_sop_layers` | absolute path, bytes, `stage` (prims, types, point counts, `self_contained`), `geometry_verified`, `knobs`, `portability_note` |
| `verify` | `output` | exists, bytes, and a pxr read of the stage: prim paths/types, point counts, resolved sublayer files |
| `sopimport` | `stage, path, prim_path` | the LOP it created |

```bash
hython -m houdini_skills.cli usd-pipeline export \
  --json '{"path":"/obj/asm_sop/bevel1","output":"C:/temp/asm.usda","container":"asm_usd"}'
```

## Two files, and the geometry is not in the one you named (22.0.368)

The SOP Import LOP **authors its own layer** through `savepath` (default template
`$HIP/usd/$OS.usd`, gated by `enable_savepath`). The USD Output ROP then writes
`lopoutput` as a ~230-byte layer containing only a `subLayers` entry pointing at it.

So a successful export is a *layer stack*, and the file you named is the pointer:

- Opening it with `pxr` composes fine — `Traverse()` returned the `Xform` and the
  `Mesh` with all 16 points of a 4×4 grid. The export was never "empty".
- Moving the one file loses the model, because the default sublayer path is absolute.
- `flattensoplayers` (ROP), `asreference`, `flattenfilelayers` and `strippostlayers`
  (SOP Import) were all probed and changed **nothing observable** here. Never claim a
  flatten flag inlined the geometry.

What `export` therefore does by default (`keep_layers_together=true`): sets
`enable_savepath=1` and `savepath=<output dir>/<container>_sop.usd`, so both files
share a folder and the subLayer entry becomes `./<container>_sop.usd`. It reports
`stage.self_contained` and warns by name if a companion resolves outside the folder.

## Proof, not byte counts

`verify`/`export` open the output with `pxr` (present in hython 22.0.368) and count
prims that carry a `points` attribute, compared against the SOP source point count —
`geometry_points_in_stage` / `geometry_verified`. Spellings that are easy to guess
wrong and are pinned by tests: `Usd.Stage.Traverse()`/`TraverseAll()` (no
`TraverseAllPrims`), `prim.GetTypeName()` (no `GetType()`), `str(prim.GetPath())`
(no `.pathname`).

## Verified parm names on 22.0.368
`loppath`, `lopoutput`, `trange` (`off, normal, on, stage`), `f1`/`f2`,
`mkpath`, `execute`, `flattensoplayers`; SOP Import: `soppath`, `primpath`,
`enable_savepath`, `savepath`.

## Undo what an action made
`export` and `sopimport` return `created_nodes` — the `/out` ROP **and** the LOP (in
`/stage`, outside the container you named). Destroy that list, deepest path first,
to leave a shared scene as you found it; prove it with
`len(hou.node("/").allSubChildren())` before == after. Deleting the .usd files is a
separate decision — ask before removing output.

## Failure modes
| error kind | meaning |
|---|---|
| `export_produced_no_file` | ROP ran, nothing written - almost always a `loppath` pointing at an empty/uncookable LOP |
| `parm_not_found` | that parm name is not on this build; the error lists real candidates |
| `node_type_unavailable` | no USD ROP type in this install |

## Not proven in v1
Multi-file layer stacks beyond the LOP + ROP pair, animation ranges beyond `f1/f2`,
and MaterialX/Cook&Tokens round-tripping. `stage` inspection reports up to 25 prims.
