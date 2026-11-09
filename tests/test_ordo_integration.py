"""Tests for the joins between probatio.py, ordo.py and the ordo bank (docs/ordo/DESIGN.md 8.6, 11.4, 11.5; Codex R4):
the preflight prices every budget.json case exactly as docs/ordo/budget.py's estimate() does, through one source of
rates; v4's own estimate keeps rates equal to that source; the baseline fixtures hashes come from the pinned manifest;
the head of a cross-type answer carries probatio's calibration words; and the recognition check of custom controls,
planned, tallied and named in the head. No test calls an agent or the network.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "docs" / "ordo"))
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import budget  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "ordo_small"
TRI = P.TRIANGLE
ROSTER_CASES = {     # the budget cases that need an entities file, as the file a user would write
    "standard: organization, 3 comparators and 1 custom control":
        {"organization": {"comparators": ["Anthropic", "OpenAI", "Meta"], "controls": ["Brackwater Mills"]}},
    "standard: organization, 8 comparators, 2 targets":
        {"organization": {"comparators": ["Anthropic", "OpenAI", "Google DeepMind", "Meta", "xAI", "Cohere",
                                          "Hugging Face", "Stability AI"],
                          "targets": ["Mozilla", "the Wikimedia Foundation"]}},
    "standard: individual:organization:nation, one control per type":
        {"individual": {"controls": ["Karim Nassar"]}, "organization": {"controls": ["Thirlbeck Systems"]},
         "nation": {"controls": ["Arvenia"]}},
    "quick: individual:organization:nation, one control for organization, named organization":
        {"organization": {"controls": ["Thirlbeck Systems"]}},
}


def command_lines():
    """Every budget.json case (docs/ordo/budget.py cases()) as the probatio arguments a user would type."""
    named, france = ["--target", "organization=Mozilla"], ["--target", "nation=France"]
    each = ["--target", "individual=Ada Lovelace"] + named + ["--target", "nation=the United States"]
    out = {}
    for tier in ("smoke", "quick", "standard", "full"):
        out[f"{tier}: {TRI}"] = ["--compare", TRI, "--tier", tier]
        out[f"{tier}: organization (v4 single-type, one target)"] = ["--compare", "organization", "--tier", tier] + named
        out[f"{tier}: nation,{TRI}"] = ["--compare", f"nation,{TRI}", "--tier", tier] + france
        out[f"{tier}: {TRI} + named organization"] = ["--compare", TRI, "--tier", tier] + named
    for tier in ("quick", "standard"):
        out[f"{tier}: individual:organization + named organization"] = ["--compare", "individual:organization",
                                                                        "--tier", tier] + named
        out[f"{tier}: {TRI} + a named target of each type"] = ["--compare", TRI, "--tier", tier] + each
    out.update({
        "standard: individual:organization": ["--compare", "individual:organization"],
        "standard: individual:organization,organization:nation": ["--compare",
                                                                  "individual:organization,organization:nation"],
        "standard: organization, 3 comparators and 1 custom control": ["--compare", "organization"] + named,
        "standard: organization, 8 comparators, 2 targets": ["--compare", "organization"],
        "standard: individual:organization:nation, one control per type": ["--compare", TRI],
        "quick: individual:organization:nation, one control for organization, named organization":
            ["--compare", TRI, "--tier", "quick"] + named,
        "standard: individual:organization:nation, memory both": ["--compare", TRI, "--memory", "both"],
        "standard: individual:organization:nation, --class I": ["--compare", TRI, "--class", "I"]})
    return {name: ["--agent", "claude"] + args for name, args in out.items()}


class TestOneBudget(unittest.TestCase):
    """The preflight's counts and minutes equal budget.py's estimate() on every budget.json case (Codex R4)."""

    def runs_of(self, name):
        args = list(command_lines()[name])
        with tempfile.TemporaryDirectory() as d:
            if name in ROSTER_CASES:
                path = Path(d) / "entities.json"
                path.write_text(json.dumps(ROSTER_CASES[name]))
                args += ["--entities", str(path)]
            a = P.parse_args_v4(args)
            return a, P.prepare_runs(a, False)[4]

    def test_every_budget_case(self):
        estimates = budget.cases()
        published = json.loads((REPO / "docs" / "ordo" / "budget.json").read_text())["cases"]
        self.assertEqual(set(estimates), set(published))
        for name in published:
            with self.subTest(name):
                a, runs = self.runs_of(name)
                counted = O.count_plan([c for r in runs for c in r["calls"]])
                want = budget.estimate(estimates[name])
                for k in ("questions", "episodes", "units", "by_family", "claude_requests", "codex_requests",
                          "retry_ceiling_claude", "retry_ceiling_codex", "warn"):
                    self.assertEqual(counted[k], want[k], k)
                for k in ("sonnet_min", "opus_min", "codex_min"):
                    self.assertAlmostEqual(counted[k], want[k], places=9)
                minutes = sum(P.run_counts(a, r)[2] for r in runs)          # the preflight's own total
                self.assertAlmostEqual(minutes, want["sonnet_min"], places=9)

    def test_v4s_own_estimate_reads_the_budgets_one_table(self):
        budget = json.loads((REPO / "docs" / "ordo" / "budget.json").read_text())["pricing"]
        self.assertEqual(P.pricing(), budget)
        self.assertIs(O.pricing(), P.pricing())                   # ordo prices through probatio's one table
        self.assertFalse(hasattr(P, "SECONDS_V4"))                # the second copy of v4's rates is gone

    def test_no_second_copy_of_a_rate(self):
        for path in ("ordo.py", "probatio.py"):
            text = (REPO / path).read_text()
            for copy in ("7.5, 5.3", "BUDGET_MINUTES", "LONG_RUN_MINUTES", "ERRAND_SECONDS"):
                self.assertNotIn(copy, text, f"{path}: {copy}")


class TestFixturesHome(unittest.TestCase):
    def test_the_hashes_are_the_pinned_manifests(self):
        manifest = json.loads((REPO / "bank" / "ordo" / "manifest.json").read_text())
        self.assertEqual(P.baseline_fixtures(), manifest["fixtures_sha256"])
        spec = P.baseline_spec(P.load_bank(P.BANK))
        for sel, sha in manifest["fixtures_sha256"].items():
            self.assertEqual(P.fixtures_sha256(spec, sel.split(":")), sha, sel)
        self.assertFalse(hasattr(P, "BASELINE_FIXTURES_SHA256"))

    def test_a_changed_manifest_is_refused(self):
        with self.assertRaises(SystemExit):
            O.baseline_fixtures(pinned="0" * 64)


class TestHeadLabels(unittest.TestCase):
    def test_the_head_carries_probatios_words(self):
        labels = ["cross-type: pilot only, not calibrated", "custom roster: not calibrated (fixtures 3f2a91c0)",
                  'custom control "Brackwater Mills": not calibrated']
        head = O.head_lines({"labels": labels, "agent_version": "2.1.309", "model": "m"}, TRI, True, "standard",
                            "claude")
        self.assertEqual(head[1], " · ".join(labels))
        plain = O.head_lines({}, "individual:organization", False, "standard", "claude")
        self.assertEqual(plain[1], "cross-type: pilot only, not calibrated · comparison set not registered (pilot "
                                   "design covers the triangle) · fixtures: baseline")


class TestRecognition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = O.load_ordo_bank(FIXTURE, None)
        cls.mills = dict(P.entity("organization", "Brackwater Mills"), type="organization")

    def test_two_calls_per_custom_control_per_condition(self):
        calls = O.recognition_plan(self.bank, [self.mills], ("without", "with"))
        self.assertEqual(len(calls), 2 * 2)
        self.assertEqual({c["meta"]["component"] for c in calls}, {"V12"})
        self.assertTrue(all("Brackwater Mills" in c["prompt"] for c in calls))
        self.assertEqual(O.count_plan(calls)["by_family"]["survey"], 4)

    def test_a_real_answer_names_the_control_in_the_head(self):
        calls = O.recognition_plan(self.bank, [self.mills], ("with",))
        done = {P.key(calls[0]): {"error": None, "outcome": "invented"},
                P.key(calls[1]): {"error": None, "outcome": "real"}}
        status = O.recognition_status(calls, done)
        self.assertEqual(status["Brackwater Mills"], {"real": 1, "valid": 2, "planned": 2, "reads_real": True})
        labels = ["custom roster: not calibrated (fixtures 1234abcd)", 'custom control "Brackwater Mills": not calibrated']
        self.assertEqual(O.relabel(labels, status)[1], 'control "Brackwater Mills" reads as real to this model: scores '
                                                       'are relative to a familiar entity, not a neutral baseline')
        quiet = O.recognition_status(calls, {P.key(c): {"error": None, "outcome": "invented"} for c in calls})
        self.assertEqual(O.relabel(labels, quiet), labels)

    def test_the_single_type_path_plans_and_names_it(self):
        entities = {"organization": {"controls": ["Brackwater Mills"]}}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mine.json"
            path.write_text(json.dumps(entities))
            argv = ["--agent", "claude", "--compare", "organization", "--target", "organization=Mozilla", "--entities",
                    str(path), "--tier", "smoke", "--workdir", d, "--out", str(Path(d) / "run")]
            a = P.parse_args_v4(argv)
            run = P.prepare_runs(a, False)[4][0]
            check = [c for c in run["calls"] if c["meta"]["class"] == "check"]
            self.assertEqual((len(check), run["recognise"]), (2, True))

            def answered(calls, *_, line=None):
                return {P.key(c): {"error": None, "outcome": "real" if c["meta"]["class"] == "check" else None}
                        for c in calls}
            version = subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
            with mock.patch.object(P, "run_calls", side_effect=answered), \
                    mock.patch.object(P, "score_run") as scored, \
                    mock.patch.object(P.subprocess, "run", return_value=version), \
                    mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": d}):
                (Path(d) / "run").mkdir()
                (Path(d) / "run" / "scorecard.md").write_text("card\n")
                with contextlib.redirect_stdout(io.StringIO()):
                    P.main(argv)
            meta = scored.call_args[0][4]               # run.json's record; scoring reads the answers (recognised)
            self.assertEqual((meta["recognition"]["asked"], meta["recognition"]["controls"]),
                             (True, ["Brackwater Mills"]))
            labels = P.recognised(Path(d) / "run", run["calls"], meta, answered(run["calls"]))["labels"]
            self.assertIn('control "Brackwater Mills" reads as real to this model: scores are relative to a familiar '
                          'entity, not a neutral baseline', labels)

    def test_a_cross_type_head_names_a_familiar_control(self):
        calls = O.plan(self.bank, TRI, "smoke", classes=("I",))
        units = []
        for c in calls:
            m = c["meta"]
            outcome = "real" if m["component"] == "V12" and m.get("full") == "Thirlbeck Systems" else None
            units += O.question_unit(c, {"error": None, "outcome": outcome})
        meta = {"labels": ["cross-type: pilot only, not calibrated",
                           'custom control "Thirlbeck Systems": not calibrated']}
        card = O.score_units(units, meta=meta, B=0, tier="smoke")
        self.assertIn('control "Thirlbeck Systems" reads as real to this model', card["head"][1])


class TestStage0(unittest.TestCase):
    """docs/ordo/stage0.py, the null simulations of SCORING 9 (STAGE0.md): its t quantiles, its widening and one
    replicate, reproducible from its seed."""

    def test_t_quantiles_and_the_widening(self):
        import stage0
        for df, printed in ((3, 3.182), (4, 2.776), (7, 2.365), (15, 2.131), (27, 2.052)):
            self.assertAlmostEqual(stage0.t975(df), printed, delta=0.01)
        self.assertIsNone(stage0.widen(50, [40, 60], 1))
        lo, hi = stage0.widen(50, [40, 60], 4)
        c = 3.182 / stage0.Z975 * (4 / 3) ** 0.5
        self.assertAlmostEqual(hi - lo, 20 * c)

    def test_the_adopted_rule_is_stage0s_candidate_b_and_c(self):
        # PREREG Amendment 3: the interval ordo prints is the percentile interval widened with the thinnest
        # component's clusters, exactly as stage0.py's candidate B computed it from the same replicate; Amendment 6
        # counts half of them on the role-free errand rows, as Stage 0b's candidate C (stage0.halved) does.
        import random
        import stage0
        stage0.init()
        units = stage0.null_units(random.Random(11), "position")
        checked = 0
        for rid, row in stage0.rows_of(units).items():
            for pn, rec in row["pairs"].items():
                if rec["value"] is None or rec.get("ci_percentile") is None:
                    continue
                g = stage0.clusters(units, rid, tuple(pn.split(":")), rec)[1]
                lo, hi = stage0.widen(rec["value"], rec["ci_percentile"], g // 2 if rid in stage0.HALVED_ROWS else g)
                want = [max(0.0, lo), min(100.0, hi)]                 # ordo clamps to the index's range
                self.assertEqual(rec["clusters"], g, (rid, pn))
                for a, b in zip(rec["ci"], want):
                    self.assertAlmostEqual(a, b, places=9)
                checked += 1
        self.assertGreater(checked, 10)

    def test_the_interval_stays_in_the_index_range(self):
        self.assertEqual(O.small_cluster(90, [80, 99], 4)[1], 100.0)
        self.assertEqual(O.small_cluster(8, [1, 20], 4)[0], 0.0)
        self.assertIsNone(O.small_cluster(50, [40, 60], 1))

    def test_a_replicate_is_its_seeds(self):
        import stage0
        stage0.init()
        self.assertEqual(stage0.replicate(("position", 7)), stage0.replicate(("position", 7)))
        self.assertTrue((REPO / "docs" / "ordo" / "stage0.csv").is_file())

    def test_stage0b_quick_tier_is_the_designed_plan(self):
        # STAGE0.md, Stage 0b: standard's role-free questions and checks (276), and role-free N0 errands on 32 tasks.
        import stage0
        calls = stage0.plan_of(O.load_ordo_bank(stage0.FIXTURE, None), "quick")
        errands = [c for c in calls if c.get("episode")]
        self.assertEqual((len(calls) - len(errands), len(errands)), (276, 96))
        self.assertEqual({(c["meta"]["cell"], c["meta"]["frame"]) for c in errands}, {("N0", O.ROLE_FREE)})
        self.assertEqual(len({c["meta"]["task"] for c in errands}), stage0.QUICK_TASKS)
        self.assertFalse(any(c["meta"].get("frame") == O.AUTHORITY for c in calls))
        stage0.init("quick", stage0.load_pairs())
        self.assertEqual(set(stage0.replicate(("fixture", 0))), {"role-free.I", "role-free.II"})

    def test_stage0b_candidate_c_halves_the_errand_clusters(self):
        import stage0
        printed = [40.0, 60.0]
        self.assertEqual(stage0.halved(50, [42, 58], (16, 16), printed, "role-free.II"), stage0.widen(50, [42, 58], 8))
        self.assertEqual(stage0.halved(50, [42, 58], (16, 16), printed, "authority.II"), printed)
        self.assertEqual(stage0.halved(50, [42, 58], (12, 12), printed, "role-free.I"), printed)
        self.assertIsNone(stage0.halved(50, [42, 58], (3, 3), printed, "role-free.III"))


if __name__ == "__main__":
    unittest.main()
