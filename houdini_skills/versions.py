"""Houdini version gating.

Houdini's Python API drifts between majors (parm names, node type names,
renamed hou functions). Every skill action declares the versions it is known
to work on; this module resolves the running version and refuses - loudly,
with a reason - rather than silently cooking the wrong graph.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The floor this repo supports. Nothing here is tested below this line.
MIN_SUPPORTED = (19, 5)

#: Verified by the test suite on a real install. Anything above is
#: "declared compatible but unverified" and the registry says so.
VERIFIED = [(22, 0)]


@dataclass(frozen=True)
class HoudiniVersion:
    major: int
    minor: int
    build: int = 0
    string: str = ""

    @property
    def series(self) -> tuple[int, int]:
        return (self.major, self.minor)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.string or f"{self.major}.{self.minor}.{self.build}"


def parse_version(text: str) -> HoudiniVersion:
    """Parse '22.0.368' / '19.5' into a HoudiniVersion."""
    parts = (text or "").strip().split(".")
    nums = []
    for p in parts:
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        nums.append(int(digits))
    while len(nums) < 3:
        nums.append(0)
    return HoudiniVersion(nums[0], nums[1], nums[2], text.strip())


def running_version() -> HoudiniVersion:
    """Version of the Houdini this process is running inside.

    Uses hou.applicationVersionString(). Note that hou.version() and
    hou.versionString() do NOT exist - a real failure mode we hit while
    writing this package, which is why the name is pinned here and asserted
    in tests/test_versions.py.
    """
    import hou  # provided by hython / hbatch / Houdini's Python

    return parse_version(hou.applicationVersionString())


def require_hou():
    """Import hou or raise a HoudiniNotAvailable with a usable message."""
    try:
        import hou  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on interpreter
        raise HoudiniNotAvailable(
            "the 'hou' module is not importable. Run this under hython, "
            "hbatch, or Houdini's own Python "
            "(<install>/bin/hython), not a system interpreter."
        ) from exc
    return hou


class HoudiniNotAvailable(RuntimeError):
    pass


def check_supported(ver: HoudiniVersion, supported: tuple[tuple[int, int], ...]) -> str | None:
    """Return None if `ver` is inside `supported`, else an explanation string."""
    if ver.series < MIN_SUPPORTED:
        return (
            f"Houdini {ver} is below this repo's v1 floor of "
            f"{MIN_SUPPORTED[0]}.{MIN_SUPPORTED[1]}"
        )
    if supported and ver.series not in supported:
        allowed = ", ".join(f"{a}.{b}" for a, b in supported)
        return f"Houdini {ver} is outside this skill's declared range: {allowed}"
    return None


def series_range(start: tuple[int, int], end: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    """Expand (19,5)-(22,0) into the list of supported series."""
    out = []
    for major in range(start[0], end[0] + 1):
        minors = range(start[1] if major == start[0] else 0,
                       end[1] + 1 if major == end[0] else 99)
        out.extend((major, m) for m in minors)
    return tuple(out)
