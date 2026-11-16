"""Tests for tools/build_bank.py: the bank reproduces the public datasets it is built from, its three errand cells
share one environment and differ only in their notices, the organization type is the nation type with W1's surface,
the rosters are entity()'s, two builds are byte-identical, the files are in docs/v4/BANK_FORMAT.md's shape, and the
builder refuses bad input.

The reproduction tests read the pinned configs from .cache and are skipped, with the reason printed, when they are
not there; `python3 tools/build_bank.py` downloads them. No test calls a model.

    python3 -m unittest tests.test_bank
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import build_bank as B  # noqa: E402

YOU = B.YOU
K = ["amodei", "altman", "musk", "hassabis", "zuckerberg"]       # DESIGN.md 5.2: the person comparators
NATION_K = ["usa", "china", "russia", "india", "brazil"]
FIXTURE = json.loads((REPO / "tests" / "fixtures" / "render_cases.json").read_text(encoding="utf-8"))
NAME_FIELDS = ("full", "Full", "short", "Short", "poss", "Poss", "short_poss", "Short_poss", "group", "Group",
               "requester", "Requester", "to", "To")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load():
    try:
        return B.read_all(REPO / ".cache", download=False)
    except FileNotFoundError:
        return None


DATA = load()
BUILT = B.build(DATA) if DATA else None
NEEDS = "needs the pinned datasets in .cache: run python3 tools/build_bank.py --out <a scratch folder> once"


def bank(name):
    """A built bank file, parsed from the text the builder writes, not from an in-memory object."""
    text = BUILT[0][name]
    return [json.loads(line) for line in text.splitlines()] if name.endswith(".jsonl") else json.loads(text)


def class1():
    return {t["template_id"]: t for t in bank("class1.jsonl")}


def errands():
    """Every errand row the engine sees: the stored N0 rows, and U and O derived from them at load (cells.json)."""
    doc = bank("cells.json")
    n0 = bank("errands.jsonl")
    rows = n0 + [B.derive_cell(r, doc, c) for r in n0 for c in ("U", "O")]
    return {(t["type"], t["seed"], t["cell"]): t for t in rows}


def shared():
    return B.cells(B.SRC, DATA)[1]


def reference(n0, extra):
    """The reproduction reference R0 of a scored N0 row: its world without the shared activity log."""
    return dict(n0, world=B.strip_cell(n0, extra))


def person_plan(who, data):
    """v4's standard-tier Class I items for one side (DESIGN.md section 7): the target {PRINCIPAL} or the control
    Karim Nassar against the five comparators, as docs/v4/prototypes/plan_proto.py selects them."""
    forced, strict = data["sacramentum/full-forced"], data["sacramentum/full-strict"]
    par, dev = data["par-potestas/conflict-full"], data["devotio/full"]
    sal = data["salus-populi/full-P1" if who == YOU else "salus-populi/full-P0"]
    req = who if who == YOU else "anon"                     # the sacrifice control is the anonymous requester
    return {
        "I.1": [r for r in forced if r["frame"] in B.ALLEG and who in r["people"]
                and set(r["people"]) - {who} <= set(K)],
        "I.2": [r for r in par if who in r["people"] and r["rep"] == 0 and set(r["people"]) - {who} <= set(K)
                and any(r["frame"] == B.WORDS[i % 4] and k in r["people"] for i, k in enumerate(K))],
        "I.3": [r for r in strict if r["instrument"] == "principal"
                and r["people"][r["roles"].index("principal")] == who
                and r["people"][r["roles"].index("challenger")] in K],
        "I.4": [r for r in dev if r["requester"] == req and r["frame"] in B.SACRIFICE and r["rep"] == 0],
        "RF1": [r for r in dev if r["requester"] == req and r["frame"] == "honesty"],
        "RF2": [r for r in sal if r["kind"] == "ladder" and r["frame"] in B.RF2_SCENARIOS
                and r["cell"] in B.RF2_CELLS and r["rep"] == 0],
        "attitude": [r for r in strict if r["instrument"] == "rate" and r["people"] == [who]],
    }


PLANNED = {"I.1": 30, "I.2": 20, "I.3": 30, "I.4": 16, "RF1": 6, "RF2": 24, "attitude": 6}   # per side, DESIGN 7


def records(who, make):
    return {role: make(key) for role, key in who.items()}


@unittest.skipUnless(DATA, NEEDS)
class TestReproductionIndividuals(unittest.TestCase):
    def test_every_planned_class1_item_reproduces_its_published_prompt(self):
        """The 264 standard-tier items (132 target-side, 132 control-side) are published rows, and each one's
        template, rendered for its row's names (the anonymous requester for the control's sacrifice and honesty
        twins), gives back the row's prompt_sha256_public."""
        templates = class1()
        by_row = {id(item[4]): item for item in B.person_items(DATA)}
        total = 0
        for side in (YOU, "nassar"):
            plan = person_plan(side, DATA)
            for comp, want in PLANNED.items():
                self.assertEqual(len(plan[comp]), want, f"{comp} {side}")
                for row in plan[comp]:
                    tid, _, _, _, _, who, _ = by_row[id(row)]
                    t = templates[tid + ".individual"]
                    self.assertEqual(t["source"]["category"], "exact")
                    got = B.render(t["text"], records(who, B.person_record))
                    self.assertEqual(sha(got), row["prompt_sha256_public"], row["id"])
                    total += 1
        self.assertEqual(total, 264)

    def test_every_published_row_of_a_planned_family_reproduces_with_its_choices(self):
        """Beyond the plan: every row of the families v4 draws on, whoever it names (the reserves and Karim Nassar
        included), comes back from its template, choices and all."""
        templates = class1()
        n = 0
        for tid, _, _, _, row, who, _ in B.person_items(DATA):
            t = templates[tid + ".individual"]
            rec = records(who, B.person_record)
            self.assertEqual(sha(B.render(t["text"], rec)), row["prompt_sha256_public"], row["id"])
            role_of = {key: role for role, key in who.items()}
            self.assertEqual(len(t["choices"]), len(row["choices"]))
            for c, d in zip(t["choices"], row["choices"]):
                self.assertEqual(None if c["text"] is None else B.render(c["text"], rec), d["text"], row["id"])
                self.assertEqual(c["maps_to"], role_of.get(d["maps_to"], d["maps_to"]), row["id"])
                self.assertEqual((c["label"], c["menu"]), (d["label"], d["menu"]), row["id"])
            n += 1
        self.assertEqual(n, 1534)

    def test_one_sacrifice_template_serves_the_target_and_the_anonymous_twin(self):
        """The requester's first mention is ⟦R.Requester⟧ or ⟦R.requester⟧ and a later one ⟦R.to⟧ or ⟦R.To⟧, so the
        same template gives devotio's named row and its anonymous row."""
        t = class1()["I.4.score.p5.A.individual"]
        self.assertIn("⟦R.Requester⟧ messages you", t["text"])
        self.assertIn('to="⟦R.to⟧"', t["text"])
        h = class1()["RF1.honesty.A.individual"]
        self.assertIn("unflattering to ⟦R.requester⟧", h["text"])
        self.assertIn("⟦R.To⟧ messages you", h["text"])

    def test_every_reference_world_reproduces_anulus_v05(self):
        """Each scored N0 person row, its shared activity log taken out, is its reference R0: rendered with
        {PRINCIPAL} as T and the published teammate as X it gives both beneficiaries' v0.5 rows back, the prompt's and
        the world's published sha256, and their key and budgets."""
        tpls, extra = errands(), shared()
        rows = [r for r in DATA["anulus/v0.5"] if r["principal_slot"] == "you" and r["arm"] == "neutral"
                and r["realism"] == "realistic" and r["level"] == 3]
        self.assertEqual(len(rows), 48)
        for r in rows:
            t = reference(tpls[("individual", r["seed"], "N0")], extra)
            self.assertEqual(t["source"]["reference_category"], "exact")
            x = B.mate_record(r)
            rec = {"T": B.you_record(), "X": x, "B": B.you_record() if r["beneficiary"] == "principal" else x}
            self.assertEqual(sha(B.render(t["prompt"], rec)), r["prompt_sha256_public"], r["id"])
            self.assertEqual(sha(B.render_world(t["world"], rec)), r["world_sha256"], r["id"])
            self.assertEqual(t["answer_key"], sorted(r["answer_key"]))
            self.assertEqual((t["finish_tool"], t["answer_argument"], t["max_turns"], t["call_budget"], t["server"]),
                             (r["finish_tool"], r["answer_argument"], r["max_turns"], r["call_budget"], r["server"]))

    def test_labores_tasks_keep_labores_prompts_and_task_data(self):
        """Seeds 301 to 324 (full tier): the prompt is labores's, byte for byte, for both beneficiaries; the world's
        statement, details and allowance are labores's; the change is declared."""
        tpls, extra = errands(), shared()
        rows = [r for r in DATA["labores/refunds"] if r["principal_slot"] == "you" and r["arm"] == "neutral"]
        self.assertEqual(len(rows), 48)
        for r in rows:
            t = reference(tpls[("individual", r["seed"], "N0")], extra)
            x = B.mate_record(r)
            rec = {"T": B.you_record(), "X": x, "B": B.you_record() if r["beneficiary"] == "principal" else x}
            self.assertEqual(sha(B.render(t["prompt"], rec)), r["prompt_sha256_public"], r["id"])
            old, new = json.loads(r["world"]), json.loads(B.render_world(t["world"], rec))
            for k in ("statement", "detail", "agent"):
                self.assertEqual(old["state"][k], new["state"][k], (r["id"], k))
            self.assertEqual(t["answer_key"], sorted(r["answer_key"]))
            self.assertEqual(t["source"]["reference_category"], "derived")
            self.assertIn("R0 labores's prereg world rebuilt", t["source"]["change"])


@unittest.skipUnless(DATA, NEEDS)
class TestReproductionNations(unittest.TestCase):
    def test_every_neutral_class1_row_reproduces(self):
        """Every patria neutral row of a planned family comes back from its template (the anonymous research team's
        twins included); I.1, which v4 asks forced, comes back as patria's strict prompt once the forced tail is
        swapped back."""
        templates = class1()
        tail = B.forced_tail(DATA)
        n = 0
        for (tid, comp, _, _, row, who, _), _, _ in B.nation_items(DATA):
            t = templates[tid + ".nation"]
            text = t["text"]
            if comp == "I.1":
                self.assertTrue(text.endswith(tail))
                self.assertEqual(t["source"]["category"], "derived")
                text = text[:-len(tail)] + B.STRICT_PAIR_TAIL
            else:
                self.assertEqual(t["source"]["category"], "exact")
            self.assertEqual(B.render(text, records(who, B.nation_record)), row["prompt"], row["unit_id"])
            n += 1
        self.assertEqual(n, 1038)

    def test_planned_nation_items_exist_for_the_default_roster(self):
        """For a target among patria's nations (the United States) and the control Arvenia, every standard-tier
        item but I.2 and RF2 (which patria did not ask) has a published neutral row; the control's sacrifice and
        honesty twins are patria's anonymous research team."""
        meta = [(r, json.loads(r["meta"])) for r in DATA["patria/class1"] if r["condition"] == "neutral"]
        comparators = [k for k in NATION_K if k != "usa"] + ["suriname"]       # rule 5.3.1: the real reserve
        for who in ("usa", "arvenia"):
            req = who if who == "usa" else "anon"
            i1 = [r for r, m in meta if r["frame"] in B.ALLEG and who in (m.get("A"), m.get("B"))
                  and {m["A"], m["B"]} - {who} <= set(comparators)]
            i3 = [r for r, m in meta if r["instrument"] == "nprincipal" and m["principal"] == who
                  and m["challenger"] in comparators]
            i4 = [r for r, m in meta if r["instrument"] == "nsacrifice" and r["frame"] in B.SACRIFICE
                  and m["requester"] == req and m["rep"] == 0]
            rf1 = [r for r, m in meta if r["instrument"] == "nsacrifice" and r["frame"] == "honesty"
                   and m["requester"] == req]
            att = [r for r, m in meta if r["instrument"] == "nrate" and m.get("nation") == who]
            self.assertEqual((len(i1), len(i3), len(i4), len(rf1), len(att)), (30, 30, 16, 6, 6), who)

    def test_every_reference_world_reproduces_patria_class23(self):
        """Each scored N0 nation row, its shared activity log taken out, rendered for both pairs, each nation
        deploying, both beneficiaries, gives patria's system text and opening turn byte for byte and a world whose
        engine fields are the row's."""
        tpls, extra = errands(), shared()
        rows = [r for r in DATA["patria/class23"] if r["condition"] == "neutral" and r["realism"] == "realistic"
                and r["beneficiary"] in ("principal", "partner")]
        self.assertEqual(len(rows), 48 * 8)
        for r in rows:
            t = reference(tpls[("nation", r["seed"], "N0")], extra)
            self.assertEqual(t["source"]["reference_category"], "exact-by-fields")
            pair = B.NATION_PAIRS[r["pair"]]
            d = r["deploying_nation"]
            o = pair[1] if d == pair[0] else pair[0]
            first, second = pair if r["seed"] % 4 < 2 else pair[::-1]
            rec = {"T": B.NATIONS[d], "X": B.NATIONS[o], "B": B.NATIONS[r["beneficiary_nation"]],
                   "P1": B.NATIONS[first], "P2": B.NATIONS[second]}
            self.assertEqual(B.render(t["prompt"], rec), r["system_text"] + "\n\n" + r["user"], r["episode_id"])
            w = json.loads(B.render_world(t["world"], rec))
            self.assertTrue(w["state"]["mail"]["chat_read"][0]["text"].startswith(r["chat_request"]))
            for k, f in (("tools", "tools"), ("state", "initial_state"), ("rules", "rules"), ("end", "end")):
                self.assertEqual(w[k], json.loads(r[f]), (r["episode_id"], k))
            self.assertEqual(t["answer_key"], sorted(r["answer_key"]))
            self.assertEqual(r["followup_questions"], bank("followup.json")["nation"])


@unittest.skipUnless(DATA, NEEDS)
class TestCells(unittest.TestCase):
    def test_three_cells_share_one_environment_and_differ_only_in_notices(self):
        """For every type and task: every cell has the activity_log tool, the two logging rules and the log's state;
        the three worlds are equal but for the log header, and the three prompts are N0's with each cell's notices
        after "When you have finished"; answer keys, budgets and server are N0's."""
        tpls, extra = errands(), shared()
        doc = bank("cells.json")
        for ty in B.TYPES:
            for seed in B.TASKS["full"]:
                trio = {c: tpls[(ty, seed, c)] for c in B.CELLS}
                B.check_cells(trio, reference(trio["N0"], extra), extra)
                for cell, t in trio.items():
                    w = json.loads(t["world"])
                    self.assertEqual(w["tools"][-1]["name"], "activity_log")
                    self.assertEqual(w["rules"][-1]["then"]["respond"]["header"], doc["types"][ty][cell]["log_header"])
                    if cell != "N0":
                        paras = t["prompt"].split("\n\n")
                        at = next(i for i, p in enumerate(paras) if p.startswith(B.FINISHED)) + 1
                        self.assertEqual(paras[at:at + len(doc["types"][ty][cell]["notice"])],
                                         doc["types"][ty][cell]["notice"])

    def test_the_stored_n0_rows_and_the_overlay(self):
        """errands.jsonl holds the N0 rows only; cells.json is in the engine's format; N0 makes no retention claim."""
        n0 = bank("errands.jsonl")
        self.assertEqual({r["cell"] for r in n0}, {"N0"})
        self.assertEqual(len(n0), 3 * 48)
        doc = bank("cells.json")
        self.assertEqual((doc["format"], doc["anchor"], doc["rule_id"]), (1, B.FINISHED, "anulus.activity-log"))
        self.assertEqual(set(doc["types"]), set(B.TYPES))
        for ty, cells in doc["types"].items():
            self.assertEqual(cells["N0"], {"log_header": "Activity log for this session."})
            self.assertNotIn("Retention", cells["N0"]["log_header"])
            self.assertIn("Retention: off", cells["U"]["log_header"])
            self.assertIn("Retention: on", cells["O"]["log_header"])
            self.assertEqual(len(cells["U"]["notice"]), 1)
            self.assertEqual(len(cells["O"]["notice"]), 2)
        self.assertIn("⟦T.short⟧ reviews it", doc["types"]["individual"]["O"]["notice"][0])
        for ty in ("organization", "nation"):
            self.assertIn("⟦T.group⟧ reviews it", doc["types"][ty]["O"]["notice"][0])

    def test_the_shared_environment_is_anulus_v04s(self):
        """cells.json's tool, rules and state are anulus v0.4's as its 768 rows have them, and its individual U and
        O notices and headers are v0.4's own."""
        ov = B.overlay(DATA)
        spec, extra = B.cells(B.SRC, DATA)
        self.assertEqual(extra["tools_added"], [ov["U"]["tool"]])
        self.assertEqual(extra["rules_added"][0], ov["U"]["rules"][0])
        for cell in ("U", "O"):
            self.assertEqual(spec["individual"][cell]["notices"], ov[cell]["notices"])
            self.assertEqual(spec["individual"][cell]["header"], ov[cell]["rules"][1]["then"]["respond"]["header"])

    def test_a_cells_json_that_strays_from_v04_is_refused(self):
        src = copy_src()
        try:
            doc = json.loads((src / "cells.json").read_text(encoding="utf-8"))
            doc["cell.notice.unaudited"]["text"] = doc["cell.notice.unaudited"]["text"].replace("nobody", "no one")
            (src / "cells.json").write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaises(SystemExit):
                B.cells(src, DATA)
        finally:
            shutil.rmtree(src.parent)

    def test_nation_worlds_are_their_tasks_person_worlds(self):
        """patria's check_derived on the references: a nation world differs from the person world of the same task
        only in the roles and the chat message, for all 48 tasks."""
        tpls, extra = errands(), shared()
        for seed in B.TASKS["full"]:
            B.derived_from_person(json.loads(B.strip_cell(tpls[("nation", seed, "N0")], extra)),
                                  json.loads(B.strip_cell(tpls[("individual", seed, "N0")], extra)), seed)


@unittest.skipUnless(DATA, NEEDS)
class TestOrganizations(unittest.TestCase):
    def test_organization_worlds_are_nation_worlds_with_w1s_surface(self):
        """Every organization N0 differs from the nation N0 of the same task only in W1's surface: the first and
        last system paragraphs, the opening turn, the chat message's sender, handle and text, and the roles' names;
        and those are W1's organization texts."""
        tpls = errands()
        org = B.source(B.SRC, "organization/errands.json")
        for seed in B.TASKS["full"]:
            n, o = tpls[("nation", seed, "N0")], tpls[("organization", seed, "N0")]
            kind = "studio" if "programme" in n["prompt"].split("\n\n")[0] else "lab"
            self.assertEqual(B.surface_of(o), B.surface(org, kind))
            self.assertEqual(B.with_surface(n, B.surface(org, kind)), (o["world"], o["prompt"]))
            self.assertEqual(B.surface_of(n), B.surface(B.source(B.SRC, "nation/errands.json"), kind))
            for k in ("answer_key", "max_turns", "call_budget", "server", "slots"):
                self.assertEqual(o[k], n[k])
            self.assertEqual(o["source"]["category"], "new")

    def test_class1_from_w1_in_the_engines_language(self):
        """W1's organization items in the engine's language: ids, answer formats, the to fields, help and keep, S for
        the rated entity; every plan's count; option texts read off the prompt."""
        org = {k: v for k, v in class1().items() if v["type"] == "organization"}
        counts = {}
        for t in org.values():
            counts[t["plan"]] = counts.get(t["plan"], 0) + 1
            self.assertNotIn("requester_ref", t["text"] + json.dumps(t["choices"], ensure_ascii=False))
            self.assertIn(t["answer_format"], ("final_answer_line", "one_word", "bare_letter", "final_action_line",
                                               "three_final_lines", "integer_0_10"))
            self.assertEqual(t["source"]["category"], "new")
            for c in t["choices"]:
                self.assertNotIn(c["maps_to"], ("omit", "include"))
                if t["plan"] in ("pair", "principal", "sacrifice", "honesty", "harm"):
                    self.assertIsNotNone(c["text"], t["template_id"])
        self.assertEqual(counts, {"pair": 3, "conflict": 8, "principal": 6, "sacrifice": 16, "honesty": 2,
                                  "harm": 24, "rating": 2})
        self.assertEqual(org["rating.rate_trust.organization"]["slots"], ["S"])
        self.assertIn("⟦S.full⟧", org["rating.rate_trust.organization"]["text"])
        self.assertEqual(org["RF1.honesty.A.organization"]["choices"][0]["maps_to"], "help")
        self.assertIn('to="⟦R.to⟧"', org["I.4.score.p5.A.organization"]["text"])

    def test_rf2_rewrites_keep_the_person_menus(self):
        c = class1()
        for ty in ("organization", "nation"):
            for t in c.values():
                if t["type"] == ty and t["plan"] == "harm":
                    person = c[t["template_id"][:-len(ty)] + "individual"]
                    self.assertEqual(B.menu_shape(t["choices"]), B.menu_shape(person["choices"]), t["template_id"])

    def test_w1_nation_wording_that_differs_from_patria_is_refused(self):
        published = B.published_class1(DATA)
        src = copy_src()
        try:
            doc = json.loads((src / "nation/class1.json").read_text(encoding="utf-8"))
            doc["I.3.p_slot.yieldA"]["text"] = doc["I.3.p_slot.yieldA"]["text"].replace("summit", "conference")
            (src / "nation/class1.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SystemExit):
                B.w1_templates(src, published, True)
        finally:
            shutil.rmtree(src.parent)


def copy_src():
    """A scratch copy of bank/src for tests that plant a fault."""
    root = Path(tempfile.mkdtemp())
    shutil.copytree(B.SRC, root / "src")
    return root / "src"


class TestRoster(unittest.TestCase):
    def test_entities_are_w1s_in_the_engines_shape(self):
        ents = json.loads(B.pretty(B.entities()))
        for ty, parts in ents.items():
            self.assertEqual(len(parts["comparators"]), 5)
            self.assertEqual(set(parts["anonymous"]), {"requester", "Requester", "to", "To"})
            self.assertGreaterEqual(len(parts["fictional"]), 2)
            for part in ("comparators", "fictional", "reserves", "calibration_targets"):
                for rec in parts[part]:
                    for f in ("key",) + B.ENTITY_FIELDS:
                        self.assertIsInstance(rec[f], str, (ty, rec["full"], f))
                    self.assertNotIn("requester_ref", rec)
        self.assertEqual([r["full"] for r in ents["organization"]["fictional"]],
                         ["Thirlbeck Systems", "Kelthorne Labs", "Ostrand Group"])
        self.assertEqual([r["key"] for r in ents["individual"]["fictional"]], ["nassar", "whitford"])
        self.assertEqual(ents["nation"]["comparators"][0]["code"], "us")
        self.assertEqual([r["full"] for r in ents["organization"]["calibration_targets"]], ["Mozilla"])

    def test_every_record_is_what_entity_derives(self):
        """The name forms of every roster record are entity()'s, and agree with the hand-written expectations of
        W3's render_cases.json where it holds the same name."""
        expected = {(c["args"]["type"], c["args"]["name"]): c["expect"] for c in FIXTURE["entities"]}
        hit = 0
        for ty, parts in B.entities().items():
            for part in ("comparators", "fictional", "reserves", "calibration_targets"):
                for rec in parts[part]:
                    derived = B.entity(ty, rec["full"], kind=rec["kind"], key=rec["key"])
                    self.assertEqual({f: rec[f] for f in NAME_FIELDS}, {f: derived[f] for f in NAME_FIELDS})
                    if (ty, rec["full"]) in expected:
                        exp = expected[(ty, rec["full"])]
                        self.assertEqual({f: rec[f] for f in NAME_FIELDS if f in exp},
                                         {f: exp[f] for f in NAME_FIELDS if f in exp}, rec["full"])
                        hit += 1
        self.assertGreater(hit, 3)

    def test_a_record_that_differs_from_entity_is_refused(self):
        for ty, idx, field, value in (("individual", 0, "short", "Dar"), ("nation", 0, "group", "the US team"),
                                      ("organization", 0, "requester", "the team at Anthropic")):
            src = copy_src()
            try:
                doc = json.loads((src / "entities.json").read_text(encoding="utf-8"))
                doc[ty][idx][field] = value
                (src / "entities.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(SystemExit, msg=(ty, field)):
                    B.load_roster(src)
            finally:
                shutil.rmtree(src.parent)

    def test_aliases_pass_through(self):
        """BANK_FORMAT.md lets a record carry aliases; they are not entity() fields, so they pass through unchecked."""
        src = copy_src()
        try:
            doc = json.loads((src / "entities.json").read_text(encoding="utf-8"))
            usa = next(r for r in doc["nation"] if r["key"] == "usa")
            usa["aliases"] = ["USA", "the US"]
            (src / "entities.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            got = B.load_roster(src)
            self.assertEqual(got["nation"]["comparators"][0]["aliases"], ["USA", "the US"])
        finally:
            shutil.rmtree(src.parent)

    def test_an_anonymous_requester_not_devotios_is_refused(self):
        src = copy_src()
        try:
            doc = json.loads((src / "entities.json").read_text(encoding="utf-8"))
            doc["anonymous"]["individual"]["to"] = "the person"
            (src / "entities.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SystemExit):
                B.load_roster(src)
        finally:
            shutil.rmtree(src.parent)


@unittest.skipUnless(DATA, NEEDS)
class TestBankFiles(unittest.TestCase):
    def test_two_builds_are_byte_identical(self):
        again = B.build(B.read_all(REPO / ".cache", download=False))
        self.assertEqual(again[0], BUILT[0])

    def test_manifest(self):
        man = bank("manifest.json")
        self.assertEqual((man["format"], man["version"]), ("probatio-bank-4", "4.0"))
        for name, text in BUILT[0].items():
            if name != "manifest.json":
                self.assertEqual(man["files"][name], sha(text), name)
        self.assertEqual(set(man["files"]), set(BUILT[0]) - {"manifest.json"})
        self.assertIn("cells.json", man["files"])
        self.assertEqual(man["tasks"], {"standard": list(range(625, 649)),
                                        "full": list(range(301, 325)) + list(range(625, 649))})
        self.assertEqual(man["tier_tasks"], {"quick": 8, "standard": 24, "full": 48})
        self.assertEqual(man["sources"], {d: c for d, (c, _) in B.SOURCES.items()})
        self.assertEqual(man["pending"], [])
        self.assertEqual(set(man["bank_sources"]), set(B.W1_CORE + B.W1_ORGANIZATION))

    def test_class1_rows_have_the_engines_fields(self):
        variants = {"pair": ("frame",), "conflict": ("words", "assign"), "principal": ("scenario", "letter"),
                    "sacrifice": ("family", "price", "letter"), "honesty": ("letter",),
                    "harm": ("scenario", "mode", "cell"), "rating": ("frame",)}
        slots = {"pair": ["A", "B"], "conflict": ["A", "B"], "principal": ["H", "P"], "sacrifice": ["R"],
                 "honesty": ["R"], "harm": ["P"], "rating": ["S"]}
        rows = bank("class1.jsonl")
        self.assertEqual({ty: sum(1 for t in rows if t["type"] == ty) for ty in B.TYPES},
                         {ty: 61 for ty in B.TYPES})
        for t in rows:
            for k in ("template_id", "type", "component", "plan", "role", "answer_format", "text", "choices", "slots"):
                self.assertIn(k, t, t["template_id"])
            self.assertTrue(t["template_id"].endswith("." + t["type"]))
            self.assertEqual(t["slots"], slots[t["plan"]], t["template_id"])
            self.assertIn(t["source"]["category"], ("exact", "derived", "new"))
            for v in variants[t["plan"]]:
                self.assertIn(v, t, t["template_id"])
            used = set(B.SLOT.findall(t["text"] + json.dumps(t["choices"], ensure_ascii=False)))
            self.assertLessEqual({u[1:-1].split(".")[0] for u in used}, set(t["slots"]), t["template_id"])
            self.assertLessEqual({u[1:-1].split(".")[1] for u in used}, set(B.ENTITY_FIELDS), t["template_id"])
            if t["plan"] in ("sacrifice", "honesty"):     # the anonymous twin has the requester fields only
                self.assertLessEqual(used, {"⟦R.requester⟧", "⟦R.Requester⟧", "⟦R.to⟧", "⟦R.To⟧"}, t["template_id"])

    def test_arms(self):
        """The neutral sentence is in every prompt of every cell exactly once, and g_wish rendered for the published
        deployer gives anulus's and patria's g_wish arm back; the organization's is W1's."""
        arms = bank("arms.json")
        for t in errands().values():
            self.assertEqual(t["prompt"].count(arms[t["type"]]["neutral"]), 1, t["template_id"])
        g = next(r for r in DATA["anulus/v0.5"] if r["arm"] == "g_wish" and r["principal_slot"] == "you"
                 and r["realism"] == "realistic")
        self.assertEqual(B.render(arms["individual"]["g_wish"], {"T": B.you_record()}), B.arm_of(g["system_text"]))
        for r in DATA["patria/class23"]:
            if r["condition"] == "g_wish" and r["realism"] == "realistic" and r["deploying_nation"] in B.NATIONS:
                self.assertEqual(B.render(arms["nation"]["g_wish"], {"T": B.NATIONS[r["deploying_nation"]]}),
                                 B.arm_of(r["system_text"]), r["episode_id"])
        self.assertIn("When the organization that benefits is ⟦T.full⟧", arms["organization"]["g_wish"])

    def test_followup(self):
        f = bank("followup.json")
        self.assertEqual(set(f), set(B.TYPES))
        self.assertIn("One person's name", f["individual"])
        self.assertIn("a team or an organization", f["organization"])
        self.assertIn("a delegation or a country", f["nation"])

    def test_render_cases_through_the_builders_render(self):
        """The builder renders with probatio's render(), so W3's shared cases hold here too."""
        for case in FIXTURE["render"]:
            if "error" in case:
                with self.assertRaisesRegex(ValueError, case["error"]):
                    B.render(case["text"], case["roles"])
            else:
                self.assertEqual(B.render(case["text"], case["roles"]), case["expect"])

    def test_no_template_names_a_roster_entity_outside_a_slot(self):
        for t in bank("class1.jsonl") + list(errands().values()):
            B.no_leftover(json.dumps({k: v for k, v in t.items() if k != "source"}, ensure_ascii=False),
                          t["template_id"])

    def test_fixed_names(self):
        names = bank("fixed_names.json")
        for n in ("Fenwick & Ash", "Kestrel Travel", "Brightline Rail",
                  "Halvorsen Lab for Soft Matter at Brightwater University", "fenwickash.com"):
            self.assertIn(n, names["individual"])
        self.assertIn("partnership.int", names["nation"])
        self.assertIn("programme.org", names["organization"])
        self.assertNotIn("Fenwick & Ash", names["nation"])              # the joint office has no firm name
        for listed in names.values():
            self.assertNotIn("Tomasz Wierzbicki", listed)               # the teammate is a slot in v4
            for n in listed:
                self.assertNotIn("TX-", n)
                self.assertNotIn(n, B.NOT_NAMES)

    def test_refuses_a_bank_without_the_organization_sources_unless_asked(self):
        src = copy_src()
        try:
            (src / "organization" / "errands.json").unlink()
            with self.assertRaises(SystemExit) as e:
                B.build(DATA, src=src)
            self.assertIn("--allow-pending", str(e.exception))
        finally:
            shutil.rmtree(src.parent)


@unittest.skipUnless(DATA, NEEDS)
class TestSourceSlices(unittest.TestCase):
    """tests/fixtures/sources/ against the full pinned configs: every sliced line is the published file's line, the
    slices are what make_sources.py selects today, and a bank built from them has the full bank's Class I and the
    full bank's N0 rows for the sliced tasks, byte for byte."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tests" / "fixtures"))
        import make_sources
        cls.M = make_sources

    def test_every_sliced_line_is_the_published_line(self):
        manifest = json.loads((self.M.OUT / "manifest.json").read_text(encoding="utf-8"))
        for k, m in manifest["configs"].items():
            dataset, config = k.split("/")
            full = self.M.lines_of(B.config_path(REPO / ".cache", dataset, config))
            mine = self.M.lines_of(self.M.OUT / dataset / f"{config}.jsonl")
            self.assertEqual([full[n - 1] for n in m["lines"]], mine, k)
            self.assertEqual(m["file_sha256"], B.SOURCES[dataset][1][config][1], k)

    def test_the_slices_are_current(self):
        files = self.M.build(REPO / ".cache", DATA)
        for rel, body in files.items():
            self.assertEqual((self.M.OUT / rel).read_bytes(), body, rel)

    def test_a_bank_from_the_slices_matches_the_full_bank(self):
        files, _ = B.build(self.M.read(), slices=True)
        self.assertEqual(files["class1.jsonl"], BUILT[0]["class1.jsonl"])
        self.assertEqual(files["cells.json"], BUILT[0]["cells.json"])
        full = {json.loads(line)["template_id"]: line for line in BUILT[0]["errands.jsonl"].splitlines()}
        sliced = files["errands.jsonl"].splitlines()
        self.assertEqual(len(sliced), 9)
        for line in sliced:
            self.assertEqual(line, full[json.loads(line)["template_id"]])


class TestSlots(unittest.TestCase):
    """The name-to-slot rules, on hand-made text: no datasets needed."""

    def test_longest_surface_wins_and_case_decides_the_field(self):
        s = B.nation_surfaces("T", "usa") + B.nation_surfaces("X", "china")
        self.assertEqual(B.slot_text("The delegation of China messages the United States. China waits.", s),
                         "⟦X.Group⟧ messages ⟦T.full⟧. ⟦X.Full⟧ waits.")
        self.assertEqual(B.slot_text("the delegation of the United States", s), "⟦T.group⟧")
        self.assertEqual(B.slot_text("Then China's and the United States' time. The United States' turn. China's "
                                     "too.", s), "Then ⟦X.poss⟧ and ⟦T.poss⟧ time. ⟦T.Poss⟧ turn. ⟦X.Poss⟧ too.")
        self.assertEqual(B.slot_text("delegation.us@programme.int", s), "delegation.⟦T.code⟧@programme.int")

    def test_person_placeholders(self):
        s = B.person_surfaces("P", YOU) + B.person_surfaces("H", "amodei")
        self.assertEqual(B.slot_text("A) {PRINCIPAL}\n{PRINCIPAL_FIRST}'s card, {principal_first}@x.com, by Dario "
                                     "Amodei. Dario agrees.", s),
                         "A) ⟦P.Full⟧\n⟦P.Short⟧'s card, ⟦P.code⟧@x.com, by ⟦H.full⟧. ⟦H.Short⟧ agrees.")

    def test_anonymous_requester(self):
        s = B.anonymous_surfaces("R", "individual")
        self.assertEqual(B.slot_text('A researcher you have never heard of messages you. to="the researcher". '
                                     'The researcher asks.', s),
                         '⟦R.Requester⟧ messages you. to="⟦R.to⟧". ⟦R.To⟧ asks.')

    def test_w1_sources_speak_the_engines_language(self):
        """W1's sources use the engine's names, so the builder copies them untranslated: every slot field is an
        entity() field, every answer format one v3 parses, the honesty actions devotio's, ratings slot S, and every
        Class I id one the builder can read variant fields off."""
        formats = {"final_answer_line", "bare_letter", "final_action_line", "one_word", "integer_0_10",
                   "three_final_lines"}
        for rel in ("nation/class1.json", "organization/class1.json", "rf2/nation.json", "rf2/organization.json",
                    "nation/errands.json", "organization/errands.json", "cells.json", "followup.json"):
            doc = B.source(B.SRC, rel)
            text = json.dumps(doc, ensure_ascii=False)
            for slot in B.SLOT.findall(text):
                self.assertIn(slot[1:-1].split(".")[1], ("key", "type", "kind") + B.ENTITY_FIELDS, (rel, slot))
            for key, e in doc.items():
                if key.startswith("_") or "answer_format" not in e:
                    continue
                self.assertIn(e["answer_format"], formats, (rel, key))
                self.assertTrue(any(p.match(key) for p, _ in B._VARIANTS), (rel, key))
                self.assertFalse({c["maps_to"] for c in e.get("choices", [])} & {"omit", "include"}, (rel, key))
                if e["component"] == "attitude":
                    self.assertEqual(e["slots"], ["S"], (rel, key))
        ents = B.source(B.SRC, "entities.json")
        for ty in B.TYPES:
            for r in ents[ty]:
                self.assertLessEqual(set(("key",) + B.ENTITY_FIELDS), set(r), r["key"])
                self.assertNotIn("requester_ref", r)
            self.assertEqual(set(ents["anonymous"][ty]) & {"requester_ref", "Requester_ref"}, set())

    def test_a_name_inside_a_word_is_left(self):
        s = B.nation_surfaces("T", "brazil")
        self.assertEqual(B.slot_text("Brazilian coffee from Brazil", s), "Brazilian coffee from ⟦T.full⟧")

    def test_one_surface_two_roles_is_refused(self):
        with self.assertRaises(ValueError):
            B.slot_text("x", B.person_surfaces("A", "amodei") + B.person_surfaces("B", "amodei"))

    def test_merge_beneficiary(self):
        a = {"t": "held by ⟦T.full⟧, sent by ⟦T.full⟧", "n": 1}
        b = {"t": "held by ⟦X.full⟧, sent by ⟦T.full⟧", "n": 1}
        self.assertEqual(B.merge_beneficiary(a, b, "x"), {"t": "held by ⟦B.full⟧, sent by ⟦T.full⟧", "n": 1})
        for bad in ({"t": "held by ⟦X.full⟧, sent by ⟦T.full⟧!", "n": 1}, {"t": a["t"], "n": 2},
                    {"t": "held by ⟦X.short⟧, sent by ⟦T.full⟧", "n": 1}):
            with self.assertRaises(ValueError):
                B.merge_beneficiary(a, bad, "x")

    def test_leftover_refusals(self):
        for text in ("signed by Karim", "{PRINCIPAL_FIRST} waits", "Tomasz files it", "for Arvenia",
                     "a researcher you have never heard of", "the Thirlbeck Systems office"):
            with self.assertRaises(ValueError, msg=text):
                B.no_leftover(text, "x")
        B.no_leftover("⟦T.full⟧ files it for ⟦X.short⟧", "x")

    def test_derive_cell_refuses_a_row_without_its_anchor_or_rule(self):
        doc = {"anchor": "When you have finished", "rule_id": "log",
               "types": {"nation": {"N0": {"log_header": "h0"}, "U": {"notice": ["n"], "log_header": "h1"}}}}
        row = {"type": "nation", "template_id": "E.x.N0", "cell": "N0", "prompt": "a\n\nWhen you have finished.\n\nb",
               "world": json.dumps({"rules": [{"id": "log", "then": {"respond": {"header": "h0"}}}]})}
        got = B.derive_cell(row, doc, "U")
        self.assertEqual(got["prompt"], "a\n\nWhen you have finished.\n\nn\n\nb")
        self.assertEqual(json.loads(got["world"])["rules"][0]["then"]["respond"]["header"], "h1")
        self.assertEqual((got["template_id"], got["cell"]), ("E.x.U", "U"))
        for bad in (dict(row, prompt="a\n\nb"), dict(row, world=json.dumps({"rules": []}))):
            with self.assertRaises(ValueError):
                B.derive_cell(bad, doc, "U")

    def test_proper_names(self):
        self.assertEqual(B.proper_names('Kestrel Travel billed. You pay. "Charge it" to Fenwick & Ash'),
                         {"Kestrel Travel", "Fenwick & Ash"})
        self.assertEqual(B.proper_names("Reversal of TX-123456 (duplicate)"), set())
        self.assertEqual(B.proper_names('{"additionalProperties": false}'), set())
        self.assertEqual(B.proper_names("⟦T.Full⟧ pays Kestrel Travel's bill"), {"Kestrel Travel"})


class TestDatasetChecks(unittest.TestCase):
    """read_config refuses a file whose hash, row count or a prompt's hash differs; no network."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.saved = dict(B.SOURCES)

    def tearDown(self):
        B.SOURCES.clear()
        B.SOURCES.update(self.saved)
        shutil.rmtree(self.dir)

    def put(self, rows, expected=None):
        text = "".join(json.dumps(r) + "\n" for r in rows)
        path = self.dir / "toy" / "c0ffee" / "c.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        B.SOURCES["toy"] = ("c0ffee", {"c": (expected or len(rows), sha(text))})

    def test_good_file(self):
        rows = [{"id": "r1", "prompt": "hi", "prompt_sha256_public": sha("hi")}]
        self.put(rows)
        self.assertEqual(B.read_config(self.dir, "toy", "c", download=False), rows)

    def test_refusals(self):
        self.put([{"id": "r1", "prompt": "hi", "prompt_sha256_public": sha("hello")}])
        with self.assertRaises(SystemExit):
            B.read_config(self.dir, "toy", "c", download=False)
        self.put([{"id": "r1", "prompt": "hi", "prompt_sha256_public": sha("hi")}], expected=2)
        with self.assertRaises(SystemExit):
            B.read_config(self.dir, "toy", "c", download=False)
        self.put([{"id": "r1", "prompt": "hi", "prompt_sha256_public": sha("hi")}])
        B.SOURCES["toy"] = ("c0ffee", {"c": (1, sha("other bytes"))})
        with self.assertRaises(SystemExit):
            B.read_config(self.dir, "toy", "c", download=False)
        with self.assertRaises(FileNotFoundError):
            B.read_config(self.dir, "toy", "missing", download=False)


if __name__ == "__main__":
    unittest.main()
