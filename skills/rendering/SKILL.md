---
name: rendering
description: Use when configuring a Karma or mantra Render Output ROP in Houdini - engine choice, resolution, frame range, output path. Configures; never renders on its own.
houdini_versions: 20.0 - 22.0 for Karma, 19.5 for mantra
verified_on: 22.0.368
---

# Rendering

Configures a Render Output ROP and stops there. Rendering costs time, a licence
and disk; that stays a human decision.

## The version conflict you must know about
This repo's v1 floor is **Houdini 19.5**, but **Karma does not exist before
Houdini 20**. On 19.5 this skill raises `engine_unavailable` and points at
mantra rather than creating a node type that isn't there.

## Actions
| action | payload |
|---|---|
| `configure` | `engine, resolution, frames, output_path, samples, aovs` |
| `inspect` | `path` |
| `list_engines` | `{}` |

```bash
hython -m houdini_skills.cli rendering configure \
  --json '{"container":"asm_karma","engine":"karma_cpu","resolution":[1280,720],"frames":[1,40],"output_path":"$HIP/out.$F4.exr","samples":128}'
```

## Parm names verified on 22.0.368 (see docs/verified-api.md)
| knob | real Karma ROP parm |
|---|---|
| resolution | `resolutionx` / `resolutiony` |
| samples | `samplesperpixel` (**not** `samples`) |
| output file | `picture` (**not** `outputpath`) |
| frame range | `trange` (menu: `off, normal, on, stage`) + `f1` / `f2` |
| execute | `execute` **and** `render` both exist on the Karma ROP; the skill's `next_step` points at `execute`, same name the USD ROP uses |

Anything the skill could not bind is listed under `unresolved` in the result.
Read that list; do not assume the render is configured.

## The drift runs both directions (verified on 22.0.368)

- **`mantra` is not registered on 22.0.368** at all (90 ROP types scanned). So the
  19.5 mantra fallback is not a safe "works everywhere" answer - asking for
  `engine="mantra"` here raises `engine_unavailable` with the real list.
- **No Karma parm selects CPU vs XPU.** There is no `karma_xpu` node type and no
  `renderer`/`rendermethod`/`schedule`/`xpu` parm on the ROP. `karma_xpu` is
  accepted so the call does not fail, and the result carries `xpu_note` saying
  the choice is a licence/render-setting concern, not a ROP knob.

## AOVs are deliberately not authored in v1
The Karma AOV list is a parm-group UI, not one parm. Guessing it produces
renders that silently miss passes, so `configure` records the request and tells
you it skipped it. Ask for explicit AOV setup and do it node-by-node.

## What `list_engines` is allowed to claim
`engines.karma_xpu` can be **true while no `karma_xpu` type exists**, because the
candidate list fell back to plain `karma`. That is why the result also carries:

- `resolved_types` — which node type each engine name actually resolved to
  (`karma_xpu -> karma` on 22.0.368);
- `exact_type_registered` — whether that exact name is a registered ROP type
  (`false` for both `karma_cpu` and `karma_xpu` here);
- `render_node_types_matching` + `note` — what the build really has.

Never report "XPU available" from the boolean alone.

## Undo what an action made
`configure` returns `created_nodes` (the `/out` ROP). Destroy it, deepest path
first, to leave a shared scene as it was; prove it with
`len(hou.node("/").allSubChildren())` before == after.

## Non-claims
`rendered: false` is always in the result. Nothing was drawn, no licence was
consumed, no file was written.
