"""Tests for the live line (live.LiveLine; the design of the quick run and the run you can watch, 4.1): display
columns with wide, combining and format characters, clipping at 50 and 20 columns, sanitized labels, the in-place line
in a terminal, checkpoints without one and below 30 columns (no CR or escape, at most 11 lines, a final newline), one
lock with no interleaved writes, the time left at the run's own pace, the quota, and launches.jsonl from run_calls.
No test calls an agent or the network.

    python3 -m unittest discover -s tests
"""
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import live as L  # noqa: E402
import probatio as P  # noqa: E402

CJK = "東京大学研究所"                                 # seven wide characters, 14 columns
COMBINING = "Amélie Zöe Nũnez"        # three combining marks, each 0 columns
NAMES = (CJK + " · " + COMBINING + " · " + "Ελληνική Δημοκρατία").strip()


class Stream(io.StringIO):
    """stderr as a test sees it: every write kept whole, with an isatty answer."""

    def __init__(self, tty):
        super().__init__()
        self.tty, self.writes = tty, []

    def isatty(self):
        return self.tty

    def write(self, text):
        self.writes.append(text)
        return super().write(text)


def plan(n, errands=0):
    """n calls: questions, then errands in N0, as v4's plan shapes them (key, meta, episode)."""
    calls = [{"condition": "with", "dataset": "v4.I", "id": f"q{i}", "meta": {"plan": "pair"}}
             for i in range(n - errands)]
    calls += [{"condition": "with", "dataset": "v4.II", "id": f"e{i}", "meta": {"cell": "N0"}, "episode": True}
              for i in range(errands)]
    return calls


def line(tty=False, width=100, jobs=1, prior=10.0, quota=None, label=None):
    stream = Stream(tty)
    t = {"now": 0.0}
    out = L.LiveLine(jobs, lambda c: prior, quota=quota, label=label, stream=stream, tty=tty,
                     width=lambda: width, clock=lambda: t["now"])
    out.ticker = True                 # no ticker thread in these tests: draws come from the calls
    return out, stream, t


def answered(call, outcome="x", error=None, seconds=4.0):
    return {"condition": call["condition"], "dataset": call["dataset"], "id": call["id"], "outcome": outcome,
            "error": error, "seconds": seconds}


class TestColumns(unittest.TestCase):
    def test_wide_combining_and_format_characters(self):
        self.assertEqual(L.columns(CJK), 14)
        self.assertEqual(L.columns(COMBINING), len(COMBINING) - 3)
        self.assertEqual(L.columns("a‍b‮c"), 3)                 # a zero-width joiner and a bidi override
        self.assertEqual(L.columns("ＡＢ"), 4)                             # fullwidth

    def test_clip_keeps_whole_characters_and_marks_with_their_base(self):
        for n in (50, 20, 7, 2):
            for text in (NAMES, CJK * 3, COMBINING * 3):
                got = L.clip(text, n)
                self.assertLessEqual(L.columns(got), n, (text, n))
                if L.columns(text) > n:
                    self.assertTrue(got.endswith("…"))
                    body = got[:-1]
                    self.assertTrue(text.startswith(body))
                    nxt = text[len(body):len(body) + 1]
                    self.assertFalse(nxt and L.char_columns(nxt) == 0, "a mark was cut off its base")
        self.assertEqual(L.clip("short", 50), "short")
        self.assertEqual(L.clip(CJK, 4), "東…")                            # 2 columns + the ellipsis, never half
        self.assertEqual(L.clip("Amélie", 4), "Amé…")          # the accent stays with its e

    def test_labels_lose_controls_and_bidi_overrides(self):
        self.assertEqual(L.plain_label("Moz\x1b[31milla‮ \r\n Labs\x07"), "Moz[31milla Labs")
        self.assertEqual(L.plain_label(COMBINING), COMBINING)


class TestTerminal(unittest.TestCase):
    def test_in_place_with_clipping_at_50_and_20_columns(self):
        for width in (51, 31):
            ll, s, t = line(tty=True, width=width, label=NAMES)
            calls = plan(40)
            ll.begin(calls, {})
            for c in calls[:7]:
                t["now"] += 3
                ll.finished(c, answered(c))
            frames = [w for w in s.writes if w.startswith("\r")]
            self.assertTrue(frames)
            for f in frames:
                self.assertTrue(f.startswith("\r  ") and f.endswith("\x1b[K"))
                self.assertLessEqual(L.columns(f[3:-3]), width - 2)
            ll.close()
            self.assertTrue(s.getvalue().endswith("\n"))
            self.assertIn("7/40", s.writes[-2])

    def test_the_line_says_what_it_should(self):
        quota = SimpleNamespace(latest=lambda: {"five_hour": 0.18, "seven_day": 0.24})
        ll, s, t = line(tty=True, jobs=2, prior=10.0, quota=quota)
        calls = plan(10, errands=4)
        ll.begin(calls, {})
        self.assertEqual(ll.text(), "0/10 · 0 valid · 0 abstained · 0s · under a minute left · 5-hour 18%, 7-day 24%")
        ll.finished(calls[0], answered(calls[0], outcome=None))                       # an abstention
        ll.finished(calls[1], answered(calls[1], error="exit 1"))                      # an error
        t["now"] = 531
        text = ll.text()
        self.assertTrue(text.startswith("2/10 · 0 valid · 1 abstained · 1 errors · 8m 51s · "), text)
        self.assertNotIn("errors", line()[0].text())                                    # only when there are errors

    def test_the_tier_leads_the_line(self):
        stream = Stream(False)
        ll = L.LiveLine(4, lambda c: 1.0, stream=stream, tier="quick", clock=lambda: 0.0)
        ll.begin(plan(4), {})
        self.assertTrue(ll.text().startswith("quick · 0/4 · 0 valid"))
        ll.close()
        self.assertTrue(stream.getvalue().startswith(L.PREFIX + "quick · 0/4"))

    def test_time_left_uses_this_launchs_mean_after_three_of_a_kind(self):
        ll, s, t = line(jobs=2, prior=100.0)
        calls = plan(8, errands=4)
        ll.begin(calls, {})
        self.assertEqual(ll.left(), 8 * 100 / 2)
        for c in calls[:2]:
            ll.finished(c, answered(c, seconds=4.0))
        self.assertEqual(ll.left(), 6 * 100 / 2)                                       # two questions: still the prior
        ll.finished(calls[2], answered(calls[2], seconds=4.0))
        self.assertEqual(ll.left(), (1 * 4.0 + 4 * 100) / 2)                           # three: the questions' mean
        for c in calls[4:7]:
            ll.finished(c, answered(c, outcome={"solved": 1}, seconds=20.0))
        self.assertEqual(ll.left(), (4.0 + 20.0) / 2)

    def test_resumed_calls_count_as_done(self):
        ll, s, t = line()
        calls = plan(10)
        done = {P.key(c): answered(c) for c in calls[:4]}
        done[P.key(calls[4])] = answered(calls[4], error="timeout")          # an error is retried, so pending
        ll.begin(calls, done)
        self.assertEqual((ll.done, ll.valid, len(ll.pending)), (4, 4, 6))


class TestCheckpoints(unittest.TestCase):
    def run_line(self, tty, width, n=137, resumed=0):
        ll, s, t = line(tty=tty, width=width, label=CJK)
        calls = plan(n)
        ll.begin(calls, {P.key(c): answered(c) for c in calls[:resumed]})
        for c in calls[resumed:]:
            t["now"] += 1
            ll.finished(c, answered(c))
        ll.close()
        return s.getvalue()

    def test_no_terminal_prints_at_most_eleven_plain_lines(self):
        for n in (137, 5, 1, 1000):
            text = self.run_line(False, 100, n)
            self.assertNotIn("\r", text)
            self.assertNotIn("\x1b", text)
            self.assertTrue(text.endswith("\n"))
            lines = text.splitlines()
            self.assertLessEqual(len(lines), 11)
            self.assertTrue(all(x.startswith(L.PREFIX) for x in lines))
            self.assertIn(f"{n}/{n}", lines[-1])

    def test_narrow_terminals_fall_back_to_checkpoints(self):
        for width in (29, 20):
            text = self.run_line(True, width)
            self.assertNotIn("\r", text)
            self.assertNotIn("\x1b", text)
            self.assertLessEqual(len(text.splitlines()), 11)

    def test_a_resumed_run_does_not_repeat_the_tenths_it_has_passed(self):
        text = self.run_line(False, 100, n=100, resumed=55)
        self.assertEqual(len(text.splitlines()), 5 + 1)            # 60, 70, 80, 90, 100 and the end

    def test_a_terminal_that_becomes_narrow_leaves_its_line_first(self):
        ll, s, t = line(tty=True, width=80)
        calls = plan(10)
        ll.begin(calls, {})
        ll.width = lambda: 20
        ll.finished(calls[0], answered(calls[0]))
        self.assertEqual(s.writes[-1], L.PREFIX + ll.text() + "\n")
        self.assertEqual(s.writes[-2], "\n")

    def test_say_prints_above_the_line(self):
        ll, s, t = line(tty=True, width=80)
        ll.begin(plan(4), {})
        ll.say("resuming: 1 of 4 calls already done")
        self.assertEqual(s.writes[-2], "\r\x1b[Kresuming: 1 of 4 calls already done\n")
        self.assertTrue(s.writes[-1].startswith("\r  0/4"))
        quiet, q, _ = line(tty=False)
        quiet.begin(plan(4), {})
        quiet.say("resuming: 1 of 4 calls already done")
        self.assertEqual(q.getvalue(), "resuming: 1 of 4 calls already done\n")


class TestLock(unittest.TestCase):
    def test_threads_and_messages_never_interleave(self):
        ll, s, t = line(tty=True, width=120)
        calls = plan(400)
        ll.begin(calls, {})
        go = threading.Barrier(8)

        def worker(part):
            go.wait()
            for c in part:
                ll.finished(c, answered(c))
                if c["id"].endswith("7"):
                    ll.say(f"note {c['id']}")
        threads = [threading.Thread(target=worker, args=(calls[i::8],)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        ll.close()
        for w in s.writes:            # each write is one whole frame or one whole message
            self.assertTrue((w.startswith("\r  ") and w.endswith("\x1b[K")) or w.endswith("\n"), repr(w))
        self.assertEqual(ll.done, 400)

    def test_the_ticker_redraws_and_stops(self):
        stream = Stream(True)
        ll = L.LiveLine(1, lambda c: 1.0, stream=stream, tty=True, width=lambda: 80)
        with mock.patch.object(L, "TICK_SECONDS", 0.01):
            ll.begin(plan(3), {})
            threading.Event().wait(0.1)
            ll.close()
        n = len(stream.writes)
        self.assertGreater(n, 3)
        threading.Event().wait(0.05)
        self.assertEqual(len(stream.writes), n)
        self.assertTrue(stream.getvalue().endswith("\n"))


class TestQuota(unittest.TestCase):
    def test_quota_log_keeps_the_newest_reading_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            log = P.QuotaLog(Path(d) / "quota.jsonl")
            self.assertIsNone(log.latest())
            info = lambda a, b: {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": a},   # noqa
                                                                          "seven_day": {"utilization": b}}}
            log.note("c1", "with", {"init": {}, "rate_limits": [info(0.1, 0.2), info(0.18, 0.24)]})
            log.note("c2", "with", {"init": {}, "rate_limits": [{"status": "allowed"}]})
            self.assertEqual(log.latest(), {"five_hour": 0.18, "seven_day": 0.24})
            self.assertEqual(L.quota_text(log.latest()), "5-hour 18%, 7-day 24%")
        self.assertIsNone(P.window_reading({"unifiedWindows": {"five_hour": {"utilization": True}}}))


class TestLaunches(unittest.TestCase):
    def test_run_calls_records_each_launch_and_draws_the_line(self):
        calls = [{"condition": "with", "dataset": "v4.I", "config": "c", "id": f"q{i}", "prompt": "p",
                  "row": {"id": f"q{i}", "answer_format": "bare_letter",
                          "choices": [{"label": "A", "maps_to": "a"}, {"label": "B", "maps_to": "b"}]},
                  "meta": {"plan": "pair"}} for i in range(6)]
        fake = lambda prompt, condition, opts: ("A", None, "m")          # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(P.CALLS, {"fake": fake}):
            out = Path(d)
            stream = Stream(False)
            ll = L.LiveLine(2, lambda c: 1.0, stream=stream)
            P.run_calls(calls[:3], "fake", None, out, 2, line=ll)
            ll2 = L.LiveLine(2, lambda c: 1.0, stream=Stream(False))
            P.run_calls(calls, "fake", None, out, 2, line=ll2)
            lines = [json.loads(x) for x in (out / "launches.jsonl").read_text().splitlines()]
            self.assertEqual([(x["ended"] is None, x["made"], x["jobs"]) for x in lines],
                             [(True, 0, 2), (False, 3, 2), (True, 0, 2), (False, 3, 2)])
            self.assertEqual(P.read_launches(out)["launches"], 2)
            self.assertEqual(P.read_launches(out)["unfinished"], 0)
            self.assertIn("3/3 · 3 valid", stream.getvalue())
            self.assertEqual((ll2.done, ll2.total), (6, 6))

    def test_read_launches_sums_the_ended_launches(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [{"started": "2026-11-08T10:00:00.000Z", "ended": None, "made": 0, "jobs": 4},
                    {"started": "2026-11-08T10:00:00.000Z", "ended": "2026-11-08T10:07:29.000Z", "made": 180,
                     "jobs": 4},
                    {"started": "2026-11-08T10:30:00Z", "ended": None, "made": 0, "jobs": 4},
                    {"started": "2026-11-08T10:30:00Z", "ended": "2026-11-08T10:30:00.500Z", "made": 1, "jobs": 4},
                    {"started": "2026-11-08T11:00:00Z", "ended": None, "made": 0, "jobs": 4}]     # killed: no end
            (Path(d) / "launches.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "{torn")
            self.assertEqual(P.read_launches(d), {"elapsed_seconds": 449.5, "launches": 3, "unfinished": 1})
            self.assertEqual(P.read_launches(Path(d) / "none")["launches"], 0)

    def test_a_stop_ends_the_line_before_its_message(self):
        calls = [{"condition": "with", "dataset": "v4.I", "config": "c", "id": f"q{i}", "prompt": "p",
                  "row": {"id": f"q{i}", "answer_format": "bare_letter", "choices": []}, "meta": {"plan": "pair"}}
                 for i in range(P.MAX_ERRORS_IN_A_ROW + 2)]
        fake = lambda prompt, condition, opts: (None, "exit 1: no", None)         # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(P.CALLS, {"fake": fake}):
            stream = Stream(True)
            ll = L.LiveLine(1, lambda c: 1.0, stream=stream, tty=True, width=lambda: 80)
            with self.assertRaises(SystemExit) as stop:
                P.run_calls(calls, "fake", None, Path(d), 1, line=ll)
            self.assertTrue(str(stop.exception).startswith("stopped: "))
            self.assertTrue(stream.getvalue().endswith("\n"))
            ended = [json.loads(x) for x in (Path(d) / "launches.jsonl").read_text().splitlines()][-1]
            self.assertIsNotNone(ended["ended"])

    def test_v3_keeps_its_own_line(self):
        calls = [{"condition": "with", "dataset": "sacramentum", "config": "c", "id": "q0", "prompt": "p",
                  "row": {"id": "q0", "answer_format": "bare_letter", "choices": []}}]
        fake = lambda prompt, condition, opts: ("A", None, "m")          # noqa: E731
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(P.CALLS, {"fake": fake}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            P.run_calls(calls, "fake", None, Path(d), 1)
            self.assertFalse((Path(d) / "launches.jsonl").exists())          # v3's folder is as it was
        self.assertIn("1/1 calls, 0 errors", err.getvalue())


if __name__ == "__main__":
    unittest.main()
