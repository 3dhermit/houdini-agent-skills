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
| `diagnose_node` | yes | real errors/warnings, upstream inputs, matched KB causes |
| `empty_geo_report` | yes | walks upstream until it finds the first non-empty SOP |
| `node_type_lookup` | yes | exact + close type names across SOP/OBJ/DOP/LOP/ROP/COP |
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

Those four came from real tracebacks while building this repo, not from a list.
