---
name: sop-automation
description: Use when building or inspecting Houdini SOP networks - create nodes, wire chains, set parms, read geometry counts. Version-gated to Houdini 19.5+.
houdini_versions: 19.5 - 22.0
verified_on: 22.0.368
---

# SOP automation

One shape for every SOP edit: validate JSON in, call the shared `hou` wrapper,
structured JSON out. You never touch `hou` directly from the agent side.

## When to trigger
"create a grid and bevel it", "wire these nodes", "set parms on /obj/geo/sphere",
"how many pieces is this?", "what is in this network".

## Actions
| action | payload | returns |
|---|---|---|
| `grid_bevel` | `container, size, divisions, bevel_width` | created paths, which parm names bound, cook status, geometry stats |
| `create_node` | `node_type, name, parent` | node ref |
| `chain` | `types[], names[], parent` | nodes + wiring list |
| `set_parms` | `path, parms{}` | `set`/`kept`/`drift`, read back from hou (unknown parm name -> error with suggestions) |
| `read_parms` | `path, names[]` | raw parm values |
| `find` | `pattern, limit` | matching node refs |
| `geo_stats` | `path` | prims, **prim types**, points, packed count, pieces by `@name`, bounds |
| `delete_node` | `path, confirm=path` | deletion receipt (confirm is mandatory) |
| `list_types` | `parent, contains` | valid node type names for that network |

## How to call
```bash
hython -m houdini_skills.cli sop-automation grid_bevel \
  --json '{"container":"asm_demo","size":[4,0,4],"divisions":12,"bevel_width":0.08}'
```

## Rules that keep you out of trouble
- **Never guess parm names.** On 22.0.368 the Grid SOP is `sizex`/`sizey` +
  `rows`/`cols` (point counts) — it has **no `sizez` and no `divisionsx`**;
  `sizex`/`sizez`/`divsx` is the **Box** SOP. A knob the skill could not bind is
  reported under `unresolved_parms`, which means the value was never set.
  Unknown names raise `unknown_parm` with real suggestions - read them and retry.
- **Count prims and count their kinds.** `geo_stats.prim_types` is a
  `{type_name: count}` map. A FLIP Source SOP with 2000 seeds upstream reports
  `prim_count: 1` because that prim is a **VDB volume** — the chain is fine, the
  count was the wrong question.
- **Piece count is not prim count.** Use `geo_stats` (`piece_count_by_name`),
  because packed/fractured geometry reports one per piece, not one per primitive.
- **Set the display flag** on the chain output (`grid_bevel` does this) or an
  Object Merge downstream pulls the wrong geometry.
- `find` matches **node paths with fnmatch**, not names: use `/obj/asm*/*` or
  `/out/*usd*`. A bare substring like `asm_l5` returns zero matches, which is correct
  behaviour, not an empty scene.
- **A successful set is not proof.** `set_parms` reads every parm back and reports
  `kept` plus `drift`, because hou clamps silently: `grid.rows = -5` and `= 0` both end
  up `1`, `karma.trange = 99` lands on `'stage'`, and an int parm **raises** on a
  fractional value (`rows = 2.7`) instead of rounding. Trust `kept`, not the request.
- Deleting needs `confirm` equal to the path. There is no bulk delete in v1.

## Undo what an action made
`chain` and `grid_bevel` return `created_nodes` — every path they made, container
included. Destroy that list, deepest path first, and prove the scene is clean with
`len(hou.node("/").allSubChildren())` before == after.

## Failure modes
| error kind | what it means | do this |
|---|---|---|
| `unknown_node_type` | wrong category or renamed type | use `list_types` |
| `unknown_parm` | renamed/misremembered parm | take the suggestion |
| `cook_errors` | node is red | hand off to the `troubleshooting` skill |
| `unsupported_version` | below 19.5 floor | stop, tell the user |
