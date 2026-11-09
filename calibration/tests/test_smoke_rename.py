"""The smoke rename (docs/ordo/PREREG.md Amendment 5): the registered plans are the plans they were, apart from the word.

probatio's quick tier became the 15-minute estimate on 2026-11-08, and the plan quick named until then is now called
smoke. The v4 calibration's Stages A and C and the frozen ordo pilot's Stage 1 registered that plan, so these tests hold
it fixed against fixtures/smoke_rename_before.json, written at 00de136 before the rename:
- every grid spec equals its pre-rename self once Stages A and C's tier word is read back as quick;
- every Stage A and C plan, planned now at smoke, has the fingerprint it had at quick (every id, prompt and world);
- the pilot's check still counts 206 and 1,289, Stage 1 plans the same ids, and its registration word and historical
  run ids are unchanged, since plan.json is not edited and the alias applies at planning boundaries only;
- changed source is a different registration: a resume under it is refused and the old record is left untouched
  (an arm-run's registration hashes the source files it ran, which this change edits);
- a run.json from before recipes reads as smoke@1.
No model is called: the plans are built from the pinned banks and nothing runs.
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))
sys.path.insert(0, str(HERE.parents[1]))
import grid as G  # noqa: E402
import ordo as O  # noqa: E402
import ordo_pilot as OP  # noqa: E402
import ordo_run as OR  # noqa: E402
import probatio as P  # noqa: E402

BEFORE = json.loads((HERE / "fixtures" / "smoke_rename_before.json").read_text(encoding="utf-8"))
RENAMED = ("A", "C")


def read_back(spec):
    """A spec with Stages A and C's tier word read back as the pre-rename quick, in both places it appears."""
    s = json.loads(json.dumps(spec))
    if s["stage"] in RENAMED:
        args = s["probatio_args"]
        assert s["tier"] == "smoke" and args[args.index("--tier") + 1] == "smoke", s["run_id"]
        s["tier"] = "quick"
        args[args.index("--tier") + 1] = "quick"
    return s


class TestRegisteredCalibration(unittest.TestCase):
    def test_the_specs_differ_only_in_the_word(self):
        specs = G.build_grid(BEFORE["codex_model"])
        self.assertEqual([read_back(s) for s in specs], BEFORE["grid_specs"])
        self.assertEqual({s["tier"] for s in specs if s["stage"] in RENAMED}, {"smoke"})

    def test_stage_a_and_c_plans_are_the_registered_plans(self):
        bank = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        specs = [s for s in G.build_grid(BEFORE["codex_model"]) if s["stage"] in RENAMED]
        self.assertEqual(len(specs), len(BEFORE["registered_plan_fingerprints"]))
        for s in specs:
            a = P.parse_args_v4(["--agent", s["agent"]] + s["probatio_args"])
            roster = P.resolve_target(bank, a.target_type, a.target[0] if a.target else a.name)
            calls = P.plan_v4(bank, roster, a.tier, a.classes, P.MEMORY_MODES["on"], a.arm)
            self.assertEqual(P.plan_fingerprint(calls), BEFORE["registered_plan_fingerprints"][s["run_id"]],
                             s["run_id"])


class TestFrozenPilot(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(OP.PLAN.read_text(encoding="utf-8"))
        self.bank = O.load_ordo_bank()

    def test_the_check_counts_are_unchanged(self):
        rows, problems, total = OP.check(self.bank, self.plan)
        self.assertEqual(problems, [])
        self.assertEqual(total, BEFORE["pilot"]["check_total"])
        self.assertEqual({label: n["units"] for label, n in rows}, BEFORE["pilot"]["check_units"])

    def test_stage_1_plans_its_frozen_ids(self):
        calls, ids = OP.planned_calls(self.bank, self.plan, 1, "neutral")
        self.assertEqual(len(calls), BEFORE["pilot"]["stage1_units"])
        self.assertEqual(OP.ids_sha256(ids), BEFORE["pilot"]["stage1_ids_sha256"])

    def test_the_recorded_word_and_run_ids_are_kept(self):
        self.assertEqual(self.plan["stage_1"]["smoke"]["tier"], "quick")         # plan.json is not edited
        self.assertEqual(OP.stage_tier(self.plan, 1, "neutral"), BEFORE["pilot"]["stage1_tier_word"])
        self.assertEqual(OP.plan_word("quick"), "smoke")
        self.assertEqual(OP.plan_word("standard"), "standard")
        self.assertEqual([u["run_id"] for u in OP.pilot_runs(self.plan)], BEFORE["pilot"]["run_ids"])

    def test_changed_source_cannot_resume_the_finished_pilot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            old_canonical = {"stage": 1, "arm": "neutral", "tier": "quick", "probatio": {"sha256": "a" * 64}}
            record = {"registration": {"fingerprint": "1" * 64, "canonical": old_canonical}, "attempts": []}
            (out / OR.RECORD).write_text(json.dumps(record, indent=1))
            before = (out / OR.RECORD).read_bytes()
            new_canonical = dict(old_canonical, probatio={"sha256": "b" * 64})       # the edited source
            self.assertIn("probatio", OR.REGISTERED_FILES[0])
            with self.assertRaises(OR.Refusal) as stop:
                OR.registered(out, "2" * 64, new_canonical, 0.0)
            self.assertIn("registered with another probatio", str(stop.exception))
            self.assertEqual((out / OR.RECORD).read_bytes(), before)               # the old record is untouched
            self.assertEqual(OR.registered(out, "1" * 64, old_canonical, 0.0), record)   # the same registration

    def test_a_legacy_run_json_reads_as_smoke(self):
        self.assertEqual(P.recipe_of({"tier": "quick"}), "smoke@1")
        self.assertEqual(P.planner_tier(P.recipe_of({"tier": "quick"})), "smoke")


if __name__ == "__main__":
    unittest.main()
