"""Line-oriented CLI so any agent can call a skill from a shell.

    hython -m houdini_skills.cli sop-automation grid_bevel \
        --json '{"size": [4, 0, 4], "bevel": 0.05}'
    python -m houdini_skills.cli --list
    python -m houdini_skills.cli schema sop-automation

Exit code 0 on ok:true, 1 on a structured error, 2 on CLI misuse.
"""
from __future__ import annotations

import argparse
import json
import sys

from .errors import SkillFailure
from .registry import discover, run_skill


def _read_payload(args) -> dict:
    raw = args.json
    if raw is None and args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            raw = fh.read()
    if raw is None and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillFailure("invalid_json", f"--json is not valid JSON: {exc}",
                           hint='use --json \'{"key": "value"}\' (single quotes on posix)')
    if not isinstance(data, dict):
        raise SkillFailure("invalid_input", "payload must be a JSON object")
    return data


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="houdini_skills.cli")
    p.add_argument("skill", nargs="?")
    p.add_argument("action", nargs="?")
    p.add_argument("--json", dest="json", help="payload as a JSON object")
    p.add_argument("--file", dest="file", help="payload from a JSON file")
    p.add_argument("--list", action="store_true", help="list skills")
    p.add_argument("--schema", metavar="SKILL", help="print a skill's action schemas")
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args(argv)

    try:
        if args.list or not args.skill:
            skills = discover()
            out = {"ok": True, "skills": {k: {"actions": sorted(v.meta.get("actions", {})),
                                              "houdini_versions": v.meta.get("houdini_versions", []),
                                              "description": v.meta.get("description", "")}
                                           for k, v in skills.items()}}
            print(json.dumps(out, indent=2 if args.pretty else None))
            return 0
        if args.schema:
            spec = discover().get(args.schema)
            if spec is None:
                raise SkillFailure("unknown_skill", f"no skill {args.schema!r}")
            print(json.dumps({"ok": True, "name": spec.name,
                              "actions": spec.meta.get("actions", {})}, indent=2))
            return 0
        result = run_skill(args.skill, args.action, _read_payload(args))
    except SkillFailure as exc:
        print(json.dumps(exc.to_result(), indent=2 if args.pretty else None))
        return 1
    except Exception as exc:  # unexpected -> still structured
        print(json.dumps({"ok": False, "error": {"kind": type(exc).__name__,
                                                 "message": str(exc),
                                                 "hint": "unexpected failure; see traceback on stderr"}}))
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
