#!/usr/bin/env python3
"""Repo self-checks that CI must not re-implement inline.

Why this file exists: both checks below used to be Python heredocs inside
ci.yml, where they silently disagreed with registry.discover(). An earlier build
step creates skills/__pycache__, and the heredocs counted that as a skill
folder, so the v1.0.0 tag push failed Tier A on all three Python versions
(2026-11-13) while passing locally only because I had deleted __pycache__ first.
Same lesson as SKILL_REGISTRY.md: one implementation, in the repo, testable.

Two rules, exit 1 with a readable message:
  1. every directory under skills/ is a complete skill
     (SKILL.md + meta.json + skill.py). Names starting with "_" or "." are not
     skills; anything else must be complete.
  2. no source listed in a skill's meta.json may be unlicensed/permissive-only
     unless it was used for nothing (an exclusion audit).

Usage:  python tools/check_skills.py [skills_dir]
"""
from __future__ import annotations

import json
import os
import sys

NEEDED = ("SKILL.md", "meta.json", "skill.py")
# Anything merged as code must carry one of these. "used_for: NOTHING" records a
# repo that was reviewed and deliberately not copied from.
PERMISSIVE = {
    "mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause",
    "isc", "unlicense", "zlib", "cc0-1.0",
}


def candidate_dirs(skills_dir: str = "skills") -> list[tuple[str, str]]:
    """Skill directories: skip caches/VCS droppings, exactly like the registry."""
    out = []
    for entry in sorted(os.listdir(skills_dir)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        d = os.path.join(skills_dir, entry)
        if os.path.isdir(d):
            out.append((entry, d))
    return out


def check_complete(skills_dir: str = "skills") -> list[str]:
    problems = []
    for entry, d in candidate_dirs(skills_dir):
        missing = [f for f in NEEDED if not os.path.isfile(os.path.join(d, f))]
        if missing:
            problems.append(f"{entry}: missing {', '.join(missing)}")
        else:
            try:
                meta = json.load(open(os.path.join(d, "meta.json"), encoding="utf-8"))
            except json.JSONDecodeError as exc:
                problems.append(f"{entry}: meta.json is not valid JSON ({exc})")
                continue
            if not meta.get("actions"):
                problems.append(f"{entry}: meta.json declares no actions")
    return problems


def check_licences(skills_dir: str = "skills") -> list[str]:
    offenders = []
    for entry, d in candidate_dirs(skills_dir):
        meta_path = os.path.join(d, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        meta = json.load(open(meta_path, encoding="utf-8"))
        for src in meta.get("sources", []):
            lic = (src.get("license") or "").lower()
            used_for = (src.get("used_for") or "").lower()
            if "nothing" in used_for:
                continue
            if lic not in PERMISSIVE:
                offenders.append(f"{entry}: {src.get('repo', '?')} licence "
                                 f"{lic or 'unset'!r} is not on the allow-list "
                                 f"({', '.join(sorted(PERMISSIVE))})")
    return offenders


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    skills_dir = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    bad = check_complete(skills_dir) + check_licences(skills_dir)
    if bad:
        print("skill tree check failed:", *bad, sep="\n  ")
        return 1
    n = len(candidate_dirs(skills_dir))
    print(f"skill tree check clean: {n} skill(s), complete docs + licence audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
