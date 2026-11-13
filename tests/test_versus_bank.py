"""versus's bank: the builder (tools/build_versus_bank.py) on the current source, whose registered build is the
shipped bank/versus/ under versus.py's pin; every row byte for byte what tools/versus_render.py prints for it; the
mechanical checks (in --development mode on edited copies); the registered gate on a complete synthetic source; and
the loader and validator (versus.load_bank, versus.check_bank) refusing a bank that fails, in plain words. No
network, no model.

    python3 -m unittest tests.test_versus_bank
"""
import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import build_versus_bank as BV  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_axes  # noqa: E402
import versus_render as V  # noqa: E402
from test_versus_fixture import (development_bank, development_bank_folder, full_bank, full_bank_folder,  # noqa: E402
                                 review_record)

SRC = REPO / "bank" / "versus" / "src"
BANK = REPO / "bank" / "versus"
ROSTERS = (V.ROSTERS["default"], V.ROSTERS["long"], V.AND_ROSTER)


def shipped_bank():
    """bank/versus/ as a run loads it: under versus.py's pin, registered, not development."""
    return VS.load_bank()


def write_bank(files, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return folder


class RegisteredBuild(unittest.TestCase):
    """The committed bank/versus/ is the registered build of the current source, and versus.py pins it."""

    def test_committed_bank_is_a_fresh_registered_build(self):
        files = BV.build(SRC, development=False)               # the builder's registered checks accept it
        for name, data in files.items():
            self.assertEqual((BANK / name).read_bytes(), data, f"bank/versus/{name} is stale: rebuild it")
        self.assertEqual(hashlib.sha256(files["manifest.json"]).hexdigest(), VS.VERSUS_MANIFEST_SHA256)

    def test_manifest_says_what_it_is(self):
        """The admitted source (tools/versus_admit.py): registered, not development and not validated; six per list
        of every family, twelve triads two in each of the six registered families, four ties; every spare the
        source lists; the fixed text reviewed."""
        bank = shipped_bank()
        m = bank.manifest
        self.assertEqual((m["format"], m["registered"], m["development"]), (VS.VERSUS_FORMAT, True, None))
        self.assertFalse(bank.development)
        self.assertNotIn("validated", m)                        # no pilot has run: every page says not calibrated
        self.assertEqual(m["version"], BV.VERSION)
        self.assertEqual(m["counts"]["scenarios"], {"allocation": 48, "conflict": 48, "tie": 4, "triad": 12})
        self.assertEqual(m["counts"]["rows"], {"allocation": 48 * 6, "conflict": 48 * 24, "tie": 24, "triad": 72})
        fams = Counter(m["scenarios"][s]["family"] for s in m["tiers"]["standard"]["triad"])
        self.assertEqual(fams, Counter({f: 2 for f in m["families"][:6]}))
        self.assertTrue(m["fixed_text"]["reviewed"])
        self.assertEqual(set(m["spares"]), set(BV.read_spares(SRC, V.load_source(SRC)["common"])))
        self.assertEqual(len(m["spares"]), 21)
        for name in ("items.jsonl", "entities.json"):
            self.assertEqual(m["files"][name], hashlib.sha256((BANK / name).read_bytes()).hexdigest())
        srcs = {f"src/{p.relative_to(SRC).as_posix()}" for p in SRC.rglob("*.json")}
        self.assertEqual(set(m["sources"]), srcs)
        self.assertEqual(m["fixed_text"]["system"], "You are a helpful assistant.")
        self.assertEqual(m["tiers"]["standard"]["errand_seeds"], list(versus_axes.ERRAND_SEEDS))
        for tier, n in (("quick", 16), ("standard", 48)):
            self.assertEqual([len(m["tiers"][tier][f]) for f in ("allocation", "conflict", "triad", "tie")],
                             [n, n, 12, 4])

    def test_the_unfamiliar_roster_is_the_baselines_first_invented_entities(self):
        ent = shipped_bank().entities
        self.assertEqual({t: ent["unfamiliar"][t]["name"] for t in VS.TYPES},
                         {"individual": "Karim Nassar", "organization": "Thirlbeck Systems", "nation": "Arvenia"})
        self.assertEqual(ent["secretariat"]["full"], "the joint secretariat")

    def test_a_development_build_of_the_current_source_owes_nothing(self):
        dev = json.loads(BV.build(SRC, development=True)["manifest.json"])["development"]
        self.assertEqual({k: dev[k] for k in ("left_out", "past_the_axes", "review_gaps", "owed")},
                         {"left_out": {}, "past_the_axes": {}, "review_gaps": {}, "owed": []})

    def test_command_line_check(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            BV.main(["--check"])                                # the registered build matches what is committed
        self.assertIn("matches a fresh build", out.getvalue())
        with self.assertRaises(SystemExit):
            BV.main(["--check", "--development"])               # a development build is another bank
        with self.assertRaises(SystemExit) as cm:              # a bank's source sits beside it
            BV.main(["--check", "--src", str(SRC), "--out", str(REPO / "bank" / "elsewhere")])
        self.assertIn("a bank's source sits beside it", str(cm.exception))

    def test_ties_record_their_label_order(self):
        source = V.load_source(SRC)
        written = {i["id"]: i["labels"] for i in source["tie"]}
        ties = [r for r in shipped_bank().rows if r["frame"] == "tie"]
        self.assertEqual(len(ties), 24)
        for r in ties:
            order = "as_written" if r["labels"] == written[r["scenario"]] else "reversed"
            self.assertEqual(r["design"], {"label_order": order}, r["template_id"])


class OneRenderingPath(unittest.TestCase):
    """Every row, filled with versus.entity_forms through probatio's render, is what versus_render prints."""

    def check_bank(self, bank, source):
        """The number of renderings checked: every rendering, on the three rosters, of every scenario the bank holds
        (a spare, or a triad past the axes, stays in the source and out of the bank)."""
        by = {(r["scenario"], r["render_run"]): r for r in bank.rows}
        n = 0
        for lst, item in V.scenarios(source):
            if item["id"] not in bank.manifest["scenarios"]:
                continue
            for roster in ROSTERS:
                for r in V.render(lst, item, roster, source["common"]):
                    row = by[(item["id"], r["run"])]
                    types = row["listing"] if lst != "tie" else row["listing"][:1]
                    roles = {VS.ROLE[t]: VS.entity_forms(t, roster[t]) for t in types}
                    self.assertEqual(P.render(row["text"], roles), r["text"], (row["template_id"], roster))
                    self.assertEqual([P.render(c["text"], roles) for c in row["choices"]], r["options"])
                    n += 1
        return n

    def test_shipped_bank_rows_are_the_renderers_text(self):
        bank = shipped_bank()
        self.assertEqual(self.check_bank(bank, V.load_source(SRC)), 3 * len(bank.rows))

    def test_full_bank_rows_are_the_renderers_text(self):
        src, _ = full_bank_folder()
        self.assertEqual(self.check_bank(full_bank(), V.load_source(src)), 3 * (48 * 6 + 48 * 24 + 12 * 6 + 4 * 6))

    def test_rows_carry_their_facts(self):
        for r in shipped_bank().rows:
            self.assertEqual(set(VS.ROW_KEYS) - set(r), set())
            self.assertTrue(r["text"].startswith(r["fixed_text"]["preamble"] + "\n\n" + "Parties: "))
            self.assertTrue(r["text"].endswith(r["fixed_text"]["footer"]))
            self.assertEqual(r["system"], "You are a helpful assistant.")
            self.assertNotIn("qzx", r["text"].lower())
            if r["frame"] == "conflict":
                d = r["design"]
                self.assertEqual(d["W"], d["T"] ^ d["L"] ^ d["N"])
                sides = {c["side"]: c["maps_to"] for c in r["choices"]}
                self.assertEqual((sides["deployer"], sides["client"]), (d["deployer"], d["client"]))
            if r["frame"] == "allocation":
                self.assertEqual([c["maps_to"] for c in r["choices"]], r["listing"] + ["lottery"])


class MechanicalChecks(unittest.TestCase):
    """Each check leaves a scenario out of a development bank, with its reason, and blocks a registered one. The
    scenarios edited are the first allocation scenarios of quotas and data-access that are not spares (a spare is
    out of the bank, so no check reports on it)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        shutil.copytree(SRC, self.src)
        self.qi, self.q = self.kept("quotas")
        self.di, self.d = self.kept("data-access")

    def kept(self, fam):
        """(index, id) of the family file's first allocation scenario that is not a spare."""
        data = json.loads((self.src / "families" / f"{fam}.json").read_text())
        spares = set(data.get("spares", []))
        return next((i, it["id"]) for i, it in enumerate(data["allocation_items"]) if it["id"] not in spares)

    def edit(self, rel, fn):
        p = self.src / rel
        data = json.loads(p.read_text())
        fn(data)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def edit_item(self, fam, fn):
        """Change the kept allocation scenario of quotas or data-access in place."""
        i = {"quotas": self.qi, "data-access": self.di}[fam]
        self.edit(f"families/{fam}.json", lambda d: fn(d["allocation_items"][i]))

    def left_out(self):
        m = json.loads(BV.build(self.src, development=True)["manifest.json"])
        return m["development"]["left_out"], m

    def test_a_malformed_id_is_left_out(self):
        self.edit_item("quotas", lambda it: it.update(id="Network_Storage"))
        out, m = self.left_out()
        self.assertIn("Network_Storage", out)
        self.assertNotIn("Network_Storage", m["scenarios"])
        self.edit_item("quotas", lambda it: it.update(id="x" * 41))
        self.assertIn("x" * 41, self.left_out()[0])
        self.edit_item("quotas", lambda it: it.update(id="storage-triad"))
        self.assertIn("storage-triad", self.left_out()[0])

    def test_a_duplicate_id_is_left_out(self):
        self.edit_item("quotas", lambda it: it.update(id=self.d))
        out, _ = self.left_out()
        self.assertTrue(any("used by 2 scenarios" in r for r in out[self.d]))

    def test_a_scenario_in_the_wrong_family_file_is_left_out(self):
        self.edit_item("quotas", lambda it: it.update(family="queues"))
        out, _ = self.left_out()
        self.assertTrue(any("carries family 'queues'" in r for r in out[self.q]))

    def test_a_shared_setting_or_object_leaves_both_out(self):
        for sid in (self.d, self.q):
            self.edit(f"reviews/{sid}.json", lambda d, sid=sid: d.update(setting="A Shared  archive",
                                                                          object=f"obj {sid}"))
        self.edit(f"reviews/{self.d}.json", lambda d: d.update(setting="a shared archive"))
        out, _ = self.left_out()
        self.assertTrue(any("setting 'a shared archive' is shared" in r for r in out[self.d]))
        self.assertIn(self.q, out)

    def test_a_personal_pronoun_or_a_bare_join_is_left_out(self):
        def pron(it):
            self.assertIn("Neither party has any authority", it["body"])
            it["body"] = it["body"].replace("Neither party has any authority",
                                            "They know neither party has any authority")
        self.edit_item("data-access", pron)
        out, _ = self.left_out()
        self.assertTrue(any("personal pronoun 'they'" in r for r in out[self.d]))
        self.edit_item("quotas", lambda it: it.update(body=it["body"] + " ⟦A.Group⟧ and ⟦B.group⟧ agree."))
        out, _ = self.left_out()
        self.assertTrue(any("bare join" in r for r in out[self.q]))

    def test_two_action_skeletons_are_left_out(self):
        def skew(it):
            self.assertIn("Give", it["options"][1])
            it["options"][1] = it["options"][1].replace("Give", "Hand")
        self.edit_item("data-access", skew)
        out, _ = self.left_out()
        self.assertTrue(any("one action skeleton" in r for r in out[self.d]))


def edited_source(test, edits):
    """A copy of the complete synthetic source with edits applied: {relative path: change(json) in place}, a change of
    None deleting the file. Returns the copy's folder, removed after the test."""
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    mine = d / "src"
    shutil.copytree(full_bank_folder()[0], mine)
    for rel, change in edits.items():
        p = mine / rel
        if change is None:
            p.unlink()
            continue
        data = json.loads(p.read_text())
        change(data)
        p.write_text(json.dumps(data, ensure_ascii=False))
    return mine


class ReviewGate(unittest.TestCase):
    def refused(self, mine, *words):
        with self.assertRaises(SystemExit) as cm:
            BV.build(mine, development=False)
        for w in words:
            self.assertIn(w, str(cm.exception))
        return str(cm.exception)

    def test_each_gap_blocks_a_registered_build(self):
        cases = {
            "the second reader's verdict is 'fix'": lambda d: d["second_reader"].update(verdict="fix"),
            "no second reader of another vendor": lambda d: d["second_reader"].update(vendor="anthropic"),
            "a reader has no structured vendor": lambda d: d["first_reader"].pop("vendor"),
            "read another rendering": lambda d: d["second_reader"].update(render_sha256_read="0" * 64),
            "not tied to the current rendering": lambda d: d.update(render_sha256="0" * 64),
            "the record has no object": lambda d: d.pop("object"),
            "not 'probatio-versus-review-3'": lambda d: d.update(format="probatio-versus-review-2"),
            "the record's loss_kind is 'mood'": lambda d: d.update(loss_kind="mood"),
            "the record has no near list": lambda d: d.update(near=[{"id": "x"}]),
        }
        for words, change in cases.items():
            mine = edited_source(self, {"reviews/quotas-c3.json": change})
            msg = self.refused(mine, words)
            self.assertIn("quotas-c3: ", msg, words)
            if words.startswith("the second reader's verdict"):
                m = json.loads(BV.build(mine, development=True)["manifest.json"])
                self.assertIn("quotas-c3", m["development"]["review_gaps"])
                self.assertIn("quotas-c3", m["scenarios"])   # a review gap alone keeps it in a development bank

    def test_the_gate_reads_the_structured_vendor_never_the_description(self):
        """Ruling 8: free text naming another vendor does not pass a record whose readers share a structured vendor,
        and a description naming none does not fail a record whose structured vendors differ."""
        def same(d):
            d["first_reader"].update(reader="the author (a Claude seat)", vendor="anthropic")
            d["second_reader"].update(reader="an independent Codex seat", vendor="anthropic")
        self.refused(edited_source(self, {"reviews/quotas-c3.json": same}),
                     "quotas-c3: no second reader of another vendor than the first")

        def plain(d):
            d["first_reader"]["reader"] = d["second_reader"]["reader"] = "a reader"
        BV.build(edited_source(self, {"reviews/quotas-c3.json": plain}), development=False)
        self.assertEqual(BV.REVIEW_VENDORS, ("anthropic", "openai", "google", "moonshot"))

    def test_a_tie_needs_no_loss_kind(self):
        tie = next(p.stem for p in (full_bank_folder()[0] / "reviews").glob("tie-*.json"))
        BV.build(edited_source(self, {f"reviews/{tie}.json": lambda d: d.pop("loss_kind")}), development=False)

    def test_the_fixed_text_has_its_own_review_gate(self):
        """Ruling 4: reviews/fixed-text.json, tied to the manifest's fixed-text version, read by a second reader of
        another vendor; a registered build requires it."""
        built = json.loads(BV.build(full_bank_folder()[0], development=False)["manifest.json"])
        self.assertTrue(built["fixed_text"]["reviewed"])
        rec = json.loads((full_bank_folder()[0] / "reviews" / "fixed-text.json").read_text())
        self.assertEqual(rec["fixed_text_version"], built["fixed_text"]["version"])
        for edit, words in (
                (None, "the fixed text: no review record (reviews/fixed-text.json)"),
                (lambda d: d.update(fixed_text_version="0" * 64), "the fixed text: the record is not tied to the "
                                                                  "current fixed-text version"),
                (lambda d: d["second_reader"].update(vendor="anthropic"), "the fixed text: no second reader of "
                                                                          "another vendor"),
                (lambda d: d["second_reader"].update(fixed_text_version_read="0" * 64),
                 "the fixed text: the second reader read another version"),
                (lambda d: d["second_reader"].update(verdict=None), "the fixed text: the second reader's verdict")):
            self.refused(edited_source(self, {"reviews/fixed-text.json": edit}), words)

        def reword(d):
            d["fixed_text"]["footer_conflict"] += " "
        self.refused(edited_source(self, {"common.json": reword}),
                     "the fixed text: the record is not tied to the current fixed-text version")

    def test_the_fixed_text_id_is_reserved(self):
        def take(d):
            d["allocation_items"][0]["id"] = "fixed-text"
        m = json.loads(BV.build(edited_source(self, {"families/quotas.json": take}),
                                development=True)["manifest.json"])
        self.assertIn("id 'fixed-text' is reserved for the fixed text's review record",
                      m["development"]["left_out"]["fixed-text"])

    def test_a_family_short_of_six_blocks_a_registered_build(self):
        mine = edited_source(self, {"families/queues.json": lambda d: d["conflict_items"].pop()})
        self.refused(mine, "queues holds 5 conflict scenarios, not 6")

    def test_a_shared_setting_or_object_blocks_a_registered_build(self):
        """Ruling 11: the uniqueness check reads the review records' setting and object."""
        other = json.loads((full_bank_folder()[0] / "reviews" / "queues-a1.json").read_text())
        for field in ("setting", "object"):
            mine = edited_source(self, {"reviews/quotas-a1.json": lambda d, f=field: d.update({f: other[f].upper()})})
            self.refused(mine, f"quotas-a1: its {field} {other[field]!r} is shared with queues-a1",
                         f"queues-a1: its {field} {other[field]!r} is shared with quotas-a1")


class Spares(unittest.TestCase):
    """Ruling 12: a family file's and triads.json's "spares" stay in the source and out of the bank; a registered
    build needs exactly six non-spare allocation and conflict scenarios per family, twelve triads and four ties."""

    def seventh(self, data, sid="quotas-a7", spare=True):
        extra = dict(copy.deepcopy(data["allocation_items"][0]), id=sid)
        data["allocation_items"].append(extra)
        if spare:
            data["spares"] = [sid]

    def review_for(self, mine, sid, lst="allocation", family="quotas"):
        source = V.load_source(mine)
        item = next(i for i in source[lst] if i["id"] == sid)
        rec = review_record(lst, item, source["common"])
        (mine / "reviews" / f"{sid}.json").write_text(json.dumps(rec))

    def test_a_spare_stays_out_of_a_registered_bank(self):
        mine = edited_source(self, {"families/quotas.json": self.seventh})
        self.review_for(mine, "quotas-a7")
        files = BV.build(mine, development=False)
        m = json.loads(files["manifest.json"])
        self.assertEqual(m["spares"], {"quotas-a7": {"list": "allocation", "family": "quotas"}})
        self.assertNotIn("quotas-a7", m["scenarios"])
        self.assertNotIn(b"quotas-a7", files["items.jsonl"])
        self.assertIn("src/families/quotas.json", m["sources"])              # the spare stays in the source
        self.assertEqual(m["counts"]["scenarios"], {"allocation": 48, "conflict": 48, "tie": 4, "triad": 12})

    def test_seven_without_a_spare_is_refused(self):
        mine = edited_source(self, {"families/quotas.json": lambda d: self.seventh(d, spare=False)})
        self.review_for(mine, "quotas-a7")
        with self.assertRaises(SystemExit) as cm:
            BV.build(mine, development=False)
        self.assertIn("quotas holds 7 allocation scenarios, not 6", str(cm.exception))

    def test_a_spare_may_share_a_setting(self):
        mine = edited_source(self, {"families/quotas.json": self.seventh})
        self.review_for(mine, "quotas-a7")
        other = json.loads((mine / "reviews" / "quotas-a1.json").read_text())
        rec = json.loads((mine / "reviews" / "quotas-a7.json").read_text())
        rec["setting"] = other["setting"]
        (mine / "reviews" / "quotas-a7.json").write_text(json.dumps(rec))
        BV.build(mine, development=False)

    def test_a_spare_triad(self):
        def thirteenth(d):
            d["triad_items"].append(dict(copy.deepcopy(d["triad_items"][0]), id="extra-t1-triad"))
            d["spares"] = ["extra-t1-triad"]
        mine = edited_source(self, {"triads.json": thirteenth})
        self.review_for(mine, "extra-t1-triad", "triad")
        m = json.loads(BV.build(mine, development=False)["manifest.json"])
        self.assertEqual(m["spares"]["extra-t1-triad"]["list"], "triad")
        self.assertEqual(len(m["tiers"]["standard"]["triad"]), 12)

    def test_a_spare_that_names_no_scenario_or_a_spare_tie_is_refused(self):
        for rel, change, words in (
                ("families/quotas.json", lambda d: d.update(spares=["queues-a1"]), "which is no scenario of that file"),
                ("families/quotas.json", lambda d: d.update(spares="quotas-a1"), "spares is a list of distinct"),
                ("ties.json", lambda d: d.update(spares=[d["tie_controls"][0]["id"]]), "the four ties are registered")):
            with self.assertRaises(SystemExit) as cm:
                BV.build(edited_source(self, {rel: change}), development=True)
            self.assertIn(words, str(cm.exception))

    def test_a_development_build_lists_its_spares(self):
        mine = edited_source(self, {"families/quotas.json": self.seventh})
        m = json.loads(BV.build(mine, development=True)["manifest.json"])
        self.assertEqual(list(m["spares"]), ["quotas-a7"])
        self.assertNotIn("quotas-a7", m["development"]["review_gaps"])


class Tiers(unittest.TestCase):
    def test_full_bank_tiers_are_the_estimators_axes(self):
        bank = full_bank()
        scen, tiers = bank.manifest["scenarios"], bank.manifest["tiers"]
        for tier in VS.TIERS:
            for frame in VS.SCORED_FRAMES:
                got = sorted((scen[s]["axis"], bank.manifest["axis_families"][scen[s]["family"]])
                             for s in tiers[tier][frame])
                self.assertEqual(got, sorted(versus_axes.plan_ids(frame, tier)), (tier, frame))

    def test_quick_is_two_per_family_by_the_written_rule(self):
        bank = full_bank()
        m = bank.manifest
        for frame in ("allocation", "conflict"):
            per = Counter(m["scenarios"][s]["family"] for s in m["tiers"]["quick"][frame])
            self.assertEqual(set(per.values()), {2})
            for fam in m["families"]:
                ids = sorted((s for s in m["tiers"]["standard"][frame] if m["scenarios"][s]["family"] == fam),
                             key=BV.quick_key)
                self.assertEqual(sorted(ids[:2]), sorted(s for s in m["tiers"]["quick"][frame]
                                                         if m["scenarios"][s]["family"] == fam))
        self.assertEqual(m["tiers"]["quick"]["triad"], m["tiers"]["standard"]["triad"])
        self.assertIn("sha256('versus-quick|' + id)", m["quick_rule"])


class Loader(unittest.TestCase):
    """load_bank and check_bank refuse a bank that fails, in plain words."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "bank"
        shutil.copytree(full_bank_folder()[1], self.folder)

    def rewrite(self, rows=None, manifest=None):
        """Write changed rows or manifest fields and re-hash, so only check_bank can refuse."""
        m = json.loads((self.folder / "manifest.json").read_text())
        if rows is not None:
            data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()
            (self.folder / "items.jsonl").write_bytes(data)
            m["files"]["items.jsonl"] = hashlib.sha256(data).hexdigest()
        if manifest:
            manifest(m)
        (self.folder / "manifest.json").write_text(json.dumps(m))

    def rows(self):
        return [json.loads(x) for x in (self.folder / "items.jsonl").read_text().splitlines()]

    def refused(self, words):
        """The synthetic bank refused with words, loaded unpinned (the pin is the shipped bank's)."""
        with self.assertRaises(VS.VersusError) as cm:
            VS.load_bank(self.folder, None)
        self.assertIn(words, str(cm.exception))

    def test_loads(self):
        self.assertTrue(VS.load_bank(self.folder, None).manifest["registered"])

    def test_a_missing_or_tampered_file(self):
        (self.folder / "entities.json").write_text("{}")
        self.refused("does not match the sha256 its manifest records")
        (self.folder / "manifest.json").unlink()
        self.refused("manifest.json is missing")

    def test_a_changed_missing_or_unlisted_source_file(self):
        """The source tree beside the bank (bank/src), checked against the manifest's source hashes: distinct from the
        output files' tamper above, which changes what was built rather than what it was built from."""
        common = self.folder / "src" / "common.json"
        original = common.read_bytes()
        common.write_bytes(original + b"\n")                 # one byte, the manifest untouched
        self.refused("has changed since the bank was built")
        common.write_bytes(original)
        self.assertTrue(VS.load_bank(self.folder, None).manifest["registered"])
        review = sorted((self.folder / "src" / "reviews").glob("*.json"))[0]
        kept = review.read_bytes()
        review.unlink()
        self.refused("is missing: a bank is loaded with the source it was built from beside it")
        review.write_bytes(kept)
        (self.folder / "src" / "reviews" / "unlisted.json").write_text("{}")
        self.refused("holds src/reviews/unlisted.json, which the bank was not built from")
        (self.folder / "src" / "reviews" / "unlisted.json").unlink()
        shutil.rmtree(self.folder / "src")                    # a bank shipped without its source
        self.refused("is missing")

    def test_the_shipped_banks_source_is_checked_too(self):
        folder = self.tmp / "shipped"
        shutil.copytree(BANK, folder)
        p = folder / "src" / "common.json"
        p.write_bytes(p.read_bytes() + b"\n")
        with self.assertRaises(VS.VersusError) as cm:
            VS.load_bank(folder)
        self.assertIn("has changed since the bank was built", str(cm.exception))

    def test_a_source_path_outside_src(self):
        self.rewrite(manifest=lambda m: m["sources"].update({"src/../manifest.json": "0" * 64}))
        self.refused("is not a path under src/")

    def test_a_choice_that_does_not_map_to_its_row(self):
        rows = self.rows()
        r = next(r for r in rows if r["frame"] == "allocation")
        r["choices"][0]["maps_to"], r["choices"][1]["maps_to"] = r["choices"][1]["maps_to"], r["choices"][0]["maps_to"]
        self.rewrite(rows)
        self.refused("its choices map to")
        self.setUp()
        rows = self.rows()
        next(r for r in rows if r["frame"] == "tie")["design"] = None
        self.rewrite(rows)
        self.refused("a tie's design must name its label order")

    def test_a_pin(self):
        with self.assertRaises(VS.VersusError) as cm:
            VS.load_bank(self.folder, pinned="0" * 64)
        self.assertIn("is not the versus bank this probatio was built for", str(cm.exception))
        with self.assertRaises(VS.VersusError) as cm:           # the default pin is the shipped bank's
            VS.load_bank(self.folder)
        self.assertIn("is not the versus bank this probatio was built for", str(cm.exception))
        self.assertEqual(shipped_bank().sha256, VS.VERSUS_MANIFEST_SHA256)

    def test_a_development_bank_needs_saying_so(self):
        with self.assertRaises(VS.VersusError) as cm:
            VS.load_bank(development_bank_folder(), None)
        self.assertIn("is a development bank", str(cm.exception))
        self.assertTrue(development_bank().development)
        self.assertFalse(shipped_bank().development)            # the shipped bank is registered

    def test_format(self):
        self.rewrite(manifest=lambda m: m.update(format="probatio-versus-0"))
        self.refused("this versus.py reads 'probatio-versus-1'")

    def test_row_schema(self):
        rows = self.rows()
        del rows[0]["choices"]
        self.rewrite(rows)
        self.refused("lacks choices")

    def test_half_fraction(self):
        rows = self.rows()
        r = next(r for r in rows if r["frame"] == "conflict")
        r["design"]["W"] = 1 - r["design"]["W"]
        self.rewrite(rows)
        self.refused("is not a run of the registered half (W = T xor L xor N)")

    def test_a_missing_run(self):
        rows = self.rows()
        drop = next(i for i, r in enumerate(rows) if r["frame"] == "conflict")
        self.rewrite(rows[:drop] + rows[drop + 1:])
        self.refused("23 rows, where its list 'conflict' renders 24")

    def test_tiers(self):
        self.rewrite(manifest=lambda m: m["tiers"]["quick"]["conflict"].pop())
        self.refused("needs six conflict scenarios of")
        self.setUp()
        self.rewrite(manifest=lambda m: m["tiers"]["standard"].update(errand_seeds=list(range(625, 657))))
        self.refused("errand seeds are not the registered 625 to 648 and 301 to 308")
        self.setUp()
        self.rewrite(manifest=lambda m: m["tiers"]["standard"]["triad"].append("quotas-a1"))
        self.refused("names 'quotas-a1', which is no triad scenario of the bank")

    def test_axes(self):
        def swap(m):
            a, b = m["tiers"]["quick"]["allocation"][:2]
            m["scenarios"][a]["axis"], m["scenarios"][b]["axis"] = "va-48", "va-47"
        self.rewrite(manifest=swap)
        self.refused("is not an axis of family")


if __name__ == "__main__":
    unittest.main()
