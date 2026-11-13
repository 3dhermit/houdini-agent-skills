# Adding a skill, file by file

A skill is a folder, not a framework. Copy the smallest one (`troubleshooting`)
and change the contents.

## 1. Folder and metadata

```
skills/<category>/meta.json      machine-readable contract
skills/<category>/SKILL.md       instructions for the agent that calls it
skills/<category>/skill.py       implementation
```

`meta.json` shape (all keys required by the Tier A hygiene gate):

```json
{
  "name": "my-skill",
  "category": "my-skill",
  "version": "1.0.0",
  "houdini_versions": [">=19.5"],
  "verified_on": ["22.0.368"],
  "dependencies": ["houdini_skills"],
  "description": "what it does, in one sentence an agent can route on",
  "triggers": ["prompts that should select this skill"],
  "actions": {
    "do_thing": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "node path, e.g. /obj/geo/box1"},
        "amount": {"type": "number", "default": 1.0}
      },
      "required": ["path"],
      "additionalProperties": false
    }
  },
  "examples": ["hython -m houdini_skills.cli my-skill do_thing --json '{\"path\":\"/obj/geo/box1\"}'"],
  "sources": [
    {"repo": "https://github.com/someone/thing", "license": "MIT",
     "used_for": "idea only, no code merged"}
  ]
}
```

Folder names may contain hyphens - `skills/__init__.py` loads them by file path,
so a hyphen is not a Python problem.

## 2. Implementation

```python
from houdini_skills.errors import SkillFailure, ok
from houdini_skills.hou_api import get_session
from houdini_skills.versions import series_range

SUPPORTED = series_range((19, 5), (22, 0))


def run(action, payload):
    s = get_session()
    s.require(SUPPORTED, f"my-skill.{action}")
    return _HANDLERS[action](s, payload)


def _do_thing(s, p):
    node = s.node(p["path"])              # raises with did-you-mean on a bad path
    ...
    return ok({"action": "do_thing", "path": node.path(), "...": "..."})


_HANDLERS = {"do_thing": _do_thing}
```

Rules that keep this honest:

- **Validate the payload before touching Houdini.** The CLI already ran your JSON
  schema; inside the skill, use what is there rather than re-checking types.
- **Never guess a parm name.** Use `s.parm_names(path)` to read what exists, or
  a candidate list with a comment naming the build each candidate was verified on,
  and report anything unresolved in the result.
- **Never call a parm a toggle.** Menus need their item value; read
  `parm.menuItems()` (bare strings on 22.x, `(label, value)` pairs on older builds).
- **Raise `SkillFailure` with a hint that contains the real list**, not prose.
  The hint is the fix.
- **Report what did not happen.** `simulated: false`, `rendered: false`,
  `unresolved: {...}`. A result object that hides a gap is a bug.

## 3. SKILL.md

Frontmatter is what an agent reads first:

```markdown
---
name: my-skill
description: Use when <trigger>. <what it does>. <one hard limit>.
---
```

Then: when to use it, the call shape, what a good result looks like, and the
traps. Write the traps as rules with the reason, not as a changelog.

## 4. Tests

`tests/test_tier_a.py` - schema validity, action/handler parity, version gating,
pure logic. `tests/test_tier_b.py` - real Houdini. Use the `asm*_<name>` node-name
prefix and `Session.destroy_matching("/obj/asm*_<name>")` in `tearDown`, and pin
any new parm-name claim in `TestVerifiedApiFacts` so a future Houdini fails loudly
instead of resolving to nothing.

## 5. Finish

```bash
python tools/make_registry.py            # regenerate the catalog (CI checks it)
python -m unittest discover -s tests -p "test_tier_a*.py"
hython -m unittest discover -s tests -p "test_tier_b*.py"
```

Bump `version` in `meta.json` per `docs/contributing.md`, and if you learned a
parm-name or node-type fact on a real build, it goes in `docs/verified-api.md` in
the same PR.
