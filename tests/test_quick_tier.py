"""The quick tier, the smoke tier and plan recipes (estimate-watch DESIGN revision 2, sections 2 and 3).

quick is the estimate sized to about 15 minutes for one condition on the Sonnet baseline: for one target the
standard tier's Class I and the full tier's N0 errands; for the triangle the standard tier's role-free items and checks
and the role-free errands on 32 tasks; Class III and the authority rows never. smoke is the plan called quick until
2026-11-08, item for item. A run records its plan recipe, and a run.json from before recipes reads through the legacy
map at every read boundary without being edited. The time model has one table, measured where it says so. Nothing here
calls a model: plans are built from the pinned banks or the small fixture, and runs are planted.
"""
import ast
import contextlib
import csv
import hashlib
import importlib.util
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "calibration"))
sys.path.insert(0, str(REPO / "docs" / "ordo"))         # stage0 and quick_sim, for the gate's provenance
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import run as RUN  # noqa: E402  (the calibration harness's preflight parser)
import test_v4_scoring as S  # noqa: E402  (its planted-run helpers)

TRIANGLE = "individual:organization,organization:nation,individual:nation"
OLD_QUICK = {"v4": "005a5fcbfdc235e9e3c6c790fa3f108f3e13cd8c3f1b880ffce7f6f67fca6d67",      # 00de136's quick plans
             "ordo": "2da661c7423a89ade4236eb61ceea4f41b592fec26ec67bf458d000e34af78de"}    # (the pilot's Stage 1)


def real_bank():
    return P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)


def args(**kw):
    base = dict(agent="claude", model="claude-sonnet-5-5", effort="medium", jobs=4, tier="quick", memory="on")
    return SimpleNamespace(**dict(base, **kw))


class TestPlans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = real_bank()
        cls.roster = P.resolve_target(cls.bank, "organization", "Mozilla")
        cls.ordo = O.load_ordo_bank()

    def plan(self, tier, classes=P.V4_CLASSES):
        return P.plan_v4(self.bank, self.roster, tier, classes, ("with",))

    def test_one_target_is_standards_class_i_and_fulls_plain_errands(self):
        quick = self.plan("quick")
        q, e = [c for c in quick if not c.get("episode")], [c for c in quick if c.get("episode")]
        self.assertEqual((len(q), len(e)), (264, 96))
        self.assertEqual({c["meta"]["cell"] for c in e}, {"N0"})
        self.assertEqual({c["id"] for c in q}, {c["id"] for c in self.plan("standard") if not c.get("episode")})
        self.assertEqual({c["id"] for c in e},
                         {c["id"] for c in self.plan("full") if c.get("episode") and c["meta"]["cell"] == "N0"})

    def test_the_triangle_is_role_free_on_32_tasks(self):
        calls = O.plan(self.ordo, TRIANGLE, "quick")
        q, e = [c for c in calls if not c.get("episode")], [c for c in calls if c.get("episode")]
        self.assertEqual((len(q), len(e)), (276, 96))
        self.assertFalse(any(c["meta"].get("frame") == O.AUTHORITY for c in calls))
        self.assertEqual({c["meta"]["cell"] for c in e}, {"N0"})
        self.assertEqual(len({c["meta"]["task"] for c in e}), O.QUICK_TASKS["role_free"])
        self.assertNotIn("F2.tri", {c["meta"].get("component") for c in q})

    def test_smoke_is_todays_quick_item_for_item(self):
        self.assertEqual(P.plan_fingerprint(self.plan("smoke")), OLD_QUICK["v4"])
        self.assertEqual(P.plan_fingerprint(O.plan(self.ordo, TRIANGLE, "smoke")), OLD_QUICK["ordo"])

    def test_a_caller_naming_every_class_gets_the_tiers_plan(self):
        self.assertEqual(self.plan("quick"), self.plan("quick", ("I", "II")))
        self.assertFalse(any(c["meta"]["class"] == "III" for c in self.plan("quick")))
        self.assertFalse(any(c["meta"]["class"] == "III" for c in O.plan(self.ordo, TRIANGLE, "quick")))

    def test_class_iii_at_quick_is_refused_with_the_fix(self):
        base = ["--agent", "claude", "--target-type", "organization", "--target", "Mozilla", "--tier", "quick"]
        self.assertEqual(P.parse_args_v4(base).classes, ("I", "II"))
        self.assertEqual(P.parse_args_v4(base + ["--class", "all"]).classes, ("I", "II"))
        self.assertEqual(P.parse_args_v4(base + ["--class", "I"]).classes, ("I",))
        for named in ("III", "II,III", "I,II,III"):
            with self.assertRaises(SystemExit) as stop:
                P.parse_args_v4(base + ["--class", named])
            self.assertEqual(str(stop.exception), "a quick run does not run Class III; use --tier standard for it")
        self.assertEqual(P.parse_args_v4(base[:-1] + ["standard"]).classes, P.V4_CLASSES)


class TestRecipes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_recipe_of_a_run(self):
        self.assertEqual(P.recipe_of({"tier": "quick"}), "smoke@1")                 # before 2026-11-08
        self.assertEqual(P.recipe_of({"tier": "quick", "recipe": "quick@3"}), "quick@3")
        self.assertEqual(P.recipe_of({"tier": "quick", "recipe": "quick@2"}), "quick@2")     # retired, still read
        self.assertEqual(P.recipe_of({"tier": "smoke", "recipe": "smoke@1"}), "smoke@1")
        self.assertEqual([P.planner_tier(r) for r in P.PLAN_RECIPES.values()], list(P.PLAN_RECIPES))
        self.assertEqual(P.planner_tier("quick@2"), "quick")
        for bad in ({"tier": "quick", "recipe": "quick@1"}, {"tier": "standard", "recipe": "smoke@1"},
                    {"tier": "standard", "recipe": "quick@2"}, {"tier": "slim"}):
            with self.assertRaises(SystemExit, msg=bad):
                P.recipe_of(bad)

    MALFORMED = ("quick@4", None, "", 2, ["quick@3"])        # a present recipe is never read as legacy

    def test_a_present_but_malformed_recipe_is_refused_naming_the_field(self):
        for recipe in self.MALFORMED:
            with self.assertRaises(SystemExit, msg=repr(recipe)) as stop:
                P.recipe_of({"tier": "quick", "recipe": recipe})
            self.assertIn("run.json's recipe field holds", str(stop.exception))

    def test_a_malformed_recipe_is_refused_at_every_boundary(self):
        b, _, _, meta = S.planted_run(self.tmp / "run", tier="smoke", memory="on")
        base = {"target_type": "organization", "bank_sha256": P.BANK_MANIFEST_SHA256, "tier": "smoke",
                "recipe": "smoke@1"}
        for recipe in self.MALFORMED:
            bad = dict(meta, tier="quick", recipe=recipe)
            (self.tmp / "run" / "run.json").write_text(json.dumps(bad))
            for name, call in (("rescore", lambda: P.rescore(self.tmp / "run", b)),
                               ("check_resume", lambda: P.check_resume(self.tmp / "run", dict(meta))),
                               ("comparable", lambda: P.comparable(base, dict(base, tier="quick", recipe=recipe)))):
                with self.assertRaises(SystemExit, msg=(name, recipe)) as stop:
                    call()
                self.assertIn("recipe field", str(stop.exception), (name, recipe))

    def legacy_quick(self):
        """A planted smoke run whose run.json is written as a run made before recipes: tier quick, no recipe."""
        b, roster, calls, meta = S.planted_run(self.tmp / "run", tier="smoke", memory="on")
        legacy = dict(meta, tier="quick")
        del legacy["recipe"]
        (self.tmp / "run" / "run.json").write_text(json.dumps(legacy, indent=2))
        return b, roster, calls, legacy

    def test_a_legacy_quick_run_rescores_as_smoke(self):
        b, _, calls, legacy = self.legacy_quick()
        before = (self.tmp / "run" / "run.json").read_bytes()
        card = P.rescore(self.tmp / "run", b)
        self.assertEqual((card["tier"], card["recipe"], card["recorded_tier"]), ("smoke", "smoke@1", "quick"))
        self.assertEqual(card["plan_fingerprint"], legacy["plan_fingerprint"])
        self.assertIn("· smoke tier (recorded as quick)", (self.tmp / "run" / "scorecard.md").read_text())
        self.assertEqual((self.tmp / "run" / "run.json").read_bytes(), before)        # never edited

    def test_a_legacy_quick_folder_resumes_only_as_smoke(self):
        _, _, _, legacy = self.legacy_quick()
        before = (self.tmp / "run" / "run.json").read_bytes()
        P.check_resume(self.tmp / "run", dict(legacy, tier="smoke", recipe="smoke@1"))     # resumes
        with self.assertRaises(SystemExit) as stop:
            P.check_resume(self.tmp / "run", dict(legacy, tier="quick", recipe="quick@3"))
        self.assertEqual(str(stop.exception), f"{self.tmp / 'run'} holds a smoke@1 run (recorded as quick before "
                                              "2026-11-08); rerun with --tier smoke to resume it, or pass another --out")
        self.assertEqual((self.tmp / "run" / "run.json").read_bytes(), before)

    def test_new_runs_record_their_recipe(self):
        a = P.parse_args_v4(["--agent", "claude", "--target-type", "organization", "--target", "Mozilla",
                             "--tier", "quick"])
        b = real_bank()
        roster = P.resolve_target(b, "organization", "Mozilla")
        opts = SimpleNamespace(model=None, effort="medium", workdir="/tmp", features=[])
        meta = P.v4_meta(a, opts, b, roster, P.plan_v4(b, roster, "quick", a.classes, ("with",)), "x", {})
        self.assertEqual((meta["tier"], meta["recipe"], meta["classes"]), ("quick", "quick@3", ["I", "II"]))


class TestTimeModel(unittest.TestCase):
    def test_the_sonnet_rates_are_their_evidence(self):
        timing = json.loads((REPO / "docs" / "ordo" / "timing.json").read_text())
        price = P.pricing()
        for family, s in timing["question_seconds"].items():
            self.assertEqual(price["question_seconds"][family]["sonnet"], round(s["mean"], 2), family)
        for cell, s in timing["episode_seconds"].items():
            self.assertEqual(price["episode_seconds"]["sonnet"][cell], round(s["mean"], 2), cell)
        m = price["measured"]
        self.assertEqual((m["date"], m["model"], m["effort"], m["jobs"]),
                         (timing["date"], timing["model"], timing["effort"], timing["jobs"]))

    def test_the_quick_plans_take_about_15_minutes_on_the_baseline(self):
        b = real_bank()
        roster = P.resolve_target(b, "organization", "Mozilla")
        one = P.plan_minutes(P.plan_v4(b, roster, "quick", P.V4_CLASSES, ("with",)), "sonnet", 4)
        tri = P.plan_minutes(O.plan(O.load_ordo_bank(), TRIANGLE, "quick"), "sonnet", 4)
        self.assertAlmostEqual(one, 14.316, places=2)
        self.assertAlmostEqual(tri, 14.604, places=2)

    def test_every_estimate_says_what_its_rates_are(self):
        cases = {("claude", "claude-sonnet-5-5", "medium"): ("sonnet", "Sonnet baseline, measured 2026-11-08"),
                 ("claude", None, "medium"): ("sonnet", "Sonnet baseline rates; the CLI's default model shows in its "
                                                        "first reply"),
                 ("claude", "claude-opus-5-5", "medium"): ("opus", "Opus: assumed, not measured"),
                 ("claude", "claude-haiku-5-5", "medium"): ("sonnet", "Sonnet rates as a proxy, not measured for "
                                                                      "claude-haiku-5-5, effort medium"),
                 ("claude", "claude-sonnet-5-5", "high"): ("sonnet", "Sonnet rates as a proxy, not measured for "
                                                                     "claude-sonnet-5-5, effort high"),
                 ("codex", None, "medium"): ("codex", "Codex: assumed, not measured")}
        for (agent, model, effort), want in cases.items():
            self.assertEqual(P.rates_profile(agent, model, effort), want)


class TestPreflight(unittest.TestCase):
    def test_the_one_target_lines(self):
        b = real_bank()
        roster = P.resolve_target(b, "organization", "Mozilla")
        calls = P.plan_v4(b, roster, "quick", P.V4_CLASSES, ("with",))
        self.assertEqual(P.estimate_v4(args(), calls), [
            "Class I      264 questions    264 calls   about 4 min",
            "Class II      96 errands       96 calls   about 10 min",
            "all          360 items        360 calls   about 14 min (Sonnet baseline, measured 2026-11-08)"])
        both = P.plan_v4(b, roster, "quick", P.V4_CLASSES, ("without", "with"))
        self.assertTrue(P.estimate_v4(args(memory="both"), both)[-1].endswith(
            "about 29 min (Sonnet baseline, measured 2026-11-08)"))

    def test_the_tier_sentences(self):
        self.assertEqual(P.tier_sentence("quick", "on"),
                         " Classes I and II; Class III is not run (--tier standard runs it)")
        self.assertEqual(P.tier_sentence("quick", "on", single=False, cross=True),
                         " the role-free rows of Classes I and II; the authority rows and Class III are at "
                         "--tier standard")
        self.assertTrue(P.tier_sentence("quick", "both").endswith("; memory both asks everything twice"))
        for tier in ("smoke", "standard", "full"):
            self.assertEqual(P.tier_sentence(tier, "both"), "")

    def test_the_calibration_harness_still_reads_the_preflight(self):
        text = ("probatio v4: claude (2.1.309 (Claude Code)), model claude-sonnet-5-5, effort medium\n"
                "  quick tier, memory on, at --jobs 4: Classes I and II; Class III is not run (--tier standard runs it)\n"
                "    Class I      264 questions    264 calls   about 4 min\n"
                "    Class II      96 errands       96 calls   about 10 min\n"
                "    all          360 items        360 calls   about 14 min (Sonnet baseline, measured 2026-11-08)\n")
        self.assertEqual(RUN.preflight_settings(text),
                         {"model": "claude-sonnet-5-5", "effort": "medium", "jobs": 4, "planned": 360})


class TestScorecard(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def launch(out, started, ended, jobs=4):
        """A launch as the watch build's run_calls logs it: a start line (ended null), and an end line unless killed."""
        with (Path(out) / "launches.jsonl").open("a") as f:
            f.write(json.dumps({"started": started, "ended": None, "made": 0, "jobs": jobs}) + "\n")
            if ended:
                f.write(json.dumps({"started": started, "ended": ended, "made": 1, "jobs": jobs}) + "\n")

    def test_a_quick_card_says_what_it_left_out_and_how_long_it_ran(self):
        b, roster, calls, meta = S.planted_run(self.tmp / "run", tier="quick", memory="on", classes=("I", "II"))
        self.launch(self.tmp / "run", "2026-11-08T20:00:00.000Z", "2026-11-08T20:14:32.250Z")
        P.score_run(self.tmp / "run", calls, b, roster, meta, B=S.B_TEST)
        text = (self.tmp / "run" / "scorecard.md").read_text()
        self.assertIn("· memory on · quick tier\n" + P.QUICK_CARD_LINE + "\n", text)
        self.assertIn("Class III internalised  not run in a quick run\n", text)
        self.assertIn("Run time 14m 32s over 1 launch.\n", text)
        card = json.loads((self.tmp / "run" / "scorecard.json").read_text())
        self.assertEqual((card["tier"], card["recipe"]), ("quick", "quick@3"))
        self.assertEqual(card["run_time"], {"elapsed_seconds": 872.25, "launches": 1, "unfinished": 0})

    def test_the_run_time_is_the_launches_own(self):
        out = self.tmp / "run"
        out.mkdir()
        self.assertEqual(P.run_time_line(P.read_launches(out)), "Run time not recorded (this folder holds no "
                                                                "launches.jsonl).")
        self.launch(out, "2026-11-08T20:00:00Z", "2026-11-08T20:05:00Z")
        self.launch(out, "2026-11-08T21:00:00Z", None)          # a launch killed outright: its start alone
        self.launch(out, "2026-11-08T22:00:00Z", "2026-11-08T22:01:30Z")
        rt = P.read_launches(out)
        self.assertEqual(P.run_time_line(rt), "Run time 6m 30s over 3 launches; 1 launch recorded no end and is not "
                                              "counted.")


class TestOrdoQuick(unittest.TestCase):
    def test_the_head_names_what_a_quick_run_left_out(self):
        calls = O.plan(O.load_ordo_bank(), TRIANGLE, "quick", classes=("I",))
        units = []
        for c in calls:
            units += O.question_unit(c, {"error": None, "outcome": None})
        card = O.score_units(units, B=0, tier="quick")
        self.assertIn(O.QUICK_CARD_LINE, card["head"])
        self.assertTrue(card["head"][0].endswith("· quick tier"))
        absent = card["conditions"]["with"]["not_measured"]
        self.assertFalse(any(label.startswith("authority") or "Class III" in label for label in absent))

    def test_amendment_6_halves_the_role_free_errand_clusters(self):
        for value, ci, g in ((48.0, [35.0, 60.0], 16), (50.0, [40.0, 58.0], 32), (40.0, None, 8)):
            self.assertEqual(O.errand_interval(value, ci, g), O.small_cluster(value, ci, g // 2))
        self.assertIsNone(O.errand_interval(50.0, [42.0, 58.0], 3))            # G/2 = 1: no interval
        lo, hi = O.errand_interval(50.0, [42.0, 58.0], 16)                    # c 1.289 at 16 tasks, against 1.123
        self.assertAlmostEqual((hi - lo) / 16, 1.289, places=2)
        for rid in ("role-free.II", "role-free.III"):
            self.assertIs(O.interval_rule(rid), O.errand_interval)
        for rid in ("role-free.I", "authority.I", "authority.II"):
            self.assertIs(O.interval_rule(rid), O.small_cluster)          # Amendment 3, G whole


# ---- the errand rule's completion gate ------------------------------------------------------------------------------
GATED = re.compile(r"<!-- gated: errand rule -->(.*?)<!-- /gated -->", re.S)    # a claim that needs the rule adopted
STALE_PHRASES = (r"(?<![-\w])readable", r"too wide to rank")   # precision claims (not "machine-readable")
AMENDMENT_6_CODE = ["ordo.HALVED_ROWS", "ordo.answer_row", "ordo.errand_interval", "ordo.interval_rule"]


def class2_widths(sim):
    """Every Class II median width the allocation reports, as the documents print it (one decimal)."""
    rows = [e["null"]["II"]["median_raw_width"] for e in sim["one_target"].values()]
    rows += [e["null"]["role-free.II"]["median_width"] for e in sim["triangle"].values()]
    return {f"{w:.1f}" for w in rows if w is not None}


def stale_claims(text, status, sim):
    """The Class II precision claims a document should not carry under status: any allocation width or STALE_PHRASES
    outside its gated blocks (a claim that would outlive a PENDING gate), and, while the gate reads PENDING, any
    gated block at all."""
    outside = GATED.sub("", text)
    found = [p for p in STALE_PHRASES if re.search(p, outside)] + sorted(w for w in class2_widths(sim) if w in outside)
    if status != "adopted":
        found += [f"gated block: {b.strip()[:40]}" for b in GATED.findall(text)]
    return found


def top_level(src):
    """{name: source} of a module's top-level functions, classes and assigned names."""
    tree, out = ast.parse(src), {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            out[n.name] = ast.get_source_segment(src, n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                for x in (t.elts if isinstance(t, ast.Tuple) else [t]):
                    if isinstance(x, ast.Name):
                        out[x.id] = ast.get_source_segment(src, n)
    return out


def git_show(commit, path):
    """A file's bytes at a commit, or None where git or the commit is not available (a copy without history)."""
    got = subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{path}"], capture_output=True)
    return got.stdout if got.returncode == 0 else None


def load_bank_check():
    """tools/check_v4_bank.py, whose in_history says whether a commit is in this checkout's history."""
    spec = importlib.util.spec_from_file_location("check_v4_bank", REPO / "tools" / "check_v4_bank.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_rule_evidence():
    spec = importlib.util.spec_from_file_location("rule_evidence", REPO / "docs" / "ordo" / "rule_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNamedTargets(unittest.TestCase):
    """quick@3 measures a cross-type run's named target in Class I only: its counterfactual errands need standard's
    worlds, which no affordable quick count reads honestly (QUICK.md, "Named targets"); so quick costs no more than
    standard for every cross-type selection, named targets included, and every surface says what it left out. (A single
    target with --class II or I,II is the exception: TestQuickAgainstStandard.)"""
    PUBLIC = (("individual", "Ada Lovelace"), ("organization", "Mozilla"), ("nation", "the United States"))

    @classmethod
    def setUpClass(cls):
        cls.ordo = O.load_ordo_bank()
        import budget                                    # docs/ordo/budget.py, the plan rules' own enumeration
        cls.budget = budget

    def named(self, *types):
        return [dict(P.entity(t, name, key=name.lower().replace(" ", "")), type=t)
                for t, name in self.PUBLIC if t in types]

    def test_quick_asks_a_named_targets_questions_and_none_of_its_errands(self):
        named = self.named("organization")
        quick, standard = (O.plan(self.ordo, TRIANGLE, tier, named=named) for tier in ("quick", "standard"))

        def mine(calls, episode):
            return {c["id"] for c in calls if c["meta"].get("named") and bool(c.get("episode")) == episode}
        self.assertEqual(len(mine(quick, False)), 56)
        self.assertEqual(mine(quick, False), mine(standard, False))       # standard's twin-matched questions
        self.assertEqual(mine(quick, True), set())
        self.assertEqual(len(mine(standard, True)), 48)
        plain = O.plan(self.ordo, TRIANGLE, "quick")
        self.assertEqual({c["id"] for c in quick} - mine(quick, False), {c["id"] for c in plain})

    def selections(self):
        """Every budget case asked at quick, and every named-target selection: each pair set, each non-empty set of
        named types it holds, memory on and both, and the classes a quick run plans."""
        out = {name: sel for name, sel in self.budget.cases().items() if sel["tier"] == "quick"}
        pairs = {"one pair": [["individual", "organization"]], "two pairs": self.budget.TWO_PAIRS,
                 "triangle": self.budget.TRIANGLE}
        for label, ps in pairs.items():
            types = [t for t in self.budget.TYPES if any(t in p for p in ps)]
            for n in range(1, len(types) + 1):
                for named in itertools.combinations(types, n):
                    for memory in ("on", "both"):
                        for classes in ("I,II", "I"):
                            out[f"{label} + {named}, memory {memory}, {classes}"] = {
                                "tier": "quick", "memory": memory, "classes": classes,
                                "cross": {"pairs": ps, "named": list(named)}}
        return out

    def test_quick_costs_no_more_than_standard_across_types_and_at_the_default_classes(self):
        keys = ("units", "claude_requests", "codex_requests", "sonnet_min", "opus_min", "codex_min")
        for name, sel in self.selections().items():
            quick, standard = self.budget.estimate(sel), self.budget.estimate(dict(sel, tier="standard"))
            for k in keys:
                self.assertLessEqual(quick[k], standard[k], (name, k))

    def test_the_planner_agrees_for_a_named_target_of_each_type(self):
        named = self.named(*O.TYPES)
        counts = {tier: O.count_plan(O.plan(self.ordo, TRIANGLE, tier, named=named)) for tier in ("quick", "standard")}
        self.assertLess(counts["quick"]["sonnet_min"], counts["standard"]["sonnet_min"])
        self.assertEqual((counts["quick"]["units"], round(counts["quick"]["sonnet_min"], 1)), (540, 17.4))
        self.assertEqual((counts["standard"]["units"], round(counts["standard"]["sonnet_min"], 1)), (936, 45.0))

    def command(self, *extra):
        targets = [f"--target={t}={name}" for t, name in self.PUBLIC]
        return ["--agent", "claude", "--compare", "individual:organization:nation", *targets, *extra]

    def plan_runs(self, *extra):
        a = P.parse_args_v4(self.command(*extra))
        bank = real_bank()
        spec = P.load_entities(None, bank, data={})
        types = list(O.TYPES)
        for t, name in P.parse_targets(a.target, types, bank):
            P.add_target(spec, bank, t, name)
        selection = P.canonical_compare(P.parse_compare(a.compare))
        return a, bank, P.plan_runs(a, bank, selection, spec, self.ordo)

    def test_named_targets_at_quick_with_class_ii_alone_are_refused_with_the_fix(self):
        with self.assertRaises(SystemExit) as stop:
            self.plan_runs("--tier", "quick", "--class", "II")
        self.assertEqual(str(stop.exception), "at --tier quick a named target is measured in Class I only; add I to "
                                              "--class, or use --tier standard for its errands")
        self.plan_runs("--tier", "standard", "--class", "II")                  # standard runs its errands

    def test_the_hint_offers_only_what_saves_time(self):
        for minutes in (-2.0, 0.0, 0.04):
            savings = []
            P.offer(savings, "--tier quick", minutes)
            self.assertEqual(savings, [], minutes)
        savings = []
        P.offer(savings, "--tier quick", 27.6)
        self.assertEqual(savings, ["--tier quick (-28 min)"])
        a, bank, runs = self.plan_runs("--tier", "standard")
        total = sum(P.run_counts(a, r)[2] for r in runs)
        hint = P.time_hint(a, bank, self.ordo, runs, total)
        self.assertEqual(hint, "about 45 minutes; to shorten: drop the named targets (-18 min), or --class I "
                               "(-35 min), or --tier quick (-28 min)")
        self.assertNotIn("(--", hint)

    def dry_run(self, *extra):
        printed, version = io.StringIO(), subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(P.subprocess, "run", return_value=version), \
                mock.patch.object(P, "run_calls", side_effect=AssertionError("a dry run reached run_calls")), \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d}), contextlib.redirect_stdout(printed):
            P.main(["--workdir", d, *self.command(*extra), "--dry-run", "--out", str(Path(d) / "out")])
        return printed.getvalue()

    def test_the_preflight_names_every_named_target(self):
        for tier in ("quick", "standard"):
            out = self.dry_run("--tier", tier)
            self.assertIn('    nation "the United States": The United States · the delegation of the United States · '
                          "the national AI research team of the United States · the United States'\n", out)
            for t, name in self.PUBLIC:
                self.assertIn(f'    {t} "{name}": ', out)

    def test_the_preflight_says_what_quick_leaves_out(self):
        quick = self.dry_run("--tier", "quick")
        self.assertIn("    named targets: individual Ada Lovelace, organization Mozilla, nation the United States "
                      "(Class I only at --tier quick; --tier standard adds their errands)\n", quick)
        self.assertIn("    540 units, 540 calls   about 17 min", quick)
        self.assertNotIn("to shorten", quick)
        standard = self.dry_run("--tier", "standard")
        self.assertIn("    named targets: individual Ada Lovelace, organization Mozilla, nation the United States\n",
                      standard)
        self.assertIn("    936 units, 936 calls   about 45 min", standard)
        self.assertIn("--tier quick (-28 min)", standard)
        self.assertNotIn("(--", standard)

    def test_a_quick_card_says_a_named_targets_errands_were_not_run(self):
        named = self.named("organization")
        units = []
        for c in O.plan(self.ordo, TRIANGLE, "quick", named=named, classes=("I",)):
            units += O.question_unit(c, {"error": None, "outcome": None})
        card = O.score_units(units, B=0, tier="quick")
        md = O.report_md(card)
        self.assertIn("- Mozilla (organization), Class II: not run in a quick run (--tier standard runs a named "
                      "target's errands).", md)
        self.assertIn("- Mozilla (organization), Class I: Δ insufficient", md)

    def test_a_retired_quick_folder_resumes_when_its_plan_is_this_one(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            _, _, _, meta = S.planted_run(out, tier="quick", memory="on", classes=("I", "II"))
            self.assertEqual(meta["recipe"], "quick@3")
            old = dict(meta, recipe="quick@2")                    # a run of the released quick@2, no named target
            (out / "run.json").write_text(json.dumps(old, indent=2))
            before = (out / "run.json").read_bytes()
            P.check_resume(out, meta)                             # the same plan, call for call: it resumes
            self.assertEqual((out / "run.json").read_bytes(), before)
            with self.assertRaises(SystemExit) as stop:
                P.check_resume(out, dict(meta, plan_fingerprint="0" * 64))
            self.assertEqual(str(stop.exception), f"{out} holds a quick@2 run whose plan is not this one (quick is "
                                                  "quick@3 now, which measures named targets in Class I only), so "
                                                  "this probatio cannot continue it; pass another --out")


class TestQuickAgainstStandard(unittest.TestCase):
    """quick costs no more than standard for the default classes and for every cross-type selection; a single target
    with --class II or I,II costs more at quick (quick asks the full tier's 48 errand tasks, standard 24), and there the
    preflight says so in one plain line and the time hint never offers quick (Codex's v4.1 check, finding 1)."""
    TARGETS = (("individual", "Ada Lovelace"), ("organization", "Mozilla"), ("nation", "the United States"))
    PROFILES = (("claude", "claude-sonnet-5-5"), ("claude", "claude-opus-5-5"), ("codex", None))

    @classmethod
    def setUpClass(cls):
        cls.bank = real_bank()
        cls.spec = P.load_entities(None, cls.bank, data={})

    def roster(self, t, name, controls):
        mine = dict(self.spec[t], controls=self.spec[t]["controls"][:controls])
        return P.resolve_target(self.bank, t, name, roster=mine)

    def cases(self):
        """Every single target: each type, one or two controls, every memory mode."""
        for t, name in self.TARGETS:
            for controls in (1, 2):
                roster = self.roster(t, name, controls)
                for memory in P.MEMORY_MODES:
                    yield (t, controls, memory), roster

    def namespace(self, agent, model, tier, memory, choice):
        a = SimpleNamespace(agent=agent, model=model, effort="medium", jobs=4, tier=tier, memory=memory, arm="neutral",
                            class_choice=choice)
        a.classes = P.resolve_classes(tier, choice)
        return a

    def test_quick_costs_no_more_than_standard_for_a_single_target_at_the_default_classes(self):
        for case, roster in self.cases():
            conds = P.MEMORY_MODES[case[2]]
            quick = P.plan_v4(self.bank, roster, "quick", P.resolve_classes("quick", "all"), conds)
            standard = P.plan_v4(self.bank, roster, "standard", P.resolve_classes("standard", "all"), conds)
            for profile in ("sonnet", "opus", "codex"):
                self.assertLess(P.plan_minutes(quick, profile, 4), P.plan_minutes(standard, profile, 4), case)
            for agent in ("claude", "codex"):
                self.assertLess(P.charged_requests(quick, agent), P.charged_requests(standard, agent), case)

    def test_the_preflight_says_so_wherever_standard_is_shorter(self):
        shown = 0
        for case, roster in self.cases():
            for choice in (("I",), ("II",), ("I", "II"), "all"):
                for agent, model in self.PROFILES:
                    a = self.namespace(agent, model, "quick", case[2], choice)
                    run = {"kind": "single", "roster": roster,
                           "calls": P.plan_v4(self.bank, roster, "quick", a.classes, P.MEMORY_MODES[case[2]])}
                    quick = P.run_counts(a, run)[2]
                    standard = P.run_counts(a, run, P.plan_v4(self.bank, roster, "standard",
                                                              P.resolve_classes("standard", choice),
                                                              P.MEMORY_MODES[case[2]]))[2]
                    line = P.standard_shorter(a, self.bank, None, [run], quick)
                    if standard < quick:
                        self.assertEqual(line, "for this selection --tier standard is shorter: "
                                               + P.duration(standard * 60), (case, choice, agent, model))
                        self.assertIn("II", a.classes)
                        shown += 1
                    else:
                        self.assertIsNone(line, (case, choice, agent, model))
        self.assertEqual(shown, 18 * 2 * len(self.PROFILES))     # every case with II, under every profile

    def dry_run(self, *args):
        printed, version = io.StringIO(), subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(P.subprocess, "run", return_value=version), \
                mock.patch.object(P, "run_calls", side_effect=AssertionError("a dry run reached run_calls")), \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d}), contextlib.redirect_stdout(printed):
            P.main(["--workdir", d, "--agent", "claude", "--model", "claude-sonnet-5-5", *args, "--dry-run"])
        return printed.getvalue()

    def test_the_cli_says_so_on_both_preflights(self):
        line = "  for this selection --tier standard is shorter: "
        one = ["--target-type", "organization", "--target", "Mozilla", "--tier", "quick"]
        for extra, shorter in ((["--class", "II"], "about 5 min"), (["--class", "I,II"], "about 9 min"),
                               (["--class", "I"], None), ([], None)):
            for args in (one + extra, ["--compare", "organization", "--target", "Mozilla", "--tier", "quick"] + extra):
                out = self.dry_run(*args)
                if shorter:
                    self.assertIn(f"{line}{shorter}\n", out, args)
                else:
                    self.assertNotIn(line.strip(), out, args)
        mixed = self.dry_run("--compare", "nation,individual:organization:nation", "--target", "nation=France",
                             "--tier", "quick", "--class", "II")   # the single group's reversal outweighs the triangle's
        self.assertIn(line, mixed)
        for args in (["--compare", "individual:organization:nation", "--tier", "quick", "--class", "II"],
                     ["--compare", "individual:organization:nation", "--target", "organization=Mozilla", "--tier",
                      "quick", "--class", "I,II"]):
            self.assertNotIn(line.strip(), self.dry_run(*args), args)               # across types quick is shorter
        for tier in ("standard", "full"):
            self.assertNotIn(line.strip(), self.dry_run(*one[:-1], tier, "--class", "II"), tier)

    def hint_lines(self, *args):
        return [line for line in self.dry_run(*args).splitlines() if line.startswith("  about ") and "minutes" in line]

    def test_v4s_own_preflight_prints_the_same_hint(self):
        """One target at the full tier with memory both: README's command (v4's own path) prints the 45-minute hint,
        word for word the options path's for the same run, from the one helper (time_hint); a run under the budget
        prints none on either path."""
        one = ["--target", "Mozilla", "--tier", "full", "--memory", "both"]
        own = self.hint_lines("--target-type", "organization", *one)
        self.assertEqual(own, ["  about 69 minutes; to shorten: --class I (-51 min), or --tier quick (-40 min)"])
        self.assertEqual(own, self.hint_lines("--compare", "organization", *one))
        self.assertEqual(own[0].strip(), self.hint("--compare", "organization", *one))
        self.assertEqual(self.hint_lines("--target-type", "organization", "--target", "Mozilla"), [])   # 17 minutes

    def test_the_hint_offers_only_what_the_run_can_change(self):
        """With no fixed saving (no named target, no --class I to drop to, no shorter tier), a single-type run is
        offered what it can still change, never "compare fewer types": --memory on for memory both, the time model's
        --jobs for fewer, fewer targets for several. A comparison of types keeps its words; quick is never offered
        where standard is shorter, and standard's own line still prints."""
        one = ["--target-type", "organization", "--target", "Mozilla"]
        self.assertEqual(self.hint_lines(*one, "--tier", "quick", "--class", "II", "--memory", "both", "--jobs", "1"),
                         ["  about 79 minutes; to shorten: --memory on (-40 min), or --jobs 4 (-59 min)"])
        out = self.dry_run(*one, "--tier", "quick", "--class", "II", "--memory", "both", "--jobs", "1")
        self.assertIn("  for this selection --tier standard is shorter: about 40 min\n", out)
        self.assertEqual(self.hint_lines(*one, "--tier", "full", "--class", "II", "--memory", "both", "--jobs", "1"),
                         ["  about 79 minutes; to shorten: --tier standard (-40 min)"])
        two = self.hint_lines(*one, "--target", "Wikimedia", "--tier", "quick", "--class", "II", "--memory", "both",
                              "--jobs", "1")
        self.assertEqual(two, ["  about 158 minutes; to shorten: --memory on (-79 min), or --jobs 4 (-119 min), or "
                               "fewer targets"])
        cross = self.hint("--compare", "individual:organization:nation", "--tier", "quick", "--class", "II",
                          "--memory", "both", "--jobs", "1")
        self.assertEqual(cross, "about 79 minutes; to shorten: compare fewer types")
        for args in ([*one, "--tier", "full", "--memory", "both"], [*one, "--tier", "quick", "--class", "II",
                                                                    "--memory", "both", "--jobs", "1"]):
            for line in self.hint_lines(*args):
                self.assertNotIn("named target", line)
                self.assertNotIn("fewer types", line)
                self.assertNotIn("(--", line)
                self.assertNotIn("(-0", line)

    def hint(self, *args):
        a = P.parse_args_v4(["--agent", "claude", "--model", "claude-sonnet-5-5", *args])
        bank, ordo_bank, _, _, runs = P.prepare_runs(a, False)
        return P.time_hint(a, bank, ordo_bank, runs, sum(P.run_counts(a, r)[2] for r in runs))

    def test_the_hint_never_offers_quick_where_standard_is_shorter(self):
        targets = ["--target", "Mozilla", "--target", "Acme Research", "--target", "Brightwater Institute"]
        many = ["--compare", "organization", *targets, "--memory", "both", "--class", "I,II"]
        standard = self.hint(*many, "--tier", "standard")              # 56 min; quick would take 86
        self.assertTrue(standard.startswith("about 56 minutes; to shorten: "), standard)
        self.assertNotIn("--tier quick", standard)
        full = self.hint(*many, "--tier", "full")                     # standard is shorter than quick here
        self.assertNotIn("--tier quick", full)
        self.assertIn("--tier standard (-", full)
        default = self.hint("--compare", "organization", *targets, "--memory", "both", "--tier", "full")
        self.assertIn("--tier quick (-", default)                     # the default classes: quick is shorter
        self.assertNotIn("--tier standard", default)


class TestRetiredResume(unittest.TestCase):
    """An accepted resume of a retired recipe (quick@2 into quick@3) goes on under the recipe run.json records: the
    scoring, the page and the card name it, so run.json and the card agree (Codex's v4.1 check, finding 5). Driven
    through execute_run itself, with only the agent's calls and the live line stubbed."""

    def execute(self, out, run, meta, ordo_bank=None):
        seen = {}

        def view(watch, a, run, meta, out, bank, line):
            seen["page"] = meta["recipe"]
        with mock.patch.object(P, "run_calls", return_value=None), mock.patch.object(P, "live_line", return_value=None), \
                mock.patch.object(P, "watch_view", side_effect=view), contextlib.redirect_stdout(io.StringIO()):
            P.execute_run(SimpleNamespace(agent="claude", jobs=4), SimpleNamespace(), S.bank(), ordo_bank, run, meta,
                          out, watch=SimpleNamespace(finish=lambda view, card: None))
        return seen

    def test_a_single_type_card_names_the_recorded_recipe(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = S.planted_run(out, tier="quick", memory="on", classes=("I", "II"))
            (out / "run.json").write_text(json.dumps(dict(meta, recipe="quick@2"), indent=2))
            seen = self.execute(out, {"kind": "single", "calls": calls, "roster": roster}, meta)
            card = json.loads((out / "scorecard.json").read_text())
            recorded = json.loads((out / "run.json").read_text())["recipe"]
            self.assertEqual((recorded, card["recipe"], seen["page"]), ("quick@2",) * 3)
            self.assertEqual(card["tier"], "quick")

    def test_a_cross_type_card_names_the_recorded_recipe(self):
        ordo_bank = O.load_ordo_bank()
        calls = O.plan(ordo_bank, TRIANGLE, "quick")
        meta = {"probatio": "4.0-dev", "agent": "claude", "tier": "quick", "recipe": "quick@3",
                "plan_fingerprint": P.plan_fingerprint(calls)}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            out.mkdir()
            (out / "run.json").write_text(json.dumps(dict(meta, recipe="quick@2")))
            seen = self.execute(out, {"kind": "cross", "calls": calls}, meta, ordo_bank)
            card = json.loads((out / "ordo" / "scorecard.json").read_text())
            self.assertEqual((json.loads((out / "run.json").read_text())["recipe"], card["recipe"], seen["page"]),
                             ("quick@2",) * 3)

    def test_a_new_run_records_and_scores_the_current_recipe(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = S.planted_run(out, tier="quick", memory="on", classes=("I", "II"))
            (out / "run.json").unlink()                           # a fresh folder: check_resume writes run.json
            self.execute(out, {"kind": "single", "calls": calls, "roster": roster}, meta)
            self.assertEqual(json.loads((out / "run.json").read_text())["recipe"], "quick@3")
            self.assertEqual(json.loads((out / "scorecard.json").read_text())["recipe"], "quick@3")


class TestErrandRuleGate(unittest.TestCase):
    """The completion gate of PREREG Amendment 6 (ordo.errand_rule_status; Codex's re-check R2 and its final check
    F1 to F3): adopted only with complete, current evidence whose artifacts the gate reads itself; every gap, wrong
    number and malformed shape reads PENDING with its problem named, never an exception; the fingerprint follows every
    numeric dependency; the builder refuses what the gate refuses; and no document carries a Class II precision claim
    the gate does not back."""

    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(O.RULE_EVIDENCE.read_text())
        cls.plans = O.quick_plan_fingerprints()
        cls.sim = json.loads((REPO / cls.evidence["allocation"]["path"]).read_text())
        with (REPO / cls.evidence["null_acceptance"]["path"]).open(newline="") as f:
            cls.rows = list(csv.DictReader(f))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def status(self, evidence=None, prereg=O.PREREG_FILE, plans=None):
        path = self.tmp / "errand_rule.json"
        if evidence is not None:
            path.write_text(evidence if isinstance(evidence, str) else json.dumps(evidence))
        return O.errand_rule_status(path, prereg, plans or self.plans)

    def assertPending(self, evidence, *phrases):
        got = self.status(evidence)
        self.assertEqual(got["status"], "PENDING", phrases)
        for phrase in phrases:
            self.assertTrue(any(phrase in p for p in got["problems"]), (phrase, got["problems"]))

    def changed(self, **parts):
        ev = json.loads(json.dumps(self.evidence))
        for dotted, value in parts.items():
            *keys, last = dotted.split("__")
            node = ev
            for k in keys:
                node = node[k]
            node[last] = value
        return ev

    def with_artifact(self, section, name, text):
        """The evidence with section's artifact replaced by text: a hash-consistent file in the scratch folder."""
        ev, path = self.changed(), self.tmp / name
        path.write_text(text)
        ev[section].update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return ev

    def with_rows(self, edit):
        rows = [dict(r) for r in self.rows]
        rows = edit(rows) or rows
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(self.rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        return self.with_artifact("null_acceptance", "stage0b.csv", out.getvalue())

    def with_sim(self, edit):
        sim = json.loads(json.dumps(self.sim))
        edit(sim)
        return self.with_artifact("allocation", "quick_sim.json", json.dumps(sim))

    def test_the_committed_evidence_adopts_the_rule(self):
        self.assertEqual(O.errand_rule_status(), {"status": "adopted", "problems": []})

    def test_every_gap_reads_pending_with_its_problem(self):
        cases = {"no readable evidence": None,
                 "is for 'Amendment 5'": self.changed(amendment="Amendment 5"),
                 "is for 'another rule'": self.changed(rule="another rule"),
                 "stale": self.changed(scorer_fingerprint="0" * 64),
                 "null simulations artifact changed": self.changed(null_acceptance__sha256="0" * 64),
                 "null simulations artifact is missing": self.changed(null_acceptance__path="docs/ordo/absent.csv"),
                 "record seeds": self.changed(null_acceptance__seeds={"standard": "invented"}),
                 "record B 1": self.changed(null_acceptance__B=1),
                 "record replicates 1": self.changed(null_acceptance__replicates=1),
                 "record candidates": self.changed(null_acceptance__candidates=[O.ADOPTED_CANDIDATE]),
                 "allocation artifact changed": self.changed(allocation__sha256="0" * 64),
                 "records the allocation's replicates": self.changed(allocation__replicates=-1),
                 "triangle winner is not the one its allocation gives":
                     self.changed(allocation__winners__triangle__plan_fingerprint=None)}
        for problem, evidence in cases.items():
            self.assertPending(evidence, problem)

    def test_a_prereg_without_the_amendment_reads_pending(self):
        prereg = self.tmp / "PREREG.md"
        prereg.write_text(O.PREREG_FILE.read_text().split("**Amendment 6, ")[0])
        got = self.status(self.evidence, prereg)
        self.assertEqual(got["status"], "PENDING")
        self.assertIn("PREREG.md carries no Amendment 6", got["problems"])

    def test_a_changed_quick_plan_reads_pending(self):
        got = self.status(self.evidence, plans=dict(self.plans, one_target="0" * 64))
        self.assertIn("the one_target menu's minimax winner is not today's quick plan", got["problems"])

    def test_the_null_simulations_are_read_not_trusted(self):
        """Codex's hash-consistent counterexamples (final check F1): every one read adopted before."""
        def rates(value):
            def edit(rows):
                for r in rows:
                    if r["rule"] == O.ADOPTED_CANDIDATE and r["measure"] == "false relation":
                        r["rate"] = value                     # met stays "yes"
            return edit
        self.assertPending(self.with_artifact("null_acceptance", "one.csv", f'rule,met\n"{O.ADOPTED_CANDIDATE}",yes\n'),
                           "lack the columns")
        self.assertPending(self.with_rows(rates("100.00")), "cells fail", "missing its target <= 15")
        self.assertPending(self.with_rows(lambda rows: [r for r in rows if r["rule"] == O.ADOPTED_CANDIDATE]),
                           "not the candidates")
        cut = next(i for i, r in enumerate(self.rows) if r["rule"] == O.ADOPTED_CANDIDATE and r["target"])
        self.assertPending(self.with_rows(lambda rows: rows[:cut] + rows[cut + 1:]), "missing or repeated")
        self.assertPending(self.with_rows(lambda rows: rows + [rows[cut]]), "missing or repeated")

        def flip(field, value):
            def edit(rows):
                rows[cut][field] = value
            return edit
        self.assertPending(self.with_rows(flip("met", "NO")), "reads met 'NO'")
        self.assertPending(self.with_rows(flip("mc_se", "0.90")), "Monte Carlo error 0.9")
        self.assertPending(self.with_rows(flip("n", "9000")), "not 10000 replicates")
        self.assertPending(self.with_rows(flip("target", ">= 50")), "not '")
        self.assertPending(self.with_rows(flip("pools", "anulus")), "other pools")

    def test_the_allocation_is_read_not_trusted(self):
        """The winners are recomputed from the allocation's own widths and checked against the plan fingerprints it
        recorded, and its rule, replicates and provenance against today's."""
        quick = {m: next(n for n in names if n.endswith(O.QUICK_LABEL))
                 for m, names in O.RULE_ALLOCATION["menus"].items()}

        def worst_quick(sim):
            sim["one_target"][quick["one_target"]]["null"]["II"]["median_raw_width"] = 1000
        self.assertPending(self.with_sim(worst_quick), "one_target menu's minimax winner is 'I standard + II 40 tasks'")

        def other_plan(sim):
            sim["triangle"][quick["triangle"]]["plan_fingerprint"] = "0" * 64
        self.assertPending(self.with_sim(other_plan), "triangle menu's minimax winner is not today's quick plan",
                           "triangle winner is not the one its allocation gives")

        def wrong_run(sim):
            sim.update(errand_interval_rule="an unrelated rule", replicates=1)
        self.assertPending(self.with_sim(wrong_run), "ran with rule 'an unrelated rule'", "ran with replicates 1")
        self.assertPending(self.with_sim(lambda sim: sim.pop("provenance")), "ran with script None",
                           "stale: the allocation was simulated")
        self.assertPending(self.with_sim(lambda sim: sim["triangle"].pop("smoke")), "triangle menu holds")
        self.assertPending(self.with_sim(lambda sim: sim["one_target"]["smoke"].pop("plan_fingerprint")),
                           "one_target candidates without minutes")

    def test_malformed_shapes_read_pending_and_never_raise(self):
        """Final check F2: malformed JSON and csv shapes, and unreadable files, are named problems."""
        self.assertPending("[]", "is not a JSON object")
        self.assertPending("{", "no readable evidence")
        self.assertPending(self.changed(null_acceptance="bad shape"), "no null_acceptance record")
        self.assertPending(self.changed(allocation=[1, 2]), "no allocation record")
        self.assertPending(self.changed(null_acceptance__path=7), "names no null simulations artifact")
        self.assertPending(self.with_artifact("null_acceptance", "cols.csv", "foo\nbar\n"), "lack the columns")
        self.assertPending(self.with_artifact("null_acceptance", "empty.csv", ""), "hold no rows")
        bad = self.with_artifact("null_acceptance", "bytes.csv", "")
        (self.tmp / "bytes.csv").write_bytes(b"\xff\xfe\x00rule")
        bad["null_acceptance"]["sha256"] = hashlib.sha256(b"\xff\xfe\x00rule").hexdigest()
        self.assertPending(bad, "unreadable")
        self.assertPending(self.with_artifact("allocation", "list.json", "[]"), "is not a JSON object")
        self.assertPending(self.with_sim(lambda sim: sim.update(triangle="menu")), "triangle menu holds 'menu'")
        self.assertPending(self.with_sim(lambda sim: sim["triangle"]["smoke"].update(null=None)),
                           "triangle candidates without minutes")
        self.assertPending(self.with_rows(lambda rows: [dict(r, rate="many") for r in rows]), "not a number")
        with mock.patch.object(O, "quick_plan_fingerprints", side_effect=OSError("no bank")):
            got = O.errand_rule_status()
        self.assertEqual(got["status"], "PENDING")
        self.assertIn("could not be checked (OSError: no bank)", got["problems"][0])

    def test_the_fingerprint_follows_every_numeric_dependency(self):
        deps = dict(O.rule_dependencies())
        for name in ("ordo.Z975", "ordo.T975_TABLE", "ordo.BOOTSTRAP_B", "ordo.ERRAND_EPSILON", "ordo.MIN_DEFINED",
                     "ordo.bootstrap_of", "ordo.answer_row", "ordo.interval_rule", "ordo.errand_interval",
                     "ordo.small_cluster", "ordo.errand_pair", "ordo.class1_pair", "probatio.Bootstrap",
                     "probatio.levels", "probatio.class_score", "probatio.BOOTSTRAP_B"):
            self.assertIn(name, deps)
        fp = O.rule_fingerprint()
        for module, name, value in ((O, "Z975", 2.5), (O, "BOOTSTRAP_B", 1000), (O, "ERRAND_EPSILON", 0.02),
                                    (O, "T975_TABLE", {}), (P, "BOOTSTRAP_B", 1000)):
            with mock.patch.object(module, name, value):
                self.assertNotEqual(O.rule_fingerprint(), fp, name)
        with mock.patch.object(O, "Z975", 2.5):                 # Codex's probe: the interval moves, so must the gate
            got = O.errand_rule_status(plans=self.plans)
        self.assertEqual(got["status"], "PENDING")
        self.assertTrue(any(p.startswith("stale") for p in got["problems"]), got["problems"])
        code = "import sys; sys.path.insert(0, sys.argv[1]); import ordo; print(ordo.rule_fingerprint())"
        seen = {subprocess.run([sys.executable, "-B", "-c", code, str(REPO)], capture_output=True, text=True,
                               check=True, env=dict(os.environ, PYTHONHASHSEED=seed)).stdout.strip()
                for seed in ("1", "2")}
        self.assertEqual(seen, {fp})                             # the hash seed does not move it

    def test_the_builder_refuses_what_the_gate_refuses(self):
        R = load_rule_evidence()
        sim = dict(self.sim, errand_interval_rule="an unrelated rule", replicates=1)
        path = self.tmp / "quick_sim.json"
        path.write_text(json.dumps(sim))
        with self.assertRaises(R.EvidenceError) as caught:
            R.allocation(path)
        self.assertIn("ran with rule 'an unrelated rule'", str(caught.exception))
        self.assertIn("ran with replicates 1", str(caught.exception))
        if git_show("HEAD", "docs/ordo/stage0b.csv") is None:
            self.skipTest("no git history: the evidence's provenance cannot be rebuilt")
        recorded = self.evidence["null_acceptance"]["commit"]
        if load_bank_check().in_history(recorded):
            self.assertEqual(R.evidence(REPO / self.evidence["allocation"]["path"]), self.evidence)
            return
        # The published history lacks the development commit that wrote stage0b.csv, so git names another writer
        # (bank/README.md's Provenance is the same case): take the recorded commit for that one field, rebuild the rest.
        with mock.patch.object(R, "writing_commit", lambda path: recorded):
            self.assertEqual(R.evidence(REPO / self.evidence["allocation"]["path"]), self.evidence)

    def test_stage0b_ran_todays_scorers_but_amendment_6s_dispatch(self):
        """stage0b.csv carries no fingerprint (it predates the gate). Its provenance is the commit that wrote it: the
        csv there is the recorded one, that commit's scorers are today's apart from what Amendment 6 added, and its
        candidate C is today's rule wherever the interval stays within 0 to 100."""
        import stage0
        commit = O.RULE_NULL["commit"]
        then = git_show(commit, self.evidence["null_acceptance"]["path"])
        if then is None:
            self.skipTest("no git history to read Stage 0b's commit from")
        self.assertEqual(hashlib.sha256(then).hexdigest(), self.evidence["null_acceptance"]["sha256"])
        old = {m: top_level(git_show(commit, f"{m}.py").decode()) for m in ("ordo", "probatio")}
        new = {m: top_level((REPO / f"{m}.py").read_text()) for m in ("ordo", "probatio")}

        def text(defs, dotted):
            m, name = dotted.split(".")
            return defs[m].get(name, defs["probatio" if m == "ordo" else "ordo"].get(name))
        moved = [name for name, _ in O.rule_dependencies() if text(old, name) != text(new, name)]
        self.assertEqual(moved, AMENDMENT_6_CODE)
        for value, ci, g in ((48.0, [35.0, 60.0], 16), (50.0, [41.0, 58.5], 32), (60.0, [52.0, 71.0], 9)):
            for rid in ("role-free.II", "role-free.III", "authority.II"):
                shown = O.interval_rule(rid)(value, ci, g)
                self.assertEqual(stage0.halved(value, ci, (g, g), O.small_cluster(value, ci, g), rid), shown)
        self.assertEqual(stage0.SEED_TAGS, O.RULE_NULL["seeds"])
        self.assertEqual(tuple(stage0.MODELS), O.RULE_NULL["models"])

    def test_the_allocation_simulated_the_plans_it_names(self):
        import quick_sim as Q
        self.assertEqual(tuple(Q.V4_MENU), O.RULE_ALLOCATION["menus"]["one_target"])
        self.assertEqual(tuple(Q.ORDO_MENU), O.RULE_ALLOCATION["menus"]["triangle"])
        self.assertEqual(Q.SEED_TAG, O.RULE_ALLOCATION["seeds"])
        bank = real_bank()
        Q.STATE.update(bank=bank, roster=P.resolve_target(bank, "organization", "Mozilla"), ordo=O.load_ordo_bank())
        for cand in Q.V4_MENU:
            self.assertEqual(self.sim["one_target"][cand]["plan_fingerprint"], P.plan_fingerprint(Q.v4_calls(cand)))
        for cand in Q.ORDO_MENU:
            self.assertEqual(self.sim["triangle"][cand]["plan_fingerprint"], P.plan_fingerprint(Q.ordo_calls(cand)))

    def test_no_document_carries_a_class_ii_claim_the_gate_does_not_back(self):
        """Final check F3: the precision claims sit in gated blocks, none outside, and a PENDING gate would find every
        one; while adopted, QUICK.md's block carries the evidence's own winners."""
        status = O.errand_rule_status()["status"]
        for doc in (REPO / "docs" / "ordo" / "QUICK.md", REPO / "README.md"):
            text = doc.read_text()
            self.assertEqual(stale_claims(text, status, self.sim), [], doc.name)
            self.assertTrue(stale_claims(text, "PENDING", self.sim), doc.name)
            self.assertEqual(stale_claims(GATED.sub("", text), "PENDING", self.sim), [], doc.name)
        if status == "adopted":
            block = "".join(GATED.findall((REPO / "docs" / "ordo" / "QUICK.md").read_text()))
            for menu, field, row in (("one_target", "median_raw_width", "II"),
                                     ("triangle", "median_width", "role-free.II")):
                name = self.evidence["allocation"]["winners"][menu]["candidate"]
                self.assertIn(f"{self.sim[menu][name]['null'][row][field]:.1f}", block, (menu, name))


class TestPendingPresentation(unittest.TestCase):
    """Final check F3: while the errand rule reads PENDING, the answer gives the role-free errand rows no width or tier
    advice and says why in one line; other rows keep theirs."""
    CARD = {"tier": "quick", "conditions": {"with": {"rows": [
        {"id": "role-free.II", "label": "role-free Class II", "unresolved": [["individual", "organization"]],
         "pairs": {"individual:organization": {"ci": [20, 80]}}},
        {"id": "authority.I", "label": "authority Class I", "unresolved": [["individual", "organization"]],
         "pairs": {"individual:organization": {"ci": [10, 90]}}}]}}}

    def test_pending_names_no_errand_row_and_says_why(self):
        self.assertEqual(O.too_wide(self.CARD, "PENDING"), "authority Class I is too wide to rank at the quick tier "
                                                           "(an interval over 40 points); --tier full narrows it.")
        self.assertEqual(O.rule_pending_line(self.CARD, "PENDING"),
                         "role-free Class II: its interval rule (PREREG Amendment 6) is PENDING its evidence, so this "
                         "run gives no width or tier advice for it.")
        self.assertIn("role-free Class II and authority Class I are too wide", O.too_wide(self.CARD, "adopted"))
        self.assertIsNone(O.rule_pending_line(self.CARD, "adopted"))

    def test_the_live_gate_is_consulted_when_no_status_is_given(self):
        pending = {"status": "PENDING", "problems": ["no readable evidence"]}
        card = json.loads(json.dumps(self.CARD))
        card["conditions"]["with"]["rows"].pop()                 # Codex's counterexample: role-free Class II alone
        with mock.patch.object(O, "errand_rule_status", return_value=pending):
            self.assertIsNone(O.too_wide(card))
            self.assertIn("PENDING its evidence", O.rule_pending_line(card))
        with mock.patch.object(O, "errand_rule_status", side_effect=AssertionError("not needed")):
            self.assertIsNone(O.rule_pending_line({"conditions": {"with": {"rows": []}}}))   # no errand row: no call


class TestRouting(unittest.TestCase):
    def test_smoke_with_a_v4_option_is_v4(self):
        self.assertFalse(P.wants_v3(["--agent", "claude", "--compare", TRIANGLE, "--tier", "smoke"]))
        self.assertFalse(P.wants_v3(["--agent", "claude", "--target-type", "nation", "--target", "France",
                                     "--tier", "smoke"]))
        self.assertTrue(P.wants_v3(["--agent", "claude", "--name", "Jane Doe", "--tier", "smoke"]))   # v3's, as before


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
