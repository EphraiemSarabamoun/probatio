"""The staged study's gates at run time (stages.py, wired into run.py and the shim), end to end on the fakes: a plan
written by grid.py, its control file, real calibration.json records, the account ledger's stage book, and a fake
probatio whose calls go through the room's shim to a fake Claude Code with a rising meter. No test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import account as A  # noqa: E402
import fakes  # noqa: E402
import grid as G  # noqa: E402
import isolate as I  # noqa: E402
import run as R  # noqa: E402
import scheduler as SC  # noqa: E402
import stages as ST  # noqa: E402
import test_run as TRUN  # noqa: E402

FIRST_A = "A-opus-L0-organization-kelthorne-labs"           # stage A's first run in the seeded order (memory off)
SECOND_A = "A-sonnet-L0-individual-daniel-whitford"


class Plan(unittest.TestCase):
    """A scratch host with a fresh grid plan, the fake probatio's plan file and an injected clock."""

    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.root = Path(self.cfg["root"])
        self.root.mkdir(parents=True)
        self.plan_dir = self.host.dir / "plan"
        self.specs = G.build_grid("fake-codex-model")
        G.write_plan(self.plan_dir, self.specs)
        self.steps = self.host.dir / "steps.json"
        self.clock = TRUN.Clock()
        env = dict(fakes.clean_environ(), FAKE_PLAN=str(self.steps), FAKE_QUOTA=str(self.root / "quota.jsonl"),
                   FAKE_STOP=str(self.root / "STOP"), FAKE_NO_MEMORY="1", FAKE_PLANNED="4")
        self.patch = unittest.mock.patch.dict(os.environ, env, clear=True)
        self.patch.start()
        self.conf = SC.conf_from(self.cfg, self.root)

    def tearDown(self):
        self.patch.stop()
        self.host.close()

    def control(self, **given):
        (self.plan_dir / ST.CONTROL).write_text(json.dumps(given))

    def done(self, *run_ids, ended=None):
        """Plant finished calibration.json records for these runs."""
        for run_id in run_ids:
            out = Path(self.cfg["results_dir"]) / run_id
            out.mkdir(parents=True, exist_ok=True)
            (out / "calibration.json").write_text(json.dumps({"run_id": run_id, "outcome": "done",
                                                              "ended": ended or time.time() - 60}))

    def stage(self, name):
        return [s["run_id"] for s in self.specs if s["stage"] == name]

    def go(self, run_id, steps, dry_run=False):
        self.steps.write_text(json.dumps(steps))
        return R.run(R.load_spec(self.plan_dir / f"{run_id}.json"), self.cfg, dry_run=dry_run, sleep=self.clock.sleep,
                     now=self.clock.now, log=lambda m: None)

    def ledger(self):
        return A.apply(self.conf["home_dir"], "read", {})


class Eligibility(Plan):
    def test_a_grid_spec_loads_and_its_harness_flags_come_from_the_runner(self):
        spec = R.load_spec(self.plan_dir / f"{FIRST_A}.json")
        self.assertEqual(spec["plan_dir"], str(self.plan_dir.resolve()))
        cmd = R.command(spec, I.build(self.cfg, FIRST_A, "claude", "off", model=spec["model"]), self.cfg, Path("/o"))
        self.assertEqual(cmd.count("--auto-memory"), 1)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")

    def test_a_staged_spec_outside_its_plan_is_refused(self):
        loose = self.host.dir / "loose.json"
        loose.write_text((self.plan_dir / f"{FIRST_A}.json").read_text())
        with self.assertRaises(I.Refusal):
            R.load_spec(loose)

    def test_a_run_waits_for_the_runs_before_it_in_its_stage(self):
        with self.assertRaises(I.Refusal) as e:
            self.go(SECOND_A, [{"claude_calls": 1}])
        self.assertIn(FIRST_A, str(e.exception))
        self.assertFalse((Path(self.cfg["results_dir"]) / SECOND_A).exists(), "nothing created before the gate")

    def test_b1_needs_the_attested_freeze_and_a_prediction(self):
        b1 = sorted(self.specs, key=lambda s: (s["stage"] != "B1", s["stage_order"]))[0]["run_id"]
        self.done(*self.stage("A"))
        with self.assertRaises(I.Refusal) as e:
            self.go(b1, [{"claude_calls": 1}])
        self.assertIn("Stage A mechanics, metering and freeze are not attested", str(e.exception))
        self.control(stage_a_complete=True)
        with self.assertRaises(I.Refusal) as e:
            self.go(b1, [{"claude_calls": 1}])
        self.assertIn("burn prediction is required", str(e.exception))
        self.control(stage_a_complete=True, predictions={"B1": {"five_hour": 250, "seven_day": 20}})
        record = self.go(b1, [], dry_run=True)                             # caps are min(1.5 x prediction, ceiling)
        self.assertEqual(record["stage_gate"]["caps"], {"five_hour": 300, "seven_day": 30})
        self.control(stage_a_complete=True, predictions={"B1": {"five_hour": 400, "seven_day": 20}})
        with self.assertRaises(I.Refusal) as e:
            self.go(b1, [{"claude_calls": 1}])
        self.assertIn("exceeds its ceiling", str(e.exception))

    def test_b2_waits_for_an_interpretable_b1_verdict(self):
        b2 = next(s["run_id"] for s in self.specs if s["stage"] == "B2" and s["stage_order"] == 0)
        self.done(*self.stage("A"))
        self.control(stage_a_complete=True)
        with self.assertRaises(I.Refusal) as e:
            self.go(b2, [{"claude_calls": 1}])
        self.assertIn("B2 requires an interpretable B1 verdict", str(e.exception))
        self.assertIn("B1 is not finished", str(e.exception))

    def test_a_retest_waits_twelve_hours_after_its_original_ended(self):
        retest = next(s for s in self.specs if s["stage"] == "B2" and s["retest_of"])
        plan = [s for s in self.specs if s["stage"] in ("A", "B1")]
        now = time.time()
        records = {s["run_id"]: {"outcome": "done", "ended": now - 3600} for s in plan}
        specs_by_id = {s["run_id"]: s for s in self.specs}
        records.update({s["run_id"]: {"outcome": "done", "ended": now - 3600} for s in self.specs
                        if s["stage"] == "B2" and s["stage_order"] < retest["stage_order"]})
        gate = G.eligibility(specs_by_id[retest["run_id"]], records, now, b1_verdict="pass", stage_a_complete=True,
                             specs=self.specs)
        self.assertFalse(gate["ready"])
        self.assertIn("12 actual hours", " ".join(gate["reasons"]))
        records[retest["retest_of"]]["ended"] = now - G.RETEST_DELAY
        gate = G.eligibility(specs_by_id[retest["run_id"]], records, now, b1_verdict="pass", stage_a_complete=True,
                             specs=self.specs)
        self.assertTrue(gate["ready"], gate["reasons"])

    def test_codex_waits_for_its_verification(self):
        c = next(s for s in self.specs if s["stage"] == "C" and s["stage_order"] == 0)
        records = {s["run_id"]: {"outcome": "done", "ended": time.time() - 60} for s in self.specs
                   if s["stage"] != "C"}
        gate = G.eligibility(c, records, time.time(), b1_verdict="pass", stage_a_complete=True, specs=self.specs)
        self.assertIn("Codex verification work is pending or unknown", gate["reasons"])
        gate = G.eligibility(c, records, time.time(), b1_verdict="pass", stage_a_complete=True,
                             codex_verification_pending=False, specs=self.specs)
        self.assertTrue(gate["ready"], gate["reasons"])

    def test_the_control_file_holds_only_its_attestations(self):
        self.control(stage_a_complete="yes")
        with self.assertRaises(ValueError):
            ST.load_plan(self.plan_dir)
        self.control(b1_verdict="pass")                                    # the verdict is computed, never typed
        with self.assertRaises(ValueError):
            ST.load_plan(self.plan_dir)


class Caps(Plan):
    def test_the_shim_stops_a_stage_at_its_cap(self):
        # Stage A's five-hour cap is 20 points. The first reading gives the meter a start (0 burn), the next two add
        # 10 and 11: the fourth call is refused by the shim before it reaches Claude Code, and STOP holds the run.
        (self.host.bin / "claude-script.json").write_text(json.dumps({"five_hour_steps": [0.10, 0.20, 0.31, 0.40]}))
        record = self.go(FIRST_A, [{"claude_calls": 4}])
        self.assertTrue(record["outcome"].startswith("stopped"), record["outcome"])
        self.assertIn("stage cap reached: five_hour", record["outcome"])
        self.assertEqual(len((self.host.bin / "claude-calls.jsonl").read_text().splitlines()), 3)
        self.assertIn("waits for Ephraiem's go", (self.root / "STOP").read_text())
        used, entry = ST.usage(self.ledger(), "A")
        self.assertEqual((used["five_hour"], entry["caps"]), (21.0, {"five_hour": 20, "seven_day": 5}))
        self.assertEqual(record["stage_gate"]["caps"], {"five_hour": 20, "seven_day": 5})

    def test_a_run_below_its_cap_finishes_and_a_resume_keeps_the_baseline(self):
        (self.host.bin / "claude-script.json").write_text(json.dumps({"five_hour_steps": [0.10, 0.12, 0.15, 0.16]}))
        record = self.go(FIRST_A, [{"claude_calls": 4}])
        self.assertEqual(record["outcome"], "done")
        first = self.ledger()["stages"]["A"]
        self.assertEqual(ST.usage(self.ledger(), "A")[0]["five_hour"], 6.0)
        self.go(FIRST_A, [{"claude_calls": 0}])                           # a resume: the stage is not opened again
        self.assertEqual(self.ledger()["stages"]["A"]["baseline"], first["baseline"])
        self.assertEqual(self.ledger()["stages"]["A"]["opened"], first["opened"])

    def test_launch_is_refused_once_the_stage_is_at_its_cap(self):
        A.apply(self.conf["home_dir"], "stage-open", {"stage": "A", "caps": G.stage_caps("A")})
        state = A.apply(self.conf["home_dir"], "read", {})
        state["burn"]["total"]["seven_day"] = 5.0                          # at the seven-day cap
        A.save(self.conf["home_dir"], state)
        record = self.go(FIRST_A, [{"claude_calls": 1}])
        self.assertIn("stage cap reached: seven_day", record["outcome"])
        self.assertFalse((self.host.bin / "claude-calls.jsonl").exists(), "no call was launched")

    def test_a_dry_run_opens_no_stage(self):
        record = self.go(FIRST_A, [], dry_run=True)
        self.assertEqual(record["outcome"], "dry run")
        self.assertIsNone(record["stage_gate"]["baseline"])
        self.assertNotIn("A", self.ledger()["stages"])

    def test_a_stages_caps_never_change_once_open(self):
        A.apply(self.conf["home_dir"], "stage-open", {"stage": "B1", "caps": {"five_hour": 30, "seven_day": 3}})
        A.apply(self.conf["home_dir"], "stage-open", {"stage": "B1", "caps": {"five_hour": 30, "seven_day": 3}})
        with self.assertRaises(ValueError):
            A.apply(self.conf["home_dir"], "stage-open", {"stage": "B1", "caps": {"five_hour": 60, "seven_day": 3}})

    def test_a_staged_room_whose_stage_is_not_open_refuses_calls(self):
        room = I.build(self.cfg, "unopened", "claude", "off", model="fake-model", stage="B1")
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        p = subprocess.run([str(room.bin_dir / "claude"), *fakes_print()], input="x", capture_output=True, text=True,
                           env=env, cwd=room.work_dir, timeout=30)
        self.assertEqual(p.returncode, 3)
        self.assertIn("not open", room.stop_path.read_text())

    def test_a_staged_call_is_refused_while_the_ledger_is_out_of_reach(self):
        room = I.build(self.cfg, "ledger-down", "claude", "off", model="fake-model", stage="A")
        A.apply(self.conf["home_dir"], "stage-open", {"stage": "A", "caps": G.stage_caps("A")})
        spec = json.loads((room.bin_dir / "room.json").read_text())
        spec["quota"]["cache_path"] = str(self.host.dir / "cache.json")
        (self.host.dir / "cache.json").write_text(json.dumps({"fetched": 0, "state": self.ledger()}))
        spec["quota"].update(home="user@fakehome", ssh=str(self.host.ssh_down))   # the ledger stops answering
        (room.bin_dir / "room.json").write_text(json.dumps(spec))
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        p = subprocess.run([str(room.bin_dir / "claude"), *fakes_print()], input="x", capture_output=True, text=True,
                           env=env, cwd=room.work_dir, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertIn("out of reach", room.stop_path.read_text())
        self.assertFalse((self.host.bin / "claude-calls.jsonl").exists())
        self.assertIsNone(ST.call_gate("A", self.ledger()))               # the same state, reachable, is fine

    def test_the_burn_counts_rises_and_whole_new_windows(self):
        now = time.time()
        # 0.36 starts the meter (0), 0.40 adds 4, 0.38 nothing, a later window's 0.05 counts whole (5), 0.12 adds 7.
        rows = [(0.36, now + 1000), (0.40, now + 1000), (0.38, now + 1000), (0.05, now + 20000), (0.12, now + 20000)]
        for i, (u, resets) in enumerate(rows):
            A.apply(self.conf["home_dir"], "publish", {"host": "h", "reading": {
                "t": now - 500 + i, "five_hour": u, "five_hour_resets": resets, "seven_day": .1,
                "seven_day_resets": now + 9e5}})
        self.assertEqual(self.ledger()["burn"]["total"], {"five_hour": 16.0, "seven_day": 0.0})


class CodexReservation(Plan):
    def test_concurrent_reservations_never_pass_the_cap(self):
        code = ("import json, sys; sys.path.insert(0, %r); import stages; conf = json.loads(sys.argv[1]); "
                "print(stages.reserve_codex(conf, 5)[0])" % str(HERE.parent))
        procs = [subprocess.Popen([sys.executable, "-c", code, json.dumps(self.conf)], stdout=subprocess.PIPE, text=True)
                 for _ in range(12)]
        granted = [p.communicate(timeout=60)[0].strip() for p in procs]
        self.assertEqual(granted.count("True"), 5)
        self.assertEqual(self.ledger()["stages"]["C"]["charged_requests"], 5)

    def test_the_reservation_applies_cap_status_rule(self):
        for count in range(4):
            state = self.ledger()
            used = state["stages"].get("C", {}).get("charged_requests", 0)
            allowed = G.cap_status("C", {"charged_requests": used})["allowed"]
            self.assertEqual(ST.reserve_codex(self.conf, G.CODEX_CAP)[0], allowed)
        state = self.ledger()
        state["stages"]["C"]["charged_requests"] = G.CODEX_CAP
        A.save(self.conf["home_dir"], state)
        self.assertFalse(G.cap_status("C", {"charged_requests": G.CODEX_CAP})["allowed"])
        self.assertEqual(ST.reserve_codex(self.conf, G.CODEX_CAP), (False, G.CODEX_CAP))

    def test_the_room_carries_the_registered_cap(self):
        room = I.build(self.cfg, "codex-cap", "codex", "on", memory_bytes=b"# synthetic\n")
        spec = json.loads((room.bin_dir / "room.json").read_text())
        self.assertEqual(spec["codex_cap"], 1000)                         # CALIBRATION 7, revision 3
        self.assertEqual(spec["codex_cap"], G.CODEX_CAP)


def fakes_print():
    return ["-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--settings",
            '{"disableAllHooks":true}', "--output-format", "json", "--model", "fake-model"]


if __name__ == "__main__":
    unittest.main()
