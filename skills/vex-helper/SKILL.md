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
| `list_templates` | no | shows intents + prerequisites |
| `template` | no | returns code, wrangle class, knob overrides applied |
| `lint` | no | pure-Python check: unbalanced delimiters, C types VEX lacks, `ch()` refs to parms that may not exist |
| `validate` | **yes (hython)** | grid -> Attribute Wrangle with the snippet, cook, return Houdini's real compile errors |
| `explain` | no | line-by-line reading of the template |

```bash
# CI-safe (no Houdini):
python -m houdini_skills.cli vex-helper lint --json '{"code":"float x = 1;"}'

# Real compile check:
hython -m houdini_skills.cli vex-helper validate --json '{"template":"curl_noise_velocity"}'
```

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
