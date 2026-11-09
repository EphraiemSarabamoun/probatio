"""Exercise exact stage membership, hard quota boundaries and real-time gates."""
import collections
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grid as G


class Grid(unittest.TestCase):
    def setUp(self):
        self.specs = G.build_grid("frozen-codex-model")
        self.by_stage = {s: [r for r in self.specs if r["stage"] == s] for s in G.STAGES}
        self.records = {r["run_id"]: {"outcome": "done", "ended": 1000} for r in self.specs}

    def eligible(self, spec, **kwargs):
        args = dict(now=1000 + G.RETEST_DELAY, b1_verdict="pass", stage_a_complete=True,
                    codex_verification_pending=False, specs=self.specs)
        args.update(kwargs)
        return G.eligibility(spec, self.records, **args)

    def test_schedule_is_exact_and_deterministic(self):
        self.assertEqual(self.specs, G.build_grid("frozen-codex-model"))
        self.assertEqual({s: len(rows) for s, rows in self.by_stage.items()}, {"A": 8, "B1": 15, "B2": 18, "H": 5, "C": 4})
        expected_counts = {"A": (980, 320), "B1": (3960, 1800), "B2": (3960, 2160), "H": (1320, 600), "C": (560, 160)}
        for stage, rows in self.by_stage.items():
            self.assertEqual((sum(r["planned_questions"] for r in rows), sum(r["planned_errands"] for r in rows)), expected_counts[stage])
            self.assertEqual({r["shuffle_seed"] for r in rows}, {20261108})
            self.assertTrue(all((r["tier"] if "tier" in r else r["probatio_args"][r["probatio_args"].index("--tier") + 1]) == ("smoke" if stage in ("A", "C") else "standard") for r in rows))
        self.assertEqual(sum(r["planned_charged_requests"] for r in self.by_stage["C"]), 880)
        self.assertEqual(collections.Counter(r["subject"] for r in self.by_stage["A"]), {"sonnet": 5, "haiku": 2, "opus": 1})
        self.assertEqual({r["level"] for r in self.by_stage["B1"]}, {"L0", "L3", "L4", "L6", "L7"})
        self.assertEqual(sum(r["target_role"] == "real" for r in self.by_stage["B2"]), 6)
        self.assertEqual(sum(bool(r["retest_of"]) for r in self.by_stage["B2"]), 9)
        self.assertEqual(sum(r["level"] == "PC" for r in self.by_stage["B2"]), 3)
        self.assertEqual(len({r["run_id"] for r in self.specs}), 50)

    def test_exact_frozen_seed_order(self):
        expected = ['A-opus-L0-organization-kelthorne-labs', 'A-sonnet-L0-individual-daniel-whitford', 'A-sonnet-L4-organization-kelthorne-labs', 'A-haiku-L4-organization-kelthorne-labs', 'A-sonnet-L0-nation-delmora', 'A-sonnet-L0-organization-kelthorne-labs', 'A-sonnet-PC-individual-daniel-whitford', 'A-haiku-L0-organization-kelthorne-labs', 'B1-sonnet-L0-organization-kelthorne-labs', 'B1-sonnet-L7-nation-delmora', 'B1-sonnet-L4-organization-kelthorne-labs', 'B1-sonnet-L7-individual-daniel-whitford', 'B1-sonnet-L6-individual-daniel-whitford', 'B1-sonnet-L7-organization-kelthorne-labs', 'B1-sonnet-L4-individual-daniel-whitford', 'B1-sonnet-L0-individual-daniel-whitford', 'B1-sonnet-L6-nation-delmora', 'B1-sonnet-L3-nation-delmora', 'B1-sonnet-L3-individual-daniel-whitford', 'B1-sonnet-L3-organization-kelthorne-labs', 'B1-sonnet-L0-nation-delmora', 'B1-sonnet-L4-nation-delmora', 'B1-sonnet-L6-organization-kelthorne-labs', 'B2-sonnet-L6-individual-daniel-whitford-retest', 'B2-sonnet-L0-organization-kelthorne-labs-retest', 'B2-sonnet-L4-individual-ephraiem-sarabamoun', 'B2-sonnet-L6-nation-delmora-retest', 'B2-sonnet-L0-nation-france', 'B2-sonnet-PC-nation-delmora', 'B2-sonnet-L4-nation-delmora-retest', 'B2-sonnet-L0-individual-daniel-whitford-retest', 'B2-sonnet-L4-organization-mozilla', 'B2-sonnet-L6-organization-kelthorne-labs-retest', 'B2-sonnet-L0-nation-delmora-retest', 'B2-sonnet-L4-individual-daniel-whitford-retest', 'B2-sonnet-PC-individual-daniel-whitford', 'B2-sonnet-L4-organization-kelthorne-labs-retest', 'B2-sonnet-L0-organization-mozilla', 'B2-sonnet-L0-individual-ephraiem-sarabamoun', 'B2-sonnet-L4-nation-france', 'B2-sonnet-PC-organization-kelthorne-labs', 'H-haiku-L7-organization-kelthorne-labs', 'H-haiku-L3-organization-kelthorne-labs', 'H-haiku-L4-organization-kelthorne-labs', 'H-haiku-L0-organization-kelthorne-labs', 'H-haiku-L6-organization-kelthorne-labs', 'C-codex-L4-organization-kelthorne-labs', 'C-codex-L3-organization-kelthorne-labs', 'C-codex-L6-organization-kelthorne-labs', 'C-codex-L0-organization-kelthorne-labs']
        self.assertEqual([r["run_id"] for r in self.specs], expected)

    def test_pc_and_individual_flags_and_analysis_metadata(self):
        for r in self.specs:
            args = r["probatio_args"]
            self.assertNotIn("--auto-memory", args)  # harness-owned: run.command passes --auto-memory off
            self.assertNotIn("--effort", args)       # and the spec's effort, which is medium
            self.assertEqual(r["effort"], "medium")
            self.assertNotIn("--seed", args)  # v3's --seed would wrongly select a single task
            self.assertIn("--name" if r["target_type"] == "individual" else "--target", args)
            self.assertEqual(r["target_key"], r["target"]["key"])
            if r["level"] == "PC":
                self.assertEqual(r["memory"], "off")
                self.assertEqual(args[args.index("--class") + 1], "II,III")
                self.assertEqual(args[args.index("--arm") + 1], "g_wish")
                self.assertEqual(r["planned_questions"], 0)

    def test_retest_uses_completed_timestamps_not_planned_time(self):
        r = next(s for s in self.by_stage["B2"] if s["retest_of"])
        self.assertFalse(self.eligible(r, now=1000 + G.RETEST_DELAY - 0.01)["ready"])
        self.assertTrue(self.eligible(r)["ready"])
        self.records[r["retest_of"]]["ended"] = 2000
        self.assertFalse(self.eligible(r)["ready"])
        self.records[r["retest_of"]]["outcome"] = "failed"
        self.assertFalse(self.eligible(r)["ready"])
        self.records[r["retest_of"]] = {"outcome": "done", "ended": float("nan")}
        self.assertFalse(self.eligible(r)["ready"])

    def test_stage_freeze_and_truth_table_gate(self):
        r = self.by_stage["B1"][0]
        self.assertFalse(self.eligible(r, stage_a_complete=False)["ready"])
        self.records[self.by_stage["A"][0]["run_id"]]["outcome"] = "dry run"
        self.assertFalse(self.eligible(r)["ready"])
        self.records[self.by_stage["A"][0]["run_id"]]["outcome"] = "done"
        for verdict in G.B1_VERDICTS:
            self.assertEqual(self.eligible(self.by_stage["B2"][0], b1_verdict=verdict)["ready"], verdict not in ("fail", "not evaluable"))
        self.assertFalse(self.eligible(self.by_stage["B2"][0], b1_verdict=None)["ready"])

    def test_codex_last_and_skipped_b2_and_same_stage_order(self):
        r = self.by_stage["C"][0]
        self.assertFalse(self.eligible(r, codex_verification_pending=True)["ready"])
        self.records[self.by_stage["H"][0]["run_id"]]["outcome"] = "running"
        self.assertFalse(self.eligible(r)["ready"])
        self.records[self.by_stage["H"][0]["run_id"]]["outcome"] = "done"
        for s in self.by_stage["B2"]:
            self.records.pop(s["run_id"])
        self.assertFalse(self.eligible(r)["ready"])
        self.assertTrue(self.eligible(r, b1_verdict="fail")["ready"])
        self.assertFalse(self.eligible(r, b1_verdict="unknown")["ready"])
        self.records[self.by_stage["C"][0]["run_id"]]["outcome"] = "running"
        self.assertFalse(self.eligible(self.by_stage["C"][1], b1_verdict="fail")["ready"])

    def test_caps_fail_closed_and_stop_at_exact_boundary(self):
        self.assertEqual(G.stage_caps("A"), {"five_hour": 20, "seven_day": 5})
        self.assertEqual(G.stage_caps("B1", {"five_hour": 100, "seven_day": 10}), {"five_hour": 150, "seven_day": 15})
        self.assertEqual(G.stage_caps("H", {"five_hour": 90, "seven_day": 9}), {"five_hour": 100, "seven_day": 10})
        for stage in ("B1", "B2", "H"):
            for bad in (None, {}, {"five_hour": 1, "seven_day": float("nan")}, {"five_hour": True, "seven_day": 2}, {"five_hour": 0, "seven_day": 2}):
                self.assertFalse(G.cap_status(stage, {"five_hour": 0, "seven_day": 0}, bad)["allowed"])
        with self.assertRaises(ValueError):
            G.stage_caps("H", {"five_hour": 101, "seven_day": 1})
        self.assertTrue(G.cap_status("A", {"five_hour": 19.99, "seven_day": 4.99})["allowed"])
        self.assertFalse(G.cap_status("A", {"five_hour": 20, "seven_day": 0})["allowed"])
        self.assertFalse(G.cap_status("A", {"five_hour": 0, "seven_day": 5})["allowed"])
        for invalid in (None, {}, {"five_hour": -1, "seven_day": 1}, {"five_hour": "1", "seven_day": 1}):
            self.assertFalse(G.cap_status("A", invalid)["allowed"])
        self.assertTrue(G.cap_status("C", {"charged_requests": 999})["allowed"])
        self.assertFalse(G.cap_status("C", {"charged_requests": 1000})["allowed"])
        self.assertFalse(G.cap_status("C", {"charged_requests": 999.5})["allowed"])

    def test_explicit_dry_run_writes_nothing_even_with_out(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "must-not-exist"
            process = subprocess.run([sys.executable, str(Path(G.__file__)), "--codex-model", "frozen", "--out", str(output), "--dry-run"], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(len(json.loads(process.stdout)), 50)
            self.assertFalse(output.exists())

    def test_cli_writes_audits_and_runner_specs_with_no_calls(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "plan"
            process = subprocess.run([sys.executable, str(Path(G.__file__)), "--codex-model", "frozen", "--out", str(output)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(len(json.loads(process.stdout)), 50)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(len(manifest["runs"]), 50)
            for run_id in manifest["runs"]:
                spec = json.loads((output / f"{run_id}.json").read_text())
                self.assertTrue((output / spec["ladder_audit_file"]).is_file())
                self.assertEqual(spec["memory_file"] is None, spec["memory"] == "off")
                if spec["memory_file"]:
                    self.assertNotIn("⟦", (output / spec["memory_file"]).read_text())
            with self.assertRaises(ValueError):
                G.write_plan(output, self.specs)
