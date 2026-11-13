"""The shared Houdini Python API wrapper every skill goes through.

Why this exists: raw `hou` is imperative, version-sensitive, and returns live
objects that an agent cannot serialise. This layer gives skills one shape:

    session = Session.open()
    session.create("grid", "ground")
    session.set_parms("ground", {"sizex": 4})     # validates parm names
    session.cook("ground")                        # collects real cook errors
    session.summary("ground")                     # JSON-safe dict

Everything here is discovery-first: parm names and node type names are checked
against the *running* Houdini, so a guess that is wrong for this version comes
back as a "did you mean" error instead of a silent no-op.
"""
from __future__ import annotations

import os
import re

from .errors import SkillFailure, hou_name_error, parse_node_errors, suggest
from .versions import HoudiniVersion, check_supported, require_hou


def _parm_names(node) -> set[str]:
    """All parm names on a node, including inside multi-parm tuples.

    hou.Node has NO parmNames() method (a very tempting guess that does not
    exist); the real surface is parms() / parmTuples() / allParms().
    """
    names: set[str] = set()
    try:
        names |= {p.name() for p in node.parms()}
    except Exception:
        pass
    try:
        for tup in node.parmTuples():
            names.add(tup.name())
            names |= {p.name() for p in tup.parms()}
    except Exception:
        pass
    if not names:
        try:
            names |= {p.name() for p in node.allParms()}
        except Exception:
            pass
    return names


class Session:
    """One connection to a running Houdini (hython / hbatch / Houdini GUI)."""

    def __init__(self, hou_mod, version: HoudiniVersion):
        self.hou = hou_mod
        self.version = version

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls) -> "Session":
        hou = require_hou()
        try:
            ver = HoudiniVersion(*hou.applicationVersion()[:3],
                                 string=hou.applicationVersionString())
        except AttributeError as exc:
            raise _wrap_hou(exc)
        return cls(hou, ver)

    def require(self, supported, action: str = "") -> None:
        reason = check_supported(self.version, supported)
        if reason:
            raise SkillFailure("unsupported_version", reason,
                               source=action or "skill",
                               details={"running": str(self.version)})

    # -- node access -------------------------------------------------------
    def node(self, path: str):
        if not path:
            raise SkillFailure("invalid_input", "node path is empty")
        try:
            n = self.hou.node(path)
        except Exception as exc:
            raise SkillFailure("hou_error", str(exc), source="hou.node")
        if n is None:
            raise SkillFailure("node_not_found", f"no node at {path!r}",
                               hint=self._nearby(path),
                               details={"path": path})
        return n

    def create(self, node_type: str, name: str, parent_path: str = "/obj",
               label: str | None = None):
        parent = self.node(parent_path)
        known = self._type_names(parent)
        if node_type not in known:
            raise SkillFailure("unknown_node_type",
                               f"{node_type!r} is not valid under {parent_path}",
                               hint="closest: " + ", ".join(suggest(node_type, known, 8)),
                               details={"requested": node_type,
                                        "suggestions": suggest(node_type, known, 8)})
        try:
            n = parent.createNode(node_type, name)
        except Exception as exc:
            raise SkillFailure("hou_error", str(exc), source="createNode",
                               details={"type": node_type, "parent": parent_path})
        if label:
            try:
                n.parm("label").set(label)
            except Exception:
                pass
        return n

    def find(self, pattern: str, limit: int = 200) -> list[dict]:
        """Match node paths with fnmatch, e.g. '/obj/geo/*' or '/out/*usd*'.

        hou has no hou.glob(); this walks the file and matches paths itself.
        """
        import fnmatch
        out = []
        for n in self._all_nodes():
            if pattern == n.path() or fnmatch.fnmatch(n.path(), pattern):
                out.append(self.ref(n))
                if len(out) >= limit:
                    break
        return out

    def delete(self, path: str, confirm: str = "") -> dict:
        """Delete one node. Destructive, so it needs an explicit confirm token."""
        n = self.node(path)
        if confirm != path:
            raise SkillFailure("needs_confirmation",
                               f"refusing to delete {path} without confirm={path!r}",
                               hint="pass confirm equal to the node path to proceed")
        label = n.name()
        try:
            n.destroy()
        except Exception as exc:
            raise SkillFailure("hou_error", str(exc), source="destroy")
        return {"deleted": path, "name": label}

    # -- parms -------------------------------------------------------------
    def set_parms(self, path: str, parms: dict) -> dict:
        """Set parms with name validation. Returns {set: {...}, skipped: {name: suggestions}}."""
        n = self.node(path)
        names = _parm_names(n)
        applied, rejected = {}, {}
        for key, value in (parms or {}).items():
            if key not in names:
                rejected[key] = suggest(key, names, 8)
                continue
            p = n.parm(key)
            try:
                if isinstance(value, bool):
                    p.set(1 if value else 0)
                elif isinstance(value, (list, tuple)):
                    p.set(list(value))
                else:
                    p.set(value)
                applied[key] = value
            except Exception as exc:
                raise SkillFailure("parm_set_failed",
                                   f"{path}.{key}: {exc}",
                                   hint=f"current value: {p.rawValue()!r}",
                                   details={"parm": key, "value": value})
        if rejected:
            raise SkillFailure(
                "unknown_parm",
                f"{path}: unknown parm(s) {sorted(rejected)}",
                hint="; ".join(f"{k} -> {', '.join(v) or '?'}" for k, v in rejected.items()),
                source="set_parms",
                details={"rejected": rejected, "applied": applied},
            )
        return applied

    def read_parms(self, path: str, names: list[str] | None = None) -> dict:
        n = self.node(path)
        keys = names if names else sorted(_parm_names(n))
        out = {}
        for k in keys:
            p = n.parm(k)
            if p is None:
                continue
            try:
                out[k] = p.rawValue()
            except Exception:
                out[k] = None
        return out

    def set_parm_flex(self, path: str, requested: str, candidates: list[str],
                      value) -> tuple[str, dict]:
        """Set a parm by concept: try `requested` then `candidates` until one exists.

        Returns (resolved_name, {'unresolved': [...]}) so callers can report
        honestly which friendly knobs bound to nothing on this build.
        """
        try:
            resolved = self.resolve_parm(path, requested, candidates)
        except SkillFailure as exc:
            return "", {"unresolved": {requested: exc.err.details.get("suggestions", [])}}
        n = self.node(path)
        n.parm(resolved).set(value)
        return resolved, {}

    def parm_names(self, path: str) -> set:
        return _parm_names(self.node(path))

    def ensure_geo(self, name: str) -> str:
        """Return /obj/<name> (a geometry container), creating it if missing."""
        path = f"/obj/{name}"
        try:
            n = self.hou.node(path)
        except Exception:
            n = None
        if n is not None:
            return path
        obj = self.node("/obj")
        try:
            obj.createNode("geo", name)
        except Exception as exc:
            raise SkillFailure("hou_error", f"cannot create {path}: {exc}",
                               source="createNode", details={"type": "geo"})
        return path

    def resolve_parm(self, path: str, requested: str, candidates: list[str]) -> str:
        """Pick the first candidate parm that actually exists on this version.

        Node type parm names move between Houdini majors. A skill asks for a
        concept ('which frames do I render') and passes plausible names; we
        bind to whatever the running build really has, and error with the real
        list when none match.
        """
        n = self.node(path)
        names = _parm_names(n)
        for cand in [requested] + candidates:
            if cand in names:
                return cand
        raise SkillFailure(
            "parm_not_found",
            f"{path}: none of {[requested] + candidates} exist on this build",
            hint="closest: " + ", ".join(suggest(requested, names, 10)),
            details={"asked_for": [requested] + candidates,
                     "suggestions": suggest(requested, names, 10)},
        )

    # -- cooking / diagnostics --------------------------------------------
    def cook(self, path: str, force: bool = True) -> dict:
        n = self.node(path)
        try:
            if force:
                n.cook(force=True)
        except Exception as exc:
            raise SkillFailure("cook_failed", str(exc), source="cook",
                               details=self._error_details(n))
        errs = parse_node_errors(n)
        hard = [e for e in errs if e["severity"] == "error"]
        if hard:
            raise SkillFailure("cook_errors",
                               f"{path} cooked with {len(hard)} error(s)",
                               source="cook", details={"errors": errs})
        return {"path": path, "errors": len(hard),
                "warnings": [e for e in errs if e["severity"] == "warning"]}

    def errors(self, path: str) -> dict:
        n = self.node(path)
        errs = parse_node_errors(n)
        return {"path": path, "type": self._type(n),
                "errors": [e for e in errs if e["severity"] == "error"],
                "warnings": [e for e in errs if e["severity"] == "warning"]}

    def chain(self, parent_path: str, types: list[str], names: list[str] | None = None):
        """Create a linear chain of nodes, wiring each into the previous one."""
        if not types:
            raise SkillFailure("invalid_input", "chain needs at least one node type")
        made = []
        prev = None
        for i, t in enumerate(types):
            nm = (names[i] if names and i < len(names) else None)
            n = self.create(t, nm or _default_name(t, i), parent_path)
            if prev is not None:
                try:
                    n.setInput(0, prev)
                except Exception as exc:
                    raise SkillFailure("hou_error",
                                       f"cannot wire {n.name()} <- {prev.name()}: {exc}",
                                       source="setInput")
            made.append(n)
            prev = n
        return made

    # -- scene / files -----------------------------------------------------
    def save_hip(self, path: str, versioned: bool = True) -> dict:
        """Save. versioned=True writes <stem>.vNN.hip and never clobbers in place."""
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
        target = path
        if versioned:
            stem, ext = os.path.splitext(path)
            existing = [int(m.group(1)) for f in os.listdir(os.path.dirname(stem) or ".")
                        if (m := re.match(re.escape(os.path.basename(stem)) + r"\.v(\d+)" + re.escape(ext) + r"$", f))]
            target = f"{stem}.v{max(existing, default=0) + 1:02d}{ext}"
        try:
            self.hou.hipFile.save(target)
        except Exception as exc:
            raise SkillFailure("hou_error", str(exc), source="hipFile.save")
        return {"saved": target, "overwrote_existing": os.path.normpath(target) == os.path.normpath(path)}

    def frame_range(self) -> dict:
        """Global playback range. Verified on 22.0.368: hou.playbar.frameRange().

        hou.playbackBarMinFrame(), hou.fetchGlobalSetup() and hou.setFrameRange()
        do NOT exist on the hou module - the playbar object owns the range.
        """
        pb = getattr(self.hou, "playbar", None)
        if pb is None:
            raise SkillFailure("hou_api_unavailable", "hou.playbar is not available")
        for getter, keys in ((lambda: pb.frameRange(), ("start", "end")),
                             (lambda: pb.playbackRange(), ("start", "end"))):
            try:
                rng = getter()
                return {"start": float(rng[0]), "end": float(rng[1]), "source": "hou.playbar"}
            except Exception:
                continue
        raise SkillFailure("hou_api_unavailable", "could not read the playback range",
                           hint="try hou.playbar.frameRange() by hand")

    def set_frame_range(self, start: float, end: float) -> dict:
        pb = getattr(self.hou, "playbar", None)
        if pb is None:
            raise SkillFailure("hou_api_unavailable", "hou.playbar is not available")
        try:
            pb.setFrameRange(float(start), float(end))
        except Exception as exc:
            raise SkillFailure("hou_error", str(exc), source="playbar.setFrameRange")
        check = self.frame_range()
        if (check["start"], check["end"]) != (float(start), float(end)):
            raise SkillFailure("range_not_applied",
                               f"asked for {(start, end)}, playbar reports "
                               f"{(check['start'], check['end'])}",
                               details={"readback": check})
        return {"start": float(start), "end": float(end), "verified_readback": check}

    # -- geometry facts ----------------------------------------------------
    def geometry_stats(self, path: str) -> dict:
        """Count real geometry facts an agent needs: prims, pieces by @name, bounds.

        Verified on 22.0.368: hou.Prim exposes findAttrib()/attribValue() (there
        is NO findAttribValue()), and packed prims are hou.PackedPrim instances.
        """
        n = self.node(path)
        try:
            geo = n.geometry()
        except Exception as exc:
            raise SkillFailure("hou_error", f"{path}: no geometry ({exc})", source="geometry")
        if geo is None:
            raise SkillFailure("no_geometry", f"{path} has no geometry to read",
                               hint="is the display flag on a SOP node?")
        prims = list(geo.prims())
        names = set()
        for p in prims:
            try:
                if p.findAttrib("name") is not None:
                    names.add(p.attribValue("name"))
            except Exception:
                continue
        try:
            packed_cls = getattr(self.hou, "PackedPrim")
            packed = sum(1 for p in prims if isinstance(p, packed_cls))
        except Exception:
            packed = None
        out = {
            "path": path,
            "type": self._type(n),
            "prim_count": len(prims),
            "point_count": len(geo.points()),
            "packed_count": packed,
            "piece_count_by_name": len(names) if names else None,
        }
        try:
            b = geo.boundingBox()
            out["framed_bounds"] = {"min": list(b.minVec()), "max": list(b.maxVec())}
        except Exception:
            pass
        return out

    # -- misc --------------------------------------------------------------
    def ref(self, n) -> dict:
        return {"path": n.path(), "name": n.name(), "type": self._type(n),
                "label": n.name(), "is_selected": n in (self.hou.selectedNodes() or [])}

    def list_types(self, parent_path: str = "/obj", contains: str = "") -> list[str]:
        names = self._type_names(self.node(parent_path))
        if contains:
            names = [t for t in names if contains.lower() in t.lower()]
        return sorted(names)

    # internals
    def _error_details(self, node) -> dict:
        """Best-effort error capture for cook failures; never masks the original."""
        try:
            errs = parse_node_errors(node)
        except Exception:
            errs = []
        return {"errors": [e for e in errs if e["severity"] == "error"],
                "warnings": [e for e in errs if e["severity"] == "warning"],
                "node": getattr(node, "path", lambda: "")()}

    def _type(self, n) -> str:
        try:
            return n.type().name()
        except Exception:
            return ""

    def _type_names(self, network) -> set:
        """Valid node type names for a network.

        Verified on 22.0.368: the call is node.childTypeCategory().nodeTypes().
        (node.networkType() does NOT exist - tempting, wrong.) A geo container
        therefore reports the ~1700 SOP types, /out reports ROP types.
        """
        for getter in (lambda: network.childTypeCategory().nodeTypes(),
                       lambda: network.type().category().nodeTypes()):
            try:
                return set(getter().keys())
            except Exception:
                continue
        return set()

    def _all_nodes(self) -> list:
        """Every node in the file. hou has NO hou.glob() - this is the walk."""
        world = self.hou.node("/")
        for getter in (lambda: world.allChildren(), lambda: world.children()):
            try:
                return list(getter())
            except Exception:
                continue
        return []

    def destroy_matching(self, pattern: str) -> list:
        """Delete every node whose path matches a fnmatch pattern. Returns paths."""
        import fnmatch
        killed = []
        for n in self._all_nodes():
            if fnmatch.fnmatch(n.path(), pattern):
                try:
                    n.destroy()
                    killed.append(n.path())
                except Exception:
                    pass
        return killed

    def _glob_fallback(self, pattern: str):
        return []

    def _nearby(self, path: str) -> str:
        parent = os.path.dirname(path) or "/"
        try:
            kids = self.hou.node(parent).children()
            names = [k.path() for k in kids]
        except Exception:
            return "check the path; use find() to list nodes"
        near = suggest(path, names, 6)
        return (f"children of {parent}: " + ", ".join(near)) if near else f"{parent} has no matching children"


def _wrap_hou(exc: AttributeError):
    err = hou_name_error(exc)
    return SkillFailure(err.kind, err.message, hint=err.hint, source=err.source,
                        details=err.details)


def _default_name(node_type: str, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", node_type).strip("_") or "node"
    return f"{safe}_{index + 1}"


_DEFAULT_SESSION: Session | None = None


def get_session() -> Session:
    """Cached session for repeated skill calls inside one process."""
    global _DEFAULT_SESSION
    if _DEFAULT_SESSION is None:
        _DEFAULT_SESSION = Session.open()
    return _DEFAULT_SESSION
