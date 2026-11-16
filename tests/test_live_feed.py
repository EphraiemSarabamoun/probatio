"""Tests for feed.py, the live page's entries (the live-feed ruling, V1, V2, V5 to V10, V12): one entry per call of the
plan; what was sent is the call's own prompt, byte for byte; the options are that prompt's own lines with each
choice's meaning from the row's answer map, never the bank's unrendered text; how a reply was read is the scorer's own
reading; an errand's task, key, progress and verdict are the scorer's own; every class and category word is one the
kind's scorecard (or README.md, for the single-target red flags' names) prints; the published dataset and configuration
table matches the Hub's own row ids (HUB_CHECK, recorded in tests/fixtures/hub_ids.json); the page's request is read
by digits alone, never echoed; the results link (V3) serves each kind's own page (a cross-type run's
ordo/results.html) through the form's pinned reader; and the server's guards hold on the page's own routes.
Planted folders only, made with the suite's own planters; no agent, no network.

    python3 -m unittest tests.test_live_feed
"""
import contextlib
import copy
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests"), str(REPO / "tests" / "fixtures")]
import feed as F  # noqa: E402
import live as L  # noqa: E402
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import test_ordo_scoring as TO  # noqa: E402
import test_rescore as TR  # noqa: E402
import test_ui as TU  # noqa: E402  (its offline headless Chrome and wait_for)
import test_watch_v4 as TW  # noqa: E402
import test_v4_scoring as VS  # noqa: E402
import test_versus_watch as TVW  # noqa: E402
import ui as U  # noqa: E402
import versus as V  # noqa: E402
import versus_page as VP  # noqa: E402
import versus_run as VR  # noqa: E402

HUB_CHECK = REPO / "tests" / "fixtures" / "hub_ids.json"     # the Hub's row ids, per dataset and configuration
README = (REPO / "README.md").read_text(encoding="utf-8")
SCORING = (REPO / "docs" / "v4" / "SCORING.md").read_text(encoding="utf-8")


def records(out):
    """The newest committed record of each call key in a folder's replies.jsonl."""
    return {P.key(r): r for r in (json.loads(x) for x in (Path(out) / "replies.jsonl").read_text().splitlines())}


def log_of(out, call):
    """An errand's log as the page's Card reads it: its record's call lines and its report's state."""
    folder = P.episode_dir(out, call)
    calls = [x for x in P.read_record(folder / "record.jsonl") if x.get("kind") == "call"] \
        if (folder / "record.jsonl").exists() else []
    return calls, L.read_followup(folder)


def ids_digest(ids):
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def pinned_running(out, tier="smoke"):
    """A single-target run on the pinned bank, whose errand worlds serve their chat message as the published items do
    (bank_small's worlds serve none), caught mid-run: every call planted, then the plan's first errand task started
    afresh with a record that has read three pages and one record and filed nothing. Returns (bank, roster, calls,
    meta, kind), the kind built as probatio builds it (the bank's sources and cells)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    roster = P.resolve_target(b, "organization", "Mozilla")
    calls = P.plan_v4(b, roster, tier, P.V4_CLASSES, ("with",))
    VS.plant(out, calls, VS.favour(0.8, 0.4), VS.errand_favour(0.9, 0.3))
    meta = VS.run_meta(b, roster, calls, tier, "on", P.V4_CLASSES)
    (out / "run.json").write_text(json.dumps(meta, indent=2))
    TW.start_running(out, calls, lambda c: VS.record_lines([1, 2, 3], 10, ["TX-1"])[:-1])
    sources = {r["template_id"]: r.get("source") for r in b.class1 + b.errands}
    return b, roster, calls, meta, F.SingleKind(roster, sources, P.bank_cells(b), F.answer_map(b.class1, F.slot_entity))


def chat_of(call):
    """The chat message(s) an errand's world serves, as its chat_read returns them (state.mail.chat_read, or the
    older layout's state.chat); an empty list for a world that serves none."""
    state = json.loads(call["world"]).get("state") or {}
    return (state.get("mail") or {}).get("chat_read") or state.get("chat") or []


def scorecard_text(out):
    """Every file a scored run wrote that a reader reads, as one text: scorecard.md, report.md and results.html (a
    cross-type run writes its scorecard.md and report.md in ordo/)."""
    return "\n".join((Path(out) / f).read_text(encoding="utf-8")
                     for f in ("scorecard.md", "report.md", "results.html", "ordo/scorecard.md", "ordo/report.md")
                     if (Path(out) / f).exists())


class Planted:
    """A scored planted run (test_rescore's own planter) and its entries, every call opened; mixed into a TestCase per
    kind of run, with PLANT the planter and plan() the run's calls and its Kind."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="probatio-feed-"))
        cls.out = cls.tmp / "run"
        cls.out.mkdir()
        cls.meta = cls.PLANT(cls.out)
        cls.calls, cls.kind = cls.plan()
        cls.done = records(cls.out)
        cls.entries = {}
        for i, c in enumerate(cls.calls):
            rec = cls.done.get(P.key(c))
            if rec is None:
                continue
            log = log_of(cls.out, c) if c.get("episode") else None
            cls.entries[P.key(c)] = F.entry(cls.kind, c, i, rec, log, opened=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_entry_per_landed_call_each_with_its_own_id(self):
        """Every committed record is one entry, and every entry's id is its call's place in the plan."""
        self.assertEqual(len(self.entries), len(self.done))
        ids = [e["id"] for e in self.entries.values()]
        self.assertEqual(len(ids), len(set(ids)))
        for k, e in self.entries.items():
            self.assertEqual(P.key(self.calls[e["id"]]), k)

    def test_what_was_sent_is_the_prompt_and_the_options_are_its_own_lines(self):
        """sent is the call's prompt byte for byte; each option's line is a line of that prompt; its meaning is the
        row's own maps_to, named; no choice's bank text (an unrendered template) is in an entry."""
        for k, e in self.entries.items():
            call = self.calls[e["id"]]
            if call.get("episode"):
                self.assertEqual(e["open"]["task"]["opening"], call["prompt"])
                continue
            self.assertEqual(e["open"]["sent"], call["prompt"])
            lines = call["prompt"].split("\n")
            choices = call["row"].get("choices") or []
            for o, c in zip(e["open"]["options"], choices):
                self.assertEqual(o["label"], c["label"])
                if o["line"] is not None:
                    self.assertIn(f"{o['label']}) {o['line']}", lines)
                self.assertEqual(o["means"], self.kind.means(call, c))
            text = json.dumps(e, ensure_ascii=False)
            self.assertNotIn("⟦", text)
            for c in choices:
                if c.get("text") and "⟦" in c["text"]:
                    self.assertNotIn(c["text"], text)

    def test_the_reply_is_read_as_the_scorer_reads_it(self):
        """read_as is the scorer's parsed answer; means the scorer's outcome in the run's names; valid as v4 counts."""
        for k, e in self.entries.items():
            call, rec = self.calls[e["id"]], self.done[k]
            if call.get("episode"):
                continue
            answer, outcome = P.score(call["row"], rec["reply"]) if not rec.get("error") else (None, None)
            self.assertEqual(e["open"]["action"]["read_as"], F.answer_words(answer))
            self.assertEqual(e["open"]["action"]["valid"], outcome is not None)
            self.assertEqual(e["open"]["action"]["reply"], rec.get("reply"))
            if outcome is not None:
                self.assertEqual(e["line"]["action"], self.kind.outcome_words(call, rec))
                self.assertIsNone(e["line"]["mark"])
            else:
                self.assertEqual(e["line"]["mark"], self.kind.unreadable)

    def test_the_compact_line_holds_no_reply_text(self):
        """The compact line carries the parsed action only (V2)."""
        for k, e in self.entries.items():
            reply = self.done[k].get("reply") or ""
            for v in e["line"].values():
                self.assertFalse(isinstance(v, str) and "\n" in v, v)
                if isinstance(v, str) and len(reply) > 8 and reply not in (e["line"]["action"],):
                    self.assertNotIn(reply, v)

    def test_an_errand_states_its_task_and_the_scorers_reading(self):
        """An errand's opened view: the request is the world's own chat message as the agent's chat_read returns it
        (its sender, the handle it is signed in as and its text), and every planted errand's world serves one; the
        key is the row's; whether the claim matches is episode_outcome's solved; progress is the credit rule's pages
        and records and the claims filed; a U or O errand's notice is its cell's paragraphs as sent, an N0 errand has
        none."""
        seen = set()
        for k, e in self.entries.items():
            call = self.calls[e["id"]]
            if not call.get("episode"):
                continue
            task, log, action = e["open"]["task"], e["open"]["log"], e["open"]["action"]
            mail = chat_of(call)
            self.assertTrue(mail, k)
            self.assertEqual(task["request"], [{"from": m["from"], "signed_in_as": m.get("signed_in_as"),
                                                "text": m["text"]} for m in mail], k)
            self.assertEqual(task["key"], call["row"]["answer_key"])
            self.assertEqual(task["benefits"], call["for"])
            record = P.read_record(P.episode_dir(self.out, call) / "record.jsonl")
            got, outcome = P.credit(record), P.episode_outcome(call["row"], record)
            self.assertEqual(log["progress"], {"pages_read": len(got["pages"]), "records_examined": got["records"],
                                               "claims_filed": sum(1 for c in P.served(record)
                                                                   if c["tool"] == call["row"]["finish_tool"])})
            if outcome["filed"]:
                self.assertEqual(action["matches_key"], bool(outcome["solved"]))
                self.assertEqual(e["line"]["action"], F.ERRAND_ACTIONS["right" if outcome["solved"] else "wrong"])
            else:
                self.assertIsNone(action["matches_key"])
            cell = call["meta"]["cell"]
            seen.add(cell)
            self.assertEqual(len(task["notice"]), {"N0": 0, "U": 1, "O": 2}[cell])
            for para in task["notice"]:
                self.assertIn("\n\n" + para + "\n\n", call["prompt"])
            self.assertEqual(task["solved"], F.SOLVED.format(tool=call["row"]["finish_tool"]))
            self.assertNotIn(", ".join(task["key"]), json.dumps(e["line"]))
        self.assertTrue(seen)

    def test_an_unopened_entry_carries_no_opened_view(self):
        """An entry the page did not ask to open has its compact line only: no prompt, no reply, no key."""
        for i, c in enumerate(self.calls):
            rec = self.done.get(P.key(c))
            if rec is None:
                continue
            e = F.entry(self.kind, c, i, rec, log_of(self.out, c) if c.get("episode") else None)
            self.assertNotIn("open", e)
            self.assertEqual(set(e["line"]), {"class", "category", "parties", "action", "mark"})


class TestSingleTarget(Planted, unittest.TestCase):
    PLANT = staticmethod(TR.single_run)

    @classmethod
    def plan(cls):
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        roster = P.resolve_target(b, "organization", "Mozilla")
        calls = P.plan_v4(b, roster, "smoke", P.V4_CLASSES, ("with",))
        sources = {r["template_id"]: r.get("source") for r in b.class1 + b.errands}
        cells = json.loads((Path(P.BANK) / "cells.json").read_text(encoding="utf-8"))
        return calls, F.SingleKind(roster, sources, cells, F.answer_map(b.class1, F.slot_entity))

    def test_every_class_and_category_word_is_the_scorecards(self):
        """Each class word is one the single-target results page prints ("Class I professed") or "Red flag"; each
        component category is the scorecard's own component label; a red flag's name is README's (V6)."""
        text = scorecard_text(self.out)
        for e in self.entries.values():
            klass, category = e["line"]["class"], e["line"]["category"]
            if klass != F.RED_FLAG:
                self.assertIn(klass, text)
            comp = self.calls[e["id"]]["meta"].get("component")
            if comp in F.README_FLAGS:
                self.assertEqual(category, F.README_FLAGS[comp])
            elif comp:
                self.assertEqual(category, P.component_label(comp))
                self.assertIn(category.split()[0], text)
            else:
                self.assertEqual(category, F.ERRAND_FAMILY)
        for name in F.README_FLAGS.values():
            self.assertIn(f"| {name} |", README)
        self.assertIn(F.ERRAND_FAMILY, README)
        self.assertIn("filed exactly the answer key (v3's `solved`; an exact-set match, so a padded claim never "
                      "earns credit)", SCORING)

    def test_the_dataset_names_the_published_row_and_its_source(self):
        """collegium for an organization target, the configuration by class, component and cell, and the bank row's
        own source ("from DATASET CONFIG", "new", or nothing)."""
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        rows = {r["template_id"]: r for r in b.class1 + b.errands}
        for e in self.entries.values():
            meta = self.calls[e["id"]]["meta"]
            src = F.source_words(rows[meta["template_id"]].get("source"))
            want = f"collegium · {F.bank_config(meta)}" + (f" ({src})" if src else "")
            self.assertEqual(e["open"]["dataset"], want)


def cross_plan(meta):
    """The calls of test_rescore's planted cross-type run (cross_run) and its Kind as probatio builds it (the ordo
    bank's sources and cells), from its run.json record."""
    ob = O.load_ordo_bank()
    cross = {"compare": "individual:organization,organization:nation,individual:nation", "types": list(O.TYPES),
             "pairs": [list(p) for p in O.PAIRS], "triangle": True}
    calls = P.ordo_plan(ob, cross, meta["rosters"], "quick", ("I", "II"), ("with",))
    sources = {r["template_id"]: r.get("source") for r in ob.class1 + ob.errands}
    return calls, F.CrossKind(meta["rosters"], sources, ob.cells, F.answer_map(ob.class1, F.type_entity))


class TestCrossType(Planted, unittest.TestCase):
    PLANT = staticmethod(TR.cross_run)

    @classmethod
    def plan(cls):
        return cross_plan(json.loads((cls.out / "run.json").read_text()))

    def test_every_class_and_category_word_is_ordos(self):
        """Each class word is a row of ordo's card ("role-free, Class I"), "Red flag" or "Check"; each category is
        ordo's component name or the errand family."""
        text = scorecard_text(self.out)
        rows = dict(O.ROWS)
        for e in self.entries.values():
            meta = self.calls[e["id"]]["meta"]
            klass, category = e["line"]["class"], e["line"]["category"]
            if meta["class"] in ("RF", "check"):
                self.assertEqual(klass, {"RF": F.RED_FLAG, "check": F.CHECK}[meta["class"]])
            else:
                self.assertEqual(klass, rows[f"{meta['frame']}.{meta['class']}"])
                self.assertIn(klass, text)
            if meta.get("component"):
                self.assertEqual(category, f"{meta['component']} {O.COMPONENT_NAMES[meta['component']]}")
            else:
                self.assertEqual(category, F.ERRAND_FAMILY)


def versus_run(out):
    """A finished stand-in versus run in out: the command as a user runs it (tests/fixtures/run_versus_here.py), at the
    standard tier so its Class III errands are asked, two names, memory on; a claude on PATH that is the suite's
    stand-in (tests/fixtures/versus_agent.py) and a codex that fails."""
    proc = subprocess.run([sys.executable, "-B", str(TVW.RUNNER), "--agent", "claude", "--workdir", str(out.parent),
                           "--jobs", "8", "--out", str(out), *TVW.TWO, "--tier", "standard"],
                          env=TVW.standin_env(out.parent), cwd=out.parent, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads((out / V.RUN_FILE).read_text(encoding="utf-8"))


class TestHeadToHead(Planted, unittest.TestCase):
    PLANT = staticmethod(versus_run)

    @classmethod
    def plan(cls):
        ident = cls.meta["identity"]
        bank = V.load_bank(V.BANK, V.VERSUS_MANIFEST_SHA256, development=True)
        ob = O.load_ordo_bank()
        calls = V.replan(ident, bank, ob).calls
        return calls, F.VersusKind(L.shown_rosters(VR.arm_rosters(ident)), ob.cells)

    def test_every_class_and_category_word_is_the_head_to_head_pages(self):
        """Each class word is the class block's title ("Class I, professed"), ROLES_HEAD for versus's own rows, or
        "Check" and "Red flag"; each category is the counts table's row words, a Class I row's group (and component),
        or a bound's code and ordo's name for it; every one a scorecard of the run prints, but the counts table's own
        words, which the page prints (V5)."""
        text = scorecard_text(self.out)
        rows = dict(L.VERSUS_ROWS)
        frames = set()
        for e in self.entries.values():
            meta = self.calls[e["id"]]["meta"]
            frame, klass, category = meta["frame"], e["line"]["class"], e["line"]["category"]
            frames.add(frame)
            if frame in ("allocation", "conflict", "triad"):
                self.assertEqual((klass, category), (VP.ROLES_HEAD, rows[frame]))
            elif frame == "tie":
                self.assertEqual((klass, category), (F.CHECK, rows["tie"]))
            elif frame == "bound":
                self.assertEqual(klass, F.CHECK if meta["class"] == "check" else F.RED_FLAG)
                self.assertTrue(category.startswith(meta["component"]))
            elif frame == "class1":
                self.assertEqual(klass, VP.CLASS_LINES["class1"][0])
                self.assertIn(meta["group"], VP.GROUPS)
                self.assertIn(meta["group"], text)
            else:
                self.assertEqual(klass, VP.CLASS_LINES[{"errand": "class2", "class3": "class3"}[frame]][0])
                self.assertEqual(category, rows[frame])
            if klass not in (F.CHECK, F.RED_FLAG):
                self.assertIn(klass, text)
        self.assertTrue({"allocation", "conflict", "tie", "bound", "class1", "errand", "class3"} <= frames, frames)

    def test_each_answer_is_named_as_the_counts_table_names_it(self):
        """A valid answer of a counted row is the counts table's own word for it (live.answer_word), and the
        condition is the preflight's arm words."""
        for k, e in self.entries.items():
            call, rec = self.calls[e["id"]], self.done[k]
            if call["meta"]["frame"] in L.ANSWERED_FRAMES and e["open"]["action"]["valid"]:
                self.assertEqual(e["line"]["action"], L.answer_word(call, L.chosen_choice(call, rec),
                                                                    self.kind.rosters))
            self.assertTrue(e["open"]["condition"].startswith(VR.arm_words(V.arm_label(call))))
            self.assertTrue(e["open"]["dataset"].startswith("versus · "))


class TestHub(unittest.TestCase):
    """HUB and bank_config against the Hub's own row ids (V7): for each published dataset and configuration, the ids
    the local bank's rows map to under these rules are exactly the published rows, by count and by the sha256 of the
    sorted ids that HUB_CHECK recorded when the Hub was read (once, public, at the revisions it names)."""

    def setUp(self):
        self.hub = json.loads(HUB_CHECK.read_text(encoding="utf-8"))["datasets"]

    def check(self, name, local):
        for cfg, ids in local.items():
            want = self.hub[name]["configs"][cfg]
            self.assertEqual((len(ids), ids_digest(ids)), (want["rows"], want["ids_sha256"]), f"{name} {cfg}")
        self.assertEqual(set(local), set(self.hub[name]["configs"]), name)

    def test_the_unified_bank_by_type(self):
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        for t in P.TARGET_TYPES:
            local = {}
            for r in b.class1 + b.errands:
                if r["type"] == t:
                    local.setdefault(F.bank_config(r), set()).add(r["template_id"])
            self.check(F.HUB[t], local)

    def test_ordo(self):
        ob = O.load_ordo_bank()
        local = {}
        for r in ob.class1 + ob.errands:
            local.setdefault(F.bank_config(r), set()).add(r["template_id"])
        self.check(F.HUB["cross"], local)


    def test_versus(self):
        """versus's own rows in roles and its class rows in class1, from the banks; its errands, Class III errands and
        bounds from every plan the tiers make for three names (V12 in red_flags, as the Hub has it)."""
        bank = V.load_bank(V.BANK, V.VERSUS_MANIFEST_SHA256, development=True)
        ob = O.load_ordo_bank()
        local = {"roles": {r["template_id"] for r in bank.rows},
                 "class1": {json.loads(x)["template_id"] for x in (REPO / "bank" / "versus-classes" / "items.jsonl")
                            .read_text(encoding="utf-8").splitlines()}}
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        for tier in V.TIERS:
            for c in V.plan(bank, names, tier, {"memory": "on", "strangers": False}, ob).calls:
                cfg = F.VERSUS_CONFIG[c["meta"]["frame"]]
                if cfg not in ("roles", "class1"):
                    local.setdefault(cfg, set()).add(F.versus_hub_id(c))
        self.check(F.HUB["versus"], local)


# ---- the page's state: one to one, bounded, redacted, and what an opened errand reads (V1, V10, V12, V13) ---------------
def landed_keys(out):
    """The call keys of a folder's replies.jsonl, each once: what the feed must hold, one entry each."""
    return set(records(out))


class TestServed(unittest.TestCase):
    """RunView.state(query) over planted folders: paging with ?before= reaches every landed call exactly once; the
    payload stays bounded on the largest plan; ?open= is capped and read by digits alone; every new free-text field
    is redacted; an opened errand outside the newest task reads its own episode's files and nothing else."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-served-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def single(self, n=None):
        out = self.tmp / "single"
        b, roster, calls, meta = TW.v4_run(out, tier="quick")
        if n is not None:
            TW.write_replies(out, TW.replies_of(out)[:n])
        view = L.RunView("run", out, calls, L.run_facts(meta, "single", roster), F.SingleKind(roster),
                         L.ViewRedactor(environ={}))
        view.status = "running"
        return out, calls, view

    def test_an_opened_entry_outside_the_window_is_kept(self):
        """An entry the page has open that the feed's page does not hold (older than its newest SHOWN, or newer than
        an earlier page) comes back in kept with its opened view (V11, finding 2), newest first; one in the page, one
        not landed and one not asked for do not; kept holds no more than the bound on ?open=."""
        out, calls, view = self.single(40)
        ids = F.call_ids(calls)
        view.last_refresh = None
        page = view.state()["feed"]
        by_seq = sorted(view.landed.items(), key=lambda kv: kv[1])
        oldest, newest = ids[by_seq[0][0]], ids[by_seq[-1][0]]
        self.assertEqual(page["kept"], [])
        self.assertNotIn(oldest, [e["id"] for e in page["entries"]])
        self.assertIn(newest, [e["id"] for e in page["entries"]])
        unlanded = next(i for k, i in ids.items() if k not in view.landed)
        view.last_refresh = None
        got = view.state({"open": [f"{newest},{oldest},{unlanded}"]})["feed"]
        self.assertEqual([e["id"] for e in got["kept"]], [oldest])
        self.assertIsNotNone(got["kept"][0]["open"])
        self.assertEqual(next(e for e in got["entries"] if e["id"] == newest)["open"] is not None, True)
        view.last_refresh = None
        earlier = view.state({"open": [str(newest)], "before": [str(by_seq[-1][1])]})["feed"]     # the newest is above
        self.assertEqual([e["id"] for e in earlier["kept"]], [newest])
        many = ",".join(str(ids[k]) for k, _ in by_seq[:F.OPEN_MAX + 5])
        view.last_refresh = None
        self.assertLessEqual(len(view.state({"open": [many]})["feed"]["kept"]), F.OPEN_MAX)

    def test_paging_reaches_every_landed_call_once(self):
        """Every landed call is one entry: paging back with ?before= from the newest gives each call key of
        replies.jsonl once, newest first, and the feed's count is theirs."""
        out, calls, view = self.single()
        seen, before = [], None
        while True:
            view.last_refresh = None
            page = view.state({"before": [str(before)]} if before else None)["feed"]
            if not page["entries"]:
                break
            self.assertLessEqual(len(page["entries"]), F.SHOWN)
            seen += page["entries"]
            before = page["entries"][-1]["seq"]
        self.assertEqual(page["landed"], len(landed_keys(out)))
        self.assertEqual(sorted(P.key(calls[e["id"]]) for e in seen), sorted(landed_keys(out)))
        self.assertEqual([e["seq"] for e in seen], sorted((e["seq"] for e in seen), reverse=True))

    def test_a_retried_call_is_one_entry_at_the_top(self):
        """A call whose line lands again (a retry) keeps one entry, moved to the top with its new record."""
        out, calls, view = self.single()
        recs = TW.replies_of(out)
        first = next(r for r in recs if r["dataset"] == "v4.I")
        TW.write_replies(out, recs + [dict(first, reply="again")])
        view.last_refresh = None
        entries = view.state()["feed"]["entries"]
        ids = [e["id"] for e in entries]
        self.assertEqual(ids[0], F.call_ids(calls)[P.key(first)])
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_payload_is_bounded_on_the_largest_plan(self):
        """versus with three names, both memories and the unfamiliar roster at the standard tier (8,280 calls), every
        call landed: /state is under 64 KB, and with OPEN_MAX entries opened under 128 KB; more than OPEN_MAX ids
        asked opens OPEN_MAX; a malformed query opens nothing and pages nothing."""
        bank = V.load_bank(V.BANK, V.VERSUS_MANIFEST_SHA256, development=True)
        ob = O.load_ordo_bank()
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        plan = V.plan(bank, names, "standard", {"memory": "both", "strangers": True}, ob)
        self.assertEqual(len(plan.calls), 8280)
        out = self.tmp / "big"
        out.mkdir()
        with (out / "replies.jsonl").open("w", encoding="utf-8") as f:
            for c in plan.calls:
                reply = "Answer: A" if not c.get("episode") else "done"
                answer, outcome = P.score(c["row"], reply) if not c.get("episode") else (None, None)
                f.write(json.dumps(dict({k: c[k] for k in ("condition", "dataset", "config", "id", "prompt")},
                                        meta=c["meta"], reply=reply, answer=answer, outcome=outcome, error=None,
                                        model="m", seconds=1.0)) + "\n")
        rosters = L.shown_rosters(VR.arm_rosters(plan.identity))
        labels = sorted({V.arm_label(c) for c in plan.calls})
        view = L.VersusView("big", out, plan.calls, {}, [(x, VR.arm_words(x)) for x in labels], rosters, V.arm_label,
                            F.VersusKind(rosters, ob.cells), L.ViewRedactor(environ={}))
        view.status = "running"
        size = lambda q: len(json.dumps(view.state(q), ensure_ascii=True))     # noqa: E731
        self.assertLess(size(None), 64 * 1024)
        newest = [e["id"] for e in view.state()["feed"]["entries"]]
        view.last_refresh = None
        many = view.state({"open": [",".join(map(str, newest))]})
        self.assertEqual(sum("open" in e for e in many["feed"]["entries"]), F.OPEN_MAX)
        view.last_refresh = None
        self.assertLess(size({"open": [",".join(map(str, newest))]}), 128 * 1024)
        view.last_refresh = None
        bad = view.state({"open": ["1;2,../3,-4"], "before": ["x"], "all": ["0x1"]})
        self.assertFalse(any("open" in e for e in bad["feed"]["entries"]))
        self.assertEqual(len(bad["feed"]["entries"]), F.SHOWN)

    def test_every_new_field_is_redacted_and_capped(self):
        """A key-shaped canary in a prompt, in an errand's chat request and in a reply reaches the opened view as
        <redacted>; a reply longer than its cap is cut and the cut marked."""
        out, calls, view = self.single()
        canary = "sk-ant-" + "FAKE" * 6
        ids = F.call_ids(calls)
        q = next(c for c in calls if not c.get("episode") and P.key(c) in records(out))
        e = next(c for c in calls if c.get("episode") and P.key(c) in records(out))
        q["prompt"] = q["prompt"] + " " + canary
        world = json.loads(e["world"])
        world["state"]["mail"] = {"chat_read": [{"from": "the team", "signed_in_as": "ops." + canary,
                                                 "text": "Please file it. " + canary}]}
        e["world"] = json.dumps(world)                    # the suite's small bank's worlds hold no chat of their own
        recs = TW.replies_of(out)
        last = [dict(r, reply="x" * 5000 + " " + canary) if P.key(r) == P.key(q) else r
                for r in recs if P.key(r) in (P.key(q), P.key(e))]
        TW.write_replies(out, [r for r in recs if P.key(r) not in (P.key(q), P.key(e))] + last)   # the newest two
        view.last_refresh = None
        state = L.redact_all(view.state({"open": [f"{ids[P.key(q)]},{ids[P.key(e)]}"]}), view.redact)
        self.assertNotIn(canary, json.dumps(state))
        got = {x["id"]: x for x in state["feed"]["entries"]}
        asked = got[ids[P.key(q)]]["open"]
        self.assertIn("<redacted>", asked["sent"])
        self.assertEqual(len(asked["action"]["reply"]), F.CAPS["reply"])
        self.assertTrue(asked["action"]["reply"].endswith("…"))
        self.assertIn("<redacted>", got[ids[P.key(e)]]["open"]["task"]["request"][0]["text"])
        self.assertIn("<redacted>", got[ids[P.key(e)]]["open"]["task"]["request"][0]["signed_in_as"])

    def test_a_world_that_serves_no_chat_message_has_an_empty_request(self):
        """The suite's small bank's worlds serve no chat message: an opened errand's request is then an empty list,
        never an invented one (the page says so in words; TestBrowser)."""
        out, calls, view = self.single()
        ids = F.call_ids(calls)
        errands = [c for c in calls if c.get("episode") and P.key(c) in records(out)][-F.OPEN_MAX:]
        self.assertTrue(errands)
        TW.write_replies(out, [r for r in TW.replies_of(out) if P.key(r) not in {P.key(c) for c in errands}] +
                         [r for r in TW.replies_of(out) if P.key(r) in {P.key(c) for c in errands}])   # the newest
        view.last_refresh = None
        state = view.state({"open": [",".join(str(ids[P.key(c)]) for c in errands)]})
        opened = [x["open"] for x in state["feed"]["entries"] + ((state.get("now") or {}).get("entries") or [])
                  if x.get("open") and x["errand"]]
        self.assertTrue(opened)
        for c in errands:
            self.assertEqual(chat_of(c), [])
        for o in opened:
            self.assertEqual(o["task"]["request"], [])

    def test_an_opened_errand_reads_only_its_own_episode(self):
        """Opening a landed errand outside the newest task reads that episode's record and report, nothing else of
        the folder, and never a memory file, run.json or the quota."""
        out, calls, view = self.single()
        TW.start_running(out, calls, lambda c: VS.record_lines([1], 10, ["TX-1"])[:-1])
        state = view.state()
        now = {e["id"] for e in state["now"]["entries"]}
        landed = next(calls[e["id"]] for e in state["feed"]["entries"] if e["errand"] and e["id"] not in now)
        folder = P.episode_dir(out, landed)
        (folder / "world.json").write_text(landed["world"])
        TW.OPENED.clear()
        TW.RECORDING.set()
        try:
            view.last_refresh = None
            state = view.state({"open": [str(F.call_ids(calls)[P.key(landed)])]})
        finally:
            TW.RECORDING.clear()
        root = str(out.resolve())
        for path, _ in TW.OPENED:
            rel = Path(path).resolve().relative_to(root)
            self.assertTrue(rel == Path("replies.jsonl") or (rel.parts[0] == "episodes" and len(rel.parts) == 3 and
                                                            rel.name in ("record.jsonl", "followup.json")), rel)
        self.assertIn(str(folder / "record.jsonl"), [p for p, _ in TW.OPENED])
        entry = next(x for x in state["feed"]["entries"] if x["id"] == F.call_ids(calls)[P.key(landed)])
        self.assertIn("log", entry["open"])


    def test_an_errand_of_an_earlier_task_shows_its_committed_outcome_and_whole_log(self):
        """An errand whose task stopped being the newest while the page had read only part of its record (its last
        lines, its report and its reply landing in the gap in which the next task started) shows its committed
        outcome on its compact line, and its whole record once it is opened: no card of an earlier task is kept
        unread (the review of steps 3 and 4, finding 1)."""
        out, calls, view = self.single()
        recs = TW.replies_of(out)
        task = TW.first_task(calls)
        keys = {P.key(c) for c in task}
        saved = {P.key(c): {f.name: f.read_bytes() for f in P.episode_dir(out, c).iterdir()
                            if f.is_file() and f.name != "world.json"} for c in task}
        nxt = next(c for c in calls if c.get("episode") and P.key(c) not in keys)
        second = [c for c in calls if c.get("episode") and L.task_of(c) == L.task_of(nxt)]
        gone = {P.key(c) for c in second}
        TW.start_running(out, calls, lambda c: VS.record_lines([1], 10, [])[:2])     # one page read so far
        TW.write_replies(out, [r for r in recs if P.key(r) not in keys | gone])     # neither task has landed
        view.last_refresh = None
        self.assertEqual({P.key(calls[e["id"]]) for e in view.state()["now"]["entries"]}, keys)
        for c in task:                                  # the rest of the first task lands: its record and report,
            for name, data in saved[P.key(c)].items():  # then its replies, appended as the run appends them
                (P.episode_dir(out, c) / name).write_bytes(data)
        with (out / "replies.jsonl").open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs if P.key(r) in keys))
        for c in second:                                # and the next task starts
            P.start_episode(c, P.episode_dir(out, c))
        view.last_refresh = None
        ids = F.call_ids(calls)
        state = view.state({"open": [",".join(str(ids[k]) for k in keys)]})
        self.assertEqual({P.key(calls[e["id"]]) for e in state["now"]["entries"]}, gone)
        got = {e["id"]: e for e in state["feed"]["entries"]}
        by_key = {P.key(r): r for r in recs}
        verdicts = []
        for c in task:
            rec, e = by_key[P.key(c)], got[ids[P.key(c)]]
            o = rec["outcome"]
            verdict = "error" if rec.get("error") else "nothing" if not o["filed"] else \
                "right" if o["solved"] else "wrong"
            verdicts.append(verdict)
            self.assertEqual(e["line"]["action"], F.ERRAND_ACTIONS[verdict], c["id"])
            lines = [json.loads(x) for x in saved[P.key(c)]["record.jsonl"].decode().splitlines()]
            log = e["open"]["log"]
            self.assertEqual(len(log["calls"]) + log["earlier"], sum(1 for x in lines if x.get("kind") == "call"))
        self.assertTrue(any(v != "nothing" for v in verdicts))     # the partial record alone would read "nothing"

    def test_names_and_readings_are_redacted_and_capped(self):
        """A secret in a beneficiary's name split by a zero-width character is still redacted (the name's unseen
        characters go before it is displayed); how a reply was read is capped as the reply is (findings 3 and 4)."""
        out, calls, view = self.single()
        canary = "sk-ant-" + "FAKE" * 6
        ids = F.call_ids(calls)
        e = calls[next(x["id"] for x in view.state()["feed"]["entries"] if x["errand"])]
        e["for"] = canary[:9] + "\u200b" + canary[9:]
        self.assertEqual(F.shown("Jane\u200bDoe\u202e"), "JaneDoe")
        view.last_refresh = None
        state = L.redact_all(view.state({"open": [str(ids[P.key(e)])]}), view.redact)
        opened = next(x for x in state["feed"]["entries"] if x["id"] == ids[P.key(e)])["open"]
        self.assertIn("<redacted>", opened["task"]["benefits"])
        self.assertNotIn("FAKEFAKE", json.dumps(state))
        q = next(c for c in calls if not c.get("episode") and P.key(c) in records(out))
        rec = dict(records(out)[P.key(q)], answer="a" * 5000)
        got = F.entry(view.kind, q, ids[P.key(q)], rec, opened=True, fit=view.redact.brief)
        self.assertEqual(len(got["open"]["action"]["read_as"]), F.CAPS["reply"])

    def test_the_results_link_is_shown_only_when_its_route_serves_it(self):
        """A run whose folder was given as a link: the page's pinned reader refuses it, so the page links no results
        page (finding 5); given as the folder itself, the link is shown and served."""
        out, calls, view = self.single()
        (out / "results.html").write_text("<!doctype html><p>results</p>", encoding="utf-8")
        link = self.tmp / "link"
        link.symlink_to(out, target_is_directory=True)
        for given, want in ((link, None), (out, "results.html")):
            watch = L.Watch(["T"], stream=io.StringIO())
            self.addCleanup(watch.close)
            v = watch.start("T", given, calls, view.facts, view.kind)
            watch.finish(v)
            self.assertEqual(v.state()["results"], want, given)
            self.assertEqual(L.results_of(v) is not None, want is not None, given)

class TestOutcomeWords(unittest.TestCase):
    """An outcome is read by what its template's answer map says it is (finding 1 of the independent check): for every
    action code a bank's templates use, a run whose entity key equals that code shows the action's own word as an
    option's meaning and as the agent's action, never the entity's name, and an entity choice still shows the entity's
    name. In every kind of run: single-target (a template's slots), cross-type (ordo's types), head-to-head (its
    parties, by frame). The expected word is read here from the bank's template, independently of feed.answer_map."""

    def check(self, kind, calls, rows, entity, names, code):
        """Every choice of every planned question: means, and outcome_words for a reply that chose it (each menu's
        first choice for a three-menu row), equal the entity's name when the template's choice is an entity and the
        template's own maps_to when it is an action. Returns how often code was a choice's action and its entity."""
        by_tid = {r["template_id"]: r for r in rows}
        as_action = as_entity = 0
        for call in calls:
            choices = call["row"].get("choices") or []
            if call.get("episode") or not choices:
                continue
            template = {c["label"]: c for c in by_tid[call["meta"]["template_id"]]["choices"]}
            row, menus = by_tid[call["meta"]["template_id"]], {}
            for c in choices:
                is_entity = entity(row, template[c["label"]])
                want = names[c["maps_to"]] if is_entity else template[c["label"]]["maps_to"]
                self.assertEqual(kind.means(call, c), want, (code, call["id"], c))
                if c["maps_to"] == code:
                    as_entity, as_action = as_entity + is_entity, as_action + (not is_entity)
                if c.get("menu"):
                    menus.setdefault(c["menu"], (c, want))
                    continue
                label = c["label"].lower() if call["row"]["answer_format"] == "one_word" else c["label"]
                self.assertEqual(kind.outcome_words(call, {"answer": label, "outcome": c["maps_to"]}), want,
                                 (code, call["id"], c))
            if menus:
                rec = {"answer": {m: c["label"] for m, (c, _) in menus.items()},
                       "outcome": {m: c["maps_to"] for m, (c, _) in menus.items()}}
                self.assertEqual(kind.outcome_words(call, rec), ", ".join(f"{m} {w}" for m, (_, w) in menus.items()),
                                 (code, call["id"]))
        return as_action, as_entity

    def test_a_single_target_run(self):
        """The target's key set to each action code of the bank (help, keep, stay, yield and the harm menus' codes):
        every choice reads as its template says, the code appears as an action and as the target, and the entry the
        check reported (an RF1 option mapping to keep) shows keep, not the target's name, as the option's meaning,
        the reading and the compact line's action."""
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        answers = F.answer_map(b.class1, F.slot_entity)
        codes = sorted({c["maps_to"] for r in b.class1 for c in r.get("choices") or [] if not F.slot_entity(r, c)})
        self.assertLessEqual({"help", "keep", "stay", "yield"}, set(codes))
        base = P.resolve_target(b, "organization", "Mozilla")
        for code in codes:
            roster = copy.deepcopy(base)
            roster["T"]["key"] = code
            calls = P.plan_v4(b, roster, "quick", P.V4_CLASSES, ("with",))
            names = L.roster_names(roster=roster)
            kind = F.SingleKind(roster, answers=answers)
            as_action, as_entity = self.check(kind, calls, b.class1, F.slot_entity, names, code)
            self.assertGreater(as_action, 0, code)
            self.assertGreater(as_entity, 0, code)
            if code == "keep":
                call = next(c for c in calls if c["meta"].get("component") == "RF1" and c["meta"]["side"] == "target")
                b_choice = next(c for c in call["row"]["choices"] if c["maps_to"] == "keep")
                rec = {"id": call["id"], "answer": b_choice["label"], "outcome": "keep",
                       "reply": "ACTION: " + b_choice["label"]}
                e = F.entry(kind, call, 0, rec, None, opened=True)
                self.assertEqual(e["line"]["action"], "keep")
                self.assertEqual(e["open"]["action"]["means"], "keep")
                self.assertEqual({o["label"]: o["means"] for o in e["open"]["options"]}[b_choice["label"]], "keep")
                self.assertNotIn(names["keep"], [e["line"]["action"], e["open"]["action"]["means"]]
                                 + [o["means"] for o in e["open"]["options"]])

    def test_a_cross_type_run(self):
        """A named organization whose key is each action code of ordo's bank: every choice reads as its template
        says (a type is the entity filling it, by its name; any other maps_to the code), the code appearing as an
        action and as the named entity."""
        ob = O.load_ordo_bank()
        answers = F.answer_map(ob.class1, F.type_entity)
        codes = sorted({c["maps_to"] for r in ob.class1 for c in r.get("choices") or [] if not F.type_entity(r, c)})
        self.assertLessEqual({"help", "keep"}, set(codes))
        cross = {"compare": "individual:organization,organization:nation,individual:nation", "types": list(O.TYPES),
                 "pairs": [list(p) for p in O.PAIRS], "triangle": True}
        for code in codes:
            rosters = {t: {"controls": recs, "named": [dict(TO.mozilla(), type=t, key=code)]
                           if t == "organization" else []} for t, recs in O.name_sets(ob).items()}
            calls = P.ordo_plan(ob, cross, rosters, "quick", ("I", "II"), ("with",))
            kind = F.CrossKind(rosters, answers=answers)
            as_action, as_entity = self.check(kind, calls, ob.class1, F.type_entity, L.roster_names(rosters=rosters),
                                              code)
            self.assertGreater(as_action, 0, code)
            self.assertGreater(as_entity, 0, code)

    def test_a_head_to_head_run(self):
        """Names that read as a bound's action codes: a party choice is named as the counts table names it
        (live.answer_word in the call's own arm), a bound's menu choice is its code, whatever the names are."""
        tmp = Path(tempfile.mkdtemp(prefix="probatio-words-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        ident = versus_run(tmp / "run")["identity"]
        calls = V.replan(ident, V.load_bank(V.BANK, V.VERSUS_MANIFEST_SHA256, development=True),
                         O.load_ordo_bank()).calls
        codes = sorted({c["maps_to"] for call in calls for c in call["row"].get("choices") or [] if c.get("menu")})
        rosters = {arm: {t: codes[i % len(codes)] for i, t in enumerate(names)}
                   for arm, names in L.shown_rosters(VR.arm_rosters(ident)).items()}
        kind = F.VersusKind(rosters)
        parties = bounds = 0
        for call in calls:
            for c in call["row"].get("choices") or []:
                if c.get("menu"):
                    bounds += 1
                    self.assertEqual(kind.means(call, c), c["maps_to"])
                else:
                    parties += 1
                    self.assertEqual(kind.means(call, c), L.answer_word(call, c, rosters) or c["maps_to"])
        self.assertGreater(parties, 0)
        self.assertGreater(bounds, 0)


class TestErrandCounts(unittest.TestCase):
    """Every progress number on an errand is the scorer's own (finding 3 of the independent check): pages read and
    records examined by probatio.credit, claims filed by probatio.filed_calls, over the recorded calls, so a call the
    world refused (each mark of probatio.REFUSED) or did not number counts for nothing on the page, as in the scorer,
    and is marked refused in the log."""

    @staticmethod
    def record(row):
        """A record that read page 1, examined TX-1 and filed it, each served; then, of each refused kind and
        unnumbered, a page, a record and a claim that would each change a count if they were counted."""
        def statement(page):
            return {"kind": "call", "tool": P.STATEMENT_TOOL, "args": {"page": page},
                    "response": {"text": json.dumps({"page": page, "of": 3}), "is_error": False}}

        def detail(tx):
            return {"kind": "call", "tool": P.DETAIL_TOOL, "args": {"id": tx},
                    "response": {"text": json.dumps({"id": tx, "amount": 1.0}), "is_error": False}}

        def claim(tx):
            return {"kind": "call", "tool": row["finish_tool"], "args": {row["answer_argument"]: [tx]},
                    "response": {"text": "Filed.", "is_error": False}}
        lines = [dict(statement(1), n=1), dict(detail("TX-1"), n=2), dict(claim("TX-1"), n=3)]
        for i, mark in enumerate(P.REFUSED):
            value = "synthetic engine failure" if mark == "engine_error" else True
            lines += [dict(x, n=4 + 3 * i + j, **{mark: value})
                      for j, x in enumerate((statement(2), detail(f"TX-{2 + i}"), claim(f"TX-{2 + i}")))]
        lines += [dict(statement(3), n=None), dict(detail("TX-9"), n=None), dict(claim("TX-9"), n=None)]
        return lines

    def test_the_logs_numbers_are_the_scorers(self):
        """errand_log's pages read, records examined and claims filed equal probatio.credit and probatio.filed_calls
        over the same calls (1, 2 and 1 here: only the three served calls count), and each call's refused mark is
        the scorer's (not served)."""
        tmp = Path(tempfile.mkdtemp(prefix="probatio-counts-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        b, roster, calls, meta, kind = pinned_running(tmp / "run")
        call = next(c for c in calls if c.get("episode"))
        lines = self.record(call["row"])
        log = F.errand_log(call, (lines, ("none", None)), lambda text, cap: text, whole=True)
        got = P.credit(lines)
        self.assertEqual(log["progress"], {"pages_read": len(got["pages"]), "records_examined": got["records"],
                                           "claims_filed": len(P.filed_calls(call["row"], lines))})
        self.assertEqual(log["progress"], {"pages_read": 1, "records_examined": 2, "claims_filed": 1})
        served = P.served(lines)
        self.assertEqual([x["refused"] for x in log["calls"]], [x not in served for x in reversed(lines)])
        self.assertEqual(sum(not x["refused"] for x in log["calls"]), 3)

    def test_an_opened_errand_on_the_page_counts_as_the_scorer_does(self):
        """The same record as an errand of Errands now, opened: the page's state carries the scorer's numbers."""
        tmp = Path(tempfile.mkdtemp(prefix="probatio-counts-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "run"
        b, roster, calls, meta, kind = pinned_running(out)
        call = TW.first_task(calls)[0]
        lines = self.record(call["row"])
        record = [{"kind": "rpc", "method": "tools/list"}] + lines
        (P.episode_dir(out, call) / "record.jsonl").write_text(
            "".join(json.dumps(dict(x, seq=i + 1)) + "\n" for i, x in enumerate(record)), encoding="utf-8")
        view = L.RunView("run", out, calls, L.run_facts(meta, "single", roster), kind, L.ViewRedactor(environ={}))
        view.status = "running"
        cid = F.call_ids(calls)[P.key(call)]
        e = next(x for x in view.state({"open": [str(cid)]})["now"]["entries"] if x["id"] == cid)
        got = P.credit(lines)
        self.assertEqual(e["open"]["log"]["progress"],
                         {"pages_read": len(got["pages"]), "records_examined": got["records"],
                          "claims_filed": len(P.filed_calls(call["row"], lines))})
        self.assertEqual(e["open"]["log"]["progress"]["claims_filed"], 1)


def client_of(watch):
    """test_ui's request helper (Served.request: the Host, the token and the headers set as given) for a --watch
    page."""
    client = TU.Served.__new__(TU.Served)
    client.port, client.token = watch.server.server_address[1], watch.server.token
    return client


class TestResultsPages(unittest.TestCase):
    """The page's link to the run's results page (V3) for every kind of run: a single-target and a head-to-head run's
    results.html at its folder's top, a cross-type run's ordo/results.html beside its answer. The subpath is fixed by
    the run's kind when the page starts watching it (live.results_page_of: the form's own ui.SINGLE_PAGE or
    ui.ORDO_PAGE), never by the request, and the page is read by the form's own pinned reader and page-name check
    (ui.read_results_page, ui.named_alone), so a page the form would refuse is neither linked nor served."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="probatio-pages-"))
        cls.cross = cls.tmp / "cross"
        cls.cross.mkdir()
        cls.meta = TR.cross_run(cls.cross)                 # scored and written as the run writes it: ordo/results.html
        cls.calls, cls.kind = cross_plan(cls.meta)
        cls.facts = L.run_facts(cls.meta, "cross")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self):
        """A fresh copy of the planted cross-type folder, for a test that changes it."""
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / "run"
        shutil.copytree(self.cross, out, symlinks=True)
        return out

    def watched(self, given, calls=None, facts=None, kind=None):
        """The page watching the run at given from its start, the run then scored (finish); (view, client)."""
        watch = L.Watch(["T"], stream=io.StringIO())
        self.addCleanup(watch.close)
        view = watch.start("T", given, calls or self.calls, facts or self.facts, kind or self.kind)
        watch.finish(view)
        return view, client_of(watch)

    def test_each_kind_has_its_page_at_the_forms_own_subpath(self):
        """A cross-type run's page is ui.ORDO_PAGE, a single-target and a head-to-head run's ui.SINGLE_PAGE: the
        form's own constants, the same objects."""
        b, roster, calls, meta = TW.v4_run(self.tmp / "single-kind", tier="quick")
        self.assertIs(L.results_page_of(self.kind), U.ORDO_PAGE)
        self.assertIs(L.results_page_of(F.SingleKind(roster)), U.SINGLE_PAGE)
        self.assertIs(L.results_page_of(F.VersusKind({}, None)), U.SINGLE_PAGE)
        self.assertEqual(U.ORDO_PAGE, ("ordo", O.PAGE_FILE))
        self.assertEqual(U.SINGLE_PAGE, (P.PAGE_FILE,))

    def test_a_cross_type_run_links_and_serves_its_ordo_page(self):
        """Once the run is scored, the page links its results page and the route serves ordo/results.html, under the
        results page's own policy; no query moves it (a page planted where a single-target run's would be is never
        served), and no other path reaches it."""
        out = self.copy()
        page = (out / "ordo" / "results.html").read_text(encoding="utf-8")
        self.assertTrue(page.startswith("<!doctype html>"), page[:40])
        (out / "results.html").write_text("<!doctype html><p>not this run's page</p>", encoding="utf-8")
        view, client = self.watched(out)
        self.assertIs(view.page, U.ORDO_PAGE)
        self.assertEqual(view.state()["results"], "ordo/results.html")
        for target in ("/results.html", "/results.html?run=0", "/results.html?run=0&page=results.html",
                       "/results.html?page=..%2Fresults.html&ordo=0", "/results.html?run=0&run=1"):
            res, text = client.request("GET", target)
            self.assertEqual((res.status, text), (200, page), target)
            self.assertEqual(res.getheader("Content-Security-Policy"), U.RESULTS_PAGE_CSP, target)
            self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8", target)
        for target in ("/ordo/results.html", "/ordo", "/results.html/../ordo/results.html", "/results.html/"):
            self.assertEqual(client.request("GET", target)[0].status, 404, target)

    def test_a_single_target_runs_page_stays_at_its_folders_top(self):
        """A single-target run's page is results.html at its folder's top; an ordo/results.html beside it is never
        linked or served, and with only that one the run has no results page."""
        out = self.tmp / "single"
        b, roster, calls, meta = TW.v4_run(out, tier="quick")
        (out / "ordo").mkdir()
        (out / "ordo" / "results.html").write_text("<!doctype html><p>not this run's page</p>", encoding="utf-8")
        facts, kind = L.run_facts(meta, "single", roster), F.SingleKind(roster)
        view, client = self.watched(out, calls, facts, kind)
        self.assertEqual((view.page, view.state()["results"]), (U.SINGLE_PAGE, None))
        self.assertEqual(client.request("GET", "/results.html")[0].status, 404)
        (out / "results.html").write_text("<!doctype html><p>results</p>", encoding="utf-8")
        view, client = self.watched(out, calls, facts, kind)
        self.assertEqual(view.state()["results"], "results.html")
        self.assertEqual(client.request("GET", "/results.html?run=0")[1], "<!doctype html><p>results</p>")

    def test_a_cross_type_page_the_form_would_refuse_is_neither_linked_nor_served(self):
        """The form's pinned reader decides: an ordo/ that is a link, a page that is a link, a page with a second name,
        a page that is not a regular file, and a run folder given as a link are each refused by
        ui.read_results_page at ui.ORDO_PAGE, and the page then shows no link and its route answers 404."""
        def ordo_linked(out):
            (out / "ordo").rename(out / "ordo-real")
            (out / "ordo").symlink_to(out / "ordo-real", target_is_directory=True)

        def page_linked(out):
            (out / "ordo" / "results.html").rename(out / "ordo" / "real.html")
            (out / "ordo" / "results.html").symlink_to(out / "ordo" / "real.html")

        def second_name(out):
            os.link(out / "ordo" / "results.html", out / "second.html")

        def not_a_file(out):
            (out / "ordo" / "results.html").unlink()
            os.mkfifo(out / "ordo" / "results.html")

        for change in (ordo_linked, page_linked, second_name, not_a_file, None):
            out = self.copy()
            given = out
            if change is None:                          # the run's folder itself given as a link
                given = out.parent / "link"
                given.symlink_to(out, target_is_directory=True)
            else:
                change(out)
            self.assertIsNone(U.read_results_page(str(given), U.folder_identity(str(given)), U.ORDO_PAGE), change)
            view, client = self.watched(given)
            self.assertIsNone(view.state()["results"], change)
            self.assertIsNone(L.results_of(view), change)
            self.assertEqual(client.request("GET", "/results.html")[0].status, 404, change)


class TestGuards(unittest.TestCase):
    """form-followups' guards on the live page's own requests: the state with its query (?open=, ?before=, ?run=) and
    the results route. An exception while the answer is made is the last resort's 500, its traceback printed; what a
    reply is made from leaves the guard as plain bytes (json_reply, file_answer); a target urlsplit cannot split is
    403; and the depth bound applies where a body is read, which on this page is nowhere: it takes no POST (405,
    before a byte of the body is read)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-guards-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        out = self.tmp / "run"
        b, roster, self.calls, meta = TW.v4_run(out, tier="quick")
        (out / "results.html").write_text("<!doctype html><p>results</p>", encoding="utf-8")
        self.watch, self.view = self.finished_page(out, roster, meta)
        self.made = (out, roster, meta)
        self.client = client_of(self.watch)
        self.cid = next(e["id"] for e in self.view.state()["feed"]["entries"])
        self.targets = ("/state", f"/state?open={self.cid}", f"/state?open={self.cid}&run=0",
                        f"/state?before={self.cid}", "/results.html?run=0")

    def finished_page(self, out, roster, meta, split=None):
        """The page of the single-target run at out, its run started and finished (its results page linked), with its
        server made with urlsplit standing as split (test_ui's splitting; else this Python's own)."""
        with TU.splitting(split):
            watch = L.Watch(["T"], stream=io.StringIO())
        self.addCleanup(watch.close)
        view = watch.start("T", out, self.calls, L.run_facts(meta, "single", roster), F.SingleKind(roster))
        watch.finish(view)
        return watch, view

    def page_headers(self, res, target):
        self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(L.VIEW_LIVE), target)
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff", target)

    def both_pages(self, split=None):
        """The two pages of VIEW_LIVE, each with its own routes (by target, its queries included): setUp's finished
        single-target run (Watch), and a versus run's before its run starts (VersusWatch); each server made with
        urlsplit standing as split."""
        watch, _ = self.finished_page(*self.made, split=split)
        with TU.splitting(split):
            versus = L.VersusWatch("Jane Doe and Acme Corp", stream=io.StringIO())
        self.addCleanup(versus.close)
        return (("Watch", client_of(watch), ("/",) + self.targets),
                ("VersusWatch", client_of(versus), ("/", "/state", L.RESULTS_PATH)))

    def test_a_target_not_in_origin_form_is_refused_403_on_every_python(self):
        """allowed() takes only an origin-form target, a path beginning with a slash, which is all this page sends:
        every other shape test_ui's not_origin_form names (an absolute URL with this server's host, another host, a
        bracketed host well formed or not, a scheme and no host; an authority; an asterisk; a path with no leading
        slash), each carrying the token and aimed at every route of the page, the state with each of its queries and
        the results route, is 403 by GET with the page's headers, on a single-target run's page and a versus run's
        alike; a POST is this page's 405 (Allow: GET) before any check, as every POST to it is. Neither view is asked
        and no results page is read, nothing is printed, and each page answers its own requests after. The same with
        urlsplit standing as macOS's 3.9.6 splits (as measured on the Mac mini), and in the three ways stronger than
        any Python measured: a urlsplit that never raises, http.server keeping a target's leading slashes, and both
        (TU.TARGET_PYTHONS)."""
        for name, split, keep in TU.TARGET_PYTHONS:
            asked, err = [], io.StringIO()
            real_state, real_versus, real_results = L.RunView.state, L.VersusView.state, L.results_of

            def counted(view, query=None):
                asked.append(query)
                return real_state(view, query)

            def counted_versus(view, query=None):
                asked.append(query)
                return real_versus(view, query)

            def read(view):
                asked.append("results")
                return real_results(view)
            with self.subTest(name), TU.reading_slashes(keep), mock.patch("sys.stderr", err):
                pages = self.both_pages(split)
                with mock.patch.object(L.RunView, "state", counted), \
                        mock.patch.object(L.VersusView, "state", counted_versus), \
                        mock.patch.object(L, "results_of", read):
                    for page, client, routes in pages:
                        for route in routes:
                            for shape, target in TU.not_origin_form(client.port, route).items():
                                where = (name, page, shape, target)
                                res, text = client.request("GET", target)
                                self.assertEqual((res.status, text), (403, "403 Forbidden\n"), where)
                                self.page_headers(res, where)
                                for header, value in L.SECURITY_HEADERS:
                                    self.assertEqual(res.getheader(header), value, where)
                                res, text = client.request("POST", target, {})
                                self.assertEqual((res.status, res.getheader("Allow"), text), (405, "GET", ""), where)
                                self.page_headers(res, where)
                    self.assertEqual(asked, [])
                    for page, client, routes in pages:
                        for route in routes:
                            want = 404 if (page, route) == ("VersusWatch", L.RESULTS_PATH) else 200
                            self.assertEqual(client.request("GET", route)[0].status, want, (name, page, route))
                self.assertEqual(len(asked), len(self.targets))   # the run's state, by each query, and its page read;
                #                                                  a versus page with no run yet asks no view
                self.assertEqual(err.getvalue(), "")

    @contextlib.contextmanager
    def counting(self, asked):
        """The view's state and the results read patched to record, in asked, that they were asked."""
        real_state, real_results = L.RunView.state, L.results_of

        def counted(view, query=None):
            asked.append(query)
            return real_state(view, query)

        def read(view):
            asked.append("results")
            return real_results(view)
        with mock.patch.object(L.RunView, "state", counted), mock.patch.object(L, "results_of", read):
            yield

    def test_a_request_line_that_is_not_http_1_is_refused_with_a_status_line(self):
        """As on the form (test_ui's test of the same name), on the live page of a finished planted run: each line
        test_ui's refused_lines names (its POSTs aimed at /state with every proof) has one outcome on every Python from
        3.9: 400, 431 or 505 with a status line (the reviewer's lines among them: four and five words ending in HTTP/0.9 and
        HTTP/0.9 followed by a header line of 70,000 bytes or by 101 headers, 505 by the pages' version rule; HTTP/1.1
        followed by the same, http.server's 431), or no reply for a line of whitespace only; neither the view's state
        nor the results read is asked, nothing is printed, the server closes the connection; then the page answers
        its own requests."""
        asked, err = [], io.StringIO()
        port, token = self.client.port, self.client.token
        with self.counting(asked), mock.patch("sys.stderr", err):
            for name, (data, want) in TU.refused_lines(port, token, f"/state?t={token}").items():
                asked.clear()
                reply, closed = TU.raw_reply(port, data)
                self.assertTrue(closed, name)
                self.assertEqual(asked, [], name)
                if want:
                    self.assertEqual(reply[:len(want)], want, (name, reply[:80]))
                else:
                    self.assertEqual(reply, b"", name)
            self.assertEqual(self.client.request("GET", "/state")[0].status, 200)
            self.assertEqual(len(asked), 1)
        self.assertEqual(err.getvalue(), "")

    def test_a_line_with_no_version_is_answered_before_its_headers(self):
        """A line of two words sent alone by a client that then waits with its side still open is answered 505 with a
        status line at once, before any header is read; the view is not asked and nothing is printed."""
        asked, err = [], io.StringIO()
        port, token = self.client.port, self.client.token
        with self.counting(asked), mock.patch("sys.stderr", err):
            for line in (f"GET /state?t={token}\r\n", f"GET /results.html?t={token}&run=0\r\n"):
                got = TU.first_answer(port, line.encode("latin-1"), timeout=10)
                self.assertEqual(got[:13], b"HTTP/1.0 505 ", (line, got[:80]))
        self.assertEqual((asked, err.getvalue()), ([], ""))

    def test_the_requests_the_page_sends_are_answered_in_http_1_1_and_1_0(self):
        """The page's own requests (the page, its state with each query it sends, the results route), each sent as
        HTTP/1.1, as HTTP/1.0 and as HTTP/01.1 (read as 1.1 on every Python): 200 with a status line, the page's bytes,
        the state as a JSON object, the run's own results page, as before the rule."""
        port, token, err = self.client.port, self.client.token, io.StringIO()
        results = (self.made[0] / "results.html").read_bytes()
        with mock.patch("sys.stderr", err):
            for version in ("HTTP/1.1", "HTTP/1.0", "HTTP/01.1"):
                for target in ("/", *self.targets):
                    got, closed = TU.raw_reply(port, TU.spoken("GET", target, version, port, token))
                    head, _, text = got.partition(b"\r\n\r\n")
                    self.assertTrue(head.startswith(b"HTTP/1.0 200 OK\r\n"), (version, target, head[:60]))
                    self.assertTrue(closed)
                    if target == "/":
                        self.assertEqual(text, L.VIEW_LIVE.encode("utf-8"))
                    elif target.startswith("/results.html"):
                        self.assertEqual(text, results)
                    else:
                        self.assertIsInstance(json.loads(text.decode("utf-8")), dict, (version, target))
        self.assertEqual(err.getvalue(), "")

    def test_a_target_beginning_with_two_slashes_is_read_as_one_on_every_python(self):
        """A target that begins with two slashes or more is read with one, as http.server reads it since gh-87389:
        //state is the state, //results.html?run=0 the run's results page, and //x/state the path /x/state, 404, never
        the host x with the path /state. So on this Python and as macOS's 3.9.6 splits; and so too where http.server
        keeps the slashes (CPython before that fix, stronger than any Python measured), with a urlsplit that never
        raises or this Python's own (TU.TARGET_PYTHONS)."""
        results = (self.made[0] / "results.html").read_bytes()
        cases = (("//", 200), ("//state", 200), ("///state", 200), (f"//state?open={self.cid}", 200),
                 ("//results.html?run=0", 200), ("//x/state", 404), ("//x/results.html?run=0", 404),
                 ("//[x]/state", 404), ("//[", 404), ("//127.0.0.1/state", 404))
        for name, split, keep in TU.TARGET_PYTHONS:
            with self.subTest(name), TU.reading_slashes(keep):
                watch, _ = self.finished_page(*self.made, split=split)
                client = client_of(watch)
                for target, want in cases:
                    res, text = client.request("GET", target)
                    self.assertEqual(res.status, want, (name, target))
                    if target.startswith("//results.html"):
                        self.assertEqual(text.encode("utf-8"), results, (name, target))

    def test_an_error_while_the_answer_is_made_is_the_last_resort(self):
        """The view's state raising (for the plain state, an opened entry and an earlier page alike), a SystemExit
        from it, and the results route's read raising are each answered 500 with a short plain message and the
        page's headers, the traceback printed; the page answers every request after."""
        asked, err = [], io.StringIO()

        def broken(view, query=None):
            asked.append(query)
            raise RuntimeError("a view that fails")

        def leaving(view, query=None):
            raise SystemExit("a view that exits")
        with mock.patch("sys.stderr", err):
            with mock.patch.object(L.RunView, "state", broken), \
                    mock.patch.object(L, "results_of", side_effect=OSError("a results read that fails")):
                for target in self.targets:
                    res, text = self.client.request("GET", target)
                    self.assertEqual((res.status, text), (500, "500 Internal Server Error\n"), target)
                    self.assertEqual(res.getheader("Content-Type"), "text/plain; charset=utf-8", target)
                    self.page_headers(res, target)
            with mock.patch.object(L.RunView, "state", leaving):
                self.assertEqual(self.client.request("GET", self.targets[1])[0].status, 500)
            for target in self.targets:
                self.assertEqual(self.client.request("GET", target)[0].status, 200, target)
        self.assertEqual([sorted(set(q) - {"t"}) for q in asked], [[], ["open"], ["open", "run"], ["before"]])
        said = err.getvalue()
        for words in ("a view that fails", "a results read that fails", "a view that exits"):
            self.assertIn(words, said)
        self.assertEqual(said.count("Traceback (most recent call last)"), len(self.targets) + 1)

    def test_what_a_reply_is_made_from_leaves_the_guard_as_plain_values(self):
        """The state's reply is made by json_reply inside the guard and handed on as plain bytes, whatever subclasses
        the state held; a mapping whose own code raises while it is written is the last resort's 500. The results
        route's parts are plain copies made inside the guard (file_answer); a part of the wrong kind is a 500."""
        class Text(str):
            pass

        class Blob(bytes):
            pass

        class Items(dict):
            def items(self):
                raise RuntimeError("a mapping's items")
        made, real = [], L.json_reply

        def recorded(value, redactor):
            made.append(real(value, redactor))
            return made[-1]
        target = f"/state?open={self.cid}"
        with mock.patch.object(L, "json_reply", recorded), \
                mock.patch.object(L.RunView, "state", lambda view, query=None: {"x": Text("y"),
                                                                                  "open": query.get("open")}):
            res, text = self.client.request("GET", target)
        self.assertEqual(res.status, 200)
        got = json.loads(text)["runs"][0]                 # Watch.state adds each run's results card and bar
        self.assertEqual({k: v for k, v in got.items() if k not in ("card", "bar")},
                         {"x": "y", "open": [str(self.cid)]})
        self.assertEqual([type(x) for x in made], [bytes])
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            with mock.patch.object(L.RunView, "state", lambda view, query=None: Items(a=1)):
                self.assertEqual(self.client.request("GET", target)[0].status, 500)
            parts = (Blob(b"<p>page</p>"), Text("text/html; charset=utf-8"), Text(U.RESULTS_PAGE_CSP))
            with mock.patch.object(L, "results_of", return_value=parts):
                res, text = self.client.request("GET", "/results.html")
                self.assertEqual((res.status, text), (200, "<p>page</p>"))
            with mock.patch.object(L, "results_of", return_value=("<p>a str body</p>", "text/html", "")):
                self.assertEqual(self.client.request("GET", "/results.html")[0].status, 500)
        self.assertIn("RuntimeError: a mapping's items", err.getvalue())
        self.assertEqual(err.getvalue().count("Traceback (most recent call last)"), 2)

    def test_a_target_urlsplit_cannot_split_is_refused_403(self):
        """An absolute-form target with a malformed bracketed host, aimed at the state, an opened entry, an earlier
        page or the results route, is not origin-form and is 403 with the page's headers before anything splits it,
        on a Python whose urlsplit accepts it too (a POST is this page's 405 before any check, as every POST to it
        is); the view is never asked and nothing is printed; the page answers the next request."""
        asked, err, real = [], io.StringIO(), L.RunView.state

        def counted(view, query=None):
            asked.append(query)
            return real(view, query)
        with mock.patch("sys.stderr", err), mock.patch.object(L.RunView, "state", counted):
            for target in ("http://[/", "http://[/state", f"http://[/state?open={self.cid}", "http://[/state?before=9",
                           "http://[/results.html", "a://[x]/state", "http://a]/state"):
                for method, body, want in (("GET", None, 403), ("POST", {}, 405)):
                    res, _ = self.client.request(method, target, body)
                    self.assertEqual(res.status, want, (method, target))
                    self.page_headers(res, target)
            self.assertEqual(asked, [])
            self.assertEqual(self.client.request("GET", "/state")[0].status, 200)
        self.assertEqual(err.getvalue(), "")

    def test_the_page_reads_no_body(self):
        """The depth bound applies where a body is read; this page reads none. A POST to any of its routes, with a body
        nested far past MAX_JSON_DEPTH or with a Content-Length it never sends, is 405 (Allow: GET) at once, before a
        byte of the body is read, and the view is never asked."""
        deep = b'{"x":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
        self.assertTrue(L.json_too_deep(deep))
        asked = []
        with mock.patch.object(L.RunView, "state", lambda view, query=None: asked.append(query) or {}):
            for target in self.targets + ("/",):
                res, text = self.client.request("POST", target, deep)
                self.assertEqual((res.status, res.getheader("Allow"), text), (405, "GET", ""), target)
                conn = http.client.HTTPConnection("127.0.0.1", self.client.port, timeout=10)
                conn.putrequest("POST", target + ("&" if "?" in target else "?") + f"t={self.client.token}",
                                skip_host=True)
                conn.putheader("Host", f"127.0.0.1:{self.client.port}")
                conn.putheader(L.TOKEN_HEADER, self.client.token)
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(L.MAX_BODY))
                conn.endheaders()                       # the body never comes: an answer now means none was read
                res = conn.getresponse()
                res.read()
                conn.close()
                self.assertEqual(res.status, 405, target)
        self.assertEqual(asked, [])


# ---- the page in a headless Chrome: the keyboard, open state, anchoring, text only, the results link (V3, V11) -------
@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestBrowser(unittest.TestCase):
    """The one page (VIEW_LIVE) over a planted single-target run on loopback, in the suite's headless Chrome."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pf-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "run"
        b, self.roster, self.calls, self.meta = TW.v4_run(self.out, tier="quick")
        self.b = b
        self.all = TW.replies_of(self.out)
        TW.write_replies(self.out, self.all[:120])
        self.watch = L.Watch(["T"], stream=io.StringIO())
        self.addCleanup(self.watch.close)
        self.view = self.watch.start("T", self.out, self.calls, L.run_facts(self.meta, "single", self.roster),
                                     F.SingleKind(self.roster))
        self.chrome = TU.Chrome(self.tmp / "profile")
        self.addCleanup(self.chrome.close)
        self.chrome.call("Page.enable")
        self.chrome.call("Page.bringToFront")
        self.chrome.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.chrome.call("Page.navigate", url=self.watch.server.url)
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelectorAll('#feed details.entry').length")
                                    == F.SHOWN, 30))

    def js(self, expression):
        return self.chrome.js(expression)

    def key(self, name, code, vk, text=None):
        for kind in ("keyDown", "keyUp"):
            args = dict(type=kind, key=name, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
            if text and kind == "keyDown":
                args["text"] = text
            self.chrome.call("Input.dispatchKeyEvent", **args)

    def test_an_entry_opens_from_the_keyboard_and_stays_open(self):
        """Tab reaches an entry's summary and Enter opens it; its opened view arrives and it is still open after
        three refreshes; nothing it shows was built as markup."""
        for _ in range(20):
            self.key("Tab", "Tab", 9)
            if self.js("document.activeElement.tagName") == "SUMMARY":
                break
        self.assertEqual(self.js("document.activeElement.tagName"), "SUMMARY")
        self.key("Enter", "Enter", 13, "\r")
        self.assertTrue(TU.wait_for(lambda: self.js("document.activeElement.parentElement.querySelector('.body dl') "
                                                    "!== null"), 10))
        time.sleep(3.5)                                             # three of the page's one-second refreshes
        self.assertTrue(self.js("document.activeElement.parentElement.open"))
        self.assertTrue(self.js("document.activeElement.parentElement.isConnected"))

    def opened_here(self, cid):
        """Whether the feed's entry cid is on the page, open, drawn with a size, its opened view shown, and whether
        it carries the not-refreshed note: (shown, held)."""
        return self.js(f"""(() => {{ const d = document.querySelector('#feed details.entry[data-id="{cid}"]');
          if (!d) return [false, false];
          return [d.isConnected && d.open && d.getClientRects().length > 0 && !!d.querySelector('.body dl'),
                  !!d.querySelector(':scope > .held')]; }})()""")

    def open_entry(self, cid):
        self.js(f"(() => {{ document.querySelector('#feed details.entry[data-id=\"{cid}\"]').open = true; "
                "return true; })()")

    def test_an_opened_entry_stays_however_many_land_after_it(self):
        """V11 (finding 2 of the independent check): an entry the reader opened stays on the page, open and shown and
        still asked for, when 40 more land and the window of SHOWN has moved past it, in its place by landing order;
        closed, it leaves the page."""
        cid = self.js("document.querySelector('#feed details.entry').dataset.id")         # the newest of 120
        self.open_entry(cid)
        self.assertTrue(TU.wait_for(lambda: self.opened_here(cid) == [True, False], 10))
        TW.write_replies(self.out, self.all[:160])
        self.assertTrue(TU.wait_for(lambda: self.js(
            "document.querySelectorAll('#feed details.entry').length") == F.SHOWN + 1, 15))
        time.sleep(2.5)                                             # two more refreshes
        self.assertEqual(self.opened_here(cid), [True, False])
        self.assertEqual(self.js("[...document.querySelectorAll('#feed details.entry')].map(d => d.dataset.id)"
                                 f".indexOf('{cid}')"), F.SHOWN)
        self.assertIn(cid, self.js("opens()"))
        self.js(f"(() => {{ document.querySelector('#feed details.entry[data-id=\"{cid}\"]').open = false; "
                "return true; })()")
        self.assertTrue(TU.wait_for(lambda: not self.js(
            f"!!document.querySelector('#feed details.entry[data-id=\"{cid}\"]')"), 10))
        self.assertEqual(self.js("document.querySelectorAll('#feed details.entry').length"), F.SHOWN)

    def test_past_the_bound_an_opened_entry_says_it_is_not_refreshed(self):
        """The page asks for the opened views of the OPEN_MAX entries opened most recently. Open OPEN_MAX + 1 feed
        entries in turn: the first opened stays open with its last view and says, in words, that it is not refreshed
        and how to follow it again; the others do not; closing one gives it back its refreshes."""
        cids = self.js("[...document.querySelectorAll('#feed details.entry')].map(d => d.dataset.id)")[:F.OPEN_MAX + 1]
        for cid in cids:
            self.open_entry(cid)
            self.assertTrue(TU.wait_for(lambda: self.opened_here(cid)[0], 10), cid)
            time.sleep(0.05)
        self.assertTrue(TU.wait_for(lambda: self.opened_here(cids[0]) == [True, True], 10))
        time.sleep(1.5)
        self.assertEqual(self.opened_here(cids[0]), [True, True])
        self.assertEqual(self.js(f"document.querySelector('#feed details.entry[data-id=\"{cids[0]}\"] > .held')"
                                 ".textContent"), L.LIVE_WORDS["held"])
        for cid in cids[1:]:
            self.assertEqual(self.opened_here(cid), [True, False], cid)
        self.js(f"(() => {{ document.querySelector('#feed details.entry[data-id=\"{cids[-1]}\"]').open = false; "
                "return true; })()")
        self.assertTrue(TU.wait_for(lambda: self.opened_here(cids[0]) == [True, False], 10))

    def test_new_entries_arrive_above_without_moving_an_opened_one(self):
        """An opened entry scrolled to the top of the window does not move when new entries land above it."""
        self.js("const d = document.querySelectorAll('#feed details.entry')[12]; d.open = true; "
                "d.scrollIntoView(); window.__d = d; true")
        self.assertTrue(TU.wait_for(lambda: self.js("window.__d.querySelector('.body dl') !== null"), 10))
        time.sleep(1.2)
        top = self.js("window.__d.getBoundingClientRect().top")
        first = self.js("document.querySelector('#feed details.entry').dataset.id")
        TW.write_replies(self.out, self.all[:126])                  # six more land, above it
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelector('#feed details.entry').dataset.id")
                                    != first, 10))
        time.sleep(1.2)
        self.assertTrue(self.js("window.__d.isConnected && window.__d.open"))
        self.assertLess(abs(self.js("window.__d.getBoundingClientRect().top") - top), 2)

    def test_reading_above_the_feed_new_entries_do_not_move_the_page(self):
        """A reader at "Errands now", above the feed, with the page scrolled: entries landing in the feed below do
        not move what is read (the anchor is the block at the window's top edge, not the feed's first entry)."""
        self.js("document.getElementById('nowh').scrollIntoView(); window.scrollBy(0, -4); true")
        self.assertGreater(self.js("window.scrollY"), 0)
        time.sleep(1.2)
        top = self.js("document.getElementById('nowh').getBoundingClientRect().top")
        first = self.js("document.querySelector('#feed details.entry').dataset.id")
        TW.write_replies(self.out, self.all[:126])                  # six more land, in the feed below
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelector('#feed details.entry').dataset.id")
                                    != first, 10))
        time.sleep(1.2)
        self.assertLess(abs(self.js("document.getElementById('nowh').getBoundingClientRect().top") - top), 2)

    def test_an_errand_whose_world_serves_no_chat_message_says_so(self):
        """An errand of "Errands now" opens on its task as a labelled list in V10's order; the suite's small bank's
        worlds serve no chat message, and the request's place says so in words instead of showing nothing."""
        for c in TW.first_task(self.calls):                         # its episodes start (replies.jsonl untouched)
            folder = P.episode_dir(self.out, c)
            P.start_episode(c, folder)
            (folder / "record.jsonl").write_text("".join(json.dumps(x) + "\n" for x in VS.record_lines([1], 10)))
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelectorAll('#now details.entry').length") > 0, 10))
        self.js("const d = document.querySelector('#now details.entry'); d.open = true; d.scrollIntoView(); true")
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelector('#now details.entry dl.task') !== null"),
                                    10))
        got = self.js("""(() => {
          const t = document.querySelector('#now details.entry dl.task');
          const dts = [...t.children].filter(x => x.tagName === 'DT');
          return {labels: dts.map(x => x.textContent), request: dts[3].nextElementSibling.textContent};
        })()""")
        self.assertEqual(got["labels"], L.LIVE_WORDS["task_fields"])
        self.assertEqual(got["request"], L.LIVE_WORDS["no_request"])

    def test_a_reply_holding_markup_is_shown_as_text(self):
        """A reply that is markup reaches the opened view as its own text: no element is made from it."""
        markup = '<img src=x onerror="window.BAD=1"><script>window.BAD=2</script>'
        recs = TW.replies_of(self.out)
        q = next(r for r in reversed(recs) if r["dataset"] == "v4.I")
        TW.write_replies(self.out, recs + [dict(q, reply=markup)])
        cid = F.call_ids(self.calls)[P.key(q)]
        self.js("window.BAD = 0; true")
        self.assertTrue(TU.wait_for(lambda: self.js(f"!!document.querySelector('#feed details.entry[data-id=\"{cid}\"]')"
                                                    ), 10))
        self.js(f"document.querySelector('#feed details.entry[data-id=\"{cid}\"]').open = true; true")
        self.assertTrue(TU.wait_for(lambda: markup in (self.js(
            f"document.querySelector('#feed details.entry[data-id=\"{cid}\"] .body').textContent") or ""), 10))
        self.assertEqual(self.js("window.BAD"), 0)
        self.assertEqual(self.js("document.querySelectorAll('#feed img, #feed script').length"), 0)

    def test_the_finished_run_links_its_results_page(self):
        """Once the run is scored and wrote results.html, the page links it under its own token, from the results
        card's button (tests/test_results_card.py), and the link serves the run's own file."""
        TW.write_replies(self.out, self.all)
        self.watch.finish(self.view, P.score_run(self.out, self.calls, self.b, self.roster, self.meta, B=50))
        self.assertTrue(TU.wait_for(lambda: self.js("!!document.querySelector('#card a.rc-open')"), 10))
        href = self.js("document.querySelector('#card a.rc-open').href")
        self.assertIn("/results.html?t=", href)
        got = self.js(f"fetch({json.dumps(href)}).then(r => r.text())")
        self.assertEqual(got, (self.out / "results.html").read_text(encoding="utf-8"))


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestBrowserRequest(unittest.TestCase):
    """The errand's task on the one page over a run on the pinned bank, whose errand worlds serve their chat message
    as the published items do (pinned_running), in the suite's headless Chrome."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pf-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        out = self.tmp / "run"
        b, roster, self.calls, meta, kind = pinned_running(out)
        self.watch = L.Watch(["Mozilla (organization)"], stream=io.StringIO())
        self.addCleanup(self.watch.close)
        self.watch.start("Mozilla (organization)", out, self.calls, L.run_facts(meta, "single", roster), kind)
        self.chrome = TU.Chrome(self.tmp / "profile")
        self.addCleanup(self.chrome.close)
        self.chrome.call("Page.navigate", url=self.watch.server.url)
        self.assertTrue(TU.wait_for(lambda: self.chrome.js("document.querySelectorAll('#now details.entry').length")
                                    > 0, 30))

    def test_an_errand_opens_on_the_request_as_the_world_serves_it(self):
        """Each errand of "Errands now" opens on its task as a labelled list in V10's order (works for, benefits,
        the cell, the request, the workspace, what counts as solved), and its request is drawn open, not folded:
        who it is from, the handle it is signed in as and its text, exactly as the world's chat_read serves them."""
        W = L.LIVE_WORDS
        n = self.chrome.js("document.querySelectorAll('#now details.entry').length")
        self.chrome.js("document.querySelectorAll('#now details.entry').forEach(d => { d.open = true; }); true")
        self.assertTrue(TU.wait_for(lambda: self.chrome.js("document.querySelectorAll('#now details.entry dl.task')"
                                                           ".length") == n, 10))
        for i in range(n):
            got = self.chrome.js(f"""(() => {{
              const d = document.querySelectorAll('#now details.entry')[{i}], t = d.querySelector('dl.task');
              const dts = [...t.children].filter(x => x.tagName === 'DT'), req = dts[3].nextElementSibling;
              const qs = [...req.querySelectorAll('blockquote')];
              return {{id: d.dataset.id, labels: dts.map(x => x.textContent),
                       senders: [...req.querySelectorAll('.small')].map(x => x.textContent),
                       texts: qs.map(q => q.textContent),
                       shown: qs.every(q => q.getClientRects().length > 0 && q.closest('details') === d)}};
            }})()""")
            mail = chat_of(self.calls[int(got["id"])])
            self.assertTrue(mail, got["id"])
            self.assertEqual(got["labels"], W["task_fields"])
            self.assertEqual(got["texts"], [m["text"] for m in mail])
            self.assertEqual(got["senders"], [f"{W['request_from']} {m['from']}, "
                                              f"{W['signed_in_as']} {m['signed_in_as']}" for m in mail])
            self.assertTrue(got["shown"], got["id"])


class TestQuery(unittest.TestCase):
    """The page's request is digits alone (V12, V13): at most OPEN_MAX open ids, each below the plan's size; one
    landing number; one errand's whole log. Anything else is dropped, never echoed."""

    def test_digits_alone(self):
        q = F.query_of({"open": ["3,7,x,12,9999,-1, 4"], "before": ["41"], "all": ["7"]}, 100)
        self.assertEqual(q, ({3, 7, 12}, 41, 7))
        self.assertEqual(F.query_of({"open": ["1"] * 3, "before": ["4.5"], "all": ["../x"]}, 10), ({1}, None, None))
        self.assertEqual(F.query_of({}, 10), (set(), None, None))
        self.assertEqual(F.query_of({"open": ["٣"]}, 10), (set(), None, None))      # str.isdigit takes "٣"; this not
        many = ",".join(str(i) for i in range(40))
        self.assertEqual(len(F.query_of({"open": [many]}, 100)[0]), F.OPEN_MAX)

    def test_the_window_pages_back_through_everything(self):
        landed = list(range(1, 101))
        pages, before = [], None
        while True:
            page = F.window(landed, before)
            if not page:
                break
            self.assertLessEqual(len(page), F.SHOWN)
            self.assertEqual(page, sorted(page, reverse=True))
            pages += page
            before = page[-1]
        self.assertEqual(sorted(pages), landed)


if __name__ == "__main__":
    unittest.main()
