---
name: vex-helper
description: Use when writing or checking Houdini VEX - generate a tested template, static-lint without Houdini, then compile-check for real by cooking an Attribute Wrangle.
houdini_versions: 19.5 - 22.0
verified_on: 22.0.368
---

# VEX helper

A snippet is not "valid VEX" because a language model says so. It is valid when
Houdini's own compiler accepts it. This skill gives you both tiers.

## When to trigger
"write VEX that scatters by slope", "displace points with noise", "is this
snippet correct", "density falloff for my pyro source".

## Actions
| action | needs Houdini? | what it does |
|---|---|---|
| `list_templates` | no | shows intents + prerequisites + which node type each template belongs in |
| `template` | no | returns code, `node_type`, wrangle class, knob overrides applied, binding note |
| `lint` | no | pure-Python check: unbalanced delimiters, C types VEX lacks, `ch()` refs, variable brace initialisers |
| `validate` | **yes (hython)** | cooks the snippet in a real node of the declared type (`attribwrangle` over a grid, or `volumewrangle` over a real density volume), returns Houdini's compile errors and what the cook produced |
| `explain` | no | structural note per code line; flags calls outside the verified-call list |

```bash
# CI-safe (no Houdini):
python -m houdini_skills.cli vex-helper lint --json '{"code":"float x = 1;"}'

# Real compile check:
hython -m houdini_skills.cli vex-helper validate --json '{"template":"curl_noise_velocity"}'
```

## Templates (verified against 22.0.368)
| template | node type | class | needs |
|---|---|---|---|
| `color_by_attribute` | `attribwrangle` | `point` | any float point attribute named in attrib_name |
| `curl_noise_velocity` | `attribwrangle` | `point` |  |
| `distance_falloff` | `attribwrangle` | `point` |  |
| `point_displacement_noise` | `attribwrangle` | `point` | @N on the input geometry |
| `random_packed_scale` | `attribwrangle` | `prim` | compiles on any primitive input; feed f@piece_scale into a Pack/Transform for packed prims rather than writing @scale directly |
| `slope_mask` | `attribwrangle` | `point` | @N |
| `volume_density_falloff` | `volumewrangle` | `None` | a volume with a float field named density |

**Node type and run class are part of the claim.** `volume_density_falloff` belongs in a
`volumewrangle` - parms `snippet`, `autobind`, `bindeach`, `bindings`, `exportlist`,
`group`, and **no** `class` parm; input 0 is labelled 'Volumes to Process with Wrangle'.

Cooked on 22.0.368 over a Volume SOP field named `density`, in four shapes, **all four
reported 0 errors** - only the placement changed:

| node / class | what appeared |
|---|---|
| `volumewrangle` | nothing added; the VDB field itself is what changes |
| Attribute Wrangle, class `point` | point attribute `density` |
| Attribute Wrangle, class `prim` | prim attribute `density` |
| Attribute Wrangle, class `detail` | detail attribute `density` |

So an error count of zero proves nothing, and "an attribute appeared" does not mean the
volume was edited. `validate` builds the node the template declares; `list_templates` and
`template` carry `node_type` so a wrong pick cannot stay silent.

**Snapshot before you destroy.** A payload that reads node state after its container was
destroyed raises `hou.ObjectWasDeleted` - which looks exactly like a cook failure. Every
template in this repo once "failed" that way despite cooking fine.

## Rules
- `lint` clean **does not mean valid**. Only `validate` (or a cook in the GUI) proves it. Report the tier you used.
- Templates inline constants instead of `ch("...")`, because `ch()` silently reads 0 when the parm does not exist - a classic silent failure.
- Wrangle **class** matters: `@primnum` in a point wrangle is not what you wanted. `validate` resolves the class menu from the running build rather than hardcoding a menu index.
- Common LLM slips the lint catches: `double`/`long`/`unsigned` (not VEX types), `#include`, unbalanced braces from truncation.

## Failure modes
| error kind | meaning |
|---|---|
| `vex_invalid` | Houdini refused to compile; `details.errors` carries the real text with line numbers |
| `unknown_template` | typo; the error lists the real template names |
| `parm_choice_unavailable` | that wrangle class does not exist on this build |
