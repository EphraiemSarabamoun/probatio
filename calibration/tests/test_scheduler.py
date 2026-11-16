"""The account-level quota scheduler: the ledger on its home (account.py), reached in-process or over ssh by shipping
the file itself; the decision from the newest reading of any host with the ledger's one pair of limits; the cache that
keeps ssh off most calls; the degraded fallback when the home is away; and the shim, the clean-room check and the run
acting on another host's reading. ssh is a fake that runs the remote command on this host (fakes.py), so the real
transport is exercised; no test calls a model or the network.

    python3 -m unittest discover -s calibration/tests
"""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import account as A  # noqa: E402
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import quota as Q  # noqa: E402
import run as R  # noqa: E402
import scheduler as SC  # noqa: E402
import shim as SH  # noqa: E402

NOW = time.time()


def reading(five=0.10, seven=0.05, t=None, five_resets=None, seven_resets=None, status="allowed"):
    info = {"status": status, "unifiedWindows": {
        "five_hour": {"utilization": five, "resetsAt": five_resets or NOW + 3600},
        "seven_day": {"utilization": seven, "resetsAt": seven_resets or NOW + 5 * 86400}}}
    return Q.reading(info, NOW if t is None else t, "run-x")


def _publish_many(home_dir, n, base):
    for i in range(n):
        A.apply(home_dir, "publish", {"reading": reading(five=round(0.1 + base / 1e5 + i / 1e4, 4), t=base + i,
                                                         five_resets=10 ** 6, seven_resets=10 ** 6), "host": f"h{base}"})


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "account"

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_folds_readings_into_the_meter_and_notes_every_host(self):
        A.apply(self.home, "publish", {"reading": reading(five=0.5, t=NOW), "host": "host-a"})
        A.apply(self.home, "publish", {"reading": reading(five=0.3, t=NOW + 5), "host": "host-b"})   # lower, same window
        state = A.apply(self.home, "read", {})
        self.assertEqual(state["version"], 2)
        five = state["meter"]["windows"]["five_hour"]
        self.assertEqual((five["u"], five["host"]), (0.5, "host-a"), "a lower reading never lowers the mark")
        self.assertEqual(set(state["hosts"]), {"host-a", "host-b"})
        A.apply(self.home, "publish", {"reading": reading(five=0.05, t=NOW + 4000, five_resets=NOW + 20000),
                                       "host": "host-b"})                                   # a new window
        self.assertEqual(A.apply(self.home, "read", {})["meter"]["windows"]["five_hour"]["u"], 0.05)

    def test_a_version_one_ledger_is_read_as_a_meter(self):
        self.home.mkdir(parents=True)
        (self.home / "account.json").write_text(json.dumps({"version": 1, "limits": None, "hosts": {},
                                                            "updated": None, "newest": reading(five=0.7, t=NOW)}))
        state = A.apply(self.home, "read", {})
        self.assertEqual((state["version"], state["meter"]["windows"]["five_hour"]["u"]), (2, 0.7))

    def test_limits_are_set_once_by_init_and_changed_only_by_set_limits(self):
        A.apply(self.home, "init", {"limits": {"five_hour": 0.6, "seven_day": 0.6}})
        A.apply(self.home, "init", {"limits": {"five_hour": 0.9, "seven_day": 0.9}})
        self.assertEqual(A.apply(self.home, "read", {})["limits"], {"five_hour": 0.6, "seven_day": 0.6})
        A.apply(self.home, "set-limits", {"limits": {"five_hour": 0.5, "seven_day": 0.55}})
        self.assertEqual(A.apply(self.home, "read", {})["limits"], {"five_hour": 0.5, "seven_day": 0.55})
        for bad in ({"five_hour": 0.5}, {"five_hour": 2, "seven_day": 0.5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                A.apply(self.home, "set-limits", {"limits": bad})

    def test_writers_on_many_processes_leave_one_whole_ledger(self):
        procs = [multiprocessing.Process(target=_publish_many, args=(str(self.home), 25, base)) for base in
                 (1000, 2000, 3000, 4000)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        state = A.apply(self.home, "read", {})
        self.assertEqual(state["meter"]["windows"]["five_hour"]["t"], 4024)
        self.assertEqual(state["meter"]["windows"]["five_hour"]["u"], round(0.1 + 4000 / 1e5 + 24 / 1e4, 4))
        self.assertEqual(len(state["hosts"]), 4)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["account.json", "account.lock"])

    def test_a_foreign_file_is_refused(self):
        self.home.mkdir(parents=True)
        (self.home / "account.json").write_text('{"version": 99}')
        with self.assertRaises(ValueError):
            A.apply(self.home, "read", {})


class Hosts(unittest.TestCase):
    """Two hosts of one account: host-a keeps the ledger, host-b reaches it over (fake) ssh."""

    def setUp(self):
        self.host = fakes.Host()
        self.home_dir = str(self.host.dir / "host-a" / "account")
        self.host_a = SC.Scheduler(self.conf("host-a", "local"))
        self.host_b = SC.Scheduler(self.conf("host-b", "user@fakehome"))

    def tearDown(self):
        self.host.close()

    def conf(self, name, home, ssh=None, limits=None, cache_seconds=30):
        root = self.host.dir / name
        root.mkdir(exist_ok=True)
        cfg = {"quota": dict(I.DEFAULTS["quota"], home=home, home_dir=self.home_dir, host=name, ssh=str(ssh or self.host.ssh),
                             cache_seconds=cache_seconds, **(limits or {}))}
        return SC.conf_from(cfg, root)

    def test_a_reading_from_either_host_pauses_both(self):
        self.host_a.publish(reading(five=0.40, t=NOW - 60))
        self.host_b.publish(reading(five=0.65, t=NOW - 10))        # newer, over the 0.60 default
        until, why, info = self.host_a.pause(now=NOW)
        self.assertEqual(until, NOW + 3600 + Q.RESUME_MARGIN_S)
        self.assertEqual((info["decided_by"], info["degraded"]), ("host-b", False))
        self.assertIn("five_hour 0.65", why)
        self.assertIsNotNone(self.host_b.pause(now=NOW)[0])

    def test_a_newer_reading_after_the_reset_resumes_both(self):
        self.host_b.publish(reading(five=0.65, t=NOW - 10))
        later = NOW + 4000
        self.host_a.publish(reading(five=0.05, t=later, five_resets=later + 18000), now=later)
        self.assertEqual(self.host_a.pause(now=later)[:2], (None, None))
        self.assertEqual(self.host_b.pause(now=later + 60)[:2], (None, None))   # its cache is stale by then: it reads

    def test_the_ledgers_limits_hold_on_every_host(self):
        self.assertEqual(self.host_a.check(), [])                   # seeds the ledger with 0.60 / 0.60
        loose = SC.Scheduler(self.conf("host-b2", "user@fakehome", limits={"five_hour": 0.70, "seven_day": 0.70}))
        problems = loose.check()
        self.assertTrue(problems and "limits" in problems[0])
        self.host_a.publish(reading(five=0.65, t=NOW - 5))
        self.assertIsNotNone(loose.pause(now=NOW)[0], "the ledger's 0.60 decides, not this host's 0.70")
        A.apply(self.home_dir, "set-limits", {"limits": {"five_hour": 0.70, "seven_day": 0.70}})
        self.assertEqual(loose.pause(now=NOW + 31)[:2], (None, None))

    def test_the_cache_keeps_ssh_off_the_reads_and_every_reading_is_sent(self):
        self.host_b.publish(reading(five=0.30, t=NOW - 20), now=NOW)
        n = len(self.host.ssh_calls())
        for dt in (1, 2, 3):
            self.host_b.pause(now=NOW + dt)                                       # fresh cache: no ssh
        self.assertEqual(len(self.host.ssh_calls()), n)
        self.host_b.publish(reading(five=0.30, t=NOW + 4), now=NOW + 4)          # the same step: still sent, since
        self.assertEqual(len(self.host.ssh_calls()), n + 1)                      # the stage burn sums every reading
        self.host_b.pause(now=NOW + 40)                                            # stale cache: read
        self.assertEqual(len(self.host.ssh_calls()), n + 2)
        self.assertEqual(self.host.ssh_calls()[-1][-2], "read")

    def test_an_unreachable_home_degrades_to_this_hosts_readings(self):
        down = SC.Scheduler(self.conf("host-b3", "user@fakehome", ssh=self.host.ssh_down))
        self.assertTrue(down.publish(reading(five=0.62, t=NOW)))               # degraded, but logged locally
        self.assertEqual(Q.latest(down.conf["log_path"])["five_hour"], 0.62)
        until, why, info = down.pause(now=NOW)
        self.assertTrue(info["degraded"])
        self.assertIsNotNone(until, "its own reading still pauses it")
        problems = down.check()
        self.assertTrue(problems and "cannot be reached" in problems[0])

    def test_a_degraded_host_catches_the_ledger_up_when_it_returns(self):
        down = SC.Scheduler(self.conf("host-b4", "user@fakehome", ssh=self.host.ssh_down))
        down.publish(reading(five=0.66, t=NOW))
        up = SC.Scheduler(dict(down.conf, ssh=str(self.host.ssh)))
        up.publish(reading(five=0.66, t=NOW + 1), now=NOW + 1)
        self.assertEqual(A.apply(self.home_dir, "read", {})["meter"]["windows"]["five_hour"]["u"], 0.66)


class Harness(unittest.TestCase):
    """The shim, the clean-room check and the run, acting on a reading another host published."""

    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.root = Path(self.cfg["root"])
        self.root.mkdir(parents=True)
        (self.host.dir / "host-b-root").mkdir()
        # the other host publishes into this host's ledger (its transport is Hosts' subject; here it is in-process)
        self.other = SC.Scheduler(dict(SC.conf_from(self.cfg, self.host.dir / "host-b-root"), host="host-b",
                                       home_dir=str(self.root / "account")))

    def tearDown(self):
        self.host.close()

    def test_the_shim_refuses_on_another_hosts_reading(self):
        room = I.build(self.cfg, "r1", "claude", "on", I.EFFORT, b"# memory\n")
        self.other.publish(reading(five=0.70, t=time.time()))
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        p = subprocess.run([str(room.bin_dir / "claude"), "-p", "--output-format", "json"], input="x",
                           capture_output=True, text=True, env=env, timeout=60, cwd=room.work_dir)
        self.assertEqual(p.returncode, SH.PAUSED)
        self.assertIn("reading from host-b", json.loads(p.stdout)["result"])
        self.assertFalse((self.host.bin / "claude-calls.jsonl").exists(), "the model was not called")

    def test_the_shim_publishes_to_the_ledger(self):
        room = I.build(self.cfg, "r2", "claude", "on", I.EFFORT, b"# memory\n")
        (self.host.bin / "claude-script.json").write_text(json.dumps({"rate_limit_info": {
            "status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.21, "resetsAt": time.time() + 3600},
                                                    "seven_day": {"utilization": 0.09, "resetsAt": time.time() + 9e4}}}}))
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        subprocess.run([str(room.bin_dir / "claude"), "-p", "--output-format", "json"], input="x",
                       capture_output=True, text=True, env=env, timeout=60, cwd=room.work_dir)
        five = A.apply(str(self.root / "account"), "read", {})["meter"]["windows"]["five_hour"]
        self.assertEqual(five["u"], 0.21)

    def test_room_json_carries_the_scheduler_and_no_secret(self):
        room = I.build(self.cfg, "r3", "claude", "on", I.EFFORT, b"# memory\n")
        spec = json.loads((room.bin_dir / "room.json").read_text())
        self.assertEqual(spec["quota"]["limits"], Q.DEFAULT_LIMITS)
        self.assertEqual(spec["quota"]["home"], "local")
        self.assertNotIn(fakes.TOKEN, (room.bin_dir / "room.json").read_text())

    def test_the_check_refuses_a_ledger_with_other_limits(self):
        room = I.build(self.cfg, "r4", "claude", "on", I.EFFORT, b"# memory\n")
        A.apply(str(self.root / "account"), "init", {"limits": {"five_hour": 0.4, "seven_day": 0.4}})
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        found = I.problems(room, self.cfg, env, fakes.clean_environ())
        self.assertTrue(any("limits" in p for p in found), found)

    def test_local_json_quota_block_is_validated(self):
        for bad in ({"five_hour": 1.2}, {"home": "host-a; rm"}, {"seven_day": "high"}):
            data = json.loads(self.host.local.read_text())
            data["quota"] = bad
            self.host.local.write_text(json.dumps(data))
            with self.subTest(bad=bad), self.assertRaises(I.Refusal):
                I.load_local(self.host.local)

    def test_a_run_waits_on_another_hosts_reading(self):
        clock = {"t": time.time()}
        plan = self.host.dir / "plan.json"
        plan.write_text(json.dumps([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}],
                                     "rc": 0}]))
        self.other.publish(reading(five=0.75, t=clock["t"], five_resets=clock["t"] + 900))
        env = dict(fakes.clean_environ(), FAKE_PLAN=str(plan), FAKE_QUOTA=str(self.host.dir / "unused.jsonl"),
                   FAKE_STOP=str(self.root / "STOP"))
        spec = {"run_id": "r5", "agent": "claude", "model": "m", "memory": "on", "jobs": 1, "cli": "v4",
                "effort": "medium", "probatio_args": [], "memory_bytes": b"# m\n"}

        def sleep(s):
            clock["t"] += s

        with unittest.mock.patch.dict(os.environ, env, clear=True):
            record = R.run(spec, self.cfg, sleep=sleep, now=lambda: clock["t"], log=lambda m: None)
        self.assertEqual(record["outcome"], "done")
        self.assertEqual(record["pauses"][0]["decided_by"], "host-b")
        self.assertEqual(record["pauses"][0]["limits"], Q.DEFAULT_LIMITS)


if __name__ == "__main__":
    unittest.main()
