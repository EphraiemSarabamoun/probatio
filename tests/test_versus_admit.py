"""tools/versus_admit.py on a copy of the source and a planted readings folder: a pass on the current rendering, a
pass on a stale one, a fail, a fail on point 8 alone for the list's form group (ruling R10) and the same without the
group, a failed reading followed by a re-reading that passes, a reading no packet confirms; the spares from the
selection, a placeholder, the fixed text's record; every record checked by the builder's own gate functions; and a
second run that changes nothing. No network, no model, no builder run (the report's builder view is planted).

    python3 -m unittest tests.test_versus_admit
"""
import contextlib
import copy
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import build_versus_bank as BV  # noqa: E402
import versus_admit as A  # noqa: E402
import versus_render as V  # noqa: E402

SRC = REPO / "bank" / "versus" / "src"
WHEN = datetime.datetime(2026, 11, 12, 12, 0).timestamp()      # the readings' modification time: the identity's date
STALE = "a" * 64                                               # a rendering hash that is no scenario's current one
R4_WORDS = "R4 list-level failure: four of this list share the request form, above three."


def points(fail=(), why8="fine"):
    """Twelve point verdicts, the ones named in fail failed (point 8's why given)."""
    out = {}
    for k in A.POINTS:
        out[k] = {"verdict": "fail" if k in fail else "pass", "why": why8 if k == "8" else f"point {k} read"}
    return out


def entry(sid, lst, fam, overall, h, fail=(), why8="fine"):
    """One reading.json entry in the readers' shape."""
    return {"id": sid, "list": lst, "family": fam, "points": points(fail, why8), "overall": overall, "fix": None,
            "quotes": [f"a quote from {sid}"], "type_argument": "none from the text", "reviewer": "a reader",
            "source_commit": "abc1234", "render_sha256": h}


def write_json(path, obj):
    """Write obj as JSON, making the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def as_review2(rec):
    """A record as the scenario branch writes it (format review-2), from a review-3 one: its status and second reader
    before the admission (or a pending slot), its first reader without a vendor."""
    out = {k: v for k, v in rec.items() if k not in ("status_before_second_reading", "earlier_second_reading")}
    out["format"] = "probatio-versus-review-2"
    out["status"] = rec.get("status_before_second_reading", rec.get("status"))
    out["second_reader"] = rec.get("earlier_second_reading") or {"reader": "pending: a second agent of another vendor",
                                                                 "verdict": None}
    out["first_reader"] = {k: v for k, v in rec.get("first_reader", {}).items() if k != "vendor"}
    return out


class Planted(unittest.TestCase):
    """A copy of the source, a packet, two readings folders (the second a re-reading) and a selection."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        shutil.copytree(SRC, self.src)
        self.source = V.load_source(self.src)
        common = self.source["common"]
        self.hash = {i["id"]: V.render_hash(V.render(lst, i, V.ROSTERS["default"], common))
                     for lst, i in V.scenarios(self.source)}
        alloc = self.source["allocation"]
        fam = alloc[0]["family"]
        ids = [i["id"] for i in alloc if i["family"] == fam]
        (self.ok, self.stale, self.bad, self.r10, self.reread, self.nogroup, self.lost) = ids[:7]
        self.fam = fam
        for sid in (self.ok, self.stale):                      # the scenario branch's records: format review-2
            rec = json.loads((self.src / "reviews" / f"{sid}.json").read_text(encoding="utf-8"))
            write_json(self.src / "reviews" / f"{sid}.json", as_review2(rec))
        self.packet = self.tmp / "packet"
        shown = {sid: self.hash[sid] for sid in self.hash}
        shown[self.stale] = STALE
        self.sheets(self.packet, shown, fixed=V.fixed_sheet(common))
        self.again = self.tmp / "reread" / "packet"
        self.sheets(self.again, {self.reread: self.hash[self.reread]})
        cur = self.hash
        first = [entry(self.ok, "allocation", fam, "pass", cur[self.ok]),
                 entry(self.stale, "allocation", fam, "pass", STALE),
                 entry(self.bad, "allocation", fam, "fail", cur[self.bad], fail=("3",)),
                 entry(self.r10, "allocation", fam, "fail", cur[self.r10], fail=("8",), why8=R4_WORDS),
                 entry(self.reread, "allocation", fam, "fail", cur[self.reread], fail=("2",)),
                 entry(self.nogroup, "allocation", fam, "fail", cur[self.nogroup], fail=("8",), why8=R4_WORDS),
                 entry(self.lost, "allocation", fam, "pass", "b" * 64)]
        lists = [{"family": fam, "list": "allocation", "request_form_groups": [
            {"form": "X has asked for Y", "ids": [self.r10, self.ok, self.bad, self.stale]},
            {"form": "another", "ids": [self.nogroup, self.reread, self.lost]}]}]
        self.readings = self.tmp / "read-1"
        write_json(self.readings / "reading.json", first)
        write_json(self.readings / "lists.json", lists)
        write_json(self.readings / "fixed.json", self.fixed_reading(common))
        later = entry(self.reread, "allocation", fam, "pass", None)
        del later["render_sha256"]                             # a re-reader that records no hash: its own packet
        write_json(self.tmp / "reread" / "reading.json", [later])
        for f in (self.readings / "reading.json", self.tmp / "reread" / "reading.json"):
            os.utime(f, (WHEN, WHEN))
        self.selection = self.tmp / "selection.json"
        write_json(self.selection, self.select())

    def sheets(self, folder, hashes, fixed=None):
        """A packet: index.json and one sheet per id with its rendering hash on the head, and fixed.txt."""
        index = []
        for sid, h in hashes.items():
            sheet = folder / "sheets" / f"{sid}.txt"
            sheet.parent.mkdir(parents=True, exist_ok=True)
            sheet.write_text(f"{sid} | a sheet\nrendering hash (default roster): {h}\nthe text\n", encoding="utf-8")
            index.append({"id": sid, "render_sha256": h, "sheet": f"sheets/{sid}.txt"})
        write_json(folder / "index.json", index)
        if fixed is not None:
            (folder / "fixed.txt").write_text(fixed, encoding="utf-8")

    def fixed_reading(self, common, sheet=None):
        """fixed.json in the reader's shape: every piece passed, on the sheet given (default the current one)."""
        return {"overall": "pass", "source_commit": "abc1234",
                "fixed_sha256": sheet or A.sha256(V.fixed_sheet(common)),
                "pieces": [{"id": pid, "overall": "pass", "why": f"{pid} read", "quotes": [], "fix": None, "limits": []}
                           for pid in A.FIXED_PIECES]}

    def select(self):
        """A selection in manager-selection-1's shape: per list the first six of the source as bank, the rest spare."""
        sel = {"format": "manager-selection-1", "allocation": {}, "conflict": {}}
        for lst in ("allocation", "conflict"):
            for fam in self.source["common"]["families"]:
                ids = [i["id"] for i in self.source[lst] if i["family"] == fam]
                sel[lst][fam] = {"bank": ids[:6], "spare": ids[6:], "fixes": {}}
        tri = [i["id"] for i in self.source["triad"]]
        sel["triad"] = {"bank": tri[:12], "spare": tri[12:], "fixes": {}}
        sel["tie"] = {"bank": [i["id"] for i in self.source["tie"]], "spare": [], "fixes": {}}
        return sel

    def run_admit(self, readings=None):
        """The admission on the planted inputs, written into the copy; (states, ctx, files written)."""
        files, states, ctx = A.admit(self.src, self.selection, readings or [self.readings, self.tmp / "reread"],
                                     [self.packet])
        return states, ctx, A.write_files(self.src, files)

    def check(self):
        """The command line's --check on the planted inputs: its exit code, its line kept off the test's output."""
        with contextlib.redirect_stdout(io.StringIO()):
            return A.main(["--check", "--selection", str(self.selection), "--readings", str(self.readings),
                           str(self.tmp / "reread"), "--packets", str(self.packet), "--src", str(self.src)])

    def record(self, sid):
        """A record of the copy, as written."""
        return json.loads((self.src / "reviews" / f"{sid}.json").read_text(encoding="utf-8"))

    def gate(self, sid):
        """The builder's own review gaps of a scenario's written record against its current rendering."""
        lst = next(lst for lst, i in V.scenarios(self.source) if i["id"] == sid)
        item = next(i for _, i in V.scenarios(self.source) if i["id"] == sid)
        return BV.review_gaps(lst, item, self.record(sid), self.hash[sid])


class Gate(Planted):
    """Each planted reading reaches the state the task's rulings give it, and the builder's gate agrees."""

    def test_states(self):
        states, ctx, _ = self.run_admit()
        word = {sid: A.state_word(states[sid]) for sid in
                (self.ok, self.stale, self.bad, self.r10, self.reread, self.nogroup, self.lost)}
        self.assertEqual(word[self.ok], "passed")
        self.assertEqual(word[self.stale], "owed: a re-reading of the changed text")
        self.assertEqual(word[self.bad], "owed: a change meeting point(s) 3, then a re-reading of the changed text")
        self.assertEqual(word[self.r10], "passed (R10)")
        self.assertEqual(word[self.reread], "passed")
        self.assertTrue(word[self.nogroup].startswith("owed: a change meeting point(s) 8"), word[self.nogroup])
        self.assertTrue(word[self.lost].startswith("owed: a reading whose rendering hash a packet sheet confirms"))
        self.assertEqual(ctx["reading_problems"],
                         [f"{self.lost} in read-1: no packet sheet of this id carries the hash the reader recorded"])

    def test_the_builders_gate_takes_exactly_the_passed(self):
        self.run_admit()
        for sid in (self.ok, self.r10, self.reread):
            self.assertEqual(self.gate(sid), [], sid)
        self.assertIn("the second reader read another rendering than the current one", self.gate(self.stale))
        self.assertIn("the second reader's verdict is 'fail', not a pass", self.gate(self.bad))
        self.assertIn("the second reader's verdict is 'fail', not a pass", self.gate(self.nogroup))
        untouched = next(i["id"] for i in self.source["conflict"])
        self.assertIn("the second reader's verdict is 'not read', not a pass", self.gate(untouched))

    def test_records(self):
        self.run_admit()
        ok, r10, again = self.record(self.ok), self.record(self.r10), self.record(self.reread)
        for rec in (ok, r10, again):
            self.assertEqual(rec["format"], "probatio-versus-review-3")
            self.assertEqual(rec["first_reader"]["vendor"], "anthropic")
            self.assertEqual(rec["second_reader"]["vendor"], "openai")
            self.assertEqual(rec["second_reader"]["verdict"], "pass")
            self.assertNotIn("reviewer", rec["second_reader"])
        s = ok["second_reader"]
        self.assertEqual(s["reader"], "an independent reader of another vendor, second reading 2026-11-12")
        self.assertEqual((s["render_sha256_read"], s["commit_read"], s["overall_read"]),
                         (self.hash[self.ok], "abc1234", "pass"))
        self.assertEqual(s["quotes"], [f"a quote from {self.ok}"])
        self.assertEqual(set(s["points"]), set(A.POINTS))
        self.assertTrue(ok["status"].startswith("admitted: the second reader passed the current text"))
        self.assertIn("status_before_second_reading", ok)       # a review-2 record's status is kept
        self.assertEqual(r10["second_reader"]["overall_read"], "fail")
        self.assertIn("ruling R10", r10["second_reader"]["note"])
        self.assertTrue(r10["second_reader"]["note"].endswith(R4_WORDS))
        self.assertIn("under ruling R10", r10["status"])
        self.assertEqual(again["second_reader"]["reader"],
                         "an independent reader of another vendor, re-reading 2026-11-12")
        stale = self.record(self.stale)["second_reader"]
        self.assertEqual((stale["verdict"], stale["overall_read"], stale["owed"]),
                         ("owed", "pass", "a re-reading of the changed text"))
        for rec in (ok, r10, again):
            self.assertIsInstance(rec["near"], list)

    def test_an_earlier_reading_is_kept_and_a_pending_slot_is_not(self):
        path = self.src / "reviews" / f"{self.bad}.json"
        rec = as_review2(json.loads(path.read_text(encoding="utf-8")))
        pending = {"reader": "pending: a second agent of another vendor", "verdict": None}
        write_json(path, dict(rec, second_reader=pending))
        self.run_admit()
        self.assertNotIn("earlier_second_reading", self.record(self.bad))
        earlier = {"reader": "an earlier reader", "verdict": "keep", "render_sha256_read": STALE}
        write_json(path, dict(rec, second_reader=earlier))
        self.run_admit()
        self.assertEqual(self.record(self.bad)["earlier_second_reading"], earlier)

    def test_the_newest_reading_of_the_current_hash_wins(self):
        later = entry(self.ok, "allocation", self.fam, "fail", self.hash[self.ok], fail=("6",))
        write_json(self.tmp / "read-2" / "reading.json", [later])
        states, _, _ = self.run_admit([self.readings, self.tmp / "read-2"])
        self.assertFalse(states[self.ok]["passed"])
        self.assertEqual(self.gate(self.ok), ["the second reader's verdict is 'fail', not a pass"])
        stale_later = entry(self.ok, "allocation", self.fam, "fail", STALE)    # a later reading of another text
        write_json(self.tmp / "read-2" / "reading.json", [stale_later])
        self.sheets(self.tmp / "read-2" / "packet", {self.ok: STALE})
        states, _, _ = self.run_admit([self.readings, self.tmp / "read-2"])
        self.assertTrue(states[self.ok]["passed"])              # the current text's own reading still decides

    def test_a_sheet_rewritten_after_its_index_counts_by_its_head(self):
        index = json.loads((self.packet / "index.json").read_text(encoding="utf-8"))
        for e in index:
            if e["id"] == self.ok:
                e["render_sha256"] = STALE                      # the index is stale; the sheet's head is current
        write_json(self.packet / "index.json", index)
        states, ctx, _ = self.run_admit()
        self.assertTrue(states[self.ok]["passed"])
        self.assertTrue(any(f"sheets/{self.ok}.txt's head carries" in x for x in ctx["reading_problems"]))

    def test_a_packet_without_an_index_is_read_from_its_sheet_heads(self):
        bare = self.tmp / "bare"
        for sub, sid in (("sheets", self.reread), ("list-sheets", self.ok)):
            (bare / sub).mkdir(parents=True, exist_ok=True)
            (bare / sub / f"{sid}.txt").write_text(f"{sid} | allocation | {self.fam}\nrendering hash (default "
                                                   f"roster): {self.hash[sid]}\n", encoding="utf-8")
        (bare / "sheets" / "stray.txt").write_text(f"{self.bad} | allocation\n", encoding="utf-8")
        packet = A.load_packet(bare)
        self.assertEqual(packet["sheets"][self.reread][0][0], self.hash[self.reread])
        self.assertIn(self.ok, packet["sheets"])
        self.assertEqual(packet["problems"],
                         [f"bare: sheets/stray.txt heads another id ({self.bad!r}); it is not read"])
        later = entry(self.reread, "allocation", self.fam, "pass", self.hash[self.reread])
        write_json(self.tmp / "read-2" / "reading.json", [later])
        files, states, _ = A.admit(self.src, self.selection, [self.readings, self.tmp / "read-2"], [self.packet, bare])
        self.assertTrue(states[self.reread]["passed"])

    def test_no_record_is_made_for_a_scenario_without_one(self):
        (self.src / "reviews" / f"{self.bad}.json").unlink()
        _, ctx, written = self.run_admit()
        self.assertFalse((self.src / "reviews" / f"{self.bad}.json").exists())
        self.assertNotIn(f"reviews/{self.bad}.json", written)
        self.assertIn(f"{self.bad}: no review record (reviews/{self.bad}.json); none is written",
                      ctx["reading_problems"])

    def test_a_pass_beside_a_failed_point_is_not_a_pass(self):
        odd = entry(self.ok, "allocation", self.fam, "pass", self.hash[self.ok], fail=("4",))
        write_json(self.tmp / "read-2" / "reading.json", [odd])
        states, _, _ = self.run_admit([self.tmp / "read-2"])
        self.assertEqual(A.state_word(states[self.ok]),
                         "owed: a re-reading: the reader passed it overall but failed point(s) 4")
        self.assertEqual(self.gate(self.ok), ["the second reader's verdict is 'owed', not a pass"])


class SparesAndSelection(Planted):
    def test_spares_are_the_selections(self):
        self.run_admit()
        sel = json.loads(self.selection.read_text(encoding="utf-8"))
        for fam in self.source["common"]["families"]:
            data = json.loads((self.src / "families" / f"{fam}.json").read_text(encoding="utf-8"))
            want = sel["allocation"][fam]["spare"] + sel["conflict"][fam]["spare"]
            self.assertEqual(data.get("spares", []), want, fam)
        tri = json.loads((self.src / "triads.json").read_text(encoding="utf-8"))
        self.assertEqual(tri.get("spares", []), sel["triad"]["spare"])
        self.assertNotIn("spares", json.loads((self.src / "ties.json").read_text(encoding="utf-8")))
        self.assertEqual(BV.read_spares(self.src, self.source["common"]).keys(),
                         set(sel["triad"]["spare"]) | {s for lst in ("allocation", "conflict")
                                                       for f in sel[lst].values() for s in f["spare"]})

    def test_a_placeholder_is_a_gap_and_is_not_written(self):
        sel = json.loads(self.selection.read_text(encoding="utf-8"))
        fam = list(self.source["common"]["families"])[0]
        moved = sel["conflict"][fam]["bank"].pop()
        sel["conflict"][fam]["bank"].append("<NEW>")
        sel["conflict"][fam]["spare"].append("<the other one>")
        write_json(self.selection, sel)
        _, ctx, _ = self.run_admit()
        self.assertIn(f"conflict/{fam}: the placeholder <NEW> is not resolved to a scenario", ctx["selection_gaps"])
        self.assertIn(f"{moved} (conflict/{fam}) is in neither bank nor spare", ctx["selection_gaps"])
        data = json.loads((self.src / "families" / f"{fam}.json").read_text(encoding="utf-8"))
        self.assertFalse(any(A.is_placeholder(s) for s in data.get("spares", [])))


class FixedText(Planted):
    def test_the_fixed_record_passes_the_builders_gate(self):
        self.run_admit()
        rec = self.record(A.FIXED_ID)
        common = self.source["common"]
        self.assertEqual(BV.fixed_text_gaps(rec, V.fixed_text_version(common)), [])
        self.assertEqual(V.check_fixed_review(common, self.src), [])
        self.assertEqual(rec["second_reader"]["fixed_text_version_read"], V.fixed_text_version(common))
        self.assertEqual({p["status"] for p in rec["pieces"]}, {"pass"})

    def test_a_stale_sheet_or_a_failed_piece_is_not_a_pass(self):
        common = self.source["common"]
        write_json(self.readings / "fixed.json", self.fixed_reading(common, sheet="c" * 64))
        self.run_admit()
        rec = self.record(A.FIXED_ID)
        self.assertIsNone(rec["second_reader"]["fixed_text_version_read"])
        self.assertTrue(BV.fixed_text_gaps(rec, V.fixed_text_version(common)))
        bad = self.fixed_reading(common)
        bad["pieces"][1]["overall"] = "fail"
        write_json(self.readings / "fixed.json", bad)
        self.run_admit()
        rec = self.record(A.FIXED_ID)                            # the reader's overall still says pass
        self.assertEqual((rec["second_reader"]["verdict"], rec["second_reader"]["overall_read"]), ("owed", "pass"))
        self.assertIn("the second reader's verdict is 'owed', not a pass",
                      BV.fixed_text_gaps(rec, V.fixed_text_version(common)))
        self.assertTrue(rec["status"].startswith("owed: a change to the piece(s) preamble"))


class Idempotence(Planted):
    def test_a_second_run_changes_nothing(self):
        _, _, first = self.run_admit()
        self.assertTrue(first)
        snapshot = {p: p.read_bytes() for p in self.src.rglob("*.json")}
        _, _, second = self.run_admit()
        self.assertEqual(second, [])
        self.assertEqual({p: p.read_bytes() for p in self.src.rglob("*.json")}, snapshot)
        self.assertEqual(self.check(), 0)

    def test_review2_and_review3_inputs_give_one_record(self):
        self.run_admit()
        once = self.record(self.ok)
        write_json(self.src / "reviews" / f"{self.ok}.json", as_review2(copy.deepcopy(once)))
        self.assertEqual(self.check(), 1)
        self.run_admit()
        self.assertEqual(self.record(self.ok), once)


class Report(Planted):
    def test_report_lists_every_bank_id_and_the_builders_verdict(self):
        self.run_admit()
        _, states, ctx = A.admit(self.src, self.selection, [self.readings, self.tmp / "reread"], [self.packet])
        builder = {"registered_accepted": False, "refusal": "refused", "left_out": {}, "past_the_axes": {},
                   "review_gaps": {self.stale: ["read another rendering"]}, "owed": []}
        rep = A.build_report(states, ctx, builder)
        first = rep["lists"][0]
        self.assertEqual((first["list"], first["family"]), ("allocation", self.fam))
        self.assertEqual([r["id"] for r in first["bank"]], json.loads(
            self.selection.read_text(encoding="utf-8"))["allocation"][self.fam]["bank"])
        text = A.report_text(rep)
        self.assertIn(f"{self.stale} (a re-reading of the changed text)", text)
        self.assertIn("registered build: refused", text)
        self.assertLess(len(text.splitlines()), 60)


if __name__ == "__main__":
    unittest.main()
