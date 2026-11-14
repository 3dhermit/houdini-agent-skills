"""Tier B tests - real Houdini. Run under hython (or inside Houdini's Python):

    "<Houdini>/bin/hython.exe" -m unittest discover -s tests -p "test_tier_b*.py"

Skipped automatically when `hou` is not importable, so CI stays green on
GitHub-hosted runners while these still assert against a live install locally.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

from test_tier_a import REAL_ONLY, hou_available  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Hyphenated skill folders load by file path, never by import path.
from skills import import_skill_module  # noqa: E402


def _session():
    from houdini_skills.hou_api import Session
    return Session.open()


@REAL_ONLY
class TestSopAutomation(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asmtest_*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asmtest_*")
        self.s.destroy_matching("/out/asmtest_*")

    def test_version_reported(self):
        self.assertGreaterEqual(self.s.version.series, (19, 5))

    def test_grid_bevel_creates_and_cooks(self):
        from skills import import_skill_module
        sop = import_skill_module("sop-automation")
        result = sop.run("grid_bevel", {"container": "asmtest_gb", "size": [4, 0, 4],
                                        "divisions": 8, "bevel_width": 0.05})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["grid"], "/obj/asmtest_gb/grid1")
        self.assertEqual(result["cook"]["errors"], 0)
        self.assertGreater(result["result"]["prim_count"], 0)
        # the whole point of flex resolution: it must tell us which real parm
        # names it bound, and bind nothing it could not find
        self.assertIn("sizex", result["applied_parms"])
        self.assertEqual(result["applied_parms"]["bevel_width"]["parm"], "offset")

    def test_unknown_parm_raises_with_suggestions(self):
        from houdini_skills.errors import SkillFailure
        geo = self.s.ensure_geo("asmtest_parms")
        grid = self.s.create("grid", "g", geo)
        with self.assertRaises(SkillFailure) as cm:
            self.s.set_parms(grid.path(), {"sizx": 3})
        err = cm.exception.to_result()["error"]
        self.assertEqual(err["kind"], "unknown_parm")
        self.assertIn("sizex", str(err["details"]["rejected"]["sizx"]))

    def test_set_parms_reports_what_hou_actually_kept(self):
        # 22.0.368: hou clamps out-of-range numbers with NO error, and raises TypeError
        # on a fractional value for an int parm. Not raising is not proof of success.
        from houdini_skills.errors import SkillFailure
        geo = self.s.ensure_geo("asmtest_kept")
        grid = self.s.create("grid", "g", geo)
        out = self.s.set_parms(grid.path(), {"rows": -5, "sizex": 6.5})
        self.assertEqual(out["kept"]["rows"], "1", "hou clamps rows below its minimum")
        self.assertIn("rows", out["drift"], "a silent clamp must be visible")
        self.assertEqual(out["drift"]["rows"]["requested"], -5)
        self.assertEqual(out["kept"]["sizex"], "6.5")
        self.assertNotIn("sizex", out["drift"], "a value that stuck is not drift")
        with self.assertRaises(SkillFailure) as cm:
            self.s.set_parms(grid.path(), {"rows": 2.7})
        err = cm.exception.to_result()["error"]
        self.assertEqual(err["kind"], "parm_set_failed")
        self.assertIn("fractional", str(err["message"]),
                      "hou's own words should reach the caller")
        self.assertNotIn("Additional information", str(err["message"]),
                         "SWIG overload dumps belong in details, not the message")
        self.assertIn("does not round", str(err["hint"]))

    def test_menu_parm_index_clamps_and_relabels(self):
        rop = self.s.create_at("/out", "karma", "asmtest_menu")
        out = self.s.set_parms(rop.path(), {"trange": 99})
        self.assertEqual(out["kept"]["trange"], "stage", "menus report the label")
        self.assertIn("trange", out["drift"], "an out-of-range index must be flagged")
        out2 = self.s.set_parms(rop.path(), {"trange": 2})
        self.assertEqual(out2["kept"]["trange"], "on")
        self.assertNotIn("trange", out2["drift"], "an in-range index is not drift")

    def test_unknown_node_type_raises_with_suggestions(self):
        from houdini_skills.errors import SkillFailure
        geo = self.s.ensure_geo("asmtest_types")
        with self.assertRaises(SkillFailure) as cm:
            self.s.create("polybeval", "b", geo)
        self.assertEqual(cm.exception.to_result()["error"]["kind"], "unknown_node_type")

    def test_delete_requires_confirm(self):
        from houdini_skills.errors import SkillFailure
        geo = self.s.ensure_geo("asmtest_del")
        n = self.s.create("grid", "g", geo)
        target = n.path()          # capture the path BEFORE anything is destroyed
        with self.assertRaises(SkillFailure):
            self.s.delete(target)
        receipt = self.s.delete(target, target)
        self.assertEqual(receipt["deleted"], target)
        self.assertIsNone(self.hou.node(target))

    def test_geometry_stats_pieces_not_prims(self):
        geo = self.s.ensure_geo("asmtest_pieces")
        box, sites = self.s.chain(geo, ["box", "scatter"], ["b", "s"])
        vor = self.s.create("voronoifracture", "v", geo)
        vor.setInput(0, box)
        vor.setInput(1, sites)     # verified: no site points -> this fails to cook
        self.s.cook(vor.path())
        stats = self.s.geometry_stats(vor.path())
        self.assertGreater(stats["prim_count"], 0)
        if stats.get("piece_count_by_name") is not None:
            self.assertLessEqual(stats["piece_count_by_name"], stats["prim_count"])


    def test_grid_bevel_binds_every_knob(self):
        """An unresolved knob means a value the caller believes was set was NOT set.

        The Grid SOP on 22.0.368 has sizex/sizey/rows/cols - no sizez, no
        divisionsx. A candidate list that stops at the obvious names leaves the
        depth and subdivision count silently at their defaults.
        """
        from skills import import_skill_module
        sop = import_skill_module("sop-automation")
        result = sop.run("grid_bevel", {"container": "asmtest_bind", "size": [4, 0, 4],
                                        "divisions": 6, "bevel_width": 0.05})
        self.assertEqual(result["unresolved_parms"], {},
                         f"knobs not bound: {result['unresolved_parms']}")
        # Verified geometry contract on the Grid itself (rows/cols are POINT
        # counts on 22.0.368): 6 -> 6x6 points -> 5x5 quads.
        grid_stats = sop.run("geo_stats", {"path": result["grid"]})
        self.assertEqual(grid_stats["prim_count"], 25,
                         f"6 divisions must give 25 quads, got {grid_stats['prim_count']}")
        self.assertEqual(grid_stats["point_count"], 36)
        self.assertGreaterEqual(result["result"]["prim_count"], 25,
                                "the bevel must not lose the grid's faces")
        self.s.destroy_matching("/obj/asmtest_bind")

    def test_find_and_cleanup_reach_nested_nodes(self):
        """hou.Node.allChildren() does not exist; allSubChildren() is the walk."""
        from skills import import_skill_module
        sop = import_skill_module("sop-automation")
        sop.run("grid_bevel", {"container": "asmtest_deep", "size": [2, 0, 2],
                               "divisions": 3, "bevel_width": 0.02})
        found = sop.run("find", {"pattern": "/obj/asmtest_deep/*"})["matches"]
        paths = [m["path"] for m in found]
        self.assertIn("/obj/asmtest_deep/bevel1", paths,
                      "find() only saw the top level - check _all_nodes")
        killed = self.s.destroy_matching("/obj/asmtest_deep/*")
        self.assertIn("/obj/asmtest_deep/grid1", killed,
                      "destroy_matching only saw the top level; cleanup is not happening")
        self.s.destroy_matching("/obj/asmtest_deep")


@REAL_ONLY
class TestVexValidation(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asmvex_*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asmvex_*")

    def test_all_templates_compile(self):
        from skills import import_skill_module
        vex = import_skill_module("vex-helper")
        bad = []
        for key, tpl in vex.TEMPLATES.items():
            kind = "point" if tpl["wrangle_kind"] == "volume" else tpl["wrangle_kind"]
            try:
                vex.run("validate", {"template": key, "wrangle_kind": kind,
                                     "container": f"asmvex_{key}", "keep": False})
            except Exception as exc:  # noqa: BLE001
                bad.append((key, str(exc)[:220]))
        self.assertFalse(bad, "templates that failed a real cook:\n" +
                         "\n".join(f"  {k}: {m}" for k, m in bad))

    def test_broken_vex_is_caught(self):
        from houdini_skills.errors import SkillFailure
        from skills import import_skill_module
        vex = import_skill_module("vex-helper")
        with self.assertRaises(SkillFailure) as cm:
            vex.run("validate", {"code": "float x = nosuchfunction(@P);\n",
                                 "container": "asmvex_broken"})
        self.assertEqual(cm.exception.to_result()["error"]["kind"], "vex_invalid")

    def test_truncated_snippet_lints_dirty(self):
        from skills import import_skill_module
        vex = import_skill_module("vex-helper")
        findings = vex.lint("vector v = {0, 0;\n")
        self.assertTrue([f for f in findings if f["severity"] == "error"])


@REAL_ONLY
class TestUsdExport(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/out/asmusd_*")
        self.s.destroy_matching("/obj/asmusd_*")
        self.s.destroy_matching("/stage/asmusd_*")

    def tearDown(self):
        self.s.destroy_matching("/out/asmusd_*")
        self.s.destroy_matching("/obj/asmusd_*")
        self.s.destroy_matching("/stage/asmusd_*")

    def test_export_writes_a_real_file(self):
        from skills import import_skill_module
        usd = import_skill_module("usd-pipeline")
        geo = self.s.ensure_geo("asmusd_geo")
        grid = self.s.create("grid", "g", geo)
        out = os.path.join(tempfile.gettempdir(), "houdini_agent_skills_probe.usda")
        if os.path.exists(out):
            os.remove(out)
        result = usd.run("export", {"path": grid.path(), "output": out,
                                    "container": "asmusd_rop", "frames": [], "unpack": False})
        self.assertTrue(os.path.isfile(out), f"no USD written: {result}")
        self.assertGreater(os.path.getsize(out), 40, "USD file is suspiciously small")
        verify = usd.run("verify", {"output": out})
        self.assertTrue(verify["exists"])


@REAL_ONLY
class TestRenderConfig(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou

    def test_karma_binds_verified_parm_names(self):
        from skills import import_skill_module
        rend = import_skill_module("rendering")
        result = rend.run("configure", {"container": "asmkarma", "engine": "karma_cpu",
                                        "resolution": [640, 360], "frames": [1, 24],
                                        "output_path": "$HIP/out.$F4.exr",
                                        "samples": 16, "aovs": []})
        applied = result["applied"]
        self.assertEqual(applied["resolutionx"]["parm"], "resolutionx")
        self.assertEqual(applied["samples"]["parm"], "samplesperpixel")
        self.assertIn("picture", applied["output_path"]["parm"])
        self.assertFalse(result["rendered"])
        self.s.destroy_matching("/out/asmkarma")

    def test_mantra_reality_is_reported(self):
        """22.x dropped mantra, so the 19.5 fallback must fail loudly, not quietly."""
        from houdini_skills.errors import SkillFailure
        from skills import import_skill_module
        rend = import_skill_module("rendering")
        engines = rend.run("list_engines", {})
        if self.s.version.series >= (22, 0):
            self.assertFalse(engines["engines"]["mantra"],
                             "mantra came back available - verify against "
                             "hou.ropNodeTypeCategory() and update the doc")
            with self.assertRaises(SkillFailure) as cm:
                rend.run("configure", {"container": "asmmantra", "engine": "mantra"})
            self.assertEqual(cm.exception.kind, "engine_unavailable")
            self.assertIn("karma", cm.exception.err.hint.lower())
        self.s.destroy_matching("/out/asmmantra")

    def test_karma_xpu_does_not_invent_a_parm(self):
        from skills import import_skill_module
        rend = import_skill_module("rendering")
        result = rend.run("configure", {"container": "asmxpu", "engine": "karma_xpu",
                                        "samples": 8})
        # No parm was invented for XPU; the result has to say what is not configurable.
        self.assertTrue(result["xpu_note"])
        self.assertNotIn("render_method", result["applied"])
        self.s.destroy_matching("/out/asmxpu")


@REAL_ONLY
class TestSimSetup(unittest.TestCase):
    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asmsim_*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asmsim_*")

    def test_sim_object_types_exist(self):
        from skills import import_skill_module
        sim = import_skill_module("simulation-tools")
        names = sim._obj_types(self.s)
        self.assertTrue(names, "objNodeTypeCategory() returned nothing - wrong API call")
        self.assertIn("dopnet", names, "dopnet must exist as the sim object")
        # The claim this repo documents: there is NO object-level pyro/rbd/flip.
        for absent in ("pyro", "rbd", "flip"):
            self.assertNotIn(absent, names,
                             f"{absent} is not an OBJ node type; the KB entry for it is wrong")
        geo = self.s.ensure_geo("asmsim_probe")
        for kind in ("pyro_source", "pyro_solver", "rbd_fracture", "rbd_solver",
                     "flip_source", "flip_solver"):
            resolved = sim._resolve_sop_type(self.s, geo, kind)
            self.assertTrue(resolved)

    def test_rbd_fall_reports_energy(self):
        from skills import import_skill_module
        sim = import_skill_module("simulation-tools")
        result = sim.run("rbd_fall", {"container": "asmsim_rbd", "frames": [1, 40],
                                      "voronoi_points": 20, "lean_degrees": 12,
                                      "drop_distance": 1.0})
        self.assertIn("energy", result)
        self.assertTrue(result["not_done"], "must state what it did not do")
        self.assertEqual(result["cook"]["errors"], 0, "fracture chain must cook clean")
        self.assertTrue(result["site_points_wired"])
        # A prim count is not a piece count. The old payload reported prim_count as
        # `source_pieces` (11,067 "pieces" for a 20-site fracture), which is exactly
        # the kind of confident wrong number an agent then designs a shot around.
        self.assertIn("source_prims", result)
        self.assertIn("source_pieces_by_name", result)
        self.assertNotIn("source_pieces", result)
        self.assertEqual(result["knobs"]["unbound"], [],
                         "energy knobs must all bind on this build: %s" % result["knobs"])
        self.assertTrue(result["energy"]["lean_applied"], "lean must actually be applied")
        self.assertTrue(result["energy"]["drop_applied"], "drop must actually be applied")
        self.assertEqual(result["knobs"]["site_count_parm"], "npts",
                         "scatter point count parm changed again")
        self.assertEqual(result["site_points"], result["site_count_requested"],
                         "requested scatter sites did not land in the geometry")
        b = result["bounds_after_energy"]
        self.assertTrue(b and "min" in b and "max" in b,
                        "bounds proof must come back: %s" % b)
        # A 12-degree lean on a 1x3x1 column must show up in the extents: the z span
        # grows past 1.0 and the y span past 3.0. Numbers here were read off
        # 22.0.368 (z -0.801..0.801, y -0.571..2.571 after a 1.0 lift).
        self.assertGreater(b["max"][2] - b["min"][2], 1.0,
                           "lean did not spread the z extents - rx is a no-op again")
        self.assertGreater(b["max"][1] - b["min"][1], 3.0,
                           "lean did not change the y extents")
        self.s.destroy_matching("/obj/asmsim_*")
        self.s.destroy_matching("/out/asmsim_*")


@REAL_ONLY
class TestVerifiedApiFacts(unittest.TestCase):
    """Pins every claim in docs/verified-api.md to the running Houdini.

    The doc is the source of the parm-name and node-type tables the skills use.
    If a future Houdini renames a parm or drops a node type, this test fails
    first and the doc gets corrected - instead of a skill quietly resolving to
    nothing. Facts that are specific to 22.x are skipped on other series so the
    declared 19.5+ floor stays honest.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.series = self.s.version.series

    def tearDown(self):
        self.s.destroy_matching("/obj/asmapi_*")
        self.s.destroy_matching("/out/asmapi_*")

    def _sop_types(self):
        return set(k.lower() for k in self.hou.sopNodeTypeCategory().nodeTypes())

    def _rop_types(self):
        return set(k.lower() for k in self.hou.ropNodeTypeCategory().nodeTypes())

    def test_module_functions_that_do_not_exist(self):
        # Each of these is a natural guess and each raises AttributeError.
        for name in ("glob", "setFrameRange", "fetchGlobalSetup",
                     "playbackBarMinFrame", "applicationVersionTuple",
                     "applicationBuild"):
            self.assertFalse(hasattr(self.hou, name),
                             f"hou.{name} exists now - update docs/verified-api.md")

    def test_playbar_owns_the_frame_range(self):
        pb = self.hou.playbar
        rng = pb.frameRange()
        self.assertEqual(len(rng), 2)
        pb.setFrameRange(11.0, 137.0)
        self.assertEqual(list(pb.frameRange()), [11.0, 137.0])

    def test_node_walk_uses_allsubchildren(self):
        """allChildren() does not exist; children() is one level and silently wrong."""
        world = self.hou.node("/")
        self.assertFalse(hasattr(world, "allChildren"),
                         "allChildren() exists now - re-check _all_nodes")
        self.assertTrue(hasattr(world, "allSubChildren"))
        geo = self.s.ensure_geo("asmapi_walk")
        self.s.create("null", "asmapi_deep", geo)
        paths = [n.path() for n in world.allSubChildren()]
        self.assertIn(f"{geo}/asmapi_deep", paths,
                      "a node two levels down was not visible to the walk")

    def test_grid_and_box_parm_names(self):
        """The obvious names on both primitives are wrong; the probe is the doc."""
        geo = self.s.ensure_geo("asmapi_gb")
        grid = self.s.create("grid", "asmapi_grid", geo)
        names = set(self.s.parm_names(grid.path()))
        self.assertTrue({"sizex", "sizey", "rows", "cols"} <= names)
        for wrong in ("sizez", "divisionsx", "divisionsz"):
            self.assertNotIn(wrong, names, f"grid has a {wrong} parm now - update the doc")
        box = self.s.create("box", "asmapi_box", geo)
        bnames = set(self.s.parm_names(box.path()))
        self.assertTrue({"sizex", "sizey", "sizez", "divsx"} <= bnames)
        self.assertNotIn("divisionsx", bnames, "box divides parm was renamed again")

    def test_wrangle_sop_type_name(self):
        types = self._sop_types()
        self.assertIn("attribwrangle", types, "the Attribute Wrangle type is attribwrangle")
        self.assertNotIn("wrangle", types, "'wrangle' is not a registered SOP type")
        self.assertNotIn("pyro", types, "pyro is not a SOP type either; it is pyrosource/pyrosolver")

    def test_filecache_parms_22(self):
        if self.series < (22, 0):
            self.skipTest("filecache::2.0 parm layout is a 22.x fact")
        geo = self.s.ensure_geo("asmapi_fc")
        fc = self.s.create("filecache", "asmapi_fc_cache", geo)
        names = set(self.s.parm_names(fc.path()))
        self.assertIn("sopoutput", names)
        self.assertIn("f1", names)
        self.assertIn("trange", names)
        for wrong in ("outputname", "outputpath", "t1", "t2", "name"):
            self.assertNotIn(wrong, names, f"filecache has no {wrong} parm")
        items = fc.parm("trange").menuItems()
        self.assertIn("normal", items, "trange is a menu; set its item value")

    def test_scatter_and_copytopoints_parms(self):
        geo = self.s.ensure_geo("asmapi_sp")
        sc = self.s.create("scatter", "asmapi_scatter", geo)
        names = set(self.s.parm_names(sc.path()))
        self.assertTrue({"npts", "seed"} <= names, "scatter count parm is npts")
        for wrong in ("perpoint", "mindist", "usedist", "poisson"):
            self.assertNotIn(wrong, names, f"scatter has no {wrong} parm")
        ctp = self.s.create("copytopoints", "asmapi_ctp", geo)
        ctnames = set(self.s.parm_names(ctp.path()))
        self.assertTrue({"pack", "pivot"} <= ctnames)
        for wrong in ("pastepoints", "pasteprims"):
            self.assertNotIn(wrong, ctnames, f"copytopoints has no {wrong} parm")

    def test_karma_parms_and_no_xpu_parm(self):
        if "karma" not in self._rop_types():
            self.skipTest("no karma ROP on this build")
        rop = self.s.create("karma", "asmapi_karma", "/out")
        names = set(self.s.parm_names(rop.path()))
        self.assertTrue({"picture", "samplesperpixel", "resolutionx", "resolutiony",
                         "trange", "f1", "f2", "execute"} <= names)
        for wrong in ("renderer", "rendermethod", "schedule", "vm_renderer"):
            self.assertNotIn(wrong, names,
                             f"karma has no {wrong} parm - do not add it back to the table")
        self.assertIn("normal", rop.parm("trange").menuItems())

    def test_mantra_absent_on_22(self):
        if self.series < (22, 0):
            self.skipTest("mantra removal is a 22.x fact")
        self.assertNotIn("mantra", self._rop_types(),
                         "mantra ROP is gone on 22.x; the rendering skill must say so")

    def test_usd_rop_parms(self):
        if "usd" not in self._rop_types():
            self.skipTest("no USD Output ROP on this build")
        rop = self.s.create("usd", "asmapi_usd", "/out")
        names = set(self.s.parm_names(rop.path()))
        self.assertTrue({"loppath", "lopoutput", "execute", "trange", "f1", "f2"} <= names)
        self.assertIn("normal", rop.parm("trange").menuItems(),
                      "the skill sets trange='normal'; the menu must still offer it")
        self.assertEqual(rop.parm("lopoutput").rawValue(), "$HIP/usd/$OS.usd",
                         "the documented default changed")

    def test_lop_types(self):
        lop = set(k.lower() for k in self.hou.lopNodeTypeCategory().nodeTypes())
        self.assertIn("sopimport", lop)
        self.assertIn("sublayer", lop)
        for wrong in ("stageimport", "componentlist", "addloplattriblayer"):
            self.assertNotIn(wrong, lop, f"{wrong} is not a LOP type; docs must not claim it")


@REAL_ONLY
class TestLargeScene(unittest.TestCase):
    """Performance ceiling tests (the goal's 'large scene performance' item).

    Not a benchmark - a guard. The two wrapper paths that would first hurt a real
    scene are the node-tree walk behind find()/destroy_matching() and the
    attribute reads behind geometry_stats(). An accidentally quadratic change
    should fail here rather than in someone's viewport.
    """

    def setUp(self):
        self.s = _session()
        self.s.destroy_matching("/obj/asmbig_*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asmbig_*")

    def test_dense_mesh_stats_stay_under_a_ceiling(self):
        import time
        from skills import import_skill_module
        sop = import_skill_module("sop-automation")
        t0 = time.perf_counter()
        result = sop.run("grid_bevel", {"container": "asmbig_grid", "size": [10, 0, 10],
                                        "divisions": 200, "bevel_width": 0.01})
        grid_stats = sop.run("geo_stats", {"path": result["grid"]})
        stats = sop.run("geo_stats", {"path": result["bevel"]})
        elapsed = time.perf_counter() - t0
        self.assertEqual(result["unresolved_parms"], {}, "200 divisions must bind")
        # rows=cols=200 point counts -> 199x199 quads, from the verified parm probe.
        self.assertEqual(grid_stats["prim_count"], 199 * 199,
                         f"expected 39601 quads, got {grid_stats['prim_count']}")
        self.assertGreater(stats["prim_count"], 30000,
                           f"bevel output is not dense: {stats['prim_count']} prims")
        self.assertLess(elapsed, 60.0,
                        f"40k-prim build + stats took {elapsed:.1f}s; the wrapper or "
                        "cook path got slower - profile before merging")

    def test_node_search_over_200_nodes_stays_linear(self):
        """find() walks every child once; a nested walk would blow up here."""
        import time
        from skills import import_skill_module
        sop = import_skill_module("sop-automation")
        geo = self.s.ensure_geo("asmbig_net")
        for i in range(200):
            self.s.create("null", f"asmbig_n{i:03d}", geo)
        t0 = time.perf_counter()
        found = sop.run("find", {"pattern": "/obj/asmbig_net/asmbig_n*", "limit": 500})
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(found["matches"]), 200, "every node must be found")
        self.assertLess(elapsed, 10.0,
                        f"200-node search took {elapsed:.1f}s - check for a nested walk")
        t0 = time.perf_counter()
        gone = self.s.destroy_matching("/obj/asmbig_net/asmbig_n*")
        self.assertLess(time.perf_counter() - t0, 10.0, "bulk delete went quadratic")
        self.assertGreaterEqual(len(gone), 200,
                                f"destroy_matching reported {len(gone)} of 200")


class TestIntrospectionReality(unittest.TestCase):
    """Type-level introspection is not instance truth - proven, not assumed.

    Found while writing tools/fact_sheet.py: I nearly shipped a parm validator built
    on NodeType templates. On 22.0.368 those templates do NOT contain parms the
    framework adds at instance time - grid `sizex`/`sizey`, File Cache `sopoutput`/
    `f1`/`f2`, Karma `f1`/`resolutionx`, USD ROP `mkpath` are all absent from the type
    and present on a real node. A validator built on them rejects parms that exist.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou

    def tearDown(self):
        self.s.destroy_matching("/obj/asmi_*")
        self.s.destroy_matching("/out/asmi_*")

    def test_opnodetype_api_names(self):
        node_type = self.hou.sopNodeTypeCategory().nodeTypes()["grid"]
        self.assertTrue(hasattr(node_type, "parmTemplates"), "parmTemplates gone")
        self.assertTrue(hasattr(node_type, "parmTemplateGroup"))
        self.assertFalse(hasattr(node_type, "parmTemplateSet"),
                         "parmTemplateSet now exists on OpNodeType - doc is stale")

    def test_type_templates_under_report_instance_parms(self):
        declared = {t.name() for t
                    in self.hou.sopNodeTypeCategory().nodeTypes()["grid"].parmTemplates()}
        geo = self.s.ensure_geo("asmi_tpl")
        grid = self.s.create("grid", "asmi_tpl_grid", geo)
        real = set(self.s.parm_names(grid.path()))
        self.assertIn("sizex", real, "grid lost sizex - bigger doc problem than this one")
        self.assertNotIn("sizex", declared,
                         "type templates now declare sizex; the under-report fact changed")

    def test_hipfile_dirty_check_is_hasUnsavedChanges(self):
        self.assertTrue(hasattr(self.hou.hipFile, "hasUnsavedChanges"))
        for wrong in ("dirty", "modified", "suppressSaveOnExit"):
            self.assertFalse(hasattr(self.hou.hipFile, wrong),
                             f"hou.hipFile.{wrong} exists now - update the doc")

    def test_parm_menu_api_is_menuitems_only(self):
        geo = self.s.ensure_geo("asmi_menu")
        wrangle = self.s.create("attribwrangle", "asmi_menu_w", geo)
        parm = wrangle.parm("class")
        items = parm.menuItems()
        self.assertTrue(items and all(isinstance(x, str) for x in items),
                        "parm.menuItems() no longer returns flat strings")
        for wrong in ("menuItemIdentifier", "menuItemLabels", "menuItemValues"):
            self.assertFalse(hasattr(parm, wrong),
                             f"hou.Parm.{wrong} exists now - the doc says it does not")

    def test_node_type_names_collide_across_categories(self):
        """`karma` is a ROP *and* a LOP type; 33 names are shared by SOP and LOP.
        Any type lookup by bare name must say which category it meant."""
        sop = set(self.hou.sopNodeTypeCategory().nodeTypes())
        lop = set(self.hou.lopNodeTypeCategory().nodeTypes())
        rop = set(self.hou.ropNodeTypeCategory().nodeTypes())
        self.assertIn("karma", rop)
        self.assertIn("karma", lop, "karma is no longer a LOP type - collision note stale")
        self.assertIn("copytopoints", sop & lop)
        self.assertIn("fetch", rop, "the ROP fetch driver was renamed again")
        for gone in ("fetchnetwork", "driver"):
            self.assertNotIn(gone, rop, f"{gone} appeared in /out - recheck the table")


@REAL_ONLY
class TestSchemaEnumsMatchRealMenus(unittest.TestCase):
    """A schema enum the running build cannot honour is a lie in the contract.

    A live run on 22.0.368 took wrangle_kind='volume' straight through JSON Schema
    and only failed at the node's own menu, so the enum has to stay inside what the
    menu offers - and the resolver has to keep rejecting what the menu lacks.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asm_enumchk*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asm_enumchk*")

    def test_wrangle_kind_enum_resolves_against_the_class_menu(self):
        import json
        vex = import_skill_module("vex-helper")
        meta_path = os.path.join(ROOT, "skills", "vex-helper", "meta.json")
        with open(meta_path, encoding="utf-8") as fh:
            enum = json.load(fh)["actions"]["validate"]["properties"]["wrangle_kind"]["enum"]
        geo = self.hou.node("/obj").createNode("geo", "asm_enumchk")
        try:
            wrangle = geo.createNode("attribwrangle")
            menu = list(wrangle.parm("class").menuItems())
            self.assertTrue(menu, "the wrangle class menu came back empty")
            for value in enum:
                try:
                    index, seen = vex._class_index(wrangle, value)
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"enum value {value!r} does not resolve on "
                              f"{self.s.version} (menu={menu}): {exc}")
                self.assertIsInstance(index, int)
            for bogus in ("volume", "not-a-class"):
                with self.assertRaises(vex.SkillFailure) as ctx:
                    vex._class_index(wrangle, bogus)
                self.assertEqual(ctx.exception.err.kind, "parm_choice_unavailable")
                self.assertIn("menu items", ctx.exception.err.hint or "",
                              "the error should name the real choices")
        finally:
            geo.destroy()

    def test_hou_module_has_no_setcurrentnode(self):
        """Restoring a *network editor view* is a pane operation, not a hou module call."""
        self.assertFalse(hasattr(self.hou, "setCurrentNode"),
                         "hou.setCurrentNode exists now - the KB entry is stale")


@REAL_ONLY
class TestCreatedNodeAccounting(unittest.TestCase):
    """A builder in someone else's live scene must be undoable by its own payload.

    Live smoke runs left `/obj/<container>_dopnet` objects behind because the
    dopnet lives outside the container the caller named. `created_nodes` closes
    that gap; this test destroys via the payload and proves nothing remained.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.sim = import_skill_module("simulation-tools")

    def tearDown(self):
        for n in self.hou.node("/obj").children():
            if n.name().startswith("asm_t_dop"):
                n.destroy()

    def test_created_nodes_covers_everything_built(self):
        out = self.sim.run("rbd_fall", {"container": "asm_t_dop", "frames": [1, 10],
                                        "voronoi_points": 8, "lean_degrees": 9,
                                        "drop_distance": 0.5})
        made = out["created_nodes"]
        self.assertIn("/obj/asm_t_dop", made)
        # The dead dopnet is gone. Verified on 22.0.368: pyrosolver/flipsolver/
        # rbdbulletsolver instances expose no dop-related parm at all, so the dopnet
        # the builders used to create was an empty object nothing drove (its only
        # child was `file_output`) while the payload called it "dop_link: unresolved".
        self.assertEqual([p for p in made if p.endswith("_dopnet")], [],
                         "a simulation builder created an unused dopnet again")
        # every node the result points at must be in the undo list
        for key in ("fracture_node", "pack"):
            if out.get(key):
                self.assertIn(out[key], made, f"{key} missing from created_nodes")
        for path in made:
            self.assertIsNotNone(self.hou.node(path), f"{path} reported but absent")

        # destroy in reverse order (children before parents) and verify the scene
        # is clean of this action's work
        for path in sorted(set(made), reverse=True):
            node = self.hou.node(path)
            if node is not None:
                node.destroy()
        left = [p for p in made if self.hou.node(p) is not None]
        self.assertEqual(left, [], "destroying created_nodes left: %s" % left)

    def test_usd_export_reports_rop_and_lop(self):
        usd = import_skill_module("usd-pipeline")
        out = self.sim.run("rbd_fall", {"container": "asm_t_dop2", "frames": [1, 5],
                                        "voronoi_points": 4})
        sop_src = out["fracture_chain"][0]
        path = os.path.join(tempfile.gettempdir(), "houdini_agent_skills_acct.usda")
        try:
            res = usd.run("export", {"path": sop_src, "output": path,
                                     "container": "asm_t_dop2_usd"})
        finally:
            self.s.destroy_matching("/obj/asm_t_dop2*")
            self.s.destroy_matching("/out/asm_t_dop2*")
            self.s.destroy_matching("/stage/asm_t_dop2*")
        made = res["created_nodes"]
        self.assertIn("/out/asm_t_dop2_usd", made)
        self.assertTrue(any("/stage" in p for p in made),
                        "the LOP it made is missing from created_nodes: %s" % made)
        for p in made:
            self.assertIsNone(self.hou.node(p), "cleanup left %s alive" % p)

    def test_grid_bevel_reports_container_and_nodes(self):
        sopmod = import_skill_module("sop-automation")
        try:
            res = sopmod.run("grid_bevel", {"container": "asm_t_dop3",
                                            "size": [1.0, 1.0], "divisions": 2,
                                            "bevel_width": 0.02})
            made = res["created_nodes"]
            self.assertIn("/obj/asm_t_dop3", made)
            self.assertIn(res["grid"], made)
            self.assertIn(res["bevel"], made)
        finally:
            self.s.destroy_matching("/obj/asm_t_dop3*")


@REAL_ONLY
class TestDiagnosticsSurface(unittest.TestCase):
    """diagnose_node / empty_geo_report / cache_sop, exercised on wired chains.

    The inputState() crash only appears on a node that HAS an input, and the mute
    check only matters when a chain is broken - so both escaped the earlier tests.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.troub = import_skill_module("troubleshooting")
        self.sim = import_skill_module("simulation-tools")
        self.sopmod = import_skill_module("sop-automation")
        self.s.destroy_matching("/obj/asm22_diag*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asm22_diag*")

    def _chain(self, container):
        return self.sopmod.run("grid_bevel", {"container": container,
                                              "size": [1.0, 1.0], "divisions": 2,
                                              "bevel_width": 0.02})

    def test_diagnose_node_reads_a_wired_input(self):
        built = self._chain("asm22_diag")
        out = self.troub.run("diagnose_node", {"path": built["bevel"]})
        self.assertEqual(len(out["inputs"]), 1, out["inputs"])
        self.assertEqual(out["inputs"][0]["path"], built["grid"])
        self.assertIsNotNone(out["inputs"][0]["output_prims"])
        self.assertGreater(out["inputs"][0]["output_prims"], 0)
        self.assertIn("input_state_note", out)

    def test_empty_geo_report_localises_the_loss(self):
        self.s.ensure_geo("asm22_diag2")
        # A Null SOP passes geometry through, so the empty node must have no input
        empty = self.s.create("null", "asm22_diag2_empty", "/obj/asm22_diag2")
        rep = self.troub.run("empty_geo_report", {"path": empty.path()})
        checks = [f["check"] for f in rep["findings"]]
        self.assertIn("zero_prims", checks, rep["findings"])
        self.assertNotIn("wrong_category", checks,
                         "hou names the SOP category 'Sop'; this compare went "
                         "case-sensitive and flagged every real SOP")

        # feed it from a node that is itself empty: the blame must move upstream
        src = self.s.create("null", "asm22_diag2_src", "/obj/asm22_diag2")
        empty.setInput(0, src)
        rep2 = self.troub.run("empty_geo_report", {"path": empty.path()})
        checks2 = [f["check"] for f in rep2["findings"]]
        self.assertIn("input_cook", checks2, checks2)
        self.assertIn("input_empty", checks2, checks2)
        self.assertIsNone(rep2.get("first_non_empty"))
        self.assertIn("not_checked", rep2, "the report must say what it cannot check")

    def test_node_type_lookup_scopes_to_the_named_network(self):
        out = self.troub.run("node_type_lookup", {"name": "karma", "network": "/out"})
        cats = out["categories"]
        self.assertEqual(out["network_child_category"], "Driver",
                         "hou calls the render category Driver, not ROP")
        # karma is a type name in Driver AND Lop; only Driver is creatable in /out
        self.assertIn("karma", cats["Driver"]["exact"], sorted(cats))
        self.assertIn("karma", cats["Lop"]["exact"], "the Driver/Lop collision vanished")
        self.assertTrue(cats["Driver"]["creatable_here"])
        self.assertFalse(cats["Lop"]["creatable_here"],
                         "name-only scoping would mark both categories valid")
        geo = self.s.ensure_geo("asm22_ntl")
        scoped = self.troub.run("node_type_lookup", {"name": "filecache",
                                                    "network": geo})
        self.assertEqual(scoped["network_child_category"], "Sop")
        self.assertTrue(scoped["categories"]["Sop"]["creatable_here"])
        self.assertFalse(scoped["categories"]["Lop"]["creatable_here"],
                         "filecache is a type name in BOTH Sop and Lop")
        self.assertIn("categories_searched", out)

    def test_cache_sop_configures_without_writing(self):
        built = self._chain("asm22_diag3")
        target = os.path.join(tempfile.gettempdir(), "asm22_diag.$F4.bgeo.sc")
        out = self.sim.run("cache_sop", {"path": built["bevel"], "cache_path": target,
                                         "frames": [1, 3]})
        self.assertIs(out["written"], False, "cache_sop wrote a file without being asked")
        self.assertIn(out["file_cache"], out["created_nodes"])
        self.assertIn("sopoutput", [v["parm"] for v in out["applied"].values()])
        strays = [f for f in os.listdir(tempfile.gettempdir())
                  if f.startswith("asm22_diag") and f.endswith(".bgeo.sc")]
        self.assertEqual(strays, [], "cache files appeared: %s" % strays)


class TestHoudini22ApiSpellings(unittest.TestCase):
    """Names that 19.5 scripts rely on, proven absent on 22.0.368.

    Every one of these failed inside a bare `except` or a `hasattr` guard before it
    was caught, which is why the code looked like it worked. If Houdini adds a name
    back, these tests fire and the docs get updated.

    Note: `.eval()` below is Houdini's hou.Parm/hou.ParmTuple value getter (the only
    way to read a tuple on this build), not Python's eval() - no arbitrary code runs.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asm22_*")

    def tearDown(self):
        self.s.destroy_matching("/obj/asm22_*")
        self.s.destroy_matching("/out/asm22_*")
        self.s.destroy_matching("/stage/asm22_*")

    def test_parm_value_accessors(self):
        geo = self.hou.node("/obj").createNode("geo", "asm22_parm")
        w = geo.createNode("attribwrangle")
        parm, tup = w.parm("class"), w.parmTuple("class")
        self.assertTrue(hasattr(parm, "rawValue") and hasattr(parm, "eval"))
        self.assertEqual(parm.rawValue(), "point", "menu parms must report the identifier")
        self.assertEqual(parm.eval(), 2, "menu eval must report the index")
        for gone in ("val", "vals", "getValue"):
            self.assertFalse(hasattr(parm, gone), f"hou.Parm.{gone} is back - recheck docs")
        # ParmTuple reads through eval(); rawValue/vals/parms do not exist on it.
        self.assertEqual(tup.eval(), (2,))
        for gone in ("rawValue", "vals", "val", "parms"):
            self.assertFalse(hasattr(tup, gone), f"hou.ParmTuple.{gone} is back - recheck docs")

    def test_bounding_box_is_lowercase_vec(self):
        geo = self.hou.node("/obj").createNode("geo", "asm22_bb")
        grid = geo.createNode("grid")
        b = grid.geometry().boundingBox()
        self.assertTrue(hasattr(b, "minvec") and hasattr(b, "maxvec"))
        for gone in ("minVec", "maxVec"):
            self.assertFalse(hasattr(b, gone), f"hou.BoundingBox.{gone} is back - revert docs")
        stats = self.s.geometry_stats(grid.path())
        self.assertIn("framed_bounds", stats, "geometry_stats lost its bounds proof")
        self.assertNotIn("bounds_error", stats)

    def test_xform_uses_tuple_children_not_rotatex(self):
        geo = self.hou.node("/obj").createNode("geo", "asm22_xf")
        box = geo.createNode("box")
        box.parm("sizex").set(1.0); box.parm("sizey").set(3.0); box.parm("sizez").set(1.0)
        xf = geo.createNode("xform"); xf.setInput(0, box)
        before = [list(v) for v in (xf.geometry().boundingBox().minvec(),
                                    xf.geometry().boundingBox().maxvec())]
        self.assertIsNone(xf.parm("rotatex"),
                          "xform gained rotatex again - the lean fallback list can shrink")
        self.assertTrue(xf.parm("rx") is not None and xf.parm("ty") is not None)
        xf.parm("rx").set(12.0)
        xf.parm("ty").set(1.0)
        after = [list(v) for v in (xf.geometry().boundingBox().minvec(),
                                   xf.geometry().boundingBox().maxvec())]
        self.assertNotEqual(before, after, "rx/ty must actually move geometry")

    def test_scatter_counts_points_through_npts(self):
        geo = self.hou.node("/obj").createNode("geo", "asm22_sc")
        box = geo.createNode("box")
        sc = geo.createNode("scatter"); sc.setInput(0, box)
        self.assertIsNone(sc.parm("number"), "scatter has a `number` parm again")
        sc.parm("npts").set(7)
        self.assertEqual(len(sc.geometry().points()), 7, "npts no longer drives the count")
        menu = list(sc.parm("generateby").menuItems())
        self.assertEqual(menu, ["bydensity", "countperprimitive", "texturespace"],
                         "scatter generateby menu changed on this build")

    def test_pane_tab_type_only_has_name(self):
        ne = self.hou.paneTabType.NetworkEditor
        self.assertEqual(ne.name(), "NetworkEditor")
        for gone in ("internalName", "internalType", "enumerationName"):
            self.assertFalse(hasattr(ne, gone), f"PaneTabType.{gone} is back - update the doc")
        # hou.ui exists only in a GUI product; hython has no panes at all.
        if not hasattr(self.hou, "ui"):
            self.skipTest("hou.ui is absent in hython; pane facts are GUI-only")

    def test_pxr_spellings_used_by_verify(self):
        from pxr import Sdf, Usd
        self.assertTrue(hasattr(Usd.Stage, "Traverse") and hasattr(Usd.Stage, "TraverseAll"))
        for gone in ("TraverseAllPims", "TraverseAllPrims"):
            self.assertFalse(hasattr(Usd.Stage, gone),
                             f"Usd.Stage.{gone} exists now - recheck the verify helper")
        self.assertTrue(hasattr(Usd.Prim, "GetTypeName"),
                        "Usd.Prim.GetTypeName is gone - the proof helper needs a new name")
        self.assertFalse(hasattr(Usd.Prim, "GetType"),
                         "Usd.Prim.GetType exists now - the camelCase habit would work")
        path = Sdf.Path("/world/thing")
        self.assertEqual(path.pathString, "/world/thing")
        self.assertFalse(hasattr(path, "pathname"),
                         "Sdf.Path.pathname exists now - the str() conversion note can go")

    def test_category_names_are_hous_not_the_ui_labels(self):
        cats = self.hou.nodeTypeCategories()
        self.assertNotIn("ROP", cats, "hou keys the render category 'Driver'")
        self.assertNotIn("OBJ", cats, "hou keys it 'Object'")
        for key in ("Sop", "Object", "Lop", "Driver", "Dop", "Cop", "Cop2", "Data",
                    "Chop", "Shop", "Top", "TopNet", "Vop", "VopNet", "CopNet",
                    "ChopNet", "Director", "Manager"):
            self.assertIn(key, cats, sorted(cats))
        self.assertEqual(self.hou.ropNodeTypeCategory().name(), "Driver")
        self.assertEqual(self.hou.sopNodeTypeCategory().name(), "Sop")
        self.assertIn("karma", cats["Driver"].nodeTypes())
        self.assertIn("karma", cats["Lop"].nodeTypes())
        self.assertIn("filecache", cats["Sop"].nodeTypes())
        self.assertIn("filecache", cats["Lop"].nodeTypes())
        # a node's category name is 'Sop'/'Object', never the UI label 'SOP'/'OBJ'
        geo_path = self.s.ensure_geo("asm22_cat")
        self.assertEqual(self.hou.node(geo_path).type().category().name(), "Object")
        self.assertEqual(self.hou.node(geo_path).childTypeCategory().name(), "Sop")
        self.assertEqual(self.hou.node("/out").childTypeCategory().name(), "Driver")
        self.assertEqual(self.hou.node("/stage").childTypeCategory().name(), "Lop")

    def test_nodetype_name_accessors(self):
        # 22.0.368: NodeType/OpNodeType instances expose name() and description() only.
        # internalName(), typeName(), label(), knownTypeNames(), supplements() and
        # allSupplements() raise AttributeError - the internal name IS name().
        # NodeTypeCategory is different again: name() yes; keyName()/internalName()/
        # description() no.
        geo_path = self.s.ensure_geo("asm22_ntapi")
        grid = self.s.create("grid", "asm22_grid", geo_path)
        t = grid.type()
        self.assertEqual(t.name(), "grid")
        self.assertTrue(t.description())
        for missing in ("internalName", "typeName", "label", "knownTypeNames",
                        "supplements", "allSupplements"):
            self.assertFalse(hasattr(t, missing),
                             "hou.NodeType.%s() exists now - update the KB entry" % missing)
        cat = self.hou.nodeTypeCategories()["Sop"]
        self.assertEqual(cat.name(), "Sop")
        # NodeTypeCategory has name()/label()/typeName(); NodeType instances do not label
        self.assertEqual(cat.label(), "Geometry")
        self.assertTrue(cat.typeName())
        for missing in ("keyName", "internalName", "description"):
            self.assertFalse(hasattr(cat, missing),
                             "hou.NodeTypeCategory.%s() exists now" % missing)
        self.assertEqual(self.hou.nodeTypeCategories()["Driver"].label(), "Outputs",
                         "the /out tab Jake sees is the Driver category's label")
        self.assertEqual(self.hou.nodeTypeCategories()["Lop"].label(), "Solaris")
        # the honest way to ask "can I build this here": childTypeCategory()
        self.assertIn("filecache", self.hou.node(geo_path).childTypeCategory().nodeTypes())
        self.assertNotIn("filecache", self.hou.node("/out").childTypeCategory().nodeTypes(),
                         "filecache is a Sop type, not a Driver type")
        self.hou.node(geo_path).destroy()

    def test_input_state_accessors_absent(self):
        hou = self.hou
        for name in ("inputState", "inputOutputState", "inputIsMuted",
                     "isInputMuted", "inputType"):
            self.assertFalse(hasattr(hou.SopNode, name),
                             "hou.SopNode.%s() exists after all - the mute-state "
                             "diagnostic can come back" % name)
        for name in ("inputLabels", "inputs", "inputConnections"):
            self.assertTrue(hasattr(hou.Node, name), "hou.Node.%s vanished" % name)
        self.assertFalse(hasattr(hou, "NodeConnector"),
                         "hou.NodeConnector is back; the rename note can go")
        self.assertTrue(hasattr(hou, "NodeConnection"))

    def test_sop_level_solvers_have_no_dop_parm_and_named_inputs(self):
        """The builders no longer create a dopnet, and this pins why.

        Live 22.0.368 read of the SOP solver instances: no dop-related parm exists,
        and each solver names its own inputs. The FLIP/Pyro builders rely on that.
        """
        sim = import_skill_module("simulation-tools")
        geo = self.s.ensure_geo("asm22_solverfacts")
        try:
            for solver_type, wanted in (("flipsolver", ["Sources", "Container",
                                                        "Collisions", "Boundary Flow"]),
                                        ("pyrosolver", ["Sources",
                                                        "Collision Geometry/Volumes"]),
                                        ("rbdbulletsolver", ["Geometry",
                                                             "Constraint Geometry",
                                                             "Proxy Geometry",
                                                             "Collision Geometry",
                                                             "Guide Sim"])):
                node = self.hou.node(geo).createNode(solver_type, "probe_" + solver_type)
                try:
                    wiring = sim._solver_wiring(node)
                    self.assertEqual(wiring["input_labels"], wanted,
                                     f"{solver_type} input labels moved")
                    self.assertEqual(wiring["dop_parms"], [],
                                     f"{solver_type} grew a dop parm - revisit the builders")
                    self.assertTrue(wiring["cache_parms"],
                                    f"{solver_type} exposes no cache knobs")
                    self.assertFalse(wiring["labels_from_probe_only"])
                finally:
                    node.destroy()
        finally:
            self.s.destroy_matching("/obj/asm22_solverfacts*")

    def test_set_radius_uses_the_parm_tuple_and_reads_back(self):
        """node.parm('rad') is None on a Sphere; the tuple is the only way in."""
        sim = import_skill_module("simulation-tools")
        geo = self.s.ensure_geo("asm22_rad")
        try:
            sphere = self.hou.node(geo).createNode("sphere", "probe_sphere")
            self.assertIsNone(sphere.parm("rad"),
                              "rad stopped being a ParmTuple - _set_radius can simplify")
            self.assertEqual(sphere.parmTuple("rad").eval(), (1.0, 1.0, 1.0))
            report = sim._set_radius(sphere, 0.5)
            self.assertTrue(report["bound"])
            self.assertEqual(report["kept"], [0.5, 0.5, 0.5],
                             "_set_radius reported a value hou did not keep")
            # and the child parms are the same numbers, read individually
            self.assertEqual([sphere.parm(n).eval() for n in ("radx", "rady", "radz")],
                             [0.5, 0.5, 0.5])
        finally:
            self.s.destroy_matching("/obj/asm22_rad*")

    def test_flip_builder_makes_a_real_solver_and_no_dopnet(self):
        """live pass 4 found the FLIP build empty: dopnet with one child, unresolved
        link, no read-backs. This pins the repaired shape."""
        sim = import_skill_module("simulation-tools")
        out = sim.run("flip", {"container": "asm22_flipv2", "site_count": 40,
                               "frames": [1, 12]})
        try:
            self.assertEqual([p for p in out["created_nodes"]
                              if p.endswith("_dopnet")], [])
            self.assertEqual(out["solver_type"], "flipsolver")
            self.assertEqual(out["site_points"], 40,
                             "site_count never reached the Scatter SOP")
            self.assertEqual(out["parm_report"]["flip_src.rad"]["kept"],
                             [0.5, 0.5, 0.5])
            self.assertEqual(out["parm_report"]["flip_points.npts"]["kept"], 40)
            self.assertEqual(out["solver_wiring"]["input_labels"][0], "Sources")
            self.assertEqual(out["solver_wiring"]["dop_parms"], [])
            # the FLIP Source SOP's single prim is a VDB volume, not lost geometry
            self.assertEqual(out["source_stats"]["prim_types"], {"VDB": 1},
                             "FLIP Source output shape moved - recheck what counts as a "
                             "broken source chain")
            self.assertIn("simulation not advanced", out["not_done"])
        finally:
            self.s.destroy_matching("/obj/asm22_flipv2*")

    def test_find_is_a_path_glob_not_a_substring(self):
        self.s.ensure_geo("asm22_find")
        try:
            hits = self.s.find("/obj/asm22_find*", 20)
            self.assertTrue(any(h["path"] == "/obj/asm22_find" for h in hits), hits)
            self.assertEqual(self.s.find("asm22_find", 20), [],
                             "find() started matching substrings - docs say path glob")
        finally:
            self.s.destroy_matching("/obj/asm22_find*")

    def test_playbar_range_is_a_vector2(self):
        rng = self.hou.playbar.frameRange()
        self.assertEqual(type(rng).__name__, "Vector2")
        for guessed in ("start", "end"):
            self.assertFalse(hasattr(rng, guessed),
                             "hou.playbar.frameRange() %s() exists after all" % guessed)
        self.assertLessEqual(float(rng[0]), float(rng[1]))

    def test_node_type_categories_cover_pyro(self):
        probe = import_skill_module("simulation-tools")
        out = probe.run("probe_types", {"contains": "pyro"})
        cats = out["by_category"]
        self.assertIn("Dop", cats, "pyro solver types vanished from the Dop category")
        self.assertIn("Sop", cats, "pyro SOP types vanished")
        self.assertIn("pyrosolver", [t["type"] for t in out["types"] if t["category"] == "Sop"]
                      + cats["Dop"]["sample"])
        self.assertTrue(out["categories_searched"], "must say what it searched")


@REAL_ONLY
class TestUsdExportLandsGeometry(unittest.TestCase):
    """`lopoutput` alone is not proof of geometry on 22.0.368.

    A `usd` ROP pointed at a SOP Import LOP writes a layer whose only content is a
    subLayers reference to a layer the LOP generated somewhere else, so the requested
    file can exist, be non-empty, and hold nothing. The skill now binds the ROP's own
    `flattensoplayers` and reports what the file really contains.
    """

    def setUp(self):
        self.s = _session()
        self.hou = self.s.hou
        self.s.destroy_matching("/obj/asm22usd_*")
        self.s.destroy_matching("/out/asm22usd_*")
        self.s.destroy_matching("/stage/asm22usd_*")
        self.usd = import_skill_module("usd-pipeline")

    def tearDown(self):
        self.s.destroy_matching("/obj/asm22usd_*")
        self.s.destroy_matching("/out/asm22usd_*")
        self.s.destroy_matching("/stage/asm22usd_*")

    def _grid(self):
        geo = self.hou.node("/obj").createNode("geo", "asm22usd_geo")
        return geo.createNode("grid")

    def test_export_pins_layers_next_to_output_and_proves_geometry(self):
        import tempfile
        grid = self._grid()
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "flat.usda")
            res = self.usd.run("export", {"path": grid.path(), "output": out,
                                          "container": "asm22usd_rop"})
            self.assertTrue(res["exists"])
            # The proof that matters: a prim carrying a `points` attribute in the
            # composed stage, matching the SOP source point count.
            self.assertTrue(res["stage"]["geometry_prims"],
                            "no prim with a points attribute in the exported stage")
            self.assertEqual(res["geometry_points_in_stage"], res["source_point_count"],
                             "USD point count must equal the SOP source point count")
            self.assertTrue(res["geometry_verified"])
            self.assertTrue(res["stage"]["self_contained"],
                            "the SOP layer must land beside the requested file: %s"
                            % res["stage"]["external_layer_files"])
            self.assertNotIn("warning", res)
            self.assertNotIn("portability_note", res)
            ver = self.usd.run("verify", {"output": out})
            self.assertGreaterEqual(ver["stage"]["prim_count"], 1,
                                    "verify must count prims, not just bytes")
            self.assertTrue(ver["stage"]["prim_types"], "verify must report prim types")
            self.assertTrue(ver["stage"]["geometry_prims"], "verify must find geometry")

    def test_keep_layers_together_off_reports_where_layers_live(self):
        import tempfile
        grid = self._grid()
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "split.usda")
            res = self.usd.run("export", {"path": grid.path(), "output": out,
                                          "container": "asm22usd_ptr",
                                          "keep_layers_together": False})
            self.assertTrue(res["exists"], "the pointer layer still gets written")
            # Geometry still composes - the layers are merely scattered under $HIP.
            self.assertEqual(res["geometry_points_in_stage"], res["source_point_count"])
            self.assertFalse(res["stage"]["self_contained"],
                             "Houdini's default $HIP/usd layer must be reported as external")
            self.assertIn("portability_note", res,
                          "a split export must say where the companion layers are")
            self.assertTrue(res["stage"]["external_layer_files"])
            # Remove only the exact companion file this run named, never a directory.
            for entry in res["stage"]["external_layer_files"]:
                path = entry.get("resolved")
                if path and entry.get("exists") and os.path.dirname(path) != td:
                    try:
                        os.remove(path)
                    except OSError:
                        pass


if __name__ == "__main__":
    unittest.main()
