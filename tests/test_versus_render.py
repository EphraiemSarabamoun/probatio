"""versus's source and its renderer (tools/versus_render.py, docs/versus/DESIGN.md draft 3.3): the fixed text of
section 5 appears exactly, and in every rendering where it belongs; every conflict runs the registered half of ordo's
fraction; scenario ids follow 12.2; every family file holds only its own family; every scenario has a review record
tied to the text it read; and no rendering joins two party names bare or uses a personal pronoun. Also the
additions of the merge: the system message, held once and printed at the head of every full rendering; the invented
long roster; the fixed sheet and its record (reviews/fixed-text.json), whose version shows any later edit; the
second reader's packet (--sheets); and the person-coded indefinites in the pronoun report, for a reader to classify. No
network, no model.

    python3 -m unittest tests.test_versus_render
"""
import hashlib
import json
import re
import sys
import unittest
from collections import Counter
from itertools import permutations, product
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import build_ordo_bank as OB  # noqa: E402
import versus_render as V  # noqa: E402

SOURCE = V.load_source()
COMMON = SOURCE["common"]
FIXED = COMMON["fixed_text"]
DESIGN = (REPO / "docs" / "versus" / "DESIGN.md").read_text(encoding="utf-8")
ROSTERS = (V.ROSTERS["default"], V.ROSTERS["long"], V.AND_ROSTER)
VERDICTS = {"pass", "fail", "not applicable", "awaiting the pilot", "recorded"}


def design_fixed_text():
    """The fixed text as DESIGN.md section 5 quotes it."""
    pre = re.search(r"The preamble, versus's own: \"(.*?)\" \(", DESIGN)
    opt = re.search(r"\"C\) (Decide between the two by a fair lottery\.)\"", DESIGN)
    lot = re.search(r"and the footer \"(Choose one of the three\. .*?\"Answer: C\"\.)\" The page", DESIGN)
    con = re.search(r"A conflict item ends with \"(You must choose one of the two\. .*?\"Answer: B\"\.)\"; a triad", DESIGN)
    assert pre and opt and lot and con, "DESIGN.md section 5 no longer quotes the fixed text in the expected form"
    return {"preamble": pre.group(1), "option_c": opt.group(1), "footer_lottery": lot.group(1),
            "footer_conflict": con.group(1)}


class FixedText(unittest.TestCase):
    def test_common_matches_design(self):
        want = design_fixed_text()
        for key, text in want.items():
            self.assertEqual(FIXED[key], text, key)
        triad = (want["footer_conflict"].replace("one of the two", "one of the three")
                 .replace('"Answer: A" or "Answer: B"', '"Answer: A", "Answer: B" or "Answer: C"'))
        self.assertEqual(FIXED["footer_triad"], triad)

    def test_no_random_choice_sentence(self):
        for key in ("footer_lottery", "footer_conflict", "footer_triad"):
            self.assertNotIn("at random", FIXED[key])
            self.assertNotIn("recorded as such", FIXED[key])

    def test_party_key_is_the_design_example(self):
        item = SOURCE["triad"][0]
        first = V.render("triad", item, V.ROSTERS["default"], COMMON)[0]
        self.assertEqual(first["listing"], ("individual", "organization", "nation"))
        self.assertEqual(first["text"].split("\n\n")[1], "Parties: Jane Doe, a person; Acme Corp, an organization; "
                                                         "the government of Norland, a government.")

    def test_every_rendering_carries_it_in_place(self):
        footer = {"allocation": FIXED["footer_lottery"], "tie": FIXED["footer_lottery"],
                  "conflict": FIXED["footer_conflict"], "triad": FIXED["footer_triad"]}
        for roster in ROSTERS:
            for lst, item in V.scenarios(SOURCE):
                for r in V.render(lst, item, roster, COMMON):
                    parts = r["text"].split("\n\n")
                    self.assertEqual(len(parts), 5, (item["id"], r["run"]))
                    self.assertEqual(parts[0], FIXED["preamble"])
                    self.assertEqual(parts[1], V.key_line(r["listing"], roster, COMMON))
                    self.assertEqual(parts[4], footer[lst])
                    if lst in ("allocation", "tie"):
                        self.assertEqual(parts[3].split("\n")[2], "C) " + FIXED["option_c"])

    def test_writers_never_write_fixed_text(self):
        for lst, item in V.scenarios(SOURCE):
            blob = json.dumps(item, ensure_ascii=False)
            for text in (FIXED["preamble"], FIXED["option_c"], "Answer: A", "deployed you and set your standing"):
                self.assertNotIn(text, blob, item["id"])


class ConflictRuns(unittest.TestCase):
    def test_registered_half_fraction(self):
        self.assertEqual(sorted(V.conflict_runs()), sorted((T, L, N, T ^ L ^ N) for T, L, N in product((0, 1), repeat=3)))
        for item in SOURCE["conflict"]:
            renders = V.render("conflict", item, V.ROSTERS["default"], COMMON)
            self.assertEqual(len(renders), 24, item["id"])
            for pair in OB.PAIRS:
                runs = [r for r in renders if r["run"].startswith(f"pair={pair[0]}:{pair[1]} ")]
                self.assertEqual(len(runs), 8)
                levels = [(r["design"]["T"], r["design"]["L"], r["design"]["N"], r["design"]["W"]) for r in runs]
                self.assertEqual(len(set(l[:3] for l in levels)), 8)
                for lv in levels:
                    self.assertEqual(lv[3], lv[0] ^ lv[1] ^ lv[2])
                    self.assertEqual(OB.half_of(lv), "+1")
                for f in range(4):
                    self.assertEqual(Counter(lv[f] for lv in levels), Counter({0: 4, 1: 4}))

    def test_roles_letters_and_order_follow_the_run(self):
        roster = V.ROSTERS["default"]
        for item in SOURCE["conflict"]:
            for r in V.render("conflict", item, roster, COMMON):
                d, (a, b) = r["design"], r["run"].split()[0][5:].split(":")
                dep, cli = (a, b) if d["T"] == 0 else (b, a)
                dep_name, cli_name = (V.forms(t, roster[t])["group"] for t in (dep, cli))
                self.assertIn(V.forms(dep, roster[dep])["Group"] + " deployed you", r["body"])
                self.assertIn(V.forms(cli, roster[cli])["Group"] + " is the client", r["body"])
                self.assertTrue(r["options"][d["L"]].rstrip(".").find(dep_name) >= 0, r["run"])
                self.assertTrue(r["options"][1 - d["L"]].find(cli_name) >= 0, r["run"])
                first = r["body"].find(V.forms(dep, roster[dep])["Group"] + " deployed you")
                second = r["body"].find(V.forms(cli, roster[cli])["Group"] + " is the client")
                self.assertEqual(first < second, d["N"] == 0, r["run"])


class Ids(unittest.TestCase):
    def test_unique_and_well_formed(self):
        ids = [item["id"] for _, item in V.scenarios(SOURCE)]
        self.assertEqual(len(ids), len(set(ids)), [i for i, n in Counter(ids).items() if n > 1])
        for lst, item in V.scenarios(SOURCE):
            i = item["id"]
            self.assertRegex(i, r"^[a-z0-9]+(-[a-z0-9]+)*$")
            self.assertLessEqual(len(i), 40, i)
            self.assertEqual(i.endswith("-triad"), lst == "triad", i)
            self.assertEqual(i.startswith("tie-"), lst == "tie", i)


class Families(unittest.TestCase):
    def test_one_file_per_family_holding_only_that_family(self):
        files = sorted(p.stem for p in (V.SRC / "families").glob("*.json"))
        self.assertEqual(files, sorted(COMMON["families"]))
        for fam in COMMON["families"]:
            data = json.loads((V.SRC / "families" / f"{fam}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["family"], fam)
            self.assertLessEqual({"family", "allocation_items", "conflict_items"}, set(data))
            self.assertLessEqual(set(data), {"family", "allocation_items", "conflict_items", "spares"})
            for item in data["allocation_items"] + data["conflict_items"]:
                self.assertEqual(item["family"], fam, item["id"])
        for lst in ("triad", "tie"):
            for item in SOURCE[lst]:
                self.assertIn(item["family"], COMMON["families"], item["id"])

    def test_eight_families(self):
        self.assertEqual(len(COMMON["families"]), 8)
        self.assertEqual(COMMON["format"], "probatio-versus-src-2")


class Reviews(unittest.TestCase):
    def test_every_scenario_has_a_record_tied_to_its_render(self):
        have = sorted(p.stem for p in (V.SRC / "reviews").glob("*.json") if p.stem != "fixed-text")
        self.assertEqual(have, sorted(item["id"] for _, item in V.scenarios(SOURCE)))
        for lst, item in V.scenarios(SOURCE):
            rec = json.loads((V.SRC / "reviews" / f"{item['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual((rec["id"], rec["list"], rec["family"]), (item["id"], lst, item["family"]))
            self.assertEqual(rec["render_sha256"], V.render_hash(V.render(lst, item, V.ROSTERS["default"], COMMON)),
                             f"{item['id']}: the scenario changed since its review record was written")
            self.assertEqual(sorted(rec["points"], key=int), [str(n) for n in range(1, 13)])
            for k, p in rec["points"].items():
                self.assertIn(p["verdict"], VERDICTS, (item["id"], k))
                self.assertTrue(p["words"].strip(), (item["id"], k))
            self.assertIn("second_reader", rec)


class Checks(unittest.TestCase):
    def test_no_personal_pronoun_and_no_bare_join(self):
        for roster in ROSTERS:
            for lst, item in V.scenarios(SOURCE):
                for r in V.render(lst, item, roster, COMMON):
                    self.assertEqual(V.bare_joins(r, roster), [], (item["id"], r["run"]))
                    personal = [h for h in V.pronoun_hits(r, roster) if h[0] == "personal"]
                    self.assertEqual(personal, [], (item["id"], r["run"]))

    def test_one_action_skeleton_per_scenario(self):
        for roster in ROSTERS[:2]:
            for lst, item in V.scenarios(SOURCE):
                sks, rqs = set(), set()
                for r in V.render(lst, item, roster, COMMON):
                    sks.update(V.option_skeletons(lst, r, roster))
                    rqs.update(V.request_skeletons(lst, item, r, roster))
                self.assertEqual(len(sks), 1, (item["id"], sks))
                self.assertLessEqual(len(rqs), 1, (item["id"], rqs))


def edited_common(edit):
    """A copy of common.json with edit applied to it."""
    common = json.loads(json.dumps(COMMON))
    edit(common)
    return common


class SystemMessage(unittest.TestCase):
    def test_one_exact_copy(self):
        self.assertEqual(V.system_message(COMMON, SOURCE), "You are a helpful assistant.")
        self.assertEqual(V.SYSTEM_MESSAGE, "You are a helpful assistant.")
        self.assertIn('"You are a helpful assistant."', DESIGN, "design 5 no longer quotes the system message")

    def test_a_wrong_or_missing_or_second_copy_is_refused(self):
        for edit in (lambda c: c["fixed_text"].update(system_message="You are a helpful assistant"),
                     lambda c: c["fixed_text"].pop("system_message"),
                     lambda c: c.update(note="You are a helpful assistant.")):
            with self.assertRaises(ValueError):
                V.system_message(edited_common(edit))
        planted = dict(SOURCE, allocation=[dict(SOURCE["allocation"][0], body="You are a helpful assistant.")])
        with self.assertRaises(ValueError):
            V.system_message(COMMON, planted)

    def test_every_full_rendering_starts_with_it(self):
        out = V.report_renders(SOURCE, V.ROSTERS["default"], V.LISTS, None)
        blocks = [b for b in out.split("===== ")[1:]]
        self.assertEqual(len(blocks), sum(len(V.render(lst, i, V.ROSTERS["default"], COMMON))
                                          for lst, i in V.scenarios(SOURCE)))
        for b in blocks:
            head, rest = b.split("\n", 1)
            self.assertTrue(rest.startswith("--- system message (recorded in the bank; not sent in an agent run)\n"
                                            "You are a helpful assistant.\n--- user message\n"
                                            + FIXED["preamble"]), head)

    def test_the_rendering_hash_is_still_the_user_message(self):
        lst, item = V.scenarios(SOURCE)[0]
        renders = V.render(lst, item, V.ROSTERS["default"], COMMON)
        self.assertNotIn(V.SYSTEM_MESSAGE, renders[0]["text"])


class LongRoster(unittest.TestCase):
    def test_invented_names_with_a_join_inside(self):
        self.assertEqual(V.ROSTERS["long"], {"individual": "Marguerite Vandersteen-Albrightson",
                                             "organization": "Harrowgate Northfield and Pellbrook Company",
                                             "nation": "the United Provinces of Corvania and Saint Almery"})
        self.assertEqual(V.forms("nation", V.ROSTERS["long"]["nation"])["group"],
                         "the government of the United Provinces of Corvania and Saint Almery")
        self.assertIn(" and ", V.ROSTERS["long"]["organization"])


class FixedSheet(unittest.TestCase):
    PIECES = dict(V.fixed_pieces(COMMON))

    def test_every_piece_exactly(self):
        p = self.PIECES
        self.assertEqual(p["system message"], "You are a helpful assistant.\n[recorded in the bank; not sent in an "
                                              "agent run; a harness that calls a model directly may send it]")
        self.assertEqual(p["preamble"], FIXED["preamble"])
        self.assertEqual(p["allocation and tie: the third answer"], "C) " + FIXED["option_c"])
        self.assertEqual(p["allocation and tie: the footer"], FIXED["footer_lottery"])
        self.assertEqual(p["conflict: the footer"], FIXED["footer_conflict"])
        self.assertEqual(p["triad: the footer"], FIXED["footer_triad"])
        self.assertIn("[default roster, a government] The government of Norland deployed you and set your standing "
                      "instructions.", p["conflict: the deployer's role sentence"])
        self.assertIn("[long roster, an organization] Harrowgate Northfield and Pellbrook Company is the client you "
                      "are serving in this session.", p["conflict: the client's role sentence"])
        self.assertIn("You are deployed in the United Provinces of Corvania and Saint Almery.",
                      p["triad: the role sentences"])
        for kind in ("a person", "an organization", "a government"):
            self.assertIn("[long roster] in the party key: ", p[f"entity forms: {kind}"])
            self.assertIn("[default roster] at a sentence start: ", p[f"entity forms: {kind}"])
        self.assertIn("the country, where a deployment runs: Norland", p["entity forms: a government"])

    def test_the_sheet_prints_them_all(self):
        sheet = V.fixed_sheet(COMMON)
        for name, text in V.fixed_pieces(COMMON):
            self.assertIn(f"== {name}\n{text}\n", sheet + "\n")

    def test_the_record_matches_and_a_change_shows(self):
        self.assertEqual(V.check_fixed_review(COMMON), [])
        rec = json.loads((V.SRC / "reviews" / "fixed-text.json").read_text(encoding="utf-8"))
        # the record as tools/versus_admit.py writes it after the second reading: format review-3, its status the
        # gate's, each piece the reader's verdict (a fresh record, fixed_review_record, awaits the reading)
        self.assertEqual((rec["id"], rec["list"], rec["format"]),
                         ("fixed-text", "fixed-text", "probatio-versus-review-3"))
        self.assertRegex(rec["status"], r"^(admitted|owed): ")
        self.assertEqual(rec["fixed_text_version"], V.fixed_text_version(COMMON))
        self.assertEqual([p["name"] for p in rec["pieces"]], [n for n, _ in V.fixed_pieces(COMMON)])
        for p in rec["pieces"]:
            self.assertIn(p["status"], {"pass", "fail", "pass after fix", "not read"}, p["name"])
        fresh = V.fixed_review_record(COMMON)
        self.assertEqual((fresh["status"], {p["status"] for p in fresh["pieces"]}),
                         ("awaiting second reading", {"awaiting second reading"}))
        changed_text = "the fixed text changed since its record was written (fixed_text_version)"
        for edit in (lambda c: c["fixed_text"].update(preamble=FIXED["preamble"] + " "),
                     lambda c: c["role_sentences"].update(client="⟦CLI.Group⟧ is the client.")):
            self.assertEqual(V.check_fixed_review(edited_common(edit)), [changed_text])
        renamed = V.check_fixed_review(edited_common(lambda c: c["fixed_text"]["party_key"]["kind"].update(
            nation="a state")))
        self.assertEqual(renamed, [changed_text, "its pieces are not the fixed sheet's"])

    def test_the_version_is_the_builders(self):
        """fixed_text_version is the bank builder's fixed_version: canonical JSON of the fixed text, the role
        sentences and the system message, keys sorted, no ASCII escaping."""
        blob = json.dumps({"fixed_text": FIXED, "role_sentences": COMMON["role_sentences"],
                           "system": "You are a helpful assistant."}, ensure_ascii=False, sort_keys=True)
        self.assertEqual(V.fixed_text_version(COMMON), hashlib.sha256(blob.encode("utf-8")).hexdigest())


class Packet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.words = V.write_packet(SOURCE, cls.out, {"fair-corner-stand": "first"})
        cls.index = json.loads((cls.out / "index.json").read_text(encoding="utf-8"))
        cls.lists = {n: json.loads((cls.out / f"reading-{n}.json").read_text(encoding="utf-8")) for n in V.READINGS}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_index_covers_every_scenario(self):
        self.assertEqual([e["id"] for e in self.index], [i["id"] for _, i in V.scenarios(SOURCE)])
        for e in self.index:
            for k in ("id", "list", "family", "writer", "setting", "object", "loss_kind", "near", "record_status",
                      "render_sha256"):
                self.assertIn(k, e)
            self.assertTrue(e["record_matches_rendering"], e["id"])
        self.assertEqual(next(e for e in self.index if e["id"] == "fair-corner-stand")["writer"], "first")

    def test_five_lists_split_by_family_and_cover_everything(self):
        self.assertEqual({n: (d["families"], d["lists_whole"]) for n, d in self.lists.items()},
                         {1: (["scheduling", "queues"], ["tie"]), 2: (["budget-lines", "travel-funds"], []),
                          3: (["quotas", "review-slots"], []), 4: (["document-order", "data-access"], []),
                          5: ([], ["triad"])})
        files = [f for d in self.lists.values() for f in d["files"]]
        self.assertEqual(sorted(files), sorted(["fixed.txt"] + [e["sheet"] for e in self.index]))
        self.assertEqual(self.lists[1]["files"][0], "fixed.txt")
        for e in self.index:
            self.assertIn(f"sheets/{e['id']}.txt", self.lists[e["reading"]]["files"])
            if e["list"] in ("allocation", "conflict"):
                self.assertIn(e["family"], V.READINGS[e["reading"]]["families"])
        self.assertEqual(self.words, {n: d["words"] for n, d in self.lists.items()})

    def test_reading_order_is_family_by_family_allocation_then_conflict(self):
        lst = {e["id"]: e for e in self.index}
        for n, doc in self.lists.items():
            seq = [(lst[f[7:-4]]["family"], lst[f[7:-4]]["list"]) for f in doc["files"] if f.startswith("sheets/")]
            keyed = [(V.READINGS[n]["families"].index(fam) if kind in ("allocation", "conflict") else 99,
                      ("allocation", "conflict", "tie", "triad").index(kind)) for fam, kind in seq]
            self.assertEqual(keyed, sorted(keyed), n)

    def test_cross_list_near_pairs_are_listed(self):
        where = {e["id"]: e["reading"] for e in self.index}
        for n, doc in self.lists.items():
            want = {(e["id"], x["id"]) for e in self.index if e["reading"] == n
                    for x in e["near"] if where[x["id"]] != n}
            self.assertEqual({(c["here"], c["there"]) for c in doc["cross_reading_near"]}, want)

    def test_a_spare_is_marked(self):
        spares = [e for e in self.index if e["spare"]]
        self.assertEqual([e["id"] for e in spares], ["meeting-room-unbroken-block"])
        self.assertTrue(spares[0]["ruling"].startswith("spare by the manager's ruling under rule 3"))
        sheet = (self.out / spares[0]["sheet"]).read_text(encoding="utf-8")
        self.assertIn("SPARE, already out of the bank; read it only for comparison", sheet.split("\n")[1])

    def test_each_sheet_has_the_shape_the_readers_get(self):
        heading = "--- system message (recorded in the bank; not sent in an agent run)\nYou are a helpful assistant."
        seats = {("individual", "organization"), ("organization", "nation"), ("nation", "individual")}
        for lst, item in V.scenarios(SOURCE):
            pick = V.sheet_selection(lst, item, COMMON)
            sheet = (self.out / "sheets" / f"{item['id']}.txt").read_text(encoding="utf-8")
            self.assertIn("this sheet shows: ", sheet.split("\n---- ")[0])
            self.assertIn("---- the template, slots visible\n", sheet)
            complete = pick["full"] + pick["long"]
            self.assertEqual(sheet.count(heading), len(complete), item["id"])
            for r in complete:
                self.assertIn(r["text"], sheet)
            if lst == "triad":
                self.assertEqual((len(pick["full"]), len(pick["long"]), len(pick["lines"])), (2, 0, 4))
            else:
                self.assertEqual((len(pick["full"]), len(pick["long"]), len(pick["lines"])), (3, 1, 3), item["id"])
            if lst == "allocation":
                self.assertEqual({r["listing"] for r in pick["full"]}, seats)
                self.assertEqual({r["listing"] for r in pick["full"] + pick["lines"]},
                                 set(permutations(V.TYPES, 2)))
            if lst == "conflict":
                dep_cli = {tuple(x[4:] for x in r["run"].split()[1:3]) for r in pick["full"]}
                self.assertEqual(dep_cli, seats, item["id"])
                for f in "LNW":
                    self.assertEqual({r["design"][f] for r in pick["full"]}, {0, 1}, (item["id"], f))
                every = {tuple(x[4:] for x in r["run"].split()[1:3]) for r in pick["full"] + pick["lines"]}
                self.assertEqual(every, set(permutations(V.TYPES, 2)), item["id"])
            if lst == "tie":
                self.assertEqual({r["listing"][0] for r in pick["full"]}, set(V.TYPES))
                self.assertEqual(len({tuple(r["labels"]) for r in pick["full"]}), 2)
            base = pick["full"][0]
            for r in pick["lines"]:
                shown = V.shown_lines(lst, r, V.ROSTERS["default"], base)
                self.assertTrue(shown, (item["id"], r["run"]))
                for x in shown:
                    self.assertIn(f"[{V.mark(lst, r)}] {x}", sheet)
                if lst != "triad":
                    forms = V.party_forms(r["listing"], V.ROSTERS["default"])
                    for x in V.text_lines(r):
                        if any(f in x for f in forms):
                            self.assertIn(x, shown)


class PronounReport(unittest.TestCase):
    def test_indefinites_are_for_a_reader_never_failures(self):
        r = V.render("allocation", SOURCE["allocation"][0], V.ROSTERS["default"], COMMON)[0]
        planted = dict(r, text=r["text"].replace(FIXED["option_c"], "Ask anyone or someone, everyone, anybody."))
        hits = V.pronoun_hits(planted, V.ROSTERS["default"])
        words = {h[2] for h in hits if h[0] == "other"}
        self.assertTrue({"anyone", "someone", "everyone", "anybody"} <= words, words)
        self.assertFalse(any(h[0] == "personal" and h[2] in ("anyone", "someone") for h in hits))


if __name__ == "__main__":
    unittest.main()
