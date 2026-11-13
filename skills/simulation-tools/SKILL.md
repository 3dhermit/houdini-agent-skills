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

## Node names are discovered, not assumed
`_resolve_object_type` tries candidate names (`pyro`, `pyro::2.0`, ...) against
`/obj`'s real type list and fails with the names that *do* contain the keyword.
That converts "wrong node type" from a silent cook failure into a readable error.

## Explicit non-claims (v1)
- Does **not** advance the timeline or step a solver. Drive frames from the GUI
  or the MCP bridge; `not_done` in every result says so.
- Does **not** wire the source object into the DOP graph's source input.
- `piece_count_by_name` is the number of pieces; do not read `prim_count` as pieces.
