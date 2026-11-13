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
        self.assertGreater(result["source_pieces"], 10)
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


if __name__ == "__main__":
    unittest.main()
