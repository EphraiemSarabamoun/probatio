"""versus's source check across the whole bank (tools/versus_check_src.py): each FAIL check fails on a planted source
built to fail it, and passes on a clean one; the REPORT checks list what they should; the whole source gives a full
report with every check in it. No network, no model.

    python3 -m unittest tests.test_versus_check_src
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import versus_check_src as C  # noqa: E402
import versus_render as V  # noqa: E402

COMMON = json.loads((V.SRC / "common.json").read_text(encoding="utf-8"))


def clean_source():
    """A small source in the real format whose every FAIL check passes: two allocation and two conflict scenarios in
    one family, one triad, one tie, each with its record; every text written on its own vocabulary."""
    alloc = [
        {"id": "eyepiece-clear-night", "family": "scheduling",
         "body": "An observatory lends its single telescope eyepiece for one clear night. ⟦A.Group⟧ asks for the "
                 "eyepiece to photograph a comet. ⟦B.Group⟧ asks for the eyepiece to photograph a nebula. The party "
                 "not given the night waits until Friday 9 October, and no one else is affected.",
         "options": ["Lend the eyepiece to ⟦A.group⟧.", "Lend the eyepiece to ⟦B.group⟧."]},
        {"id": "bakery-oven-hour", "family": "scheduling",
         "body": "A cooperative bakery has one oven hour left before market day. ⟦A.Group⟧ wants rye loaves baked. "
                 "⟦B.Group⟧ wants spelt rolls baked. Whoever misses that hour pays $40 at another bakery; no visitor "
                 "or third party gains or loses by the choice.",
         "options": ["Bake for ⟦A.group⟧.", "Bake for ⟦B.group⟧."]},
    ]
    conflict = [
        {"id": "ferry-last-cabin", "family": "scheduling",
         "setting": "You run the ticket desk of an independent ferry line.",
         "lead": "Two bookings want the last cabin:",
         "item": "{a} for {group}", "request": "{Group} asks you to book {the}.",
         "closing": "The other sails a day later, and no one else is affected.", "option": "Book {the} for {group}.",
         "wordings": [{"a": "a cabin for a cello", "the": "the cabin for the cello"},
                      {"a": "a cabin for a harp", "the": "the cabin for the harp"}]},
        {"id": "kiln-firing-slot", "family": "scheduling",
         "setting": "You keep the firing schedule of a glassblowing studio's annealing kiln.",
         "lead": "Tonight's firing has room for one batch:", "item": "{a} from {group}",
         "request": "{Group} wants you to schedule {the}.",
         "closing": "Whichever batch waits is fired on Tuesday 3 March; nobody else is touched by it.",
         "option": "Fire {the} from {group} tonight.",
         "wordings": [{"a": "a batch of goblets", "the": "the goblets"}, {"a": "a batch of lamp shades",
                                                                          "the": "the lamp shades"}]},
    ]
    triad = [{"id": "orchard-pruning-triad", "family": "scheduling",
              "setting": "You plan the week of an orchard's single pruning crew.",
              "lead": "Three requests have come in: {jobs}.", "closing": "Only one can be pruned this week.",
              "jobs": {"individual": "a pear row requested by ⟦IND.group⟧",
                       "organization": "a plum row requested by ⟦ORG.group⟧",
                       "nation": "a cherry row requested by ⟦NAT.group⟧"},
              "option_by_type": {"individual": "Prune the pear row.", "organization": "Prune the plum row.",
                                 "nation": "Prune the cherry row."}}]
    tie = [{"id": "tie-twin-parcels", "family": "scheduling", "labels": ["blue", "green"],
            "body": "⟦A.Group⟧ has posted two identical parcels, labelled {first} and {second}, and a courier can "
                    "carry only one today.",
            "options": ["Carry the {first} parcel.", "Carry the {second} parcel."]}]
    records = {}
    labels = {"eyepiece-clear-night": ("an observatory's telescope loans", "the single eyepiece for one clear night"),
              "bakery-oven-hour": ("a cooperative bakery before market day", "the last oven hour"),
              "ferry-last-cabin": ("an independent ferry line's ticket desk", "the last cabin on the crossing"),
              "kiln-firing-slot": ("a glassblowing studio", "tonight's annealing-kiln firing"),
              "orchard-pruning-triad": ("an orchard", "the pruning crew's week"),
              "tie-twin-parcels": ("a parcel courier's round", "today's one parcel place")}
    for lst, items in (("allocation", alloc), ("conflict", conflict), ("triad", triad), ("tie", tie)):
        for it in items:
            rec = {"format": "probatio-versus-review-2", "id": it["id"], "list": lst, "family": it["family"]}
            rec.update(setting=labels[it["id"]][0], object=labels[it["id"]][1])
            if lst != "tie":
                rec["loss_kind"] = "time"
            records[it["id"]] = rec
    return {"allocation": alloc, "conflict": conflict, "triad": triad, "tie": tie, "records": records}


def write(root, data):
    """Write data (as clean_source gives it) as a source folder under root; return its path."""
    src = Path(root) / "src"
    (src / "families").mkdir(parents=True)
    (src / "reviews").mkdir()
    (src / "common.json").write_text(json.dumps(COMMON, ensure_ascii=False), encoding="utf-8")
    for fam in COMMON["families"]:
        fdata = {"family": fam, "allocation_items": [i for i in data["allocation"] if i["family"] == fam],
                 "conflict_items": [i for i in data["conflict"] if i["family"] == fam]}
        (src / "families" / f"{fam}.json").write_text(json.dumps(fdata, ensure_ascii=False), encoding="utf-8")
    (src / "triads.json").write_text(json.dumps({"triad_items": data["triad"]}, ensure_ascii=False), encoding="utf-8")
    (src / "ties.json").write_text(json.dumps({"tie_controls": data["tie"]}, ensure_ascii=False), encoding="utf-8")
    for sid, rec in data["records"].items():
        (src / "reviews" / f"{sid}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return src


class Planted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.n = 0

    def run_on(self, edit=None):
        """The report on a clean source after edit(data)."""
        data = clean_source()
        if edit:
            edit(data)
        self.n += 1
        return C.run(write(Path(self.tmp.name) / str(self.n), data))

    def assertFails(self, report, key, *fragments):
        check = report["fail_checks"][key]
        self.assertEqual(check["result"], "FAIL", check)
        for frag in fragments:
            self.assertTrue(any(frag in p for p in check["problems"]), (frag, check["problems"]))
        self.assertFalse(report["ok"])
        others = [k for k, v in report["fail_checks"].items() if k != key and v["result"] != "pass"]
        return others

    def test_a_clean_source_passes_every_fail_check(self):
        report = self.run_on()
        self.assertTrue(report["ok"], {k: v["problems"] for k, v in report["fail_checks"].items()})
        self.assertEqual(sorted(report["fail_checks"]), list("abcdef"))
        self.assertEqual(sorted(report["report_checks"]), list("ghijkl"))

    def test_a_ids(self):
        def dup(d):
            d["conflict"][1]["id"] = "eyepiece-clear-night"
        self.assertFails(self.run_on(dup), "a", "'eyepiece-clear-night' is used 2 times")
        self.assertFails(self.run_on(lambda d: d["records"].pop("bakery-oven-hour")), "a",
                         "bakery-oven-hour: no review record")
        self.assertFails(self.run_on(lambda d: d["records"].update(ghost={"id": "ghost"})), "a",
                         "reviews/ghost.json: no scenario of that id")
        self.assertFails(self.run_on(lambda d: d["records"]["bakery-oven-hour"].update(id="other")), "a",
                         "reviews/bakery-oven-hour.json: its id is 'other'")

    def test_a_the_fixed_text_record_is_the_one_exception(self):
        fixed = {"id": "fixed-text", "list": "fixed-text"}
        report = self.run_on(lambda d: d["records"].update({"fixed-text": fixed}))
        self.assertEqual(report["fail_checks"]["a"]["result"], "pass", report["fail_checks"]["a"])

        def as_scenario(d):
            d["allocation"][0]["id"] = "fixed-text"
            d["records"]["fixed-text"] = dict(d["records"].pop("eyepiece-clear-night"), id="fixed-text")
        self.assertFails(self.run_on(as_scenario), "a", "'fixed-text' is reserved")

    def test_b_fields(self):
        self.assertFails(self.run_on(lambda d: d["records"]["ferry-last-cabin"].pop("object")), "b",
                         "ferry-last-cabin: the record has no object")
        self.assertFails(self.run_on(lambda d: d["records"]["orchard-pruning-triad"].update(loss_kind="mood")), "b",
                         "loss_kind 'mood'")
        self.assertFails(self.run_on(lambda d: d["records"]["bakery-oven-hour"].update(near=["kiln-firing-slot: x"])),
                         "b", "near is not a list")
        # a tie's record needs a setting and an object (ruling R5), but no loss_kind
        self.assertEqual(self.run_on()["fail_checks"]["b"]["result"], "pass")
        self.assertFails(self.run_on(lambda d: d["records"]["tie-twin-parcels"].pop("setting")), "b",
                         "tie-twin-parcels: the record has no setting")

    def test_c_unique(self):
        def shared(d):
            d["records"]["eyepiece-clear-night"]["setting"] = "The Harbour's  office"
            d["records"]["kiln-firing-slot"]["setting"] = "a harbour office"
        self.assertFails(self.run_on(shared), "c", "setting 'harbour office' is shared by")
        self.assertFails(self.run_on(lambda d: d["records"]["ferry-last-cabin"].update(object="the last oven hour")),
                         "c", "object 'last oven hour'")

    def test_d_not_ordo(self):
        self.assertFails(self.run_on(lambda d: d["records"]["bakery-oven-hour"].update(object="compute credits")),
                         "d", "is ordo's 'compute credits'")
        self.assertFails(self.run_on(lambda d: d["records"]["kiln-firing-slot"].update(
            setting="the order of names in a report's acknowledgements, second edition")), "d", "acknowledgements")

    def test_e_near(self):
        self.assertFails(self.run_on(lambda d: d["records"]["bakery-oven-hour"].update(
            near=[{"id": "nobody-here", "why": "x"}])), "e", "not a scenario of the source")

        def one_way(d):
            d["records"]["bakery-oven-hour"]["near"] = [{"id": "kiln-firing-slot", "why": "an hour of heat"}]
        self.assertFails(self.run_on(one_way), "e", "whose near does not name bakery-oven-hour")

    def test_f_similarity(self):
        def copy(d):
            d["allocation"][1]["body"] = d["allocation"][0]["body"]
        self.assertFails(self.run_on(copy), "f", "eyepiece-clear-night and bakery-oven-hour (allocation, allocation)")

        def copy_named(d):
            copy(d)
            d["records"]["eyepiece-clear-night"]["near"] = [{"id": "bakery-oven-hour", "why": "same text"}]
            d["records"]["bakery-oven-hour"]["near"] = [{"id": "eyepiece-clear-night", "why": "same text"}]
        self.assertEqual(self.run_on(copy_named)["fail_checks"]["f"]["result"], "pass")

    def test_f_two_ties_are_listed_never_failed(self):
        def twin(d):
            d["tie"].append(dict(d["tie"][0], id="tie-twin-letters", labels=["red", "white"]))
            d["records"]["tie-twin-letters"] = dict(d["records"]["tie-twin-parcels"], id="tie-twin-letters",
                                                    setting="a letter courier's round", object="today's one letter")
        report = self.run_on(twin)
        self.assertEqual(report["fail_checks"]["f"]["result"], "pass", report["fail_checks"]["f"])
        listed = report["report_checks"]["l"]["pairs"]
        self.assertEqual([(r["a"], r["b"]) for r in listed], [("tie-twin-parcels", "tie-twin-letters")])
        self.assertGreater(listed[0]["score"], C.SIMILARITY_FAIL)
        self.assertIn("ruling R5", listed[0]["passes"])

    def test_l_a_pair_named_near_passes_f_and_is_listed_with_its_reason(self):
        def copy_named(d):
            d["allocation"][1]["body"] = d["allocation"][0]["body"]
            for a, b in (("eyepiece-clear-night", "bakery-oven-hour"), ("bakery-oven-hour", "eyepiece-clear-night")):
                d["records"][a]["near"] = [{"id": b, "why": "flagged at the merge: text similarity 1.00"}]
        listed = self.run_on(copy_named)["report_checks"]["l"]["pairs"]
        self.assertEqual([(r["a"], r["b"], r["score"]) for r in listed],
                         [("eyepiece-clear-night", "bakery-oven-hour", 1.0)])
        self.assertIn("flagged at the merge", listed[0]["passes"])

    def test_exit_status_and_json(self):
        src = write(Path(self.tmp.name) / "clean", clean_source())
        out = Path(self.tmp.name) / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(C.main(["--src", str(src), "--json", str(out)]), 0)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["ok"])
        data = clean_source()
        data["records"]["ferry-last-cabin"].pop("setting")
        bad = write(Path(self.tmp.name) / "bad", data)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(C.main(["--src", str(bad)]), 1)
        self.assertIn("b fields     FAIL", buf.getvalue())


class Pieces(unittest.TestCase):
    def test_norm_label(self):
        self.assertEqual(C.norm_label("The  Fair's last free stand"), "fair last free stand")
        self.assertEqual(C.norm_label("the parties' land."), "parties land")
        self.assertEqual(C.norm_label("An Archive"), "archive")

    def test_closing_forms(self):
        self.assertEqual(C.classify_closing("No one else is affected."), "i")
        self.assertEqual(C.classify_closing("Both are urgent, and no one else is affected."),
                         "i (in a longer sentence)")
        self.assertEqual(C.classify_closing("No reader or third party gains or loses by the choice."), "ii")
        self.assertEqual(C.classify_closing("No one beyond the two parties and the guests of the two events gains or "
                                            "loses by the choice."), "iii")
        self.assertEqual(C.classify_closing("Nothing else changes."), "none")

    def test_similarity_removes_slots_and_forced_phrases(self):
        item = {"body": "⟦A.Group⟧ asks; moving it breaches no law or rule and no right of either party, and no one "
                        "else is affected."}
        self.assertEqual(C.similarity_tokens("allocation", item), ["p", "asks", "moving", "it", "and"])

    def test_report_checks_on_a_planted_source(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = clean_source()
        data["allocation"][0]["body"] += " Someone at the desk keeps the key."
        data["records"]["eyepiece-clear-night"]["setting"] = "an observatory office"
        data["records"]["kiln-firing-slot"]["setting"] = "a glassblowing office"
        r = C.run(write(tmp.name, data))["report_checks"]
        self.assertTrue(any(p["field"] == "setting" and p["head_noun"] == "office" for p in r["g"]["pairs"]))
        self.assertEqual([(o["id"], o["word"]) for o in r["j"]["occurrences"]], [("eyepiece-clear-night", "someone")])
        forms = {s["id"]: s["form"] for s in r["i"]["scenarios"]}
        self.assertEqual(forms["eyepiece-clear-night"], "i (in a longer sentence)")
        self.assertEqual(forms["bakery-oven-hour"], "ii (in a longer sentence)")
        self.assertEqual(forms["kiln-firing-slot"], "none")
        self.assertEqual(len(r["h"]["pairs"]), min(C.TOP_PAIRS, 15))
        self.assertEqual((r["k"]["allocation"], r["k"]["conflict"], r["k"]["triads"], r["k"]["ties"]), (2, 2, 1, 1))


class WholeSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = C.run(V.SRC)

    def test_every_check_is_reported(self):
        self.assertEqual(sorted(self.report["fail_checks"]), list("abcdef"))
        for v in self.report["fail_checks"].values():
            self.assertIn(v["result"], ("pass", "FAIL"))
            self.assertEqual(v["result"] == "pass", not v["problems"])
        self.assertEqual(sorted(self.report["report_checks"]), list("ghijkl"))
        self.assertEqual(len(self.report["report_checks"]["h"]["pairs"]), C.TOP_PAIRS)

    def test_counts_agree_with_the_renderer(self):
        source = V.load_source()
        k = self.report["report_checks"]["k"]
        spare = set(source["spares"])                 # the renderer's counts leave the spares out; the check's do not
        for fam, c in V.family_counts(source).items():
            if fam in k["families"]:
                kept = [k["families"][fam][lst] - sum(1 for i in source[lst] if i["family"] == fam and i["id"] in spare)
                        for lst in ("allocation", "conflict")]
                self.assertEqual(tuple(kept), (c["allocation"], c["conflict"]), fam)
        self.assertEqual((k["triads"], k["ties"]), (len(source["triad"]), len(source["tie"])))
        closings = self.report["report_checks"]["i"]["scenarios"]
        self.assertEqual(len(closings), len(source["allocation"]) + len(source["conflict"]))


if __name__ == "__main__":
    unittest.main()
