# Usage

## Calling a skill

Two hosts, same interface.

**System Python (no Houdini running)** - inventory, schemas, static VEX lint,
error diagnosis. Anything that needs a live scene raises `houdini_unavailable`:

```bash
python -m houdini_skills.cli --list
python -m houdini_skills.cli --schema vex-helper
python -m houdini_skills.cli vex-helper lint --json '{"code":"double x = 1;"}'
python -m houdini_skills.cli troubleshooting diagnose --json '{"message":"Error: The following node type is not registered: pyro"}'
```

**Houdini's own Python (`hython`)** - everything, including real cooks:

```bash
"<Houdini>/bin/hython" -m houdini_skills.cli sop-automation grid_bevel \
  --json '{"container":"demo","size":[4,0,4],"divisions":12,"bevel_width":0.08}'
"<Houdini>/bin/hython" -m houdini_skills.cli vex-helper validate --json '{"template":"all"}'
"<Houdini>/bin/hython" -m houdini_skills.cli simulation-tools rbd_fall \
  --json '{"container":"rbd_demo","seed":7,"pieces":40,"height":3,"drop":1.0,"frames":[1,100]}'
```

Run `hython` from the repo root, or put the repo on `PYTHONPATH`:

```bash
export PYTHONPATH="$PWD"
"<Houdini>/bin/hython" -m houdini_skills.cli rendering configure \
  --json '{"engine":"karma_cpu","output_path":"/show/$HIPNAME/beauty.$F4.exr","samples":128,"frames":[1,24]}'
```

Inside a running Houdini GUI, `hou` is already imported and connected to your
session - `Session.open()` attaches to it, so a skill can operate on the scene
you are looking at rather than a throwaway one.

## Output contract

```json
{"ok": true,  "action": "...", "category": "...", "houdini": "22.0.368", "...": "..."}
{"ok": false, "error": {"kind": "unresolved_knobs", "message": "...",
                        "hint": "readable parms: ...", "source": "sop-automation",
                        "recovered": false, "details": {"candidates_tried": {"scale": [...]}}}}
```

Exit codes (locked by `tests/test_tier_a.py::TestCliContract`): `0` ok, `1` any
structured error - bad `--json`, unknown skill or action, a missing Houdini, or a
failed skill (the JSON on stdout is the payload), `2` argparse misuse (unknown
flag, missing argument).

## Running the tests

```bash
python -m unittest discover -s tests -p "test_tier_a*.py" -v
"<Houdini>/bin/hython" -m unittest discover -s tests -p "test_tier_b*.py" -v
python tools/make_registry.py --check     # SKILL_REGISTRY.md matches meta.json
python tools/check_skills.py              # every skill complete, licences audited
```

Tier B skips cleanly when `hou` is missing, so one command works everywhere.
Run all four before pushing; they are exactly what CI runs.

## Getting Tier B into CI honestly

GitHub-hosted runners **cannot install Houdini**: SideFX publishes no public
installer action for the hosted fleet, and an apprentice/non-commercial install
needs a SideFX account and a licence redeem code, which a shared runner may not
hold. So `ci.yml` splits the tiers and the integration job targets
`runs-on: [self-hosted, houdini]`.

To light it up, point a machine that already has a licensed Houdini at the repo:

1. On that machine, install the Actions runner for the repo
   (Settings -> Actions -> Runners -> New self-hosted runner).
2. Add the label `houdini` to the runner.
3. Set `HYTHON` in the runner environment to the hython binary, e.g.
   `C:/Program Files/Side Effects Software/Houdini 22.0.368/bin/hython.exe`.
4. Set the repository variable `TIER_B_RUNNER=true`
   (Settings -> Secrets and variables -> Actions -> Variables).

The variable is the switch, and it exists because of how Actions behaves: a
`runs-on: [self-hosted, houdini]` job with no matching runner does not fail and
does not skip - it **queues forever**, so the run never turns red *or* green and
the real signal gets buried. With the variable unset the job is skipped and the
Tier A jobs are the visible gate. With it set and no runner online, the queue
hang is a configuration fault you are meant to see.

Until someone wires up such a runner, Tier B coverage here is **local only**:
`hython -m unittest` on a licensed machine. That is a coverage gap, not a pass,
and the release notes for each tag say so.

## Version reality

The floor is 19.5 by declaration; every skill's `verified_on` field records what
has actually been run. Today that is `22.0.368` only. `docs/verified-api.md`
lists the API facts proven on that build, which is where the parm-name and
node-type tables came from.
