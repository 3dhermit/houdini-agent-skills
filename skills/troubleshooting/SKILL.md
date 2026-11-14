---
name: troubleshooting
description: Use when a Houdini node is red, geometry is empty, a node type or parm name is refused, or a hou call throws - diagnose against the running build with a tiered knowledge base.
houdini_versions: 19.5 - 22.0
verified_on: 22.0.368
---

# Troubleshooting

Diagnose from evidence: read the node's real errors, ask the running build what
its types and parms are called, then match against the knowledge base. Never
guess at a red node.

## When to trigger
"This node is red", "why is my geo empty", "invalid node type", "hou has no
attribute X", "the export ran but nothing happened".

## Actions
| action | needs Houdini? | what it returns |
|---|---|---|
| `hou_lookup` | no (better with) | does that hou name exist, what are the close matches, is it a known confusion |
| `explain_error` | no | KB entries matching an error string, each flagged `verified` or not |
| `diagnose_node` | yes | real errors/warnings, matched KB causes, and per input: label, type and **cooked prim count** (input mute state is not readable through hou on 22.0.368) |
| `empty_geo_report` | yes | cooks each input, walks upstream to the last non-empty SOP, and lists what it **cannot** check in `not_checked` |
| `node_type_lookup` | yes | exact + close type names across **all 18 categories**, plus `creatable_here` for the network you named — `karma` is a `Driver` type *and* a LOP type, and only one of them is valid in `/out` |
| `version_report` | yes | version, licence mode, type counts, frame range |

```bash
python  -m houdini_skills.cli troubleshooting hou_lookup --json '{"name":"parmNames"}'
hython  -m houdini_skills.cli troubleshooting empty_geo_report --json '{"path":"/obj/geo/output"}'
```

## The honesty rule
Every KB entry carries `verified`. `verified: true` means **this repo reproduced
it on Houdini 22.0.368 and a test covers it**. `verified: false` means it is
sound practice we have not reproduced here - present it to the user that way.

## Confusions this repo actually hit on 22.0.368
| you wrote | reality |
|---|---|
| `hou.version()` / `hou.versionString()` | `hou.applicationVersion()` / `applicationVersionString()` |
| `node.parmNames()` | `node.parms()` / `node.parmTuples()` / `node.allParms()` |
| `node.parmTemplate('class')` | `node.parmTemplateGroup().find('class')` |
| `parm.parmType()` | not exposed; use `rawValue()` / `eval()` + `menuItems()` |
| `hou.SopNode.inputState(i)` / `inputOutputState(i)` / `inputIsMuted()` / `inputType(i)` | none exist on 22.0.368 — cook the input and count prims instead (`inputLabels()` is real) |
| `hou.NodeConnector` | renamed `hou.NodeConnection` |
| `node.type().category().name() == "SOP"` | hou spells it **`Sop`** — compare case-insensitively or every SOP is "wrong category" |
| `hou.nodeTypeCategories()["ROP"]` | the render category is keyed **`Driver`** (its `label()` is "Outputs"); `Lop` labels as "Solaris" |
| `node_type.label()` / `internalName()` / `knownTypeNames()` | NodeType has only `name()` + `description()`; ask a network what it accepts with `node.childTypeCategory()` |

Every row came from a real traceback or a live probe on 22.0.368, not from a list.
