"""ordo's bank (bank/ordo/, docs/ordo/BANK_FORMAT.md): the builder is deterministic and matches what is on disk; the
rows are the counts the design and docs/ordo/budget.json assume; every slot is known and every row renders; F2.b is the
registered half fraction; every world is collegium's of its seed outside the declared fields; the cells, report and
entities are derived as declared; and the v4 bank the published trio pins is untouched. No .cache, no network, no
model, except the one test that rebuilds the v4 bank, which needs .cache and skips without it.

    python3 -m unittest tests.test_ordo_bank
"""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from collections import Counter
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import probatio as P  # noqa: E402
import ordo as O  # noqa: E402
import build_ordo_bank as OB  # noqa: E402

FILES = OB.build()
CLASS1 = [json.loads(line) for line in FILES["class1.jsonl"].decode("utf-8").splitlines()]
ERRANDS = [json.loads(line) for line in FILES["errands.jsonl"].decode("utf-8").splitlines()]
ENTITIES = json.loads(FILES["entities.json"])
CELLS = json.loads(FILES["cells.json"])
MANIFEST = json.loads(FILES["manifest.json"])
V4 = OB.load_v4()
V4_MANIFEST_SHA256 = "df93099ac0d59ffc1c67d0d57cfedb2f5e07a50d5b652ef120211c9d6f1d3514"
V4_BASE = "86e918cf10b28726677d894e9da285f494fa8e86"
FROZEN = ("bank/manifest.json", "tools/build_bank.py", "bank/src", "world.py", "calibration/ladder")

spec = importlib.util.spec_from_file_location("ordo_budget", REPO / "docs" / "ordo" / "budget.py")
BUDGET = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BUDGET)
BUDGET_JSON = json.loads((REPO / "docs" / "ordo" / "budget.json").read_text(encoding="utf-8"))


# the tier rules of SCORING.md 12, written out independently of docs/ordo/budget.py, whose enumeration they check.
# smoke is the plan called quick until 2026-11-08 (PREREG Amendment 5); quick is standard's role-free rules and checks,
# with no authority frame and no Class III, on 32 role-free tasks (docs/ordo/QUICK.md)
TIER = {"smoke": dict(f1_pairings=1, f2a_pairings=1, f2b_pairings=1, reps=1, tri_sets=1, v12=1, rf1_reps=1,
                      rf2_modes=1, rf2_cells=1, rf4_sets=1, authority=True, iii=True),
        "quick": dict(f1_pairings=4, f2a_pairings=4, f2b_pairings=2, reps=1, tri_sets=2, v12=2, rf1_reps=3,
                      rf2_modes=2, rf2_cells=1, rf4_sets=2, authority=False, iii=False),
        "standard": dict(f1_pairings=4, f2a_pairings=4, f2b_pairings=2, reps=1, tri_sets=2, v12=2, rf1_reps=3,
                         rf2_modes=2, rf2_cells=1, rf4_sets=2, authority=True, iii=True),
        "full": dict(f1_pairings=4, f2a_pairings=4, f2b_pairings=2, reps=2, tri_sets=2, v12=4, rf1_reps=6,
                     rf2_modes=2, rf2_cells=2, rf4_sets=2, authority=True, iii=True)}
TASKS = {"smoke": (4, 2, 2), "quick": (32, 0, 0), "standard": (16, 8, 8),       # role-free, observed, authority
         "full": (48, 24, 16)}


def rows(component=None, **match):
    return [r for r in CLASS1 if (component is None or r["component"] == component)
            and all(r.get(k) == v for k, v in match.items())]


def recs(name_set):
    """{type: the invented entity record} of one name set."""
    return {t: next(r for r in ENTITIES["rosters"][t]["fictional"] if r["key"] == ENTITIES["name_sets"][name_set][t])
            for t in OB.TYPES}


def type_roles(records):
    return {OB.TYPE_ROLE[t]: r for t, r in records.items()}


NAMED = {"individual": P.entity("individual", "Ada Lovelace"), "organization": P.entity("organization", "Mozilla"),
         "nation": P.entity("nation", "France")}


class TestDeterminism(unittest.TestCase):
    def test_two_builds_are_byte_identical(self):
        self.assertEqual(OB.build(), FILES)

    def test_bank_ordo_on_disk_is_what_the_sources_build(self):
        for name, data in FILES.items():
            with self.subTest(name):
                self.assertEqual((OB.ORDO / name).read_bytes(), data, f"{name}: run tools/build_ordo_bank.py")

    def test_the_manifest_lists_every_file_and_source(self):
        self.assertEqual(set(MANIFEST["files"]), set(OB.OUTPUTS))
        for name, sha in MANIFEST["files"].items():
            self.assertEqual(hashlib.sha256(FILES[name]).hexdigest(), sha, name)
        self.assertEqual(set(MANIFEST["sources"]), {f"src/{n}" for n in OB.SOURCES})
        self.assertEqual(MANIFEST["builder_sha256"], hashlib.sha256((REPO / "tools" / "build_ordo_bank.py")
                                                                    .read_bytes()).hexdigest())
        self.assertEqual(MANIFEST["v4_bank_manifest_sha256"], V4_MANIFEST_SHA256)

    def test_check_mode_passes(self):
        out = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "build_ordo_bank.py"), "--check"],
                             capture_output=True, text=True, cwd=REPO)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])


class TestCounts(unittest.TestCase):
    def test_rows_per_component_are_the_designs(self):
        want = {"F1.a": 18, "F1.b": 12, "F1.c": 12, "F1.tri": 6, "F2.a": 48, "F2.b": 96, "F2.tri": 6, "V12": 3,
                "RF1": 6, "RF2": 72, "RF4": 12}
        self.assertEqual(dict(Counter(r["component"] for r in CLASS1)), want)
        self.assertEqual(len([r for r in CLASS1 if not r["component"].startswith("RF")]), 201)
        self.assertEqual(len(ERRANDS), 48)
        self.assertEqual(MANIFEST["counts"]["class1_rows"], 201)
        self.assertEqual(MANIFEST["counts"]["red_flag_rows"], 90)

    def test_every_derived_cell_loads(self):
        derived = [P.derive_cell(r, CELLS, c) for r in ERRANDS for c in ("U", "O")]
        self.assertEqual(len(derived), 96)

    def bank_units(self, tier, pairs):
        """Question units and episodes a tier plans for a set of pairs, counted from the bank's rows with the tier's
        multipliers from docs/ordo/budget.py: the cardinalities come from the bank, not from budget.py."""
        T = TIER[tier]
        names = [OB.pair_name(p) for p in pairs]
        types = sorted({t for p in pairs for t in p}, key=OB.TYPES.index)
        k, m = len(types), len(pairs)
        q = 0
        for pair in names:
            per_order = lambda comp: len(rows(comp, pair=pair)) // 2           # noqa: E731  (rows per order)
            q += (per_order("F1.a") + per_order("F1.b") + per_order("F1.c")) * T["f1_pairings"] * 2 * T["reps"]
            if not T["authority"]:
                continue
            words = len({r["words"] for r in rows("F2.a", pair=pair)})
            q += len(rows("F2.a", pair=pair)) // words * T["f2a_pairings"] * T["reps"]
            plus = len([r for r in rows("F2.b", pair=pair) if r["design"]["half"] == "+1"])
            q += plus * T["f2b_pairings"] * T["reps"]
        if m == 3:
            q += (len(rows("F1.tri")) + (len(rows("F2.tri")) if T["authority"] else 0)) * T["tri_sets"] * T["reps"]
        q += sum(len(rows("V12", pair=t)) for t in types) * 2 * T["v12"]
        for t in types:
            q += len(rows("RF1", pair=t)) * T["rf1_reps"]
            cells = ("S4C4",) if T["rf2_cells"] == 1 else ("S2C2", "S4C4")
            modes = ("explicit",) if T["rf2_modes"] == 1 else ("explicit", "discretion")
            q += len([r for r in rows("RF2", pair=t) if r["cell"] in cells and r["mode"] in modes])
            q += len(rows("RF4", pair=t)) * T["rf4_sets"] * T["reps"]
        q += len(rows("RF1", pair=types[0])) * T["rf1_reps"]                # the anonymous twin, once
        rf, obs, au = TASKS[tier]
        ep = rf * k * (2 if T["iii"] else 1) + obs * k * T["iii"] + au * (k + 2 * m)
        return q, ep


    def test_tier_units_match_budget_json(self):
        triangle = list(OB.PAIRS)
        for tier in TIER:
            with self.subTest(tier):
                q, ep = self.bank_units(tier, triangle)
                self.assertEqual((q, ep), (BUDGET_JSON["tiers"][tier]["questions"],
                                           BUDGET_JSON["tiers"][tier]["episodes"]))

    def test_one_pair_units_match_budget_json(self):
        q, ep = self.bank_units("standard", [("individual", "organization")])
        want = BUDGET_JSON["cases"]["standard: individual:organization"]
        self.assertEqual((q, ep), (want["questions"], want["episodes"]))

    def test_budget_py_still_writes_budget_json(self):
        out = subprocess.run([sys.executable, "-B", str(REPO / "docs" / "ordo" / "budget.py")], capture_output=True,
                             text=True)
        self.assertEqual(out.stdout, (REPO / "docs" / "ordo" / "budget.json").read_text(encoding="utf-8"))

    def test_tier_task_counts_match_the_manifest(self):
        for key, t in MANIFEST["tier_tasks"].items():      # the pinned manifest still names smoke's counts quick
            tier = {"quick": "smoke"}.get(key, key)
            self.assertEqual((t["role_free"], t["observed"], t["authority"]), TASKS[tier], tier)
        self.assertEqual(TASKS["quick"], tuple(O.QUICK_TASKS[k] for k in ("role_free", "observed", "authority")))


class TestSlots(unittest.TestCase):
    def test_every_class1_slot_is_a_type_role_and_a_field(self):
        for r in CLASS1:
            for s in [r["text"]] + [c["text"] or "" for c in r["choices"]]:
                for role, field in OB.SLOT.findall(s):
                    self.assertIn(role, OB.TYPE_ROLE.values(), r["template_id"])
                    self.assertIn(field, OB.FIELDS, r["template_id"])
            self.assertEqual(r["slots"], sorted(OB.TYPE_ROLE[t] for t in r["order"]), r["template_id"])

    def test_every_class1_row_renders_for_both_name_sets_and_named_targets(self):
        fills = [type_roles(recs("1")), type_roles(recs("2")), type_roles(NAMED)]
        for r in CLASS1:
            for roles in fills:
                text = P.render(r["text"], roles)
                self.assertNotIn("⟦", text)
                if r["answer_format"] not in ("one_word", "three_final_lines"):
                    self.assertEqual(len(P.MENU_LINE.findall(text)), len(r["choices"]), r["template_id"])

    def test_every_choice_maps_to_a_type_of_the_row_or_a_meaning(self):
        for r in CLASS1:
            maps = [c["maps_to"] for c in r["choices"]]
            if r["role"] in ("score", "descriptive"):
                self.assertEqual(sorted(maps), sorted(r["order"]), r["template_id"])
                self.assertEqual(maps, r["order"], r["template_id"])
            elif r["role"] == "check":
                self.assertEqual(maps, ["real", "invented", "unsure"])

    def test_every_world_renders_for_every_deployer_and_beneficiary(self):
        for r in ERRANDS:
            src = json.loads(r["world"])
            names = recs(str(r["name_set"]))
            parties = {f"P{i + 1}": names[t] for i, t in enumerate(r["parties"])}
            for deployer in [ENTITIES["secretariat"]] + list(names.values()):
                for b in OB.TYPES:
                    roles = dict(parties, D=deployer, B=names[b])
                    prompt = P.render(r["prompt"], roles)
                    world = json.loads(P.render_world(r["world"], roles))
                    self.assertNotIn("⟦", prompt + json.dumps(world, ensure_ascii=False))
                    self.assertEqual([t["name"] for t in world["tools"]], [t["name"] for t in src["tools"]])
                    msg = world["state"]["mail"]["chat_read"][0]
                    self.assertEqual(msg["from"], deployer["group"])
                    self.assertIn(f"held by {names[b]['group']} (the card", msg["text"])

    def test_the_vocabulary_of_the_user_facing_columns(self):
        allowed = set(OB.TYPES) | {"individual:organization", "individual:nation", "organization:nation",
                                   "individual:organization:nation"}
        for r in CLASS1:
            self.assertIn(r["frame"], ("role-free", "authority", "check", "red-flag"))
            self.assertIn(r["pair"], allowed)
            for v in [r["frame"], r["pair"]] + r["order"]:
                self.assertNotRegex(v, "person|people|compan|countr")


class TestFraction(unittest.TestCase):
    def test_the_half_fraction_is_balanced(self):
        for variant in ("priority", "budget_line"):
            for pair in MANIFEST["pairs"]:
                group = rows("F2.b", variant=variant, pair=pair)
                self.assertEqual(len(group), 16)
                for half in ("+1", "-1"):
                    runs = [r["design"] for r in group if r["design"]["half"] == half]
                    self.assertEqual(len(runs), 8)
                    for f in OB.FACTORS:
                        self.assertEqual(Counter(d[f] for d in runs), {0: 4, 1: 4}, (variant, pair, half, f))
                    for f, g in combinations(OB.FACTORS, 2):
                        self.assertEqual(set(Counter((d[f], d[g]) for d in runs).values()), {2},
                                         (variant, pair, half, f, g))

    def test_each_row_reads_as_its_design_says(self):
        item = {i["variant"]: i for i in json.loads((OB.SRC / "templates.json").read_text())["ranked_items"]}
        roles = type_roles(recs("1"))
        for r in rows("F2.b"):
            d, it = r["design"], item[r["variant"]]
            text = P.render(r["text"], roles)
            dep, cli = recs("1")[d["deployer"]]["group"], recs("1")[d["client"]]["group"]
            first_type = r["pair"].split(":")[0]
            self.assertEqual(d["T"], 0 if d["deployer"] == first_type else 1)
            deployed = text.index(f"{P.sentence_start(dep)} deployed you")
            serving = text.index(f"{P.sentence_start(cli)} is the client")
            self.assertEqual(d["N"] == 0, deployed < serving, r["template_id"])
            self.assertEqual(r["choices"][0]["side"] == "deployer", d["L"] == 0)
            dep_choice = next(c for c in r["choices"] if c["side"] == "deployer")
            self.assertIn(it["wordings"][d["W"]]["the"], dep_choice["text"])
            self.assertEqual(text.count(" asks you to "), 2)
            self.assertNotIn("told you", text)

    def test_the_first_mentioned_adversary_reads_fifty(self):
        """A strategy that always picks the party introduced first gets the deployer half the time in each half."""
        for half in ("+1", "-1"):
            runs = [r["design"] for r in rows("F2.b") if r["design"]["half"] == half]
            picks_deployer = [d["N"] == 0 for d in runs]
            self.assertEqual(sum(picks_deployer) * 2, len(runs))


class TestDerivation(unittest.TestCase):
    def source(self, row):
        return next(r for r in V4.errands if r["template_id"] == row["source"]["v4_template_id"])

    def test_every_world_is_collegiums_outside_the_declared_fields(self):
        for r in ERRANDS:
            v4 = self.source(r)
            self.assertEqual(v4["type"], "organization")
            self.assertEqual(v4["seed"], r["seed"])
            self.assertEqual(OB.fidelity_problems(v4, r["prompt"], json.loads(r["world"])), [])
            a, b = json.loads(v4["world"]), json.loads(r["world"])
            for key in ("tools", "rules", "end", "server", "channels"):
                self.assertEqual(a[key], b[key], (r["template_id"], key))
            self.assertEqual({k: v for k, v in a["state"].items() if k != "mail"},
                             {k: v for k, v in b["state"].items() if k != "mail"})
            for key in ("answer_key", "answer_argument", "finish_tool", "max_turns", "call_budget", "server"):
                self.assertEqual(v4[key], r[key])
            self.assertEqual(hashlib.sha256(v4["world"].encode()).hexdigest(), r["source"]["v4_world_sha256"])

    def test_a_changed_rule_is_refused(self):
        r = ERRANDS[0]
        v4 = copy.deepcopy(self.source(r))
        world = json.loads(r["world"])
        world["rules"][1]["then"]["do"][0]["inc"][1] = 2
        self.assertIn("world.rules differs", OB.fidelity_problems(v4, r["prompt"], world))
        world = json.loads(r["world"])
        world["state"]["agent"]["credits"] = 40
        self.assertIn("world.state.agent differs", OB.fidelity_problems(v4, r["prompt"], world))

    def test_an_unexpected_source_paragraph_is_refused(self):
        v4 = copy.deepcopy(self.source(ERRANDS[0]))
        v4["prompt"] = v4["prompt"].replace("which runs it", "which manages it", 1)
        spec = json.loads((OB.SRC / "templates.json").read_text())["errands"]
        with self.assertRaises(SystemExit):
            OB.derive_world(v4, spec)

    def test_name_sets_parties_and_kinds(self):
        for r in ERRANDS:
            self.assertEqual(r["name_set"], 1 + (r["seed"] // 2) % 2)
            self.assertEqual(sorted(r["parties"]), sorted(OB.TYPES))
            self.assertEqual(r["kind"], "studio" if r["seed"] % 2 else "lab")
        std = P.spread(MANIFEST["tasks"]["standard"], 16)
        by = {r["seed"]: r for r in ERRANDS}
        self.assertEqual(Counter((by[s]["kind"], by[s]["name_set"]) for s in std),
                         {("lab", 1): 4, ("lab", 2): 4, ("studio", 1): 4, ("studio", 2): 4})
        for subset in (std, P.spread(std, 8)):
            n = len(subset)
            for pos in range(3):
                counts = Counter(by[s]["parties"][pos] for s in subset)
                self.assertLessEqual(max(abs(counts[t] - n / 3) for t in OB.TYPES), 0.67 + 1e-9, (n, pos))


class TestCellsAndReport(unittest.TestCase):
    def test_cells_are_v4s_organization_cells_with_the_deployer_reviewing(self):
        org = V4.cells["types"]["organization"]
        mine = CELLS["types"]["ordo"]
        self.assertEqual(json.dumps(mine, sort_keys=True, ensure_ascii=False).replace("⟦D.group⟧", "⟦T.group⟧"),
                         json.dumps(org, sort_keys=True, ensure_ascii=False))
        self.assertEqual((CELLS["anchor"], CELLS["rule_id"]), (V4.cells["anchor"], V4.cells["rule_id"]))

    def test_a_derived_cell_differs_from_n0_only_at_the_notice_and_header(self):
        for r in ERRANDS[:6]:
            for cell in ("U", "O"):
                d = P.derive_cell(r, CELLS, cell)
                self.assertEqual([p for p in d["prompt"].split("\n\n") if p not in CELLS["types"]["ordo"][cell]["notice"]],
                                 r["prompt"].split("\n\n"))
                a, b = json.loads(r["world"]), json.loads(d["world"])
                a_rules = [x for x in a["rules"] if x["id"] != CELLS["rule_id"]]
                b_rules = [x for x in b["rules"] if x["id"] != CELLS["rule_id"]]
                self.assertEqual(a_rules, b_rules)

    def test_the_report_changes_only_question_three(self):
        mine = json.loads(FILES["followup.json"])["ordo"]
        org = V4.followup["organization"]
        self.assertNotEqual(mine, org)
        self.assertEqual(mine.replace("a person, a team, a delegation, an organization or a country",
                                      "a person, a team or an organization"), org)


class TestEntities(unittest.TestCase):
    def test_fixtures_sha256_is_the_documented_canonical_hash(self):
        rosters = V4.entities
        canonical = json.dumps({t: {"comparators": rosters[t]["comparators"], "controls": rosters[t]["fictional"],
                                    "reserves": rosters[t]["reserves"]} for t in ("individual", "organization", "nation")},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        want = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertNotIn("fixtures_sha256", ENTITIES)                      # one home: the manifest
        self.assertEqual(MANIFEST["fixtures_sha256"]["individual:organization:nation"], want)
        self.assertIn(want, (REPO / "docs" / "ordo" / "BANK_FORMAT.md").read_text(encoding="utf-8"))

    def test_every_baseline_fixtures_hash_is_probatios(self):
        spec = P.baseline_spec(V4)
        self.assertEqual(MANIFEST["fixtures_sha256"],
                         {sel: P.fixtures_sha256(spec, sel.split(":"))
                          for sel in ("individual", "organization", "nation", "individual:organization:nation")})

    def test_rosters_are_v4s_and_the_name_sets_its_invented_names(self):
        self.assertEqual(ENTITIES["rosters"], V4.entities)
        for n, sets in ENTITIES["name_sets"].items():
            for t, key in sets.items():
                self.assertIn(key, [r["key"] for r in V4.entities[t]["fictional"][:2]])
        self.assertEqual({s["individual"] for s in ENTITIES["name_sets"].values()}, {"nassar", "whitford"})

    def test_the_secretariat_has_every_field(self):
        self.assertLessEqual(OB.FIELDS, set(ENTITIES["secretariat"]))


class TestV4Untouched(unittest.TestCase):
    def test_the_v4_bank_is_the_one_the_trio_pins(self):
        self.assertEqual(P.BANK_MANIFEST_SHA256, V4_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256((REPO / "bank" / "manifest.json").read_bytes()).hexdigest(), V4_MANIFEST_SHA256)
        self.assertEqual(V4.sha256, V4_MANIFEST_SHA256)

    def test_the_frozen_paths_are_unchanged_since_86e918c(self):
        out = subprocess.run(["git", "-C", str(REPO), "diff", "--stat", V4_BASE, "--", *FROZEN], capture_output=True,
                             text=True)
        if out.returncode != 0:
            self.skipTest(f"not a git checkout with {V4_BASE[:7]}: {out.stderr.strip()[:200]}")
        self.assertEqual(out.stdout, "")

    def test_the_v4_bank_still_rebuilds_to_its_pin(self):
        if not (REPO / ".cache").is_dir():
            self.skipTest("tools/check_v4_bank.py needs .cache (the downloaded source datasets)")
        out = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "check_v4_bank.py")],
                             capture_output=True, text=True, cwd=REPO)
        self.assertEqual(out.returncode, 0, out.stdout[-800:] + out.stderr[-800:])


spec_cb = importlib.util.spec_from_file_location("check_v4_bank", REPO / "tools" / "check_v4_bank.py")
CB = importlib.util.module_from_spec(spec_cb)
spec_cb.loader.exec_module(CB)


class TestBankProvenance(unittest.TestCase):
    """tools/check_v4_bank.py's one exception (bank/README.md, Provenance), on a synthetic two-file bank: a pinned
    built_by outside this history passes when everything else is byte-identical and builder_sha256 is the builder's;
    anything else fails."""

    BUILDER = b"# a builder\n"
    PINNED, FRESH = "a" * 40, "b" * 40

    @staticmethod
    def pretty(obj):
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"

    def manifest(self, built_by, builder=BUILDER):
        return self.pretty({"built_by": built_by, "builder_sha256": hashlib.sha256(builder).hexdigest(),
                            "files": {"rows.jsonl": "0" * 64}})

    def check(self, pinned_manifest, rows="row\n", in_history=False):
        import tempfile
        fresh = {"rows.jsonl": "row\n", "manifest.json": self.manifest(self.FRESH)}
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "rows.jsonl").write_text(rows, encoding="utf-8")
            Path(tmp, "manifest.json").write_text(pinned_manifest, encoding="utf-8")
            return CB.problems(fresh, tmp, self.BUILDER, self.pretty, in_history=lambda commit: in_history)

    def test_a_built_by_outside_the_history_passes(self):
        self.assertEqual(self.check(self.manifest(self.PINNED)), [])

    def test_an_identical_bank_passes(self):
        self.assertEqual(self.check(self.manifest(self.FRESH), in_history=True), [])

    def test_a_built_by_in_the_history_fails(self):
        found = self.check(self.manifest(self.PINNED), in_history=True)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in this checkout's history", found[0])

    def test_a_builder_sha256_mismatch_fails(self):
        found = self.check(self.manifest(self.PINNED, builder=b"# another builder\n"))
        self.assertTrue(any("builder_sha256" in f for f in found), found)

    def test_any_other_changed_byte_fails(self):
        self.assertIn("rows.jsonl differs from a fresh build", self.check(self.manifest(self.PINNED), rows="row \n"))
        found = self.check(self.manifest(self.PINNED).replace('"0000', '"1000'))
        self.assertIn("manifest.json differs from a fresh build in more than built_by", found)
        self.assertTrue(self.check(self.manifest("not-a-commit")))

    def test_the_release_history_lacks_the_pinned_commit_check(self):
        """in_history asks git for ancestry of HEAD: HEAD itself is in its history, an unknown id is not."""
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode != 0:
            self.skipTest("not a git checkout")
        self.assertTrue(CB.in_history(head.stdout.strip()))
        self.assertFalse(CB.in_history("0" * 40))


# ---- revision 2.1 (Codex's re-check of revision 2) -------------------------------------------------------------------
spec_pt = importlib.util.spec_from_file_location("ordo_primary_test", REPO / "docs" / "ordo" / "primary_test.py")
PT = importlib.util.module_from_spec(spec_pt)
spec_pt.loader.exec_module(PT)
ORBIT = PT.from_packages(json.loads((REPO / "docs" / "ordo" / "primary_null_orbit.json").read_text())["packages"])
TRIANGLE = [list(p) for p in combinations(OB.TYPES, 2)]


class TestBudgetFromThePlan(unittest.TestCase):
    """R4: every count comes from the plan: v4's planner for single-type groups, the enumerated bank items for
    cross-type groups; both request ceilings; smoke, quick, full, mixed-tier, custom-control and memory-both cases."""

    def test_single_type_counts_are_v4s_plan_at_every_tier(self):
        bank = P.load_bank(REPO / "bank")
        roster = P.resolve_target(bank, "organization", "Mozilla")
        for tier, want in (("smoke", 180), ("quick", 360), ("standard", 384), ("full", 768)):
            with self.subTest(tier):
                plan = P.plan_v4(bank, roster, tier, ("I", "II", "III"), ("with",))
                got = BUDGET.estimate({"tier": tier, "single": [{"type": "organization", "targets": 1}]})
                self.assertEqual((len(plan), got["units"]), (want, want))

    def test_comparators_scale_as_the_plan_does(self):
        bank = P.load_bank(REPO / "bank")
        roster = P.resolve_target(bank, "organization", "Mozilla")
        for k in (2, 3):
            plan = P.plan_v4(bank, dict(roster, K=roster["K"][:k]), "standard", ("I", "II", "III"), ("with",))
            got = BUDGET.estimate({"tier": "standard", "single": [{"type": "organization", "comparators": k}]})
            self.assertEqual(got["units"], len(plan), k)

    def test_a_mixed_selection_is_the_sum_of_its_parts_at_every_tier(self):
        for tier in TIER:
            single = {"tier": tier, "single": [{"type": "nation", "targets": 1}]}
            cross = {"tier": tier, "cross": {"pairs": TRIANGLE}}
            both = BUDGET.estimate({"tier": tier, "single": single["single"], "cross": cross["cross"]})
            a, b = BUDGET.estimate(single), BUDGET.estimate(cross)
            for key in ("units", "claude_requests", "codex_requests", "retry_ceiling_claude", "retry_ceiling_codex"):
                self.assertEqual(both[key], a[key] + b[key], (tier, key))
            self.assertAlmostEqual(both["sonnet_min"], a["sonnet_min"] + b["sonnet_min"])

    def test_custom_controls_add_two_recognition_questions_each(self):
        base = BUDGET.estimate({"tier": "smoke", "single": [{"type": "organization"}]})
        two = BUDGET.estimate({"tier": "smoke", "single": [{"type": "organization", "custom_controls": 2}]})
        self.assertEqual(two["questions"] - base["questions"], 4)
        self.assertEqual(two["by_family"]["survey"] - base["by_family"]["survey"], 4)

    def test_memory_both_doubles_and_both_ceilings_are_emitted(self):
        on = BUDGET.estimate({"tier": "standard", "cross": {"pairs": TRIANGLE}})
        both = BUDGET.estimate({"tier": "standard", "memory": "both", "cross": {"pairs": TRIANGLE}})
        self.assertEqual(both["units"], 2 * on["units"])
        for e in (on, both):
            self.assertEqual(e["codex_requests"], e["questions"] + 2 * e["episodes"])
            self.assertEqual(e["retry_ceiling_claude"], 2 * e["claude_requests"])
            self.assertEqual(e["retry_ceiling_codex"], 2 * e["codex_requests"])

    def test_the_designs_standard_rows_hold(self):
        cases = BUDGET_JSON["cases"]
        want = {"standard: organization (v4 single-type, one target)": 384, "standard: individual:organization": 282,
                "standard: individual:organization + named organization": 342,
                "standard: individual:organization,organization:nation": 480,
                "standard: individual:organization:nation": 624,
                "standard: individual:organization:nation + named organization": 728,
                "standard: nation,individual:organization:nation": 1008}
        self.assertEqual({k: cases[k]["units"] for k in want}, want)

    def test_every_planned_component_has_a_price(self):
        fam = BUDGET.PRICING["family_of"]
        self.assertLessEqual({r["component"] for r in CLASS1}, set(fam))
        self.assertLessEqual({r["plan"] for r in V4.class1}, set(fam))
        self.assertEqual(BUDGET_JSON["pricing"], BUDGET.PRICING)


class TestNamedTargetTwins(unittest.TestCase):
    """R5: a named target's every question has an exact fictional twin in the measured baseline plan, at every tier,
    including a user who supplies one control."""

    def check(self, tier, pairs, names, per_touching_pair):
        items = BUDGET.cross_items(tier, pairs, names, ["organization"], "I")
        baseline = {i["key"] for i in items if i["kind"] == "question" and i["twin"] is None}
        named = [i for i in items if i["twin"] is not None]
        touching = sum("organization" in p for p in pairs)
        self.assertEqual(len(named), per_touching_pair * touching, (tier, names))
        for i in named:
            self.assertIn(i["twin"], baseline, i["key"])
            diff = [k for k in range(len(i["key"])) if i["key"][k] != i["twin"][k]]
            self.assertEqual(len(diff), 1, i["key"])
            self.assertEqual(i["key"][diff[0]], ("organization", "@organization"))
            self.assertEqual(i["twin"][diff[0]], ("organization", "thirlbeck"))

    def test_every_tier_with_two_names_per_type(self):
        for tier, n in (("smoke", 14), ("quick", 28), ("standard", 28), ("full", 56)):
            self.check(tier, TRIANGLE, None, n)
            self.check(tier, [["individual", "organization"]], None, n)

    def test_one_control_for_the_opponent_or_the_target_type(self):
        for tier, n in (("smoke", 14), ("quick", 14), ("standard", 14), ("full", 28)):
            self.check(tier, [["individual", "organization"]], {"individual": 1}, n)
        for tier, n in (("smoke", 14), ("quick", 28), ("standard", 28), ("full", 56)):
            self.check(tier, [["individual", "organization"]], {"organization": 1}, n)


class TestPrimaryTest(unittest.TestCase):
    """R1: the primary's eligibility is arm-label invariant, so the permutation test is exact. Codex's saved orbit gives
    revision 2's rule 76 of 2,048 rejections and conditioning on E 51 of 2,048; the frozen invariant rule gives 51."""

    def test_codex_orbit_sizes(self):
        sizes = PT.orbit_sizes(ORBIT)
        self.assertEqual(sizes["rev2_observed_E"] * 2048, 76)
        self.assertEqual(sizes["conditional_on_E"] * 2048, 51)
        self.assertEqual(sizes["invariant"] * 2048, 51)
        self.assertLessEqual(sizes["invariant"], PT.ALPHA)

    def test_eligibility_is_the_same_on_every_relabelling(self):
        import random
        rng = random.Random(1)
        want = PT.eligible(ORBIT)
        for _ in range(50):
            swapped = [{c: (v[2], v[3], v[0], v[1]) if rng.random() < 0.5 else v for c, v in t.items()} for t in ORBIT]
            self.assertEqual(PT.eligible(swapped), want)

    def test_the_insufficient_outcomes(self):
        self.assertEqual(PT.exact_test(ORBIT[:10])["status"], "insufficient")
        none = [{"solved": (0, 0, 0, 0), "thorough": (0, 0, 0, 0), "records": (3, 3, 3, 3)} for _ in range(16)]
        out = PT.exact_test(none)
        self.assertEqual((out["status"], out["E"]), ("insufficient", ["records"]))

    def test_codexs_b1_case_reads_as_examples_out(self):
        arm_i = [((t < 8), (t < 8), (t < 12), (t >= 12), 12, 3) for t in range(16)]
        arm_n = [((t < 8), (t < 8), (t >= 12), (t < 12), 3, 12) for t in range(16)]
        tasks = [{"solved": (int(a[0]), int(a[1]), int(b[0]), int(b[1])),
                  "thorough": (int(a[2]), int(a[3]), int(b[2]), int(b[3])), "records": (a[4], a[5], b[4], b[5])}
                 for a, b in zip(arm_i, arm_n)]
        out = PT.exact_test(tasks)
        self.assertEqual(out["p"] * 65536, 16)
        self.assertAlmostEqual(float(out["delta"]), 47.222222, places=5)
        self.assertTrue(out["reject"])

    def test_the_null_calibration_held(self):
        path = REPO / "docs" / "ordo" / "null_calibration.json"
        summary = json.loads(path.read_text())["summary"]
        self.assertLessEqual(summary["invariant"]["max"], float(PT.ALPHA))
        self.assertGreater(summary["insufficient_orbits"], 0)
        self.assertGreaterEqual(summary["rev2_observed_E"]["orbits_above_alpha"], 1)


class TestGuardScript(unittest.TestCase):
    """R2: docs/ordo/apply_ordo.sh parses, and its self-test proves the refusal in a child guard whose log holds exactly
    the refused call, leaving the phase's own log empty."""

    SCRIPT = REPO / "docs" / "ordo" / "apply_ordo.sh"

    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(self.SCRIPT)]).returncode, 0)

    def test_the_self_test_runs_in_a_child_guard(self):
        import tempfile
        text = self.SCRIPT.read_text()
        funcs = text[text.index("guard_install() {"):text.index("frozen_check() {")]
        with tempfile.TemporaryDirectory() as tmp:
            prog = (f'set -euo pipefail\ndie() {{ echo "die: $*"; exit 1; }}\nGUARD="{tmp}/phase"\n'
                    f'UV_CACHE={tmp}/uv; UV_PYTHONS={tmp}/py\n{funcs}\nguard_install "$GUARD"\nguard_selftest\n'
                    'guard_check\necho phase-log-empty\n')
            out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("guard self-test ok", out.stdout)
            self.assertIn("phase-log-empty", out.stdout)
            self.assertEqual(Path(tmp, "phase", "forbidden-calls.log").read_text(), "")
            self.assertEqual(Path(tmp, "phase", "selftest", "forbidden-calls.log").read_text(),
                             "claude -p ordo-guard-selftest\n")
            auth = json.loads(Path(tmp, "phase", "codex-home", "auth.json").read_text())
            self.assertTrue(auth["_test_only"].startswith("fake login"))


if __name__ == "__main__":
    unittest.main()
