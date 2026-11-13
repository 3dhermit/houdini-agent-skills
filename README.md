# Houdini Agent Skills - v1.0.0

Skills that let an LLM agent drive **SideFX Houdini** through one shared,
version-aware Python wrapper around `hou`, with a test suite that runs against a
real Houdini install.

**Scope: Houdini 19.5+. Verified on 22.0.368.** Nothing in here has been tested
on 19.5-21.x; those versions are a *declared* floor, not a verified one, and the
registry says so per skill.

## What is actually in the box

```
skills/
  sop-automation/     create + wire SOPs, set parms, read geometry counts
  vex-helper/         generate VEX, static-lint it, compile-check it for real
  simulation-tools/   pyro / rbd / flip source+solver chains, dopnet, File Cache
  rendering/          Karma / mantra ROP configuration (config only, never renders)
  usd-pipeline/       SOP -> Solaris SOP Import -> USD ROP, proven on disk
  troubleshooting/    red nodes, wrong parm/type/API names, tiered error KB
houdini_skills/       the shared wrapper: hou_api, schema, versions, errors, cli, registry
tests/                test_tier_a.py (no Houdini) + test_tier_b.py (real hython)
docs/                 usage, contributing, adding-a-skill, verified-api
.github/workflows/    ci.yml, release.yml
SKILL_REGISTRY.md     the catalog an agent reads first
```

Each skill folder holds: `SKILL.md` (agent-facing instructions), `meta.json`
(machine-readable actions + JSON schemas + sources + versions) and `skill.py`
(implementation). `docs/` holds cross-cutting guides; the per-skill reference is
`skills/<name>/SKILL.md` on purpose, so there is exactly one place to edit and
no chance of the docs drifting from the code.

## Quick start

```bash
# Inventory the skills (works with no Houdini at all):
python -m houdini_skills.cli --list
python -m houdini_skills.cli --schema sop-automation

# Inside a real Houdini (this is where the geometry actually happens):
"<Houdini>/bin/hython" -m houdini_skills.cli sop-automation grid_bevel \
  --json '{"container":"demo","size":[4,0,4],"divisions":12,"bevel_width":0.08}'
"<Houdini>/bin/hython" -m houdini_skills.cli vex-helper validate \
  --json '{"template":"curl_noise_velocity"}'
```

Every call returns one JSON object: `{"ok": true, ...}` or
`{"ok": false, "error": {"kind", "message", "hint", "source", "details"}}`.
Exit code 0 on success, 1 on any structured error (bad payload, unknown skill or
action, no Houdini, failed skill), 2 on argparse misuse.

## The three rules this repo exists to enforce

1. **Never guess a parm name.** Node types and parms are resolved against the
   running build (`childTypeCategory().nodeTypes()`, `parmTemplateGroup()`), and
   a wrong name returns a `did you mean` error instead of a silent no-op.
2. **Never claim success without proof.** An export is a file on disk with a byte
   count. A VEX snippet is valid when Houdini's compiler says so. A sim result
   states what was *not* done.
3. **Version-gate or fail loudly.** Karma needs H20; a 19.5 floor that pretends
   otherwise is a bug, so the rendering skill refuses and points at mantra.

## Why a shared wrapper

`hou` is imperative, returns live objects, and renames things between majors.
`houdini_skills.hou_api.Session` is the one place that knows how to talk to it,
so a fix for an API change lands once and every skill inherits it. Every skill
is `run(action, payload: dict) -> dict` and validates its input before touching
Houdini.

## Testing, and the honest part about CI

```bash
python -m unittest discover -s tests -p "test_tier_a*.py"   # needs no Houdini
"<Houdini>/bin/hython" -m unittest discover -s tests -p "test_tier_b*.py"
```

GitHub-hosted runners **cannot install Houdini**: there is no official public
installer action, and a non-commercial/apprentice install needs a SideFX account
plus a licence redeem code. So the split is deliberate:

| tier | runs where | covers |
|---|---|---|
| A | GitHub Actions on every PR | schemas, version gating, VEX static lint, registry/metadata hygiene, error envelope |
| B | your machine or a self-hosted runner with Houdini | real cooks, real parm binding, real files on disk |

CI marks the integration job `pass if no houdini-labelled runner is online`, and
`docs/usage.md` explains how to attach one. Tests are stdlib `unittest` so they
run inside Houdini's own Python with **no pip installs** - hython's interpreter
is not the Python you pip into.

## Semantic versioning

`MAJOR` - a skill's action name, its payload schema, or its result envelope
changes; or the supported Houdini floor rises.
`MINOR` - new skill, new action, new backwards-compatible knob.
`PATCH` - bug fixes, parm-name candidate corrections, knowledge-base entries,
docs, tests.

Adding a skill is a MINOR. Renaming an action is MAJOR and needs a migration
note in `SKILL_REGISTRY.md`. v1.0.0 limitations are listed there.

## Contribution gate

A PR fails CI if a skill is missing `meta.json` keys, declares an action with no
handler, has a `SKILL.md` without frontmatter, or lists a source without a
licence. **Repos with no licence are not mergeable** - no licence means all
rights reserved, so such projects are cited as demand evidence only, never
copied. Full rules in `docs/contributing.md`.

## Known v1 limitations

- Verified only on Houdini 22.0.368 (Windows). 19.5-21.x are declared, not tested.
- `simulation-tools` builds and cooks source/solver chains and creates a `dopnet`;
  it does not author the DOP graph or step a simulation.
- `rendering` configures ROPs and never renders; Karma AOV authoring is
  explicitly out of v1.
- `usd-pipeline` proves one file landed; multi-file layer stacks and MaterialX
  round-tripping are out of scope.
- No crowds/cloth/Vellum authoring beyond verified Vellum solver names.

## Licence

MIT. Third-party projects consulted during curation, and exactly what each
contributed, are listed per skill in the `sources` block of its `meta.json` and
in `SKILL_REGISTRY.md`.
