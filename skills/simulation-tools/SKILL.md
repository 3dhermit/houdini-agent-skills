---
name: simulation-tools
description: Use when standing up Pyro/RBD/FLIP setups, frame ranges and File Cache paths in Houdini - with version-discovered node names and honest "not simulated yet" reporting.
houdini_versions: 19.5 - 22.0
verified_on: 22.0.368
---

# Simulation tools

Builds the **container, the source and the energy**, then stops and tells you
what it did not do. It never claims a sim ran.

## When to trigger
"set up a pyro sim", "fracture this column and let it fall", "cache the sim to disk".

## Actions
| action | what it builds |
|---|---|
| `pyro` | source chain (sphere -> volume -> density wrangle) + Pyro object + frame range |
| `rbd_fall` | box -> Voronoi fracture -> xform (lean + lift) + RBD object + frame range |
| `flip` | sphere -> scatter -> copytopoints + FLIP object |
| `cache_sop` | File Cache SOP below any SOP, with path/frame parms bound by discovery |
| `set_frame_range` | global timeline range |
| `probe_types` | which sim object types exist on this exact build |

```bash
hython -m houdini_skills.cli simulation-tools rbd_fall \
  --json '{"container":"asm_rbd","frames":[1,100],"voronoi_points":60,"lean_degrees":16}'
```

## The physics rule this skill exists to enforce
Fractured pieces that start **upright, resting on the ground, with no velocity**
will sit still forever - and that is *correct*. If a collapse "did not work",
check the sourcing before blaming the solver. `rbd_fall` therefore always
supplies potential energy (`lean_degrees`, `drop_distance`) and reports it.

## Parm names moved since 19.5 — bind, then read the effect back

Verified on 22.0.368 by reading parms off live nodes:

| 19.5 habit | 22.0.368 | Effect of the stale name |
|---|---|---|
| `xform` `rotatex` | tuples `t`/`r`/`s`, children `rx`/`ty` | the lean never applied; the column sat upright and looked like a solver bug |
| `scatter` `number` | `npts` (mode menu `generateby` → `bydensity`, `countperprimitive`, `texturespace`) | fracture sites scattered at default density: 11,067 prims instead of 20 |
| `scatter` `stdswitcher` | absent | — |

`node.parm("rotatex")` returns `None`, so a `_set()` that returns `False` failed
**silently**. That is why `rbd_fall` now answers three questions in its payload:

- `knobs` — which parm name each intent actually bound, and `unbound` if none matched;
- `site_points` — the real point count at the scatter output (what the fracture
  actually sees), next to `site_count_requested`;
- `bounds_after_energy` — framed bounds after the lean/lift, so the energy is proven
  by geometry, not by a `set()` call.

## Node names are discovered, not assumed
`_resolve_object_type` tries candidate names (`pyro`, `pyro::2.0`, ...) against
`/obj`'s real type list and fails with the names that *do* contain the keyword.
That converts "wrong node type" from a silent cook failure into a readable error.

`probe_types` searches **every** category via `hou.nodeTypeCategories()`
(`Chop, ChopNet, Cop, Cop2, CopNet, Data, Director, Dop, Driver, Lop, Manager,
Object, Shop, Sop, Top, TopNet, Vop, VopNet`) and labels each hit. Searching only
`/obj` said pyro does not exist; on 22.0.368 there are 3 Dop, 14 Sop, 36 Cop,
23 Vop, 3 Shop and 71 Data types containing `pyro`.

## Undo what an action made
Builder results include `created_nodes` - every path the action created, including
ones outside the container you named (the `/obj/<container>_dopnet` object, the
`/out` ROP, the LOP in `/stage`). Destroy that list, deepest path first, to leave a
shared scene exactly as it was. Prove it by node count:
`len(hou.node("/").allSubChildren())` before == after.

## Explicit non-claims (v1)
- Does **not** advance the timeline or step a solver. Drive frames from the GUI
  or the MCP bridge; `not_done` in every result says so.
- Does **not** wire the source object into the DOP graph's source input.
- A prim count is not a piece count. `source_prims` is primitives;
  `source_pieces_by_name` counts distinct `@name` values (or `None`, which means
  "nothing declares pieces yet" — not "one piece").
