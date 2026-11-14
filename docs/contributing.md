# Contributing

Short version: **prove it on a real Houdini, or say that you did not.**

## Non-negotiables

1. **No guessed parm or node-type names.** If a change names a parm or node type,
   it must come from a running build, and the fact goes into
   `docs/verified-api.md` with the version it was read off. `Session.parm_names()`
   and `hou.<category>NodeTypeCategory().nodeTypes()` are the tools; a forum post
   about an older version is not.
2. **Success needs evidence.** An export is a file with a byte count. A VEX
   snippet is valid when Houdini's compiler says so in `vex-helper validate`. A
   simulation result states what it did *not* do. A PR that makes a skill claim
   more than it proves will be closed.
3. **Licensing is a gate, not a formality.** A repo with no licence file is **all
   rights reserved** by default and cannot be merged, no matter how good the code
   is. If you cite such a project, its `sources` entry must say
   `used_for: "NOTHING (no licence: all rights reserved by default)"`. CI audits
   exactly this.
4. **Version floor is a claim.** `houdini_versions` may declare 19.5+; `verified_on`
   must list only builds actually run. Never move a version into `verified_on`
   without a Tier B run on it.

## Adding or changing a skill

See [adding-a-skill.md](adding-a-skill.md) for the file-by-file walkthrough.
The gate every skill must pass:

- `SKILL.md` with YAML frontmatter (`name`, `description`) - the agent-facing instructions.
- `meta.json` with `name`, `category`, `version`, `houdini_versions`, `verified_on`,
  `dependencies`, `actions` (action -> JSON schema), `examples`, `sources`.
- `skill.py` exposing `run(action, payload) -> dict` and a `_HANDLERS` dict that
  covers every action declared in `meta.json`.
- Every failure raised as `SkillFailure(kind, message, hint=..., source=...)`, never
  a bare exception and never a silent default.
- Tier A test for anything that needs no Houdini; Tier B test for anything that
  touches a scene.

CI fails on: a declared action with no handler, a missing `SKILL.md`, a
`meta.json` missing required keys or actions, an out-of-date
`SKILL_REGISTRY.md` (regenerate with `python tools/make_registry.py`), and a
merged source whose licence is not on the allow-list
(`python tools/check_skills.py` runs both tree checks; directories starting with
`_` or `.` under `skills/` are not skills).

## Versioning

| bump | when |
|---|---|
| MAJOR | an action name, its payload schema, or the result envelope changes; the supported Houdini floor rises; the `hou` wrapper's public methods change |
| MINOR | a new skill, a new action, or a new optional knob |
| PATCH | bug fix, parm-name candidate correction, knowledge-base entry, docs, tests |

A MAJOR needs a migration note in `SKILL_REGISTRY.md` and in the release notes.

## Report style

State the tier of each claim: **verified** (test name and build), **reproduced
by hand** (what you ran, what it printed), or **declared** (docs/forum/memory,
unreproduced - must be labelled as such). "It should work" is not a status. When
a test fails, paste the failure; a fix that hides a failing assertion is a
regression, not a fix.

## What v1 deliberately does not do

Render (configuring a ROP is not rendering), change solver settings or step
simulations, author Karma AOVs, handle multi-file USD layer stacks or MaterialX,
or touch crowds. Those are v2 conversations, and each needs its own proof plan.
