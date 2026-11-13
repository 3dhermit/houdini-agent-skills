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
| `export` | `path, output, container, frames, unpack` | absolute path, byte count, which parms bound |
| `verify` | `output` | exists, bytes, stage open attempt via `pxr` if available |
| `sopimport` | `stage, path, prim_path` | the LOP it created |

```bash
hython -m houdini_skills.cli usd-pipeline export \
  --json '{"path":"/obj/asm_sop/bevel1","output":"C:/temp/asm.usda","container":"asm_usd"}'
```

## Verified parm names on 22.0.368
`loppath`, `lopoutput`, `trange` (`off, normal, on, stage`), `f1`/`f2`,
`mkpath`, `execute`, and SOP Import's `soppath`.

## Failure modes
| error kind | meaning |
|---|---|
| `export_produced_no_file` | ROP ran, nothing written - almost always a `loppath` pointing at an empty/uncookable LOP |
| `parm_not_found` | that parm name is not on this build; the error lists real candidates |
| `node_type_unavailable` | no USD ROP type in this install |

## Not proven in v1
Multi-file layer stacks, animation ranges beyond `f1/f2`, and MaterialX/Cook&Tokens
round-tripping. `verify` opens the stage with `pxr` **only when Houdini's Python
ships pxr**; otherwise it reports bytes on disk and says pxr was unavailable.
